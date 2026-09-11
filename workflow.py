"""
Daily Tech Video Automation Workflow
Automates daily AI, Programming, and Tech video creation + YouTube upload.
"""

import os
import csv
import re
import yaml
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List

# HTTP error import for YouTube upload error handling
try:
    from googleapiclient.errors import HttpError
    HAS_HTTP_ERROR = True
except ImportError:
    HAS_HTTP_ERROR = False

from video import CinematicVideoGenerator, VideoConfig, Scene
from topics import TechTopicGenerator, VideoScript
from upload import YouTubeUploader, ThumbnailGenerator
from topic_tracker import TopicTracker

try:
    from ollama_gen import OllamaClient
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

try:
    from research import TopicResearch
    HAS_RESEARCH = True
except ImportError:
    HAS_RESEARCH = False

try:
    from translate import normalize_lang, lang_label, translate_voiceover, translate_text
    HAS_TRANSLATE = True
except ImportError:
    HAS_TRANSLATE = False

try:
    from seo import build_seo
    HAS_SEO = True
except ImportError:
    HAS_SEO = False

try:
    from growth import apply_growth, apply_hook
    HAS_GROWTH = True
except ImportError:
    HAS_GROWTH = False

try:
    from stockbg import PexelsBackground
    HAS_STOCK = True
except ImportError:
    HAS_STOCK = False

try:
    from youtube_trending import (
        YouTubeTrending, build_topic_dict, FIELD_MENU, FIELD_FALLBACKS, FIELD_KEYWORDS, _kw_match,
    )
    HAS_TRENDING = True
except ImportError:
    HAS_TRENDING = False

os.environ["PYTHONIOENCODING"] = "utf-8"

# Load .env file if it exists (simple key=value parser, no dependency needed)
def _load_dotenv(path: str = ".env"):
    """Load environment variables from .env file (if present)."""
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value

_load_dotenv()


class DailyTechWorkflow:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)

        # Initialize Ollama if configured
        self.ollama = None
        ollama_cfg = self.config.get("ollama", {})
        if HAS_OLLAMA and ollama_cfg.get("enabled", False):
            host = ollama_cfg.get("host", "http://localhost:11434")
            model = ollama_cfg.get("model", "llama3.1:70b")
            print(f"Connecting to Ollama at {host}...")
            self.ollama = OllamaClient(host=host, model=model)
            print(f"  {self.ollama.status()}")
        elif not HAS_OLLAMA:
            print("  Ollama not available (ollama_gen.py not found)")
        else:
            print("  Ollama disabled in config.yaml")

        self.topic_generator = TechTopicGenerator()
        self.video_generator = None
        self.youtube_uploader = None
        self.thumbnail_generator = ThumbnailGenerator()
        self.tracker = TopicTracker()

        # Real stock background provider (Pexels) when a key is configured.
        self.stock_background = None
        self.background_mode = "auto"
        stock_cfg = self.config.get("stock", {}) or {}
        stock_key = os.environ.get("PEXELS_API_KEY", stock_cfg.get("api_key", "")).strip()
        if HAS_STOCK and stock_key:
            try:
                self.stock_background = PexelsBackground(
                    api_key=stock_key,
                    width=self.config["video"]["width"],
                    height=self.config["video"]["height"],
                    cache_dir=stock_cfg.get("cache_dir", "output/temp/stock"),
                )
                self.background_mode = stock_cfg.get("background_mode", "auto")
                status = "enabled" if self.stock_background.available else "key missing"
                print(f"  Stock backgrounds: {status} (mode={self.background_mode})")
            except Exception as e:
                print(f"  Stock backgrounds failed to init: {e}")

        for d in ["output", "output/scripts", "output/thumbnails", "output/videos",
                  "output/temp/stock"]:
            Path(d).mkdir(parents=True, exist_ok=True)

        # YouTube Data API key (trending discovery): .env overrides config.yaml.
        self.youtube_api_key = (os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()
                                or ((self.config.get("youtube", {}) or {}).get("data_api_key", "") or "").strip())

    def _load_config(self, config_path: str) -> dict:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        return self._default_config()

    def _default_config(self) -> dict:
        return {
            "video": {"width": 1920, "height": 1080, "fps": 30,
                      "ken_burns_zoom": 1.15, "transition_duration": 0.5,
                      "character_enabled": True},
            "voice": {"language": "en"},
            "youtube": {"client_secrets": "client_secret.json",
                        "credentials": "token.json",
                        "default_category": "28",
                        "privacy_status": "public"},
            "branding": {
                "enabled": True,
                "accent_color": "#00E5FF",
                "intro": "Hi everyone, welcome to our channel, Core Infotech!",
                "intro_suffix": "By the end of this video, you'll understand {topic}.",
                "outro": "Thank you for watching! Don't forget to like, comment, and subscribe to our channel for the latest tech updates and informative videos.",
            },
            "seo": {"enabled": True},
            "schedule": {"daily_upload_time": "10:00", "auto_upload": False}
        }

    def _validate_script(self, script, topic: str = "") -> None:
        """Quality gate for education-focused scripts."""
        if not script.scenes or len(script.scenes) < 4:
            raise ValueError(f"Script too short: {len(script.scenes)} scenes (need >=4)")
        total = sum(len(s.voiceover or "") for s in script.scenes)
        if total < 500:
            raise ValueError(f"Script voiceover too short ({total} chars) — need richer explanation")
        total_words = sum(len((s.voiceover or "").split()) for s in script.scenes)
        if total_words < 500:
            raise ValueError(f"Script narration too short ({total_words} words) — target is 700-1,200 words")
        texts = [s.text.strip().lower() for s in script.scenes if s.text]
        if len(texts) != len(set(texts)):
            raise ValueError("Duplicate scene titles detected — script not grounded")
        # Title must reflect the requested topic
        if topic:
            tks = [w for w in topic.lower().replace("_"," ").split() if len(w) > 2]
            title_low = (script.title or "").lower()
            if tks and not any(k in title_low for k in tks[:3]):
                print(f"  [validate] warning: title does not contain topic keywords {tks[:3]}")
        # Detect repeated generic bodies across points
        bodies = [(s.voiceover or "").strip()[:160].lower() for s in script.scenes[1:-1] if s.voiceover]
        if len(bodies) >= 3 and len(set(bodies)) == 1:
            raise ValueError("All point voiceovers identical — grounding failed")
        generic_phrases = ["in this video we will discuss", "is a key area of modern technology", "core idea behind"]
        for s in script.scenes:
            low = (s.voiceover or "").lower()
            for g in generic_phrases:
                if low.count(g) > 1:
                    raise ValueError(f"Generic filler detected in: {s.text[:40]}")
        if not script.title or len(script.title) < 10:
            raise ValueError("Missing or too-short title")

    def _guess_category(self, topic: str) -> str:
        """Best-effort category guess for a topic, used to filter trending."""
        t = topic.lower().replace("_", " ")
        mapping = [
            ("gpu", {"gpu", "graphics", "hardware", "cuda", "nvidia", "chip", "processor"}),
            ("data_science", {"data science", "data", "statistics", "analytics", "database", "pandas", "sql"}),
            ("machine_learning", {"machine learning", "ml", "model training", "deep learning", "tensorflow", "pytorch"}),
            ("programming", {"python", "coding", "programming", "developer", "javascript", "software", "code", "compiler"}),
            ("ai", {"ai", "chatgpt", "gpt", "llm", "agent", "neural", "intelligence", "prompt", "generative"}),
        ]
        for cat, words in mapping:
            if t in words or any(w in t for w in words):
                return cat
        return "ai"

    def fetch_trending(self, top_n: int = 12, field: str = "") -> list:
        """Fetch current YouTube Science & Tech trending topics (live).

        Uses the YouTube Data API most-popular chart when a key is configured
        (from .env / config.yaml), otherwise RSS channel feeds. Pass a
        `field` from FIELD_MENU (e.g. "windows", "containers", "ai_ml") to
        rank results toward that category. Returns a list of TrendingTopic
        ready for selection or video generation.
        """
        if not HAS_TRENDING:
            print("  [trending] youtube_trending.py unavailable")
            return []
        src = "YouTube Data API" if self.youtube_api_key else "RSS feeds (no key)"
        label = dict(FIELD_MENU).get(field, "General")
        print(f"  [trending] field={label} | fetching from: {src}")
        try:
            return YouTubeTrending(api_key=self.youtube_api_key, timeout=15).discover(top_n=top_n, field=field)
        except Exception as e:
            print(f"  [trending] failed: {e}")
            return []

    def _auto_trending_topic(self, topic: str):
        """
        Search live YouTube trending topics and return the one whose title best
        matches the given topic's category. Returns (topic_dict, matched_title)
        or (None, None) when trending is unavailable/no match.
        """
        if not HAS_TRENDING:
            return None, None
        try:
            items = YouTubeTrending(api_key=self.youtube_api_key).discover(top_n=20)
        except Exception as e:
            print(f"  [trending] auto-search failed: {e}")
            return None, None
        if not items:
            return None, None

        cat = self._guess_category(topic)
        cat_words = {
            "ai": {"ai", "artificial", "chatgpt", "gpt", "llm", "model", "agent", "neural", "intelligence", "prompt", "machine"},
            "gpu": {"gpu", "graphics", "nvidia", "chip", "hardware", "cuda", "raster", "render"},
            "data_science": {"data", "database", "sql", "analytics", "statistics", "pandas", "science"},
            "machine_learning": {"learning", "model", "training", "ml", "neural", "deep"},
            "programming": {"python", "coding", "code", "programming", "developer", "software", "javascript", "api"},
        }.get(cat, set())
        cat_words.update(topic.lower().split("_"))

        def score(it):
            t = it.topic_dict["title"].lower()
            return sum(1 for w in cat_words if w in t)

        best = sorted(items, key=score, reverse=True)
        top = best[0]
        if score(top) == 0:
            print(f"  [trending] no strong match for '{topic}', using best available.")
        else:
            print(f"  [trending] category '{cat}' matched: {top.topic_dict['title']}")
        return top.topic_dict, top.topic_dict["title"]

    def create_video(
        self, topic: str, style: str = "educational",
        duration: int = 3, upload: bool = False, privacy: str = "public",
        topic_dict: Optional[dict] = None, research_query: Optional[str] = None,
        language: Optional[str] = None, character_enabled: Optional[bool] = None,
        preview: bool = False, seo_field: str = "",
    ) -> dict:
        results = {
            "success": False, "topic": topic, "title": "",
            "script_path": None, "video_path": None,
            "thumbnail_path": None, "youtube_url": None,
            "estimated_duration": 0, "error": None
        }

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            lang = normalize_lang(language or self.config.get("voice", {}).get("language", "en"))
            lang_name = lang_label(lang) if HAS_TRANSLATE else lang

            # Infer the content field for SEO when the caller didn't supply one.
            if not seo_field:
                infer_src = [research_query or "", topic_dict.get("title", "") if topic_dict else "", topic]
                seo_field = next((f for f, kws in FIELD_KEYWORDS.items()
                                  if any(_kw_match(kw, s) for s in infer_src for kw in kws)), "")

            # Only auto-search trending when caller didn't give an explicit
            # known topic. Explicit keys like `rag_vs_finetuning` must render
            # that topic, not a random trending video about GPT-6.
            is_known = topic in self.topic_generator.ALL_TOPICS
            should_trend = (
                topic_dict is None
                and HAS_TRENDING
                and (topic.startswith("trending_") or not is_known)
            )
            if should_trend:
                print(f"\n  Searching trending YouTube topics related to: {topic}")
                found_dict, matched_title = self._auto_trending_topic(topic)
                if found_dict is not None:
                    topic_dict = found_dict
                    if research_query is None:
                        research_query = matched_title

            # Log the pipeline run start
            self.tracker.log(
                topic=topic,
                title="",
                status="generating",
                notes="Pipeline started",
            )

            # Step 1: Research the topic (live web search)
            research_text = ""
            research_snippets: List[dict] = []
            rq = research_query or topic.replace("_", " ")
            if HAS_RESEARCH:
                print(f"  Researching topic: {rq}...")
                researcher = TopicResearch()
                try:
                    research_snippets = researcher.search_snippets(rq, num_results=5)
                except Exception:
                    research_snippets = []
                if research_snippets:
                    parts = []
                    for i, s in enumerate(research_snippets, 1):
                        parts.append(f"[{i}] {s['title']}: {s['snippet']} ({s['url']})")
                    research_text = " | ".join(parts)
                    if len(research_text) > 1500:
                        research_text = research_text[:1497] + "..."
                    print(f"  Research: {len(research_text)} chars from {len(research_snippets)} sources")
                else:
                    # Fallback to the one-liner helper
                    research_text = researcher.research(rq, num_results=5)
                    if research_text:
                        print(f"  Research: {len(research_text)} chars fetched")
                    else:
                        print("  Research: no results (continuing with template)")
            else:
                print("  Research: unavailable (skipping)")

            print(f"\n{'='*60}")
            print(f"  STEP 1: Generating Script")
            print(f"{'='*60}")

            ollama_dict = None
            if self.ollama is not None and getattr(self.ollama, "available", False):
                try:
                    print(f"  Trying Ollama ({self.ollama.model}) for grounded script...")
                    ollama_dict = self.ollama.generate_video_script(
                        topic=topic, style=style, duration_minutes=duration, research_text=research_text)
                    if ollama_dict:
                        print(f"  Ollama: grounded script ready — {ollama_dict.get('title','')[:60]}")
                except Exception as e:
                    print(f"  Ollama failed, falling back to templates: {e}")
                    ollama_dict = None

            if ollama_dict:
                topic_dict = {
                    "title": ollama_dict.get("title", topic.replace("_", " ").title()),
                    "intro": ollama_dict.get("intro", ""),
                    "points": ollama_dict.get("points", []) or topic_dict.get("points", []) if topic_dict else ollama_dict.get("points", []),
                    "outro": ollama_dict.get("outro", ""),
                    "research_text": research_text,
                    "topic_dict": topic_dict or ollama_dict,
                    "_voiceovers": ollama_dict.get("voiceovers", []),
                    "_sources": research_snippets,
                }

            script = self.topic_generator.generate_script(
                topic=topic, style=style, target_duration=duration,
                topic_dict=topic_dict, research_text=research_text,
            )
            # If Ollama provided per-point voiceovers, inject them so narration is specific.
            if ollama_dict and ollama_dict.get("voiceovers"):
                vos = ollama_dict["voiceovers"]
                for idx, sc in enumerate(script.scenes):
                    if idx < len(vos) and vos[idx] and len(vos[idx].strip()) > 20:
                        sc.voiceover = vos[idx].strip()
            # Attach sources to description for traceability.
            if research_snippets:
                src_lines = "\n".join(f"• {s['title']} — {s['url']}" for s in research_snippets[:5])
                script.description = script.description.strip() + "\n\nSources:\n" + src_lines
            self._validate_script(script, topic=topic)

            # Only the spoken narration is translated — on-screen text stays
            # English. This lets the male Hindi/Nepali voice speak properly.
            if HAS_TRANSLATE and lang != "en":
                print(f"  Translating narration to {lang_name} (text stays English)...")
                script = translate_voiceover(script, lang)

            script_path = f"output/scripts/{topic}_{timestamp}.json"
            self.topic_generator.save_script(script, script_path)
            results["script_path"] = script_path
            results["title"] = script.title
            results["estimated_duration"] = script.estimated_duration

            if preview and len(script.scenes) > 4:
                print(f"  Preview mode: trimming {len(script.scenes)} -> 4 scenes for fast render")
                script.scenes = script.scenes[:3] + script.scenes[-1:]
                script.estimated_duration = sum(s.duration for s in script.scenes)
            # Bullet points for the SEO description come from the on-screen
            # scene headings (captured before the brand intro/outro are added).
            content_points = [sc.text for sc in script.scenes if (sc.text or "").strip()][:8]
            content_points = list(dict.fromkeys(content_points))
            self._add_brand_scenes(script, topic, lang, seo_field)
            print(f"\n  Title: {script.title}")
            print(f"  Scenes: {len(script.scenes)}")
            print(f"  Duration: ~{script.estimated_duration/60:.1f} minutes")

            print(f"\n{'='*60}")
            print(f"  STEP 2: Generating Video")
            print(f"{'='*60}")

            # Preview: auto downscale for 4x faster render
            pv_w, pv_h, pv_fps = self.config["video"]["width"], self.config["video"]["height"], self.config["video"]["fps"]
            if preview:
                pv_w, pv_h, pv_fps = 640, 360, 20
                print(f"  Preview config: {pv_w}x{pv_h} @ {pv_fps}fps (fast)")
            video_config = VideoConfig(
                width=pv_w,
                height=pv_h,
                fps=pv_fps,
                ken_burns_zoom=self.config["video"].get("ken_burns_zoom", 1.15),
                transition_duration=self.config["video"].get("transition_duration", 0.5),
                safety_margin=self.config["video"].get("safety_margin", 0.20),
                voice_lang=lang,
                presenter_avatar_path=((self.config.get("presenter", {}) or {}).get("avatar_path", "") or ""),
                presenter_avatar_data=((self.config.get("presenter", {}) or {}).get("avatar_data_uri", "") or ""),
                stock_background=self.stock_background,
                background_mode=self.background_mode,
                visualizer_enabled=((self.config.get("visualizer", {}) or {}).get("enabled", False)),
                visualizer_opacity=((self.config.get("visualizer", {}) or {}).get("opacity", 0.85)),
                visualizer_font_size=((self.config.get("visualizer", {}) or {}).get("font_size", 24)),
                visualizer_font_path=((self.config.get("visualizer", {}) or {}).get("font_path", "C:/Windows/Fonts/comic.ttf")),
            )
            # Allow per-call override; falls back to config default
            char_enabled = (character_enabled if character_enabled is not None
                            else self.config.get("video", {}).get("character_enabled", True))
            video_config.character_enabled = char_enabled

            print(f"  Character on screen: {'yes' if char_enabled else 'no'}")

            self.video_generator = CinematicVideoGenerator(video_config)

            lang_tag = f"_{lang}" if lang != "en" else ""
            video_path = f"output/videos/{topic}{lang_tag}_{timestamp}.mp4"
            self.video_generator.generate_video(script.scenes, video_path)
            results["video_path"] = video_path

            print(f"\n{'='*60}")
            print(f"  STEP 3: Generating Thumbnail")
            print(f"{'='*60}")

            thumbnail_path = f"output/thumbnails/{topic}_{timestamp}.jpg"
            try:
                self.thumbnail_generator.generate_thumbnail(
                    title=script.title, output_path=thumbnail_path,
                    character_enabled=char_enabled,
                )
                print(f"  Thumbnail: {thumbnail_path}")
            except Exception as e:
                thumbnail_path = None
                print(f"  Thumbnail generation failed (non-fatal): {e}")

            if upload:
                print(f"\n{'='*60}")
                print(f"  STEP 4: Uploading to YouTube")
                print(f"{'='*60}")
                srt_path = getattr(self.video_generator, "last_srt_path", None)
                chapters = getattr(self.video_generator, "chapters", [])
                youtube_url = self._upload_video(
                    video_path=video_path, script=script,
                    thumbnail_path=thumbnail_path, privacy=privacy,
                    seo_field=seo_field, content_points=content_points,
                    srt_path=srt_path, chapters=chapters, lang=lang,
                )
                results["youtube_url"] = youtube_url
                results["video_id"] = getattr(self, "_last_video_id", None)
                results["srt_path"] = srt_path if srt_path and os.path.exists(srt_path) else None
                results["seo"] = getattr(self, "_last_seo", None)

            results["success"] = True
            # Update tracker: success
            self.tracker.update_status(
                topic=topic,
                status="done",
                youtube_url=results["youtube_url"],
                notes=f"Video created: {results['video_path']}",
            )
            self._print_results(results)

        except Exception as e:
            results["error"] = str(e)
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()
            # Update tracker: failed
            self.tracker.update_status(
                topic=topic,
                status="failed",
                notes=f"Error: {str(e)}",
            )

        return results

    def _upload_video(self, video_path, script, thumbnail_path, privacy,
                      seo_field="", content_points=None, srt_path=None,
                      chapters=None, lang="en"):
        try:
            self.youtube_uploader = YouTubeUploader(
                client_secrets_path=self.config["youtube"]["client_secrets"],
                credentials_path=self.config["youtube"]["credentials"]
            )
        except Exception as auth_exc:
            print(f"\n  YouTube upload SKIPPED — authentication failed: {auth_exc}")
            print("  To fix: run `py upload.py setup` in a standalone terminal,")
            print("  complete the browser OAuth flow, then re-run the pipeline.")
            return None

        # SEO metadata (auto from the topic title + field).
        seo_enabled = (self.config.get("seo", {}) or {}).get("enabled", True)
        if seo_enabled and HAS_SEO:
            try:
                seo = build_seo(script.title, field=seo_field,
                                points=content_points, chapters=chapters)
                print(f"\n  [SEO] Title: {seo['title']}")
                print(f"  [SEO] Tags: {', '.join(seo['tags'])}")
                print(f"  [SEO] Hashtags: {', '.join(seo['hashtags'])}")
            except Exception as e:
                print(f"  [SEO] metadata build failed ({e}) — using plain script metadata")
                seo = {"title": script.title, "description": script.description,
                       "tags": script.tags or []}
        else:
            seo = {"title": script.title, "description": script.description,
                   "tags": script.tags or []}

        try:
            response = self.youtube_uploader.upload_video(
                video_path=video_path, title=seo["title"],
                description=seo["description"], tags=seo["tags"],
                category_id=self.config["youtube"]["default_category"],
                privacy_status=privacy, thumbnail_path=thumbnail_path
            )
            video_id = response["id"]
            url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"\n  Uploaded: {url}")
            self._last_video_id = video_id
            self._last_seo = seo

            if srt_path and os.path.exists(srt_path):
                try:
                    self.youtube_uploader.upload_captions(
                        video_id, srt_path, language=lang)
                except Exception as cap_exc:
                    print(f"  [captions] upload skipped ({cap_exc})")
            return url
        except (HttpError if HAS_HTTP_ERROR else Exception) as e:
            if not HAS_HTTP_ERROR and not type(e).__name__ == "HttpError":
                raise
            # YouTube API-level error (quota, invalid request, etc.)
            detail = e.content.decode('utf-8', errors='replace') if e.content else str(e)
            print(f"\n  YouTube API error: {e}")
            print(f"  Details: {detail[:300]}")
            return None
        except FileNotFoundError as e:
            print(f"\n  YouTube upload skipped: {e}")
            return None
        except Exception as e:
            print(f"\n  Upload failed: {type(e).__name__}: {e}")
            return None

    def _add_brand_scenes(self, script, topic, lang, seo_field: str = ""):
        """Prepend the branded intro and append the CTA outro every video.

        Voiceover stays in the narration language (EN stays as-is; hi/ne is
        translated). On-screen text/headings stay English per the channel rule.

        When the growth module is available, also injects:
        - Cold-open hook (before the brand greeting)
        - Mid-video engagement CTA
        - Key-takeaways recap (before the outro)
        - Scene variation pass for visual pattern interrupts
        """
        brand = (self.config.get("branding", {}) or {})
        if not brand.get("enabled", True):
            return
        accent = brand.get("accent_color", "#00E5FF")

        # ── Growth boosts (watch-time & engagement) ──────────────────────
        if HAS_GROWTH:
            try:
                apply_growth(script.scenes, script.title, lang, seo_field,
                             translate_text if HAS_TRANSLATE else None)
                print(f"  [growth] mid-CTA + recap + variation applied ({lang})")
            except Exception as exc:
                print(f"  [growth] skipped: {exc}")
            try:
                apply_hook(script.scenes, script.title, seo_field, lang,
                           translate_text if HAS_TRANSLATE else None)
                print(f"  [growth] cold-open hook added ({lang})")
            except Exception as exc:
                print(f"  [growth] hook skipped: {exc}")

        # ── Brand intro / outro ──────────────────────────────────────────
        # Remove any pre-existing "Thanks for Watching" or topics.py outro
        # scene so the brand outro is the only closing scene.
        _outro_re = re.compile(r"thanks\s+for\s+watch", re.IGNORECASE)
        if script.scenes and _outro_re.search(script.scenes[-1].text or ""):
            script.scenes.pop()
        intro = (brand.get("intro", "") or
                 "Hi everyone, welcome to our channel, Core Infotech!")
        suffix = (brand.get("intro_suffix", "") or "").replace("{topic}", script.title)
        outro = (brand.get("outro", "") or
                 "Thank you for watching! Don't forget to like, comment, and "
                 "subscribe to our channel for the latest tech updates and "
                 "informative videos.")
        intro_vo = f"{intro} {suffix}".strip() if suffix else intro.strip()
        outro_vo = outro.strip()
        if HAS_TRANSLATE and lang != "en":
            try:
                intro_vo = translate_text(intro_vo, lang)
            except Exception:
                pass
            try:
                outro_vo = translate_text(outro_vo, lang)
            except Exception:
                pass
        intro_scene = Scene(
            text="Core Infotech",
            title_text="Core Infotech",
            voiceover=intro_vo,
            visual_style="educational_infographic",
            text_color=accent,
            show_character=False,
            animation="fade",
            bullet_points=[script.title],
            background_prompt=f"tech logo intro {accent}",
        )
        outro_scene = Scene(
            text="Thanks for watching!",
            title_text="Thanks for watching!",
            voiceover=outro_vo,
            visual_style="educational_infographic",
            text_color=accent,
            show_character=False,
            animation="fade",
            bullet_points=["Like this video", "Subscribe to Core Infotech",
                           f"Next: {script.title}"],
            background_prompt=f"tech outro subscribe {accent}",
        )
        script.scenes.insert(0, intro_scene)
        script.scenes.append(outro_scene)
        brand_count = 1 + 1 + (2 if HAS_GROWTH else 0)
        print(f"  [brand] intro + outro scenes added ({lang}) + {brand_count - 2} growth scenes")

    def _print_results(self, results):
        print(f"\n{'='*60}")
        print(f"  VIDEO CREATED SUCCESSFULLY")
        print(f"{'='*60}")
        print(f"  Title: {results['title']}")
        print(f"  Duration: ~{results['estimated_duration']/60:.1f} minutes")
        print(f"  Script: {results['script_path']}")
        print(f"  Video: {results['video_path']}")
        print(f"  Thumbnail: {results['thumbnail_path']}")
        if results['youtube_url']:
            print(f"  YouTube: {results['youtube_url']}")
        print(f"{'='*60}\n")

    def run_daily(self, upload=False, privacy="public", language: Optional[str] = None, character_enabled: bool = True):
        # Rotate through content fields each day so coverage stays balanced
        # across networks, OS, cloud, AI/ML, DevOps, containers, languages...
        field, label = FIELD_MENU[datetime.now().timetuple().tm_yday % len(FIELD_MENU)]
        print(f"\n  Today's field: {label}")
        items = self.fetch_trending(top_n=8, field=field)
        if not items:
            items = _pick_trending_item(self, field)
        if items:
            it = items[0]
            topic_key = f"trending_{field}_{datetime.now().strftime('%Y%m%d')}"
            print(f"  Today's trending pick: {it.topic_dict['title']}")
            print(f"{'='*60}")
            return self.create_video(
                topic=topic_key, style="educational", duration=3,
                upload=upload, privacy=privacy, topic_dict=it.topic_dict,
                research_query=it.topic_dict["title"],
                language=language, character_enabled=True,
                seo_field=field,
            )
        # Fallback: rotating static topic from the full pool
        topic = self.topic_generator.get_daily_topic()
        topic_info = self.topic_generator.ALL_TOPICS[topic]
        print(f"\n  Daily Video (rotation): {topic_info['title']}")
        print(f"{'='*60}")
        return self.create_video(topic=topic, style="educational", duration=3, upload=upload, privacy=privacy, language=language, seo_field=field)

    def discover_trending(self, top_n: int = 12, field: str = "") -> list:
        """Fetch trending topics (API if key set, else RSS), filtered by field."""
        if not HAS_TRENDING:
            print("  Trending module unavailable.")
            return []
        items = YouTubeTrending(api_key=self.youtube_api_key, timeout=15).discover(top_n=top_n, field=field)
        src = "YouTube Data API" if self.youtube_api_key else "RSS feeds (no key)"
        label = dict(FIELD_MENU).get(field, "General")
        print(f"  Found {len(items)} trending topics via {src} (field: {label}).\n")
        for i, it in enumerate(items, 1):
            print(f"{i}. {it.display()}")
        return items

    def auto_make(
        self, top_n: int = 3, duration: int = 3,
        upload: bool = False, privacy: str = "public",
        language: Optional[str] = None, character_enabled: bool = True,
    ) -> dict:
        """
        Auto-pipeline: trending list -> pick best matching topics -> research,
        script, render, thumbnail, and optionally upload each one.
        """
        print(f"\n{'='*60}")
        print(f"  AUTO MODE: generating {top_n} videos from trending topics")
        print(f"{'='*60}")

        if not HAS_TRENDING:
            print("  Trending module unavailable — using static topic pool.")
            topics = self.topic_generator.get_topic_list()[:top_n]
            return self.run_batch(topics, duration=duration, upload=upload, language=language)

        # Rotate across fields so auto mode covers different categories.
        day = datetime.now().timetuple().tm_yday
        results = {}
        made = 0
        for i in range(top_n):
            field, label = FIELD_MENU[(day + i) % len(FIELD_MENU)]
            print(f"\n[{i + 1}/{top_n}] Field: {label}")
            items = self.fetch_trending(top_n=6, field=field)
            if not items:
                items = _pick_trending_item(self, field)
            if not items:
                print("  No topics for this field — skipping.")
                continue
            it = items[0]
            slug = re.sub(r"[^a-z0-9]+", "_", it.topic_dict["title"].lower()).strip("_")[:60]
            print(f"  Trending topic: {it.topic_dict['title']}")
            res = self.create_video(
                topic=slug, style="educational", duration=duration,
                upload=upload, privacy=privacy,
                topic_dict=it.topic_dict, research_query=it.topic_dict["title"],
                language=language, character_enabled=True,
                seo_field=field,
            )
            results[it.topic_dict["title"]] = res
            made += 1

        ok = sum(1 for r in results.values() if r.get("success"))
        print(f"\n{'='*60}")
        print(f"  AUTO COMPLETE: {ok}/{made} videos created")
        print(f"{'='*60}")
        return results

    def run_batch(self, topics: List[str], duration: int = 3, upload: bool = False,
                  language: Optional[str] = None, character_enabled: bool = True):
        results = []
        print(f"\n  BATCH MODE: Creating {len(topics)} videos")
        print(f"{'='*60}")
        for i, topic in enumerate(topics, 1):
            print(f"\n[{i}/{len(topics)}] Processing: {topic}")
            result = self.create_video(topic=topic, duration=duration, upload=upload,
                                       language=language, character_enabled=character_enabled)
            results.append(result)
        success = sum(1 for r in results if r["success"])
        print(f"\n{'='*60}")
        print(f"  BATCH COMPLETE: {success}/{len(topics)} videos created")
        print(f"{'='*60}")
        return results

    def schedule_daily(self, time_str="10:00", upload=False):
        try:
            import schedule
        except ImportError:
            raise ImportError("Install schedule: py -m pip install schedule")
        print(f"\n  Scheduling daily videos at {time_str}")
        print(f"   Press Ctrl+C to stop\n")
        schedule.every().day.at(time_str).do(self.run_daily, upload=upload)
        while True:
            schedule.run_pending()
            time.sleep(60)


def interactive_mode():
    workflow = DailyTechWorkflow()
    print("=" * 60)
    print("   DAILY TECH VIDEO AUTOMATION")
    print("   AI | GPU | Data Science | ML | Programming")
    print("=" * 60)

    while True:
        print("\nMAIN MENU:")
        print("  1. Create Single Video")
        print("  2. Create Daily Video (Auto Topic)")
        print("  3. Batch Create Multiple Videos")
        print("  4. List Trending Topics")
        print("  5. Setup YouTube API")
        print("  6. Exit")

        choice = input("\nSelect option (1-6): ").strip()

        if choice == "1":
            _create_single_video(workflow)
        elif choice == "2":
            _create_daily_video(workflow)
        elif choice == "3":
            _batch_create(workflow)
        elif choice == "4":
            _list_topics(workflow)
        elif choice == "5":
            os.system("py upload.py setup")
        elif choice == "6":
            print("\nGoodbye!")
            break
        else:
            print("Invalid option.")


def _pick_trending_item(workflow, field: str, top_n: int = 10):
    """Fetch trending for a field; fall back to a generated title if empty."""
    items = workflow.fetch_trending(top_n=top_n, field=field)
    if items:
        return items

    label = dict(FIELD_MENU).get(field, "General")
    print(f"\n  No trending matches in '{label}' right now — using a curated topic instead.")
    fallback_titles = FIELD_FALLBACKS.get(field, FIELD_FALLBACKS["news"])
    title = fallback_titles[0]
    from youtube_trending import TrendingTopic
    return [TrendingTopic(title=title, source="fallback", score=1,
                          topic_dict=build_topic_dict(title))]


def _create_single_video(workflow):
    print("\n" + "=" * 60)
    print("SELECT CATEGORY (content field)")
    print("=" * 60)
    for i, (key, label) in enumerate(FIELD_MENU, 1):
        print(f"  {i:>2}. {label}")
    n = len(FIELD_MENU)
    print(f"  {n + 1:>2}. All Trending (mixed)")
    print(f"  {n + 2:>2}. Custom Topic")
    cat = input(f"\nCategory (1-{n + 2}): ").strip()

    if cat.isdigit() and 0 < int(cat) <= n:
        field = FIELD_MENU[int(cat) - 1][0]
        items = _pick_trending_item(workflow, field)
    elif cat.isdigit() and int(cat) == n + 1:
        field = ""
        items = workflow.fetch_trending(top_n=12)
        print(f"\n  Found {len(items)} all-field trending topics.\n")
    else:
        item = None

    if "items" in dir() and items:
        print("\n" + "=" * 60)
        print("TRENDING TOPICS")
        print("=" * 60)
        for i, it in enumerate(items, 1):
            print(f"  {i:>2}. {it.display()}")
        print(f"  {len(items) + 1:>2}. Custom topic (type your own)")
        choice = input(f"\nSelect topic (1-{len(items) + 1}): ").strip()
        if choice.isdigit() and 0 < int(choice) <= len(items):
            item = items[int(choice) - 1]
        else:
            item = None

    if "item" not in dir() or item is None:
        topic_key = input("\nEnter topic name (underscores): ").strip().replace(" ", "_")
        if not topic_key:
            topic_key = "ai_basics"
        item = None
    else:
        slug = re.sub(r"[^a-z0-9]+", "_", item.topic_dict["title"].lower()).strip("_")[:60]
        print(f"\n  Selected: {item.topic_dict['title']}")
        print("  Generating a fresh script from this trending topic + live research.")
        topic_key = f"trending_{slug}"

    print("\nStyle: 1=Educational  2=Listicle  3=Tutorial  4=Deep Dive")
    style = {"1": "educational", "2": "listicle", "3": "tutorial", "4": "deep_dive"}.get(
        input("Style (1-4): ").strip(), "educational"
    )
    print("\nDuration: 1=1min  2=3min  3=5min")
    duration = {"1": 1, "2": 3, "3": 5}.get(input("Duration (1-3): ").strip(), 3)
    print("\nLanguage: 1=English  2=Hindi  3=Nepali")
    lang = {"1": "en", "2": "hi", "3": "ne"}.get(
        input("Language (1-3): ").strip(), "en"
    )
    upload = input("\nUpload to YouTube? (y/n): ").strip().lower() == "y"
    privacy = "public"
    if upload:
        print("Privacy: 1=Public  2=Unlisted  3=Private")
        privacy = {"1": "public", "2": "unlisted", "3": "private"}.get(
            input("Privacy (1-3): ").strip(), "public"
        )
    print("\nCharacter on screen? 1=Yes (default)  2=No")
    char_opt = input("Character (1-2): ").strip()
    char_enabled = char_opt != "2"
    workflow.create_video(
        topic=topic_key, style=style, duration=duration, upload=upload,
        privacy=privacy, language=lang, character_enabled=char_enabled,
        topic_dict=item.topic_dict if item else None,
        research_query=item.topic_dict["title"] if item else None,
        seo_field=field if "field" in dir() else "",
    )


def _create_daily_video(workflow):
    upload = input("\nUpload to YouTube? (y/n): ").strip().lower() == "y"
    privacy = "public"
    if upload:
        print("Privacy: 1=Public  2=Unlisted  3=Private")
        privacy = {"1": "public", "2": "unlisted", "3": "private"}.get(
            input("Privacy (1-3): ").strip(), "public"
        )
    print("\nCharacter on screen? 1=Yes (default)  2=No")
    char_opt = input("Character (1-2): ").strip()
    char_enabled = char_opt != "2"
    workflow.run_daily(upload=upload, privacy=privacy, character_enabled=char_enabled)


def _batch_create(workflow):
    generator = TechTopicGenerator()
    print("\nBATCH CREATE")
    print("  1. All AI Topics")
    print("  2. All GPU Topics")
    print("  3. All Data Science Topics")
    print("  4. All ML Topics")
    print("  5. All Programming Topics")
    print("  6. All Trending Topics")
    print("  7. All Topics")
    cat = input("\nSelect (1-7): ").strip()

    if cat == "6":
        # Live trending topics (Data API when a key is set, else RSS).
        items = workflow.fetch_trending(top_n=8)
        if not items:
            print("  No trending topics right now — aborting batch.")
            return
        topics = [it.topic_dict["title"] for it in items]
        print(f"\n  Using {len(topics)} live trending topics.")
    elif cat == "7":
        topics = list(generator.ALL_TOPICS.keys())
    elif cat in {"1", "2", "3", "4", "5"}:
        cat_key = {"1": "ai", "2": "gpu", "3": "data_science",
                   "4": "machine_learning", "5": "programming"}[cat]
        topics = list(generator.CATEGORY_MAP[cat_key].keys())
    else:
        topics = list(generator.CATEGORY_MAP["ai"].keys())

    print(f"\nDuration: 1=1min  2=3min  3=5min")
    duration = {"1": 1, "2": 3, "3": 5}.get(input("Duration (1-3): ").strip(), 3)
    workflow.run_batch(topics=topics, duration=duration, character_enabled=True)


def _list_topics(workflow):
    print("\n" + "=" * 60)
    print("TRENDING TOPICS BY FIELD (YouTube Science & Tech)")
    print("=" * 60)
    pool = workflow.fetch_trending(top_n=40) if HAS_TRENDING else []
    if not pool:
        print("  No trending topics available right now.")
        print("  Retry later or run: py workflow.py trending")
    else:
        for key, label in FIELD_MENU:
            kw = FIELD_KEYWORDS.get(key, [])
            matches = [it for it in pool if _kw_match(
                f"{it.title} {it.channel}".lower(), kw)]
            # When nothing matches the field keywords, show the strongest
            # general items so the category menu is never empty (discover()
            # applies the same fallback for field-based generation).
            shown = matches or pool[:4]
            print(f"\n{label}:")
            if shown:
                for it in shown[:4]:
                    print(f"   - {it.title[:95]}")
            else:
                print("   (no live match now — curated topics cover this field)")
    print(f"\n{'='*60}")
    print(f"Live pool: {len(pool)} trending topics | Fields: {len(FIELD_MENU)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    import sys

    def arg_lang(argv):
        """Pull --lang <code> from argv (en/hi/ne or English/Hindi/Nepali)."""
        if "--lang" in argv:
            try:
                return argv[argv.index("--lang") + 1]
            except IndexError:
                return None
        return None

    def arg_no_char(argv):
        """Return True when --no-char was passed on the command line."""
        return "--no-char" in argv

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "daily":
            workflow = DailyTechWorkflow()
            upload = "--upload" in sys.argv
            no_char = arg_no_char(sys.argv)
            workflow.run_daily(upload=upload, language=arg_lang(sys.argv),
                               character_enabled=not no_char)

        elif cmd == "topic":
            topic = sys.argv[2] if len(sys.argv) > 2 else "ai_basics"
            raw_dur = int(sys.argv[3]) if len(sys.argv) > 3 else 3
            duration = max(1, min(5, round(raw_dur / 60))) if raw_dur > 10 else raw_dur
            upload = "--upload" in sys.argv
            no_char = arg_no_char(sys.argv)
            preview = "--preview" in sys.argv or "--fast" in sys.argv
            workflow = DailyTechWorkflow()
            workflow.create_video(topic=topic, duration=duration, upload=upload,
                                  language=arg_lang(sys.argv),
                                  character_enabled=not no_char, preview=preview)

        elif cmd == "batch":
            cat = sys.argv[2] if len(sys.argv) > 2 else "ai"
            workflow = DailyTechWorkflow()
            gen = TechTopicGenerator()
            if cat == "trending":
                topics = [it.topic_dict["title"]
                          for it in workflow.fetch_trending(top_n=8)]
            elif cat in gen.CATEGORY_MAP:
                topics = list(gen.CATEGORY_MAP[cat].keys())
            else:
                topics = list(gen.ALL_TOPICS.keys())
            if not topics:
                print("  No topics for this category — aborting batch.")
            else:
                no_char = arg_no_char(sys.argv)
                workflow.run_batch(topics, language=arg_lang(sys.argv),
                                   character_enabled=not no_char)

        elif cmd == "topics":
            workflow = DailyTechWorkflow()
            _list_topics(workflow)

        elif cmd == "trending":
            workflow = DailyTechWorkflow()
            field = sys.argv[2] if len(sys.argv) > 2 else ""
            if field and field not in dict(FIELD_MENU):
                print(f"  Unknown field '{field}'. Options: {', '.join(k for k, _ in FIELD_MENU)}")
            else:
                workflow.discover_trending(top_n=12, field=field)

        elif cmd == "auto":
            workflow = DailyTechWorkflow()
            top_n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 3
            raw_dur = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            duration = max(1, min(5, round(raw_dur / 60))) if raw_dur > 10 else raw_dur
            upload = "--upload" in sys.argv
            no_char = arg_no_char(sys.argv)
            workflow.auto_make(top_n=top_n, duration=duration, upload=upload,
                               language=arg_lang(sys.argv),
                               character_enabled=not no_char)

        else:
            print("Usage:")
            print("  py workflow.py daily [--upload] [--lang en|hi|ne]")
            print("  py workflow.py topic <topic_name> [duration_seconds] [--upload] [--lang en|hi|ne]")
            print("  py workflow.py batch <category> [--lang en|hi|ne]")
            print("  py workflow.py topics")
            print("  py workflow.py trending                # list trending topics + suggestions")
            print("  py workflow.py auto [count] [minutes] [--upload] [--lang en|hi|ne]   # auto-generate from trending")
            print("  Duration: seconds (60-300) or minutes (1-5)")
            print("  Language: en (English) | hi (Hindi) | ne (Nepali)")
    else:
        interactive_mode()
