"""
Ollama Remote LLM Integration
Connects to a remote PC running Ollama (with H200 GPU) for natural script generation.
Falls back to template-based generation if Ollama is unreachable.
"""

import json
import urllib.request
import urllib.error
from typing import Optional


class OllamaClient:
    """Connects to a remote Ollama instance for LLM-powered script generation."""

    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3.1:70b"):
        self.host = host.rstrip("/")
        self.model = model
        self.available = False
        self._check_connection()

    def _check_connection(self):
        """Check if Ollama is reachable."""
        try:
            url = f"{self.host}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                models = [m.get("name", "") for m in data.get("models", [])]
                self.available = self.model in models or any(self.model in m for m in models)
                if not self.available and models:
                    self.model = models[0]
                    self.available = True
        except Exception:
            self.available = False

    def _generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> str:
        """Send a prompt to Ollama and get a response."""
        url = f"{self.host}/api/generate"
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "")

    def generate_video_script(self, topic: str, style: str = "educational",
                               duration_minutes: int = 3,
                               research_text: str = "") -> Optional[dict]:
        """
        Generate a complete video script using Ollama.

        Returns dict with keys: title, intro, points, outro, voiceovers
        or None if generation fails.
        """
        if not self.available:
            return None

        num_points = 5 if duration_minutes <= 2 else 6

        research_section = ""
        if research_text:
            research_section = f"""
RESEARCH CONTEXT (use this to ground your script in real, current information):
{research_text}

IMPORTANT: Reference specific facts from the research above where relevant.
Don't just repeat the research verbatim, but use it to make your explanations
more concrete and credible."""

        prompt = f"""You are a senior tech educator writing for CS students and working professionals. Generate a YouTube video script that is educational, practical, and precise.

TOPIC: {topic.replace('_', ' ')} -- the entire script must stay on this exact topic. Do not drift to a different trending title.
STYLE: {style}
DURATION: {duration_minutes} minutes
NUMBER OF MAIN POINTS: {num_points}
{research_section}

Return ONLY valid JSON with this exact structure (no markdown, no code fences):
{{
  "title": "Specific educational title for the TOPIC (max 60 chars, must contain topic keywords)",
  "intro": "3-4 sentences: hook + why this topic matters for students/professionals in 2026 + what you will learn.",
  "points": [
    {{
      "heading": "Specific point heading grounded in the TOPIC (2-6 words)",
      "explanation": "60-80 words. Start with definition, then mechanism/how-it-works, then concrete example or number from research, then practical takeaway. Avoid vague filler."
    }}
  ],
  "outro": "3 sentences: key takeaways + one common mistake to avoid + call to action"
}}

RULES:
- Stay strictly on TOPIC -- do not invent a different subject.
- Educational depth: define terms, explain mechanism, give one real example, one number or spec, one tradeoff/limitation per point.
- Use specific names, versions, benchmarks, and tool names from RESEARCH when available; otherwise use accurate general knowledge for the TOPIC.
- Each explanation 60-80 words, natural but technically precise.
- Title must contain topic keywords; outro must include a practical next step.
- NO filler: never write "in this video we will discuss" as a sentence.
- Audience: tech students and professionals -- assume curiosity, not expertise."""

        try:
            response = self._generate(prompt, temperature=0.8, max_tokens=1500)
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
            if response.endswith("```"):
                response = response.rsplit("```", 1)[0]
            response = response.strip()

            data = json.loads(response)

            if "title" not in data or "points" not in data:
                return None

            point_headings = [p.get("heading", f"Point {i+1}") for i, p in enumerate(data["points"])]
            point_explanations = []
            for p in data["points"]:
                exp = p.get("explanation", "")
                if exp:
                    point_explanations.append(exp)

            return {
                "title": data["title"],
                "intro": data.get("intro", f"Let's explore {topic.replace('_', ' ')}."),
                "points": point_headings,
                "outro": data.get("outro", "Thanks for watching! Subscribe for more tech content."),
                "voiceovers": [data.get("intro", "")] + point_explanations + [data.get("outro", "")],
            }

        except (json.JSONDecodeError, KeyError, Exception) as e:
            print(f"  Ollama generation error: {e}")
            return None

    def generate_single_voiceover(self, point_heading: str, context: str = "") -> str:
        """Generate a natural voiceover paragraph for a single point."""
        if not self.available:
            return ""

        prompt = f"""Write an educational voiceover paragraph for a tech video for students and professionals.

POINT: {point_heading}
{('CONTEXT: ' + context) if context else ''}

RULES:
- 60-80 words, technically precise and educational
- Definition first, then how it works, then example or number, then takeaway
- Include one specific spec, benchmark, tool name, or limitation
- Stay strictly on the POINT -- no off-topic drift
- Return ONLY the paragraph text, nothing else"""

        try:
            return self._generate(prompt, temperature=0.7, max_tokens=200).strip()
        except Exception:
            return ""

    def status(self) -> str:
        """Return connection status string."""
        if self.available:
            return f"Connected: {self.model} at {self.host}"
        return f"Disconnected from {self.host}"
