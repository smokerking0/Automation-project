"""
Scene Visualizer — orchestrates text-to-image generation and animation for
each video scene.

This module has two responsibilities:
  1. Transform a Scene's text/bullets into a structured visual layout
     (via t2i.TextToImageGenerator).
  2. Animate that generated visual in sync with the scene's narration
     (via animations.AnimationComposition).

The result is a callable `make_frame(t) -> HxWx4 RGBA numpy array` that the
existing video renderer composites on top of (or replaces) its background.

Usage::

    from visualizer import SceneVisualizer
    vis = SceneVisualizer(width=1280, height=720)
    visual = vis.visualize(scene, duration=8.0)
    # visual.render(t) -> np.ndarray  (per-frame callable)
"""

import re
from typing import List, Optional, Dict, Tuple, Any, Callable

import numpy as np
from PIL import Image

from t2i import TextToImageGenerator, analyze_content
from animations import (
    AnimationComposition, AnimationEffect,
    KenBurns, FadeIn, SlideIn, ZoomPulse, PanScan, Float, TypewriterReveal,
    preset_kin_burns, preset_zoom_breathe, preset_pan_scan, preset_slide_in,
)

# ---------------------------------------------------------------------------
# Content parsing helpers
# ---------------------------------------------------------------------------
def extract_bullets(scene_text: str, bullet_points: Optional[List[str]] = None) -> List[str]:
    """Extract a list of key points from a scene.

    1. If the scene already has bullet_points, use them.
    2. Otherwise split on sentences / list markers.
    """
    if bullet_points:
        return [b for b in bullet_points if (b or "").strip()]

    # Try to find numbered / dashed list items
    lines = [l.strip() for l in scene_text.split("\n") if l.strip()]
    parsed = []
    list_re = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$")
    for line in lines:
        m = list_re.match(line)
        if m:
            parsed.append(m.group(1).strip())

    # Fallback: split on sentences, take the first N
    if not parsed:
        sentences = re.split(r"(?<=[.!?])\s+", scene_text)
        parsed = [s for s in sentences if s.strip()]
        if len(parsed) > 6:
            parsed = parsed[:6]

    return parsed


def extract_stats(scene_text: str) -> List[Dict[str, str]]:
    """Extract numeric statistics from text for stats_card layouts."""
    stats: List[Dict[str, str]] = []
    # Patterns: "NN%", "NNx", "NN minutes", "NN million", "@NN"
    patterns = [
        (r"(\d+(?:\.\d+)?%)\s+(.{2,30}?)(?=[,.;]|$)", "percent"),
        (r"(\d+(?:\.\d+)?\s*(?:x|times?))\s+(.{2,30}?)(?=[,.;]|$)", "times"),
        (r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|sec(?:onds)?)?\s+(.{2,30}?)(?=[,.;]|$)", "count"),
    ]
    for pattern, kind in patterns:
        for m in re.finditer(pattern, scene_text, re.IGNORECASE):
            value = m.group(1).strip()
            label = m.group(2).strip().rstrip(".,")
            if label and len(label) <= 40:
                stats.append({"value": value, "label": label, "kind": kind})
                if len(stats) >= 4:
                    return stats

    # Fallback if no explicit match
    numbers = re.findall(r"\b(\d{2,3}(?:\.\d+)?)\b", scene_text)
    if numbers:
        for i, n in enumerate(numbers[:4]):
            stats.append({"value": n, "label": "metric", "kind": "number"})
    return stats[:4]


def extract_steps(scene_text: str) -> List[str]:
    """Extract ordered steps for flowchart layouts."""
    steps = []
    list_re = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$")
    for line in scene_text.split("\n"):
        m = list_re.match(line)
        if m:
            steps.append(m.group(1).strip())
    if not steps:
        words = re.split(r"[\s,;]+", scene_text)
        if len(words) >= 5:
            steps = words[:5]
    return steps[:5]


def extract_timeline(scene_text: str) -> List[str]:
    """Extract timeline events (with dates/years if available)."""
    events = []
    years = re.findall(r"(19|20)\d{2}", scene_text)
    year_text = re.split(r"(?<=[.!?])\s+", scene_text)
    if years:
        # Split into chunks around years
        for i, y in enumerate(years):
            chunk_start = scene_text.find(y)
            if chunk_start == -1:
                continue
            chunk = scene_text[chunk_start:chunk_start + 80].split(".")[0].strip()
            events.append(f"{y}: {chunk[:50]}")
    elif year_text:
        events = [s for s in year_text if s.strip()][:6]
    return events[:6]


# ---------------------------------------------------------------------------
# Frame producer — wraps composition into a per-time callable
# ---------------------------------------------------------------------------
class GeneratedVisual:
    """Holds the base image + configured animation and renders per-frame."""

    def __init__(self, base_array: np.ndarray,
                 composition: AnimationComposition,
                 duration: float):
        self.base = base_array
        self.composition = composition
        self.duration = duration

    def render(self, t: float) -> np.ndarray:
        """Return the frame at time *t* (0 <= t <= duration)."""
        frame = self.composition.apply(self.base.copy(), t, self.duration)
        return frame

    @property
    def shape(self) -> Tuple[int, int, int]:
        return self.base.shape


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
class SceneVisualizer:
    """Transforms a scene's text into an animated visual.

    The visualizer composes remaining text (heading, voiceover narration,
    character, bullets) on top of the generated visual so information
    stays complete.
    """

    def __init__(self, width: int = 1280, height: int = 720,
                 palette: Optional[Dict[str, str]] = None,
                 font_path: str = "C:/Windows/Fonts/comic.ttf",
                 font_size: int = 24):
        self.w = width
        self.h = height
        self.palette = palette
        self.font_path = font_path
        self.font_size = font_size
        self._t2i = TextToImageGenerator(
            width=width, height=height, palette=palette,
            font_path=font_path, font_size=font_size)

    # -- Layout selection --------------------------------------------------
    def select_layout(self, scene_text: str,
                      bullet_points: Optional[List[str]] = None,
                      diagram_type: Optional[str] = None,
                      flags: Optional[Dict[str, Any]] = None) -> str:
        flags = flags or {}
        # Explicit override from flags
        if flags.get("layout"):
            return flags["layout"]
        if flags.get("data_kind"):
            mapping = {
                "steps": "flowchart",
                "comparison": "comparison",
                "stats": "stats_card",
                "timeline": "timeline_h",
                "events": "timeline",
                "attributes": "icon_grid",
            }
            return mapping.get(flags["data_kind"], "infographic")
        return analyze_content(scene_text, bullet_points, diagram_type)

    # -- Animation selection ----------------------------------------------
    def select_animation(self, scene_animation: str = "fade",
                         scene_index: int = 0,
                         duration: float = 6.0,
                         flags: Optional[Dict[str, Any]] = None) -> AnimationComposition:
        """Choose an animation composition based on the scene's animation field."""
        flags = flags or {}
        name = flags.get("motion") or scene_animation

        motion_map = {
            "ken_burns": preset_kin_burns,
            "zoom_breathe": preset_zoom_breathe,
            "pan_scan": preset_pan_scan,
            "slide_left": lambda d: preset_slide_in("left", 0.8),
            "slide_right": lambda d: preset_slide_in("right", 0.8),
            "slide_top": lambda d: preset_slide_in("top", 0.8),
            "slide_bottom": lambda d: preset_slide_in("bottom", 0.8),
        }

        if name in motion_map:
            return motion_map[name](duration)

        # Default based on animation style
        if scene_animation == "fade":
            return preset_kin_burns(duration)
        if scene_animation == "slide_up" or scene_animation == "slide":
            return preset_slide_in("bottom", 0.8)
        if scene_animation == "slide_down":
            return preset_slide_in("top", 0.8)
        return preset_zoom_breathe(duration)

    # -- Build the visual per scene ---------------------------------------
    def visualize(self, scene_text: str,
                  bullet_points: Optional[List[str]] = None,
                  diagram_type: Optional[str] = None,
                  diagram_data: Optional[Dict[str, Any]] = None,
                  duration: float = 6.0,
                  scene_animation: str = "fade",
                  scene_index: int = 0,
                  flags: Optional[Dict[str, Any]] = None,
                  title: Optional[str] = None,
                  ) -> Optional[GeneratedVisual]:
        """Generate an animated visual for the scene.

        Args:
            scene_text: The scene's on-screen heading/text.
            bullet_points: Optional bullet list (from Scene).
            diagram_type: Optional diagram type hint.
            diagram_data: Optional structured data for the diagram.
            duration: Scene duration in seconds.
            scene_animation: Scene animation style.
            scene_index: Index in the video.
            flags: Control flags (layout, motion, data_kind...).
            title: Optional custom title for the layout (defaults to scene_text).

        Returns:
            GeneratedVisual or None if the scene isn't visualizable.
        """
        flags = flags or {}

        # Skip scenes that are purely audio/hook/intro/outro
        text_lower = (scene_text or "").lower()
        if any(kw in text_lower for kw in ("core infotech",
                                           "thanks for watch",
                                           "like & subscribe",
                                           "key takeaways")):
            return None

        layout = self.select_layout(scene_text, bullet_points, diagram_type, flags)
        vis_title = title or scene_text or ""

        # Prepare the structured data for the layout
        formatted: Dict[str, Any] = {"title": vis_title, "accent": flags.get("accent", "")}

        if layout == "infographic":
            formatted["bullets"] = extract_bullets(scene_text, bullet_points)
        elif layout == "flowchart":
            formatted["steps"] = extract_steps(scene_text)
        elif layout == "comparison":
            l, r = self._split_comparison(scene_text, bullet_points)
            formatted["left_title"] = l[0]
            formatted["left_items"] = l[1]
            formatted["right_title"] = r[0]
            formatted["right_items"] = r[1]
        elif layout == "stats_card":
            formatted["stats"] = extract_stats(scene_text)
        elif layout in ("timeline", "timeline_h"):
            formatted["events"] = extract_timeline(scene_text)
        elif layout == "list":
            formatted["items"] = extract_bullets(scene_text, bullet_points)
        elif layout == "icon_grid":
            formatted["items"] = self._make_icon_items(extract_bullets(scene_text, bullet_points))
        elif layout == "title_card":
            formatted["title"] = vis_title
            formatted["subtitle"] = flags.get("subtitle", "")

        # Generate the base image
        try:
            pil_img = self._t2i.generate(layout, **formatted)
        except Exception as exc:
            print(f"  [visualizer] layout '{layout}' failed: {exc}")
            return None

        base_array = np.array(pil_img)

        # If the generated image is mostly transparent (empty), skip it
        if base_array[:, :, 3].max() < 8:
            return None

        composition = self.select_animation(scene_animation, scene_index, duration, flags)
        return GeneratedVisual(base_array, composition, duration)

    # -- Helpers ------------------------------------------------------------
    def _split_comparison(self, text: str,
                          bullets: Optional[List[str]]) -> Tuple[List[str], List[str]]:
        """Split content into two comparison columns."""
        if bullets and len(bullets) >= 2:
            mid = (len(bullets) + 1) // 2
            return (["Option A", bullets[:mid]],
                    ["Option B", bullets[mid:]])
        # Parse "Left vs Right: ..." style
        parts = re.split(r"\s+vs\.?\s+|\s+versus\s+|\s+vs\s+", text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) >= 2:
            lw = re.split(r"[,;:\-]", text)[0] if text else ""
            return (["Left", parts[0].split(":")[0][:30]],
                    ["Right", text[len(parts[0]):len(parts[0]) + 60].split(":")[-1][:60]])
        return (["Left", []], ["Right", []])

    def _make_icon_items(self, labels: List[str]) -> List[Dict[str, str]]:
        """Create icon_grid items from labels (emoji-free, uses geometric tiles)."""
        tiles = ["▰", "◈", "◆", "●", "▲", "★", "▪", "▸"]
        items = []
        for i, label in enumerate(labels[:8]):
            items.append({"icon": tiles[i % len(tiles)], "label": label})
        return items


# ---------------------------------------------------------------------------
# Convenience factory — builds a frame callable for the video renderer
# ---------------------------------------------------------------------------
def build_scene_visual(width: int, height: int, scene_text: str,
                       bullet_points: Optional[List[str]] = None,
                       diagram_type: Optional[str] = None,
                       duration: float = 6.0,
                       scene_animation: str = "fade",
                       scene_index: int = 0,
                       palette: Optional[Dict[str, str]] = None,
                       flags: Optional[Dict[str, Any]] = None,
                       visualizer: Optional[SceneVisualizer] = None,
                       font_path: str = "C:/Windows/Fonts/comic.ttf",
                       font_size: int = 24,
                       ) -> Optional[Callable[[float], np.ndarray]]:
    """One-function shortcut used by the video pipeline.

    Returns a `make_frame(t)` callable or None when no visual applies.
    """
    sv = visualizer or SceneVisualizer(width=width, height=height,
                                       palette=palette, font_path=font_path,
                                       font_size=font_size)
    visual = sv.visualize(
        scene_text=scene_text,
        bullet_points=bullet_points,
        diagram_type=diagram_type,
        duration=duration,
        scene_animation=scene_animation,
        scene_index=scene_index,
        flags=flags,
    )
    if visual is None:
        return None
    return visual.render


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Build a few representative scenes
    scenes = [
        ("RAG vs Fine-tuning", ["RAG retrieves from external knowledge", "Fine-tuning trains model weights"],
         "comparison"),
        ("How RAG Works in 5 Steps", ["Query → retrieve → augment → generate → output"], "flowchart"),
        ("Channel Growth 2026", ["50% avg retention", "4.2% CTR", "90 min watch time"], "stats_card"),
        ("A Brief History of AI", ["1960: Perceptron", "1997: Deep Blue", "2012: AlexNet", "2023: GPT"], "timeline"),
        ("Key Takeaways", ["Watch time is #1", "CTR 4%+ target", "Session depth matters"], "infographic"),
    ]

    vis = SceneVisualizer(1280, 720)

    for title, bullets, expected in scenes:
        layout = vis.select_layout(title, bullets)
        visual = vis.visualize(title, bullets, duration=5.0)
        status = "OK" if visual is not None else "SKIP"
        print(f"  [{status}] '{title}' -> layout={layout}, shape={visual.shape if visual else '-'}")

    print("\nAll visualizer tests passed!")