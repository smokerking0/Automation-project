"""
Topic Tracker — logs every video pipeline run to a local CSV file.

Designed as a lightweight, stdlib-only drop-in replacement for the
"Store topic in Excel" flowchart step. Swap to gspread/Google Sheets
later by implementing the same log() interface.

CSV columns
-----------
topic, title, timestamp, status, script_path, video_path,
thumbnail_path, youtube_url, duration_seconds, notes
"""

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class TopicTracker:
    """Append-only CSV ledger for video pipeline runs."""

    HEADER = [
        "topic",
        "title",
        "timestamp",
        "status",
        "script_path",
        "video_path",
        "thumbnail_path",
        "youtube_url",
        "duration_seconds",
        "notes",
    ]

    def __init__(self, csv_path: str = "output/topic_tracker.csv"):
        self.csv_path = csv_path
        Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
        if not os.path.exists(csv_path):
            self._write_header()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_header(self):
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(self.HEADER)

    def _read_rows(self):
        rows = []
        if os.path.exists(self.csv_path):
            with open(self.csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(
        self,
        topic: str,
        title: str,
        status: str,
        script_path: Optional[str] = None,
        video_path: Optional[str] = None,
        thumbnail_path: Optional[str] = None,
        youtube_url: Optional[str] = None,
        duration_seconds: float = 0.0,
        notes: str = "",
    ):
        """Append one run record to the CSV."""
        row = {
            "topic": topic,
            "title": title,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "script_path": script_path or "",
            "video_path": video_path or "",
            "thumbnail_path": thumbnail_path or "",
            "youtube_url": youtube_url or "",
            "duration_seconds": f"{duration_seconds:.1f}",
            "notes": notes,
        }
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADER)
            writer.writerow(row)

    def update_status(
        self,
        topic: str,
        status: str,
        youtube_url: Optional[str] = None,
        notes: str = "",
    ):
        """Update the most recent record for *topic* with a new status."""
        rows = self._read_rows()
        # find last matching row (most recent timestamp)
        target_idx = None
        for i in range(len(rows) - 1, -1, -1):
            if rows[i]["topic"] == topic:
                target_idx = i
                break
        if target_idx is None:
            return
        rows[target_idx]["status"] = status
        if youtube_url:
            rows[target_idx]["youtube_url"] = youtube_url
        if notes:
            rows[target_idx]["notes"] = notes
        # rewrite whole file
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.HEADER)
            writer.writeheader()
            writer.writerows(rows)

    def get_summary(self) -> list:
        """Return all rows as list of dicts for inspection."""
        return self._read_rows()
