"""
Pose + expression library + contextual selection for the character renderer.

Contains:
- A library of at least 8 named poses (B2) with parameters (arm angle,
  gesture progress curve, emotional valence) that the renderer interprets.
- A PosePerformer that maps scene metadata (intent, gesture hint, bullet
  positions) to a pose sequence with timing/easing (B1/B2/B5).
- Gaze controller (B3) that tracks the active bullet / title and blends
  natural idle saccades.
- Expression state machine (B3) mapping scene intent to emotion.

This is a data-driven layer on top of CharacterRenderer.render_frame_index;
the renderer stays a drawing function, while this module decides WHAT to draw
at each frame.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Pose library (B2): at least 8 named poses with parameters.
# ---------------------------------------------------------------------------

@dataclass
class Pose:
    """A named pose with drawing parameters + emotional-context label."""
    name: str
    # Hand/arm position hint for the renderer (in body-local units).
    arm_angle_deg: float = 0.0          # 0 = both down, +/- = one arm out
    arm_raise_deg: float = 0.0          # how high the raising arm goes
    hand_open: bool = False             # open hand vs point/fist
    lean_deg: float = 0.0              # torso lean (positive = toward viewer)
    # Emotional/contextual label + which emotion it helps express.
    emotion_hints: List[str] = field(default_factory=list)
    # Default gesture-progress curve shape (EEG-style ease in/out).
    curve: str = "easeInOut"            # easeInOut, easeOut, linear, hold
    # Typical dwell time in seconds before transitioning out.
    dwell_hint: float = 3.0
    # Markup: is this pose a "reaction" (spike then back) or "sustained"?
    kind: str = "sustained"             # sustained | reaction | transition

# The canonical pose library (>= 8). Names chosen to be intuitive for a
# tech explainer; the renderer reads parameters, not semantics.
POSE_LIBRARY: List[Pose] = [
    Pose(
        name="rest", emotion_hints=["neutral", "curious"],
        arm_angle_deg=0.0, arm_raise_deg=0.0, hand_open=False,
        lean_deg=2.0, curve="easeInOut", dwell_hint=4.0, kind="sustained",
    ),
    Pose(
        name="explain_open", emotion_hints=["confident", "curious"],
        arm_angle_deg=35.0, arm_raise_deg=25.0, hand_open=True,
        lean_deg=4.0, curve="easeInOut", dwell_hint=5.0, kind="sustained",
    ),
    Pose(
        name="point", emotion_hints=["confident"],
        arm_angle_deg=-45.0, arm_raise_deg=20.0, hand_open=False,
        lean_deg=0.0, curve="easeOut", dwell_hint=3.0, kind="sustained",
    ),
    Pose(
        name="surprise", emotion_hints=["surprised"],
        arm_angle_deg=40.0, arm_raise_deg=45.0, hand_open=True,
        lean_deg=6.0, curve="easeOut", dwell_hint=1.5, kind="reaction",
    ),
    Pose(
        name="think", emotion_hints=["curious"],
        arm_angle_deg=0.0, arm_raise_deg=0.0, hand_open=False,
        lean_deg=12.0, curve="easeInOut", dwell_hint=4.0, kind="sustained",
    ),
    Pose(
        name="facepalm", emotion_hints=["frustrated"],
        arm_angle_deg=20.0, arm_raise_deg=30.0, hand_open=False,
        lean_deg=20.0, curve="easeIn", dwell_hint=2.5, kind="reaction",
    ),
    Pose(
        name="celebrate", emotion_hints=["happy", "confident"],
        arm_angle_deg=60.0, arm_raise_deg=70.0, hand_open=True,
        lean_deg=0.0, curve="easeOut", dwell_hint=2.0, kind="reaction",
    ),
    Pose(
        name="list", emotion_hints=["confident", "neutral"],
        arm_angle_deg=-30.0, arm_raise_deg=15.0, hand_open=True,
        lean_deg=0.0, curve="easeInOut", dwell_hint=3.0, kind="sustained",
    ),
    Pose(
        name="welcome", emotion_hints=["happy"],
        arm_angle_deg=20.0, arm_raise_deg=35.0, hand_open=True,
        lean_deg=6.0, curve="easeOut", dwell_hint=2.5, kind="reaction",
    ),
    Pose(
        name="concern", emotion_hints=["frustrated", "curious"],
        arm_angle_deg=10.0, arm_raise_deg=10.0, hand_open=False,
        lean_deg=10.0, curve="easeInOut", dwell_hint=3.0, kind="sustained",
    ),
]

# Quick lookup.
POSE_BY_NAME = {p.name: p for p in POSE_LIBRARY}


# ---------------------------------------------------------------------------
# Emotion state machine (B3): map scene intent -> emotion + eyebrow/eye params.
# ---------------------------------------------------------------------------

EMOTION_STATE = {
    "neutral": {
        "eyebrow_raise": 0.0, "eyelid_tension": 0.3, "brow_squeeze": 0.0,
        "pupil_dilate": 0.0, "blink_rate": 1.0,
    },
    "confident": {
        "eyebrow_raise": 0.1, "eyelid_tension": 0.5, "brow_squeeze": 0.0,
        "pupil_dilate": 0.05, "blink_rate": 0.9,
    },
    "curious": {
        "eyebrow_raise": 0.35, "eyelid_tension": 0.3, "brow_squeeze": 0.0,
        "pupil_dilate": 0.2, "blink_rate": 0.8,
    },
    "surprised": {
        "eyebrow_raise": 0.7, "eyelid_tension": 0.2, "brow_squeeze": 0.0,
        "pupil_dilate": 0.5, "blink_rate": 2.0,  # less frequent but longer
    },
    "frustrated": {
        "eyebrow_raise": -0.2, "eyelid_tension": 0.8, "brow_squeeze": 0.5,
        "pupil_dilate": -0.1, "blink_rate": 1.2,
    },
    "happy": {
        "eyebrow_raise": 0.2, "eyelid_tension": 0.2, "brow_squeeze": 0.0,
        "pupil_dilate": 0.15, "blink_rate": 0.8,
    },
}


@dataclass
class ExpressionState:
    """Current expression for the character at a given frame time."""
    emotion: str = "neutral"
    eyebrow_raise: float = 0.0        # -1..1, positive = raised
    eyelid_tension: float = 0.3       # 0..1, affects eyelid occlusion
    brow_squeeze: float = 0.0         # 0..1, angry/squeeze look
    blink_intensity: float = 0.0      # 0..1, 1 = full blink
    pupil_dilate: float = 0.0         # -1..1
    gaze_x: float = 0.0               # -1..1 screen-relative gaze target
    gaze_y: float = 0.0               # -1..1


# ---------------------------------------------------------------------------
# Gaze controller (B3): track active bullet/title + idle saccades.
# ---------------------------------------------------------------------------

@dataclass
class GazeTarget:
    x: float   # screen-relative x in -1..1 (0 = center)
    y: float   # screen-relative y in -1..1 (0 = center)
    weight: float = 1.0


class GazeController:
    """
    Blends gaze across multiple on-screen targets (title, bullets) plus an
    idle wandering component. Output is a smooth gaze (x,y) that the renderer
    can use to offset the pupils + eyelid tension.

    Timing/easing guidelines (B1):
    - Saccades (gaze jumps) use ease-out with ~0.08-0.12s ramp.
    - Between saccades, gaze drifts with a slow sine wander (±2-3% screen).
    - Blink windows briefly freeze gaze so the blink reads as a reset.
    """

    def __init__(
        self,
        *,
        fps: float = 24.0,
        saccade_ease_out: float = 0.10,
        wander_amp: float = 0.04,
        wander_freq: float = 0.5,
        seed: Optional[int] = None,
    ):
        self.fps = fps
        self.saccade_ease = saccade_ease_out
        self.wander_amp = wander_amp
        self.wander_freq = wander_freq
        self.rng = random.Random(seed)
        self.targets: List[GazeTarget] = []
        self._current_x = 0.0
        self._current_y = 0.0
        self._saccade_start = -10.0
        self._saccade_from_x = 0.0
        self._saccade_from_y = 0.0
        self._saccade_to_x = 0.0
        self._saccade_to_y = 0.0
        self._frozen_until = -1.0

    def set_targets(self, targets: List[GazeTarget]) -> None:
        # Weighted average of the active targets is the "goal" gaze.
        if not targets:
            self.targets = [GazeTarget(0.0, 0.0, 1.0)]
        else:
            self.targets = targets

    def _goal_gaze(self, t: float) -> Tuple[float, float]:
        if not self.targets:
            return 0.0, 0.0
        total_w = sum(tgt.weight for tgt in self.targets)
        if total_w <= 0:
            return 0.0, 0.0
        gx = sum(tgt.x * tgt.weight for tgt in self.targets) / total_w
        gy = sum(tgt.y * tgt.weight for tgt in self.targets) / total_w
        return gx, gy

    def update(self, t: float) -> Tuple[float, float, float]:
        """
        Returns (gaze_x, gaze_y, blink_freeze_factor).
        gaze_x/y in -1..1; blink_freeze_factor 0..1 (1 = fully frozen).
        """
        goal_x, goal_y = self._goal_gaze(t)

        # If a blink is in progress, freeze gaze (so the eye reset is clean).
        if t < self._frozen_until:
            return self._current_x, self._current_y, 1.0

        # Start a new saccade if goal differs enough from current.
        dx = goal_x - self._current_x
        dy = goal_y - self._current_y
        dist = math.hypot(dx, dy)
        if dist > 0.03 and t - self._saccade_start > 0.25:
            self._saccade_from_x = self._current_x
            self._saccade_from_y = self._current_y
            self._saccade_to_x = goal_x
            self._saccade_to_y = goal_y
            self._saccade_start = t

        # Saccade ease-out.
        if t - self._saccade_start < self.saccade_ease:
            p = (t - self._saccade_start) / self.saccade_ease
            ease = p * p * (3.0 - 2.0 * p)  # smoothstep
            self._current_x = self._saccade_from_x + (self._saccade_to_x - self._saccade_from_x) * ease
            self._current_y = self._saccade_from_y + (self._saccade_to_y - self._saccade_from_y) * ease
        else:
            self._current_x = goal_x
            self._current_y = goal_y

        # Add slow wander on top (subtle, ±wander_amp around the target).
        wander_x = math.sin(t * self.wander_freq * 2.0 * math.pi) * self.wander_amp
        wander_y = math.sin(t * self.wander_freq * 3.0 * math.pi + 1.2) * self.wander_amp
        return self._current_x + wander_x, self._current_y + wander_y, 0.0

    def freeze_until(self, t: float, duration: float) -> None:
        self._frozen_until = t + duration


# ---------------------------------------------------------------------------
# Pose performer (B1/B2): schedule pose changes with timing/easing.
# ---------------------------------------------------------------------------

class PosePerformer:
    """
    Schedules transitions among poses for a scene, with:
    - Contextual selection from POSE_LIBRARY based on scene intent
      (B2 contextual rules).
    - Eased transitions between poses (B1 timing/easing).
    - Walk-in / walk-out windows (B5).
    - Reaction poses (short spike then return to a sustained pose).
    """

    def __init__(
        self,
        scene_intent: str = "explain",
        gesture_hint: Optional[str] = None,
        *,
        fps: float = 24.0,
        seed: Optional[int] = None,
    ):
        self.fps = fps
        self.intent = scene_intent
        self.gesture_hint = (gesture_hint or "").lower()
        self.rng = random.Random(seed)

    def _candidate_poses(self) -> List[Pose]:
        """Select pose candidates given scene intent + gesture hint (B2)."""
        # Intent -> emotion mapping (guides emotion + poses).
        intent_emotion = {
            "explain": ["confident", "curious"],
            "reveal": ["surprised", "confident"],
            "problem": ["frustrated", "curious"],
            "how_it_works": ["curious", "neutral"],
            "wrap_up": ["happy", "confident"],
            "list": ["confident", "neutral"],
            "compare": ["curious", "neutral"],
            "reaction_negative": ["frustrated"],
            "reaction_positive": ["happy", "confident"],
        }
        emotion_list = intent_emotion.get(self.intent, ["neutral", "curious"])

        # Preferred poses for this intent: filter library by emotion hint match.
        preferred: List[Pose] = []
        for p in POSE_LIBRARY:
            if any(e in emotion_list for e in p.emotion_hints):
                preferred.append(p)

        # If gesture_hint matches a named pose, pin it at the front.
        if self.gesture_hint and self.gesture_hint in POSE_BY_NAME:
            explicit = POSE_BY_NAME[self.gesture_hint]
            if explicit in preferred:
                preferred.remove(explicit)
            preferred.insert(0, explicit)

        # Ensure at least a fallback.
        if not preferred:
            preferred = [POSE_BY_NAME["rest"]]

        # Deduplicate by name.
        seen = set()
        uniq: List[Pose] = []
        for p in preferred:
            if p.name not in seen:
                seen.add(p.name)
                uniq.append(p)
        return uniq

    def schedule(
        self,
        total_frames: int,
        walk_in_frames: int = 36,     # ~1.5s @24fps
        walk_out_start: Optional[int] = None,
    ) -> List[Tuple[int, str, float]]:
        """
        Returns a list of (frame_idx, pose_name, interp_progress) that the
        renderer can use to interpolate between poses.

        - walk_in_frames: first N frames the character walks in from off-screen
          (the pose during walk-in is 'walk_in' which the renderer treats as
          off-screen motion, not a display pose).
        - walk_out_start: frame index at which walk-out begins (or None to
          disable). After that, the pose is 'walk_out'.
        """
        if walk_out_start is None:
            walk_out_start = total_frames - 30  # ~1.25s out

        schedule: List[Tuple[int, str, float]] = []

        # Phase 1: walk-in (off-screen).
        for i in range(walk_in_frames):
            schedule.append((i, "walk_in", 0.0))

        # Phase 2: choose pose sequence from candidates.
        candidates = self._candidate_poses()
        # Simple sequence: pick a sustained base pose, optionally a reaction
        # in the middle, then return to base. Reaction only if a "reaction_*"
        # intent or a reaction-capable pose exists.
        base = candidates[0]
        reaction = None
        for p in candidates[1:]:
            if p.kind == "reaction":
                reaction = p
                break
        # If intent suggests a reaction (reveal, problem, wrap_up positive spike),
        # prefer one.
        if self.intent in ("reveal", "problem", "wrap_up") and reaction is None:
            for p in candidates:
                if p.kind == "reaction" and p.emotion_hints:
                    reaction = p
                    break

        frame = walk_in_frames
        # Transition into base pose.
        schedule.append((frame, base.name, 0.0))
        frame += 1
        # Hold base for dwell_hint (clamped to scene length).
        dwell_frames = max(12, min(int(base.dwell_hint * self.fps), total_frames - frame - 40))
        for _ in range(dwell_frames):
            if frame >= walk_out_start:
                break
            schedule.append((frame, base.name, 1.0))
            frame += 1
        # Optional reaction.
        if reaction and frame < walk_out_start - 24:
            # Ease into reaction.
            schedule.append((frame, reaction.name, 0.0))
            frame += 1
            r_frames = max(6, min(int(reaction.dwell_hint * self.fps * 0.6), walk_out_start - frame))
            for k in range(r_frames):
                if frame >= walk_out_start:
                    break
                prog = min(1.0, k / max(1, r_frames - 1))
                schedule.append((frame, reaction.name, prog))
                frame += 1
            # Return to base.
            schedule.append((frame, base.name, 0.0))
            frame += 1
            for _ in range(12):
                if frame >= walk_out_start:
                    break
                schedule.append((frame, base.name, min(1.0, (_ + 1) / 12.0)))
                frame += 1
        # Fill remaining base holds.
        while frame < walk_out_start:
            schedule.append((frame, base.name, 1.0))
            frame += 1

        # Phase 5: walk-out.
        for i in range(walk_out_start, total_frames):
            schedule.append((i, "walk_out", 0.0))

        return schedule


# ---------------------------------------------------------------------------
# Body life / micro-motion engine (B1): idle bounce, breathing, weight shift.
# ---------------------------------------------------------------------------

def body_life_params(t: float, fps: float, pose_cycle_t: float) -> dict:
    """
    Returns additive offsets for a single body that make the static pose feel
    alive (B1). Applied on top of the rendered pose so the artist doesn't need
    to draw every micro-motion by hand.

    Guidelines applied:
    - Breathing: slow sinusoidal, 3.5-4.5s cycle, ±1.5-2.5px torso.
    - Idle bounce: foot/subtle vertical on a 1.2-1.6s cycle, smaller amplitude.
    - Weight shift: very slow lateral shift between poses, ~6-8s cycle, ±1-2px.
    - Micro-ought-to-look-natural: small high-freq jitter on hands (barely
      perceptible) to avoid the "frozen vector" look.
    """
    return {
        "breath_y": math.sin(t * 2.0 * math.pi / 4.0) * 2.0,
        "bounce_y": math.sin(t * 2.0 * math.pi / 1.4) * 0.6,
        "weight_x": math.sin(t * 2.0 * math.pi / 7.0) * 1.2,
        "hand_jitter_x": math.sin(t * 18.0) * 0.4,
        "hand_jitter_y": math.sin(t * 23.0 + 0.7) * 0.4,
        "head_settle_y": math.sin(t * 2.0 * math.pi / 5.2) * 0.8,
        "head_settle_x": math.sin(t * 2.0 * math.pi / 6.8) * 0.5,
    }


# ---------------------------------------------------------------------------
# Easing helpers (B1).
# ---------------------------------------------------------------------------

def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_sine(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return -(math.cos(math.pi * t) - 1.0) / 2.0


def ease_out_elastic(t: float) -> float:
    """Soft overshoot for pose transitions (used for walk-in/enter)."""
    t = max(0.0, min(1.0, t))
    if t == 0 or t == 1:
        return t
    c4 = (2.0 * math.pi) / 3.0
    return math.pow(2.0, -10.0 * t) * math.sin((t * 10.0 - 0.75) * c4) + 1.0


# ---------------------------------------------------------------------------
# Selection helpers for video.py integration (B6/B7).
# ---------------------------------------------------------------------------

def select_pose_for_bullet(
    bullet_index: int,
    total_bullets: int,
    elapsed: float,
    prev_pose: Optional[str],
) -> str:
    """
    Decide which pose the character should be in while a given bullet is
    visible. Keeps it varied but not chaotic: every 2-3 bullets we cycle to
    a fresh pose if available; otherwise hold.
    """
    # Keep a small rotation to avoid the same pose for every bullet.
    rotation = ["explain_open", "point", "list", "think", "explain_open", "rest"]
    idx = (bullet_index // max(1, total_bullets // len(rotation))) % len(rotation)
    chosen = rotation[idx]
    if prev_pose == chosen:
        chosen = "rest"
    return chosen


def gaze_targets_for_scene(
    bullet_positions: List[Tuple[float, float]],  # (x,y) screen-rel -1..1
    title_position: Tuple[float, float],
    character_position: str,  # "left" | "right" | "center"
) -> List[GazeTarget]:
    """
    Build GazeTarget list from on-screen content positions.

    - Title is the primary look when no bullet is active.
    - Active bullet gets higher weight.
    - If character is on the right, gaze is biased slightly toward the left
      (toward the text), and vice versa.
    """
    base_bias = 0.0
    if character_position == "right":
        base_bias = -0.12
    elif character_position == "left":
        base_bias = 0.12
    targets = [GazeTarget(title_position[0] + base_bias, title_position[1], weight=0.4)]
    for (bx, by) in bullet_positions:
        targets.append(GazeTarget(bx + base_bias, by, weight=0.8))
    return targets
