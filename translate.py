"""
Video language helpers.

Supports English (en), Hindi (hi), and Nepali (ne) video output.

* ``translate_text`` / ``translate_script`` translate English script content
  into the target language using the free MyMemory translation API
  (no API key required). When translation fails it degrades gracefully and
  keeps the original English text so the pipeline never crashes.
* ``normalize_lang`` maps any common name/code to a gTTS-compatible code.
"""

import re
import time

import requests

LANG_CODES = {
    "en": "en", "english": "en",
    "hi": "hi", "hindi": "hi",
    "ne": "ne", "nepali": "ne",
}

LANG_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ne": "Nepali",
}

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

_TRANSLATE_CACHE = {}

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_CHUNK = 420  # stay under the free API's per-request size limit

# Primary free endpoint (works without a key). Falls back to MyMemory.
GOOGLE_URL = "https://translate.google.com/translate_a/single"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
}

_LAST_REQUEST_TIME = 0.0
_MIN_REQUEST_GAP = 0.9  # seconds between provider calls (avoid throttling)


def normalize_lang(lang) -> str:
    """Map a user-facing label or ISO code to a TTS language code."""
    if not lang:
        return "en"
    key = str(lang).strip().lower()
    return LANG_CODES.get(key, key if key in ("en", "hi", "ne") else "en")


def lang_label(lang) -> str:
    return LANG_NAMES.get(normalize_lang(lang), normalize_lang(lang))


def is_devanagari(text) -> bool:
    """True when *text* contains Hindi/Nepali script characters."""
    return bool(text and _DEVANAGARI_RE.search(text))


def _chunk_text(text: str, limit: int):
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) + 1 > limit:
            if cur:
                chunks.append(cur)
            cur = ""
            if len(s) > limit:
                for i in range(0, len(s), limit):
                    chunks.append(s[i:i + limit])
                continue
        cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    return chunks


def _throttle():
    """Space out provider calls so the free endpoints don't rate-limit us."""
    global _LAST_REQUEST_TIME
    elapsed = time.monotonic() - _LAST_REQUEST_TIME
    if elapsed < _MIN_REQUEST_GAP:
        time.sleep(_MIN_REQUEST_GAP - elapsed)
    _LAST_REQUEST_TIME = time.monotonic()


def _clean_translation(text: str) -> str:
    """Normalize a translated string for smooth TTS reading."""
    text = re.sub(r"<!--.*?-->", "", text)          # provider placeholder junk
    text = text.replace("\u200b", "").replace("\u200e", "").replace("\u200f", "")
    text = re.sub(r"\s+", " ", text)                # collapse whitespace
    text = text.replace(" ।", "।").replace(" ।", "।")          # Devanagari danda spacing
    text = re.sub(r"\s+([,.;:!?\u0964])", r"\1", text)          # no space before punct
    text = re.sub(r"([,.;:!?\u0964])(?=[\u0900-\u097F])", r"\1 ", text)  # space after punct
    return text.strip()


def _translate_chunk_google(text: str, lang: str, timeout: int = 15):
    _throttle()
    try:
        r = requests.get(
            GOOGLE_URL,
            params={"client": "dict-chrome-ex", "sl": "en", "tl": lang, "dt": "t", "q": text},
            headers=_HEADERS,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        parts = data[0]
        if parts and parts[0]:
            return _clean_translation("".join(seg[0] for seg in parts if seg and seg[0]))
    except Exception:
        pass
    return None


def _translate_chunk_my_memory(text: str, lang: str, timeout: int = 25):
    _throttle()
    try:
        r = requests.get(
            MYMEMORY_URL,
            params={"q": text, "langpair": f"en|{lang}"},
            timeout=timeout,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("responseStatus") == 200:
            tr = (d.get("responseData") or {}).get("translatedText")
            if tr:
                return _clean_translation(tr)
    except Exception:
        pass
    return None


def _translate_chunk(text: str, lang: str, timeout: int = 25, retries: int = 1):
    for attempt in range(retries + 1):
        translated = _translate_chunk_google(text, lang) or _translate_chunk_my_memory(text, lang)
        if translated:
            return translated
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None


def translate_text(text, lang) -> str:
    """Translate *text* to *lang*. English input passes through unchanged."""
    lang = normalize_lang(lang)
    text = (text or "").strip()
    if lang == "en" or not text:
        return text
    cache_key = (text, lang)
    if cache_key in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[cache_key]

    chunks = _chunk_text(text, MYMEMORY_CHUNK)
    translated_parts = []
    for chunk in chunks:
        part = _translate_chunk(chunk, lang)
        if part is None:
            print(f"  [translate] couldn't translate {len(chunks)} chunk(s) to {lang}; keeping English")
            _TRANSLATE_CACHE[cache_key] = text
            return text
        translated_parts.append(part)

    result = "".join(translated_parts).strip()
    _TRANSLATE_CACHE[cache_key] = result
    return result


def translate_voiceover(script, lang):
    """Return a copy of *script* with only the spoken narration translated.

    On-screen text (scene.text, title_text, bullet_points) stays untouched so
    videos keep English titles/captions while the audio speaks *lang*.
    """
    lang = normalize_lang(lang)
    if lang == "en":
        return script

    from dataclasses import replace

    scenes = [
        replace(s, voiceover=translate_text(s.voiceover or s.text, lang))
        for s in script.scenes
    ]
    return replace(script, scenes=scenes)


def translate_script(script, lang):
    """Return a copy of *script* (title/description/scenes) translated to *lang*."""
    lang = normalize_lang(lang)
    if lang == "en":
        return script

    from dataclasses import replace
    from video import Scene

    scenes = []
    for s in script.scenes:
        scenes.append(replace(
            s,
            text=translate_text(s.text, lang),
            title_text=translate_text(s.title_text or s.text, lang) if s.title_text else None,
            voiceover=translate_text(s.voiceover or s.text, lang),
            bullet_points=(
                [translate_text(b, lang) for b in s.bullet_points]
                if s.bullet_points else s.bullet_points
            ),
        ))

    new_title = translate_text(script.title, lang)
    return replace(
        script,
        title=new_title,
        description=translate_text(script.description, lang),
        thumbnail_text=translate_text(script.thumbnail_text or "", lang) or new_title,
        scenes=scenes,
    )