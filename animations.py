"""
Animation Engine — applies smooth motion effects to PIL images and numpy
arrays.  All animations are computed as per-frame transformations that
return RGBA numpy arrays ready for compositing.

Supported effects:
  - Ken Burns (slow zoom + pan on a static image)
  - Parallax (multi-layer depth illusion)
  - Fade in / fade out
  - Slide in from edge
  - Zoom pulse (breathing effect)
  - Pan scan (horizontal or vertical sweep)
  - Float (gentle vertical bob)
  - Typewriter reveal (progressive mask)

Design goals:
  - Pure numpy/PIL (no GPU, no external deps)
  - Deterministic (seeded RNG for reproducibility)
  - Stateless (each call returns a fresh frame array)
  - Composable (effects can be chained via Composition)
"""

import math
from typing import Tuple, Optional, List, Callable

import numpy as np
from PIL import Image, ImageFilter


# ---------------------------------------------------------------------------
# Easing functions
# ---------------------------------------------------------------------------
def ease_linear(t: float) -> float:
    return t

def ease_in_quad(t: float) -> float:
    return t * t

def ease_out_quad(t: float) -> float:
    return t * (2 - t)

def ease_in_out_quad(t: float) -> float:
    return 2 * t * t if t < 0.5 else -1 + (4 - 2 * t) * t

def ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4 * t * t * t
    return 1 - (-2 * t + 2) ** 3 / 2

def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# Low-level frame operations
# ---------------------------------------------------------------------------
def _ensure_rgba(arr: np.ndarray) -> np.ndarray:
    """Ensure array is HxWx4 RGBA."""
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr, np.full_like(arr, 255)], axis=-1)
    elif arr.shape[2] == 3:
        alpha = np.full(arr.shape[:2] + (1,), 255, dtype=arr.dtype)
        arr = np.concatenate([arr, alpha], axis=-1)
    return arr


def _resize_image(img: Image.Image, w: int, h: int) -> np.ndarray:
    """Resize a PIL Image to (w, h) and return RGBA numpy array."""
    resized = img.resize((w, h), Image.LANCZOS)
    return _ensure_rgba(np.array(resized))


def _alpha_composite(base: np.ndarray, overlay: np.ndarray,
                     x: int = 0, y: int = 0) -> None:
    """Alpha-composite overlay onto base in-place (base must be mutable)."""
    oh, ow = overlay.shape[:2]
    bh, bw = base.shape[:2]
    # Clip to bounds
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(bw, x + ow)
    y1 = min(bh, y + oh)
    ox0 = x0 - x
    oy0 = y0 - y
    ox1 = ox0 + (x1 - x0)
    oy1 = oy0 + (y1 - y0)
    if x1 <= x0 or y1 <= y0:
        return
    src = overlay[oy0:oy1, ox0:ox1].astype(np.float32)
    dst = base[y0:y1, x0:x1].astype(np.float32)
    src_a = src[:, :, 3:4] / 255.0
    inv_a = 1.0 - src_a
    blended = src[:, :, :3] * src_a + dst[:, :, :3] * inv_a
    base[y0:y1, x0:x1, :3] = blended.astype(np.uint8)
    base[y0:y1, x0:x1, 3] = np.maximum(
        src[:, :, 3], (dst[:, :, 3] * inv_a.squeeze() + src[:, :, 3] * src_a.squeeze()).astype(np.uint8)
    )


# ---------------------------------------------------------------------------
# Animation effects
# ---------------------------------------------------------------------------
class AnimationEffect:
    """Base class for animation effects.  Subclasses implement apply()."""

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        """Apply the effect to a frame at time t.

        Args:
            frame: HxWx4 RGBA numpy array (will not be mutated).
            t: Current time in seconds (0 <= t <= duration).
            duration: Total duration of the effect.
        Returns:
            Transformed HxWx4 RGBA numpy array.
        """
        raise NotImplementedError


class KenBurns(AnimationEffect):
    """Slow zoom + pan on a static image (museum documentary effect).

    The image starts slightly larger than the canvas and slowly zooms in
    while panning from one corner to the opposite.
    """

    def __init__(self, zoom_start: float = 1.08, zoom_end: float = 1.20,
                 pan_x: float = 0.3, pan_y: float = 0.2):
        self.zoom_start = zoom_start
        self.zoom_end = zoom_end
        self.pan_x = pan_x  # -1.0 to 1.0 (left to right)
        self.pan_y = pan_y  # -1.0 to 1.0 (top to bottom)

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        progress = smoothstep(t / max(duration, 0.001))
        zoom = self.zoom_start + (self.zoom_end - self.zoom_start) * progress
        h, w = frame.shape[:2]
        # Compute crop region
        crop_w = int(w / zoom)
        crop_h = int(h / zoom)
        # Pan offset
        max_pan_x = (w - crop_w) // 2
        max_pan_y = (h - crop_h) // 2
        cx = int(max_pan_x + self.pan_x * max_pan_x * progress)
        cy = int(max_pan_y + self.pan_y * max_pan_y * progress)
        # Clamp
        cx = max(0, min(w - crop_w, cx))
        cy = max(0, min(h - crop_h, cy))
        crop = frame[cy:cy + crop_h, cx:cx + crop_w]
        # Resize back to canvas size
        result = np.array(Image.fromarray(crop).resize((w, h), Image.LANCZOS))
        return _ensure_rgba(result)


class Parallax(AnimationEffect):
    """Multi-layer depth illusion.  Background moves slower than foreground.

    Given a list of (layer_image, depth, speed_x, speed_y), each layer
    is composited with its own motion offset.
    """

    def __init__(self, layers: Optional[List[Tuple[np.ndarray, float, float, float]]] = None):
        """layers: list of (RGBA array, depth 0-1, speed_x, speed_y)."""
        self.layers = layers or []

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        result = frame.copy()
        for layer_img, depth, speed_x, speed_y in self.layers:
            h, w = result.shape[:2]
            lh, lw = layer_img.shape[:2]
            # Depth controls how much the layer moves (closer = more)
            factor = 1.0 - depth  # depth 0 = far, 1 = near
            offset_x = int(speed_x * t * factor * 50)
            offset_y = int(speed_y * t * factor * 50)
            # Resize layer to canvas if needed
            if lw != w or lh != h:
                layer_resized = np.array(
                    Image.fromarray(_ensure_rgba(layer_img)).resize((w, h), Image.LANCZOS)
                )
            else:
                layer_resized = layer_img
            # Apply offset
            if offset_x != 0 or offset_y != 0:
                shifted = np.zeros_like(layer_resized)
                sx = max(0, offset_x)
                sy = max(0, offset_y)
                dx = max(0, -offset_x)
                dy = max(0, -offset_y)
                copy_w = min(lw - sx, w - dx)
                copy_h = min(lh - sy, h - dy)
                if copy_w > 0 and copy_h > 0:
                    shifted[dy:dy + copy_h, dx:dx + copy_w] = layer_resized[sy:sy + copy_h, sx:sx + copy_w]
                layer_resized = shifted
            _alpha_composite(result, layer_resized)
        return result


class FadeIn(AnimationEffect):
    """Fade in from transparent (or black)."""

    def __init__(self, fade_duration: float = 0.8):
        self.fade_duration = fade_duration

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        alpha = min(1.0, t / self.fade_duration)
        alpha = smoothstep(alpha)
        result = frame.copy()
        result[:, :, 3] = (result[:, :, 3].astype(np.float32) * alpha).astype(np.uint8)
        return result


class FadeOut(AnimationEffect):
    """Fade out to transparent."""

    def __init__(self, fade_duration: float = 0.8):
        self.fade_duration = fade_duration

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        remaining = duration - t
        alpha = min(1.0, remaining / self.fade_duration)
        alpha = smoothstep(alpha)
        result = frame.copy()
        result[:, :, 3] = (result[:, :, 3].astype(np.float32) * alpha).astype(np.uint8)
        return result


class SlideIn(AnimationEffect):
    """Slide in from an edge with easing."""

    def __init__(self, direction: str = "left", slide_duration: float = 0.8):
        self.direction = direction
        self.slide_duration = slide_duration

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        if t >= self.slide_duration:
            return frame
        progress = smoothstep(t / self.slide_duration)
        h, w = frame.shape[:2]
        result = np.zeros_like(frame)

        if self.direction == "left":
            offset_x = int(-w * (1 - progress))
            sx = max(0, -offset_x)
            dx = max(0, offset_x)
            cw = min(w - sx, w - dx)
            result[:, dx:dx + cw] = frame[:, sx:sx + cw]
        elif self.direction == "right":
            offset_x = int(w * (1 - progress))
            sx = max(0, -offset_x)
            dx = max(0, offset_x)
            cw = min(w - sx, w - dx)
            result[:, dx:dx + cw] = frame[:, sx:sx + cw]
        elif self.direction == "top":
            offset_y = int(-h * (1 - progress))
            sy = max(0, -offset_y)
            dy = max(0, offset_y)
            ch = min(h - sy, h - dy)
            result[dy:dy + ch] = frame[sy:sy + ch]
        elif self.direction == "bottom":
            offset_y = int(h * (1 - progress))
            sy = max(0, -offset_y)
            dy = max(0, offset_y)
            ch = min(h - sy, h - dy)
            result[dy:dy + ch] = frame[sy:sy + ch]
        else:
            return frame

        return result


class ZoomPulse(AnimationEffect):
    """Gentle zoom-in/zoom-out breathing effect."""

    def __init__(self, zoom_min: float = 1.0, zoom_max: float = 1.05,
                 cycles: float = 2.0):
        self.zoom_min = zoom_min
        self.zoom_max = zoom_max
        self.cycles = cycles

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        progress = t / max(duration, 0.001)
        # Sinusoidal breathing
        wave = (math.sin(progress * math.pi * 2 * self.cycles - math.pi / 2) + 1) / 2
        zoom = self.zoom_min + (self.zoom_max - self.zoom_min) * wave
        h, w = frame.shape[:2]
        crop_w = int(w / zoom)
        crop_h = int(h / zoom)
        cx = (w - crop_w) // 2
        cy = (h - crop_h) // 2
        crop = frame[cy:cy + crop_h, cx:cx + crop_w]
        result = np.array(Image.fromarray(crop).resize((w, h), Image.LANCZOS))
        return _ensure_rgba(result)


class PanScan(AnimationEffect):
    """Horizontal or vertical pan across a larger image."""

    def __init__(self, direction: str = "horizontal", overscan: float = 1.2):
        self.direction = direction
        self.overscan = overscan  # How much larger the source is than canvas

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        h, w = frame.shape[:2]
        progress = smoothstep(t / max(duration, 0.001))

        if self.direction == "horizontal":
            src_w = int(w * self.overscan)
            src_h = h
            # Crop region moves from left to right
            crop_x = int((src_w - w) * progress)
            # We need to work with a stretched version
            stretched = np.array(Image.fromarray(_ensure_rgba(frame)).resize(
                (src_w, src_h), Image.LANCZOS))
            result = stretched[:, crop_x:crop_x + w].copy()
        else:
            src_w = w
            src_h = int(h * self.overscan)
            stretched = np.array(Image.fromarray(_ensure_rgba(frame)).resize(
                (src_w, src_h), Image.LANCZOS))
            crop_y = int((src_h - h) * progress)
            result = stretched[crop_y:crop_y + h].copy()

        return _ensure_rgba(result)


class Float(AnimationEffect):
    """Gentle vertical bobbing motion."""

    def __init__(self, amplitude: float = 8.0, cycles: float = 1.5):
        self.amplitude = amplitude
        self.cycles = cycles

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        h, w = frame.shape[:2]
        progress = t / max(duration, 0.001)
        offset_y = int(self.amplitude * math.sin(progress * math.pi * 2 * self.cycles))

        result = np.zeros_like(frame)
        sy = max(0, -offset_y)
        dy = max(0, offset_y)
        ch = min(h - sy, h - dy)
        if ch > 0:
            result[dy:dy + ch] = frame[sy:sy + ch]
        return result


class TypewriterReveal(AnimationEffect):
    """Progressive mask reveal (left to right)."""

    def __init__(self, reveal_duration: float = 1.5):
        self.reveal_duration = reveal_duration

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        progress = smoothstep(min(1.0, t / self.reveal_duration))
        h, w = frame.shape[:2]
        result = frame.copy()
        mask_x = int(w * progress)
        if mask_x < w:
            result[:, mask_x:, 3] = 0
        return result


# ---------------------------------------------------------------------------
# Composition — chain multiple effects
# ---------------------------------------------------------------------------
class AnimationComposition:
    """Chain multiple effects together in sequence or parallel.

    Usage::

        comp = AnimationComposition([
            (KenBurns(), 0.0, duration),      # effect, start_time, end_time
            (FadeIn(0.8), 0.0, 0.8),
            (FadeOut(0.8), duration - 0.8, duration),
        ])
        frame = comp.apply(base_image, t, duration)
    """

    def __init__(self, effects: Optional[List[Tuple[AnimationEffect, float, float]]] = None):
        self.effects = effects or []

    def add(self, effect: AnimationEffect, start: float = 0.0, end: float = float('inf')):
        self.effects.append((effect, start, end))
        return self

    def apply(self, frame: np.ndarray, t: float, duration: float,
              **kwargs) -> np.ndarray:
        result = frame.copy()
        for effect, start, end in self.effects:
            if start <= t <= end:
                # Remap t to local effect time
                local_t = t - start
                local_dur = end - start
                result = effect.apply(result, local_t, local_dur, **kwargs)
        return result


# ---------------------------------------------------------------------------
# Preset compositions for common scene types
# ---------------------------------------------------------------------------
def preset_kin_burns(duration: float, direction: str = "in") -> AnimationComposition:
    """Standard Ken Burns with fade in/out."""
    zoom = KenBurns(zoom_start=1.08, zoom_end=1.20,
                    pan_x=0.3 if direction == "in" else -0.3,
                    pan_y=0.2)
    comp = AnimationComposition()
    comp.add(zoom, 0.0, duration)
    comp.add(FadeIn(0.6), 0.0, 0.6)
    comp.add(FadeOut(0.6), duration - 0.6, duration)
    return comp


def preset_slide_in(direction: str = "left", duration: float = 0.8) -> AnimationComposition:
    """Slide in with Ken Burns background."""
    comp = AnimationComposition()
    comp.add(SlideIn(direction, duration), 0.0, duration)
    return comp


def preset_zoom_breathe(duration: float) -> AnimationComposition:
    """Gentle zoom pulse with fade in."""
    comp = AnimationComposition()
    comp.add(ZoomPulse(zoom_min=1.0, zoom_max=1.04, cycles=1.5), 0.0, duration)
    comp.add(FadeIn(0.8), 0.0, 0.8)
    return comp


def preset_pan_scan(direction: str = "horizontal", duration: float = 8.0) -> AnimationComposition:
    """Pan scan with fade."""
    comp = AnimationComposition()
    comp.add(PanScan(direction, overscan=1.15), 0.0, duration)
    comp.add(FadeIn(0.6), 0.0, 0.6)
    comp.add(FadeOut(0.6), duration - 0.6, duration)
    return comp


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from PIL import Image as PILImage

    # Create a test image
    test_img = PILImage.new("RGBA", (1280, 720), (30, 30, 50, 255))
    draw = PILImageDraw.Draw(test_img) if hasattr(PILImage, 'ImageDraw') else None
    # Draw some content
    from PIL import ImageDraw
    d = ImageDraw.Draw(test_img)
    d.rectangle([100, 100, 1180, 620], fill=(50, 50, 80, 255))
    d.text((400, 340), "TEST FRAME", fill=(255, 255, 255, 255))

    frame = np.array(test_img)
    duration = 5.0
    fps = 24

    # Test each effect
    effects = {
        "KenBurns": KenBurns(),
        "FadeIn": FadeIn(1.0),
        "SlideIn": SlideIn("left", 1.0),
        "ZoomPulse": ZoomPulse(),
        "Float": Float(),
        "TypewriterReveal": TypewriterReveal(2.0),
    }

    for name, effect in effects.items():
        # Just test a single frame at t=1.0
        result = effect.apply(frame, 1.0, duration)
        assert result.shape == frame.shape, f"{name} shape mismatch"
        print(f"  {name}: OK (shape={result.shape})")

    # Test composition
    comp = preset_kin_burns(duration)
    result = comp.apply(frame, 2.5, duration)
    assert result.shape == frame.shape
    print(f"  Composition: OK (shape={result.shape})")

    print("\nAll animation tests passed!")
