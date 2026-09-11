"""
Cinematic Tech Video Generator (MoviePy 2.x)
Animated character presenter, tech backgrounds, particles, text animations.
"""

import os
import base64
import io
import math
import random
import re
import asyncio
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from gtts import gTTS

from moviepy import (
    AudioFileClip, AudioClip, concatenate_videoclips,
    CompositeAudioClip, concatenate_audioclips, VideoClip
)

from character import CharacterRenderer2D as CharacterRenderer

try:
    from visualizer import build_scene_visual
    HAS_VISUALIZER = True
except ImportError:
    HAS_VISUALIZER = False

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False


@dataclass
class Scene:
    text: str
    background_prompt: str = ""
    duration: float = 5.0
    voiceover: str = ""
    text_position: str = "center"
    animation: str = "fade"
    font_size: int = 70
    text_color: str = "#FFFFFF"
    outline: bool = False
    outline_color: str = "#000000"
    outline_width: int = 3
    glow: bool = False
    glow_color: str = "#FFD700"
    glow_width: int = 6
    glow_intensity: float = 0.5
    overlay_opacity: float = 0.55
    show_character: bool = True
    character_position: str = "right"
    character_gesture: str = "default"
    character_type: str = "it_guy"
    character_avatar_path: str = ""
    character_avatar_data: str = ""
    visual_style: str = "cinematic_realistic"
    bullet_points: Optional[List[str]] = None
    title_text: Optional[str] = None
    enter_from: Optional[str] = None
    exit_to: Optional[str] = None
    # Technical diagram overlay for educational/professional scenes
    diagram_type: Optional[str] = None
    diagram_data: Optional[dict] = None
    diagram_position: str = "bottom"


@dataclass
class VideoConfig:
    width: int = 1280
    height: int = 720
    fps: int = 24
    font_path: str = "C:/Windows/Fonts/comic.ttf"
    background_color: str = "#0a0a0a"
    text_shadow: bool = True
    transition_duration: float = 0.5
    ken_burns_zoom: float = 1.08
    voice_lang: str = "en"
    character_enabled: bool = True
    background_music: Optional[str] = None
    music_volume: float = 0.3
    # Guaranteed padding so a scene's video slot is never shorter than its
    # narration. The compose overlap (padding=-1.0) consumes 1.0s per scene
    # boundary, so the effective display slot equals duration - 1.0; adding
    # SAFETY_MARGIN keeps complete speech + a natural tail in every scene.
    safety_margin: float = 0.20
    # Real stock backgrounds. stock_background is any object exposing
    # get_background(prompt, scene_index, visual_style) -> np.ndarray|None
    # (see stockbg.PexelsBackground). None -> procedural backgrounds only.
    stock_background: Optional[object] = None
    # "auto" (stock if available else procedural), "stock", "procedural"
    background_mode: str = "auto"
    presenter_avatar_path: str = ""
    presenter_avatar_data: str = ""
    # Text-to-image visualizer: animates scene content as structured visuals
    # (infographics, flowcharts, comparisons...) synced with the narration.
    visualizer_enabled: bool = False
    visualizer_opacity: float = 0.85
    visualizer_font_size: int = 24
    visualizer_font_path: str = "C:/Windows/Fonts/comic.ttf"


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


class TechBackground:
    # Visual style presets.
    # - "cinematic_tech":    default — dark, cyan/purple, perspective grid + hex + circuit traces
    # - "cinematic_realistic":  filtered / desaturated, subtle, film-like
    # - "educational_infographic":  bright blue↔purple gradient, yellow accent, starburst, floating icons
    #
    # Color scheme indices for educational_infographic are set below.

    # Shared icon-drawing helpers (used by educational_infographic style).
    # Each icon is drawn into an RGBA tile and composited onto the background.

    _ICON_TILES = None  # lazy cache: dict name→PIL.Image RGBA tile

    @classmethod
    def _get_icon_tiles(cls, icon_size: int = 80):
        if cls._ICON_TILES is not None and cls._ICON_TILES.get("size") == icon_size:
            return cls._ICON_TILES["tiles"]
        tiles = {}
        # ---- Brain (purple circle + squiggly lines) ----
        b = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
        db = ImageDraw.Draw(b)
        cx, cy, r = icon_size // 2, icon_size // 2, icon_size // 2 - 6
        db.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(140, 40, 200, 230))
        db.ellipse([cx - r + 8, cy - r + 8, cx + r - 8, cy + r - 8], outline=(220, 180, 255, 200), width=2)
        # squiggle lines inside
        for _ in range(5):
            sx = cx + random.randint(-r // 2, r // 2)
            sy = cy + random.randint(-r // 2, r // 2)
            db.arc([sx - 12, sy - 12, sx + 12, sy + 12], start=0, end=180, fill=(240, 210, 255, 160), width=2)
        tiles["brain"] = b

        # ---- Robot (rounded rectangle body + circle head + antenna) ----
        r2 = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
        dr = ImageDraw.Draw(r2)
        bw, bh = icon_size // 2, icon_size // 3
        bx, by = (icon_size - bw) // 2, icon_size // 2 - bh // 2 + 6
        dr.rounded_rectangle([bx, by, bx + bw, by + bh], radius=10, fill=(80, 80, 110, 235))
        hr = icon_size // 5
        dr.ellipse([cx - hr, by - hr - 4, cx + hr, by - 4], fill=(120, 120, 160, 235))
        dr.line([cx, by - hr - 4, cx, by - hr - 22], fill=(150, 150, 180, 235), width=2)
        dr.ellipse([cx - 4, by - hr - 24, cx + 4, by - hr - 16], fill=(255, 200, 50, 235))
        # eyes
        dr.ellipse([cx - hr // 2 - 6, by + bh // 4 - 4, cx - hr // 2 + 6, by + bh // 4 + 8], fill=(0, 200, 255, 235))
        dr.ellipse([cx + hr // 2 - 6, by + bh // 4 - 4, cx + hr // 2 + 6, by + bh // 4 + 8], fill=(0, 200, 255, 235))
        # mouth
        dr.line([cx - 12, by + bh // 2 + 4, cx + 12, by + bh // 2 + 4], fill=(180, 180, 200, 220), width=2)
        tiles["robot"] = r2

        # ---- Gear (cog shape) ----
        g = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
        dg = ImageDraw.Draw(g)
        gr = icon_size // 2 - 4
        # outer cog teeth
        teeth = 10
        for i in range(teeth):
            angle = math.radians(i * 360 / teeth)
            tx = cx + int(gr * 0.85 * math.cos(angle))
            ty = cy + int(gr * 0.85 * math.sin(angle))
            ax2 = angle + math.radians(8)
            bx = cx + int(gr * 1.05 * math.cos(ax2))
            by = cy + int(gr * 1.05 * math.sin(ax2))
            dg.polygon([
                (cx + int(gr * 0.7 * math.cos(angle - 0.12)), cy + int(gr * 0.7 * math.sin(angle - 0.12))),
                (cx + int(gr * 1.05 * math.cos(angle - 0.12)), cy + int(gr * 1.05 * math.sin(angle - 0.12))),
                (bx, by),
                (cx + int(gr * 1.05 * math.cos(angle + 0.12)), cy + int(gr * 1.05 * math.sin(angle + 0.12))),
                (cx + int(gr * 0.7 * math.cos(angle + 0.12)), cy + int(gr * 0.7 * math.sin(angle + 0.12))),
            ], fill=(220, 180, 50, 230))
        # inner circle
        dg.ellipse([cx - gr * 0.5, cy - gr * 0.5, cx + gr * 0.5, cy + gr * 0.5], fill=(240, 210, 80, 230))
        dg.ellipse([cx - gr * 0.2, cy - gr * 0.2, cx + gr * 0.2, cy + gr * 0.2], fill=(180, 140, 30, 230))
        tiles["gear"] = g

        # ---- Lightbulb ----
        lb = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lb)
        br = icon_size // 3
        dl.ellipse([cx - br, cy - br * 0.7, cx + br, cy + br * 0.7], fill=(255, 230, 80, 235))
        dl.ellipse([cx - br + 6, cy - br * 0.7 + 6, cx + br - 6, cy + br * 0.7 - 6], outline=(255, 255, 200, 200), width=2)
        # filament squiggles
        for _ in range(3):
            fx = cx + random.randint(-br // 3, br // 3)
            fy = cy - br * 0.3 + random.randint(0, br // 2)
            dl.arc([fx - 8, fy - 8, fx + 8, fy + 8], start=0, end=180, fill=(255, 160, 40, 200), width=2)
        # base
        bs = icon_size // 7
        dl.rounded_rectangle([cx - bs, cy + br * 0.5, cx + bs, cy + br * 0.5 + 14], radius=3, fill=(180, 160, 50, 230))
        dl.rounded_rectangle([cx - bs + 3, cy + br * 0.5 + 16, cx + bs - 3, cy + br * 0.5 + 24], radius=2, fill=(140, 120, 40, 230))
        tiles["lightbulb"] = lb

        # ---- Puzzle piece (simple L-shape + notch) ----
        p = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
        dp = ImageDraw.Draw(p)
        ps = icon_size // 2 - 4
        px, py = cx - ps // 2, cy - ps // 2
        dp.rounded_rectangle([px, py, px + ps, py + ps], radius=6, fill=(60, 160, 220, 235))
        # notch
        notch = ps // 4
        dp.rounded_rectangle([px + ps - notch, py + ps // 2 - notch // 2, px + ps + notch, py + ps // 2 + notch // 2], radius=3, fill=(60, 160, 220, 235))
        dp.rounded_rectangle([px - notch, py + ps // 2 - notch // 2, px, py + ps // 2 + notch // 2], radius=3, fill=(60, 160, 220, 235))
        tiles["puzzle"] = p

        # ---- Database cylinder ----
        d = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
        dd = ImageDraw.Draw(d)
        dw, dh = icon_size // 3, icon_size // 2
        dx, dy = cx - dw // 2, cy - dh // 2
        dd.rounded_rectangle([dx, dy, dx + dw, dy + dh], radius=6, fill=(100, 200, 100, 220))
        dd.ellipse([dx - 2, dy + dh // 3, dx + dw + 2, dy + dh // 3 + 6], outline=(60, 140, 60, 220), width=2)
        dd.ellipse([dx - 2, dy + dh * 2 // 3, dx + dw + 2, dy + dh * 2 // 3 + 6], outline=(60, 140, 60, 220), width=2)
        # rows
        for i in range(3):
            yy = dy + dh // 4 + i * dh // 4
            dd.line([dx + 4, yy, dx + dw - 4, yy], fill=(160, 230, 160, 180), width=2)
        tiles["database"] = d

        cls._ICON_TILES = {"tiles": tiles, "size": icon_size}
        return tiles

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height

    def generate(self, prompt: str, scene_index: int, visual_style: str = "cinematic_tech") -> np.ndarray:
        # ---- Color schemes ----
        cinematic_schemes = [
            {"base": (8, 15, 35), "accent": (0, 120, 200), "glow": (0, 180, 255)},
            {"base": (12, 10, 30), "accent": (140, 40, 180), "glow": (180, 80, 255)},
            {"base": (10, 25, 20), "accent": (0, 180, 100), "glow": (50, 255, 150)},
            {"base": (25, 10, 10), "accent": (200, 50, 50), "glow": (255, 100, 80)},
            {"base": (15, 20, 35), "accent": (60, 120, 200), "glow": (100, 180, 255)},
            {"base": (20, 15, 30), "accent": (180, 80, 160), "glow": (220, 120, 200)},
        ]

        # Bright blue↔purple gradient + yellow accent — matches Simplilearn thumbnail.
        infographic_schemes = [
            {"base": (20, 35, 70), "accent": (255, 200, 50), "glow": (255, 220, 100), "secondary": (100, 160, 240)},
            {"base": (25, 25, 65), "accent": (255, 210, 60), "glow": (255, 230, 120), "secondary": (120, 140, 230)},
            {"base": (18, 30, 60), "accent": (255, 190, 40), "glow": (255, 210, 90), "secondary": (90, 150, 230)},
        ]

        if visual_style == "cinematic_realistic":
            scheme = {"base": (8, 10, 14), "accent": (60, 80, 110), "glow": (170, 200, 220)}
            schemes_type = "realistic"
        elif visual_style == "educational_infographic":
            scheme = infographic_schemes[hash(prompt) % len(infographic_schemes)]
            schemes_type = "infographic"
        else:
            scheme = cinematic_schemes[hash(prompt) % len(cinematic_schemes)]
            schemes_type = "cinematic"

        img = Image.new("RGB", (self.w, self.h), scheme["base"])
        draw = ImageDraw.Draw(img)

        if schemes_type == "infographic":
            # ---- Bright gradient: deep blue at top → slightly lighter purple-blue at bottom ----
            for y in range(self.h):
                ratio = y / self.h
                r = int(scheme["base"][0] + (scheme["secondary"][0] - scheme["base"][0]) * ratio * 0.4)
                g = int(scheme["base"][1] + (scheme["secondary"][1] - scheme["base"][1]) * ratio * 0.5)
                b = int(scheme["base"][2] + (scheme["secondary"][2] - scheme["base"][2]) * ratio * 0.3)
                draw.line([(0, y), (self.w, y)], fill=(max(r, 0), max(g, 0), min(b, 255)))
        else:
            for y in range(self.h):
                ratio = y / self.h
                r = int(scheme["base"][0] * (1 - ratio * 0.5))
                g = int(scheme["base"][1] * (1 - ratio * 0.3))
                b = int(scheme["base"][2] + (scheme["accent"][2] - scheme["base"][2]) * ratio * 0.3)
                draw.line([(0, y), (self.w, y)], fill=(max(r, 0), max(g, 0), min(b, 255)))

        rng = random.Random(hash(prompt) + scene_index * 1000)

        horizon_y = int(self.h * 0.35)
        if visual_style != "cinematic_realistic":
            # Perspective grid
            grid_lines = rng.randint(6, 10)
            for i in range(grid_lines):
                x_offset = (i - grid_lines // 2) * (self.w // grid_lines)
                top_x = self.w // 2 + x_offset // 3
                bot_x = self.w // 2 + x_offset * 2
                line_color = tuple(min(scheme["accent"][j] // 2 + 20, 255) for j in range(3)) if schemes_type != "infographic" else tuple(
                    min(scheme["secondary"][j] // 2 + 30, 255) for j in range(3)
                )
                draw.line([(top_x, horizon_y), (bot_x, self.h)], fill=line_color, width=1)
            for i in range(4):
                y = horizon_y + int((self.h - horizon_y) * (i / 4) ** 1.5)
                alpha_ratio = i / 4
                if schemes_type == "infographic":
                    line_color = tuple(min(int(scheme["secondary"][j] * (0.3 + alpha_ratio * 0.4)) + 20, 255) for j in range(3))
                else:
                    line_color = tuple(min(int(scheme["accent"][j] * (0.3 + alpha_ratio * 0.4)) + 15, 255) for j in range(3))
                draw.line([(0, y), (self.w, y)], fill=line_color, width=1)

            # Hexagonal grid overlay (partial)
            hex_size = rng.randint(40, 60)
            for row in range(-1, self.h // (hex_size * 2) + 2):
                for col in range(-1, self.w // (hex_size * 2) + 2):
                    cx2 = col * int(hex_size * 1.73) + (row % 2) * int(hex_size * 0.87)
                    cy2 = row * int(hex_size * 1.5)
                    if rng.random() < 0.15:
                        if schemes_type == "infographic":
                            hex_color = tuple(min(scheme["secondary"][j] // 4 + 40, 255) for j in range(3))
                        else:
                            hex_color = tuple(min(scheme["accent"][j] // 3 + 25, 255) for j in range(3))
                        points = []
                        for angle in range(0, 360, 60):
                            px2 = cx2 + int(hex_size * 0.4 * math.cos(math.radians(angle)))
                            py2 = cy2 + int(hex_size * 0.4 * math.sin(math.radians(angle)))
                            points.append((px2, py2))
                        if len(points) >= 3:
                            draw.polygon(points, outline=hex_color)

            # Circuit board traces
            for _ in range(rng.randint(8, 15)):
                x1 = rng.randint(0, self.w)
                y1 = rng.randint(0, self.h)
                segments = rng.randint(3, 7)
                current_x, current_y = x1, y1
                if schemes_type == "infographic":
                    trace_color = tuple(min(scheme["secondary"][j] // 2 + 40, 255) for j in range(3))
                else:
                    trace_color = tuple(min(scheme["accent"][j] // 2 + 30, 255) for j in range(3))
                for _ in range(segments):
                    direction = rng.choice(["h", "v"])
                    length = rng.randint(30, 120)
                    if direction == "h":
                        new_x = current_x + rng.choice([-1, 1]) * length
                        draw.line([(current_x, current_y), (new_x, current_y)], fill=trace_color, width=1)
                        current_x = new_x
                    else:
                        new_y = current_y + rng.choice([-1, 1]) * length
                        draw.line([(current_x, current_y), (current_x, new_y)], fill=trace_color, width=1)
                        current_y = new_y
                if schemes_type == "infographic":
                    node_color = tuple(min(scheme["glow"][j], 255) for j in range(3))
                else:
                    node_color = tuple(min(scheme["glow"][j], 255) for j in range(3))
                draw.ellipse([current_x - 3, current_y - 3, current_x + 3, current_y + 3], fill=node_color)

        if schemes_type == "infographic":
            # ---- STARBURST / EXPLOSION behind center title area ----
            # Draw a white-ish starburst radiating from upper-center.
            sx_c, sy_c = self.w // 2, int(self.h * 0.28)
            burst_r = int(self.h * 0.32)
            star_color = (255, 255, 240)
            for i in range(24):
                angle = math.radians(i * 15 + rng.randint(-3, 3))
                arm_len = burst_r * (0.7 + 0.3 * math.sin(i * 1.7 + rng.random()))
                ex = sx_c + int(arm_len * math.cos(angle))
                ey = sy_c + int(arm_len * math.sin(angle))
                mid_r = arm_len * 0.5
                mx = sx_c + int(mid_r * math.cos(angle))
                my = sy_c + int(mid_r * math.sin(angle))
                draw.polygon([
                    (sx_c, sy_c),
                    (ex, ey),
                    (mx + int(8 * math.cos(angle + math.pi / 2)), my + int(8 * math.sin(angle + math.pi / 2))),
                    (mx + int(8 * math.cos(angle - math.pi / 2)), my + int(8 * math.sin(angle - math.pi / 2))),
                ], fill=star_color if rng.random() < 0.5 else (240, 240, 220))
            # Soft glow core
            for r2 in range(int(burst_r * 0.25), 0, -3):
                alpha = int(30 * (1 - r2 / (burst_r * 0.25)))
                draw.ellipse([sx_c - r2, sy_c - r2, sx_c + r2, sy_c + r2], fill=(255, 255, 240))

        # Glowing orbs with halos
        if schemes_type == "infographic":
            orb_count = rng.randint(5, 9)
        elif visual_style != "cinematic_realistic":
            orb_count = rng.randint(4, 8)
        else:
            orb_count = rng.randint(2, 4)
        for _ in range(orb_count):
            x = rng.randint(0, self.w)
            y = rng.randint(0, self.h)
            if schemes_type == "infographic":
                radius = rng.randint(60, 180)
                for r2 in range(radius, 0, -2):
                    alpha = int(10 * (1 - r2 / radius))
                    color = tuple(min(c + alpha, 255) for c in scheme["accent"])
                    draw.ellipse([x - r2, y - r2, x + r2, y + r2], outline=color)
                core_r = rng.randint(4, 8)
                core_color = tuple(min(scheme["glow"][j] + 50, 255) for j in range(3))
                draw.ellipse([x - core_r, y - core_r, x + core_r, y + core_r], fill=core_color)
            else:
                radius = rng.randint(60, 180) if visual_style != "cinematic_realistic" else rng.randint(80, 220)
                for r2 in range(radius, 0, -2):
                    alpha = int(12 * (1 - r2 / radius))
                    color = tuple(min(c + alpha, 255) for c in scheme["accent"])
                    draw.ellipse([x - r2, y - r2, x + r2, y + r2], outline=color)
                core_r = rng.randint(4, 8)
                core_color = tuple(min(scheme["glow"][j] + 50, 255) for j in range(3))
                draw.ellipse([x - core_r, y - core_r, x + core_r, y + core_r], fill=core_color)

        # Floating data particles
        if schemes_type == "infographic":
            particle_count = rng.randint(25, 45)
            icon_tiles = self._get_icon_tiles(icon_size=70)
            icon_names = ["brain", "robot", "gear", "lightbulb", "puzzle", "database"]
        else:
            particle_count = rng.randint(12, 24) if visual_style == "cinematic_realistic" else rng.randint(20, 40)
            icon_tiles = None
            icon_names = []

        for _ in range(particle_count):
            x = rng.randint(0, self.w)
            y = rng.randint(0, self.h)
            if schemes_type == "infographic" and icon_tiles and rng.random() < 0.35:
                # Draw a floating icon at this position
                name = rng.choice(icon_names)
                tile = icon_tiles[name]
                ts = tile.size[0]
                ox = x - ts // 2
                oy = y - ts // 2
                if 0 <= ox < self.w - ts and 0 <= oy < self.h - ts:
                    img.paste(tile, (ox, oy), tile)
            else:
                size = rng.randint(1, 3)
                brightness = rng.randint(80, 200)
                if schemes_type == "infographic":
                    color = tuple(min(scheme["glow"][i] + brightness, 255) for i in range(3))
                else:
                    color = tuple(min(scheme["glow"][i] + brightness, 255) for i in range(3))
                draw.ellipse([x - size, y - size, x + size, y + size], fill=color)

        if schemes_type != "infographic" and visual_style != "cinematic_realistic":
            # Subtle scan lines
            for y in range(0, self.h, 3):
                if rng.random() < 0.3:
                    scan_color = tuple(max(0, c - 8) for c in scheme["base"])
                    draw.line([(0, y), (self.w, y)], fill=scan_color, width=1)

        if visual_style == "cinematic_realistic":
            img = img.filter(ImageFilter.GaussianBlur(radius=0.6))

        arr = np.array(img).astype(np.uint8)
        if schemes_type == "infographic":
            # Keep colors vibrant — no desaturation for educational style.
            pass
        else:
            noise = np.random.normal(0, 1.5, arr.shape).astype(np.int16)
            arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
            if visual_style == "cinematic_realistic":
                arr = (arr.astype(np.float32) * np.array([0.95, 0.97, 1.0], dtype=np.float32)).clip(0, 255).astype(np.uint8)
        return arr


class Particles:
    def __init__(self, width: int, height: int, count: int = 40):
        self.w = width
        self.h = height
        self.particles = []
        rng = random.Random(42)
        for _ in range(count):
            self.particles.append({
                "x": rng.uniform(0, width),
                "y": rng.uniform(0, height),
                "vx": rng.uniform(-0.3, 0.3),
                "vy": rng.uniform(-0.5, -0.1),
                "size": rng.uniform(1, 3),
                "alpha": rng.uniform(40, 120),
                "color_base": rng.choice([(100, 200, 255), (150, 100, 255), (50, 255, 180), (255, 180, 100)]),
            })

    def render_to(self, frame: np.ndarray, t: float):
        for p in self.particles:
            x = (p["x"] + p["vx"] * t * 20) % self.w
            y = (p["y"] + p["vy"] * t * 20) % self.h
            pulse = 0.6 + 0.4 * math.sin(t * 2.0 + p["x"] * 0.01)
            size = max(1, int(p["size"] * pulse))
            alpha = int(p["alpha"] * pulse)
            color = tuple(min(int(c * alpha / 255), 255) for c in p["color_base"])
            y_int, x_int = int(y), int(x)
            sy = max(0, y_int - size)
            ey = min(self.h, y_int + size)
            sx = max(0, x_int - size)
            ex = min(self.w, x_int + size)
            if sy < ey and sx < ex:
                region = frame[sy:ey, sx:ex]
                if region.dtype == np.float32:
                    frame[sy:ey, sx:ex] = np.clip(region * 0 + color, 0, 255).astype(np.uint8)
                else:
                    frame[sy:ey, sx:ex] = color


class VoiceoverGenerator:
    # Male neural voices for Hindi (hi) and Nepali (ne). gTTS only offers a
    # female voice, so hi/ne output uses Edge TTS instead. The slight rate
    # slowdown + small pitch lift produces a calm, natural narrator delivery
    # instead of the default flat robotic read.
    VOICE_PROFILE = {
        "en": {"voice": "en-US-GuyNeural", "rate": "-4%", "pitch": "+0Hz", "volume": "+0%"},
        "hi": {"voice": "hi-IN-MadhurNeural", "rate": "-8%", "pitch": "+2Hz", "volume": "+0%"},
        "ne": {"voice": "ne-NP-SagarNeural", "rate": "-8%", "pitch": "+2Hz", "volume": "+0%"},
    }
    VALID_GTT_LANGS = {"en", "hi", "ne"}

    def __init__(self, config: VideoConfig):
        self.config = config
        self.temp_dir = Path("output/temp")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, text: str, scene_index: int) -> str:
        output_path = str(self.temp_dir / f"voiceover_{scene_index}.mp3")
        lang = self.config.voice_lang
        profile = self.VOICE_PROFILE.get(lang)
        if profile and HAS_EDGE_TTS:
            for attempt in range(2):
                if self._speak_male(text, profile, output_path) and self._audio_ok(output_path):
                    return output_path
                print(f"  [voice] Edge TTS attempt {attempt + 1} produced invalid audio, retrying")
            print(f"  [voice] Edge TTS failed for {lang}, falling back to gTTS")
        tts_lang = lang if lang in self.VALID_GTT_LANGS else "en"
        tts = gTTS(text=text, lang=tts_lang, slow=False)
        tts.save(output_path)
        if not self._audio_ok(output_path):
            raise RuntimeError(f"TTS produced an invalid audio file: {output_path}")
        return output_path

    @staticmethod
    def _audio_ok(path: str) -> bool:
        """Confirm the TTS file is fully written and decodable:
        exists, non-zero, opens, and reports a positive duration."""
        try:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return False
            clip = AudioFileClip(path)
            try:
                return clip.duration > 0.0
            finally:
                clip.close()
        except Exception:
            return False

    @staticmethod
    def _speak_male(text: str, profile: dict, output_path: str) -> bool:
        try:
            async def _run():
                communicate = edge_tts.Communicate(
                    text,
                    profile["voice"],
                    rate=profile.get("rate", "+0%"),
                    volume=profile.get("volume", "+0%"),
                    pitch=profile.get("pitch", "+0Hz"),
                )
                await communicate.save(output_path)
            asyncio.run(_run())
            return os.path.exists(output_path)
        except Exception as e:
            print(f"  [voice] Edge TTS error: {e}")
            return False

    def cleanup(self):
        import glob
        for f in glob.glob(str(self.temp_dir / "voiceover_*.mp3")):
            os.remove(f)


class AmbientMusicGenerator:
    def __init__(self, sample_rate: int = 44100):
        self.sr = sample_rate

    def generate(self, duration: float, output_path: str, style: str = "tech") -> str:
        t = np.linspace(0, duration, int(self.sr * duration), endpoint=False)
        audio = np.zeros_like(t)

        if style == "tech":
            audio = self._tech_ambient(t, duration)
        elif style == "upbeat":
            audio = self._upbeat_ambient(t, duration)
        else:
            audio = self._calm_ambient(t, duration)

        audio = audio / np.max(np.abs(audio)) * 0.3
        audio_int16 = (audio * 32767).astype(np.int16)

        import wave
        with wave.open(output_path, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sr)
            wf.writeframes(audio_int16.tobytes())

        return output_path

    def _tech_ambient(self, t, duration):
        audio = np.zeros_like(t)

        for freq in [65.41, 82.41, 98.0, 110.0]:
            env = np.ones_like(t) * 0.15
            for i in range(int(duration / 4)):
                start = int(i * 4 * self.sr)
                end = int(min((i * 4 + 0.5), duration) * self.sr)
                if end > start:
                    env[start:end] *= np.linspace(0, 1, end - start)
                fade_end = int(min((i * 4 + 3.5), duration) * self.sr)
                fade_start = int(min((i * 4 + 3.0), duration) * self.sr)
                if fade_end > fade_start:
                    env[fade_start:fade_end] *= np.linspace(1, 0, fade_end - fade_start)
            audio += np.sin(2 * np.pi * freq * t) * env

        for freq in [130.81, 164.81, 196.0]:
            pulse_env = np.abs(np.sin(2 * np.pi * 0.25 * t)) * 0.1
            audio += np.sin(2 * np.pi * freq * t) * pulse_env

        noise = np.random.normal(0, 0.02, len(t))
        audio += noise

        return audio

    def _upbeat_ambient(self, t, duration):
        audio = np.zeros_like(t)

        for freq in [110.0, 146.83, 174.61, 220.0]:
            env = (np.sin(2 * np.pi * 2 * t) * 0.5 + 0.5) * 0.2
            audio += np.sin(2 * np.pi * freq * t) * env

        kick_times = np.arange(0, duration, 0.5)
        for kt in kick_times:
            idx = int(kt * self.sr)
            length = min(int(0.1 * self.sr), len(t) - idx)
            if length > 0:
                kick = np.sin(2 * np.pi * 60 * np.linspace(0, 0.1, length))
                kick *= np.exp(-np.linspace(0, 5, length))
                audio[idx:idx+length] += kick * 0.3

        return audio

    def _calm_ambient(self, t, duration):
        audio = np.zeros_like(t)

        for freq in [65.41, 98.0, 130.81]:
            audio += np.sin(2 * np.pi * freq * t) * 0.15

        lfo = np.sin(2 * np.pi * 0.1 * t) * 0.5 + 0.5
        audio *= (0.7 + 0.3 * lfo)

        return audio


class TextRenderer:
    def __init__(self, config: VideoConfig):
        self.config = config
        self.font = self._load_font(70)
        self.font_small = self._load_font(20)
        self._font_cache = {}

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("C:/Windows/Fonts/comic.ttf", size)
        except (OSError, IOError):
            try:
                return ImageFont.truetype("arial.ttf", size)
            except (OSError, IOError):
                return ImageFont.load_default()

    # Order of preference for Indic (Hindi/Nepali) script rendering on Windows.
    _INDIC_FONTS = [
        r"C:\Windows\Fonts\Nirmala.ttc",
        r"C:\Windows\Fonts\Mangal.ttf",
        r"C:\Windows\Fonts\Kokila.ttf",
        r"C:\Windows\Fonts\Utsaah.ttf",
    ]

    @staticmethod
    def _is_devanagari(text) -> bool:
        return bool(text and re.search(r"[\u0900-\u097F]", text))

    def _pick_font(self, size: int, text=None) -> ImageFont.FreeTypeFont:
        """Pick a font that can actually render *text*.

        For Hindi/Nepali (Devanagari) text Montserrat/arial have no glyphs, so
        fall back to a system Indic font; otherwise use the configured font.
        """
        key = (size, bool(self._is_devanagari(text)))
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached
        if key[1]:
            font = None
            for path in self._INDIC_FONTS:
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except (OSError, IOError):
                    continue
            if font is None:
                font = self._load_font(size)
        else:
            try:
                font = ImageFont.truetype(self.config.font_path, size)
            except (OSError, IOError):
                font = self._load_font(size)
        self._font_cache[key] = font
        return font

    def render_text_frame(
        self, text: str, font_size: int = 70,
        text_color: str = "#FFFFFF", position: str = "center",
        shadow: bool = True, animation: str = "fade",
        anim_progress: float = 1.0, text_area_w: int = 0,
        text_area_x: int = 0,
        outline: bool = False,
        outline_color: str = "#000000",
        outline_width: int = 3,
        glow: bool = False,
        glow_color: str = "#FFD700",
        glow_width: int = 6,
        glow_intensity: float = 0.5,
    ) -> np.ndarray:
        font = self._pick_font(font_size, text)

        w = text_area_w if text_area_w > 0 else int(self.config.width * 0.55)
        img = Image.new("RGBA", (self.config.width, self.config.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = _hex_to_rgb(text_color)
        outline_rgb = _hex_to_rgb(outline_color) if outline else None

        # Shrink the font if the text still overflows the target box.
        fit_font = font
        fit_size = font_size
        while fit_size > 26:
            lines = self._wrap_text(text, fit_font, draw, w)
            if all((draw.textbbox((0, 0), line, font=fit_font)[2] - draw.textbbox((0, 0), line, font=fit_font)[0]) <= w for line in lines):
                break
            fit_size -= 2
            fit_font = self._pick_font(fit_size, text)

        lines = self._wrap_text(text, fit_font, draw, w)
        font = fit_font
        line_height = getattr(font, "size", font_size) + 12
        total_height = len(lines) * line_height

        if position == "center":
            start_y = (self.config.height - total_height) // 2
        elif position == "top":
            start_y = max(28, int(self.config.height * 0.10))
        else:
            start_y = self.config.height - total_height - 120

        alpha = int(min(1.0, anim_progress * 2) * 255)
        if animation == "slide_up":
            slide_offset = int((1 - min(1.0, anim_progress * 1.4)) * 36)
        elif animation == "typewriter":
            slide_offset = int((1 - min(1.0, anim_progress * 2.0)) * 10)
        else:
            slide_offset = 0

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            if text_area_w > 0:
                x = text_area_x + max(0, (text_area_w - text_width) // 2)
            else:
                x = (self.config.width - text_width) // 2
            y = start_y + i * line_height + slide_offset

            render_line = line
            if animation == "typewriter" and anim_progress < 1.0:
                visible = max(1, int(len(line) * anim_progress))
                render_line = line[:visible]

            if shadow:
                for offset in [(3, 3), (2, 2), (1, 1)]:
                    draw.text((x + offset[0], y + offset[1]), render_line, font=font, fill=(0, 0, 0, int(140 * alpha / 255)))
            if glow and glow_intensity > 0:
                glow_rgb = _hex_to_rgb(glow_color)
                for gw in range(glow_width, 0, -2):
                    ga = int(255 * glow_intensity * (1.0 - gw / glow_width) * 0.3)
                    for gox, goy in [(gw, 0), (-gw, 0), (0, gw), (0, -gw),
                                     (gw, gw), (-gw, gw), (gw, -gw), (-gw, -gw)]:
                        draw.text((x + gox, y + goy), render_line, font=font, fill=(*glow_rgb, ga))
            if outline and outline_rgb:
                # Draw outline by rendering in all 8 compass directions.
                for ox, oy in [(outline_width, 0), (-outline_width, 0), (0, outline_width), (0, -outline_width),
                               (outline_width, outline_width), (-outline_width, outline_width),
                               (outline_width, -outline_width), (-outline_width, -outline_width)]:
                    draw.text((x + ox, y + oy), render_line, font=font, fill=(*outline_rgb, alpha))
            draw.text((x, y), render_line, font=font, fill=(*color, alpha))

        return np.array(img)

    def render_info_bar(self, title: str, scene_num: int, total_scenes: int, progress: float = 0.0) -> np.ndarray:
        w, h = self.config.width, 50
        img = Image.new("RGBA", (w, h), (0, 0, 0, 210))
        draw = ImageDraw.Draw(img)

        # Top accent line
        draw.rectangle([(0, 0), (w, 2)], fill=(0, 200, 220, 200))

        font = self._pick_font(16, title)
        font_small = self._pick_font(13, title)

        # Title
        draw.text((20, 8), title[:55], font=font, fill=(200, 220, 255, 240))

        # Scene counter
        counter = f"Scene {scene_num}/{total_scenes}"
        bbox = draw.textbbox((0, 0), counter, font=font_small)
        cw = bbox[2] - bbox[0]
        draw.text((w - cw - 20, 12), counter, font=font_small, fill=(150, 170, 200, 200))

        # Progress bar background
        bar_y = h - 10
        bar_h = 4
        draw.rounded_rectangle([(20, bar_y), (w - 20, bar_y + bar_h)], radius=2, fill=(40, 40, 50, 180))

        # Progress bar fill
        if progress > 0:
            fill_w = int((w - 40) * progress)
            draw.rounded_rectangle([(20, bar_y), (20 + fill_w, bar_y + bar_h)], radius=2, fill=(0, 200, 220, 220))

        return np.array(img)

    def render_bullet_point(
        self, text: str, number: int, font_size: int = 50,
        text_color: str = "#FFFFFF", text_area_w: int = 0
    ) -> np.ndarray:
        font = self._pick_font(font_size, text)

        w = text_area_w if text_area_w > 0 else int(self.config.width * 0.50)
        tmp_img = Image.new("RGBA", (w + 60, 180), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tmp_img)
        color = _hex_to_rgb(text_color)

        # --- number chip -----------------------------------------------------
        chip_r = (0, 210, 210)
        num_text = f"{number}."
        num_font = font
        num_bbox = draw.textbbox((0, 0), num_text, font=num_font)
        num_w = num_bbox[2] - num_bbox[0]
        chip_pad = 14
        chip_h = max(34, num_bbox[3] - num_bbox[1] + 20)
        chip_w = max(34, num_w + 24)

        lines_unused = self._wrap_text(text, font, draw, w - chip_w - 28)
        line_h = max(26, getattr(font, "size", font_size) + 4)
        body_lines = lines_unused[:2]
        height = max(chip_h + 18, 18 + len(body_lines) * line_h + 16)

        img = Image.new("RGBA", (w + 60, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # --- card background (rounded, translucent dark) ----------------------
        card_margin = 2
        draw.rounded_rectangle(
            [(card_margin, card_margin), (w + 60 - card_margin, height - card_margin)],
            radius=14, fill=(8, 12, 22, 205), outline=(255, 255, 255, 26), width=1
        )
        # thin accent line on the left
        draw.rounded_rectangle(
            [(card_margin + 6, card_margin + 8), (card_margin + 10, height - card_margin - 8)],
            radius=3, fill=(chip_r[0], chip_r[1], chip_r[2], 235)
        )

        # --- icon chip --------------------------------------------------------
        chip_x, chip_y = 20, (height - chip_h) // 2
        draw.rounded_rectangle(
            [(chip_x, chip_y), (chip_x + chip_w, chip_y + chip_h)],
            radius=chip_h // 2, fill=(*chip_r, 235)
        )
        # Semantic icon (or the numbered tile) on the chip so each bullet has
        # a scannable visual cue instead of bare text.
        icon_tile = None
        try:
            tiles = TechBackground._get_icon_tiles(50)
            icon_key = CinematicVideoGenerator._icon_name_for_text(text)
            icon_tile = tiles.get(icon_key) or tiles.get("gear")
        except Exception:
            icon_tile = None
        if icon_tile is not None:
            use = int(min(chip_w - 16, chip_h - 16, 42))
            itile = icon_tile.resize((use, use), Image.LANCZOS)
            img.paste(itile, (chip_x + (chip_w - use) // 2,
                              chip_y + (chip_h - use) // 2 - 3), itile)
        else:
            # number text on chip (dark, centered)
            nb = draw.textbbox((0, 0), num_text, font=num_font)
            nw = nb[2] - nb[0]
            nh = nb[3] - nb[1]
            draw.text((chip_x + (chip_w - nw) // 2, chip_y + (chip_h - nh) // 2 - nb[1]),
                      num_text, font=num_font, fill=(10, 20, 24, 255))

        # --- body text --------------------------------------------------------
        txt_x = chip_x + chip_w + 16
        for i, line in enumerate(body_lines):
            y = 10 + i * line_h
            for offset in [(2, 2), (1, 1)]:
                draw.text((txt_x + offset[0], y + offset[1]), line, font=font, fill=(0, 0, 0, 130))
            draw.text((txt_x, y), line, font=font, fill=(*color, 255))

        return np.array(img)

    def _wrap_text(self, text, font, draw, max_width):
        words = text.split()
        lines, current_line = [], []
        for word in words:
            test = ' '.join(current_line + [word])
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        if current_line:
            lines.append(' '.join(current_line))
        return lines


def _alpha_composite(base: np.ndarray, overlay_rgba: np.ndarray, x: int, y: int):
    oh, ow = overlay_rgba.shape[:2]
    bh, bw = base.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(bw, x + ow)
    y2 = min(bh, y + oh)
    if x1 >= x2 or y1 >= y2:
        return
    ox1, oy1 = x1 - x, y1 - y
    ox2, oy2 = ox1 + (x2 - x1), oy1 + (y2 - y1)

    # Work in float32, then write back as uint8 — no unsafe casting.
    roi = base[y1:y2, x1:x2].astype(np.float32)
    overlay = overlay_rgba[oy1:oy2, ox1:ox2]
    if overlay.shape[2] == 3:
        # RGB overlay: treat as fully opaque (alpha = 255)
        overlay = np.dstack([overlay, np.full((overlay.shape[0], overlay.shape[1], 1), 255, dtype=np.uint8)])
    a_u8 = overlay[:, :, 3]
    if np.all(a_u8 == 0):
        return
    if np.all(a_u8 == 255):
        base[y1:y2, x1:x2, :3] = overlay[:, :, :3]
        return
    a = a_u8.astype(np.float32) / 255.0
    inv_a = 1.0 - a
    if roi.shape[2] == 4:
        # RGBA base: blend RGB channels, preserve max alpha
        blended_rgb = roi[:, :, :3] * inv_a[:, :, np.newaxis] + overlay[:, :, :3].astype(np.float32) * a[:, :, np.newaxis]
        base[y1:y2, x1:x2, :3] = np.clip(blended_rgb, 0, 255).astype(np.uint8)
        base[y1:y2, x1:x2, 3] = np.maximum(
            base[y1:y2, x1:x2, 3].astype(np.float32),
            overlay[:, :, 3].astype(np.float32) * a,
        ).clip(0, 255).astype(np.uint8)
    else:
        blended = roi * inv_a[:, :, np.newaxis] + overlay[:, :, :3].astype(np.float32) * a[:, :, np.newaxis]
        base[y1:y2, x1:x2] = np.clip(blended, 0, 255).astype(np.uint8)


class TechnicalDiagram:
    """Renders technical diagram overlays onto video frames.

    Supported diagram types:
      - "flow_chart": horizontal flow with boxes and arrows between steps
      - "architecture": layered architecture diagram (client/server/db)
      - "cnn_layers": stacked convolutional neural network layers
      - "network_graph": simple node-and-edge graph layout
      - "data_pipeline": ETL/data flow diagram
      - "comparison": side-by-side feature comparison table
    """

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height

    def render(self, diagram_type: str, diagram_data: dict, t: float) -> "Image.Image":
        """Render a diagram overlay at time t. Returns RGBA PIL Image."""
        renderers = {
            "flow_chart": self._render_flow_chart,
            "architecture": self._render_architecture,
            "cnn_layers": self._render_cnn_layers,
            "network_graph": self._render_network_graph,
            "data_pipeline": self._render_data_pipeline,
            "comparison": self._render_comparison,
        }
        renderer = renderers.get(diagram_type)
        if renderer is None:
            return Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        return renderer(diagram_data, t)

    def position_diagram(self, img: "Image.Image",
                         zone_top: int, zone_bottom: int,
                         zone_width_frac: float = 0.74) -> "Image.Image":
        """Crop a full-frame diagram to its drawn content and scale it into a
        dedicated horizontal band (below the scene heading), so text and the
        technical diagram never overlap. Returns a full-frame RGBA image with
        the diagram centered inside the requested zone."""
        arr = np.array(img)
        alpha = arr[:, :, 3]
        ys, xs = np.where(alpha > 8)
        if len(xs) == 0:
            return Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        pad = 8
        x0 = max(0, int(xs.min()) - pad)
        y0 = max(0, int(ys.min()) - pad)
        x1 = min(self.w - 1, int(xs.max()) + pad)
        y1 = min(self.h - 1, int(ys.max()) + pad)
        crop = img.crop((x0, y0, x1 + 1, y1 + 1))
        zone_h = max(1, zone_bottom - zone_top)
        zone_w = max(1, int(self.w * zone_width_frac))
        # Only ever downscale; keep the diagram readable and never fuzzy-up.
        scale = max(0.25, min(zone_w / crop.width, zone_h / crop.height, 1.0))
        nw = max(1, int(crop.width * scale))
        nh = max(1, int(crop.height * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        canvas.paste(crop, ((self.w - nw) // 2, zone_top + (zone_h - nh) // 2), crop)
        return canvas

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _box(draw, xy, size, fill, outline=None, outline_width=2):
        x, y = xy
        w, h = size
        draw.rounded_rectangle(
            [(x, y), (x + w, y + h)], radius=8,
            fill=fill, outline=outline, width=outline_width,
        )

    @staticmethod
    def _arrow(draw, x1, y1, x2, y2, color, width=3):
        draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
        import math
        angle = math.atan2(y2 - y1, x2 - x1)
        head_len = 12
        for da in (-0.4, 0.4):
            hx = x2 - head_len * math.cos(angle + da)
            hy = y2 - head_len * math.sin(angle + da)
            draw.polygon([(x2, y2), (hx, hy),
                          (x2 - head_len * math.cos(angle - da),
                           y2 - head_len * math.sin(angle - da))],
                         fill=color)

    @staticmethod
    def _label(draw, xy, text, font, fill=(255, 255, 255, 255), anchor="mm"):
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)

    def _pick_font(self, size: int):
        """Try to load a nice font; fall back to default."""
        try:
            from PIL import ImageFont
            return ImageFont.truetype("C:/Windows/Fonts/comic.ttf", size)
        except Exception:
            try:
                return ImageFont.truetype("arial.ttf", size)
            except Exception:
                return ImageFont.load_default()

    # ------------------------------------------------------------------
    # Diagram renderers
    # ------------------------------------------------------------------

    def _render_flow_chart(self, data: dict, t: float) -> Image.Image:
        steps = data.get("steps", [])
        if not steps:
            # Return diagram-sized transparent image (caller handles empty case)
            return Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        n = len(steps)
        box_w = min(180, (self.w - (n + 1) * 40) // n)
        box_h = 80
        gap = 30
        total_w = n * box_w + (n - 1) * gap
        start_x = (self.w - total_w) // 2
        y = self.h - box_h - 60
        font = self._pick_font(28)
        for i, step in enumerate(steps):
            label = step.get("label", f"Step {i + 1}")
            color = step.get("color", (40, 60, 120, 230))
            bx = start_x + i * (box_w + gap)
            self._box(draw, (bx, y), (box_w, box_h), color,
                      outline=(100, 180, 255, 180), outline_width=2)
            self._label(draw, (bx + box_w // 2, y + box_h // 2), label, font)
            if i < n - 1:
                ax1 = bx + box_w
                ax2 = bx + box_w + gap
                self._arrow(draw, ax1, y + box_h // 2, ax2, y + box_h // 2,
                            (100, 180, 255, 200))
        title = data.get("title", "")
        if title:
            title_font = self._pick_font(32)
            title_alpha = min(1.0, t / 0.5) if t < 0.5 else 1.0
            title_y = y - 40
            if title_alpha < 1.0:
                overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                self._label(od, (self.w // 2, title_y), title, title_font,
                            fill=(255, 255, 255, int(255 * title_alpha)))
                img = Image.alpha_composite(img, overlay)
            else:
                self._label(draw, (self.w // 2, title_y), title, title_font,
                            fill=(255, 255, 255, 255))
        return img

    def _render_architecture(self, data: dict, t: float) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        raw_layers = data.get("layers", [
            {"label": "Client", "color": (60, 120, 200, 220), "height_ratio": 0.12},
            {"label": "Load Balancer", "color": (40, 160, 160, 220), "height_ratio": 0.10},
            {"label": "App Server", "color": (180, 80, 60, 220), "height_ratio": 0.18},
            {"label": "Database", "color": (100, 180, 60, 220), "height_ratio": 0.14},
        ])
        # Tolerate string layers from topic generator (education mode)
        layers = [({"label": l} if isinstance(l, str) else l) for l in raw_layers]
        layer_h = self.h // len(layers)
        for i, layer in enumerate(layers):
            y = i * layer_h
            h = max(30, int(layer_h * layer.get("height_ratio", 0.12)))
            w = int(self.w * 0.55)
            x = (self.w - w) // 2
            color = layer.get("color", (60, 60, 120, 200))
            label = layer.get("label", f"Layer {i + 1}")
            self._box(draw, (x, y), (w, h), color,
                      outline=(200, 200, 255, 100), outline_width=1)
            font = self._pick_font(max(16, h // 4))
            self._label(draw, (x + w // 2, y + h // 2), label, font)
            if i < len(layers) - 1:
                ay = y + h
                nxt = layers[i + 1] if isinstance(layers[i + 1], dict) else {"height_ratio": 0.12}
                ny = (i + 1) * layer_h + max(30, int(layer_h * nxt.get("height_ratio", 0.12)))
                self._arrow(draw, self.w // 2, ay, self.w // 2, ny,
                            (150, 180, 220, 150), width=2)
        alpha = min(1.0, t / 0.6) if t < 0.6 else 1.0
        if alpha < 1.0:
            overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            for i, layer in enumerate(layers):
                y = i * layer_h
                h = max(30, int(layer_h * layer.get("height_ratio", 0.12)))
                w = int(self.w * 0.55)
                x = (self.w - w) // 2
                color = layer.get("color", (60, 60, 120, 200))
                label = layer.get("label", "")
                self._box(od, (x, y), (w, h), (*color[:3], int(color[3] * alpha)),
                          outline=(200, 200, 255, int(100 * alpha)), outline_width=1)
                font = self._pick_font(max(16, h // 4))
                self._label(od, (x + w // 2, y + h // 2), label, font,
                            fill=(255, 255, 255, int(255 * alpha)))
            img = Image.alpha_composite(img, overlay)
        return img

    def _render_cnn_layers(self, data: dict, t: float) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        raw_layers = data.get("layers", [
            {"label": "Input", "channels": 3, "size": 224},
            {"label": "Conv1", "channels": 32, "size": 112, "kernel": 3},
            {"label": "Pool1", "channels": 32, "size": 56},
            {"label": "Conv2", "channels": 64, "size": 56, "kernel": 3},
            {"label": "Pool2", "channels": 64, "size": 28},
            {"label": "FC", "channels": 128, "size": 1},
            {"label": "Output", "channels": 10, "size": 1},
        ])
        layers = [({"label": l, "channels": 1} if isinstance(l, str) else l) for l in raw_layers]
        n = len(layers)
        layer_h = (self.h - 60) // n
        start_y = 30
        max_bar_w = int(self.w * 0.5)
        min_bar_w = 20
        for i, layer in enumerate(layers):
            y = start_y + i * layer_h
            channels = layer.get("channels", 1)
            bar_w = max(min_bar_w, int(max_bar_w * (channels / 128)))
            bar_h = max(8, layer_h - 16)
            x = (self.w - bar_w) // 2
            label = layer.get("label", f"L{i + 1}")
            if "Conv" in label:
                color = (60, 100, 200, 220)
            elif "Pool" in label:
                color = (40, 160, 160, 220)
            elif "FC" in label or "Output" in label:
                color = (200, 80, 60, 220)
            elif "Input" in label:
                color = (100, 200, 100, 220)
            else:
                color = (120, 120, 160, 220)
            self._box(draw, (x, y), (bar_w, bar_h), color,
                      outline=(200, 220, 255, 80), outline_width=1)
            font = self._pick_font(max(12, layer_h // 3))
            self._label(draw, (x + bar_w // 2, y + bar_h + 4), f"{channels}ch",
                        font, fill=(200, 220, 255, 200), anchor="mt")
            name_font = self._pick_font(max(11, layer_h // 4))
            self._label(draw, (x - 8, y + bar_h // 2), label, name_font,
                        fill=(200, 220, 255, 200), anchor="rm")
            if i < n - 1:
                ny = start_y + (i + 1) * layer_h
                draw.line([(self.w // 2, y + bar_h), (self.w // 2, ny)],
                         fill=(100, 150, 200, 80), width=1)
        title = data.get("title", "")
        if title:
            title_font = self._pick_font(30)
            alpha = min(1.0, t / 0.5) if t < 0.5 else 1.0
            if alpha < 1.0:
                overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                self._label(od, (self.w // 2, 8), title, title_font,
                            fill=(255, 255, 255, int(255 * alpha)))
                img = Image.alpha_composite(img, overlay)
            else:
                self._label(draw, (self.w // 2, 8), title, title_font,
                            fill=(255, 255, 255, 255))
        return img

    def _render_network_graph(self, data: dict, t: float) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        raw_nodes = data.get("nodes", [])
        raw_edges = data.get("edges", [])
        nodes = [({"id": n, "label": n} if isinstance(n, str) else n) for n in raw_nodes]
        edges = []
        for e in raw_edges:
            if isinstance(e, (list, tuple)) and len(e) == 2:
                edges.append({"from": str(e[0]), "to": str(e[1])})
            elif isinstance(e, dict):
                edges.append(e)
        if not nodes:
            return img
        cx, cy = self.w // 2, self.h // 2 - 20
        radius = min(self.w, self.h) // 3
        n = len(nodes)
        node_positions = {}
        for i, node in enumerate(nodes):
            angle = 2 * math.pi * i / n - math.pi / 2
            nx = cx + radius * math.cos(angle)
            ny = cy + radius * math.sin(angle)
            node_positions[node["id"]] = (nx, ny, node)
        edge_color = (100, 150, 200, 120)
        for edge in edges:
            src = edge.get("from")
            dst = edge.get("to")
            if src in node_positions and dst in node_positions:
                x1, y1, _ = node_positions[src]
                x2, y2, _ = node_positions[dst]
                draw.line([(x1, y1), (x2, y2)], fill=edge_color, width=2)
                steps = 10
                for s in range(1, steps):
                    t2 = s / steps
                    dx = x1 + (x2 - x1) * t2
                    dy = y1 + (y2 - y1) * t2
                    if s % 2 == 0:
                        draw.ellipse([dx - 1, dy - 1, dx + 1, dy + 1], fill=edge_color)
        for node_id, (nx, ny, node) in node_positions.items():
            r = node.get("radius", 22)
            color = node.get("color", (60, 100, 180, 230))
            self._box(draw, (nx - r, ny - r), (r * 2, r * 2), color,
                      outline=(200, 220, 255, 100), outline_width=2)
            font = self._pick_font(max(10, r // 2))
            self._label(draw, (nx, ny), node.get("label", node_id), font,
                        fill=(255, 255, 255, 240))
        title = data.get("title", "")
        if title:
            title_font = self._pick_font(28)
            alpha = min(1.0, t / 0.5) if t < 0.5 else 1.0
            if alpha < 1.0:
                overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                self._label(od, (self.w // 2, 8), title, title_font,
                            fill=(255, 255, 255, int(255 * alpha)))
                img = Image.alpha_composite(img, overlay)
            else:
                self._label(draw, (self.w // 2, 8), title, title_font,
                            fill=(255, 255, 255, 255))
        return img

    def _render_data_pipeline(self, data: dict, t: float) -> Image.Image:
        raw = data.get("stages", [
            {"label": "Extract", "icon": "database", "color": (60, 120, 200, 220)},
            {"label": "Transform", "icon": "gear", "color": (200, 160, 40, 220)},
            {"label": "Load", "icon": "lightbulb", "color": (60, 200, 100, 220)},
        ])
        stages = [({"label": s} if isinstance(s, str) else s) for s in raw]
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        n = len(stages)
        box_w = min(160, (self.w - (n + 1) * 30) // n)
        box_h = 90
        gap = 25
        total_w = n * box_w + (n - 1) * gap
        start_x = (self.w - total_w) // 2
        y = self.h - box_h - 50
        try:
            tiles = TechBackground._get_icon_tiles(50)
        except Exception:
            tiles = {}
        for i, stage in enumerate(stages):
            bx = start_x + i * (box_w + gap)
            color = stage.get("color", (60, 60, 120, 220))
            label = stage.get("label", f"Step {i + 1}")
            self._box(draw, (bx, y), (box_w, box_h), color,
                      outline=(180, 200, 255, 100), outline_width=2)
            icon_name = stage.get("icon", "puzzle")
            if icon_name in tiles:
                icon = tiles[icon_name].copy()
                isz = max(20, int(min(box_w, box_h) * 0.35))
                icon = icon.resize((isz, isz), Image.LANCZOS)
                ix = bx + (box_w - isz) // 2
                iy = y + (box_h - isz) // 2 - 5
                # Sync img_arr from current img (includes box just drawn)
                img_arr = np.array(img)
                _alpha_composite(img_arr, np.array(icon), ix, iy)
                img = Image.fromarray(img_arr)
                draw = ImageDraw.Draw(img)
            font = self._pick_font(22)
            self._label(draw, (bx + box_w // 2, y + box_h + 12), label, font,
                        fill=(200, 220, 255, 240))
            if i < n - 1:
                ax1 = bx + box_w
                ax2 = bx + box_w + gap
                self._arrow(draw, ax1, y + box_h // 2, ax2, y + box_h // 2,
                            (150, 200, 255, 180), width=2)
        title = data.get("title", "")
        if title:
            title_font = self._pick_font(30)
            alpha = min(1.0, t / 0.5) if t < 0.5 else 1.0
            if alpha < 1.0:
                overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                self._label(od, (self.w // 2, y - 30), title, title_font,
                            fill=(255, 255, 255, int(255 * alpha)))
                img = Image.alpha_composite(img, overlay)
            else:
                self._label(draw, (self.w // 2, y - 30), title, title_font,
                            fill=(255, 255, 255, 255))
        return img

    def _render_comparison(self, data: dict, t: float) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        items = data.get("items", [])
        headers = data.get("headers", ["Feature", "Option A", "Option B"])
        if not items:
            return img
        cols = len(headers)
        rows = len(items) + 1
        margin = 40
        avail_w = self.w - 2 * margin
        avail_h = self.h - 80
        col_w = avail_w // cols
        row_h = max(28, avail_h // rows)
        header_color = (30, 40, 60, 230)
        row_colors = [(20, 25, 40, 210), (30, 35, 50, 210)]
        accent_color = (0, 180, 220, 200)
        font = self._pick_font(22)
        font_sm = self._pick_font(18)
        for r in range(rows):
            for c in range(cols):
                x = margin + c * col_w
                y = 40 + r * row_h
                if r == 0:
                    self._box(draw, (x, y), (col_w - 4, row_h - 4),
                              header_color, outline=accent_color, outline_width=1)
                    text = headers[c]
                else:
                    bg = row_colors[r % 2]
                    self._box(draw, (x, y), (col_w - 4, row_h - 4),
                              bg, outline=(60, 70, 90, 80), outline_width=1)
                    item = items[r - 1]
                    text = item.get(headers[c], "") if isinstance(item, dict) else str(item)
                text_x = x + (col_w - 4) // 2
                text_y = y + (row_h - 4) // 2
                use_font = font_sm if len(str(text)) > 12 else font
                self._label(draw, (text_x, text_y), str(text), use_font,
                            fill=(220, 230, 255, 240))
        title = data.get("title", "")
        if title:
            title_font = self._pick_font(28)
            self._label(draw, (self.w // 2, 16), title, title_font,
                        fill=(255, 255, 255, 240))
        return img


class FloatingIconOverlay:
    """
    Animated floating icons that orbit around a target point (typically the
    character). Uses the same icon tiles as TechBackground.educational_infographic
    but moves them in slow looping paths so the scene feels alive.
    """

    def __init__(
        self,
        width: int,
        height: int,
        icon_names: List[str],
        icon_size: int = 64,
        count: int = 8,
        rng_seed: Optional[int] = None,
    ):
        self.w = width
        self.h = height
        self.icon_size = icon_size
        self.icons = []
        rng = random.Random(rng_seed)
        tiles = TechBackground._get_icon_tiles(icon_size)
        for _ in range(count):
            name = rng.choice(icon_names)
            tile = tiles[name].copy()
            # Orbit parameters
            self.icons.append({
                "tile": tile,
                "name": name,
                "cx": rng.uniform(-0.15, 0.15),   # relative center offset from target
                "cy": rng.uniform(-0.15, 0.15),
                "rx": rng.uniform(80, 200),        # orbit radius X
                "ry": rng.uniform(40, 120),        # orbit radius Y
                "phase": rng.uniform(0, 2 * math.pi),
                "speed": rng.uniform(0.2, 0.6),    # radians per second
                "scale": rng.uniform(0.5, 0.85),
                "pulse_speed": rng.uniform(0.3, 0.8),
                "pulse_phase": rng.uniform(0, 2 * math.pi),
                "opacity": rng.uniform(0.5, 0.9),
            })

    def render_to(
        self,
        frame: np.ndarray,
        t: float,
        target_x: float,
        target_y: float,
        target_size: float = 1.0,
    ) -> None:
        """Composite floating icons onto *frame* at time *t*, orbiting (*target_x*, *target_y*)."""
        w, h = self.w, self.h
        is_ = self.icon_size
        for ic in self.icons:
            angle = ic["speed"] * t + ic["phase"]
            # Elliptical orbit around target.
            ox = ic["cx"] * is_ * target_size
            oy = ic["cy"] * is_ * target_size
            px = ox + math.cos(angle) * ic["rx"] * target_size
            py = oy + math.sin(angle) * ic["ry"] * target_size
            # Pulse scale.
            pulse = 0.6 + 0.4 * math.sin(ic["pulse_speed"] * t + ic["pulse_phase"])
            scale = ic["scale"] * pulse
            # Size.
            size = max(20, int(is_ * scale))
            if size != is_:
                scaled = ic["tile"].resize((size, size), Image.LANCZOS)
            else:
                scaled = ic["tile"]
            # Position (centered on orbit point).
            x = int(target_x + px - size / 2)
            y = int(target_y + py - size / 2)
            # Bounds check.
            sx1 = max(0, x)
            sy1 = max(0, y)
            sx2 = min(w, x + size)
            sy2 = min(h, y + size)
            if sx1 >= sx2 or sy1 >= sy2:
                continue
            ox1 = sx1 - x
            oy1 = sy1 - y
            ox2 = ox1 + (sx2 - sx1)
            oy2 = oy1 + (sy2 - sy1)
            region = frame[sy1:sy2, sx1:sx2]
            overlay = np.array(scaled)[oy1:oy2, ox1:ox2]
            # Alpha blend.
            alpha = overlay[:, :, 3].astype(np.float32) / 255.0 * ic["opacity"]
            alpha_3 = alpha[:, :, np.newaxis]
            region[:, :, :3] = (
                region[:, :, :3].astype(np.float32) * (1 - alpha_3)
                + overlay[:, :, :3].astype(np.float32) * alpha_3
            ).clip(0, 255).astype(np.uint8)
            region[:, :, 3] = np.maximum(
                region[:, :, 3].astype(np.float32),
                overlay[:, :, 3].astype(np.float32) * ic["opacity"],
            ).clip(0, 255).astype(np.uint8)


class FloatingIcons:
    """
    Renders floating icons (brain, robot, gear, etc.) that orbit around a
    target position. Used to add life to the scene around the character.
    """

    def __init__(self, width: int, height: int, icon_size: int = 70, count: int = 10):
        self.w = width
        self.h = height
        self.icon_size = icon_size
        self.count = count
        self.icons = []
        rng = random.Random(42)
        tiles = TechBackground._get_icon_tiles(icon_size)
        for _ in range(count):
            name = rng.choice(["brain", "robot", "gear", "lightbulb", "puzzle", "database"])
            self.icons.append({
                "tile": tiles[name],
                "name": name,
                "base_x": rng.uniform(0, width),
                "base_y": rng.uniform(0, height),
                "orbit_cx": rng.uniform(-200, 200),
                "orbit_cy": rng.uniform(-150, 150),
                "orbit_r": rng.uniform(80, 250),
                "speed": rng.uniform(0.15, 0.4),
                "phase": rng.uniform(0, 2 * math.pi),
                "scale": rng.uniform(0.5, 0.8),
                "pulse_speed": rng.uniform(0.3, 0.8),
            })

    def render_to(self, frame: np.ndarray, t: float, target_x: float, target_y: float):
        """Render floating icons onto frame, orbiting around (target_x, target_y)."""
        w, h = self.w, self.h

        for ic in self.icons:
            angle = ic["speed"] * t + ic["phase"]
            # Orbit centered on the target, with tighter spread.
            orbit_x = math.cos(angle) * ic["orbit_r"] * 0.5
            orbit_y = math.sin(angle) * ic["orbit_r"] * 0.3
            cx = target_x + orbit_x
            cy = target_y + orbit_y
            pulse = 0.7 + 0.3 * math.sin(ic["pulse_speed"] * t + ic["phase"])
            size = max(15, int(self.icon_size * ic["scale"] * pulse))
            tile = ic["tile"]
            if tile.size[0] != size:
                tile = tile.resize((size, size), Image.LANCZOS)
            x = int(cx - size / 2)
            y = int(cy - size / 2)
            # Only render if visible on screen
            if x + size < 0 or x > w or y + size < 0 or y > h:
                continue
            # Use _alpha_composite which handles RGB base + RGBA overlay.
            _alpha_composite(frame, np.array(tile), x, y)


class CinematicVideoGenerator:
    def __init__(self, config: Optional[VideoConfig] = None):
        self.config = config or VideoConfig()
        self.voice_gen = VoiceoverGenerator(self.config)
        self.music_gen = AmbientMusicGenerator()
        self.text_renderer = TextRenderer(self.config)
        self.presenter_avatar = self._load_presenter_avatar()
        self.char_renderer = CharacterRenderer(
            width=int(self.config.width * 0.30),
            height=int(self.config.height * 0.62)
        )
        self.diagram_renderer = TechnicalDiagram(self.config.width, self.config.height)

    # Keyword -> icon-tile map used to decorate headings/bullets with a
    # semantic visual (drawn tiles, so they render reliably in any font).
    _ICON_KEYWORDS = [
        ("brain", ["ai", "artificial", "intelligence", "neural", "deep learning",
                   "machine learning", "learning", "model", "rag", "llm", "gpt",
                   "transformer", "training", "reasoning"]),
        ("robot", ["robot", "automation", "agent", "bot", "robotics", "autonomous"]),
        ("gear", ["hardware", "gpu", "cpu", "chip", "docker", "container",
                  "kubernetes", "server", "cloud", "infra", "devops", "deploy",
                  "ci", "cd", "build", "pipeline", "infrastructure", "compute"]),
        ("database", ["database", "data", "storage", "sql", "etl", "warehouse",
                      "spark", "hadoop", "query", "retriev", "index"]),
        ("puzzle", ["problem", "challenge", "security", "risk", "vs ",
                    "comparison", "tradeoff", "limit", "pitfall", "debug"]),
        ("lightbulb", ["idea", "innovation", "future", "trend", "tip", "best",
                       "start", "why", "overview", "intro", "guide", "understand"]),
    ]

    @classmethod
    def _icon_name_for_text(cls, text: str) -> str:
        low = (text or "").lower()
        for icon, keywords in cls._ICON_KEYWORDS:
            if any(kw in low for kw in keywords):
                return icon
        return "gear"

    def _bullet_icon(self, text: str) -> Optional[Image.Image]:
        """Return a small semantic icon tile for a bullet, or None if tiles
        are unavailable."""
        try:
            tiles = TechBackground._get_icon_tiles(50)
            icon = self._icon_name_for_text(text or "")
            tile = tiles.get(icon) or tiles.get("gear")
            if tile is None:
                return None
            tile = tile.copy()
            isz = 40
            return tile.resize((isz, isz), Image.LANCZOS)
        except Exception:
            return None

    def _heading_icon_image(self, text: str, icon_size: int = 44) -> Optional[np.ndarray]:
        """Full-frame RGBA array with a semantic icon tile centered horizontally,
        positioned a fixed gap above the scene heading line."""
        if not (text or "").strip():
            return None
        try:
            tiles = TechBackground._get_icon_tiles(60)
            tile = tiles.get(self._icon_name_for_text(text)) or tiles.get("gear")
            if tile is None:
                return None
            tile = tile.copy().resize((icon_size, icon_size), Image.LANCZOS)
            out = np.zeros((self.config.height, self.config.width, 4), dtype=np.uint8)
            arr = np.array(tile)
            x = (self.config.width - icon_size) // 2
            out[0:icon_size, x:x + icon_size] = arr
            return out
        except Exception:
            return None

    @staticmethod
    def _text_panel(
        text_area_x: int, text_area_w: int, h: int, w: int,
        panel_h: int = 300, alpha_max: int = 165,
    ) -> np.ndarray:
        """
        Vertical dark gradient panel (RGBA) centered behind the text column.
        Top/bottom fade out so the stock photo shows through elsewhere.
        """
        x0 = max(0, text_area_x - 26)
        x1 = min(w, text_area_x + text_area_w + 26)
        pw = x1 - x0
        if pw <= 0:
            pw = int(w * 0.6)
        y0 = max(0, (h - panel_h) // 2 - 12)
        y1 = min(h, y0 + panel_h)
        ph = y1 - y0
        img = np.zeros((ph, pw, 4), dtype=np.float32)
        # Vertical alpha: dark in middle, fading to transparent at both ends.
        t = np.linspace(0.0, 1.0, ph)
        amp = 1.0 - np.abs((t - 0.5) * 2.0)
        amp = np.clip(amp * 1.6, 0.0, 1.0)  # stronger center hold
        alpha = (amp * alpha_max)[:, None] * np.ones((1, pw))
        img[..., 3] = alpha
        # Dark blue-grey base with zero outer alpha.
        img[..., 0] = 6
        img[..., 1] = 8
        img[..., 2] = 16
        out = np.zeros((h, w, 4), dtype=np.uint8)
        out[y0:y1, x0:x1] = np.clip(img, 0, 255).astype(np.uint8)
        return out

    def _load_presenter_avatar(self) -> Optional[Image.Image]:
        """Load a presenter avatar from file path or data URI, if configured."""
        path = (self.config.presenter_avatar_path or "").strip()
        data = (self.config.presenter_avatar_data or "").strip()
        try:
            if path and os.path.exists(path):
                return Image.open(path).convert("RGBA")
            if data.startswith("data:image") and "," in data:
                payload = data.split(",", 1)[1]
                raw = base64.b64decode(payload)
                return Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception as e:
            print(f"  Presenter avatar load failed: {e}")
        return None

    def generate_video(self, scenes: List[Scene], output_path: str) -> str:
        print(f"Generating video with {len(scenes)} scenes...")
        # Propagate character_enabled config into each Scene's show_character
        # so the render code (which checks scene.show_character) respects the
        # workflow-level character on/off toggle.
        char_on = getattr(self.config, "character_enabled", True)
        for scene in scenes:
            if char_on:
                scene.show_character = True
            else:
                scene.show_character = False
        clips = []
        self._scene_texts = []
        self._cue_specs = []
        self.chapters = []
        self.last_srt_path = None

        for i, scene in enumerate(scenes):
            print(f"  Scene {i + 1}/{len(scenes)}: {scene.text[:40]}...")
            voiceover_text = scene.voiceover or scene.text
            print(f"    [TTS] Generating narration for scene {i + 1}...")
            audio_path = self.voice_gen.generate(voiceover_text, i)
            with AudioFileClip(audio_path) as aac:
                speech_dur = aac.duration
            print(f"    [TTS] Audio file created: {os.path.basename(audio_path)}")
            print(f"    [TTS] Actual audio duration: {speech_dur:.2f} sec")
            clip = self._create_scene_clip(scene, audio_path, i, len(scenes))
            scene_dur = clip.duration - 1.0  # overlap consumes 1.0s per boundary
            print(f"    [SCENE] Scene {i + 1} slot: {scene_dur:.2f} sec (narration {speech_dur:.2f} sec, safety margin {self.config.safety_margin:.2f} sec)")
            if scene_dur < speech_dur:
                print(f"    [WARNING] Narration exceeds scene duration. [FIX] Extending scene duration to match narration.")
            clips.append(clip)
            self._scene_texts.append((scene.voiceover or scene.text).strip())

        print("Concatenating scenes with crossfade...")
        final_video = self._concatenate_with_crossfade(clips)

        # Build chapter map + SRT transcript sidecar from the real audio cue
        # times (used for YouTube captions and description timestamps).
        texts = self._scene_texts
        cues = self._cue_specs
        if cues:
            self.chapters = [
                (int(start), texts[i][:60] if i < len(texts) else "")
                for i, (start, _dur) in enumerate(cues)
            ]
            if output_path and output_path.lower().endswith(".mp4"):
                srt_path = os.path.splitext(output_path)[0] + ".srt"
                try:
                    self._write_srt(srt_path, cues, texts)
                    self.last_srt_path = srt_path
                    print(f"  [SRT] Transcript written: {os.path.basename(srt_path)}"
                          f" ({len(cues)} cues)")
                except Exception as e:
                    print(f"  [SRT] Could not write captions ({e})")

        # Only add background music if explicitly configured
        if self.config.background_music and os.path.exists(self.config.background_music):
            final_video = self._add_background_music(final_video)

        print(f"Exporting: {output_path} ({final_video.duration:.1f}s @ {self.config.fps}fps, {self.config.width}x{self.config.height})")
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        temp_path = output_path + ".part.mp4"
        try:
            MAX_BYTES = 50 * 1024 * 1024
            AUDIO_BITRATE = 128
            duration = final_video.duration
            fps = self.config.fps
            w, h = self.config.width, self.config.height
            pixels_per_frame = w * h
            # Target total bitrate: (50MB * 8 * 1000) / duration
            target_total_kbps = (MAX_BYTES * 8) / duration / 1000
            video_bitrate_kbps = max(200, target_total_kbps - AUDIO_BITRATE)
            # Conservative CRF: use higher CRF for safety margin
            if pixels_per_frame > 600000:
                crf = 28
                preset = "medium"
            else:
                crf = 30
                preset = "medium"
            # Hard bitrate cap via maxrate (1.5x average for VBR headroom)
            maxrate_kbps = int(video_bitrate_kbps * 1.5)
            bufsize_kbps = int(video_bitrate_kbps * 2)
            threads = max(2, (os.cpu_count() or 4) - 1)
            ffmpeg_params = [
                "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{bufsize_kbps}k",
            ]
            print(f"  Encoding: CRF={crf}, maxrate={maxrate_kbps}kbps, preset={preset}, target <= {MAX_BYTES//1024//1024}MB")
            final_video.write_videofile(
                temp_path, fps=fps, codec='libx264',
                audio_codec='aac', audio_bitrate='128k',
                preset=preset, threads=threads,
                ffmpeg_params=ffmpeg_params,
                logger="bar",
            )
            actual_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
            if actual_size > MAX_BYTES:
                print(f"  File too large ({actual_size/1024/1024:.1f}MB), re-encoding with higher CRF...")
                os.remove(temp_path)
                crf += 8
                final_video.write_videofile(
                    temp_path, fps=fps, codec='libx264',
                    audio_codec='aac', audio_bitrate='96k',
                    preset=preset, threads=threads,
                    ffmpeg_params=[
                        "-crf", str(crf), "-pix_fmt", "yuv420p",
                        "-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{bufsize_kbps}k",
                    ],
                    logger="bar",
                )
            # Atomically move temp → final
            import shutil
            shutil.move(str(temp_path), output_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        self.voice_gen.cleanup()
        for clip in clips:
            clip.close()
        final_video.close()
        print(f"Done: {output_path}")
        return output_path

    def _concatenate_with_crossfade(self, clips):
        if len(clips) == 0:
            return None
        if len(clips) == 1:
            return clips[0]

        # Build a single continuous audio track by concatenating trimmed
        # TTS audio sequentially with short natural pauses between scenes.
        # Uses concatenate_audioclips (not CompositeAudioClip+with_start)
        # because MoviePy 2.1.2 does not reflect with_start() placement in
        # get_frame() for CompositeAudioClip - confirmed quirk.
        PAUSE_BETWEEN_SCENES = 0.20  # natural pause between scenes (configurable)
        LEAD_TRIM = 0.12  # trim up to 120ms of leading silence from edge-tts

        # Extract and trim each clip's audio.
        trimmed_audios = []
        for clip in clips:
            src = clip.audio
            if src is None or src.duration <= 0:
                continue
            # Trim leading + trailing silence from edge-tts output.
            # Note: max_trim caps BOTH leading and trailing trim equally.
            # Since _create_scene_clip sizes the video slot for the RAW
            # audio duration + SAFETY_MARGIN, and we trim both ends here,
            # the trimmed audio is shorter than the slot. We compensate by
            # NOT trimming trailing silence - only trim leading (edge-tts
            # warmup artifact). Trailing TTS silence provides natural scene
            # separation. To trim both ends, increase SAFETY_MARGIN in
            # _create_scene_clip to match the expected trim amount.
            src = _trim_silence_both_ends(src, max_trim=LEAD_TRIM)
            trimmed_audios.append(src)

        # Record where each narration cue actually lands on the composed
        # audio timeline (scene order matches clips order).
        cue_specs = []
        t_cursor = 0.0
        for i, aud in enumerate(trimmed_audios):
            cue_specs.append((t_cursor, aud.duration))
            t_cursor += aud.duration + PAUSE_BETWEEN_SCENES
        self._cue_specs = cue_specs

        if not trimmed_audios:
            composed = concatenate_videoclips(clips, method="compose", padding=-1.0)
            return composed

        # Build audio: [clip0, pause, clip1, pause, ..., clipN]
        parts = []
        for i, aud in enumerate(trimmed_audios):
            parts.append(aud)
            if i < len(trimmed_audios) - 1:
                parts.append(AudioClip(
                    lambda t: np.zeros(1), duration=PAUSE_BETWEEN_SCENES, fps=44100
                ))

        full_audio = concatenate_audioclips(parts)

        # Compose video with crossfade overlap.
        composed = concatenate_videoclips(clips, method="compose", padding=-1.0)

        # Make sure audio matches video duration exactly.
        # The video is composed with 1.0s overlap per scene boundary.
        # Each scene's duration = speech_dur + SAFETY_MARGIN (0.20s).
        # With n scenes: video_dur ≈ sum(speech_durs) + n*0.20 - (n-1)*1.0
        #                  = sum(speech_durs) + 0.20 - (n-1)*0.80
        # Audio: sum(trimmed_speech_durs) + (n-1)*0.20 pause
        #         ≈ sum(speech_durs) - n*0.12 trim + (n-1)*0.20 pause
        #         ≈ sum(speech_durs) + 0.08 - 0.12*n
        # Audio will typically be slightly SHORTER than video, so pad if needed.
        if full_audio.duration > composed.duration + 0.05:
            full_audio = full_audio.subclipped(0, composed.duration)
        elif full_audio.duration < composed.duration - 0.05:
            rest = composed.duration - full_audio.duration
            if rest > 0.001:
                sil = AudioClip(lambda t: np.zeros(1), duration=rest, fps=44100)
                full_audio = concatenate_audioclips([full_audio, sil])

        # Gentle overall fade in/out.
        if full_audio.duration > 0.3:
            from moviepy.audio.fx import AudioFadeIn, AudioFadeOut
            fade_dur = min(0.15, full_audio.duration * 0.03)
            if fade_dur > 0.01:
                full_audio = full_audio.with_effects([
                    AudioFadeIn(fade_dur),
                    AudioFadeOut(fade_dur),
                ])

        composed = composed.with_audio(full_audio)
        return composed

    @staticmethod
    def _srt_timecode(seconds: float) -> str:
        seconds = max(0.0, seconds)
        ms = int(round((seconds - int(seconds)) * 1000))
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _write_srt(self, srt_path: str, cues, texts) -> str:
        """Write a WebVTT-style .srt transcript from (start, dur) cues and
        per-scene narration text. Returns the output path."""
        lines = []
        for idx, ((start, dur), text) in enumerate(zip(cues, texts), 1):
            end = start + dur
            subtitle = re.sub(r"\s+", " ", text or "").strip()
            if not subtitle:
                subtitle = " "
            lines.append(str(idx))
            lines.append(f"{self._srt_timecode(start)} --> {self._srt_timecode(end)}")
            lines.append(subtitle)
            lines.append("")
        with open(srt_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return srt_path

    def _measure_speech_duration(self, audio_clip, threshold=0.0015, sample_interval=0.01):
        """Measure the actual speech duration of an audio clip by finding the
        first and last non-silent samples. Returns the duration between them,
        or the full duration if no speech is detected.
        """
        if audio_clip.duration < 0.05:
            return audio_clip.duration
        n_samples = int(audio_clip.duration / sample_interval)
        first_v = None
        for i in range(min(n_samples, int(0.30 / sample_interval) + 10)):
            t = i * sample_interval
            if t >= audio_clip.duration:
                break
            frame = audio_clip.get_frame(t)
            rms = np.sqrt(np.mean(frame ** 2))
            if rms > threshold:
                first_v = t
                break
        if first_v is None:
            return audio_clip.duration
        last_v = None
        for i in range(n_samples - 1, -1, -1):
            t = i * sample_interval
            if t < 0:
                break
            if audio_clip.duration - t < 0.01:
                continue
            frame = audio_clip.get_frame(t)
            rms = np.sqrt(np.mean(frame ** 2))
            if rms > threshold:
                last_v = t
                break
        if last_v is None:
            return audio_clip.duration
        return max(0.1, last_v - first_v)

    def _create_scene_clip(self, scene, audio_path, scene_index, total_scenes):
        import lip_sync
        audio = AudioFileClip(audio_path)
        audio_dur = audio.duration
        # Use raw audio duration for clip sizing. The compose overlap
        # (padding=-1.0) consumes 1.0s per scene boundary, so the effective
        # display slot is duration - 1.0 = audio_dur + safety_margin. This
        # guarantees the scene is never shorter than the narration, with a
        # configurable safety tail so the final word is never clipped.
        SAFETY = getattr(self.config, "safety_margin", 0.20)
        duration = audio_dur + SAFETY + 1.0

        cfg = self.config
        h, w = cfg.height, cfg.width
        total_frames = int(duration * cfg.fps)

        tech_bg = TechBackground(w, h)
        stock_provider = getattr(cfg, "stock_background", None)
        use_stock = (
            stock_provider is not None
            and cfg.background_mode != "procedural"
        )
        bg_base = None
        if use_stock:
            try:
                bg_base = stock_provider.get_background(
                    scene.background_prompt or scene.text,
                    scene_index,
                    scene.visual_style,
                )
            except Exception:
                bg_base = None
            if bg_base is not None and (bg_base.shape[1] != w or bg_base.shape[0] != h):
                bg_base = np.array(Image.fromarray(bg_base).resize((w, h), Image.LANCZOS))
            if bg_base is not None:
                print(f"  [stock] scene {scene_index + 1}: real background")
        if bg_base is None:
            bg_base = tech_bg.generate(scene.background_prompt, scene_index, scene.visual_style)
        particles = Particles(w, h, count=12 if use_stock else 35)

        # ── Text-to-image visual (optional) ──────────────────────────────
        # Generates a structured animated visual (infographic / flowchart /
        # comparison / stats...) from the scene content and syncs it with the
        # narration.  The visual is composited as the primary content layer;
        # the scene's own text/bullets are suppressed so nothing duplicates.
        visual_render = None
        visual_opacity = getattr(cfg, "visualizer_opacity", 0.85)
        if HAS_VISUALIZER and getattr(cfg, "visualizer_enabled", False):
            try:
                visual_render = build_scene_visual(
                    width=w, height=h,
                    scene_text=scene.text,
                    bullet_points=scene.bullet_points,
                    duration=duration,
                    scene_animation=scene.animation,
                    scene_index=scene_index,
                    flags={"accent": scene.text_color or "#00E5FF"},
                    font_path=getattr(cfg, "visualizer_font_path",
                                      "C:/Windows/Fonts/comic.ttf"),
                    font_size=getattr(cfg, "visualizer_font_size", 24),
                )
                if visual_render is not None:
                    print(f"  [visualizer] animated visual active (opacity={visual_opacity:.2f})")
            except Exception as exc:
                print(f"  [visualizer] skipped: {exc}")
                visual_render = None
        vis_active = visual_render is not None

        # Floating icon overlay that orbits the character position.
        icon_names = ["brain", "robot", "gear", "lightbulb", "puzzle", "database"]
        icon_overlay = FloatingIconOverlay(
            w, h, icon_names=icon_names, icon_size=72, count=10,
            rng_seed=hash(scene.background_prompt) + scene_index * 1000,
        )

        info_bar_base = self.text_renderer.render_info_bar(
            scene.title_text or scene.text[:60], scene_index + 1, total_scenes, 0.0
        )
        info_bar_full = self.text_renderer.render_info_bar(
            scene.title_text or scene.text[:60], scene_index + 1, total_scenes, 1.0
        )

        char_w_real, char_h_real = self.char_renderer.width, self.char_renderer.height
        if not scene.show_character:
            char_w_real, char_h_real = 0, 0

        has_bullets = scene.bullet_points and len(scene.bullet_points) > 0

        # Per-frame lip-sync shapes derived from voiceover text.
        lip_table = lip_sync.synthesize(
            scene.voiceover or scene.text,
            total_frames=total_frames,
            fps=cfg.fps,
            emotion="confident",
        )
        lip_lookup = {idx: shapes for idx, _, shapes in lip_table}

        def _get_lip_shapes(idx):
            return lip_lookup.get(idx, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        text_position = scene.text_position
        text_font_size = scene.font_size

        # Long centered titles collide with the presenter. Move them to the top
        # and slightly shrink the font so the character can sit underneath.
        long_title = len(scene.text) > 34 or (scene.title_text and len(scene.title_text) > 34)
        avatar_mode = self.presenter_avatar is not None
        if scene.character_position == "left":
            text_area_w = int(w * 0.50)
            text_x_offset = int(w * 0.42)
        elif scene.character_position == "center":
            if long_title or avatar_mode:
                text_area_w = int(w * 0.82)
                text_x_offset = int(w * 0.09)
                text_position = "top"
                text_font_size = min(scene.font_size, 62)
            else:
                text_area_w = int(w * 0.70)
                text_x_offset = int(w * 0.15)
        else:
            text_area_w = int(w * 0.50) if scene.show_character else int(w * 0.80)
            text_x_offset = 0

        # A technical diagram gets the lower band of the frame; the heading
        # moves to the top band so the two never overlap.
        has_diagram = bool(scene.diagram_type and scene.diagram_data)
        if has_diagram:
            text_position = "top"
            text_font_size = min(text_font_size, 50)
            text_area_w = int(w * 0.86)
            text_x_offset = int(w * 0.07)
            if scene.character_position == "center":
                # Keep the presenter away from the centered diagram zone.
                scene.character_position = "right"

        final_text_frame = self.text_renderer.render_text_frame(
            text=scene.text, font_size=text_font_size,
            text_color=scene.text_color, position=text_position,
            shadow=True, animation=scene.animation,
            anim_progress=1.0, text_area_w=text_area_w,
            text_area_x=text_x_offset,
            outline=scene.outline, outline_color=scene.outline_color,
            outline_width=scene.outline_width,
            glow=scene.glow, glow_color=scene.glow_color,
            glow_width=scene.glow_width, glow_intensity=scene.glow_intensity,
        )

        title_bottom_y = 0
        title_alpha_rows = np.where(final_text_frame[:, :, 3] > 0)[0]
        if len(title_alpha_rows) > 0:
            title_bottom_y = int(title_alpha_rows.max())
            text_top_y = int(title_alpha_rows.min())
        else:
            text_top_y = 0

        # Semantic icon above the heading on non-diagram scenes adds a quick
        # visual cue about the point being discussed.
        heading_icon = None
        if not has_diagram and text_top_y > 64:
            heading_icon = self._heading_icon_image(
                scene.text, icon_size=44 if scene.character_position == "center" else 40)

        # Floating icons overlay around the character.
        icon_names = ["brain", "robot", "gear", "lightbulb", "puzzle", "database"]
        floating_icons = FloatingIcons(w, h, icon_size=72, count=10)

        bullet_frames = []
        if has_bullets and not has_diagram:
            # Spread bullets across the spoken time, not the padded duration.
            bullet_interval = audio_dur / max(1, len(scene.bullet_points)) * 0.7
            bullet_interval = max(0.6, min(bullet_interval, 2.2))
            for bi, bp in enumerate(scene.bullet_points):
                bullet_t = 0.9 + bi * bullet_interval
                bf = self.text_renderer.render_bullet_point(
                    bp, bi + 1, scene.font_size - 10,
                    scene.text_color, text_area_w
                )
                bullet_frames.append((bullet_t, bf))

        text_frame_cache = {}
        anim_duration = 0.8 if scene.animation == "fade" else 1.2

        def _get_text_frame(t):
            if t >= anim_duration:
                return final_text_frame
            if t not in text_frame_cache:
                anim_p = min(t / anim_duration, 1.0)
                text_frame_cache[t] = self.text_renderer.render_text_frame(
                    text=scene.text, font_size=text_font_size,
                    text_color=scene.text_color, position=text_position,
                    shadow=True, animation=scene.animation,
                    anim_progress=anim_p, text_area_w=text_area_w,
                    text_area_x=text_x_offset,
                    outline=scene.outline, outline_color=scene.outline_color,
                    outline_width=scene.outline_width,
                    glow=scene.glow, glow_color=scene.glow_color,
                    glow_width=scene.glow_width, glow_intensity=scene.glow_intensity,
                )
            return text_frame_cache[t]

        overlay_f = scene.overlay_opacity
        is_stock = bg_base is not None and hasattr(cfg, "stock_background") and use_stock
        # Stock photos are already bright: keep them bright (slight veil only)
        # and rely on the localized text panel for legibility. Procedural
        # backgrounds keep the heavier overlay for the neon look.
        blend_weight = 0.90 if is_stock else (1.0 - overlay_f)
        bg_blended = np.clip(bg_base.astype(np.float32) * blend_weight, 0, 255).astype(np.uint8)

        # Localized dark gradient panel behind the text area so the (now
        # brighter) background photo stays visible while text stays readable.
        panel = None
        if is_stock:
            panel = self._text_panel(text_x_offset, text_area_w, h, w)
        bar_y = h - 45

        enter_dur = 0.6
        exit_start = duration - 0.6

        def _get_char_pos(t):
            if not scene.show_character:
                return None, None, 1.0

            char_x = w - char_w_real - 30
            char_y = h - char_h_real - 55
            alpha = 1.0

            if scene.character_position == "left":
                char_x = 30
            elif scene.character_position == "center":
                char_x = (w - char_w_real) // 2
                char_y = h - char_h_real - 30

            if scene.enter_from == "left" or (scene_index == 0 and scene.enter_from is None):
                if t < enter_dur:
                    progress = t / enter_dur
                    ease = progress * progress * (3.0 - 2.0 * progress)
                    char_x = int(-char_w_real + (char_x + char_w_real) * ease)
                    alpha = ease
            elif scene.enter_from == "right":
                if t < enter_dur:
                    progress = t / enter_dur
                    ease = progress * progress * (3.0 - 2.0 * progress)
                    char_x = int(w + char_w_real - (w + char_w_real - char_x) * ease)
                    alpha = ease

            if scene.exit_to == "left" and t > exit_start:
                progress = (t - exit_start) / 0.6
                ease = progress * progress * (3.0 - 2.0 * progress)
                char_x = int(char_x - (char_x + char_w_real) * ease)
                alpha = max(0.0, 1.0 - ease)
            elif scene.exit_to == "right" and t > exit_start:
                progress = (t - exit_start) / 0.6
                ease = progress * progress * (3.0 - 2.0 * progress)
                char_x = int(char_x + (w - char_x) * ease)
                alpha = max(0.0, 1.0 - ease)

            return char_x, char_y, alpha

        def make_frame(t):
            idx = min(int(t * cfg.fps), total_frames - 1)
            frame = bg_blended.copy()
            particles.render_to(frame, t)

            # ── Animated text-to-image visual as the primary content layer ──
            if vis_active and visual_render is not None:
                try:
                    vis_arr = visual_render(t)
                    if vis_opacity < 1.0 and vis_arr.shape[:2] == (h, w):
                        vis_arr = vis_arr.copy()
                        # Blend toward the background using the overlay alpha.
                        vis_arr[:, :, 3] = (vis_arr[:, :, 3].astype(np.float32)
                                            * vis_opacity).astype(np.uint8)
                    _alpha_composite(frame, vis_arr, 0, 0)
                except Exception:
                    pass

            # Localized dark gradient behind text so the bright stock photo
            # stays visible while captions remain readable.  Not needed
            # when a generated visual is active (the visual has its own bg).
            if panel is not None and not vis_active:
                _alpha_composite(frame, panel, 0, 0)

            # Floating icons orbiting the character position (if character is visible).
            cx_pos, cy_pos, _ = _get_char_pos(t)
            if cx_pos is not None and floating_icons is not None:
                floating_icons.render_to(frame, t, cx_pos + char_w_real // 2, cy_pos + char_h_real // 2)

            # Scene's own heading text is suppressed when a generated visual
            # is active, since the visual already contains its own layout.
            if not vis_active:
                text_frame = _get_text_frame(t)
                _alpha_composite(frame, text_frame, 0, 0)

            # Semantic heading icon fades in just above the heading line.
            if heading_icon is not None and not vis_active:
                h_alpha = min(1.0, t / 0.5) if t < 0.5 else 1.0
                if h_alpha > 0.02:
                    hico = heading_icon
                    if h_alpha < 1.0:
                        hico = heading_icon.copy()
                        hico[:, :, 3] = (hico[:, :, 3] * h_alpha).astype(np.uint8)
                    _alpha_composite(frame, hico, 0, max(0, text_top_y - 56))

            if has_bullets and not vis_active:
                visible_bullets = [(bt, bf) for bt, bf in bullet_frames if t >= bt]
                if visible_bullets:
                    bullet_y_start = max(title_bottom_y + 28, int(h * 0.42))
                    bullet_x = text_x_offset + 20
                    for bi, (bt, bf) in enumerate(visible_bullets):
                        bullet_alpha = min(1.0, (t - bt) / 0.4)
                        bullet_img = bf.copy()
                        if bullet_alpha < 1.0:
                            bullet_img[:, :, 3] = (bullet_img[:, :, 3] * bullet_alpha).astype(np.uint8)
                        by = bullet_y_start + sum(prev_bf.shape[0] + 12 for _, prev_bf in visible_bullets[:bi])
                        if by + bf.shape[0] <= h - 55:
                            _alpha_composite(frame, bullet_img, bullet_x, by)

            # Info bar with progress
            bar_progress = t / duration
            if bar_progress < 0.1:
                bar_alpha = bar_progress / 0.1
                current_bar = info_bar_base.copy()
                current_bar[:, :, 3] = (current_bar[:, :, 3] * bar_alpha).astype(np.uint8)
            else:
                current_bar = info_bar_full
            _alpha_composite(frame, current_bar, 0, bar_y)

            # Technical diagram overlay — confined to the lower band below the
            # heading so it never collides with the on-screen text.  Skipped
            # when a generated visual is active (the visual owns the layout).
            if scene.diagram_type and scene.diagram_data and not vis_active:
                diagram_img = self.diagram_renderer.render(scene.diagram_type, scene.diagram_data, t)
                diagram_img = self.diagram_renderer.position_diagram(
                    diagram_img, zone_top=int(h * 0.42), zone_bottom=h - 65)
                diag_alpha = min(1.0, t / 0.6) if t < 0.6 else 1.0
                diag_arr = np.array(diagram_img)
                if diag_alpha < 1.0:
                    diag_arr = diag_arr.copy()
                    diag_arr[:, :, 3] = (diag_arr[:, :, 3] * diag_alpha).astype(np.uint8)
                _alpha_composite(frame, diag_arr, 0, 0)

            if scene.show_character:
                cx, cy, c_alpha = _get_char_pos(t)
                if cx is not None and c_alpha > 0.01:
                    # Per-frame lip-sync shapes from voiceover timing.
                    shapes = _get_lip_shapes(idx)
                    char_img = np.array(self.char_renderer.render_frame_index(
                        i=idx,
                        fps=cfg.fps,
                        pose_name=scene.character_gesture,
                        pose=None,
                        pose_progress=1.0,
                        gesture_progress=min(1.0, t / duration * 1.2),
                        emotion="confident",
                        mouth_shapes=shapes,
                        gaze_x=0.0,
                        gaze_y=0.0,
                        blink_state=False,
                        blink_intensity=0.0,
                        pupil_dilate=0.0,
                        in_walk=False,
                        walk_progress=0.0,
                        orientation=scene.character_position,
                        avatar_image=self.presenter_avatar,
                    ))
                    if c_alpha < 1.0:
                        char_img = char_img.copy()
                        char_img[:, :, 3] = (char_img[:, :, 3] * c_alpha).astype(np.uint8)
                    _alpha_composite(frame, char_img, cx, cy)

            if scene_index > 0 and t < cfg.transition_duration:
                alpha = t / cfg.transition_duration
                ease = alpha * alpha * (3.0 - 2.0 * alpha)
                frame = (frame * ease).astype(np.uint8)
            elif scene_index > 0 and t > duration - cfg.transition_duration:
                alpha = (duration - t) / cfg.transition_duration
                ease = alpha * alpha * (3.0 - 2.0 * alpha)
                frame = (frame * ease).astype(np.uint8)

            # ── Cinematic polish ──────────────────────────────────────────
            # 1. Subtle film grain (adds texture, hides procedural look)
            rng = np.random.default_rng(hash(scene_index * 1000 + int(t * 30)))
            grain = rng.normal(0, 4.0, (h, w, 1)).astype(np.float32)
            frame_f = frame.astype(np.float32) + grain
            frame_f = np.clip(frame_f, 0, 255).astype(np.uint8)
            frame = frame_f

            # 2. Vignette (darker edges → focuses attention on center,
            #    hides procedural background seams at frame borders)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            cx_v, cy_v = w * 0.5, h * 0.5
            dist = np.sqrt((xx - cx_v) ** 2 + (yy - cy_v) ** 2)
            max_dist = np.sqrt(cx_v ** 2 + cy_v ** 2)
            vignette = 1.0 - 0.28 * (dist / max_dist) ** 1.6
            vignette = np.clip(vignette, 0.0, 1.0)[:, :, None]
            frame_f = frame.astype(np.float32) * vignette
            frame = np.clip(frame_f, 0, 255).astype(np.uint8)

            # 3. Slight contrast + saturation boost (filmic look)
            fmean = frame.astype(np.float32).mean(axis=(0, 1), keepdims=True)
            frame_f = (frame.astype(np.float32) - fmean) * 1.08 + fmean
            # Simple saturation nudge via HSV-ish: boost chroma
            r, g, b = frame_f[..., 0], frame_f[..., 1], frame_f[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            frame_f[..., 0] = lum + (r - lum) * 1.06
            frame_f[..., 1] = lum + (g - lum) * 1.06
            frame_f[..., 2] = lum + (b - lum) * 1.06
            frame = np.clip(frame_f, 0, 255).astype(np.uint8)
            # ── End cinematic polish ─────────────────────────────────────

            return frame.astype(np.uint8)

        clip = VideoClip(make_frame, duration=duration).with_fps(cfg.fps)
        clip = clip.with_audio(audio)
        return clip

    def _add_background_music(self, video_clip):
        music = AudioFileClip(self.config.background_music)
        if music.duration < video_clip.duration:
            loops_needed = int(video_clip.duration / music.duration) + 1
            music = concatenate_audioclips([music] * loops_needed)
        music = music.subclipped(0, video_clip.duration)
        music = music.with_volume_scaled(self.config.music_volume)
        final_audio = CompositeAudioClip([video_clip.audio, music])
        return video_clip.with_audio(final_audio)


def _trim_silence_both_ends(audio_clip, max_trim=0.20, threshold=0.0015, sample_interval=0.01):
    """Trim near-silence from the LEADING end of an audio clip.

    The trailing end is NOT trimmed - TTS trailing silence is natural and
    provides a clean speech ending. Only edge-tts startup silence (~100-150ms)
    is removed from the beginning.

    Args:
        audio_clip: The audio clip to trim.
        max_trim: Maximum seconds to trim from the LEADING end (default 0.20).
        threshold: RMS below this is considered silence.
        sample_interval: Time between samples when scanning for voice.
    """
    if audio_clip.duration < 0.05:
        return audio_clip

    n_samples = int(audio_clip.duration / sample_interval)

    first_voice = None
    for i in range(min(n_samples, int(max_trim / sample_interval) + 10)):
        t = i * sample_interval
        if t >= audio_clip.duration:
            break
        frame = audio_clip.get_frame(t)
        rms = np.sqrt(np.mean(frame ** 2))
        if rms > threshold:
            first_voice = t
            break

    trim_start = first_voice if first_voice is not None and first_voice > 0.01 else 0.0
    # Cap leading trim at max_trim
    trim_start = min(trim_start, max_trim)

    last_voice = None
    for i in range(n_samples - 1, -1, -1):
        t = i * sample_interval
        if t < 0:
            break
        if audio_clip.duration - t < 0.01:
            continue
        frame = audio_clip.get_frame(t)
        rms = np.sqrt(np.mean(frame ** 2))
        if rms > threshold:
            last_voice = t
            break

    trim_end = last_voice if last_voice is not None else audio_clip.duration
    # Cap trailing trim at max_trim from the end
    max_trim_end = audio_clip.duration - max_trim
    if trim_end < max_trim_end:
        trim_end = max_trim_end
    if audio_clip.duration - trim_end < 0.01:
        trim_end = audio_clip.duration

    new_dur = trim_end - trim_start
    if new_dur < 0.05:
        return audio_clip
    if trim_start > 0.001 or (audio_clip.duration - trim_end) > 0.001:
        return audio_clip.subclipped(trim_start, trim_end)
    return audio_clip


def create_sample_script() -> List[Scene]:
    return [
        Scene(text="Introduction to Technical Analysis",
              voiceover="Welcome to our guide on technical analysis for trading.",
              duration=4.0, font_size=72, text_color="#FFD700", animation="fade"),
        Scene(text="What is Technical Analysis?",
              voiceover="Technical analysis is the study of price movements and trading volume to predict future price directions.",
              duration=6.0, font_size=64, text_color="#FFFFFF", animation="fade"),
        Scene(text="Key Patterns to Know",
              voiceover="Let's explore the most important chart patterns every trader should know.",
              duration=5.0, font_size=68, text_color="#00FF88", animation="fade"),
        Scene(text="Support and Resistance Levels",
              voiceover="Support and resistance levels are fundamental concepts in technical analysis.",
              duration=6.0, font_size=64, text_color="#FFFFFF", animation="fade"),
        Scene(text="Start Trading with Confidence",
              voiceover="Now you have the basics of technical analysis. Start practicing today!",
              duration=5.0, font_size=72, text_color="#FFD700", animation="fade"),
    ]


if __name__ == "__main__":
    config = VideoConfig()
    generator = CinematicVideoGenerator(config)
    scenes = create_sample_script()
    output = generator.generate_video(scenes, "output/test_video.mp4")
    print(f"\nTest video: {output}")
