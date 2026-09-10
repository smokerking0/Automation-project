"""
Real stock background provider (Pexels free API).
Fetches landscape photos matched to each scene prompt, caches them locally,
and returns a cinematic-graded RGB numpy frame ready for the video renderer.

Usage:
    bg = PexelsBackground(api_key="...", width=1280, height=720)
    frame = bg.get_background(scene_prompt, scene_index, visual_style)
    if frame is None:   # no key / offline / API error -> use procedural fallback
        frame = tech_bg.generate(...)

No API key in config -> get_background() always returns None and the renderer
falls back to the existing procedural TechBackground, so this module never
blocks video generation.
"""

import os
import hashlib
import random
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

try:
    import requests
except ImportError:
    requests = None

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Scene prompt keyword -> Pexels search term. Lowercased prompt is checked in
# order; the first matching key wins. Order matters (most specific first).
QUERY_MAP = [
    ("rag vs finetuning", "artificial intelligence comparison chart"),
    ("rag", "vector database search technology"),
    ("fine tuning", "machine learning training workstation"),
    ("vector database", "database servers technology"),
    ("transformer", "neural network abstract blue"),
    ("llm hallucination", "ai brain error abstract"),
    ("prompt engineering", "programming code screen closeup"),
    ("reinforcement", "robot learning laboratory"),
    ("nlp", "language processing text analysis"),
    ("mlops", "devops pipeline server room"),
    ("gpu", "graphics card gpu closeup"),
    ("nvidia blackwell", "graphics card gpu technology blue"),
    ("apple m4", "apple mac computer workspace"),
    ("neural network", "neural network abstract ai"),
    ("machine learning", "data center server room"),
    ("deep learning", "brain neurons science abstract"),
    ("data science", "data analytics dashboard charts"),
    ("big data", "server room data center"),
    ("data analysis", "data analytics monitor charts"),
    ("database", "database servers data center"),
    ("sql", "database sql server screen"),
    ("spark", "server room data center blue"),
    ("tableau power bi", "business charts dashboard screen"),
    ("feature engineering", "data analytics charts laptop"),
    ("python", "programming code screen closeup"),
    ("rust", "programming code screen dark"),
    ("coding", "programming code screen closeup"),
    ("programming", "programmer typing code dark"),
    ("software", "software developer workspace"),
    ("developer", "software developer workspace"),
    ("chatgpt", "ai chatbot robot technology"),
    ("llm", "artificial intelligence futuristic"),
    ("ai agents", "artificial intelligence futuristic"),
    ("robotics", "robot technology industrial"),
    ("computer vision", "camera lens macro technology"),
    ("autonomous", "self driving car technology"),
    ("quantum", "quantum computer technology blue"),
    ("cybersecurity", "cybersecurity network hacking dark"),
    ("security", "cybersecurity network dark"),
    ("cloud", "cloud computing server room"),
    ("server", "server room data center"),
    ("startup", "startup office technology"),
    ("future", "futuristic city night technology"),
    ("crypto", "bitcoin cryptocurrency dark"),
    ("internet", "network cables server"),
    ("algorithm", "abstract technology nodes network"),
    ("technology", "technology dark motherboard macro"),
    ("science", "science laboratory research"),
    ("computer", "computer motherboard macro"),
    ("electronics", "circuit board macro"),
]

# Deterministic per-scene fallback queries when nothing in QUERY_MAP matches.
FALLBACK_QUERIES = [
    "dark technology abstract",
    "server room data center",
    "futuristic technology blue",
    "computer motherboard macro",
    "city night aerial technology",
]


def _quote(prompt: str) -> str:
    return (prompt or "").lower().strip()


def query_for(prompt: str, visual_style: str = "cinematic_realistic") -> str:
    """Map a scene prompt + visual style to a Pexels search term."""
    p = _quote(prompt)
    if p:
        for key, term in QUERY_MAP:
            if key in p:
                return term
    options = list(FALLBACK_QUERIES)
    if visual_style == "cinematic_realistic":
        options = [
            "dark industrial architecture",
            "server room blue light",
            "technology dark moody",
            "city night aerial",
        ] + options
    elif visual_style == "educational_infographic":
        options = [
            "business presentation charts",
            "technology education classroom",
            "data analytics dashboard",
        ] + options
    rng = random.Random(hash(f"{prompt}|{visual_style}"))
    return options[rng.randrange(len(options))]


def _cover_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize an image to exactly (w, h) with cover semantics (crop overflow)."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


class PexelsBackground:
    """Fetch and grade real Pexels photos as cinematic backgrounds."""

    def __init__(
        self,
        api_key: str = "",
        width: int = 1280,
        height: int = 720,
        cache_dir: str = "output/temp/stock",
        timeout: int = 15,
    ):
        self.api_key = (api_key or "").strip()
        self.w = width
        self.h = height
        self.timeout = timeout
        self.cache_dir = Path(cache_dir)
        self.index_path = self.cache_dir / "index.json"
        self.index = self._load_index()
        self._search_cache: dict = {}
        if self.api_key:
            os.makedirs(self.cache_dir, exist_ok=True)

    @property
    def available(self) -> bool:
        """True when an API key is configured (network may still fail)."""
        return bool(self.api_key) and requests is not None

    def _load_index(self) -> dict:
        try:
            if self.index_path.exists():
                return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_index(self):
        try:
            self.index_path.write_text(
                json.dumps(self.index), encoding="utf-8"
            )
        except Exception:
            pass

    def _cached_path(self, query: str) -> str:
        key = hashlib.md5(query.encode("utf-8")).hexdigest()[:16]
        return str(self.cache_dir / f"{key}.jpg")

    def _cached_path_for_scene(self, query: str, scene_index: int) -> str:
        raw = f"{query}#{scene_index}"
        key = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
        return str(self.cache_dir / f"{key}.jpg")

    # ---- API --------------------------------------------------------------

    def search(self, query: str, per_page: int = 5) -> list:
        """Return a list of Pexels photo URLs for a query (cached per query)."""
        if query in self._search_cache:
            return self._search_cache[query]
        if not self.available:
            return []
        try:
            resp = requests.get(
                PEXELS_SEARCH_URL,
                params={
                    "query": query,
                    "per_page": per_page,
                    "orientation": "landscape",
                    "size": "large",
                },
                headers={"Authorization": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            photos = data.get("photos", [])
            urls = []
            for p in photos:
                src = p.get("src", {})
                url = src.get("large2x") or src.get("large") or src.get("original")
                if url:
                    urls.append(url)
            self._search_cache[query] = urls
            self.index.setdefault("queries", {})[query] = {}  # mark as seen
            self._save_index()
            return urls
        except Exception as e:
            print(f"  [stock] search failed for '{query}': {e}")
            return []

    def download(self, url: str, dest: str) -> bool:
        try:
            if os.path.exists(dest):
                return True
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                f.write(resp.content)
            return True
        except Exception as e:
            print(f"  [stock] download failed: {e}")
            return False

    # ---- Grading ----------------------------------------------------------

    def _grade(self, pil_img: Image.Image) -> np.ndarray:
        """Cover-resize + subtle blur + gentle desaturate + cool tint.

        Keeps the photo bright so it stays visible; text legibility is handled
        by a localized dark panel in the video renderer, not by darkening the
        whole image.
        """
        img = _cover_resize(pil_img, self.w, self.h).convert("RGB")
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        img = ImageEnhance.Brightness(img).enhance(1.08)
        img = ImageEnhance.Color(img).enhance(0.85)
        arr = np.asarray(img, dtype=np.float32)
        # Gentle cool (blue-ish) tint only — no uniform darkening.
        arr[..., 0] *= 0.94  # red down
        arr[..., 1] *= 0.97  # green down
        arr[..., 2] = np.minimum(255.0, arr[..., 2] * 1.0 + 4)  # slight blue boost
        return np.clip(arr, 0, 255).astype(np.uint8)

    # ---- Public API --------------------------------------------------------

    def get_background(
        self,
        prompt: str,
        scene_index: int = 0,
        visual_style: str = "cinematic_realistic",
    ) -> np.ndarray:
        """
        Return a graded (w, h, 3) uint8 background frame for a scene, or None
        when stock is unavailable (no key / offline / API error) so callers
        can fall back to procedural backgrounds.
        """
        if not self.available:
            return None
        query = query_for(prompt, visual_style)
        dest = self._cached_path_for_scene(query, scene_index)
        if not os.path.exists(dest):
            urls = self.search(query)
            if not urls:
                return None
            # Try URLs brightest-first so the background photo is actually
            # visible on screen (avoids very dark moody shots).
            dest = self._download_brightest(urls, dest)
            if dest is None:
                return None
        try:
            pil_img = Image.open(dest)
            pil_img.load()
            return self._grade(pil_img)
        except Exception as e:
            print(f"  [stock] failed to load cached image: {e}")
            return None

    def _img_mean(self, path: str) -> float:
        try:
            img = Image.open(path)
            img.load()
            img = img.convert("L").resize((48, 27), Image.LANCZOS)
            return sum(img.getdata()) / (48 * 27)
        except Exception:
            return 0.0

    def _download_brightest(self, urls: list, default_dest: str, min_mean: float = 45.0) -> str:
        """Download the brightest of the photos (to get a visible backdrop)."""
        candidates = []  # (mean, path)
        for i, url in enumerate(urls[:4]):
            dest = default_dest if i == 0 else self._cached_path(url)
            if not self.download(url, dest):
                continue
            m = self._img_mean(dest)
            candidates.append((m, dest))
            if m >= min_mean:
                # Prefer the first that clears the threshold to stay near the
                # deterministic pick, but still keep track of brighter ones.
                break
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]


def main():
    """CLI smoke test: fetch one background for a few prompts."""
    import sys

    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if len(sys.argv) > 1 and sys.argv[1] != "--key":
        api_key = sys.argv[1]
    bg = PexelsBackground(api_key=api_key, width=1280, height=720)
    print(f"Available: {bg.available}")
    if not bg.available:
        print("No PEXELS_API_KEY set — pass it as argv[1] or set the env var.")
        return
    for prompt in ["GPU basics explained", "neural networks for beginners",
                   "python programming tips", "ai ethics"]:
        for style in ["cinematic_realistic", "cinematic_tech"]:
            frame = bg.get_background(prompt, 0, style)
            print(f"  {style:22s} | {prompt[:30]:32s} -> {frame.shape if frame is not None else 'None (fallback)'}")


if __name__ == "__main__":
    main()