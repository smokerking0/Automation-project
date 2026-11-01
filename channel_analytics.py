"""
YouTube Channel Analytics — fetches channel stats and video performance
via the YouTube Analytics API + Data API v3.

Provides:
  - Channel info: title, description, subscriber count, total views, video count
  - Video performance: views, likes, comments, avg view duration for recent uploads
  - Trend tracking: compare current vs previous period for key metrics
  - CSV export of video performance for external analysis

Requires:
  - YouTube Data API v3 key (data_api_key in config.yaml / YOUTUBE_DATA_API_KEY in .env)
  - OAuth credentials with youtube.readonly scope (already in token.json)
  - The channel must be linked to the authenticated Google account (i.e. you
    are logged into the Google account that owns @CoreInfotech-w4m).
"""

import os
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request


# ---------------------------------------------------------------------------
# Scope — only reads analytics, never modifies anything
# ---------------------------------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _load_credentials(
    client_secrets_path: str = "client_secret.json",
    credentials_path: str = "token.json",
) -> Optional[Credentials]:
    """Load or refresh OAuth credentials. Returns None when no valid token exists."""
    creds = None
    if os.path.exists(credentials_path):
        try:
            creds = Credentials.from_authorized_user_file(credentials_path, SCOPES)
        except Exception:
            pass

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(client_secrets_path):
            return None
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
        for port in (8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089, 8080):
            try:
                creds = flow.run_local_server(port=port, prompt="consent")
                break
            except OSError:
                continue
        else:
            return None
        # Save for next time
        with open(credentials_path, "w") as f:
            f.write(creds.to_json())

    return creds


def _build_service():
    """Build both Data API and Analytics API services. Returns (data_api, analytics_api)."""
    creds = _load_credentials()
    if not creds:
        return None, None
    try:
        data_api = build("youtube", "v3", credentials=creds)
    except Exception:
        data_api = None
    try:
        analytics_api = build("youtubeanalytics", "v2", credentials=creds)
    except Exception:
        analytics_api = None
    return data_api, analytics_api


# ---------------------------------------------------------------------------
# Channel info
# ---------------------------------------------------------------------------

def get_channel_info(client_secrets_path: str = "client_secret.json",
                     credentials_path: str = "token.json") -> Dict[str, Any]:
    """Return channel snippet + statistics for the authenticated user's channel."""
    data_api, _ = _build_service()
    if data_api is None:
        return {"error": "Cannot build YouTube Data API service (check OAuth token)"}

    try:
        resp = data_api.channels().list(mine=True, part="snippet,statistics").execute()
    except Exception as e:
        return {"error": f"channels.list failed: {e}"}

    if not resp.get("items"):
        return {"error": "No channel found for authenticated account"}

    item = resp["items"][0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})

    return {
        "channel_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "custom_url": snippet.get("customUrl", ""),
        "country": snippet.get("country", ""),
        "published_at": snippet.get("publishedAt", ""),
        "thumbnail_default": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
        "thumbnail_medium": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
        "thumbnail_high": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
        "view_count": int(stats.get("viewCount", 0)),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "hidden_subscriber_count": stats.get("hiddenSubscriberCount", False),
    }


# ---------------------------------------------------------------------------
# Video performance (Data API v3 — views, likes, comments)
# ---------------------------------------------------------------------------

def get_video_stats(video_id: str,
                    client_secrets_path: str = "client_secret.json",
                    credentials_path: str = "token.json") -> Dict[str, Any]:
    """Return views, likes, comments, and duration for a single video."""
    data_api, _ = _build_service()
    if data_api is None:
        return {"error": "Cannot build YouTube Data API service"}

    try:
        resp = data_api.videos().list(
            part="statistics,snippet",
            id=video_id,
        ).execute()
    except Exception as e:
        return {"error": f"videos.list failed: {e}"}

    items = resp.get("items", [])
    if not items:
        return {"error": f"Video {video_id} not found"}

    item = items[0]
    stats = item.get("statistics", {})
    snippet = item.get("snippet", {})

    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "published_at": snippet.get("publishedAt", ""),
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "dislikes": int(stats.get("dislikeCount", 0)) if stats.get("dislikeCount") else 0,
        "avg_view_duration": stats.get("averageViewDuration", ""),
        "favorite_count": int(stats.get("favoriteCount", 0)),
        "duration": stats.get("duration", ""),
    }


def get_recent_video_stats(max_results: int = 20,
                           client_secrets_path: str = "client_secret.json",
                           credentials_path: str = "token.json") -> List[Dict[str, Any]]:
    """Return stats for the most recent uploads on the authenticated channel."""
    data_api, _ = _build_service()
    if data_api is None:
        return [{"error": "Cannot build YouTube Data API service"}]

    # First get the channel's uploads playlist
    try:
        channel_resp = data_api.channels().list(mine=True, part="contentDetails").execute()
    except Exception as e:
        return [{"error": f"channels.list failed: {e}"}]

    if not channel_resp.get("items"):
        return [{"error": "No channel found"}]

    uploads_playlist_id = channel_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Then get the most recent items from that playlist
    try:
        playlist_resp = data_api.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=max_results,
        ).execute()
    except Exception as e:
        return [{"error": f"playlistItems.list failed: {e}"}]

    results = []
    for item in playlist_resp.get("items", []):
        video_id = item.get("contentDetails", {}).get("videoId", "")
        if video_id:
            stats = get_video_stats(video_id, client_secrets_path, credentials_path)
            results.append(stats)

    return results


# ---------------------------------------------------------------------------
# YouTube Analytics API — deeper metrics (watch time, avg view duration, etc.)
# ---------------------------------------------------------------------------

def get_analytics_report(
    video_ids: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    metrics: Optional[List[str]] = None,
    dimensions: Optional[List[str]] = None,
    client_secrets_path: str = "client_secret.json",
    credentials_path: str = "token.json",
) -> Dict[str, Any]:
    """Query the YouTube Analytics API for aggregated metrics.

    Args:
        video_ids: Limit to specific video IDs (None = all videos on channel)
        start_date: YYYY-MM-DD (default: 28 days ago)
        end_date: YYYY-MM-DD (default: yesterday)
        metrics: List of metrics to fetch (default: views,likeCount,commentCount,estimatedMinutesWatched)
        dimensions: List of dimensions (default: day for time-series)

    Returns:
        Dict with 'rows' (list of row dicts) and 'columnHeaders' or an 'error' key.
    """
    _, analytics_api = _build_service()
    if analytics_api is None:
        return {"error": "Cannot build YouTube Analytics API service (check OAuth token)"}

    if start_date is None:
        start_date = (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if metrics is None:
        metrics = ["views", "likeCount", "commentCount", "estimatedMinutesWatched", "averageViewDuration"]
    if dimensions is None:
        dimensions = ["day"]

    body = {
        "ids": "channel==MINE",
        "startDate": start_date,
        "endDate": end_date,
        "metrics": metrics,
        "dimensions": dimensions,
    }

    if video_ids:
        body["filters"] = f"video=={','.join(video_ids)}"

    try:
        resp = analytics_api.reports().query(body=body).execute()
    except Exception as e:
        return {"error": f"analytics.reports.query failed: {e}"}

    if not resp:
        return {"rows": [], "columnHeaders": []}

    column_headers = resp.get("columnHeaders", [])
    rows = resp.get("rows", [])

    # Convert rows to list of dicts
    result_rows = []
    for row in rows:
        row_dict = {}
        for i, header in enumerate(column_headers):
            row_dict[header["name"]] = row[i] if i < len(row) else None
        result_rows.append(row_dict)

    return {
        "column_headers": column_headers,
        "rows": result_rows,
        "start_date": start_date,
        "end_date": end_date,
    }


def get_video_analytics(
    video_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    client_secrets_path: str = "client_secret.json",
    credentials_path: str = "token.json",
) -> Dict[str, Any]:
    """Get analytics for a single video over a date range."""
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    return get_analytics_report(
        video_ids=[video_id],
        start_date=start_date,
        end_date=end_date,
        dimensions=["day"],
        client_secrets_path=client_secrets_path,
        credentials_path=credentials_path,
    )


# ---------------------------------------------------------------------------
# Comparison: current period vs previous period
# ---------------------------------------------------------------------------

def get_period_comparison(
    video_ids: Optional[List[str]] = None,
    days: int = 28,
    client_secrets_path: str = "client_secret.json",
    credentials_path: str = "token.json",
) -> Dict[str, Any]:
    """Compare metrics between the last `days` days and the previous `days` days.

    Returns a dict with:
      - current: metrics for the most recent `days`-day period
      - previous: metrics for the period before that
      - change: percent change for each metric
    """
    today = datetime.now()
    current_start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    current_end = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    previous_start = (today - timedelta(days=2 * days)).strftime("%Y-%m-%d")
    previous_end = (today - timedelta(days=days + 1)).strftime("%Y-%m-%d")

    current = get_analytics_report(
        video_ids=video_ids,
        start_date=current_start,
        end_date=current_end,
        dimensions=[],
        client_secrets_path=client_secrets_path,
        credentials_path=credentials_path,
    )
    previous = get_analytics_report(
        video_ids=video_ids,
        start_date=previous_start,
        end_date=previous_end,
        dimensions=[],
        client_secrets_path=client_secrets_path,
        credentials_path=credentials_path,
    )

    if "error" in current:
        return {"error": current["error"]}
    if "error" in previous:
        return {"error": previous["error"]}

    current_totals = _sum_rows(current.get("rows", []))
    previous_totals = _sum_rows(previous.get("rows", []))

    comparison = {}
    for key in set(current_totals.keys()) | set(previous_totals.keys()):
        cur = current_totals.get(key, 0)
        prev = previous_totals.get(key, 0)
        if prev and cur:
            pct = ((cur - prev) / prev) * 100
        else:
            pct = 0.0
        comparison[key] = {
            "current": cur,
            "previous": prev,
            "change": cur - prev,
            "change_percent": round(pct, 1),
        }

    return {
        "current_period": f"{current_start} to {current_end}",
        "previous_period": f"{previous_start} to {previous_end}",
        "current": current_totals,
        "previous": previous_totals,
        "comparison": comparison,
    }


def _sum_rows(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Sum numeric values across rows, keyed by metric name."""
    totals = {}
    for row in rows:
        for key, val in row.items():
            try:
                totals[key] = totals.get(key, 0) + int(val)
            except (ValueError, TypeError):
                pass
    return totals


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_video_stats_to_csv(
    video_stats: List[Dict[str, Any]],
    csv_path: str = "output/channel_performance.csv",
) -> str:
    """Write a list of video stat dicts to a CSV file."""
    if not video_stats:
        return csv_path

    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "video_id", "title", "published_at", "views", "likes",
        "comments", "dislikes", "avg_view_duration", "duration",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in video_stats:
            writer.writerow(row)

    return csv_path


# ---------------------------------------------------------------------------
# Convenience: full channel performance summary
# ---------------------------------------------------------------------------

def get_channel_performance_summary(
    max_recent_videos: int = 20,
    client_secrets_path: str = "client_secret.json",
    credentials_path: str = "token.json",
) -> Dict[str, Any]:
    """Combine channel info + recent video stats + period comparison into one report."""
    channel = get_channel_info(client_secrets_path, credentials_path)
    recent = get_recent_video_stats(max_recent_videos, client_secrets_path, credentials_path)
    comparison = get_period_comparison(days=28, client_secrets_path=client_secrets_path,
                                       credentials_path=credentials_path)

    return {
        "channel": channel,
        "recent_videos": recent,
        "period_comparison": comparison,
        "generated_at": datetime.now().isoformat(),
    }
