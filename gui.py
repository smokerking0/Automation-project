"""
Daily Tech Video Automation GUI
Simple tkinter interface for video generation and YouTube upload.
"""

import os
import sys
import re
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from datetime import datetime

from video import CinematicVideoGenerator, VideoConfig, Scene
from topics import TechTopicGenerator, VideoScript
from upload import YouTubeUploader, ThumbnailGenerator

try:
    from translate import normalize_lang, lang_label, translate_voiceover, translate_text
    HAS_TRANSLATE = True
except ImportError:
    HAS_TRANSLATE = False

try:
    from growth import apply_growth, apply_hook
    HAS_GROWTH = True
except ImportError:
    HAS_GROWTH = False

try:
    from ollama_gen import OllamaClient
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

BG = "#1e1e2e"
FG = "#cdd6f4"
ACCENT = "#89b4fa"


class VideoApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Daily Tech Video Maker")
        self.root.geometry("640x780")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        self.topic_gen = TechTopicGenerator()
        self.thumb_gen = ThumbnailGenerator()
        self.is_running = False
        self.cfg = {}
        self.ollama = None
        self.stock_background = None
        self.stock_status = "Pexels key not set (procedural backgrounds)"

        try:
            import yaml
            if os.path.exists("config.yaml"):
                with open("config.yaml", "r") as f:
                    self.cfg = yaml.safe_load(f) or {}
        except Exception:
            self.cfg = {}

        # Initialize Ollama if available
        try:
            ollama_cfg = self.cfg.get("ollama", {}) or {}
            if HAS_OLLAMA and ollama_cfg.get("enabled", False):
                self.ollama = OllamaClient(
                    host=ollama_cfg.get("host", "http://localhost:11434"),
                    model=ollama_cfg.get("model", "llama3.1:70b")
                )
                self.topic_gen.ollama = self.ollama
        except Exception:
            pass

        # Real stock backgrounds (Pexels) when a key is configured
        try:
            stock_cfg = self.cfg.get("stock", {}) or {}
            if stock_cfg.get("api_key"):
                from stockbg import PexelsBackground
                self.stock_background = PexelsBackground(
                    api_key=stock_cfg["api_key"],
                    width=1280, height=720,
                    cache_dir=stock_cfg.get("cache_dir", "output/temp/stock"),
                )
                ok = "available" if self.stock_background.available else "key set but unavailable"
                self.stock_status = f"Pexels background: {ok}"
        except Exception:
            pass

        self._build_ui()

    def _clean_prompt(self, text: str) -> str:
        text = re.sub(r"[#*_`>]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:120]

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 14, "bold"))
        style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("TCheckbutton", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#313244", background=ACCENT)
        style.configure("TRadiobutton", background=BG, foreground=FG, font=("Segoe UI", 10))

        main = tk.Frame(self.root, bg=BG, padx=15, pady=15)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Daily Tech Video Maker", style="Title.TLabel").pack(pady=(0, 10))

        row1 = tk.Frame(main, bg=BG)
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="Topic:", width=10).pack(side="left")
        self.topic_var = tk.StringVar(value="ai_basics")
        self.topic_entry = ttk.Entry(row1, textvariable=self.topic_var, width=45)
        self.topic_entry.pack(side="left", padx=5)

        row_prompt = tk.Frame(main, bg=BG)
        row_prompt.pack(fill="x", pady=4)
        ttk.Label(row_prompt, text="Prompt:", width=10).pack(side="left")
        self.prompt_text = tk.Text(
            row_prompt, height=4, width=46, wrap="word",
            bg="#111827", fg=FG, insertbackground=FG, relief="flat"
        )
        self.prompt_text.pack(side="left", padx=5)

        row2 = tk.Frame(main, bg=BG)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Duration:", width=10).pack(side="left")
        self.duration_var = tk.StringVar(value="1 min")
        dur_frame = tk.Frame(row2, bg=BG)
        dur_frame.pack(side="left")
        for val in ["1 min", "2 min", "3 min", "5 min"]:
            ttk.Radiobutton(dur_frame, text=val, variable=self.duration_var, value=val).pack(side="left", padx=4)

        row3 = tk.Frame(main, bg=BG)
        row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="Style:", width=10).pack(side="left")
        self.style_var = tk.StringVar(value="educational")
        style_menu = ttk.Combobox(row3, textvariable=self.style_var, width=20, state="readonly",
                                  values=["educational", "listicle", "tutorial", "deep_dive"])
        style_menu.pack(side="left", padx=5)

        row3b = tk.Frame(main, bg=BG)
        row3b.pack(fill="x", pady=4)
        ttk.Label(row3b, text="Language:", width=10).pack(side="left")
        self.language_var = tk.StringVar(value="English")
        lang_options = ["English", "Hindi", "Nepali"]
        lang_menu = ttk.Combobox(row3b, textvariable=self.language_var, width=20, state="readonly",
                                 values=lang_options)
        lang_menu.pack(side="left", padx=5)
        lang_hint = ttk.Label(row3b, text="Voice audio only — on-screen text stays English", foreground="#a6adc8")
        lang_hint.pack(side="left")

        row4 = tk.Frame(main, bg=BG)
        row4.pack(fill="x", pady=4)
        ttk.Label(row4, text="Text Color:", width=10).pack(side="left")
        self.color_var = tk.StringVar(value="#FFFFFF")
        color_menu = ttk.Combobox(row4, textvariable=self.color_var, width=12, state="readonly",
                                  values=["#FFFFFF", "#FFD700", "#00FF88", "#89b4fa", "#f38ba8", "#fab387"])
        color_menu.pack(side="left", padx=5)

        row5 = tk.Frame(main, bg=BG)
        row5.pack(fill="x", pady=4)
        ttk.Label(row5, text="", width=10).pack(side="left")
        self.character_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row5, text="Show presenter character", variable=self.character_var).pack(side="left")

        row5b = tk.Frame(main, bg=BG)
        row5b.pack(fill="x", pady=4)
        ttk.Label(row5b, text="Character:", width=10).pack(side="left")
        self.character_mode_var = tk.StringVar(value="alien")
        char_menu = ttk.Combobox(
            row5b, textvariable=self.character_mode_var, width=20, state="readonly",
            values=["alien", "it_guy"]
        )
        char_menu.pack(side="left", padx=5)

        row5c = tk.Frame(main, bg=BG)
        row5c.pack(fill="x", pady=4)
        ttk.Label(row5c, text="Visual:", width=10).pack(side="left")
        self.visual_mode_var = tk.StringVar(value="cinematic_realistic")
        visual_menu = ttk.Combobox(
            row5c, textvariable=self.visual_mode_var, width=20, state="readonly",
            values=["cinematic_realistic", "cinematic_tech"]
        )
        visual_menu.pack(side="left", padx=5)

        row5d = tk.Frame(main, bg=BG)
        row5d.pack(fill="x", pady=4)
        ttk.Label(row5d, text="Background:", width=10).pack(side="left")
        self.bg_mode_var = tk.StringVar(value="auto")
        bg_menu = ttk.Combobox(
            row5d, textvariable=self.bg_mode_var, width=20, state="readonly",
            values=["auto", "stock", "procedural"]
        )
        bg_menu.pack(side="left", padx=5)
        bg_status = ttk.Label(row5d, text=self.stock_status, foreground="#a6adc8")
        bg_status.pack(side="left")

        row6 = tk.Frame(main, bg=BG)
        row6.pack(fill="x", pady=4)
        ttk.Label(row6, text="", width=10).pack(side="left")
        self.upload_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row6, text="Upload to YouTube (public)", variable=self.upload_var).pack(side="left")

        sep = ttk.Separator(main, orient="horizontal")
        sep.pack(fill="x", pady=8)

        btn_frame = tk.Frame(main, bg=BG)
        btn_frame.pack(fill="x", pady=4)

        self.generate_btn = ttk.Button(btn_frame, text="GENERATE VIDEO", command=self._start_generate)
        self.generate_btn.pack(side="left", padx=(0, 10))

        self.trending_btn = ttk.Button(btn_frame, text="Trending Topics", command=self._start_trending)
        self.trending_btn.pack(side="left", padx=(0, 10))

        self.browse_btn = ttk.Button(btn_frame, text="Open Output", command=self._open_output)
        self.browse_btn.pack(side="left")

        self.progress = ttk.Progressbar(main, mode="determinate", length=550)
        self.progress.pack(fill="x", pady=(8, 4))

        self.status_var = tk.StringVar(value="Ready. Enter a topic and click Generate.")
        self.status_label = ttk.Label(main, textvariable=self.status_var, wraplength=550)
        self.status_label.pack(fill="x", pady=4)

        topics_frame = tk.Frame(main, bg=BG)
        topics_frame.pack(fill="x", pady=(4, 0))
        header = tk.Frame(topics_frame, bg=BG)
        header.pack(fill="x")
        self.quick_label = ttk.Label(header, text="Trending Topics (tap to load):",
                                     font=("Segoe UI", 9, "bold"))
        self.quick_label.pack(side="left")
        refresh_btn = ttk.Button(header, text="Refresh", width=9, command=self._refresh_quick)
        refresh_btn.pack(side="right")

        self.quick_frame = tk.Frame(topics_frame, bg=BG)
        self.quick_frame.pack(fill="x")

        self.static_quick = [
            "What is Artificial Intelligence?",
            "How ChatGPT Actually Works",
            "AI Agents - The Next Revolution",
            "GPUs Explained",
            "Python Programming Basics",
            "Machine Learning for Beginners",
        ]
        self._populate_quick(self.static_quick)
        # Refresh with live trending topics on launch (background, non-blocking).
        self._refresh_quick()

    def _populate_quick(self, titles):
        for child in self.quick_frame.winfo_children():
            child.destroy()
        for label in titles:
            t = label.strip()
            if not t:
                continue
            btn = ttk.Button(self.quick_frame, text=t[:26], width=18,
                             command=lambda x=t: self._set_topic(x))
            btn.pack(side="left", padx=2, pady=2, anchor="n")

    def _refresh_quick(self):
        self.quick_label.configure(text="Loading trending topics...")
        threading.Thread(target=self._load_quick, daemon=True).start()

    def _load_quick(self):
        titles = None
        try:
            from youtube_trending import YouTubeTrending
            yt_cfg = self.cfg.get("youtube", {}) or {}
            api_key = yt_cfg.get("data_api_key", "") or ""
            items = YouTubeTrending(api_key=api_key).discover(top_n=12)
            if items:
                titles = [it.topic_dict["title"] for it in items[:6]]
        except Exception as e:
            print("Quick topics refresh failed:", e)
        if not titles:
            titles = self.static_quick
        self.root.after(0, lambda: self._apply_quick(titles))

    def _apply_quick(self, titles):
        self._populate_quick(titles)
        if titles != self.static_quick:
            self.quick_label.configure(text="Trending Topics (tap to load):")
        else:
            self.quick_label.configure(text="Trending Topics (offline fallback):")

    def _set_topic(self, topic):
        self.topic_var.set(topic)

    def _open_output(self):
        path = os.path.abspath("output/videos")
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showinfo("Info", "No output folder yet. Generate a video first.")

    def _start_generate(self):
        if self.is_running:
            return
        self.is_running = True
        self.generate_btn.configure(state="disabled")
        self.progress["value"] = 0
        thread = threading.Thread(target=self._generate, daemon=True)
        thread.start()

    def _generate(self):
        try:
            topic = self.topic_var.get().strip()
            prompt = self.prompt_text.get("1.0", "end").strip()
            if not topic and not prompt:
                self._done("Please enter a topic.")
                return

            topic_source = self._clean_prompt(prompt) if prompt else topic
            file_slug = re.sub(r"[^a-z0-9]+", "_", topic_source.lower()).strip("_")[:60] or "video"
            dur_text = self.duration_var.get()
            duration = int(dur_text.split()[0])
            style = self.style_var.get()
            upload = self.upload_var.get()
            show_char = self.character_var.get()
            character_mode = self.character_mode_var.get()
            visual_mode = self.visual_mode_var.get()
            text_color = self.color_var.get()
            lang = normalize_lang(self.language_var.get()) if HAS_TRANSLATE else "en"
            lang_name = lang_label(lang) if HAS_TRANSLATE else "English"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            for d in ["output/scripts", "output/videos", "output/thumbnails", "output/temp"]:
                Path(d).mkdir(parents=True, exist_ok=True)

            self._update_status("Generating script...", 5)
            script = self.topic_gen.generate_script(
                topic=topic_source, style=style, target_duration=duration
            )
            if HAS_TRANSLATE and lang != "en":
                self._update_status(f"Translating narration to {lang_name}…", 10)
                script = translate_voiceover(script, lang)
            script_path = f"output/scripts/{file_slug}_{timestamp}.json"
            self.topic_gen.save_script(script, script_path)

            self._update_status(f"Generating video: {script.title}...", 15)
            config = VideoConfig(
                voice_lang=lang,
                presenter_avatar_path=((self.cfg.get("presenter", {}) or {}).get("avatar_path", "") or ""),
                presenter_avatar_data=((self.cfg.get("presenter", {}) or {}).get("avatar_data_uri", "") or ""),
                stock_background=self.stock_background,
                background_mode=self.bg_mode_var.get(),
            )
            generator = CinematicVideoGenerator(config)

            scenes = []
            for s in script.scenes:
                if isinstance(s, Scene):
                    s.show_character = show_char
                    s.character_type = character_mode
                    s.visual_style = visual_mode
                    s.text_color = text_color
                    s.overlay_opacity = 0.55
                    if prompt:
                        s.background_prompt = prompt
                    scenes.append(s)
                else:
                    scenes.append(Scene(
                        text=s["text"],
                        voiceover=s.get("voiceover", s["text"]),
                        duration=s.get("duration", 5.0),
                        font_size=s.get("font_size", 70),
                        text_color=text_color,
                        text_position=s.get("text_position", "center"),
                        animation=s.get("animation", "fade"),
                        overlay_opacity=0.55,
                        show_character=show_char,
                        character_type=character_mode,
                        visual_style=visual_mode,
                        background_prompt=topic_source,
                    ))

            lang_tag = f"_{lang}" if lang != "en" else ""
            video_path = f"output/videos/{file_slug}{lang_tag}_{timestamp}.mp4"

            # Growth boosts — cold-open hook, mid-CTA, recap, scene variation
            if HAS_GROWTH and len(scenes) >= 3:
                try:
                    apply_growth(scenes, topic_source, lang, "",
                                 translate_text if HAS_TRANSLATE else None)
                    apply_hook(scenes, topic_source, "", lang,
                               translate_text if HAS_TRANSLATE else None)
                except Exception as exc:
                    print(f"  [growth] skipped: {exc}")

            self._update_status("Rendering scenes...", 25)
            generator.generate_video(scenes, video_path)
            self._update_status("Video rendered!", 70)

            self._update_status("Generating thumbnail...", 75)
            thumb_path = f"output/thumbnails/{file_slug}{lang_tag}_{timestamp}.jpg"
            self.thumb_gen.generate_thumbnail(title=script.title, output_path=thumb_path)

            youtube_url = None
            if upload:
                self._update_status("Uploading to YouTube...", 85)
                try:
                    uploader = YouTubeUploader()
                    response = uploader.upload_video(
                        video_path=video_path,
                        title=script.title,
                        description=script.description,
                        tags=script.tags,
                        category_id="28",
                        privacy_status="public",
                        thumbnail_path=thumb_path
                    )
                    youtube_url = f"https://www.youtube.com/watch?v={response['id']}"
                except Exception as e:
                    self._update_status(f"Upload failed: {e}", 95)
                    self.root.after(0, lambda: messagebox.showerror("Upload Error", str(e)))

            self._update_status("Done!", 100)
            msg = f"Video: {video_path}\nThumbnail: {thumb_path}"
            if youtube_url:
                msg += f"\nYouTube: {youtube_url}"
            self.root.after(0, lambda: messagebox.showinfo("Success", msg))

        except Exception as e:
            err_text = f"{type(e).__name__}: {e}"
            trace_text = traceback.format_exc()
            print(trace_text)
            self._update_status(f"Error: {err_text}", 0)
            self.root.after(0, lambda: messagebox.showerror("Error", err_text))
        finally:
            self.is_running = False
            self.root.after(0, lambda: self.generate_btn.configure(state="normal"))

    def _start_trending(self):
        if self.is_running:
            return
        self.is_running = True
        self.trending_btn.configure(state="disabled")
        self._update_status("Fetching trending topics...", 0)
        thread = threading.Thread(target=self._fetch_trending, daemon=True)
        thread.start()

    def _fetch_trending(self):
        items = []
        try:
            from youtube_trending import YouTubeTrending
            yt_cfg = self.cfg.get("youtube", {}) or {}
            api_key = yt_cfg.get("data_api_key", "") or ""
            items = YouTubeTrending(api_key=api_key).discover(top_n=12)
        except Exception as e:
            print(traceback.format_exc())
            self.root.after(0, lambda: self._update_status(f"Trending failed: {e}", 0))
            self.root.after(0, lambda: messagebox.showerror("Trending", str(e)))
        self.root.after(0, lambda: self._show_trending_picker(items))

    def _show_trending_picker(self, items):
        self.is_running = False
        self.trending_btn.configure(state="normal")
        if not items:
            self._update_status("No trending topics found.", 0)
            return
        win = tk.Toplevel(self.root)
        win.title("Trending Topics - pick a video idea")
        win.configure(bg=BG)
        win.geometry("640x480")
        lb = tk.Listbox(
            win, bg="#111827", fg=FG, selectbackground=ACCENT,
            selectforeground="#111827", font=("Segoe UI", 10), activestyle="none"
        )
        for it in items:
            channel = f"  [{it.channel}]" if it.channel else ""
            lb.insert("end", f"{it.title}{channel}")
        lb.pack(fill="both", expand=True, padx=10, pady=10)

        def use_selected():
            sel = lb.curselection()
            if not sel:
                return
            topic = items[sel[0]]
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", topic.title)
            win.destroy()
            self._update_status(f"Topic ready: {self._clean_prompt(topic.title)}  -  click GENERATE", 0)

        use_btn = ttk.Button(win, text="Use Selected Idea", command=use_selected)
        use_btn.pack(pady=(0, 10))

    def _update_status(self, text, progress_val):
        self.root.after(0, lambda: self.status_var.set(text))
        self.root.after(0, lambda: self.progress.configure(value=progress_val))

    def _done(self, msg):
        self.root.after(0, lambda: self.status_var.set(msg))
        self.is_running = False
        self.root.after(0, lambda: self.generate_btn.configure(state="normal"))


def main():
    root = tk.Tk()
    VideoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
