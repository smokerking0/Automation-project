"""
YouTube Video Uploader
Handles authentication and video upload to YouTube.
"""

import os
import math
import json
from pathlib import Path
from typing import Optional, Dict, Any, Union
from datetime import datetime

from PIL import Image as PILImage
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


def _client_secret_ok(path: str) -> Optional[dict]:
    """Pre-flight probe of the OAuth client secret file WITHOUT exposing it.

    Calls Google's token endpoint with a dummy refresh token. The response
    classifies the client:
      - requests 'invalid_client' / 'deleted_client' -> client is unusable
      - anything else (e.g. invalid_grant)            -> client is valid
    Returns a dict with a human-readable status; 'ok' True when the client
    secret is accepted. Never prints the secret.
    """
    import requests
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        obj = data.get("installed") or data.get("web") or data
        cid = obj.get("client_id", "")
        csec = obj.get("client_secret", "")
        if not cid or not csec:
            return {"ok": False, "error": "missing client_id/client_secret fields"}
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": cid,
                "client_secret": csec,
                "refresh_token": "invalid_dummy_probe",
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        err = resp.json().get("error")
        if err in ("invalid_client", "deleted_client"):
            return {"ok": False, "error": err}
        return {"ok": True, "error": err}
    except Exception as e:
        return {"ok": None, "error": f"probe failed: {e}"}


# YouTube API scopes
# NOTE: youtube.thumbnail is NOT a valid OAuth scope (Google rejects it with
# 400 invalid_scope). Thumbnails are uploaded via the videos.insert endpoint
# or the thumbnails.set endpoint which only requires youtube.upload scope.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class YouTubeUploader:
    """Handles YouTube video uploads."""
    
    def __init__(
        self,
        client_secrets_path: str = "client_secret.json",
        credentials_path: str = "token.json"
    ):
        """
        Initialize YouTube uploader.
        
        Args:
            client_secrets_path: Path to OAuth2 client secrets JSON
            credentials_path: Path to store/retrieve user credentials
        """
        self.client_secrets_path = client_secrets_path
        self.credentials_path = credentials_path
        self.service = None
        self._authenticate()
    
    def _authenticate(self):
        """Authenticate with YouTube API."""
        creds = None

        # Load existing credentials (JSON format)
        if os.path.exists(self.credentials_path):
            try:
                creds = Credentials.from_authorized_user_file(
                    self.credentials_path, SCOPES
                )
            except (json.JSONDecodeError, Exception) as e:
                print(f"  Warning: Could not load credentials: {e}")
                # Try to migrate from pickle format
                creds = self._try_migrate_pickle()
                if creds:
                    # Save in new JSON format
                    self._save_credentials(creds)

        # Try to refresh if expired (silent — saves browser round-trip)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("  Refreshed expired credentials (silent).")
            except Exception as refresh_exc:
                # Refresh failed: token revoked/expired past the grace window.
                # Log clearly so the user knows what happened, then fall through
                # to full browser OAuth below.
                print(f"  Credential refresh failed: {refresh_exc}")
                print("  (The saved token has expired or been revoked by Google. Re-authentication required.)")
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(self.client_secrets_path):
                raise FileNotFoundError(
                    f"Client secrets file not found: {self.client_secrets_path}\n"
                    "Please download it from Google Cloud Console."
                )

            # Pre-flight: verify the client secret is actually accepted by
            # Google before opening a browser flow. Saves the user a pointless
            # OAuth round-trip when the client was revoked or deleted.
            probe = _client_secret_ok(self.client_secrets_path)
            if probe is not None and not probe["ok"]:
                raise RuntimeError(
                    f"Your OAuth client is not accepted by Google "
                    f"(error: {probe['error']}).\n"
                    f"  File: {self.client_secrets_path}\n"
                    "  Fix: open Google Cloud Console > APIs & Services > "
                    "Credentials, create a NEW OAuth client (type: Desktop), "
                    "download its JSON, and overwrite client_secret.json. "
                    "Then re-run the upload to complete browser authentication."
                )

            print("\n" + "=" * 60)
            print("YouTube Authentication Required")
            print("=" * 60)
            print("A browser window will open.")
            print("Login with your YouTube channel Google account.")
            print("Grant permissions to the app.")
            print("=" * 60 + "\n")

            # Try ports 8081, 8082, 8083, ... (8080 is occupied by Hermes
            # desktop; skip it to avoid a 1–2s OSError delay on every run).
            # The Hermes desktop app already owns 8080, so we must avoid it.
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secrets_path, SCOPES
            )
            last_exc = None
            for port in (8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089, 8080):
                try:
                    creds = flow.run_local_server(
                        port=port,
                        prompt='consent',
                        authorization_prompt_message="Opening browser for authentication...",
                    )
                    print(f"  Authenticated on port {port}.")
                    break
                except OSError as exc:
                    last_exc = exc
                    continue
            else:
                # All ports busy — give a clear, actionable error instead of
                # a stack trace the user can't act on.
                raise RuntimeError(
                    "Could not open an OAuth browser callback on any port "
                    f"(tried 8080–8089). Most likely another process owns the "
                    "port. Close the blocking process (e.g. the Hermes desktop "
                    "app's local server) and re-run, or run `python upload.py "
                    "setup` from a standalone terminal where a browser can open. "
                    "Original error: " + str(last_exc)
                ) from last_exc

        # Save credentials for next run (JSON format)
        self._save_credentials(creds)
        print("\nAuthentication successful! Token saved.")

        self.service = build('youtube', 'v3', credentials=creds)
        # Quick verification: call channels.list to confirm the token works.
        try:
            channel_res = self.service.channels().list(mine=True, part="snippet").execute()
            channel_title = channel_res["items"][0]["snippet"]["title"]
            print(f"YouTube API connected! Channel: {channel_title}\n")
        except HttpError as hb:
            print(f"YouTube API connected, but channel check failed: {hb}\n")
            print("  This may indicate an invalid/expired token despite successful build.")
        except Exception as e:
            print(f"YouTube API connected (channel check skipped): {e}\n")
    
    def _save_credentials(self, creds):
        """Save credentials to JSON file."""
        with open(self.credentials_path, 'w') as f:
            f.write(creds.to_json())
    
    def _try_migrate_pickle(self):
        """Try to load credentials from legacy pickle format."""
        try:
            import pickle
            with open(self.credentials_path, 'rb') as f:
                return pickle.load(f)
        except Exception:
            return None
    
    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list = None,
        category_id: str = "28",
        privacy_status: str = "private",
        thumbnail_path: Optional[str] = None,
        schedule_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Upload a video to YouTube.
        
        Args:
            video_path: Path to video file
            title: Video title
            description: Video description
            tags: List of tags
            category_id: YouTube category ID (28 = Science & Technology)
            privacy_status: private, public, or unlisted
            thumbnail_path: Path to thumbnail image
            schedule_time: Scheduled publish time (for private videos)
            
        Returns:
            Upload response with video ID
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(
                f"Video file not found: {video_path}\n"
                "  Check that the video was generated successfully before uploading."
            )
        
        # Prepare video metadata
        body = {
            "snippet": {
                "title": title[:100],  # YouTube limit
                "description": description[:5000],
                "tags": tags or [],
                "categoryId": category_id
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }
        
        # Add scheduled publishing if specified
        if schedule_time and privacy_status == "private":
            body["status"]["publishAt"] = schedule_time.isoformat() + "Z"
        
        # Create media upload object
        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024 * 10  # 10MB chunks
        )
        
        # Execute upload
        print(f"Uploading video: {title}")
        print(f"File: {video_path}")
        
        request = self.service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"Upload progress: {progress}%")
        
        video_id = response["id"]
        print(f"\nUpload complete! Video ID: {video_id}")
        print(f"URL: https://www.youtube.com/watch?v={video_id}")
        
        # Upload thumbnail if provided
        if thumbnail_path and os.path.exists(thumbnail_path):
            self._upload_thumbnail(video_id, thumbnail_path)
        
        return response

    def upload_captions(self, video_id: str, srt_path: str, language: str = "en") -> dict:
        """Attach an SRT/WebVTT transcript as video captions (great for SEO).

        Needs the same channel auth as video upload. Non-fatal at the caller:
        raises on failure so the caller can log and continue.
        """
        if not srt_path or not os.path.exists(srt_path):
            raise FileNotFoundError(f"Caption file not found: {srt_path}")

        lang = {"ne": "ne", "hi": "hi", "en": "en"}.get(language, language or "en")
        body = {
            "snippet": {
                "videoId": video_id,
                "language": lang,
                "name": "AI-generated transcript",
                "isDraft": False,
            }
        }
        print(f"Uploading captions for {video_id} (language: {lang})...")
        request = self.service.captions().insert(
            part="snippet",
            body=body,
            media_body=MediaFileUpload(
                srt_path,
                mimetype="application/octet-stream",
                resumable=True,
            ),
        )
        resp = request.execute()
        print(f"Captions uploaded: track id {resp.get('id', '?')}")
        return resp

    def _upload_thumbnail(self, video_id: str, thumbnail_path: str):
        """Upload custom thumbnail for video."""
        print("Uploading thumbnail...")
        
        try:
            self.service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            ).execute()
            print("Thumbnail uploaded successfully!")
        except HttpError as e:
            print(f"Thumbnail upload failed: {e}")
    
    def get_upload_status(self, video_id: str) -> Dict[str, Any]:
        """Get processing status of uploaded video."""
        response = self.service.videos().list(
            part="status,processingDetails",
            id=video_id
        ).execute()
        
        if response["items"]:
            return response["items"][0]
        return None
    
    def list_recent_uploads(self, max_results: int = 10) -> list:
        """List recent uploads to the channel."""
        response = self.service.channels().list(
            part="contentDetails",
            mine=True
        ).execute()
        
        if not response["items"]:
            return []
        
        uploads_playlist_id = response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        
        playlist_response = self.service.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=max_results
        ).execute()
        
        return playlist_response.get("items", [])


class ThumbnailGenerator:
    """Generates video thumbnails."""
    
    def __init__(self, width: int = 1280, height: int = 720):
        self.width = width
        self.height = height
    
    def generate_thumbnail(
        self,
        title: str,
        output_path: str,
        background_color: str = "#1a1a2e",
        text_color: str = "#FFD700",
        accent_color: str = "#e94560",
        character_art: Optional[PILImage.Image] = None,
        character_enabled: bool = True,
    ) -> str:
        """Generate an enhanced thumbnail with bold text, starburst, and optional character art.

        Style: Simplilearn-inspired — yellow bold text with black outline on a
        starburst burst, deep blue gradient background with corner accents,
        and an optional character/presenter image on the right side.
        """
        from PIL import Image, ImageDraw, ImageFont, ImageFilter

        # --- Background: deep blue/purple gradient ---
        img = Image.new("RGB", (self.width, self.height), background_color)
        draw = ImageDraw.Draw(img)
        for y in range(self.height):
            t = y / self.height
            r = int(26 + (40 - 26) * t)
            g = int(18 + (20 - 18) * t)
            b = int(46 + (80 - 46) * t)
            draw.line([(0, y), (self.width, y)], fill=(r, g, b))

        # --- Subtle circuit/grid pattern ---
        if accent_color.startswith("#") and len(accent_color) >= 7:
            hex_color = accent_color[1:]
            grid_rgb = (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))
        elif accent_color.startswith("#") and len(accent_color) == 4:
            hex_color = accent_color[1:]
            grid_rgb = (int(hex_color[0]*2, 16), int(hex_color[1]*2, 16), int(hex_color[2]*2, 16))
        elif accent_color.startswith("#") and len(accent_color) == 3:
            hex_color = accent_color[1:]
            grid_rgb = (int(hex_color[0]*2, 16), int(hex_color[1]*2, 16), int(hex_color[2]*2, 16))
        else:
            grid_rgb = (233, 69, 96)
        grid_alpha = 15
        for x in range(0, self.width, 40):
            draw.line([(x, 0), (x, self.height)], fill=grid_rgb, width=1)
        for y in range(0, self.height, 40):
            draw.line([(0, y), (self.width, y)], fill=grid_rgb, width=1)

        # --- Starburst behind title ---
        cx, cy = self.width // 2, self.height // 2 - 40
        num_rays = 24
        ray_len = min(self.width, self.height) // 2
        for i in range(num_rays):
            angle = 2 * math.pi * i / num_rays
            ex = cx + ray_len * math.cos(angle)
            ey = cy + ray_len * math.sin(angle)
            inner_r = ray_len * 0.3
            ix = cx + inner_r * math.cos(angle)
            iy = cy + inner_r * math.sin(angle)
            draw.line([(ix, iy), (ex, ey)], fill=accent_color, width=2)

        # Radial burst rings
        for radius in range(int(ray_len * 0.3), int(ray_len * 0.8), int(ray_len * 0.12)):
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                         outline=(*grid_rgb, 60), width=1)

        # --- Character art (right side) ---
        if character_enabled and character_art is not None:
            ca = character_art.convert("RGBA")
            ca_w, ca_h = ca.size
            target_h = int(self.height * 0.7)
            scale = target_h / ca_h
            new_w = int(ca_w * scale)
            new_h = target_h
            ca_resized = ca.resize((new_w, new_h), Image.LANCZOS)

            # Soft glow behind character
            glow = ca_resized.copy()
            glow = glow.filter(ImageFilter.GaussianBlur(radius=12))
            glow_bg = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            glow_x = self.width - new_w - 60
            glow_y = self.height - new_h - 60
            glow_bg.paste(glow, (glow_x - 8, glow_y - 8), glow)
            img.paste(glow_bg, (0, 0), glow_bg)

            # Paste character
            img.paste(ca_resized, (glow_x, glow_y), ca_resized)

            # Border around character
            draw.rectangle(
                [glow_x - 4, glow_y - 4, glow_x + new_w + 4, glow_y + new_h + 4],
                outline=accent_color, width=3
            )

            text_x_max = glow_x - 40
        else:
            text_x_max = self.width - 60

        # --- Bold title text with outline ---
        try:
            font_title = ImageFont.truetype("C:/Windows/Fonts/comic.ttf", 72)
            font_sub = ImageFont.truetype("C:/Windows/Fonts/comic.ttf", 36)
        except Exception:
            try:
                font_title = ImageFont.truetype("arial.ttf", 72)
                font_sub = ImageFont.truetype("arial.ttf", 36)
            except Exception:
                font_title = ImageFont.load_default()
                font_sub = ImageFont.load_default()

        # Word wrap title to fit text_x_max
        words = title.split()
        lines = []
        current_line = []
        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font_title)
            if bbox[2] - bbox[0] < text_x_max and len(current_line) < 8:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            lines.append(" ".join(current_line))

        # Limit to 3 lines max
        if len(lines) > 3:
            lines = lines[:3]

        # Title Y position — centered vertically
        line_height = 80
        total_text_h = len(lines) * line_height
        start_y = (self.height - total_text_h) // 2 - 20

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font_title)
            text_w = bbox[2] - bbox[0]
            x = (self.width - text_w) // 2

            # Black outline (4 directions + diagonals)
            outline_offsets = [
                (4, 0), (-4, 0), (0, 4), (0, -4),
                (3, 3), (-3, 3), (3, -3), (-3, -3),
            ]
            for ox, oy in outline_offsets:
                draw.text((x + ox, start_y + i * line_height + oy), line,
                         fill="#000000", font=font_title)
            # Yellow text
            draw.text((x, start_y + i * line_height), line,
                     fill=text_color, font=font_title)

        # --- Subtitle / tag line ---
        tag_text = title.split("_")[0].replace("_", " ").title() if "_" in title else title[:40]
        tag_bbox = draw.textbbox((0, 0), tag_text, font=font_sub)
        tag_w = tag_bbox[2] - tag_bbox[0]
        tag_x = (self.width - tag_w) // 2
        tag_y = start_y + total_text_h + 20
        # Tag background pill
        pill_pad = 20
        draw.rounded_rectangle(
            [tag_x - pill_pad, tag_y - 4, tag_x + tag_w + pill_pad, tag_y + 36],
            radius=18, fill=accent_color, outline=None
        )
        draw.text((tag_x, tag_y), tag_text, fill="#FFFFFF", font=font_sub)

        # --- Corner accents ---
        corner_size = 60
        for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            cx0 = 0 if sx < 0 else self.width - corner_size
            cy0 = 0 if sy < 0 else self.height - corner_size
            draw.rectangle([cx0, cy0, cx0 + corner_size, cy0 + 4], fill=accent_color)
            draw.rectangle([cx0, cy0, cx0 + 4, cy0 + corner_size], fill=accent_color)

        # --- Bottom accent bar ---
        draw.rectangle(
            [(0, self.height - 8), (self.width, self.height)],
            fill=accent_color
        )

        # --- Save ---
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        img.save(output_path, "JPEG", quality=95)
        print(f"Thumbnail saved to: {output_path}")
        return output_path


def setup_youtube_api():
    """Interactive setup for YouTube API credentials.

    Run from a STANDALONE terminal where a browser can open on ports
    8080–8089. The Hermes desktop app's local server may occupy port
    8080, which blocks the OAuth callback — if setup fails with a port
    error, close the blocking process and retry, or use a different
    terminal.
    """
    print("=" * 60)
    print("YouTube API Setup Guide")
    print("=" * 60)

    print("""
Step 1: Go to Google Cloud Console
        https://console.cloud.google.com/

Step 2: Create a new project or select existing one

Step 3: Enable YouTube Data API v3
        APIs & Services > Library > Search "YouTube Data API v3"

Step 4: Create OAuth 2.0 credentials
        APIs & Services > Credentials > Create Credentials > OAuth client ID
        Application type: Desktop app

Step 5: Download the credentials JSON file

Step 6: Save it as 'client_secret.json' in this project directory
    """)

    input("Press Enter when you have the client_secret.json file ready...")

    if not os.path.exists("client_secret.json"):
        print("\nFile not found. Please place client_secret.json in the project directory.")
        return

    print("\nFile found! Testing authentication...")
    print("(A browser window should open — complete the OAuth flow.)")
    try:
        uploader = YouTubeUploader()
        print("\nSetup complete! You can now upload videos.")
        print(f"  Channel: {uploader.service.channels().list(mine=True, part='snippet').execute()['items'][0]['snippet']['title']}")
    except Exception as e:
        print(f"\nError during authentication: {e}")
        if "Only one usage of each socket" in str(e):
            print()
            print("PORT CONFLICT: Another process (likely the Hermes desktop app)")
            print("is holding port 8080 (or another port in 8080–8089).")
            print()
            print("Fix: close the blocking process and retry, OR run this setup")
            print("from a different terminal / command prompt where no local server")
            print("is running.")
            print()
            print("After fixing, re-run:  py upload.py setup")
        else:
            print("Please check your credentials and try again.")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_youtube_api()
    else:
        print("Usage:")
        print("  python upload.py setup    - Set up YouTube API credentials")
        print("\nFor uploading, import and use YouTubeUploader class.")
