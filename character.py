"""
2D Character Renderer — Option B rewrite
========================================
Renders a stylized presenter (IT guy / alien) with:

- Pose library + contextual selection (pose_lib.POSE_LIBRARY, PosePerformer)
- Lip-sync shape weights from phoneme segments (lip_sync.synthesize)
- Expression/gaze controller (pose_lib.GazeController, ExpressionState)
- Body life micro-motion (idle bounce, breathing, weight shift, jitter)
- Walk-in / walk-out windows (render_frame handles "walk_in"/"walk_out" poses)

The renderer stays a single draw function per frame. Animation logic lives
in pose_lib + lip_sync, so the drawing code is testable/previewable in
isolation.
"""

from __future__ import annotations

import io
import math
import random
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

import pose_lib
import lip_sync


# ---------------------------------------------------------------------------
# Re-export a few useful types for video.py consumers
# ---------------------------------------------------------------------------

LipSyncFrame = Tuple[int, float, List[float]]  # (frame_idx, mouth_open, shape_weights[6])


# ---------------------------------------------------------------------------
# Blink machine (keeps its original spirit, now with emotion-driven rate).
# ---------------------------------------------------------------------------

class BlinkMachine:
    def __init__(self, blink_rate_factor: float = 1.0, seed: Optional[int] = None):
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self._blink_start = -10.0
        self._blink_end = 0.0
        self._blinking = False
        self._next_blink_at = 1.0
        self.blink_rate_factor = blink_rate_factor

    def _next_interval(self, t: float) -> float:
        base = 3.0 - 1.0 * self.blink_rate_factor  # 2..3s baseline
        return max(1.2, self.rng.uniform(base, base + 2.0)) / self.blink_rate_factor

    def update(self, t: float) -> bool:
        if self._blinking:
            if t >= self._blink_end:
                self._blinking = False
                self._next_blink_at = t + self._next_interval(t)
            return True
        else:
            if t >= self._next_blink_at:
                self._blinking = True
                self._blink_end = t + 0.14
            return False


# ---------------------------------------------------------------------------
# Expression-driven parameters (B3).
# ---------------------------------------------------------------------------

def expression_params(emotion: str, t: float) -> dict:
    """
    Returns current expression-derived drawing params for time t.

    - Eyebrow raise: -1..1
    - Eyelid tension: 0..1 (higher = more eyelid occlusion / focused stare)
    - Brow squeeze: 0..1 (angry/squeezed brows)
    - Blink rate factor: multiplier on blink interval
    - Pupil dilate: -1..1 (used to scale iris size)
    - Blink intensity envelope: pre-roll into a blink (0..1)
    """
    base = pose_lib.EMOTION_STATE.get(emotion, pose_lib.EMOTION_STATE["neutral"])
    blink_machine = BlinkMachine(blink_rate_factor=base["blink_rate"])
    # Blink intensity envelope: ramp up right before a blink.
    next_int = blink_machine._next_blink_at
    pre_blink = 0.0
    if 0 <= next_int - 0.18 <= t <= next_int:
        pre_blink = min(1.0, (next_int - t) / 0.18)
    return {
        "eyebrow_raise": base["eyebrow_raise"],
        "eyelid_tension": base["eyelid_tension"],
        "brow_squeeze": base["brow_squeeze"],
        "blink_rate_factor": base["blink_rate"],
        "pupil_dilate": base["pupil_dilate"],
        "pre_blink": pre_blink,
    }


# ---------------------------------------------------------------------------
# Body offsets from life motion (B1).
# ---------------------------------------------------------------------------

def life_offsets(t: float, fps: float, pose_cycle_t: float) -> dict:
    """Cheaper inline version of pose_lib.body_life_params kept here for
    drawing-time convenience (same math)."""
    return pose_lib.body_life_params(t, fps, pose_cycle_t)


# ---------------------------------------------------------------------------
# Main renderer (B6/B7: draws one frame given full animation state).
# ---------------------------------------------------------------------------

class CharacterRenderer2D:
    """
    Drop-in upgrade for the old CharacterRenderer. Same public surface for
    the parts video.py already uses, plus new parameters for poses, lip-sync
    shapes, expression, and gaze.

    For testing/preview, you can call render_frame_index(...) with full
    animation state and get back a PIL RGBA frame without the rest of the
    pipeline.

    The class-level alias `CharacterRenderer` is set after this class
    finishes constructing, so old importers continue to work.
    """

    def __init__(
        self,
        width: int = 300,
        height: int = 500,
        *,
        seed: Optional[int] = None,
    ):
        self.width = width
        self.height = height
        self.seed = seed
        self._blink = BlinkMachine(seed=seed)
        # Reusable random for per-frame micro-jitter (deterministic if seed set).
        self._jitter_rng = random.Random(seed)

    def _draw_body(
        self,
        draw: ImageDraw.ImageDraw,
        cx: int,
        base_y: float,
        life: dict,
        pose_name: str,
        pose: pose_lib.Pose,
        pose_progress: float,
        gesture_progress: float,
        emotion_params: dict,
        orientation: str,  # "left" | "right" | "center"
        in_walk: bool,
        walk_progress: float,
    ) -> None:
        """
        Draw the body + arms for a named pose. For walk_in/walk_out, we draw
        the character off-screen or entering/exiting (B5). For in-scene poses
        we apply life offsets + the pose's arm/gesture parameters.
        """
        body_top = base_y + 115
        base_x = cx + life["weight_x"]

        # Walk-in: character slides in from off-screen right → center.
        if pose_name == "walk_in":
            off_x = cx + (1 - ease_out_elastic(walk_progress)) * (self.width * 0.6)
            self._draw_body_core(
                draw, off_x, base_y + life["breath_y"] + life["bounce_y"],
                pose, gesture_progress, orientation, emotion_params,
            )
            return

        # Walk-out: slide toward off-screen right.
        if pose_name == "walk_out":
            off_x = cx + ease_out_cubic(walk_progress) * (self.width * 0.6)
            self._draw_body_core(
                draw, off_x, base_y + life["breath_y"] + life["bounce_y"],
                pose, gesture_progress, orientation, emotion_params,
            )
            return

        # In-scene pose: apply pose parameters + life.
        self._draw_body_core(
            draw,
            base_x,
            base_y + life["breath_y"] + life["bounce_y"] + life["head_settle_y"],
            pose,
            gesture_progress,
            orientation,
            emotion_params,
        )

    def _draw_body_core(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        base_y: float,
        pose: pose_lib.Pose,
        gesture_progress: float,
        orientation: str,
        emotion_params: dict,
    ) -> None:
        body_top = base_y + 115
        body_color = (35, 35, 45)
        hoodie_accent = (0, 180, 160)
        hoodie_dark = (0, 140, 125)

        draw = draw  # ImageDraw object
        # Torso / hoodie base.
        draw.rounded_rectangle(
            [cx - 72, body_top, cx + 72, body_top + 185],
            radius=18, fill=body_color,
        )
        draw.rounded_rectangle(
            [cx - 62, body_top + 8, cx + 62, body_top + 55],
            radius=10, fill=hoodie_accent,
        )
        for i in range(3):
            y = body_top + 65 + i * 28
            draw.line([cx - 42, y, cx + 42, y], fill=hoodie_dark, width=2)

        # Arms: derived from pose + gesture_progress.
        self._draw_arms(
            draw,
            cx,
            body_top,
            pose.arm_angle_deg,
            pose.arm_raise_deg,
            pose.gesture_progress if hasattr(pose, "gesture_progress") else gesture_progress,
            pose.hand_open if hasattr(pose, "hand_open") else False,
            orientation,
        )

        # Lower body / legs.
        draw.rounded_rectangle(
            [cx - 78, body_top + 185, cx - 22, body_top + 265],
            radius=12, fill=body_color,
        )
        draw.rounded_rectangle(
            [cx + 22, body_top + 185, cx + 78, body_top + 265],
            radius=12, fill=body_color,
        )

    def _draw_arms(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        body_top: float,
        arm_angle_deg: float,
        arm_raise_deg: float,
        gesture_progress: float,
        hand_open: bool,
        orientation: str,
    ) -> None:
        # Map pose angle + gesture into arm geometry.
        # For simplicity, we always draw both arms but accent the "active" one
        # based on orientation and pose.

        if orientation == "left":
            active_arm_x_sign = -1
            passive_arm_x_sign = 1
        elif orientation == "right":
            active_arm_x_sign = 1
            passive_arm_x_sign = -1
        else:
            active_arm_x_sign = 0
            passive_arm_x_sign = 0

        rad = math.radians(arm_angle_deg)
        raise_rad = math.radians(arm_raise_deg)
        active_angle = rad + raise_rad * math.sin(gesture_progress * math.pi * 0.5)

        # Active arm.
        ax = cx + active_arm_x_sign * (70 + abs(active_arm_x_sign) * 40)
        ay = body_top + 25 + int(math.sin(active_angle) * 30)
        self._draw_arm(draw, cx, body_top + 25, ax, ay, hand_open, orientation == "left")

        # Passive arm: lower, subtle sway.
        pax = cx + passive_arm_x_sign * (70 + abs(passive_arm_x_sign) * 30)
        pay = body_top + 40 + int(math.sin(gesture_progress * math.pi * 0.5) * 6)
        self._draw_arm(draw, cx, body_top + 40, pax, pay, False, orientation == "left")

    def _draw_arm(
        self,
        draw: ImageDraw.ImageDraw,
        shoulder_x: float,
        shoulder_y: float,
        elbow_x: float,
        elbow_y: float,
        hand_open: bool,
        from_left: bool,
    ) -> None:
        from PIL import ImageDraw as _ID

        body_color = (35, 35, 45)
        skin = (210, 175, 135)

        # Upper arm segment.
        draw.line(
            [shoulder_x, shoulder_y, elbow_x, elbow_y],
            fill=body_color, width=16,
        )
        # Forearm + hand.
        if hand_open:
            draw.ellipse(
                [elbow_x - 8, elbow_y - 8, elbow_x + 8, elbow_y + 8],
                fill=skin,
            )
            draw.arc(
                [elbow_x - 12, elbow_y - 4, elbow_x + 12, elbow_y + 18],
                start=0, end=180, fill=skin, width=3,
            )
        else:
            # Pointing/fist shape.
            draw.rounded_rectangle(
                [elbow_x - 6, elbow_y - 6, elbow_x + 6, elbow_y + 10],
                radius=3, fill=skin,
            )

    # ------------------------------------------------------------------
    # Head + face, with expression + gaze + blink (B3).
    # ------------------------------------------------------------------

    def _draw_head(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        base_y: float,
        life: dict,
        head_tilt: float,
        emotion_params: dict,
        gaze_x: float,
        gaze_y: float,
        blink_state: bool,
        blink_intensity: float,
        pupil_dilate: float,
    ) -> None:
        skin = (210, 175, 135)
        hair_color = (40, 30, 25)
        shirt_color = (35, 35, 45)

        # Head base with subtle tilt + life settle.
        tilt_x = head_tilt * 0.4 + life["head_settle_x"]
        cx2 = cx + tilt_x

        draw.ellipse(
            [cx2 - 72, base_y + 65, cx2 + 72, base_y + 140],
            fill=shirt_color,
        )
        draw.ellipse(
            [cx2 - 57, base_y - 10, cx2 + 57, base_y + 105],
            fill=skin,
        )

        # Hair.
        draw.pieslice(
            [cx2 - 60, base_y - 48, cx2 + 60, base_y + 28],
            start=180, end=360, fill=hair_color,
        )
        draw.rectangle([cx2 - 60, base_y - 32, cx2 - 46, base_y + 12], fill=hair_color)
        draw.rectangle([cx2 + 46, base_y - 32, cx2 + 60, base_y + 12], fill=hair_color)

        # Ears.
        ear_l_x = cx2 - 57
        ear_r_x = cx2 + 49
        ear_y = base_y + 45
        draw.ellipse([ear_l_x, ear_y - 9, ear_l_x + 14, ear_y + 9], fill=skin)
        draw.ellipse([ear_r_x, ear_y - 9, ear_r_x + 14, ear_y + 9], fill=skin)

        # Face.
        self._draw_face(
            draw,
            cx2,
            base_y,
            tilt=head_tilt,
            emotion_params=emotion_params,
            gaze_x=gaze_x,
            gaze_y=gaze_y,
            blink_state=blink_state,
            blink_intensity=blink_intensity,
            pupil_dilate=pupil_dilate,
        )

    def _draw_face(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        base_y: float,
        tilt: float,
        emotion_params: dict,
        gaze_x: float,
        gaze_y: float,
        blink_state: bool,
        blink_intensity: float,
        pupil_dilate: float,
    ) -> None:
        eye_y = base_y + 48

        eyebrow_raise = emotion_params["eyebrow_raise"]
        eyelid_tension = emotion_params["eyelid_tension"]
        brow_squeeze = emotion_params["brow_squeeze"]
        pre_blink = emotion_params["pre_blink"]

        # Eyebrows.
        brow_y_off = eyebrow_raise * 3.0 + brow_squeeze * 2.0
        brow_color = (60, 40, 30) if brow_squeeze < 0.3 else (50, 30, 25)
        draw.line(
            [cx - 30, eye_y - 16 + brow_y_off, cx - 12, eye_y - 18 + brow_y_off],
            fill=brow_color, width=2,
        )
        draw.line(
            [cx + 12, eye_y - 18 + brow_y_off, cx + 30, eye_y - 16 + brow_y_off],
            fill=brow_color, width=2,
        )

        # Eyelids partially occlude the eye when eyelid_tension is high.
        eyelid_occlude = eyelid_tension * 0.5

        if blink_state:
            # Blink: closed line; blink_intensity ramps open→closed→open.
            close_gap = int(blink_intensity * 4)
            draw.line(
                [cx - 30, eye_y, cx - 10, eye_y], fill=(60, 40, 30), width=2,
            )
            draw.line(
                [cx + 10, eye_y, cx + 30, eye_y], fill=(60, 40, 30), width=2,
            )
        else:
            # White of eye.
            eye_w = 30
            eye_h = 18
            if eyelid_occlude > 0:
                eye_h *= (1 - eyelid_occlude)
            draw.ellipse(
                [cx - eye_w, eye_y - eye_h // 2, cx - eye_w + eye_w * 2, eye_y + eye_h // 2],
                fill="white",
            )
            draw.ellipse(
                [cx + eye_w - eye_w * 2, eye_y - eye_h // 2, cx + eye_w, eye_y + eye_h // 2],
                fill="white",
            )

            # Gaze offset (B3): shift pupil toward gaze target.
            gaze_shift_x = gaze_x * 4.0
            gaze_shift_y = gaze_y * 2.5

            # Pupil size vs pupil_dilate.
            pupil_base = 6
            pupil_r = max(2, pupil_base + pupil_dilate * 3)

            look_x = int(gaze_shift_x)
            look_y = int(gaze_shift_y)

            # Left eye pupil.
            l_eye_cx = cx - eye_w / 2 + eye_w / 2
            l_iris_x = cx - eye_w / 2 + eye_w / 2 + look_x
            l_pupil_x = l_iris_x
            draw.ellipse(
                [l_pupil_x - pupil_r, eye_y - pupil_r, l_pupil_x + pupil_r, eye_y + pupil_r],
                fill=(35, 30, 25),
            )
            # Right eye pupil.
            r_eye_cx = cx + eye_w / 2 - eye_w / 2
            r_iris_x = cx + eye_w / 2 - eye_w / 2 + look_x
            r_pupil_x = r_iris_x
            draw.ellipse(
                [r_pupil_x - pupil_r, eye_y - pupil_r, r_pupil_x + pupil_r, eye_y + pupil_r],
                fill=(35, 30, 25),
            )

            # Glasses frame (keeps the character recognizable).
            glass_color = (60, 60, 70)
            draw.rounded_rectangle(
                [cx - 37, eye_y - 12, cx - 6, eye_y + 12],
                radius=4, outline=glass_color, width=2,
            )
            draw.rounded_rectangle(
                [cx + 6, eye_y - 12, cx + 37, eye_y + 12],
                radius=4, outline=glass_color, width=2,
            )
            draw.line([cx - 6, eye_y, cx + 6, eye_y], fill=glass_color, width=2)
            draw.line(
                [cx - 37, eye_y, cx - 52, eye_y - 6], fill=glass_color, width=2,
            )
            draw.line(
                [cx + 37, eye_y, cx + 52, eye_y - 6], fill=glass_color, width=2,
            )

        # Pre-blink eyelid closure (closed eye starts to cover).
        if pre_blink > 0 and not blink_state:
            eyelid_occlude2 = pre_blink * 0.7
            if eyelid_occlude2 > 0:
                pass  # (visual handled via eyelid_tension-ish; leave as-is for now)

    # ------------------------------------------------------------------
    # Mouth (B4): phoneme shape weights → mouth drawing.
    # ------------------------------------------------------------------

    def _draw_mouth(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        mouth_y: float,
        shapes: List[float],  # length 6: CLOSED, NARROW, WIDE_OPEN, ROUND, STRETCH, SMILE
        emotion: str,
    ) -> None:
        """
        Turn the 6 archetype weights into a mouth drawing.

        Strategy (B4):
        - Pick a dominant archetype from the weights.
        - Draw a mouth that reads as that archetype, with the overall
          mouth_open scalar controlling how wide open it is.
        - Emotion can modulate (smile archetypes boosted for happy/confident).
        """
        if not shapes or len(shapes) != 6:
            shapes = [0.0] * 6

        closed, narrow, wide_open, round_, stretch, smile = shapes

        # Determine mouth_open from weights.
        mouth_open = lip_sync.mouth_open_from_shapes(shapes)

        # Blend emotion smile into shape.
        smile_boost = {
            "neutral": 0.0,
            "confident": 0.25,
            "curious": 0.05,
            "surprised": 0.0,
            "frustrated": -0.05,
            "happy": 0.4,
        }.get(emotion, 0.0)
        smile = min(1.0, smile + smile_boost)

        # Decide dominant shape.
        total = closed + narrow + wide_open + round_ + stretch + smile
        if total <= 0:
            dominant = "closed"
        else:
            vals = [closed, narrow, wide_open, round_, stretch, smile]
            idx = max(range(len(vals)), key=lambda i: vals[i])
            dominant = ["closed", "narrow", "wide_open", "round", "stretch", "smile"][idx]

        mouth_w = 16
        color = (120, 50, 40)

        if dominant == "closed":
            # Thin line / barely open.
            open_h = max(0, mouth_open * 4)
            y_top = mouth_y - open_h // 2
            y_bot = mouth_y + open_h // 2
            if open_h <= 1:
                draw.arc(
                    [cx - 12, mouth_y - 2, cx + 12, mouth_y + 8],
                    start=0, end=180, fill=(150, 80, 70), width=2,
                )
            else:
                draw.ellipse(
                    [cx - mouth_w, y_top, cx + mouth_w, y_bot],
                    fill=color,
                )
                draw.ellipse(
                    [cx - mouth_w + 3, y_top + 2, cx + mouth_w - 3, y_top + open_h // 3 + 2],
                    fill=(180, 80, 70),
                )

        elif dominant == "narrow":
            # Thin tense mouth.
            draw.arc(
                [cx - mouth_w, mouth_y - 3, cx + mouth_w, mouth_y + 7],
                start=0, end=180, fill=(140, 70, 60), width=2,
            )

        elif dominant == "wide_open":
            # Big open mouth.
            open_h = max(4, mouth_open * 16)
            y_top = mouth_y - open_h // 2
            y_bot = mouth_y + open_h // 2
            draw.ellipse(
                [cx - mouth_w, y_top, cx + mouth_w, y_bot],
                fill=color,
            )
            inner_h = max(2, open_h // 3)
            draw.ellipse(
                [cx - mouth_w + 3, y_top + 2, cx + mouth_w - 3, y_top + inner_h + 2],
                fill=(180, 80, 70),
            )

        elif dominant == "round":
            # Rounded lips (O-shaped).
            open_h = max(4, mouth_open * 14)
            draw.ellipse(
                [cx - mouth_w, mouth_y - open_h // 2, cx + mouth_w, mouth_y + open_h // 2],
                outline=color, width=3,
            )
            if open_h > 6 and mouth_open > 0.4:
                draw.ellipse(
                    [cx - mouth_w + 3, mouth_y - open_h // 2 + 2, cx + mouth_w - 3, mouth_y + open_h // 2 - 2],
                    fill=(20, 20, 20),
                )

        elif dominant == "stretch":
            # Wide stretched smile.
            stretch_w = mouth_w + int(mouth_open * 20)
            draw.arc(
                [cx - stretch_w, mouth_y - 8, cx + stretch_w, mouth_y + 14],
                start=0, end=180, fill=(150, 80, 70), width=2,
            )
            if smile > 0.3:
                draw.line(
                    [cx - stretch_w + 4, mouth_y + 2, cx - 8, mouth_y + 4],
                    fill=(200, 100, 80), width=2,
                )
                draw.line(
                    [cx + 8, mouth_y + 4, cx + stretch_w - 4, mouth_y + 2],
                    fill=(200, 100, 80), width=2,
                )

        elif dominant == "smile":
            # Full smile.
            smile_w = mouth_w + int(smile * 26)
            draw.arc(
                [cx - smile_w, mouth_y - 8, cx + smile_w, mouth_y + 14],
                start=0, end=180, fill=(150, 80, 70), width=3,
            )
            if smile > 0.4:
                draw.arc(
                    [cx - smile_w + 6, mouth_y - 2, cx + smile_w - 6, mouth_y + 10],
                    start=0, end=180, fill=(220, 120, 90), width=2,
                )
        else:
            # Fallback.
            draw.arc(
                [cx - 12, mouth_y - 2, cx + 12, mouth_y + 8],
                start=0, end=180, fill=(150, 80, 70), width=2,
            )

    # ------------------------------------------------------------------
    # Top-level frame render.
    # ------------------------------------------------------------------

    def render_frame_index(
        self,
        i: int,
        fps: float,
        gesture: str = "default",
        character_type: str = "it_guy",
        avatar_image: Optional[Image.Image] = None,
        # Full-state params (new pipeline). Defaults here keep the old 4-arg
        # call site in video.py working without changes.
        pose_name: Optional[str] = None,
        pose: Optional["pose_lib.Pose"] = None,
        pose_progress: float = 1.0,
        gesture_progress: float = 0.5,
        emotion: Optional[str] = None,
        mouth_shapes: Optional[List[float]] = None,
        gaze_x: float = 0.0,
        gaze_y: float = 0.0,
        blink_state: Optional[bool] = None,
        blink_intensity: float = 0.0,
        pupil_dilate: float = 0.0,
        in_walk: bool = False,
        walk_progress: float = 0.0,
        orientation: str = "right",
    ) -> Image.Image:
        """
        Render a single character frame from full animation state.

        This is the heart of B6: all animation decision data (pose, lip-sync,
        expression, gaze, blink) is passed in; the renderer is a pure drawing
        function for that frame.
        """
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        t = i / fps
        cx = self.width // 2
        base_y = 30

        # Backward-compatible derivation: when the new full-state params are
        # omitted (e.g. old call sites in video.py), derive sensible defaults.
        if pose_name is None:
            pose_name = pose_lib.POSE_BY_NAME.get(gesture, "rest").name if gesture in pose_lib.POSE_BY_NAME else "rest"
        if pose is None:
            pose = pose_lib.POSE_BY_NAME.get(gesture, pose_lib.POSE_BY_NAME["rest"])
        if emotion is None:
            emotion = "confident"
        if mouth_shapes is None:
            # Old call sites (e.g. video.py) pass no lip-sync state; start
            # from a closed-mouth default. The full pipeline derives shapes
            # from lip_sync and passes them explicitly.
            mouth_shapes = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if blink_state is None:
            self._blink.blink_rate_factor = pose_lib.EMOTION_STATE.get(emotion, pose_lib.EMOTION_STATE["neutral"])["blink_rate"]
            blink_state = self._blink.update(t)

        if avatar_image is not None or character_type == "image":
            return self._draw_avatar_scene(img, avatar_image, t)

        life = life_offsets(t, fps, gesture_progress)
        head_tilt = math.sin(t * 0.8) * 2 + life["head_settle_x"] * 0.4

        # Emotion drawing params + blink result (used by body/head/mouth draws).
        emotion_params = expression_params(emotion, t)
        is_blinking = blink_state if blink_state is not None else self._blink.update(t)

        if character_type == "alien":
            # For alien, delegate to a simpler draw (keep existing alien look).
            self._draw_alien_scene(
                draw, cx, base_y + life["breath_y"], life,
                pose_name, pose, pose_progress, gesture_progress,
                emotion, mouth_shapes, emotion_params,
                gaze_x, gaze_y, is_blinking, blink_intensity, pupil_dilate,
                in_walk, walk_progress, character_type,
            )
        else:
            # It-guy: full body + head + face + mouth.
            self._draw_body(
                draw, cx, base_y, life, pose_name, pose, pose_progress,
                gesture_progress, emotion_params, orientation,
                in_walk, walk_progress,
            )
            # Head + face.
            self._draw_head(
                draw, cx, base_y, life, head_tilt, emotion_params,
                gaze_x, gaze_y, is_blinking, blink_intensity, pupil_dilate,
            )
            # Mouth.
            self._draw_mouth(
                draw, cx, base_y + 78, mouth_shapes, emotion,
            )

        return img

    def _draw_avatar_scene(self, img: Image.Image, avatar_image: Optional[Image.Image], t: float) -> Image.Image:
        """Draw a circular presenter avatar with a subtle breathing ring."""
        cx = self.width // 2
        cy = self.height // 2 - 10
        size = min(self.width, self.height) - 90
        size = max(180, min(size, 300))
        pulse = 1.0 + math.sin(t * 1.8) * 0.02
        box = int(size * pulse)
        x0 = cx - box // 2
        y0 = cy - box // 2

        # Outer ring / thinking circle.
        draw = ImageDraw.Draw(img)
        draw.ellipse([x0 - 10, y0 - 10, x0 + box + 10, y0 + box + 10], fill=(8, 10, 18, 180), outline=(0, 210, 200, 190), width=4)

        if avatar_image is None:
            # Fallback when the caller asks for image mode but no image exists.
            draw.ellipse([x0, y0, x0 + box, y0 + box], fill=(20, 30, 45, 220))
            draw.text((x0 + 18, y0 + box // 2 - 8), "Avatar missing", fill=(240, 240, 240, 255))
            return img

        avatar = avatar_image.convert("RGBA")
        avatar = ImageOps.fit(avatar, (box, box), method=Image.LANCZOS, centering=(0.5, 0.35))

        # Circular mask.
        mask = Image.new("L", (box, box), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, box - 1, box - 1], fill=255)

        # Add a soft circular frame behind the image.
        bubble = Image.new("RGBA", (box + 26, box + 26), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(bubble)
        bdraw.ellipse([0, 0, box + 25, box + 25], fill=(18, 22, 34, 220), outline=(0, 210, 200, 220), width=4)
        bubble.paste(avatar, (13, 13), mask)

        # Composite onto the canvas.
        img.alpha_composite(bubble, (cx - bubble.width // 2, cy - bubble.height // 2))
        return img

    def _draw_alien_scene(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        base_y: float,
        life: dict,
        pose_name: str,
        pose: pose_lib.Pose,
        pose_progress: float,
        gesture_progress: float,
        emotion: str,
        mouth_shapes: List[float],
        emotion_params: dict,
        gaze_x: float,
        gaze_y: float,
        is_blinking: bool,
        blink_intensity: float,
        pupil_dilate: float,
        in_walk: bool,
        walk_progress: float,
        character_type: str,
    ) -> None:
        """Alien variant: simpler body + face, but still uses life + blink + gaze."""
        # Shortened: reuse existing alien draw helpers with life offsets.
        breath = life["breath_y"] + life["bounce_y"]
        self._draw_alien_body(draw, cx, base_y + breath, pose, gesture_progress)
        head_tilt = math.sin((cx / fps) * 0.8) * 2 if fps else 0.0
        self._draw_alien_head(draw, cx, base_y + breath, head_tilt)
        self._draw_alien_face(
            draw, cx, base_y + breath, head_tilt,
            lip_sync.mouth_open_from_shapes(mouth_shapes),
            is_blinking,
            cx / fps if fps else 0.0,
        )

    # ------------------------------------------------------------------
    # Legacy helper aliases (kept so existing alien draws still work).
    # ------------------------------------------------------------------

    def _draw_alien_body(self, draw, cx, base_y, pose, gesture_progress):
        body_top = base_y + 122
        suit = (22, 55, 50)
        accent = (90, 255, 190)
        skin = (70, 220, 120)

        draw.rounded_rectangle([cx - 72, body_top, cx + 72, body_top + 185], radius=22, fill=suit)
        draw.rounded_rectangle([cx - 60, body_top + 12, cx + 60, body_top + 56], radius=12, fill=accent)
        draw.line([cx - 44, body_top + 74, cx + 44, body_top + 74], fill=(55, 90, 80), width=3)
        draw.line([cx - 40, body_top + 100, cx + 40, body_top + 100], fill=(55, 90, 80), width=3)
        draw.line([cx - 36, body_top + 126, cx + 36, body_top + 126], fill=(55, 90, 80), width=3)

        if pose.name == "point" or pose.name == "pointing":
            draw.rounded_rectangle([cx - 138, body_top + 20, cx - 72, body_top + 52], radius=12, fill=suit)
            draw.ellipse([cx - 152, body_top + 14, cx - 122, body_top + 46], fill=skin)
            draw.rounded_rectangle([cx + 65, body_top + 22, cx + 128, body_top + 52], radius=12, fill=suit)
            draw.ellipse([cx + 118, body_top + 18, cx + 148, body_top + 50], fill=skin)
        elif pose.name == "waving" or pose.name == "celebrate":
            wave_y = int(math.sin(gesture_progress * math.pi * 3) * 12)
            draw.rounded_rectangle([cx - 135, body_top + 18, cx - 72, body_top + 52], radius=12, fill=suit)
            draw.ellipse([cx - 148, body_top + 14, cx - 120, body_top + 46], fill=skin)
            draw.rounded_rectangle([cx + 70, body_top - 34 + wave_y, cx + 132, body_top + 30 + wave_y], radius=12, fill=suit)
            draw.ellipse([cx + 120, body_top - 50 + wave_y, cx + 152, body_top - 22 + wave_y], fill=skin)
        else:
            sway = int(math.sin(gesture_progress * math.pi * 0.5) * 4)
            draw.rounded_rectangle([cx - 132 + sway, body_top + 20, cx - 72, body_top + 50], radius=12, fill=suit)
            draw.ellipse([cx - 144 + sway, body_top + 16, cx - 122 + sway, body_top + 46], fill=skin)
            draw.rounded_rectangle([cx + 72, body_top + 20, cx + 132 - sway, body_top + 50], radius=12, fill=suit)
            draw.ellipse([cx + 122 - sway, body_top + 16, cx + 144 - sway, body_top + 46], fill=skin)

        draw.rounded_rectangle([cx - 78, body_top + 184, cx - 22, body_top + 265], radius=12, fill=suit)
        draw.rounded_rectangle([cx + 22, body_top + 184, cx + 78, body_top + 265], radius=12, fill=suit)

    def _draw_alien_head(self, draw, cx, base_y, tilt):
        green = (72, 220, 120)
        dark_green = (25, 90, 60)
        eye_white = (235, 255, 240)
        hair_glow = (110, 255, 170)

        draw.ellipse([cx - 84, base_y - 18, cx + 84, base_y + 132], fill=green)
        draw.ellipse([cx - 104, base_y - 30, cx - 80, base_y - 2], fill=green)
        draw.ellipse([cx + 80, base_y - 30, cx + 104, base_y - 2], fill=green)

        draw.line([cx - 26, base_y - 52, cx - 44, base_y - 82], fill=hair_glow, width=4)
        draw.line([cx + 26, base_y - 52, cx + 44, base_y - 82], fill=hair_glow, width=4)
        draw.ellipse([cx - 50, base_y - 96, cx - 34, base_y - 80], fill=hair_glow)
        draw.ellipse([cx + 34, base_y - 96, cx + 50, base_y - 80], fill=hair_glow)

        draw.pieslice([cx - 66, base_y - 10, cx - 6, base_y + 78], start=180, end=360, fill=eye_white)
        draw.pieslice([cx + 6, base_y - 10, cx + 66, base_y + 78], start=180, end=360, fill=eye_white)

        draw.ellipse([cx - 44, base_y + 18, cx - 22, base_y + 44], fill=(15, 20, 25))
        draw.ellipse([cx + 22, base_y + 18, cx + 44, base_y + 44], fill=(15, 20, 25))

        draw.arc([cx - 28, base_y + 54, cx + 28, base_y + 78], start=0, end=180, fill=dark_green, width=3)
        draw.line([cx - 26, base_y + 10, cx - 10, base_y + 16], fill=dark_green, width=2)
        draw.line([cx + 10, base_y + 16, cx + 26, base_y + 10], fill=dark_green, width=2)

    def _draw_alien_face(self, draw, cx, base_y, tilt, mouth_open, is_blinking, t):
        eye_y = base_y + 36
        skin = (72, 220, 120)
        eye_glow = (230, 255, 240)

        if is_blinking:
            draw.line([cx - 40, eye_y, cx - 18, eye_y], fill=(20, 45, 28), width=3)
            draw.line([cx + 18, eye_y, cx + 40, eye_y], fill=(20, 45, 28), width=3)
        else:
            draw.ellipse([cx - 48, eye_y - 16, cx - 14, eye_y + 16], fill=eye_glow)
            draw.ellipse([cx + 14, eye_y - 16, cx + 48, eye_y + 16], fill=eye_glow)
            look_x = int(math.sin(t * 0.6) * 2)
            draw.ellipse([cx - 36 + look_x, eye_y - 8, cx - 26 + look_x, eye_y + 2], fill=(10, 15, 20))
            draw.ellipse([cx + 26 + look_x, eye_y - 8, cx + 36 + look_x, eye_y + 2], fill=(10, 15, 20))

        draw.rounded_rectangle([cx - 48, eye_y - 18, cx - 14, eye_y + 18], radius=8, outline=(20, 70, 40), width=2)
        draw.rounded_rectangle([cx + 14, eye_y - 18, cx + 48, eye_y + 18], radius=8, outline=(20, 70, 40), width=2)

        mouth_y = base_y + 74
        if mouth_open > 0.25:
            h = max(4, int(mouth_open * 12))
            draw.ellipse([cx - 10, mouth_y - h, cx + 10, mouth_y + h], fill=(20, 70, 45))
        else:
            draw.arc([cx - 14, mouth_y - 2, cx + 14, mouth_y + 10], start=0, end=180, fill=(20, 70, 45), width=2)

        draw.line([cx - 20, eye_y - 28, cx - 36, eye_y - 40], fill=(120, 255, 170), width=3)
        draw.line([cx + 20, eye_y - 28, cx + 36, eye_y - 40], fill=(120, 255, 170), width=3)
        draw.ellipse([cx - 42, eye_y - 48, cx - 30, eye_y - 36], fill=(120, 255, 170))
        draw.ellipse([cx + 30, eye_y - 48, cx + 42, eye_y - 36], fill=(120, 255, 170))
        draw.line([cx - 16, eye_y + 26, cx + 16, eye_y + 26], fill=(20, 60, 38), width=2)


# ---------------------------------------------------------------------------
# Easing helpers (used by walk-in/walk-out + pose transitions).
# ---------------------------------------------------------------------------

def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def ease_out_elastic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t == 0 or t == 1:
        return t
    c4 = (2.0 * math.pi) / 3.0
    return math.pow(2.0, -10.0 * t) * math.sin((t * 10.0 - 0.75) * c4) + 1.0


# Backward-compatible alias: old importers (video.py, gui.py, workflow.py)
# reference `CharacterRenderer`; map it to the current renderer class.
CharacterRenderer = CharacterRenderer2D


if __name__ == "__main__":
    # Tiny preview: render one frame with pose + lip-sync + expression + gaze.
    from PIL import Image

    r = CharacterRenderer2D(width=260, height=420)
    # Build a quick animation state for frame 30 of a 60-frame scene.
    table = lip_sync.synthesize(
        "GPU stands for graphics",
        total_frames=60,
        fps=24.0,
        emotion="confident",
    )
    shapes = table[30][2]
    mouth_open = lip_sync.mouth_open_from_shapes(shapes)

    pose = pose_lib.POSE_BY_NAME["explain_open"]
    img = r.render_frame_index(
        i=30,
        fps=24.0,
        pose_name="explain_open",
        pose=pose,
        pose_progress=1.0,
        gesture_progress=0.5,
        emotion="confident",
        mouth_shapes=shapes,
        gaze_x=0.1,
        gaze_y=0.0,
        blink_state=False,
        blink_intensity=0.0,
        pupil_dilate=0.05,
        in_walk=False,
        walk_progress=0.0,
        orientation="right",
    )
    img.save("output/temp/_preview_frame.png")
    print("preview saved:", img.size, "mouth_open=", round(mouth_open, 2))
