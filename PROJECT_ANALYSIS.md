================================================================================
DHARMA PROJECT ANALYSIS REPORT
Automated YouTube Video Pipeline — Core Infotech Channel
Generated: 2026-09-06
================================================================================

┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. PROJECT OVERVIEW                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

Project: Dharma — Daily Tech Video Automation Pipeline
Location: F:\Verge\Dharma
Language: Python 3.11+ (MoviePy 2.x, gTTS, Edge TTS, PIL, google-api-python-client)
Goal: Auto-generate 1-5 minute educational tech videos and upload to YouTube
Channel: Core Infotech (https://youtube.com/@CoreInfotech-w4m)
Style: Simplilearn-inspired — yellow bold text with black outline, starburst,
        deep blue/purple gradient backgrounds, floating tech icons,
        optional 2D character presenter

Pipeline Steps:
  1. Topic selection (static pool OR YouTube trending RSS/API)
  2. Research (live web search via TopicResearch, optional Ollama, optional translate)
  3. Script generation (TechTopicGenerator → VideoScript with 8 scenes)
  4. Video rendering (CinematicVideoGenerator → MP4 with character + backgrounds)
  5. Thumbnail generation (ThumbnailGenerator → JPEG with starburst + character art)
  6. YouTube upload (YouTubeUploader → video + optional thumbnail)

15 Python modules, ~555 KB total, zero external dependencies beyond pip packages.


┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. MODULE BREAKDOWN                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

workflow.py (30 KB) — Orchestrator
  - DailyTechWorkflow class: ties all modules together
  - CLI: py workflow.py daily|topic|batch|topics|trending|auto
  - Interactive mode menu (6 options)
  - Config: config.yaml (video, voice, youtube, schedule sections)
  - Features: character_on/off toggle, --no-char CLI flag, language (en/hi/ne),
              privacy (public/unlisted/private), upload toggle
  - TopicTracker integration for accounting

topics.py (84 KB) — Content Engine (largest module)
  - TechTopicGenerator class: generates complete VideoScript objects
  - 35 curated topics across 5 categories (AI, GPU, Data Science, ML, Programming)
  - ALL_TOPICS dict: title, intro, 6 points, outro per topic
  - CONTENT_LIBRARY (from content_library.py): 210 topic-specific entries (204 KB)
  - Difficulty levels: beginner (500-1000w), intermediate (500-1000w),
                       advanced (500-1000w) — all verified in range
  - _expand_point_technical(): library lookup → topic_dict intro → research_text
                               → fallback (4-tier content resolution)
  - Generates: 8 scenes (intro + 6 points + outro), proper storytelling flow
  - Scripts saved as JSON to output/scripts/

content_library.py (204 KB) — Topic-Specific Content Database
  - 210 entries, one per topic+point combination
  - Each entry: "{Title}. {topic-specific content paragraph}"
  - Covers all 35 topics × 6 points = 210 entries
  - Sourced from real tech knowledge (not generic filler)
  - Module-level dict, imported by topics.py

video.py (85 KB) — Video Renderer (second-largest module)
  - CinematicVideoGenerator: renders MP4 from Scene list
  - VideoConfig: width, height, fps, ken_burns_zoom, character_enabled, etc.
  - Scene dataclass: text, voiceover, duration, font, color, animation,
                      character_position, gesture, diagram_type/data/position,
                      show_character, enter_from, exit_to, etc.
  - 6 technical diagram renderers: flow_chart, architecture, cnn_layers,
                                  network_graph, comparison, data_pipeline
  - CharacterRenderer2D integration (from character.py)
  - TechBackground: procedural circuit-pattern backgrounds
  - FloatingIconOverlay: orbiting brain/robot/gear/lightbulb/puzzle/database
  - Particles: ambient floating particles for depth
  - Lip-sync via lip_sync.py
  - _alpha_composite: proper RGBA compositing
  - generate_video(): propagates character_enabled → scene.show_character

character.py (37 KB) — 2D Character Presenter
  - CharacterRenderer2D: renders character frames with poses, gestures, emotions
  - Pose library (pose_lib.py): 10+ poses
  - Lip-sync shapes (lip_sync.py): phoneme-based mouth shapes
  - Gestures: explaining, pointing, thinking, welcoming, etc.
  - Emotions: confident, happy, serious, etc.
  - Idle animation + gaze control + blink

upload.py (21 KB) — YouTube Upload
  - YouTubeUploader: OAuth2 authentication, video upload, thumbnail set
  - ThumbnailGenerator: 1280x720 JPEG, Simplilearn style
    - Deep blue/purple gradient background
    - Starburst behind title
    - Yellow bold text with black outline (Montserrat Bold)
    - Optional character art on right side
    - Corner accents, bottom accent bar
    - Rounded pill subtitle
  - SCOPES: youtube.upload + youtube.readonly
  - Multi-port OAuth fallback (8081-8089, skip 8080 which Hermes owns)
  - Thumbnail upload: separate API call (requires extra permission)

youtube_trending.py (9 KB) — Trending Discovery
  - Two-tier: YouTube Data API v3 (exact chart) OR RSS feeds (free, no key)
  - RSS: 8 hardcoded tech channel IDs (MKBHD, 3Blue1Brown, freeCodeCamp, etc.)
  - keyword_score(): ranks videos by tech keyword match
  - build_topic_dict(): auto-generates {title, intro, points, outro} from title
  - discover(): returns sorted list of TrendingTopic objects
  - PROBLEM: 2 of 8 channel IDs are dead (404), 1 returns consumer tech not GPU

topic_tracker.py (4 KB) — Analytics/Accounting
  - Tracks topic usage, video status, YouTube URLs
  - CSV-based storage
  - Prevents duplicate topic selection

stockbg.py (12 KB) — Pexels Stock Backgrounds
  - PexelsBackground: fetches real photos via Pexels API
  - Configurable API key, cache directory
  - Fallback to procedural if key missing

research.py (7 KB) — Web Research
  - TopicResearch: live web search for topic content
  - Optional: feeds real content into script generation
  - Currently returns no results in user's environment (network/API issue)

translate.py (7 KB) — Voiceover Translation
  - normalize_lang(), lang_label(), translate_voiceover()
  - Supports en/hi/ne (English/Hindi/Nepali)
  - Uses gTTS or Edge TTS for voice generation

gui.py (21 KB) — GUI Frontend (separate from CLI)
  - Graphical interface for the pipeline
  - Likely Tkinter/PyQt based

ollama_gen.py (6 KB) — Optional Ollama Integration
  - OllamaClient: connects to local Ollama for LLM-based content generation
  - Disabled by default (needs local Ollama running)

lip_sync.py (16 KB) — Lip Sync Engine
  - Synthesizes mouth shapes from voiceover text timing
  - Used by video.py per-frame rendering

pose_lib.py (20 KB) — Character Pose Library
  - Defines character poses, gestures, animations
  - Used by character.py renderer

config.yaml (not read, but referenced) — Pipeline Configuration
  - video: width=1920, height=1080, fps=30, character_enabled=True
  - voice: language=en
  - youtube: client_secret.json, token.json, default_category=28, privacy=public
  - schedule: daily_upload_time=10:00, auto_upload=False


┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. WHAT'S WORKING WELL                                                     │
└─────────────────────────────────────────────────────────────────────────────┘

✓ Pipeline runs end-to-end: topic → script → video → thumbnail → upload
✓ YouTube upload works (video uploaded successfully: K4S7DyL2ZJg)
✓ OAuth2 authentication working (token refreshed silently)
✓ Video renders with character, backgrounds, diagrams, particles
✓ Thumbnail generates with starburst, bold yellow text, character art
✓ Content library has 210 topic-specific entries (no generic fallback for known topics)
✓ All 35 topics produce 500-1000 word scripts across 3 difficulty levels
✓ Storytelling structure: intro → 6 points → outro
✓ Technical diagram overlays working (6 diagram types with alpha composite)
✓ Character on/off toggle implemented and functional
✓ Multi-language support (en/hi/ne)
✓ Topic tracker prevents duplicates
✓ Error handling: graceful degradation when optional modules missing
✓ Configurable via config.yaml

Recent successful upload:
  Video: "Did OpenAI actually build AGI? GPT-6 Astra first look"
  URL: https://www.youtube.com/watch?v=K4S7DyL2ZJg
  Length: 6:53, Category: Science & Technology, Privacy: Public
  Channel: Core Infotech


┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. CRITICAL ISSUES (NEED FIXING)                                           │
└─────────────────────────────────────────────────────────────────────────────┘

ISSUE A — Trending RSS Channel IDs are STALE (youtube_trending.py:33-42)
  Severity: HIGH (breaks trending for GPU/ML/programming categories)
  Details:
    - UC8butISFwT-Wl7EV0hUK0BQ (freeCodeCamp) → 404 Not Found
    - UC2D2CMWXMOVWx7giW1n3LIg (Lex Fridman) → 404 Not Found
    - Only 6 of 8 channels respond; 1 (MKBHD) returns consumer tech not GPU
  Impact: When you select cuda_vs_rocm (GPU category), trending returns
          irrelevant topics because no GPU channel is reachable
  Fix: Replace dead IDs with working GPU/tech channels


ISSUE B — Trending Category Mismatch (workflow.py + youtube_trending.py)
  Severity: MEDIUM
  Details:
    - When no channel matches the category, pipeline falls back to "best available"
    - This can pick a Python tutorial for a GPU topic request
    - User expected cuda_vs_rocm video, got FastAPI tutorial
  Fix: When no category match, fall back to static topic in same category
        rather than random trending video from wrong category


ISSUE C — Thumbnail Upload Fails (upload.py)
  Severity: MEDIUM (cosmetic but hurts CTR)
  Details:
    - Video upload works: HttpError 403 "doesn't have permissions to upload
      and set custom video thumbnails"
    - The youtube.upload scope does NOT include thumbnail.set permission
    - Need youtube.thumbnail scope or separate OAuth consent
  Impact: Thumbnail stays as YouTube auto-generated (lower click-through rate)
  Fix: Add "https://www.googleapis.com/auth/youtube.thumbnail" to SCOPES
        OR accept that thumbnail upload requires manual step


ISSUE D — Research Module Returns No Results (research.py)
  Severity: LOW-MEDIUM
  Details:
    - "Research: no results (continuing with template)" in every run
    - Script falls back to generic topic_dict content instead of fresh research
    - For trending topics, this means the video uses auto-generated generic
      content rather than real researched content
  Fix: Check research.py network/setup; or accept RSS-based content as sufficient


ISSUE E — Narration for Trending Topics Was Generic (topics.py, NOW FIXED)
  Severity: FIXED
  Details:
    - Trending topics not in CONTENT_LIBRARY got identical generic paragraphs
    - Now fixed: _adapt_topic_content() rotates different intro sentences per point
    - _extract_research_sentence() uses research text when available
  Status: ✅ Resolved — next trending video will have varied per-point content


ISSUE F — Character Still Showing When "No" Selected (video.py, NOW FIXED)
  Severity: FIXED
  Details:
    - video_config.character_enabled=False didn't propagate to scene.show_character
    - Render code checked scene.show_character (per-scene), not config
  Fix: generate_video() now propagates config → each scene before rendering
  Status: ✅ Resolved


┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. SUBSCRIBER GROWTH STRATEGY                                               │
└─────────────────────────────────────────────────────────────────────────────┘

Current State:
  - Channel: Core Infotech
  - Recent video: "Did OpenAI actually build AGI? GPT-6 Astra first look"
  - Length: 6:53 (good — over 5 minutes aids monetization)
  - Category: Science & Technology (#28)
  - Uploaded: 2026-09-06
  - Views: 0 (new video, too early to judge)

Growth Levers (ranked by impact):

GROWTH LEVER 1 — Upload CONSISTENCY (highest impact, fully within control)
  Current: Ad-hoc uploads (user triggers manually)
  Target: Daily at 10:00 AM (configurable in config.yaml)
  Action:
    - Set "auto_upload: true" in config.yaml schedule section
    - Run: py workflow.py daily --upload
    - Or use schedule_daily() for automated daily execution
  Why: YouTube's algorithm heavily favors channels that upload consistently.
        Daily videos = 30+ videos/month = compounding visibility.
  Note: Schedule requires the machine to be ON at 10:00. For true automation,
        run on a server/always-on machine, or use a cloud CI runner.

GROWTH LEVER 2 — Thumbnail Click-Through Rate (CTR)
  Current: Thumbnail upload fails (403 error), YouTube uses auto-generated
  Target: Custom thumbnail uploaded successfully for every video
  Action:
    - Fix thumbnail permission scope (see Issue C)
    - The current thumbnail DESIGN is good (Simplilearn-style, yellow text,
      starburst, character art) — keep it
    - A/B test: try different accent colors, character positioning
    - Ensure title on thumbnail is readable at 10% size (mobile)
  Why: CTR is the #1 driver of impressions → views. A 5% CTR video gets
        5x more views than a 1% CTR video from the same impressions.
  Benchmark: Tech education channels target 4-8% CTR

GROWTH LEVER 3 — Video Titles (search + suggested traffic)
  Current: Auto-generated titles like "Did OpenAI actually build AGI? GPT-6 Astra first look"
  Target: Titles that combine curiosity + search keywords
  Action:
    - Current auto-title format from build_topic_dict():
      "What {topic} is" / "How {topic} works" — these are OK but generic
    - Better: include power words + specific benefit
      e.g. "GPT-6 Astra Explained: Is This Actually AGI?" instead of
           "Did OpenAI actually build AGI? GPT-6 Astra first look"
    - Add to topics.py: title templates that include "Explained", "Guide",
      "Masterclass", "Complete Guide", "Why X Matters"
  Why: Titles drive both search (SEO) and suggested (browse) traffic.
        "Explained" and "Complete Guide" perform well in tech education.

GROWTH LEVER 4 — Video Length & Retention
  Current: 6:53 for recent video (good length)
  Target: 5-8 minute videos consistently
  Action:
    - Current script: 8 scenes, ~800-900 words → ~5-7 minutes at normal pace
    - Keep target_duration=3 (minutes) for 5-8 min videos
    - Avoid going under 4 minutes (low watch time) or over 10 (drop-off)
  Why: 5-8 min videos earn more ad revenue and signal engagement to YouTube.

GROWTH LEVER 5 — Category & Tags Optimization
  Current: Category 28 (Science & Technology), auto-generated tags
  Target: More specific category + better tags
  Action:
    - Category 28 is correct for tech education
    - Tags from _generate_tags(): base + category + specific title words
    - Add trending keywords from the video title to tags
    - Consider "Howto & Style" (27) for tutorial-style content
  Why: Proper categorization helps YouTube show videos to the right audience.

GROWTH LEVER 6 — Topic Selection Strategy
  Current: Mixed — trending RSS (when available) or static topic pool
  Target: Data-driven topic selection
  Action:
    - Use YouTube Data API key (free, $0) for REAL trending data instead of
      RSS fallback (which has dead channels and category mismatch)
    - Get a YouTube Data API v3 key from Google Cloud Console (free tier: 10,000
      units/day — more than enough for trending queries)
    - Store successful topic→performance mappings in topic_tracker
    - After 30+ videos, analyze which topics get most views → double down
  Why: Trending topics get more impressions. Data-driven topics outperform guesses.

GROWTH LEVER 7 — Audience Retention Hooks
  Current: Script has intro + 6 points + outro (good structure)
  Target: Stronger opening hook in first 15 seconds
  Action:
    - First scene (intro) should state the PROBLEM before the solution
    - Current intro: "Artificial Intelligence is transforming every industry..."
      → Better: "Every day, AI makes another human job obsolete. But what if
                I told you most people still don't understand what AI actually is?"
    - Add "hook" variant to topic intros in ALL_TOPICS
  Why: First 30 seconds determine if viewer stays. Retention > 50% at 30s
        triggers YouTube to push the video to more people.

GROWTH LEVER 8 — End Screen & Playlist Strategy
  Current: Not implemented
  Target: Link to related videos + subscribe CTA
  Action:
    - After upload, use YouTube API to add end screen elements
    - Create a "Tech Basics" playlist and add each video to it
    - End screen: "Watch Next" (related video) + "Subscribe" button
  Why: End screens drive session time → more views per viewer → algorithm boost.

GROWTH LEVER 9 — Community & Engagement
  Current: Description has subscribe/like/comment prompts
  Target: Active community building
  Action:
    - Pin a comment on each video asking a question related to the topic
    - Reply to every comment in the first 24 hours
    - Create community posts (polls, updates) between uploads
  Why: Comments signal engagement. Early replies boost comment velocity.


┌─────────────────────────────────────────────────────────────────────────────┐
│ 6. PRIORITIZED ACTION PLAN                                                 │
└─────────────────────────────────────────────────────────────────────────────┘

IMMEDIATE (do this week):
  1. Fix dead YouTube RSS channel IDs in youtube_trending.py
     - Replace UC8butISFwT-Wl7EV0hUK0BQ (dead) with working channel ID
     - Replace UC2D2CMWXMOVWx7giW1n3LIg (dead) with working channel ID
     - Suggested GPU channels to add: Hardware Unboxed, Gamers Nexus,
       TechPowerUp, Level1Techs, JayzTwoCents
  2. Fix trending fallback: when no category match, use static topic
     in the SAME category rather than wrong-category trending video
  3. Fix thumbnail upload permission (add youtube.thumbnail scope)
  4. Set up YouTube Data API key (free) for real trending data

SHORT-TERM (this month):
  5. Set auto_upload: true + schedule daily uploads
  6. Improve title templates in build_topic_dict() for better CTR
  7. Strengthen intro hooks in ALL_TOPICS (problem-first openings)
  8. Add end screen + playlist automation via YouTube API

MEDIUM-TERM (next 3 months):
  9. After 30 videos: analyze topic_tracker data → find top-performing topics
  10. Double down on top 3 categories by topic count
  11. A/B test thumbnail styles (color, character presence, text placement)
  12. Engage with comments daily (15 min after each upload)


┌─────────────────────────────────────────────────────────────────────────────┐
│ 7. CHANNEL HEALTH CHECK                                                    │
└─────────────────────────────────────────────────────────────────────────────┘

✓ Channel exists and is active (video uploaded 2026-09-06)
✓ Video is public, properly categorized (Science & Technology)
✓ Video length is good (6:53 — monetization-eligible)
✓ Thumbnail exists on video page (auto-generated by YouTube since custom failed)
✓ Description is populated (auto-generated from script)
✓ Tags are set (auto-generated)
⚠  Custom thumbnail not set (thumbnail upload failed — fix needed)
⚠  Only 1 video so far (need consistency for algorithm to learn)
⚠  No playlist structure yet
⚠  No end screens configured
⚠  No community posts or comment engagement visible yet

Overall: Channel is functional but early-stage. The pipeline works — the
          bottleneck is consistency and thumbnail quality, not technical capability.


┌─────────────────────────────────────────────────────────────────────────────┐
│ 8. TECHNICAL DEBT (non-blocking, note for later)                          │
└─────────────────────────────────────────────────────────────────────────────┘

- research.py returns no results — needs debugging or replacement
- youtube_trending.py RSS fallback is fragile (hardcoded channel IDs)
- topic_tracker.csv could grow large over time — consider DB or rotation
- gui.py exists but its relationship to workflow.py is unclear
- No end-to-end test suite (manual testing only)
- No CI/CD or deployment automation
- Content library generation scripts are in temp dirs (not in project)
- No video performance analytics integration (views, watch time, CTR tracking)


================================================================================
END OF REPORT
================================================================================
