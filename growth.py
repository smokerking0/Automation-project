"""
Video Growth Boost — cold-open hook, mid-CTA, recap, scene variation.

These boosts improve watch-time, engagement signals, and the YouTube
algorithm's ranking factors.  Applied by both the CLI pipeline
(workflow._add_brand_scenes) and the GUI (gui._generate).
"""

from typing import List, Optional, Callable

from video import Scene

# ---------------------------------------------------------------------------
# Hook templates  (cold-open, before the brand greeting)
# ---------------------------------------------------------------------------
_HOOK_TEMPLATES = [
    "In 2026, {topic} is changing the game — and most people are missing it.",
    "Struggling with {topic}? By the end of this video, you won't be.",
    "If you've seen the hype around {topic}, here's what's actually true.",
    "The shortcut most people never find for {topic} — explained in minutes.",
    "Here's the one thing I wish I knew before learning {topic}.",
    "What nobody tells you about {topic} — until now.",
    "{topic}: what works, what doesn't, and why it matters in 2026.",
]

# ---------------------------------------------------------------------------
# Mid-CTA & recap copy
# ---------------------------------------------------------------------------
_MID_CTA_BODY_EN = (
    "If this video is helping you, take two seconds to hit like and subscribe. "
    "Coming up next, we wrap up everything you need to know."
)
_RECAP_HEADING_EN = "Key Takeaways"
_RECAP_BODY_EN = (
    "Here's the short version: {topic} is simpler than most people think. "
    "Master the fundamentals, stay consistent, and the rest follows."
)

_VARIATION_STYLES = [
    "educational_infographic",
    "cinematic_tech",
    "cinematic_realistic",
]

# Deterministic pick from a list based on text hash.
def _pick(text: str, pool: list) -> int:
    h = 0
    for ch in (text or ""):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % len(pool)


def _translate(text: str, lang: str, translate_fn: Optional[Callable]) -> str:
    """Translate *text* to *lang* using *translate_fn*, falling back to English."""
    if lang == "en" or not translate_fn or not text:
        return text
    try:
        return translate_fn(text, lang)
    except Exception:
        return text


# ---------------------------------------------------------------------------
# Scene builders
# ---------------------------------------------------------------------------

def build_hook(title: str, field: str = "", lang: str = "en",
               translate: Optional[Callable] = None) -> Scene:
    """Cold-open hook scene shown BEFORE the brand greeting."""
    topic = (title or "this topic").strip()
    tpl = _HOOK_TEMPLATES[_pick(topic + field, _HOOK_TEMPLATES)]
    vo = tpl.replace("{topic}", topic)
    vo = _translate(vo, lang, translate)
    heading = f"The Truth About {topic[:36]}"
    return Scene(
        text=heading,
        title_text=heading,
        voiceover=vo,
        visual_style="cinematic_tech",
        text_color="#00E5FF",
        show_character=False,
        animation="slide_up",
        background_prompt=f"dramatic tech intro {topic}",
    )


def build_mid_cta(topic: str, lang: str = "en",
                  translate: Optional[Callable] = None) -> Scene:
    """Mid-video engagement CTA — placed at ~50 % of content."""
    vo = _translate(_MID_CTA_BODY_EN, lang, translate)
    heading = "Like & Subscribe"
    return Scene(
        text=heading,
        title_text=heading,
        voiceover=vo,
        visual_style="educational_infographic",
        text_color="#FFD700",
        show_character=False,
        animation="fade",
        background_prompt="subscribe like comment",
    )


def build_recap(topic: str, scene_titles: List[str],
                lang: str = "en",
                translate: Optional[Callable] = None) -> Scene:
    """Key-takeaways recap placed right before the outro."""
    topic_label = (topic or "this topic").strip()
    vo = _RECAP_BODY_EN.replace("{topic}", topic_label)
    vo = _translate(vo, lang, translate)
    bullets = []
    for t in scene_titles[:6]:
        short = (t or "").strip()
        if short and len(short) > 70:
            short = short[:67] + "..."
        if short:
            bullets.append(short)
    return Scene(
        text=_RECAP_HEADING_EN,
        title_text=_RECAP_HEADING_EN,
        voiceover=vo,
        visual_style="educational_infographic",
        text_color="#FFFFFF",
        show_character=False,
        animation="fade",
        bullet_points=bullets or ["Review the key points above"],
        background_prompt="summary key takeaways tech",
    )


# ---------------------------------------------------------------------------
# Scene variation pass — pattern interrupt for retention
# ---------------------------------------------------------------------------

def _vary_content(scenes: List[Scene]) -> None:
    """Ensure consecutive content scenes differ in at least one visual attribute.

    The YouTube algorithm rewards average view duration.  Visual monotony
    (same style, same pose, same animation) makes viewers skip.  This pass
    deterministically rotates *visual_style*, *character_position*, and
    *animation* so every adjacent pair feels fresh.
    """
    for i in range(1, len(scenes)):
        prev = scenes[i - 1]
        cur = scenes[i]
        if cur.visual_style == prev.visual_style:
            idx = _pick(cur.text, _VARIATION_STYLES)
            cur.visual_style = _VARIATION_STYLES[(idx + 1) % len(_VARIATION_STYLES)]
        if cur.character_position == prev.character_position:
            cur.character_position = "left" if prev.character_position != "left" else "right"
        if cur.animation == prev.animation:
            cur.animation = "slide_up" if prev.animation == "fade" else "fade"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_growth(scenes: List[Scene], topic: str, lang: str = "en",
                 field: str = "",
                 translate: Optional[Callable] = None) -> List[Scene]:
    """Inject growth-boost scenes into *scenes* (mutates in place).

    Ordering after this call::

        [hook, <original scenes>, mid-CTA, recap]

    The caller (workflow._add_brand_scenes) then wraps with::

        [intro, hook, <original scenes>, mid-CTA, recap, outro]

    Returns the mutated list for convenience.
    """
    if len(scenes) < 3:
        return scenes

    # 1. Collect content scene titles BEFORE inserting anything new.
    content_titles = [sc.text for sc in scenes if (sc.text or "").strip()]

    # 2. Variation pass on original content scenes (skip if only brand scenes).
    _vary_content(scenes)

    # 3. Mid-CTA — insert at the halfway point.
    mid = max(1, len(scenes) // 2)
    scenes.insert(mid, build_mid_cta(topic, lang, translate))

    # 4. Key-takeaways recap — insert before the last scene (which is
    #    typically the topics.py outro scene that will be replaced by the
    #    brand outro later).  This keeps the order: content, recap, outro.
    insert_pos = max(0, len(scenes) - 1)
    scenes.insert(insert_pos, build_recap(topic, content_titles, lang, translate))

    return scenes


def apply_hook(scenes: List[Scene], topic: str, field: str = "",
               lang: str = "en",
               translate: Optional[Callable] = None) -> List[Scene]:
    """Prepend a cold-open hook at index 0.

    Called separately from apply_growth so the CLI can place it BEFORE
    the brand intro, while the GUI can call it without brand scenes.
    """
    hook = build_hook(topic, field, lang, translate)
    scenes.insert(0, hook)
    return scenes
