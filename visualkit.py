"""
Rich scene painter kit — deterministic PIL visuals for prompt-driven videos.

Each scene in the learning workflow carries a `visual_type` and
`visual_content`.  This module turns those into an RGBA frame callable that the
video renderer composites over its background (same contract as visualizer).

Usage::

    from visualkit import build_rich_visual
    render = build_rich_visual(
        visual_type="terminal_scene",
        visual_content={"title": "Verify", "lines": [...]},
        width=1280, height=720, duration=7.0,
    )
    frame = render(t)      # HxWx4 uint8 RGBA

Painters are self-contained, deterministic and narration-timed: content reveals
proportionally across the scene so the picture always matches the speech.
"""

import math
import re
from functools import lru_cache
from typing import List, Optional, Dict, Any, Callable, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

MONO = "C:/Windows/Fonts/consola.ttf"
MONO_B = "C:/Windows/Fonts/consolab.ttf"
UI = "C:/Windows/Fonts/segoeui.ttf"
UI_B = "C:/Windows/Fonts/arialbd.ttf"
FALLBACK = "C:/Windows/Fonts/comic.ttf"

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

PANEL = (15, 22, 36, 242)
PANEL_SOFT = (18, 27, 44, 235)
PANEL_EDGE = (86, 130, 205, 130)
TITLEBAR = (11, 17, 29, 255)
TEXT = (228, 242, 255, 255)
DIM = (138, 158, 190, 255)
FAINT = (86, 104, 132, 255)
OK = (52, 211, 153, 255)
ERR = (248, 113, 113, 255)
WARN = (251, 191, 36, 255)


def _hex(hex_color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = (hex_color or "#00E5FF").lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (r, g, b, alpha)


# ---------------------------------------------------------------------------
# Fonts / text helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=256)
def _font(path: str, size: int, bold: bool = False) -> ImageFont:
    candidates = [path]
    if not bold:
        candidates.insert(0, path)
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.truetype(FALLBACK, size)
    except Exception:
        return ImageFont.load_default()


def _mono(size: int, bold: bool = False) -> ImageFont:
    return _font(MONO_B if bold else MONO, size)


def _ui(size: int, bold: bool = False) -> ImageFont:
    return _font(UI_B if bold else UI, size)


def _tb(draw: ImageDraw.ImageDraw, text: str, font: ImageFont) -> Tuple[int, int, int, int]:
    b = draw.textbbox((0, 0), text, font=font)
    return b


def _tw(draw: ImageDraw.ImageDraw, text: str, font: ImageFont) -> int:
    lb = _tb(draw, text, font)
    return lb[2] - lb[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font: ImageFont) -> int:
    lb = _tb(draw, text, font)
    return lb[3] - lb[1]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont, maxw: int) -> List[str]:
    out: List[str] = []
    for para in (text or "").split("\n"):
        if not para:
            continue
        cur = ""
        for w in para.split():
            trial = (cur + " " + w).strip()
            if _tw(draw, trial, font) <= maxw:
                cur = trial
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out


def _text_size(txt: str, font: ImageFont) -> Tuple[int, int]:
    img = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(img)
    return _tw(d, txt, font), _th(d, txt, font)


def _text_image(text: str, font: ImageFont, fill: Tuple[int, int, int, int]) -> Image.Image:
    w, h = _text_size(text, font)
    img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((1, 1), text, font=font, fill=fill)
    return img


def _rounded(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], r: int,
             fill: Optional[Tuple[int, ...]] = None,
             outline: Optional[Tuple[int, ...]] = None, width: int = 1) -> None:
    try:
        draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)
    except AttributeError:  # pragma: no cover
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _seg(t: float, start: float, end: float) -> float:
    if end <= start:
        return 1.0 if t >= start else 0.0
    return _clamp01((t - start) / (end - start))


def _ease(x: float) -> float:
    return x * x * (3.0 - 2.0 * x)


def _reveal_window(duration: float) -> Tuple[float, float]:
    end = max(0.9, duration - 0.55)
    return 0.45, end


def _canvas(width: int, height: int) -> Image.Image:
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def _draw_title_bar(img: Image.Image, title: str, accent: Tuple[int, int, int, int],
                    dots: bool = True) -> int:
    d = ImageDraw.Draw(img)
    w = img.width
    bh = 38
    d.rectangle([0, 0, w, bh], fill=TITLEBAR)
    d.line([0, bh - 1, w, bh - 1], fill=(40, 56, 86, 255), width=1)
    font = _ui(15)
    if dots:
        r = 7
        for i, col in enumerate(((240, 92, 92), (240, 184, 80), (92, 200, 120))):
            d.ellipse([14 + i * (r * 2 + 10), bh / 2 - r, 14 + i * (r * 2 + 10) + r * 2, bh / 2 - r + r * 2],
                      fill=col + (255,))
    if title:
        d.text((66, bh / 2 - _th(d, title, font) / 2 - 1), title, font=font, fill=DIM)
    if accent:
        d.rectangle([w - 6, 8, w - 2, bh - 8], fill=accent)
    return bh


def _cursor_img(font: ImageFont, fill: Tuple[int, int, int, int] = (0, 229, 255, 255)) -> Image.Image:
    h = _th(ImageDraw.Draw(Image.new("RGBA", (4, 4))), "Mg", font)
    img = Image.new("RGBA", (12, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, 9, h - 1], fill=fill)
    return img


def _draw_check(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int,
                color: Tuple[int, int, int, int], progress: float = 1.0) -> None:
    p = _clamp01(progress)
    if p <= 0.03:
        return
    alpha = int(color[3] * p)
    c = color[:3] + (alpha,)
    part = int(r * p)
    _rounded(draw, (cx - r, cy - r, cx + r, cy + r), 4, fill=(color[:3] + (int(80 * p),)))
    if part >= 2:
        _rounded(draw, (cx - part, cy - part, cx + part, cy + part), int(part * 0.5), outline=c, width=max(1, part // 4))
    if p > 0.55:
        sc = cx - r // 2, cy
        eco = cx, cy + r // 3
        draw.line([sc, (cx - 2, cy + 1), (cx + 2, cy + 1), eco], fill=c, width=max(1, int(r / 5)))
    else:
        ang = math.pi * (0.75 - 1.5 * p) if p > 0.03 else 0
        bx = cx + int(r * 0.6 * math.cos(ang))
        by = cy + int(r * 0.6 * math.sin(ang))
        draw.arc([cx - r, cy - r, cx + r, cy + r], start=int(math.degrees(ang) + 90), end=int(math.degrees(ang) + 270),
                 fill=c, width=max(1, int(r / 5)))


# ---------------------------------------------------------------------------
# Panel backgrounds shared by painting styles
# ---------------------------------------------------------------------------

def _panel(img: Image.Image, box: Tuple[int, int, int, int], fill: Tuple[int, int, int, int] = PANEL,
           r: int = 18) -> None:
    d = ImageDraw.Draw(img)
    _rounded(d, box, r, fill=fill)
    _rounded(d, box, r, outline=PANEL_EDGE, width=1)


def _strip_panel(img: Image.Image, y0: int, y1: int, alpha: int = 210) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, y0, img.width, y1], fill=(10, 15, 26, alpha))


# ---------------------------------------------------------------------------
# Individual painters.  Each returns render(t) -> np.ndarray (HxWx4 RGBA).
# ---------------------------------------------------------------------------

def _paint_title(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                 accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    title = (visual_content.get("title") or scene_text or "Title").strip()
    subtitle = (visual_content.get("subtitle") or "").strip()
    head = _ui(56, bold=True)
    head2 = _ui(30, bold=True)
    sub_f = _ui(22)
    tt_img = _text_image(title, head, TEXT)
    if len(title) > 26:
        tt_img = _text_image(title, head2, TEXT)
    if len(title) > 42:
        sub_f = _ui(19)
    sub_img = _text_image(subtitle, sub_f, DIM) if subtitle else None

    band = int(h * 0.22)
    bar_h = 6
    box = Image.new("RGBA", (w, band + 30), (0, 0, 0, 0))

    def render(t: float) -> np.ndarray:
        p = _seg(t, 0.15, 0.9)
        ease = _ease(p)
        img = _canvas(w, h)
        d = ImageDraw.Draw(img)
        _strip_panel(img, h // 2 - band // 2 - 12, h // 2 + band // 2 + 12, 235)
        top = h // 2 - band // 2
        bary = int(top + band * 0.18)
        bw = int((w * 0.86) * ease)
        d.rectangle([w // 2 - bw // 2, bary, w // 2 + bw // 2, bary + bar_h], fill=accent)

        tt = tt_img
        if ease < 1:
            tw_, th_ = tt.size
            tt = tt.crop((0, 0, int(tw_ * ease), th_)) if tw_ else tt
        tw_, th_ = tt.size
        img.alpha_composite(tt, (w // 2 - tw_ // 2, top + int(band * 0.30) - th_ // 2))

        if sub_img is not None and p > 0.45:
            alpha = int(255 * _seg(t, 0.55, 0.9))
            if alpha > 4:
                ssub = sub_img.copy()
                ssub.putalpha(alpha)
                sw_, sh_ = ssub.size
                img.alpha_composite(ssub, (w // 2 - sw_ // 2, int(band * 0.55) + int(band * 0.30) + 8 - sh_ // 2))
        else:
            return np.array(img)
        return np.array(img)

    return render


def _paint_list(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    title = (visual_content.get("title") or scene_text or "Key points").strip()
    items = [i for i in (visual_content.get("items") or []) if (i or "").strip()]
    if not items:
        items = [title]
    tf = _ui(30, bold=True)
    li_f = _ui(21)
    rows = []
    maxw = int(w * 0.62)
    for it in items:
        rows.append(_wrap(ImageDraw.Draw(_canvas(8, 8)), it, li_f, maxw))
    pad = int(h * 0.09)
    top = pad
    gap = 14
    item_h = 44
    total = item_h * len(rows) + gap * (len(rows) - 1)
    box_y = (h - total) // 2 - 10

    def render(t: float) -> np.ndarray:
        p = _seg(t, 0.25, 1.0)
        img = _canvas(w, h)
        d = ImageDraw.Draw(img)
        _strip_panel(img, pad - 40, h - pad + 10, 235)
        ty = pad
        _rounded(d, (w // 2 - 6, ty, w // 2 + 6, ty + 34), 4, fill=accent)
        d.text((w // 2 - _tw(d, title, tf) // 2, ty + 44), title, font=tf, fill=TEXT)
        n = len(rows)
        for i, rl in enumerate(rows):
            pr = _clamp01((t - (0.5 + i * 0.35)) / 0.45)
            if pr <= 0:
                continue
            y = box_y + i * (item_h + gap)
            cx = w // 2 - int(maxw * 0.5)
            _draw_check(d, cx + 26, y + item_h // 2, 17, accent, pr)
            x0 = cx + 64
            for li, line in enumerate(rl):
                alpha = int(255 * pr)
                d.text((x0, y + li * 28), line, font=li_f, fill=TEXT[:3] + (alpha,))
        return np.array(img)

    return render


def _paint_terminal(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                    accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    title = (visual_content.get("title") or "Terminal").strip()
    lines: List[dict] = visual_content.get("lines") or []
    if not lines:
        lines = [{"prompt": "$", "cmd": scene_text, "output": ""}]

    pad = 46
    box = [pad, pad, w - pad, h - pad]
    bw = box[2] - box[0]
    bh = box[3] - box[1]
    bhdr = _draw_title_bar(_canvas(w, h), title, accent)
    font = _mono(20)
    prompt_col = (118, 201, 78, 255)
    cmd_col = TEXT
    out_col = (170, 200, 225, 255)

    # Pre-render each line's prompt+cmd and output strip.
    typed: List[Tuple[Image.Image, Image.Image]] = []
    for ln in lines:
        prompt_txt = ln.get("prompt", "$") + " "
        cmd_txt = ln.get("cmd", "")
        full = _text_image(prompt_txt + cmd_txt, font, cmd_col)
        pfx_w = _tw(ImageDraw.Draw(full), prompt_txt, font)
        pfx = _text_image(prompt_txt, font, prompt_col)
        out = None
        otxt = ln.get("output") or ""
        if otxt:
            out = _text_image(otxt[:110], font, out_col)
        typed.append((pfx, full, pfx_w, out))

    n = len(typed)
    start, end = _reveal_window(duration)
    slot = (end - start) / max(1, n)
    line_gap = 34

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        _panel(img, tuple(box), PANEL, 16)
        _draw_title_bar(img, title, accent)
        d = ImageDraw.Draw(img)
        y = box[1] + 20
        for i, (pfx, full, pfx_w, out) in enumerate(typed):
            t0 = start + i * slot
            tw_dur = max(0.7, slot * 0.55)
            prog = _seg(t, t0, t0 + tw_dur)
            if prog <= 0:
                y += line_gap
                continue
            x = box[0] + 26
            reveal = int((pfx.size[0] + (full.size[0] - pfx.size[0]) * _ease(prog))) if prog < 1 else full.size[0]
            piece = full.crop((0, 0, reveal, full.size[1]))
            img.alpha_composite(piece, (x, y))
            if prog < 1:
                cur = _cursor_img(font, accent)
                at = (x + reveal, y)
                img.alpha_composite(cur, at)
            else:
                cur = _cursor_img(font, accent)
                blink = 0.6 if (t % 0.9) < 0.45 else 0.0
                if blink > 0 and out is None:
                    cb = cur.copy()
                    cb.putalpha(220)
                    img.alpha_composite(cb, (x + full.size[0] + 4, y))
            if out is not None:
                oprog = _seg(t, t0 + tw_dur + 0.25, t0 + tw_dur + 0.6)
                if oprog > 0:
                    alpha = int(255 * oprog)
                    oo = out.copy()
                    oo.putalpha(alpha)
                    img.alpha_composite(oo, (x + 8, y + full.size[1] + 6))
                    y += line_gap + 6
                else:
                    y += line_gap
            else:
                y += line_gap
        return np.array(img)

    return render


_PY_KW = {"def", "import", "from", "as", "class", "return", "if", "elif", "else",
          "for", "while", "in", "not", "and", "or", "None", "True", "False",
          "with", "print", "lambda", "try", "except", "finally", "pass"}
_JAVA_KW = {"public", "private", "class", "void", "int", "static", "new", "return",
            "if", "else", "for", "while", "String", "System"}


def _highlight_runs(line: str, font: ImageFont) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    runs: List[Tuple[str, Tuple[int, int, int, int]]] = []
    low = line
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"' or ch == "'":
            j = i + 1
            while j < n and line[j] != ch:
                j += 1
            runs.append((line[i:j + 1], (152, 211, 135, 255)))
            i = j + 1
        elif ch == '#':
            runs.append((line[i:], (96, 116, 140, 255)))
            break
        elif ch.isalpha() or ch == '_':
            j = i
            while j < n and (line[j].isalnum() or line[j] == '_'):
                j += 1
            ww = line[i:j]
            if ww in _PY_KW or ww in _JAVA_KW:
                runs.append((ww, (198, 150, 255, 255)))
            elif i == 0 and re.match(r"def |class ", line):
                runs.append((ww, (86, 182, 255, 255)))
            else:
                runs.append((ww, TEXT))
            i = j
        elif ch.isdigit():
            j = i
            while j < n and line[j].isdigit():
                j += 1
            runs.append((line[i:j], (255, 196, 87, 255)))
            i = j
        else:
            runs.append((ch, TEXT))
            i += 1
    return runs


def _paint_code(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    filename = (visual_content.get("filename") or "demo.py").strip()
    code = visual_content.get("code") or scene_text
    if isinstance(code, list):
        code = "\n".join(code)
    out = visual_content.get("output") or ""
    highlight = visual_content.get("highlight") or ""

    pad = 46
    box = [pad, pad, w - pad, h - pad]
    font = _mono(19)
    gutter_w = 46
    raw_lines = code.split("\n") if code else [""]
    code_lines: List[List[Tuple[str, Tuple[int, int, int, int]]]] = []
    for ln in raw_lines:
        code_lines.append(_highlight_runs(ln, font))
    max_lines = max(1, int((box[3] - box[1] - 120) // 32))
    code_lines = code_lines[:max_lines]
    n = len(code_lines)

    hl_idx = next((i for i, runs in enumerate(code_lines)
                   if highlight and any(highlight in r[0] for r in runs)), None)
    start, end = _reveal_window(duration)
    slot = (end - start) / max(1, n)

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        _panel(img, tuple(box), PANEL, 16)
        _draw_title_bar(img, filename, accent, dots=False)
        d = ImageDraw.Draw(img)
        x0 = box[0] + 24
        y = box[1] + 26
        for i, runs in enumerate(code_lines):
            t0 = start + i * slot * 0.7
            prog = _seg(t, t0, t0 + slot * 0.5)
            if prog <= 0:
                y += 32
                continue
            alpha = int(235 * prog)
            d.text((x0, y), f"{i + 1:>3}", font=font, fill=FAINT)
            lx = x0 + gutter_w
            if hl_idx == i:
                _rounded(d, (lx - 6, y - 3, box[2] - 26, y + 24), 6, fill=(40, 66, 96, 150))
            if prog < 1:
                total = sum(len(r[0]) for r in runs)
                budget = int(total * _ease(prog))
                for run, col in runs:
                    if budget <= 0:
                        break
                    take = min(len(run), budget)
                    if take:
                        d.text((lx, y), run[:take], font=font, fill=col[:3] + (alpha,))
                    lx += _tw(d, run[:take], font)
                    budget -= take
                    if budget <= 0:
                        break
                cur = _cursor_img(font, accent)
                img.alpha_composite(cur, (lx + 2, y))
            else:
                for run, col in runs:
                    d.text((lx, y), run, font=font, fill=col[:3] + (alpha,))
                    lx += _tw(d, run, font)
            y += 32
        if out and _seg(t, end - 0.7, end) > 0:
            oprog = _seg(t, end - 0.6, end)
            oy = box[3] - 52
            _rounded(d, (box[0] + 18, oy - 8, box[2] - 18, oy + 30), 10, fill=(16, 34, 30, 235))
            d.rectangle([box[0] + 18, oy - 8, box[0] + 24, oy + 30], fill=(52, 211, 153, 255))
            d.text((box[0] + 40, oy), out[:90], font=font, fill=(182, 236, 208, int(255 * oprog)))
        return np.array(img)

    return render


def _paint_config(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                  accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    title = (visual_content.get("title") or "Configuration").strip()
    entries = visual_content.get("entries") or []
    font = _mono(19)

    rows: List[Tuple[str, str]] = []
    for e in entries:
        if isinstance(e, (list, tuple)) and len(e) == 2:
            rows.append((str(e[0]), str(e[1])))
        elif isinstance(e, dict):
            rows.append((str(e.get("key", "?")), str(e.get("value", ""))))
    pad = 52
    box = [pad, pad, w - pad, h - pad]
    start, end = _reveal_window(duration)
    slot = (end - start) / max(1, len(rows))
    row_h = 46

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        _panel(img, tuple(box), PANEL, 16)
        _draw_title_bar(img, title, accent, dots=False)
        d = ImageDraw.Draw(img)
        y = box[1] + 34
        for i, (k, v) in enumerate(rows):
            pr = _seg(t, 0.4 + i * slot * 0.8, 0.4 + i * slot * 0.8 + 0.5)
            if pr <= 0:
                y += row_h
                continue
            ky = y
            _rounded(d, (box[0] + 24, ky, box[0] + 270, ky + 30), 8, fill=(24, 36, 58, 255))
            d.text((box[0] + 38, ky + 6), str(k), font=font, fill=(160, 185, 220, int(255 * pr)))
            vx = box[0] + 300
            vw = _tw(d, v, font)
            maxv = box[2] - vx - 30
            d.rectangle([vx, ky, vx + maxv, ky + 30], fill=(14, 24, 40, 255))
            if vw > maxv:
                v = v[: max(0, int(len(v) * maxv / vw))]
                vw = maxv
            reveal = int(vw * _seg(t, 0.5 + i * slot * 0.8, 0.5 + i * slot * 0.8 + 0.6))
            shown = v[: max(1, int((reveal / max(1, vw)) * len(v)))]
            d.rectangle([vx, ky, vx + maxv, ky + 30], fill=(14, 24, 40, 255))
            d.text((vx + 10, ky + 6), shown, font=font, fill=TEXT[:3] + (int(255 * pr),))
            if 0 < _seg(t, 0.5 + i * slot * 0.8, 0.5 + i * slot * 0.8 + 0.6) < 1:
                cur = _cursor_img(font, accent)
                img.alpha_composite(cur, (vx + 10 + int(reveal), ky + 6))
            y += row_h
        return np.array(img)

    return render


def _paint_form(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    title = (visual_content.get("title") or "Form").strip()
    fields = [(str(f[0]), str(f[1])) for f in (visual_content.get("fields") or [])
              if isinstance(f, (list, tuple)) and len(f) >= 2]
    font = _ui(18)
    val_f = _ui(19)
    pad = 60
    box = [pad, pad, w - pad, h - pad]
    field_w = box[2] - box[0] - 90
    fh = 42
    gap = 18
    n = len(fields)
    tb_h = 64
    start_y = box[1] + tb_h + 18
    start, end = _reveal_window(duration)
    slot = (end - start) / max(1, n)

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        _panel(img, tuple(box), PANEL, 18)
        d = ImageDraw.Draw(img)
        _draw_title_bar(img, title, accent, dots=False)
        d.text((box[0] + 34, box[1] + 22), title, font=_ui(26, bold=True), fill=TEXT)
        focus = int((t - start) // max(0.5, slot) % max(1, n)) if t >= start else -1
        for i, (k, v) in enumerate(fields):
            pr = _seg(t, start + i * slot, start + i * slot + 0.5)
            if pr <= 0:
                continue
            y = start_y + i * (fh + gap)
            ly = y - 6
            d.text((box[0] + 44, ly - 2), k, font=font, fill=(170, 190, 220, int(255 * pr)))
            fbox = (box[0] + 44, y + fh - fh + 4 - 4 + 12, box[0] + 44 + field_w, y + fh + 12)
            _rounded(d, (box[0] + 40, y + 14, box[0] + 44 + field_w, y + 14 + fh - 8), 8,
                     fill=(22, 34, 54, 255))
            d.rectangle([box[0] + 40, y + 14, box[0] + 44 + field_w, y + 14 + fh - 8],
                        outline=(96, 140, 210, 255), width=1)
            if i == focus:
                d.rectangle([box[0] + 40, y + 14, box[0] + 44 + field_w, y + 14 + fh - 8],
                            outline=accent, width=2)
            vv = v
            cur = None
            if pr < 1:
                vv = v[:int(len(v) * _ease(pr))]
            d.text((box[0] + 56, y + 14 + 8), vv, font=val_f, fill=TEXT[:3] + (int(255 * pr),))
            if i == focus and pr > 0.7:
                cur = _cursor_img(val_f, accent)
                x_cur = box[0] + 56 + _tw(d, vv, val_f)
                img.alpha_composite(cur, (x_cur + 2, y + 14 + 8))
            ck = _seg(t, start + i * slot + 0.4, start + i * slot + 0.8)
            if ck > 0 and i == focus:
                _draw_check(d, box[0] + 44 + field_w + 26, y + 14 + fh // 2, 14, OK, ck)
        return np.array(img)

    return render


def _paint_dialog(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                  accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    mtitle = (visual_content.get("title") or "Message").strip()
    message = (visual_content.get("message")
               or visual_content.get("detail")
               or scene_text).strip()
    dlg_w = int(w * 0.56)
    dlg_h = int(h * 0.34)
    x0 = (w - dlg_w) // 2
    y0 = (h - dlg_h) // 2
    font = _ui(19)
    tf = _ui(24, bold=True)
    tf2 = _ui(22, bold=True)

    def render(t: float) -> np.ndarray:
        p = _seg(t, 0.3, 0.85)
        ease = _ease(p)
        img = _canvas(w, h)
        d = ImageDraw.Draw(img)
        if ease > 0.03:
            sh = int(6 * ease)
            _rounded(d, (x0 + 4, y0 + sh + 4, x0 + dlg_w + 4, y0 + dlg_h + sh + 4), 16,
                     fill=(0, 0, 0, int(150 * ease)))
            _rounded(d, (x0, y0, x0 + dlg_w, y0 + dlg_h), 16, fill=(24, 34, 54, 250))
            d.rectangle([x0, y0, x0 + dlg_w, y0 + dlg_h], outline=(96, 140, 210, int(180 * ease)), width=1)
            d.text((x0 + 28, y0 + 22), mtitle, font=tf, fill=TEXT)
            d.line([x0 + 12, y0 + 62, x0 + dlg_w - 12, y0 + 62], fill=(40, 56, 86, 255), width=1)
            d.text((x0 + 28, y0 + 82), message[:160], font=font, fill=(190, 208, 228, 255))
            btn_w = 130
            btn_h = 40
            bx = x0 + dlg_w - btn_w - 24
            by = y0 + dlg_h - btn_h - 18
            ckp = _seg(t, 0.7, 0.95)
            _rounded(d, (bx, by, bx + btn_w, by + btn_h), 8, fill=accent[:3] + (int(255 * ckp * 0.92),))
            d.text((bx + btn_w // 2 - _tw(d, "OK", tf2) // 2, by + 8), "OK", font=tf2,
                   fill=(10, 16, 26, int(255 * ckp)))
            if ckp > 0 and ckp < 0.5:
                rp = ckp / 0.5
                r = int(30 * rp)
                _rounded(d, (bx + btn_w // 2 - r, by + btn_h // 2 - r, bx + btn_w // 2 + r, by + btn_h // 2 + r),
                         r, outline=accent[:3] + (int(200 * (1 - rp)),), width=2)
        return np.array(img)

    return render


def _paint_workflow(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                    accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    nodes = [n for n in (visual_content.get("nodes") or
                         visual_content.get("steps") or []) if (n or "").strip()]
    title = (visual_content.get("title") or "Process").strip()
    if not nodes:
        nodes = [scene_text]
    nodes = nodes[:6]
    n = len(nodes)
    box_w = int(w * 0.70)
    box_h = int(h * 0.15)
    x0 = (w - box_w) // 2
    total = n * box_h + (n - 1) * 34
    y0 = max(int(h * 0.15), (h - total) // 2)
    font = _ui(20)
    start, end = _reveal_window(duration)
    gap = 0.5

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        d = ImageDraw.Draw(img)
        _strip_panel(img, y0 - 40, y0 + total + 40, 235)
        for i, nd in enumerate(nodes):
            prog = _seg(t, 0.5 + i * (gap / max(1, n)), 0.52 + i * (gap / max(1, n)) + 0.45)
            if prog <= 0:
                continue
            e = _ease(prog)
            y = y0 + i * (box_h + 32) + (1 - e) * 18
            x = x0 + (1 - e) * 14
            _rounded(d, (x, y, x + box_w, y + box_h), 14, fill=(20, 32, 52, int(245 * prog)))
            d.rectangle([x, y, x + box_w, y + box_h], outline=accent[:3] + (int(210 * prog),), width=2)
            num_f = _ui(18, bold=True)
            num = f"{i + 1}"
            d.ellipse([x + 22, y + box_h // 2 - 17, x + 56, y + box_h // 2 + 17],
                      fill=accent[:3] + (int(255 * prog),))
            d.text((x + 39 - _tw(d, num, num_f) // 2, y + box_h // 2 - _th(d, num, num_f) // 2 - 2),
                   num, font=num_f, fill=(12, 18, 30, int(255 * prog)))
            lbl = nd
            lf = font
            tw_ = _tw(d, lbl, lf)
            if tw_ > box_w - 90:
                lf = _ui(16)
                tw_ = _tw(d, lbl, lf)
            lines = _wrap(d, lbl, lf, box_w - 100)
            ly = y + box_h // 2 - (len(lines) * 24) // 2
            for ln in lines:
                d.text((x + 74, ly), ln, font=lf, fill=TEXT[:3] + (int(255 * prog),))
                ly += 24
            if i < n - 1:
                ay = y + box_h + 8
                ax = (x + box_w) // 2
                ad = 0.55 + 0.4 * math.sin(t * 2.0)
                d.line([ax - 6, ay, ax + 6, ay], fill=accent[:3] + (int(200 * ad),), width=2)
                d.line([ax + 6, ay, ax, ay - 4], fill=accent[:3] + (int(200 * ad),), width=2)
                d.line([ax + 6, ay, ax, ay + 4], fill=accent[:3] + (int(200 * ad),), width=2)
        return np.array(img)

    return render


def _paint_gui(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
               accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    window = (visual_content.get("window") or "Application").strip()
    role = visual_content.get("role") or ""
    elements = visual_content.get("elements") or []
    pad = 44
    box = [pad, pad, w - pad, h - pad]

    # Normalize element positions relative to the panel.
    elems: List[Dict[str, Any]] = []
    for e in elements:
        if not isinstance(e, dict):
            continue
        kind = e.get("kind", "button")
        label = e.get("label", "")
        center = e.get("center")
        if isinstance(center, (list, tuple)) and len(center) == 2:
            cx = pad + int(center[0])
            cy = pad + int(center[1])
        else:
            cx, cy = w // 2, h // 2
        elems.append({"kind": kind, "label": label, "cx": cx, "cy": cy,
                      "checked": bool(e.get("checked", False))})
    n = max(1, len(elems))
    start, end = _reveal_window(duration)
    slot = (end - start) / n

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        _panel(img, tuple(box), PANEL, 16)
        _draw_title_bar(img, window, accent, dots=True)
        d = ImageDraw.Draw(img)
        if role:
            d.text((box[0] + 26, box[1] + 26), "Role: " + role, font=_ui(19, bold=True), fill=accent)
        for i, e in enumerate(elems):
            appear = _seg(t, start + i * slot, start + i * slot + 0.35)
            if appear <= 0:
                continue
            alpha = int(255 * appear)
            kind = e["kind"]
            font = _ui(17)
            if kind == "checkbox":
                _rounded(d, (e["cx"] - 12, e["cy"] - 12, e["cx"] + 12, e["cy"] + 12),
                         4, fill=(24, 40, 64, alpha))
                if e["checked"] and _seg(t, start + i * slot + 0.1, start + i * slot + 0.5) > 0:
                    d.line([(e["cx"] - 6, e["cy"]), (e["cx"] - 1, e["cy"] + 6), (e["cx"] + 7, e["cy"] - 7)],
                           fill=accent[:3] + (alpha,), width=3)
                if e.get("label"):
                    d.text((e["cx"] + 24, e["cy"] - 10), e["label"], font=_ui(18), fill=TEXT[:3] + (alpha,))
            elif kind == "button":
                bw = max(90, len(e["label"]) * 11 + 44)
                bh = 40
                bx = e["cx"] - bw // 2
                by = e["cy"] - bh // 2
                _rounded(d, (bx, by, bx + bw, by + bh), 8, fill=accent[:3] + (int(190 * appear),))
                d.text((e["cx"] - _tw(d, e["label"], _ui(18)) // 2, by + 9),
                       e["label"], font=_ui(18), fill=(10, 16, 26, alpha))
            elif kind == "text":
                d.text((e["cx"], e["cy"]), e.get("label", ""), font=_ui(18), fill=DIM[:3] + (alpha,))
            elif kind == "field":
                fw = 220
                _rounded(d, (e["cx"] - fw // 2, e["cy"] - 18, e["cx"] + fw // 2, e["cy"] + 18),
                         7, fill=(16, 26, 44, alpha))
                d.rectangle([e["cx"] - fw // 2, e["cy"] - 18, e["cx"] + fw // 2, e["cy"] + 18],
                            outline=(96, 140, 210, int(alpha * 0.8)), width=1)
                if e.get("label"):
                    d.text((e["cx"] - _tw(d, e["label"], font) // 2, e["cy"] - 12),
                           e["label"], font=font, fill=TEXT[:3] + (alpha,))
            # Cursor + click ripple on the current element.
            if i == int((t - start) // max(0.4, slot) % n):
                bob = math.sin(t * 6.0) * 3
                cur = _cursor_img(_ui(18), accent)
                img.alpha_composite(cur, (e["cx"] + 46, e["cy"] + 14 + int(bob)))
            ckp = _seg(t, start + i * slot + 0.7, start + i * slot + 1.0)
            if ckp > 0 and ckp < 0.35:
                rp = ckp / 0.35
                r = int(26 + 26 * rp)
                _rounded(d, (e["cx"] - r, e["cy"] - r, e["cx"] + r, e["cy"] + r),
                         r, outline=accent[:3] + (int(220 * (1 - rp)),), width=2)
        return np.array(img)

    return render


def _paint_error(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                 accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    etitle = (visual_content.get("title") or scene_text or "Error").strip()
    detail = (visual_content.get("detail") or "").strip()
    cause = (visual_content.get("cause") or "").strip()
    fix = (visual_content.get("fix") or "").strip()
    pad = 56
    box = [pad, pad, w - pad, h - pad]
    font = _ui(19)
    tf = _ui(27, bold=True)
    err_col = ERR
    start, end = _reveal_window(duration)

    def render(t: float) -> np.ndarray:
        p = _seg(t, 0.2, 0.9)
        img = _canvas(w, h)
        _panel(img, tuple(box), (30, 20, 30, 245), 18)
        d = ImageDraw.Draw(img)
        _draw_title_bar(img, "Error", err_col[:3] + (130,), dots=False)
        _rounded(d, (box[0] + 30, box[1] + 46, box[2] - 30, box[1] + 46 + 66), 12,
                 fill=(40, 24, 34, 240))
        d.text((box[0] + 56, box[1] + 60), etitle[:70], font=tf, fill=err_col)
        y = box[1] + 150
        if detail:
            d.text((box[0] + 42, y), detail[:130], font=font, fill=(236, 210, 210, int(255 * p)))
            y += 40
        if cause:
            d.text((box[0] + 42, y), "CAUSE   " + cause[:120], font=font, fill=(255, 190, 120, 255))
            y += 40
        if fix:
            _rounded(d, (box[0] + 30, y, box[2] - 30, y + 58), 12, fill=(24, 44, 38, 240))
            d.text((box[0] + 48, y + 10), "FIX      " + fix[:140], font=_ui(19), fill=(150, 235, 190, 255))
        return np.array(img)

    return render


def _paint_success(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                   accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    stitle = (visual_content.get("title") or scene_text or "Success").strip()
    detail = (visual_content.get("detail") or "").strip()
    pad = 56
    box = [pad, pad, w - pad, h - pad]
    font = _ui(21)
    tf = _ui(30, bold=True)

    def render(t: float) -> np.ndarray:
        p = _seg(t, 0.2, 0.95)
        img = _canvas(w, h)
        _panel(img, tuple(box), (18, 34, 30, 242), 18)
        d = ImageDraw.Draw(img)
        _draw_title_bar(img, "Success", OK[:3] + (120,), dots=False)
        prog = _seg(t, 0.3, 1.0)
        _draw_check(d, w // 2, box[1] + 110, 44, OK, prog)
        ck_p = _seg(t, 0.35, 0.8) - 0
        if prog > 0.55:
            a = int(180 * ck_p + 60)
            for i in range(5):
                rr = 60 + i * 26 + int(14 * math.sin(t * 3 + i))
                _rounded(d, (w // 2 - rr, box[1] + 110 - rr, w // 2 + rr, box[1] + 110 + rr),
                         rr, outline=(52, 211, 153, int(a / (i + 1.2))), width=int(3 - i * 0.5))
        d.text((w // 2 - _tw(d, stitle, tf) // 2, box[1] + 190), stitle[:60], font=tf, fill=(190, 255, 210, 255))
        if detail:
            d.text((w // 2 - _tw(d, detail, font) // 2, box[1] + 240), detail[:120], font=font, fill=(160, 220, 185, 230))
        return np.array(img)

    return render


def _paint_comparison(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                      accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    title = (visual_content.get("title") or "Comparison").strip()
    left = (visual_content.get("left") or {}).get("title", "Before")
    right = (visual_content.get("right") or {}).get("title", "After")
    left_items = visual_content.get("left", {}).get("items") or []
    right_items = visual_content.get("right", {}).get("items") or []
    rows = max(len(left_items), len(right_items), 1)
    pad = 56
    box = [pad, pad, w - pad, h - pad]
    col_w = (box[2] - box[0] - 40) // 2
    font = _ui(19)
    tf = _ui(22, bold=True)
    start, end = _reveal_window(duration)
    slot = (end - start) / max(1, rows)

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        _panel(img, tuple(box), PANEL, 16)
        d = ImageDraw.Draw(img)
        _draw_title_bar(img, title, accent, dots=False)
        for ci, (label, items, col) in enumerate((("left", left_items, (20, 30, 48, 255)),
                                                  ("right", right_items, (22, 40, 52, 255)))):
            x = box[0] + 24 + ci * (col_w + 22)
            hd = "left" if label == "left" else "right"
            d.text((x, box[1] + 26), "  " + label, font=_ui(18, bold=True), fill=DIM)
            _rounded(d, (x, box[1] + 64, x + col_w - 4, box[3] - box[1] - 10), 12, fill=(16, 26, 44, 240))
            d.rectangle([x, box[1] + 64, x + col_w - 4, box[3] - box[1] - 10],
                        outline=(90, 130, 190, 120), width=1)
            ly = box[1] + 92
            for i in range(min(rows, 5)):
                txt = items[i] if i < len(items) else ""
                pr = _seg(t, 0.45 + i * slot, 0.45 + i * slot + 0.5)
                if pr <= 0 or not txt:
                    ly += 40
                    continue
                paren = False
                idx = txt.find("(")
                mark = "✓" if not paren else "•"
                d.ellipse([x + 22, ly + 7, x + 38, ly + 23], fill=accent[:3] + (int(70 * pr),))
                d.text((x + 56, ly), txt[:56], font=font, fill=TEXT[:3] + (int(255 * pr),))
                ly += 40
        return np.array(img)

    return render


def _paint_diagram(visual_content: dict, scene_text: str, w: int, h: int, duration: float,
                   accent: Tuple[int, int, int, int]) -> Callable[[float], np.ndarray]:
    nodes = [n for n in (visual_content.get("nodes") or []) if (n or "").strip()]
    kind = visual_content.get("kind") or "flow"
    if not nodes:
        nodes = [scene_text]
    nodes = nodes[:6]
    n = len(nodes)
    pad = 56
    box = [pad, pad, w - pad, h - pad]
    font = _ui(18)
    bw = min(220, int((box[2] - box[0] - (n - 1) * 30) / n))
    bh = int(h * 0.16)
    x0 = w // 2 - (n * bw + (n - 1) * 30) // 2
    y = (h - bh) // 2
    start, end = _reveal_window(duration)
    slot = (end - start) / max(1, n)

    def render(t: float) -> np.ndarray:
        img = _canvas(w, h)
        _strip_panel(img, box[1] - 30, box[3] + 10, 235)
        d = ImageDraw.Draw(img)
        for i, nd in enumerate(nodes):
            pr = _seg(t, 0.5 + i * slot, 0.5 + i * slot + 0.5)
            if pr <= 0:
                continue
            x = x0 + i * (bw + 30)
            _rounded(d, (x, y, x + bw, y + bh), 14, fill=(20, 32, 52, int(245 * pr)))
            d.rectangle([x, y, x + bw, y + bh], outline=accent[:3] + (int(200 * pr),), width=2)
            lines = _wrap(d, nd, font, bw - 30)
            ly = y + bh // 2 - (len(lines) * 24) // 2
            for ln in lines:
                d.text((x + bw // 2 - _tw(d, ln, font) // 2, ly), ln, font=font,
                       fill=TEXT[:3] + (int(255 * pr),))
                ly += 24
            if i < n - 1:
                ax = x + bw + 8
                ay = y + bh // 2
                ad = 0.5 + 0.4 * math.sin(t * 2.2 + i)
                d.line([ax, ay - 6, ax, ay + 6], fill=accent[:3] + (int(200 * ad),), width=2)
                d.polygon([(ax + 10, ay), (ax + 2, ay - 5), (ax + 2, ay + 5)],
                          fill=accent[:3] + (int(200 * ad),))
        return np.array(img)

    return render


# ---------------------------------------------------------------------------
# Dispatch / factory
# ---------------------------------------------------------------------------

_PAINTERS = {
    "title_scene": _paint_title,
    "list_scene": _paint_list,
    "terminal_scene": _paint_terminal,
    "code_scene": _paint_code,
    "config_scene": _paint_config,
    "form_scene": _paint_form,
    "dialog_scene": _paint_dialog,
    "workflow_scene": _paint_workflow,
    "gui_scene": _paint_gui,
    "error_scene": _paint_error,
    "success_scene": _paint_success,
    "comparison_scene": _paint_comparison,
    "diagram_scene": _paint_diagram,
}


def build_rich_visual(
    visual_type: str = "",
    visual_content: Optional[dict] = None,
    scene_text: str = "",
    width: int = 1280,
    height: int = 720,
    duration: float = 6.0,
    accent: str = "#00E5FF",
    font_path: str = "C:/Windows/Fonts/comic.ttf",
    title_text: Optional[str] = None,
) -> Optional[Callable[[float], np.ndarray]]:
    """Build a rich visual painter for a scene, or None when none applies.

    Returns a `make_frame(t)` callable -> HxWx4 uint8 RGBA.  Unsupported or
    empty visual types return None so the caller falls back to its normal
    text rendering.
    """
    painter = _PAINTERS.get(visual_type or "")
    if painter is None:
        return None
    vc = visual_content or {}
    if not vc and not scene_text:
        return None
    accent_rgba = _hex(accent, 255)
    try:
        return painter(vc, scene_text or title_text or "", width, height,
                       max(1.0, duration), accent_rgba)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    samples = [
        ("terminal_scene", {"title": "Verification", "lines": [
            {"prompt": "PS>", "cmd": "dcdiag /c", "output": "Passed with no errors"}]}),
        ("code_scene", {"filename": "app.py", "code": "from flask import Flask\n"
                        "app = Flask(__name__)\n@app.route('/')\ndef hello():\n"
                        "    return 'ok'\napp.run(port=5000)",
                        "highlight": "def hello", "output": "Running on :5000"}),
        ("config_scene", {"title": "Wizard", "entries": [["Zone", "corp.local"],
                                                          ["Type", "Primary"], ["Updates", "Yes"]]}),
        ("form_scene", {"title": "Network", "fields": [["IP", "192.168.1.10"],
                        ["Mask", "255.255.255.0"], ["Gateway", "192.168.1.1"]]}),
        ("workflow_scene", {"title": "Flow", "nodes": ["Prepare", "Configure", "Verify", "Fix", "Done"]}),
        ("gui_scene", {"window": "Server Manager", "role": "Web Server (IIS)",
                       "elements": [{"kind": "checkbox", "label": "Web Server (IIS)",
                                     "center": (320, 250), "checked": True},
                                    {"kind": "button", "label": "Install",
                                     "center": (640, 430)}]}),
        ("error_scene", {"title": "Prerequisite check failed",
                         "detail": "Role requires a static IP",
                         "cause": "No static IP",
                         "fix": "Assign the IP and retry"}),
        ("success_scene", {"title": "Setup complete", "detail": "Service running"}),
        ("list_scene", {"title": "Prerequisites", "items": ["Admin access",
                        "Static IP", "Stable hostname", "Joined server"]}),
        ("title_scene", {"title": "Active Directory", "subtitle": "step by step"}),
        ("dialog_scene", {"title": "Confirm", "message": "Install the role?"}),
        ("comparison_scene", {"title": "Before / After", "left": {"items": ["Manual"]},
                               "right": {"items": ["Automated"]}}),
        ("diagram_scene", {"kind": "network", "nodes": ["Internet", "Gateway",
                          "Switch", "Device"]}),
    ]
    for vtype, vc in samples:
        render = build_rich_visual(vtype, vc, "Demo", 1280, 720, 6.0)
        if render is None:
            print(f"[SKIP] {vtype}")
            continue
        arr = render(4.0)
        print(f"[OK]   {vtype:<16} shape={arr.shape} dtype={arr.dtype} "
              f"alpha_px={(arr[:, :, 3] > 0).sum()}")