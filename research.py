"""
Topic Research — fetches live search results for a topic.

Fills the "Search Topic" box in the flowchart by hitting DuckDuckGo's
HTML endpoint (no API key required). Returns a plain-text summary that
can be fed into the script generator for more grounded, current scripts.

Usage
-----
    from research import TopicResearch
    r = TopicResearch()
    summary = r.research("What is a GPU and why is it important for AI?")
    print(summary)
"""

from __future__ import annotations

import html
import re
import time
from typing import Optional

import requests

# requests is installed system-wide (pip 25.3, Python 3.14).
# If the active venv can't see it, the research step simply returns
# an empty summary — the pipeline falls back to template-based scripts.

try:
    import requests as _req
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class TopicResearch:
    """
    Lightweight web research tool.

    Public methods
    --------------
    research(topic: str, num_results: int = 5) -> str
        Returns a paragraph summarising the top search snippets.
    search_snippets(topic: str, num_results: int = 5) -> list[dict]
        Raw structured results: [{"title":..., "snippet":..., "url":...}]
    """

    def __init__(self, delay: float = 1.0):
        """
        Parameters
        ----------
        delay : float
            Seconds to wait between HTTP calls to be polite to DDG.
        """
        self.delay = delay
        self.session = None
        if HAS_REQUESTS:
            self.session = _req.Session()
            self.session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def research(self, topic: str, num_results: int = 5) -> str:
        """
        Ask DuckDuckGo for the topic and return a compact summary.

        The summary is built from the top result snippets and is suitable
        for feeding into an LLM prompt as "research context".
        """
        if not HAS_REQUESTS or self.session is None:
            return ""

        snippets = self.search_snippets(topic, num_results)
        if not snippets:
            return ""

        parts = []
        for i, s in enumerate(snippets, 1):
            parts.append(f"[{i}] {s['title']}: {s['snippet']}")
        research_text = " | ".join(parts)

        # Keep it under ~1500 chars so it fits comfortably in prompts
        if len(research_text) > 1500:
            research_text = research_text[:1497] + "..."

        return research_text

    def search_snippets(self, topic: str, num_results: int = 5) -> list:
        """
        Return a list of {title, snippet, url} dicts from DuckDuckGo.
        """
        if not HAS_REQUESTS or self.session is None:
            return []

        results = []
        try:
            # DuckDuckGo HTML endpoint
            url = "https://html.duckduckgo.com/html/"
            resp = self.session.post(
                url,
                data={"q": topic, "format": "html"},
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.text

            # Parse result blocks — DDG HTML results use this pattern
            # Results are in <a class="result__a"> (title+url) and
            # <a class="result__snippet"> (snippet).
            result_blocks = re.findall(
                r'<div class="result results_links results_links_deep result--acttype-\d+".*?'
                r'</div>\s*</div>',
                body,
                re.DOTALL,
            )

            # Fallback: try the simpler regex if DDG changes markup
            if not result_blocks:
                result_blocks = re.findall(
                    r'<a rel="nofollow" class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                    r'<a class="result__snippet"[^>]*>(.*?)</a>',
                    body,
                    re.DOTALL,
                )
                for url_match, title_match, snippet_match in result_blocks[:num_results]:
                    title = re.sub(r"<[^>]+>", "", title_match).strip()
                    snippet = re.sub(r"<[^>]+>", "", snippet_match).strip()
                    results.append({
                        "title": html.unescape(title),
                        "snippet": html.unescape(snippet),
                        "url": html.unescape(url_match),
                    })
                return results

            for block in result_blocks[:num_results]:
                # Extract URL (DDG wraps it in /l/?uddg=...)
                url_match = re.search(
                    r'<a rel="nofollow" class="result__a"[^>]*href="([^"]+)"',
                    block,
                )
                title_match = re.search(
                    r'<a rel="nofollow" class="result__a"[^>]*>(.*?)</a>',
                    block,
                    re.DOTALL,
                )
                snippet_match = re.search(
                    r'<a class="result__snippet"[^>]*>(.*?)</a>',
                    block,
                    re.DOTALL,
                )
                if url_match and title_match and snippet_match:
                    raw_url = url_match.group(1)
                    # Resolve DDG redirect urls
                    uddg = re.search(r"uddg=([^&]+)", raw_url)
                    if uddg:
                        from urllib.parse import unquote
                        raw_url = unquote(uddg.group(1))
                    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
                    snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
                    results.append({
                        "title": html.unescape(title),
                        "snippet": html.unescape(snippet),
                        "url": html.unescape(raw_url),
                    })

            time.sleep(self.delay)

        except Exception as exc:
            # Silently fail — research is a bonus, not a blocker
            results = []

        return results

    def search_ddg(self, queries, num_results_per_query: int = 3):
        """Compat shim for topics.py trending fallback."""
        out = []
        qs = queries if isinstance(queries, list) else [queries]
        for q in qs:
            for r in self.search_snippets(q, num_results=num_results_per_query):
                out.append({"title": r["title"], "body": r["snippet"], "href": r["url"]})
            time.sleep(self.delay)
        return out
