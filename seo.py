"""
SEO metadata generator for Core Infotech videos.

Builds a keyword-first title (<=60 chars), a search-friendly description
(with bullet points, hashtags and the channel boilerplate), and a tag
list (6-10 tags). Pure functions -- no I/O -- so it is unit-testable and
safe to call at upload time.

The `field` argument re-uses the content-field keyword sets from
youtube_trending.py so titles/descriptions/tags stay consistent with the
category the video was generated from.
"""

import re
from typing import List, Optional

try:
    from youtube_trending import FIELD_KEYWORDS, _kw_match
except ImportError:  # pragma: no cover - degrade gracefully
    FIELD_KEYWORDS = {}
    _kw_match = lambda text, words: False

BRAND = "Core Infotech"
HANDLE = "@CoreInfotech-w4m"
TAGLINE = "trending tech, explained in minutes"

FIX_FIELDS = {"windows", "linux", "networking", "cloud"}
AI_FIELDS = {"ai_ml", "models_agents"}
GUIDE_FIELDS = {"devops", "languages", "containers"}

# One field hashtag set per field (max 3, used in the description).
FIELD_HASHTAGS = {
    "kids": ["#KidsTech", "#STEM"],
    "networking": ["#Networking", "#TechTips"],
    "windows": ["#Windows", "#TechTips"],
    "linux": ["#Linux", "#OpenSource"],
    "cloud": ["#CloudComputing", "#Cloud"],
    "news": ["#TechNews", "#Tech"],
    "ai_ml": ["#AI", "#MachineLearning"],
    "models_agents": ["#AI", "#TechNews"],
    "devops": ["#DevOps", "#Automation"],
    "languages": ["#Programming", "#Coding"],
    "containers": ["#Docker", "#Kubernetes"],
    "gpu": ["#GPU", "#Hardware"],
    "": ["#Tech"],
}

# Field tag seeds used to build a tag list (kept short: full tag list is
# assembled from title tokens + these + generic tags).
FIELD_TAG_SEEDS = {
    "kids": ["kids tech", "stem"],
    "networking": ["networking", "wifi"],
    "windows": ["windows", "pc tips"],
    "linux": ["linux", "open source"],
    "cloud": ["cloud computing", "cloud"],
    "news": ["tech news", "technology"],
    "ai_ml": ["artificial intelligence", "machine learning"],
    "models_agents": ["ai models", "ai agents"],
    "devops": ["devops", "automation"],
    "languages": ["programming languages", "developer"],
    "containers": ["docker", "kubernetes"],
    "gpu": ["gpu", "hardware"],
    "": ["technology"],
}

GENERIC_TAGS = ["tech", "technology", "tech explainer", "core infotech"]

_STOP = {"the", "a", "an", "and", "or", "but", "for", "of", "to", "in",
         "on", "with", "is", "are", "was", "that", "this", "how", "what",
         "why", "your", "you", "it", "at", "by", "be", "up"}

_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F300-\U0001F5FF]"
)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _clip(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit - 3].rstrip() + "..."


def _core(raw: str) -> str:
    """Reduce a raw title to a clean topic phrase (no channel names, pipes)."""
    t = _EMOJI_RE.sub("", raw or "")
    t = re.sub(r"\s*[|\u2014]\s*", " ", t)  # ' | ' / ' — ' separators
    t = re.sub(r"\s+", " ", t).strip(" -:?")
    return t


def build_title(base: str, field: str = "") -> str:
    """Keyword-first SEO title, always <=60 chars, tuned per field."""
    core = _core(base)
    low = core.lower()
    core = core.rstrip("? ").strip()
    cap = _cap(core)
    if not core:
        return "Tech Explained Simply (2026)"

    if field in FIX_FIELDS or low.startswith(("why", "how", "fix", "what to do")):
        t = _clip(cap, 55)
        tail = " (2026 Fixes)"
        if len(t) + len(tail) <= 60:
            t += tail
        return t

    if field == "kids":
        return _clip(f"{cap}: Fun Tech Explained for Kids", 60)

    if field in AI_FIELDS and any(k in low for k in
                                  ("gpt", "agi", "breakthrough", "first look",
                                   "revolution", "aligned", "model")):
        return _clip(f"{cap}: Breakthrough or Hype?", 60)

    if " vs " in low:
        return _clip(f"{cap}: Which Should You Learn?", 60)

    if field == "news":
        return _clip(f"{cap}: What It Means for You", 60)

    if field in GUIDE_FIELDS:
        return _clip(f"{cap} (2026 Guide)", 60)

    if field == "gpu":
        return _clip(f"{cap}: Specs & What to Buy", 60)

    if low.startswith(("what is", "what are", "why is", "how do", "how to",
                       "who is")):
        return _clip(f"{cap} (2026 Guide)", 60)

    return _clip(f"{cap}: What It Is & Why It Matters", 60)


def _tag_tokens(base: str) -> List[str]:
    toks = []
    for w in re.findall(r"[a-zA-Z0-9#+][a-zA-Z0-9#+.-]*", base.lower()):
        w = w.strip(".-")
        if len(w) > 2 and w not in _STOP and w not in toks:
            toks.append(w)
    return toks


def build_tags(base: str, field: str = "") -> List[str]:
    """6-10 tags: title tokens + field seeds + generic."""
    tags = list(FIELD_TAG_SEEDS.get(field, FIELD_TAG_SEEDS[""]))
    for tok in _tag_tokens(base):
        if len(tags) >= 9:
            break
        if tok not in tags:
            tags.append(tok)
    for g in GENERIC_TAGS:
        if len(tags) >= 10:
            break
        if g not in tags:
            tags.append(g)
    return tags[:10]


def build_description(
    title: str,
    points: Optional[List[str]] = None,
    chapters: Optional[List[tuple]] = None,  # [(start_sec:int, label:str)]
    fields_hashtags: Optional[List[str]] = None,
) -> str:
    """150-250 word search-friendly description with bullets + hashtags."""
    base = _core(title)
    lines = []
    lines.append(f"{base} explained simply - what it is, how it works, "
                 f"and what to watch out for.")
    lines.append(f"This is {BRAND} - {TAGLINE}.")
    lines.append("")
    lines.append(f"In this video we cover {base}. We explain the topic in plain "
                 f"language with concrete examples anyone can follow - no hype, "
                 f"just the practical details, costs, limitations, and how to "
                 f"get started.")
    if points:
        lines.append("")
        lines.append("What you will learn:")
        for p in points[:8]:
            bp = re.sub(r"^[-*\u2022\s]+", "", str(p)).strip()
            if bp:
                lines.append(f"  - {bp}")
    if chapters:
        lines.append("")
        lines.append("Timestamps:")
        for sec, label in chapters:
            if sec < 0:
                sec = 0
            hh, mm, ss = sec // 3600, (sec % 3600) // 60, sec % 60
            lines.append(f"  {hh:02d}:{mm:02d}:{ss:02d} {label}")
    lines.append("")
    lines.append("Hashtags: " + " ".join(fields_hashtags or FIELD_HASHTAGS[""]))
    lines.append("")
    lines.append("-" * 20)
    lines.append(f"Welcome to {BRAND} - {TAGLINE}. New videos every week.")
    lines.append(f"YouTube: {HANDLE}")
    lines.append("")
    lines.append("If this video helped, like, comment, and subscribe for the "
                 "latest tech updates and informative videos.")
    return "\n".join(lines)


def build_seo(
    base_title: str,
    field: str = "",
    points: Optional[List[str]] = None,
    chapters: Optional[List[tuple]] = None,
) -> dict:
    """Return {title, description, tags, hashtags} for upload metadata.

    description uses the sanitized SEO title (not the raw base), so the
    first line mirrors the on-platform title for search consistency.
    """
    title = build_title(base_title, field)
    hashtags = list(FIELD_HASHTAGS.get(field, FIELD_HASHTAGS[""]))[:3]
    tags = build_tags(base_title, field)
    description = build_description(title, points=points, chapters=chapters,
                                    fields_hashtags=hashtags)
    return {
        "title": title[:100],
        "description": description[:5000],
        "tags": tags,
        "hashtags": hashtags,
    }