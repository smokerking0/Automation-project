"""
Lip-sync model for the character renderer.

Maps English phonemes (or rough phoneme-like categories) to mouth-shape
weights, plus a simple smoothing/hold model so the mouth doesn't thrash
frame-to-frame. Input is a list of (time_sec, phoneme) segments; output is
a list of (frame_index, mouth_open, mouth_shape) tuples suitable for the
render pipeline.

Devanagari script (Hindi/Nepali narration) is also accepted: characters
are decomposed into consonant + inherent-vowel / matra / virama phones so
translated voiceovers still animate the mouth.

Design notes
------------
- We intentionally keep this lightweight and dependency-free. A real
  production lip-sync pass would parse an audio waveform (or a
  Whisper/aeneas alignment), but for a rendered video pipeline a
  phoneme-segment list produced from the script text is enough to give
  the *look* of phoneme-driven lip shapes rather than a single blink-open.
- Mouth shapes: defined as weights into 6 archetypes: CLOSED, NARROW,
  WIDE_OPEN, ROUND, STRETCH, SMILE. The renderer turns those weights into
  an actual drawing; this module only emits the weights.
- Smoothing: a basic EMA-style hold so shape transitions are eased rather
  than telegraphing every phoneme boundary.
"""

from __future__ import annotations

import math
from typing import List, Tuple

# ---- Archetype weights (one value per archetype per phoneme) ------------

# Order: CLOSED, NARROW, WIDE_OPEN, ROUND, STRETCH, SMILE
# Values are 0..1 and sum can exceed 1 (we treat them as per-archetype
# confidences that the renderer can normalize or blend).
_PHONEME_MAP = {
    # Closed / nasal / plosive-ish
    "p": [0.9, 0.1, 0.0, 0.0, 0.0, 0.0],
    "b": [0.85, 0.15, 0.0, 0.0, 0.0, 0.0],
    "m": [0.95, 0.05, 0.0, 0.0, 0.0, 0.0],
    "t": [0.8, 0.2, 0.0, 0.0, 0.0, 0.0],
    "d": [0.8, 0.2, 0.0, 0.0, 0.0, 0.0],
    "k": [0.7, 0.2, 0.1, 0.0, 0.0, 0.0],
    "g": [0.7, 0.2, 0.1, 0.0, 0.0, 0.0],
    # Fricatives / thin mouth
    "f": [0.3, 0.6, 0.0, 0.1, 0.3, 0.0],
    "v": [0.3, 0.6, 0.0, 0.1, 0.3, 0.0],
    "th": [0.2, 0.7, 0.0, 0.0, 0.2, 0.0],
    "s": [0.2, 0.8, 0.0, 0.0, 0.15, 0.0],
    "z": [0.2, 0.8, 0.0, 0.0, 0.15, 0.0],
    "sh": [0.15, 0.85, 0.0, 0.0, 0.1, 0.0],
    "zh": [0.15, 0.85, 0.0, 0.0, 0.1, 0.0],
    "h": [0.3, 0.3, 0.2, 0.2, 0.1, 0.0],
    # Liquids / glides (relaxed open)
    "l": [0.3, 0.5, 0.2, 0.2, 0.0, 0.1],
    "r": [0.35, 0.55, 0.0, 0.15, 0.15, 0.0],
    "w": [0.15, 0.1, 0.4, 0.7, 0.0, 0.0],
    "y": [0.1, 0.15, 0.3, 0.6, 0.0, 0.0],
    # Vowels (open scale)
    "i": [0.1, 0.2, 0.4, 0.0, 0.6, 0.1],  # /i/ as in "see"
    "ee": [0.1, 0.2, 0.4, 0.0, 0.6, 0.1],
    "ai": [0.2, 0.15, 0.6, 0.0, 0.5, 0.0],  # /aɪ/ "my"
    "ay": [0.2, 0.15, 0.6, 0.0, 0.5, 0.0],
    "e": [0.2, 0.3, 0.3, 0.0, 0.5, 0.1],  # /ɛ/ "bed"
    "eh": [0.2, 0.3, 0.3, 0.0, 0.5, 0.1],
    "a": [0.15, 0.15, 0.7, 0.0, 0.3, 0.05],  # /æ/ "cat"
    "ah": [0.1, 0.1, 0.7, 0.0, 0.2, 0.05],  # /ɑ/ "hot"
    "o": [0.1, 0.1, 0.5, 0.8, 0.1, 0.0],  # /ɔ/ "law"
    "oh": [0.1, 0.1, 0.5, 0.8, 0.1, 0.0],
    "oo": [0.1, 0.05, 0.3, 0.9, 0.0, 0.0],  # /u/ "boot"
    "u": [0.1, 0.05, 0.3, 0.9, 0.0, 0.0],
    "uh": [0.3, 0.3, 0.3, 0.2, 0.1, 0.0],  # /ə/ schwa
    # Affricates
    "ch": [0.3, 0.4, 0.2, 0.0, 0.2, 0.0],
    "j": [0.3, 0.4, 0.2, 0.0, 0.2, 0.0],
    # Smile-adjacent context phoneme
    "smile": [0.15, 0.1, 0.05, 0.0, 0.2, 0.85],
}


def phonemes_from_text(text: str) -> List[Tuple[float, str]]:
    """
    Extremely rough phoneme segmentation from plain text. This is NOT a
    real linguistic tokenizer — it's a screenshot of what a real alignment
    would look like, tuned so the mouth visibly shapes around vowels and
    closes around plosives. Use a real TTS-aligned phoneme stream in
    production; this is for the *look* of lip-sync in the rendered output.

    Supports both Latin script (English) and Devanagari script (Hindi /
    Nepali narration), so translated voiceovers still drive mouth shapes.
    """
    import re

    out: List[Tuple[float, str]] = []
    # Tokenize on whitespace so each "word" is self-contained; per-word
    # phone mapping decides how the characters play out.
    words = re.findall(r"[^\s]+", text)
    approx_dur_per_char = 0.07  # seconds per character — crude
    cursor = 0.0
    for w in words:
        phones = _phones_for_word(w)
        if not phones:
            continue
        # Spread the word's budget across its phones.
        per = max(0.04, len(w) * approx_dur_per_char / len(phones))
        for ph in phones:
            out.append((cursor, ph))
            cursor += per
        # space between words
        cursor += 0.06
    return out


# ---- Devanagari support (Hindi / Nepali narration) -----------------------
# An abugida: a consonant carries an inherent "a" that is replaced by an
# explicit vowel sign (matra) or suppressed by a virama (्). We map each
# grapheme to the existing _PHONEME_MAP keys so the mouth behaves like it
# does for English text, with the inherent-vowel logic applied between
# consonant and vowel-sign characters.

_DEV_VOWEL_SIGNS = {
    0x093E: "ah",  # ा  AA
    0x093F: "i",   # ि  I
    0x0940: "i",   # ी  II
    0x0941: "oo",  # ु  U
    0x0942: "oo",  # ू  UU
    0x0943: "i",   # ृ  VOCALIC R (Hindi "ri")
    0x0944: "i",   # ॄ  VOCALIC RR
    0x0945: "e",   # ॅ  CANDRA E
    0x0946: "e",   # े  E
    0x0947: "ai",  # ै  AI
    0x0948: "o",   # ॉ  CANDRA O
    0x0949: "o",   # ॊ  SHORT O
    0x094A: "o",   # ो  O
    0x094B: "o",   # ौ  AU
}

_DEV_INDEPENDENT_VOWELS = {
    0x0905: "ah",  # अ
    0x0906: "ah",  # आ
    0x0907: "i",   # इ
    0x0908: "i",   # ई
    0x0909: "oo",  # उ
    0x090A: "oo",  # ऊ
    0x090B: "i",   # ऋ
    0x090C: "i",   # ॠ
    0x090E: "e",   # ऎ
    0x090F: "e",   # ए
    0x0910: "ai",  # ऐ
    0x0911: "o",   # ऑ
    0x0913: "o",   # ओ
    0x0914: "o",   # औ
    0x0960: "ah",  # ॠ
    0x0961: "i",   # ॡ
}

_DEV_CONSONANTS = {
    0x0915: "k",    # क
    0x0916: "k",    # ख
    0x0917: "g",    # ग
    0x0918: "g",    # घ
    0x0919: "m",    # ङ (nasal ~ "ng")
    0x091A: "ch",   # च
    0x091B: "s",    # छ (approx)
    0x091C: "j",    # ज
    0x091D: "j",    # झ
    0x091E: "m",    # ञ (nasal)
    0x091F: "t",    # ट
    0x0920: "t",    # ठ
    0x0921: "d",    # ड
    0x0922: "d",    # ढ
    0x0923: "m",    # ण (nasal)
    0x0924: "t",    # त
    0x0925: "th",   # थ
    0x0926: "d",    # द
    0x0927: "d",    # ध
    0x0928: "m",    # न (nasal-ish -> "m" shape, like English n)
    0x092A: "p",    # प
    0x092B: "f",    # फ
    0x092C: "b",    # ब
    0x092D: "b",    # भ
    0x092E: "m",    # म
    0x092F: "y",    # य
    0x0930: "r",    # र
    0x0931: "r",    # ऱ
    0x0932: "l",    # ल
    0x0933: "l",    # ळ
    0x0934: "l",    # ऴ
    0x0935: "w",    # व (v/w)
    0x0936: "sh",   # श
    0x0937: "sh",   # ष
    0x0938: "s",    # स
    0x0939: "h",    # ह
    # Nukta variants (precomposed)
    0x0958: "k",    # क़
    0x0959: "k",    # ख़
    0x095A: "g",    # ग़
    0x095B: "z",    # ज़
    0x095C: "r",    # ड़
    0x095D: "r",    # ढ़
    0x095E: "f",    # फ़
    0x095F: "y",    # य़
}

# Marks that carry sound vs. combine or break.
_DEV_SONIC_MARKS = {
    0x0902: "m",     # ं anusvara (nasalizes, closes mouth)
    0x0903: "h",     # ः visarga (breathy open)
}
_DEV_VIJAMA = 0x094D  # ् virama — suppresses the inherent vowel
_DEV_NUKTA = 0x093C   # ़ nukta — combining, part of the consonant


def _phones_for_word(w: str) -> List[str]:
    """Map one whitespace token to rough phoneme strings (Latin or Devanagari)."""
    phones: List[str] = []
    chars = list(w)
    n = len(chars)
    i = 0
    while i < n:
        ch = chars[i]
        cp = ord(ch)
        # Latin letter (case-insensitive) -> existing letter mapping.
        lc = ch.lower()
        if "a" <= lc <= "z":
            phones.append(_guess_phoneme(lc))
            i += 1
            continue
        if 0x0900 <= cp <= 0x097F:
            # Devanagari syllable components.
            if cp == _DEV_VIJAMA or cp == _DEV_NUKTA:
                i += 1
                continue
            if cp in _DEV_VOWEL_SIGNS:
                phones.append(_DEV_VOWEL_SIGNS[cp])
                i += 1
                continue
            if cp in _DEV_INDEPENDENT_VOWELS:
                phones.append(_DEV_INDEPENDENT_VOWELS[cp])
                i += 1
                continue
            if cp in _DEV_SONIC_MARKS:
                phones.append(_DEV_SONIC_MARKS[cp])
                i += 1
                continue
            if cp in _DEV_CONSONANTS:
                phones.append(_DEV_CONSONANTS[cp])
                # Inherent "a" is spoken unless a matra, virama, or nukta
                # follows (matra supplies its own vowel, virama silences).
                nxt_cp = ord(chars[i + 1]) if i + 1 < n else None
                # An uppercase Latin vowel after a consonant (weird but legal,
                # e.g. "क्याAI") must NOT emit an inherent Devanagari "a".
                nxt_lc = chr(nxt_cp).lower() if nxt_cp is not None else ""
                inherent_suppressed = (
                    nxt_cp in _DEV_VOWEL_SIGNS
                    or nxt_cp == _DEV_VIJAMA
                    or nxt_cp == _DEV_NUKTA
                    or "a" <= nxt_lc <= "z"
                )
                if not inherent_suppressed:
                    phones.append("ah")
                i += 1
                continue
        # Anything else (digits, danda, punctuation, other scripts) -> skip.
        i += 1
    return phones


def _guess_phoneme(ch: str) -> str:
    """Map a single lowercase letter to a rough phoneme string."""
    v = {
        "a": "ah",
        "e": "e",
        "i": "i",
        "o": "o",
        "u": "oo",
        "y": "y",
    }
    c = {
        "b": "b",
        "p": "p",
        "m": "m",
        "t": "t",
        "d": "d",
        "k": "k",
        "g": "g",
        "f": "f",
        "v": "v",
        "s": "s",
        "z": "z",
        "l": "l",
        "r": "r",
        "w": "w",
        "h": "h",
        "n": "m",  # nasal-ish, close enough for visual shape
        "ng": "m",
        "j": "j",
        "c": "k",
        "q": "k",
        "x": "s",
    }
    if ch in v:
        return v[ch]
    if ch in c:
        return c[ch]
    # Multigraphs inside a single-letter tokenizer collapse, but try.
    digraphs = {"th": "th", "sh": "sh", "ch": "ch", "ph": "f"}
    return "ah"  # fallback vowel-ish


def smooth_shapes(
    frames: List[Tuple[int, float, List[float]]],
    *,
    hold_ms: float = 60.0,
    fps: float = 24.0,
    ema_alpha: float = 0.45,
) -> List[Tuple[int, float, List[float]]]:
    """
    Post-process raw per-frame shape weights so mouth shapes don't jitter
    at every phoneme boundary.

    - hold_ms: minimal hold duration after a shape change before a new change
      can start (prevents flicker on fast phoneme runs).
    - ema_alpha: exponential smoothing factor 0..1; lower = smoother.
    """
    if not frames:
        return frames
    hold_frames = max(1, int(round(hold_ms / 1000.0 * fps)))
    smoothed: List[Tuple[int, float, List[float]]] = []
    last_change_frame = -hold_frames
    carry = list(frames[0][2])
    for idx, open_, raw in frames:
        if idx - last_change_frame < hold_frames:
            # Hold the last shape.
            smoothed.append((idx, open_, list(carry)))
            continue
        # EMA blend toward raw.
        blended = [carry[i] + ema_alpha * (raw[i] - carry[i]) for i in range(len(carry))]
        # Only commit a change if the max delta is meaningful.
        max_delta = max(abs(blended[i] - carry[i]) for i in range(len(carry)))
        if max_delta < 0.02:
            smoothed.append((idx, open_, list(carry)))
        else:
            carry = list(blended)
            last_change_frame = idx
            smoothed.append((idx, open_, list(carry)))
    return smoothed


def mouth_open_from_shapes(shapes: List[float]) -> float:
    """Aggregate archetype weights into a single 'mouth_open' scalar 0..1."""
    if not shapes:
        return 0.0
    closed, narrow, wide, round_, stretch, smile = shapes
    # Closed/narrow pull open down; wide/open pull it up; round adds a bit.
    open_scalar = (wide * 1.0) + (round_ * 0.35) + (stretch * 0.3) + (smile * 0.1)
    close_scalar = (closed * 0.9) + (narrow * 0.4)
    value = max(0.0, min(1.0, open_scalar - close_scalar * 0.6))
    return value


def shape_weights_from_frame(
    frame_shapes: List[float],
    *,
    emotion: str = "neutral",
) -> List[float]:
    """Apply emotion bias on top of phoneme shape weights, in place-safe copy."""
    out = list(frame_shapes)
    bias = {
        "neutral": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "confident": [0.0, 0.0, 0.0, 0.0, 0.0, 0.25],
        "curious": [0.0, 0.0, 0.1, 0.15, 0.0, 0.05],
        "surprised": [0.0, 0.0, 0.3, 0.0, 0.4, 0.0],
        "frustrated": [0.15, 0.15, 0.0, 0.0, 0.0, 0.0],
        "happy": [0.0, 0.0, 0.0, 0.0, 0.0, 0.4],
    }.get(emotion, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for i in range(len(out)):
        out[i] = min(1.0, out[i] + bias[i])
    return out


def synthesize(
    text: str,
    total_frames: int,
    fps: float,
    emotion: str = "neutral",
    *,
    add_silence_leading: float = 0.2,
) -> List[Tuple[int, float, List[float]]]:
    """
    Convenience: from a script sentence, produce the full per-frame
    lip-sync table for a character of `total_frames` at `fps`.

    Returns list of (frame_index, mouth_open, shape_weights[6]) where the
    last entry covers any remaining silent frames at the end (closed mouth).
    """
    segments = phonemes_from_text(text)
    if not segments:
        segments = [(0.0, "ah")]

    # Build raw per-frame samples. We step through time at 1/fps and
    # pick the phoneme active at that instant.
    dt = 1.0 / fps
    latest: List[float] = [0.0] * 6  # start closed-ish
    raw: List[Tuple[int, float, List[float]]] = []
    cursor = 0.0
    seg_idx = 0
    n = len(segments)

    # Leading silence (closed mouth).
    for i in range(total_frames):
        t = i * dt
        if t < add_silence_leading:
            raw.append((i, 0.0, list(latest)))
            continue
        # Advance segment cursor.
        while seg_idx < n - 1 and t >= segments[seg_idx + 1][0]:
            seg_idx += 1
        ph = segments[seg_idx][1]
        w = _PHONEME_MAP.get(ph, _PHONEME_MAP["ah"])
        latest = list(w)
        open_ = mouth_open_from_shapes(w)
        raw.append((i, open_, list(w)))

    # If we ran out of phonemes mid-scene, pad the rest with closed + last.
    if seg_idx >= n - 1:
        for i in range(len(raw), total_frames):
            raw.append((i, 0.0, list(latest)))

    # Trim/pad to exactly total_frames.
    raw = raw[:total_frames]
    if len(raw) < total_frames:
        last = raw[-1] if raw else (0, 0.0, [0.0] * 6)
        while len(raw) < total_frames:
            raw.append((len(raw), last[1] * 0.5, list(last[2])))

    # Emotion bias + smoothing.
    biased = [(i, o, shape_weights_from_frame(w, emotion=emotion)) for i, o, w in raw]
    return smooth_shapes(biased, hold_ms=60.0, fps=fps, ema_alpha=0.45)


if __name__ == "__main__":
    # Tiny sanity test.
    table = synthesize("GPU stands for graphics processing unit", 120, 24.0, emotion="confident")
    print(f"frames: {len(table)}")
    print(f"first 3: {table[:3]}")
    print(f"max open: {max(o for _, o, _ in table):.2f}")
    print(f"min open: {min(o for _, o, _ in table):.2f}")
