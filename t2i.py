"""
Text-to-Image Generator — converts scene text/content into structured visual
layouts (infographics, flowcharts, comparisons, stats cards, timelines).

All rendering is PIL-based (no GPU required).  The generated images are RGBA
numpy arrays that can be composited onto any background by the animation
engine or the existing video pipeline.

Design goals:
  - Deterministic (seeded RNG for reproducibility)
  - Modular (each generator method is independent)
  - Configurable (colors, fonts, spacing via constructor params)
  - Stateless (no side effects; each call returns a fresh image)
"""

import math
import textwrap
from typing import List, Tuple, Optional, Dict, Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Default palette (matches channel branding)
# ---------------------------------------------------------------------------
DEFAULT_PALETTE = {
    "bg_dark":      "#0D1117",
    "bg_card":      "#161B22",
    "accent":       "#00E5FF",
    "accent2":      "#FFD700",
    "text_primary": "#FFFFFF",
    "text_dim":     "#8B949E",
    "success":      "#3FB950",
    "danger":       "#F85149",
    "warning":      "#D29922",
    "border":       "#30363D",
    "glow":         "#00E5FF",
}

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------
# Global font size scale. TextToImageGenerator.__init__ sets it from the
# visualizer font_size setting (24 = 1.0, no change); layouts call the
# module-level _load_font() so they all respect the scale automatically.
# ---------------------------------------------------------------------------
_FONT_SCALE = 1.0


def _load_font(size: int, bold: bool = False, path: str = "") -> ImageFont.FreeTypeFont:
    """Load a TrueType font, falling back to the default bitmap font."""
    size = max(int(size * _FONT_SCALE), 6)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/comicbd.ttf" if bold else "C:/Windows/Fonts/comic.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    for fp in candidates:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    """Return (width, height) of rendered text, compatible across PIL versions."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


# ---------------------------------------------------------------------------
# Shape helpers
# ---------------------------------------------------------------------------
def _rounded_rect(draw: ImageDraw.ImageDraw, xy: Tuple[int, int, int, int],
                  radius: int, fill: str, outline: str = "", width: int = 0):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    r = min(radius, (x1 - x0) // 4, (y1 - y0) // 4)
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline or None, width=width)


def _arrow_right(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str):
    """Draw a right-pointing arrow."""
    s = size
    pts = [(x, y - s // 2), (x + s, y), (x, y + s // 2)]
    draw.polygon(pts, fill=color)


def _chevron_right(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str):
    """Draw a chevron (>) symbol."""
    s = size
    draw.line([(x, y - s // 2), (x + s // 2, y), (x, y + s // 2)], fill=color, width=max(2, s // 8))


def _circle(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, fill: str, outline: str = ""):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline or None)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
class TextToImageGenerator:
    """Generates structured visual layouts from text content.

    Usage::

        gen = TextToImageGenerator(width=1280, height=720)
        img = gen.generate("infographic", title="RAG vs Fine-tuning",
                          bullets=["RAG retrieves", "Fine-tuning trains"])
        # img is a PIL RGBA Image
    """

    def __init__(self, width: int = 1280, height: int = 720,
                 palette: Optional[Dict[str, str]] = None,
                 font_path: str = "C:/Windows/Fonts/comic.ttf",
                 font_size: int = 24):
        self.w = width
        self.h = height
        self.pal = {**DEFAULT_PALETTE, **(palette or {})}
        self.font_path = font_path
        global _FONT_SCALE
        _FONT_SCALE = max(font_size / 24.0, 0.4)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate(self, layout: str, **kwargs) -> Image.Image:
        """Dispatch to a specific layout generator.

        Supported layouts:
          - infographic    (title + bullet cards)
          - flowchart      (connected step boxes)
          - comparison     (side-by-side columns)
          - stats_card     (large number + label)
          - timeline       (vertical timeline with events)
          - timeline_h     (horizontal timeline with events)
          - list           (numbered list with icons)
          - icon_grid      (grid of icon + label tiles)
          - title_card     (big title + subtitle)
        """
        method = getattr(self, f"_layout_{layout}", None)
        if method is None:
            raise ValueError(f"Unknown layout '{layout}'. "
                             f"Available: infographic, flowchart, comparison, "
                             f"stats_card, timeline, timeline_h, list, icon_grid, title_card")
        return method(**kwargs)

    # ------------------------------------------------------------------
    # Layout: infographic  (title + bullet cards)
    # ------------------------------------------------------------------
    def _layout_infographic(self, title: str = "", bullets: Optional[List[str]] = None,
                            accent: str = "", **kw) -> Image.Image:
        bullets = bullets or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Title
        if title:
            tf = _load_font(42, bold=True, path=self.font_path)
            tw, th = _text_size(draw, title, tf)
            tx = (self.w - tw) // 2
            ty = 40
            draw.text((tx, ty), title, fill=self.pal["text_primary"], font=tf)
            # Accent underline
            draw.rectangle([tx, ty + th + 6, tx + tw, ty + th + 10], fill=ac)

        # Bullet cards
        card_h = 72
        card_gap = 16
        card_margin_x = 60
        start_y = 110 if title else 40
        bf = _load_font(26, path=self.font_path)
        nf = _load_font(20, bold=True, path=self.font_path)

        for i, bullet in enumerate(bullets[:6]):
            cy = start_y + i * (card_h + card_gap)
            if cy + card_h > self.h - 60:
                break
            # Card background
            _rounded_rect(draw, (card_margin_x, cy, self.w - card_margin_x, cy + card_h),
                          radius=12, fill=self.pal["bg_card"], outline=self.pal["border"], width=1)
            # Number badge
            badge_cx = card_margin_x + 36
            badge_cy = cy + card_h // 2
            _circle(draw, badge_cx, badge_cy, 22, fill=ac)
            num_str = str(i + 1)
            nw, nh = _text_size(draw, num_str, nf)
            draw.text((badge_cx - nw // 2, badge_cy - nh // 2 - 1),
                      num_str, fill=self.pal["bg_dark"], font=nf)
            # Bullet text
            txt_x = card_margin_x + 72
            wrapped = textwrap.shorten(bullet, width=80, placeholder="...")
            draw.text((txt_x, cy + (card_h - 30) // 2), wrapped,
                      fill=self.pal["text_primary"], font=bf)

        return img

    # ------------------------------------------------------------------
    # Layout: flowchart  (connected step boxes)
    # ------------------------------------------------------------------
    def _layout_flowchart(self, title: str = "", steps: Optional[List[str]] = None,
                          accent: str = "", **kw) -> Image.Image:
        steps = steps or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if title:
            tf = _load_font(36, bold=True, path=self.font_path)
            tw, _ = _text_size(draw, title, tf)
            draw.text(((self.w - tw) // 2, 30), title, fill=self.pal["text_primary"], font=tf)

        n = min(len(steps), 5)
        if n == 0:
            return img

        box_w = 200
        box_h = 70
        gap = 40
        total_w = n * box_w + (n - 1) * gap
        start_x = (self.w - total_w) // 2
        cy = self.h // 2 - box_h // 2
        bf = _load_font(22, path=self.font_path)

        for i, step in enumerate(steps[:n]):
            bx = start_x + i * (box_w + gap)
            _rounded_rect(draw, (bx, cy, bx + box_w, cy + box_h),
                          radius=10, fill=self.pal["bg_card"], outline=ac, width=2)
            wrapped = textwrap.shorten(step, width=24, placeholder="...")
            tw2, th2 = _text_size(draw, wrapped, bf)
            draw.text((bx + (box_w - tw2) // 2, cy + (box_h - th2) // 2),
                      wrapped, fill=self.pal["text_primary"], font=bf)
            # Arrow between boxes
            if i < n - 1:
                ax = bx + box_w + 6
                _chevron_right(draw, ax, cy + box_h // 2, gap - 12, ac)

        return img

    # ------------------------------------------------------------------
    # Layout: comparison  (side-by-side columns)
    # ------------------------------------------------------------------
    def _layout_comparison(self, title: str = "",
                           left_title: str = "Option A", left_items: Optional[List[str]] = None,
                           right_title: str = "Option B", right_items: Optional[List[str]] = None,
                           accent: str = "", **kw) -> Image.Image:
        left_items = left_items or []
        right_items = right_items or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        col_w = (self.w - 120) // 2
        col_gap = 40
        col_h = self.h - 140

        if title:
            tf = _load_font(36, bold=True, path=self.font_path)
            tw, _ = _text_size(draw, title, tf)
            draw.text(((self.w - tw) // 2, 25), title, fill=self.pal["text_primary"], font=tf)

        for col_idx, (col_title, items, col_x, col_ac) in enumerate([
            (left_title, left_items, 60, ac),
            (right_title, right_items, 60 + col_w + col_gap, self.pal["accent2"]),
        ]):
            # Column card
            _rounded_rect(draw, (col_x, 80, col_x + col_w, 80 + col_h),
                          radius=14, fill=self.pal["bg_card"], outline=self.pal["border"], width=1)
            # Column header
            hf = _load_font(28, bold=True, path=self.font_path)
            hw, hh = _text_size(draw, col_title, hf)
            draw.text((col_x + (col_w - hw) // 2, 96), col_title, fill=col_ac, font=hf)
            draw.rectangle([col_x + 30, 96 + hh + 8, col_x + col_w - 30, 96 + hh + 12], fill=col_ac)

            # Items
            bf = _load_font(22, path=self.font_path)
            iy = 96 + hh + 28
            for item in items[:5]:
                wrapped = textwrap.shorten(item, width=36, placeholder="...")
                draw.text((col_x + 28, iy), f"• {wrapped}", fill=self.pal["text_primary"], font=bf)
                iy += 38

        # VS divider
        vf = _load_font(30, bold=True, path=self.font_path)
        vw, vh = _text_size(draw, "VS", vf)
        vx = (self.w - vw) // 2
        vy = (self.h - vh) // 2
        _circle(draw, vx + vw // 2, vy + vh // 2, max(vw, vh) // 2 + 12, fill=self.pal["bg_dark"])
        draw.text((vx, vy), "VS", fill=ac, font=vf)

        return img

    # ------------------------------------------------------------------
    # Layout: stats_card  (large number + label)
    # ------------------------------------------------------------------
    def _layout_stats_card(self, title: str = "",
                           stats: Optional[List[Dict[str, str]]] = None,
                           accent: str = "", **kw) -> Image.Image:
        """stats: list of {"value": "99%", "label": "Retention"}"""
        stats = stats or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if title:
            tf = _load_font(36, bold=True, path=self.font_path)
            tw, _ = _text_size(draw, title, tf)
            draw.text(((self.w - tw) // 2, 30), title, fill=self.pal["text_primary"], font=tf)

        n = min(len(stats), 4)
        card_w = 240
        card_h = 160
        gap = 30
        total_w = n * card_w + (n - 1) * gap
        start_x = (self.w - total_w) // 2
        cy = (self.h - card_h) // 2 + 20

        for i, st in enumerate(stats[:n]):
            cx = start_x + i * (card_w + gap)
            _rounded_rect(draw, (cx, cy, cx + card_w, cy + card_h),
                          radius=12, fill=self.pal["bg_card"], outline=self.pal["border"], width=1)
            # Value
            vf = _load_font(48, bold=True, path=self.font_path)
            vw, vh = _text_size(draw, st.get("value", "0"), vf)
            draw.text((cx + (card_w - vw) // 2, cy + 24),
                      st.get("value", "0"), fill=ac, font=vf)
            # Label
            lf = _load_font(20, path=self.font_path)
            lw, lh = _text_size(draw, st.get("label", ""), lf)
            draw.text((cx + (card_w - lw) // 2, cy + 24 + vh + 16),
                      st.get("label", ""), fill=self.pal["text_dim"], font=lf)

        return img

    # ------------------------------------------------------------------
    # Layout: timeline  (vertical)
    # ------------------------------------------------------------------
    def _layout_timeline(self, title: str = "",
                         events: Optional[List[str]] = None,
                         accent: str = "", **kw) -> Image.Image:
        events = events or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if title:
            tf = _load_font(34, bold=True, path=self.font_path)
            tw, _ = _text_size(draw, title, tf)
            draw.text(((self.w - tw) // 2, 25), title, fill=self.pal["text_primary"], font=tf)

        n = min(len(events), 6)
        line_x = self.w // 2
        start_y = 90
        end_y = self.h - 50
        step = max(40, (end_y - start_y) // max(1, n - 1)) if n > 1 else 0

        bf = _load_font(22, path=self.font_path)

        for i, evt in enumerate(events[:n]):
            ey = start_y + i * step
            # Node
            _circle(draw, line_x, ey, 10, fill=ac)
            # Text (alternating sides)
            wrapped = textwrap.shorten(evt, width=40, placeholder="...")
            tw2, th2 = _text_size(draw, wrapped, bf)
            if i % 2 == 0:
                draw.text((line_x + 22, ey - th2 // 2), wrapped,
                          fill=self.pal["text_primary"], font=bf)
            else:
                draw.text((line_x - 22 - tw2, ey - th2 // 2), wrapped,
                          fill=self.pal["text_primary"], font=bf)
            # Connector line
            if i < n - 1:
                draw.line([(line_x, ey + 10), (line_x, ey + step - 10)],
                          fill=self.pal["border"], width=2)

        return img

    # ------------------------------------------------------------------
    # Layout: timeline_h  (horizontal)
    # ------------------------------------------------------------------
    def _layout_timeline_h(self, title: str = "",
                           events: Optional[List[str]] = None,
                           accent: str = "", **kw) -> Image.Image:
        events = events or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if title:
            tf = _load_font(34, bold=True, path=self.font_path)
            tw, _ = _text_size(draw, title, tf)
            draw.text(((self.w - tw) // 2, 30), title, fill=self.pal["text_primary"], font=tf)

        n = min(len(events), 6)
        line_y = self.h // 2
        margin = 100
        usable = self.w - 2 * margin
        step = usable // max(1, n - 1) if n > 1 else 0

        bf = _load_font(18, path=self.font_path)

        # Horizontal line
        draw.line([(margin, line_y), (self.w - margin, line_y)],
                  fill=self.pal["border"], width=3)

        for i, evt in enumerate(events[:n]):
            ex = margin + i * step if n > 1 else self.w // 2
            _circle(draw, ex, line_y, 10, fill=ac)
            wrapped = textwrap.shorten(evt, width=20, placeholder="...")
            tw2, th2 = _text_size(draw, wrapped, bf)
            draw.text((ex - tw2 // 2, line_y + 20), wrapped,
                      fill=self.pal["text_primary"], font=bf)

        return img

    # ------------------------------------------------------------------
    # Layout: list  (numbered list with icons)
    # ------------------------------------------------------------------
    def _layout_list(self, title: str = "",
                     items: Optional[List[str]] = None,
                     accent: str = "", **kw) -> Image.Image:
        items = items or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if title:
            tf = _load_font(36, bold=True, path=self.font_path)
            tw, _ = _text_size(draw, title, tf)
            draw.text(((self.w - tw) // 2, 30), title, fill=self.pal["text_primary"], font=tf)
            draw.rectangle([(self.w - tw) // 2, 76, (self.w + tw) // 2, 80], fill=ac)

        nf = _load_font(22, bold=True, path=self.font_path)
        bf = _load_font(24, path=self.font_path)
        start_y = 100 if title else 30
        row_h = 64
        margin_x = 80

        for i, item in enumerate(items[:8]):
            iy = start_y + i * row_h
            if iy + row_h > self.h - 40:
                break
            # Number
            _rounded_rect(draw, (margin_x, iy, margin_x + 44, iy + 48),
                          radius=8, fill=ac)
            nw, nh = _text_size(draw, str(i + 1), nf)
            draw.text((margin_x + 22 - nw // 2, iy + 24 - nh // 2),
                      str(i + 1), fill=self.pal["bg_dark"], font=nf)
            # Text
            wrapped = textwrap.shorten(item, width=70, placeholder="...")
            draw.text((margin_x + 60, iy + 12), wrapped,
                      fill=self.pal["text_primary"], font=bf)

        return img

    # ------------------------------------------------------------------
    # Layout: icon_grid  (grid of icon + label tiles)
    # ------------------------------------------------------------------
    def _layout_icon_grid(self, title: str = "",
                          items: Optional[List[Dict[str, str]]] = None,
                          accent: str = "", **kw) -> Image.Image:
        """items: list of {"icon": "🤖", "label": "AI"}"""
        items = items or []
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if title:
            tf = _load_font(34, bold=True, path=self.font_path)
            tw, _ = _text_size(draw, title, tf)
            draw.text(((self.w - tw) // 2, 25), title, fill=self.pal["text_primary"], font=tf)

        n = min(len(items), 8)
        cols = min(4, n)
        rows = math.ceil(n / cols) if cols > 0 else 0
        tile_w = 220
        tile_h = 140
        gap = 24
        total_w = cols * tile_w + (cols - 1) * gap
        total_h = rows * tile_h + (rows - 1) * gap
        start_x = (self.w - total_w) // 2
        start_y = (self.h - total_h) // 2 + 20

        lf = _load_font(20, path=self.font_path)

        for i, item in enumerate(items[:n]):
            r, c = divmod(i, cols)
            tx = start_x + c * (tile_w + gap)
            ty = start_y + r * (tile_h + gap)
            _rounded_rect(draw, (tx, ty, tx + tile_w, ty + tile_h),
                          radius=12, fill=self.pal["bg_card"], outline=self.pal["border"], width=1)
            label = item.get("label", "")
            lw, lh = _text_size(draw, label, lf)
            draw.text((tx + (tile_w - lw) // 2, ty + tile_h - lh - 16),
                      label, fill=self.pal["text_primary"], font=lf)

        return img

    # ------------------------------------------------------------------
    # Layout: title_card  (big title + subtitle)
    # ------------------------------------------------------------------
    def _layout_title_card(self, title: str = "", subtitle: str = "",
                           accent: str = "", **kw) -> Image.Image:
        ac = accent or self.pal["accent"]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        tf = _load_font(52, bold=True, path=self.font_path)
        tw, th = _text_size(draw, title, tf)
        tx = (self.w - tw) // 2
        ty = self.h // 2 - th - 20
        draw.text((tx, ty), title, fill=self.pal["text_primary"], font=tf)
        # Accent bar under title
        bar_w = min(tw + 40, self.w - 120)
        bx = (self.w - bar_w) // 2
        draw.rectangle([bx, ty + th + 12, bx + bar_w, ty + th + 18], fill=ac)

        if subtitle:
            sf = _load_font(28, path=self.font_path)
            sw, sh = _text_size(draw, subtitle, sf)
            draw.text(((self.w - sw) // 2, ty + th + 32), subtitle,
                      fill=self.pal["text_dim"], font=sf)

        return img


# ---------------------------------------------------------------------------
# Content analyzer — determines the best layout for given text
# ---------------------------------------------------------------------------
def analyze_content(text: str, bullets: Optional[List[str]] = None,
                    diagram_type: Optional[str] = None) -> str:
    """Analyze scene content and suggest the best layout type.

    Returns one of: infographic, flowchart, comparison, stats_card,
    timeline, list, icon_grid, title_card.
    """
    text_lower = (text or "").lower()
    bullets = bullets or []

    # Explicit diagram type override
    if diagram_type:
        mapping = {
            "flowchart": "flowchart",
            "comparison": "comparison",
            "timeline": "timeline",
            "stats": "stats_card",
        }
        return mapping.get(diagram_type, "infographic")

    # Keyword-based heuristics
    comparison_words = ["vs", "versus", "compared", "difference", "compare",
                        "pros and cons", "advantage", "disadvantage"]
    if any(w in text_lower for w in comparison_words):
        return "comparison"

    flow_words = ["step", "process", "how", "workflow", "pipeline", "stages",
                  "sequence", "order", "phase"]
    if any(w in text_lower for w in flow_words):
        return "flowchart"

    timeline_words = ["history", "timeline", "evolution", "milestone",
                      "year", "chronological", "from.*to"]
    if any(w in text_lower for w in timeline_words):
        return "timeline"

    stat_words = ["percent", "number", "statistic", "data", "growth",
                  "rate", "metric", "benchmark"]
    if any(w in text_lower for w in stat_words):
        return "stats_card"

    list_words = ["list", "top", "best", "essential", "key", "important",
                  "principles", "rules", "tips"]
    if any(w in text_lower for w in list_words):
        return "list"

    # If we have many bullets, use infographic
    if len(bullets) >= 3:
        return "infographic"

    # If we have a few bullets, use list
    if len(bullets) >= 1:
        return "list"

    return "title_card"


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    gen = TextToImageGenerator(1280, 720)

    # Test infographic
    img = gen.generate("infographic", title="RAG vs Fine-tuning",
                       bullets=["RAG retrieves from external knowledge base",
                                "Fine-tuning adapts the model weights",
                                "RAG is better for dynamic data",
                                "Fine-tuning excels at domain expertise"])
    img.save("output/t2i_test_infographic.png")
    print("Saved: output/t2i_test_infographic.png")

    # Test flowchart
    img = gen.generate("flowchart", title="How RAG Works",
                       steps=["Query", "Retrieve", "Augment", "Generate", "Output"])
    img.save("output/t2i_test_flowchart.png")
    print("Saved: output/t2i_test_flowchart.png")

    # Test comparison
    img = gen.generate("comparison", title="RAG vs Fine-tuning",
                       left_title="RAG",
                       left_items=["External knowledge", "No retraining", "Dynamic data"],
                       right_title="Fine-tuning",
                       right_items=["Internal knowledge", "Requires training", "Static domain"])
    img.save("output/t2i_test_comparison.png")
    print("Saved: output/t2i_test_comparison.png")

    # Test stats
    img = gen.generate("stats_card", title="Channel Performance",
                       stats=[{"value": "954", "label": "Words"},
                              {"value": "9", "label": "Scenes"},
                              {"value": "8.2m", "label": "Duration"},
                              {"value": "4.2%", "label": "CTR"}])
    img.save("output/t2i_test_stats.png")
    print("Saved: output/t2i_test_stats.png")

    print("\nAll tests passed!")
