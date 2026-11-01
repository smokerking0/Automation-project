"""
YouTube trending discovery for content planning.

Two free tiers:
  1. YouTube Data API v3 key (config youtube.data_api_key) -> real
     most-popular Science & Technology videos (exact chart, 1 unit each).
  2. RSS fallback (no key): pull latest uploads from a curated list of
     top tech/AI channels and rank by relevance keywords.

Each result becomes a `TrendingTopic` carrying a ready-made `topic_dict`
({title, intro, points, outro}) that plugs straight into
TechTopicGenerator.generate_script(topic_dict=...) for instant script
generation.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

try:
    import xml.etree.ElementTree as ET
    HAS_XML = True
except ImportError:
    HAS_XML = False

# Curated tech/AI channels (channel IDs, verified RSS feeds). A wrong/removed
# ID simply yields an empty feed — handled gracefully, never a crash.
DEFAULT_CHANNELS = [
    ("MKBHD", "UCBJycsmduvYEL83R_U4JriQ"),
    ("3Blue1Brown", "UCYO_jab_esuFRV4b17AJtAw"),
    ("freeCodeCamp", "UC8butISFwT-Wl7EV0hUK0BQ"),
    ("Computerphile", "UC9-y-6csu5WGm29I7JiwpnA"),
    ("Corey Schafer", "UCCezIgC97PvUuR4_gbFUs5g"),
    ("sentdex", "UCfzlCWGWYyIQ0aLC5w48gBQ"),
    ("Fireship", "UCsBjURrPoezykLs9EqgamOA"),
    ("Traversy Media", "UC29ju8bIPH5as8OGnQzwJyA"),
    ("Tech With Tim", "UC4JX40jDee_tINbkjycV4Sg"),
    ("Linus Tech Tips", "UCXuqSBlHAE6Xw-yeJA0Tunw"),
    ("Level1Techs", "UC4w1YQAJMWOz4qtxinq55LQ"),
    ("Hardware Unboxed", "UCBcRF18a7Qf58cCRy5xuWwQ"),
]

# Keywords that mark a video as on-topic for this channel's automation.
KEYWORDS = [
    "ai", "gpt", "llm", "model", "neural", "deep learning", "machine learning",
    "gpu", "nvidia", "cuda", "python", "coding", "programming", "data science",
    "data", "algorithm", "agent", "robot", "quantum", "server", "cloud",
    "mathematics", "math", "statistics", "computer", "tech", "shader",
    "rendering", "game dev",
]

_TAG_RE = re.compile(r"\s*[-|]\s*(YouTube|cast|podcast)$", re.I)
_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F300-\U0001F5FF]"
)
_LIVE_RE = re.compile(r"^(\[LIVE\]|🔴|LIVE)\s*[: -]*", re.I)
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _parse_duration_secs(dur: str) -> int:
    """ISO-8601 'PT4M13S' -> seconds (0 when unparseable)."""
    m = _DUR_RE.match(dur or "")
    if not m:
        return 0
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + int(s or 0)


# Titles that mark clips/Shorts — not suitable as main-video topics.
_SHORT_RE = re.compile(r"#shorts\b|#short\b", re.I)
_HASHTAG_RE = re.compile(r"#\w+")

# Content fields for the main menu. Each field maps to keywords used to
# filter/sort live trending topics, so the video pipeline can cover e.g.
# "Windows issues & fixes", "Kubernetes & Docker", "new models & agents", etc.
FIELD_KEYWORDS = {
    "kids": ["kids", "child", "for children", "toy", "lego", "stem", "school", "educational", "gaming", "minecraft", "roblox"],
    "networking": ["network", "networking", "wifi", "wi-fi", "router", "dns", "tcp", "ethernet", "vpn", "5g", "internet", "cybersecurity", "hacker", "hacking", "security", "firewall", "packet", "protocol", "bandwidth", "latency", "ping", "troubleshoot", "fix", "connectivity", "network card", "switch", "hub", "modem", "LAN", "WAN", "IP address", "subnet", "router configuration", "network monitoring", "network analysis", "network performance", "network optimization"],
    "windows": ["windows", "microsoft", "pc", "pcs", "windows 11", "windows 12", "win11", "win12", "win10", "win7", "win8", "fix", "troubleshoot", "update", "upgrade", "patch", "registry", "task manager", "control panel", "explorer", "cmd", "powershell", "desktop", "start menu", "settings", "drivers", "blue screen", "bsod", "windows server", "IIS Web Server", "MSSQL Server", "Active Directory", "Hyper-V", "DirectX", "Windows Subsystem for Linux", "WSL", "License", "Activation", "Windows Update", "Group Policy", "Event Viewer", "Performance Monitor", "Resource Monitor", "Disk Management", "Device Manager", "System Restore", "Backup and Restore"],
    "linux": ["linux", "ubuntu", "debian", "fedora", "kernel", "bash", "unix", "arch", "terminal", "open source", "shell", "command line", "package manager", "apt", "yum", "dnf", "snap", "flatpak", "systemd", "init", "cron", "ssh", "scp", "rsync", "docker", "kubernetes", "k8s", "container", "pod", "helm", "orchestration", "firewall", "iptables", "ufw", "selinux", "apparmor", "x11", "wayland", "gnome", "kde", "xfce", "lxde", "mate", "cinnamon", "desktop environment", "window manager", "xorg", "wayland compositor", "linux mint", "centos", "red hat", "suse", "gentoo", "slackware", "Package installation", "Qcow2", "ext4", "btrfs", "zfs", "swap", "filesystem", "mount", "umount", "fstab", "grub", "bootloader", "initramfs recovery", "kernel panic", "system logs", "journalctl", "dmesg", "procfs", "sysfs", "cgroups", "namespaces", "containers", "virtualization"],
    "cloud": ["cloud", "aws", "azure", "gcp", "google cloud", "serverless", "lambda", "s3", "saas", "cloudflare", "hosting", "virtual machine", "vm", "monitoring", "Storage", "ackup", "disaster recovery", "load balancer", "cdn", "content delivery network", "api gateway", "cloud security", "cloud migration", "cloud architecture", "cloud cost optimization", "cloud native", "Object storage", "block storage", "file storage", "cloud database", "cloud networking", "cloud automation", "cloud orchestration", "cloud compliance", "cloud governance", "cloud monitoring", "cloud logging", "cloud alerting", "cloud scaling", "cloud performance tuning"],
    "news": ["news", "update", "updates", "release", "releases", "announce", "announces", "reveals", "unveils", "leaks", "leak", "recap", "event", "launch", "review", "Scences & Technology", "Tech News", "Tech Updates", "Tech Announcements", "Tech Releases", "Tech Reveals", "Tech Leaks", "Tech Recap", "Tech Events", "Tech Launches", "Tech Reviews", "Blog Posts", "Press Releases", "Industry News", "Product Announcements", "Product Launches", "Product Reviews", "Product Updates", "Software Updates", "Software Releases", "Software Announcements", "Software Launches", "Software Reviews", "Hardware Updates", "Hardware Releases", "Hardware Announcements", "Hardware Launches", "Hardware Reviews"],
    "ai_ml": ["ai", "artificial", "gpt", "llm", "machine learning", "deep learning", "transformer", "neural", "openai", "anthropic", "google", "gemini", "intelligence", "Python", "Pytorch", "Numpay", "Pandas", "Matplotlib", "TensorFlow", "Keras", "Scikit-learn", "Jupyter", "Colab", "Hugging Face", "LangChain", "Prompt Engineering", "Reinforcement Learning", "Computer Vision", "Natural Language Processing", "NLP", "Generative AI", "GANs", "Diffusion Models", "AI Ethics", "AI Safety", "AI Alignment", "AI Regulation", "AI Policy", "AI Governance", "AI Explainability", "AI Fairness", "AI Bias", "AI Transparency", "AI Accountability", "AI Robustness", "AI Security", "AI Privacy", "AI Trustworthiness", "AI Interpretability", "AI Generalization", "AI Adaptation", "AI Transfer Learning", "AI Meta-Learning", "AI Continual Learning"],
    "models_agents": ["agent", "agents", "gpt", "gemini", "claude", "llama", "mistral", "sonnet", "opus", "grok", "reasoning", "model", "new ai", "vLLM", "foundation model", "large language model", "llm", "chatgpt", "chatbot", "assistant", "ai agent", "autonomous agent", "multi-agent system", "agent-based modeling", "agent-based simulation", "agent-based learning", "agent-based reasoning", "agent-based decision making", "agent-based planning", "agent-based control", "agent-based optimization", "Autonomous agent", "AI agent", "intelligent agent", "cognitive agent", "virtual agent", "software agent", "robotic agent", "multi-agent system", "agent-based architecture", "agent-based framework", "agent-based platform", "Natural language processing", "NLP", "reinforcement learning", "RL", "deep reinforcement learning", "DRL", "multi-agent reinforcement learning", "MARL", "agent-based reinforcement learning", "ABRL"],
    "devops": ["devops", "ci/cd", "ci cd", "github actions", "terraform", "ansible", "pipeline", "automation", "deploy", "deploying", "monitoring", "observability", "git", "MLOps", "continuous integration", "continuous delivery", "continuous deployment", "infrastructure as code", "IaC", "configuration management", "containerization", "orchestration", "kubernetes", "docker", "helm", "prometheus", "grafana", "alerting", "logging", "tracing", "service mesh", "microservices", "cloud-native", "serverless", "Migration", "scaling", "load balancing", "high availability", "disaster recovery", "security", "compliance", "testing", "quality assurance", "performance optimization", "cost optimization", "Zabbix", "Nagios", "New Relic", "Datadog", "Splunk", "ELK stack", "Fluentd", "Jaeger", "OpenTelemetry", "Istio", "Linkerd", "Consul", "Envoy"],
    "languages": ["python", "rust", "typescript", "javascript", "golang", "java", "c++", "c#", "swift", "kotlin", "languages", "language", "compiler", "FastAPI", "Flask", "Django", "Node.js", "Express.js", "React", "Vue.js", "Angular", "Svelte", "Next.js", "Nuxt.js", "Spring Boot", "Micronaut", "Quarkus", "JHipster", "Grails", "Play Framework", "ASP.NET Core", "Blazor", "WPF", "WinForms", "Bash", "Zsh", "Fish", "PowerShell", "Lua", "Perl", "Ruby on Rails", "Sinatra", "Hanami", "Crystal", "Elixir", "Phoenix", "Erlang", "Haskell", "OCaml", "F#", "Scala", "Clojure", "Automation", "Scripting", "Web development", "Mobile development", "Game development", "Data science", "Machine learning", "Artificial intelligence", "Cloud computing", "DevOps", "Microservices", "Serverless", "API development", "Database management", "Networking", "Security"],
    "containers": ["docker", "kubernetes", "k8s", "container", "containers", "pod", "helm", "orchestration", "containerization", "microservices", "service mesh", "container runtime", "container registry", "container networking", "container security", "container monitoring", "container logging", "container scaling", "container orchestration", "container deployment", "container management", "Ansible", "Puppet", "Chef", "OpenShift", "Rancher", "Portainer", "K3s", "K3OS", "Kubeadm", "Kubelet", "Kubectl", "Kustomize", "Helm charts", "Terraform", "CI/CD pipelines", "GitOps", "Continuous Integration", "Continuous Deployment", "Continuous Delivery", "GitLab CI", "Jenkins", "CircleCI", "Travis CI", "Argo CD", "Flux CD", "Spinnaker", "Tekton", "Drone CI", "Buildkite", "Codefresh", "Harness", "Octopus Deploy", "GitHub Actions", "Bitbucket Pipelines", "Azure DevOps", "AWS CodePipeline", "Google Cloud Build", "Cloud Native Computing Foundation (CNCF)", "Kubernetes Operators", "Kubernetes Custom Resources", "Kubernetes API", "Kubernetes Controllers", "Kubernetes CRDs", "Kubernetes Namespaces", "Kubernetes RBAC", "Kubernetes Secrets", "Kubernetes ConfigMaps", "Kubernetes Volumes", "Kubernetes Persistent Volumes", "Kubernetes StatefulSets", "Kubernetes DaemonSets", "Kubernetes Deployments", "Kubernetes Services"],
    "gpu": ["gpu", "nvidia", "amd", "intel", "graphics card", "chip", "hardware", "cuda", "processor", "cpu", "blackwell", "m4", "graphics", "rendering", "ray tracing", "ai acceleration", "tensor cores", "rt cores", "gpu architecture", "gpu performance", "gpu benchmarks", "gpu drivers", "gpu overclocking", "gpu cooling", "gpu power consumption", "gpu memory", "gpu compute", "gpu rendering", "gpu gaming", "gpu workstation", "gpu ai workloads", "gpu deep learning", "gpu machine learning", "gpu neural networks", "gpu training", "gpu inference", "gpu virtualization", "gpu cloud computing", "gpu data center", "gpu high performance computing", "gpu scientific computing", "gpu cryptocurrency mining"],
}

# Ordered menu labels for the interactive category screen (workflow.py).
FIELD_MENU = [
    ("kids", "Kids Tech & STEM"),
    ("networking", "Network Issues & Fixes"),
    ("windows", "Windows Issues & Fixes"),
    ("linux", "Linux Issues & Fixes"),
    ("cloud", "Cloud Issues & Fixes"),
    ("news", "Latest Tech News & Updates"),
    ("ai_ml", "AI/ML New Features & Updates"),
    ("models_agents", "New Models & Agents"),
    ("devops", "DevOps & Automation Tools"),
    ("languages", "Programming Languages"),
    ("containers", "Kubernetes & Docker"),
    ("gpu", "GPU & Hardware"),
]

# Generic titles used when a field has no matching trending video, so the
# menu can still produce a video for any category.
FIELD_FALLBACKS = {
    "kids": ["How Computers Work - A Guide for Kids", "Fun STEM Experiments You Can Try at Home", "Why the Sky Is Blue - Simple Science for Kids"],
    "networking": ["Why Your Wi-Fi Is Slow and How to Fix It", "What Is DNS and How Does It Work", "How a VPN Protects Your Privacy"],
    "windows": ["Fix High CPU Usage in Windows", "Windows Updates Breaking Things? Common Fixes", "Speed Up Your Windows PC - Top 10 Tips"],
    "linux": ["Linux Basics - Getting Started with the Terminal", "Fix Common Linux Boot Problems", "Linux vs Windows - Which Should You Choose"],
    "cloud": ["AWS vs Azure vs GCP in 2026", "Serverless vs Containers - Which to Choose", "Common Cloud Cost Mistakes to Avoid"],
    "news": ["This Week in Tech - Latest News Roundup", "Biggest Tech Announcements This Month", "Tech Updates You Missed This Week"],
    "ai_ml": ["What's New in AI This Month", "Machine Learning Updates You Should Know", "How AI Is Changing Everyday Tools"],
    "models_agents": ["New AI Models Explained Simply", "AI Agents - What They Can Do Now", "Comparing the Latest AI Models"],
    "devops": ["CI/CD Pipelines Explained Simply", "Infrastructure as Code with Terraform", "Top DevOps Automation Tools in 2026"],
    "languages": ["Python vs Rust - Which to Learn", "Most Popular Programming Languages in 2026", "TypeScript for Beginners"],
    "containers": ["Docker Explained in Simple Terms", "Kubernetes for Beginners", "Docker vs Podman - Which to Use"],
    "gpu": ["NVIDIA vs AMD GPUs in 2026", "How GPUs Work - Explained", "Best GPUs for AI Workloads"],
}


@dataclass
class TrendingTopic:
    title: str
    channel: str = ""
    url: str = ""
    published: str = ""
    source: str = "rss"
    score: int = 0
    topic_dict: dict = field(default_factory=dict)

    def display(self) -> str:
        return (f"  {self.title}\n"
                f"      by {self.channel or '?'}  |  {self.source}  |  score {self.score}\n"
                f"      suggested: {self.topic_dict.get('title', ''):.80s}")


def _ascii_safe(text: str) -> str:
    """Map any character not printable on the Windows cp1252 console to '?'."""
    try:
        return text.encode("cp1252", "replace").decode("cp1252")
    except Exception:
        return text.encode("ascii", "replace").decode("ascii")


def clean_title(raw: str) -> str:
    """Strip emoji, live markers, channel-name suffixes from a video title."""
    t = _EMOJI_RE.sub("", raw or "")
    t = _LIVE_RE.sub("", t)
    t = _TAG_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -:|")
    return _ascii_safe(t[:90])


def keyword_score(title: str) -> int:
    t = title.lower()
    return sum(1 for k in KEYWORDS if k in t)


def _kw_match(text: str, words: list) -> bool:
    """Word-boundary match: 'intel' must not match 'intelligent', 'pod' must
    not match 'podcast'. Digits count as word chars for things like '5g'."""
    for w in words:
        try:
            if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text):
                return True
        except re.error:
            if w in text:
                return True
    return False


def build_topic_dict(title: str) -> dict:
    """
    Generate a ready-to-use topic dict ({title, intro, points, outro}) for a
    trending video title so generate_script() can run with zero extra input.
    Tailors points to the title's keywords so scripts feel specific, not templated.
    """
    clean = clean_title(title)
    low = clean.lower()
    if _kw_match(low, ["gpu", "nvidia", "cuda", "chip", "blackwell", "m4", "amd", "intel", "graphics card", "processor", "cpu"]):
        points = [f"What {clean} is and why it matters now", f"Architecture & key specs", "Performance vs previous generation", "Real workloads where it shines", "Limitations & who should wait", "What to buy or build next"]
    elif _kw_match(low, ["agent", "agents", "reasoning", "llm", "gpt", "prompt", "chatgpt", "claude", "gemini", "grok", "new ai", "model"]):
        points = [f"Who/what {clean} is and where it comes from", f"How {clean} actually works under the hood", f"What's genuinely new about it", "How it compares to the current state of the art", "Costs, limits, risks & safety", "How to try or build with it today"]
    elif _kw_match(low, ["python", "rust", "typescript", "javascript", "golang", "java", "c++", "swift", "kotlin", "compiler", "language"]):
        points = [f"What {clean} is best used for", "Core concepts in 2 minutes", "Minimal working example", "Common mistakes & pitfalls", "When to use it vs alternatives", "Next steps & resources"]
    elif _kw_match(low, ["windows", "windows 11", "windows 12", "pc", "pcs"]):
        points = [f"The {clean} problem, framed simply", "Most common causes & culprits", "Step-by-step fixes you can apply now", "Settings to check first", "What to avoid (and why)", "When it's time to upgrade"]
    elif _kw_match(low, ["linux", "ubuntu", "debian", "kernel", "bash", "unix", "terminal", "open source"]):
        points = [f"What {clean} is and who it's for", "Getting started with the basics", "Key commands or settings that matter", "Troubleshooting common problems", "When Linux makes sense vs Windows/macOS", "Where to go next"]
    elif _kw_match(low, ["wifi", "wi-fi", "router", "dns", "network", "vpn", "ethernet", "5g", "cybersecurity", "hack"]):
        points = [f"What {clean} means in plain terms", "How it actually works", "Most common causes of problems", "Step-by-step fixes you can try", "How to prevent it going forward", "When to call a pro"]
    elif _kw_match(low, ["docker", "kubernetes", "k8s", "container", "helm", "pod"]):
        points = [f"What {clean} is and the problem it solves", "Core concepts explained simply", "A minimal runnable example", "Common mistakes & gotchas", "When it's overkill (and when it's not)", "Best practices for production"]
    elif _kw_match(low, ["devops", "ci/cd", "ci cd", "terraform", "ansible", "pipeline", "automation", "deploy", "github actions", "monitoring", "observability"]):
        points = [f"What {clean} is and why teams use it", "How the workflow fits together", "A concrete step-by-step setup", "Tools & alternatives compared", "Common pitfalls & how to avoid them", "Real-world use case + next steps"]
    elif _kw_match(low, ["cloud", "aws", "azure", "gcp", "google cloud", "serverless", "lambda", "s3", "saas", "cloudflare", "hosting"]):
        points = [f"What {clean} means and the problem it solves", "How the pieces fit together", "A hands-on example walkthrough", "Costs & pricing realities", "Common mistakes and gotchas", "When not to use it"]
    elif _kw_match(low, ["kids", "child", "toy", "lego", "stem", "school", "children"]):
        points = [f"An easy, friendly intro to {clean}", "How it works, explained like you're 10", "A simple hands-on activity or demo", "Fun facts that stick", "How grown-ups can help", "What to try next"]
    elif _kw_match(low, ["news", "update", "release", "announce", "reveals", "leaks", "recap", "event", "launch", "review"]):
        points = [f"The big story: {clean} in one paragraph", "Why it matters right now", "What changed vs before", "Public & industry reaction", "What analysts are watching", "What happens next"]
    elif _kw_match(low, ["data", "spark", "tableau", "power bi", "pandas", "analytics", "visualization"]):
        points = [f"What {clean} is for", f"How {clean} fits the data lifecycle", "Key features that matter", "Real dashboard or pipeline example", "Limitations & costs", "Where to start"]
    else:
        points = [f"What {clean} is", f"How {clean} works", "Why it matters right now", "Real-world examples", "Limits, risks, and costs", "How to get started"]
    return {
        "title": clean,
        "intro": f"{clean} is trending right now. In this video we explain what it is, how it works, where it helps, what it costs, and what to watch out for — with concrete examples.",
        "points": points,
        "outro": f"That's {clean} in a nutshell — what it is, how it works, and how to use it. If this helped, like and subscribe for the next breakdown.",
    }


class YouTubeTrending:
    """Discover current trending tech topics from YouTube."""

    def __init__(
        self,
        api_key: str = "",
        channels: list = None,
        min_keywords: int = 1,
        timeout: int = 15,
    ):
        self.api_key = (api_key or "").strip()
        self.channels = channels or DEFAULT_CHANNELS
        self.min_keywords = min_keywords
        self.timeout = timeout

    # ---- Data API v3 (exact most-popular chart) --------------------------

    def _via_data_api(self, top_n: int, region: str = "US") -> list:
        items = []
        try:
            from googleapiclient.discovery import build

            youtube = build("youtube", "v3", developerKey=self.api_key)
            # Pull up to 2 pages (100 candidates) — the mostPopular S&T chart
            # is heavily short-form; we need a large pool to filter into
            # full-length main videos.
            page_token = None
            for _ in range(2):
                if len(items) >= 100:
                    break
                kwargs = {
                    "part": "snippet,contentDetails",
                    "chart": "mostPopular",
                    "videoCategoryId": "28",  # Science & Technology
                    "regionCode": region,
                    "maxResults": min(max(top_n * 6, 30), 50),
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = youtube.videos().list(**kwargs).execute()
                for v in resp.get("items", []):
                    sn = v["snippet"]
                    title = clean_title(sn.get("title", ""))
                    if not title:
                        continue
                    # Skip Shorts / clips: our channel makes 2-5 min main videos.
                    dur = _parse_duration_secs((v.get("contentDetails") or {}).get("duration", ""))
                    low = title.lower()
                    if dur and dur < 120:
                        continue
                    if _SHORT_RE.search(low):
                        continue
                    # Hashtags don't disqualify a title (common on S&T videos);
                    # the tags are just stripped so the topic reads cleanly.
                    if _HASHTAG_RE.search(title):
                        title = _HASHTAG_RE.sub("", title).strip()
                        if not title:
                            continue
                    items.append(TrendingTopic(
                        title=title,
                        channel=sn.get("channelTitle", ""),
                        url=f"https://www.youtube.com/watch?v={v['id']}",
                        published=sn.get("publishedAt", ""),
                        source="youtube-api",
                        score=20 + keyword_score(title),
                        topic_dict=build_topic_dict(title),
                    ))
                nxt = resp.get("nextPageToken")
                if not nxt:
                    break
                page_token = nxt
        except Exception as e:
            print(f"  [trending] Data API failed ({e}) — falling back to RSS.")
        return items

    # ---- Data API search (recent tech videos; replaces dead RSS feeds) -------

    def _via_search(self, top_n: int, queries: list = None) -> list:
        '''Recent, high-view tech videos via the Data API search endpoint.

        RSS channel feeds are no longer reachable, so this replaces them as the
        secondary source. Returns TrendingTopic objects sorted by view count.
        '''
        if not queries:
            queries = ["technology news", "programming tutorial", "AI machine learning"]
        items = []
        try:
            from googleapiclient.discovery import build
            import datetime
            youtube = build("youtube", "v3", developerKey=self.api_key)
            week_ago = (datetime.datetime.now(datetime.timezone.utc)
                        - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for q in queries:
                try:
                    resp = youtube.search().list(
                        part="snippet",
                        q=q,
                        type="video",
                        order="viewCount",
                        publishedAfter=week_ago,
                        regionCode="US",
                        relevanceLanguage="en",
                        maxResults=min(max(top_n, 8), 15),
                    ).execute()
                except Exception as e:
                    print(f"  [trending] search '{q}' failed: {e}")
                    continue
                for v in resp.get("items", []):
                    sn = v.get("snippet", {}) or {}
                    vid = (v.get("id", {}) or {}).get("videoId", "")
                    title = clean_title(sn.get("title", ""))
                    if not title:
                        continue
                    low = title.lower()
                    if _SHORT_RE.search(low):
                        continue
                    # Keep only tech-relevant results.
                    if keyword_score(title) < 1:
                        continue
                    if _HASHTAG_RE.search(title):
                        title = _HASHTAG_RE.sub("", title).strip()
                        if not title:
                            continue
                    items.append(TrendingTopic(
                        title=title,
                        channel=sn.get("channelTitle", ""),
                        url=f"https://www.youtube.com/watch?v={vid}" if vid else "",
                        published=sn.get("publishedAt", ""),
                        source="youtube-search",
                        score=10 + keyword_score(title),
                        topic_dict=build_topic_dict(title),
                    ))
        except Exception as e:
            print(f"  [trending] search failed: {e}")
        return items

    # ---- RSS feeds (legacy, mostly dead now) --------------------------------

    def _channel_feed(self, channel_id: str, limit: int = 5) -> list:
        """Latest uploads from a channel RSS feed."""
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"  [trending] feed {channel_id}: {e}")
            return []
        out = []
        for i, entry in enumerate(root.findall(
                "{http://www.w3.org/2005/Atom}entry")):
            if i >= limit:
                break
            title_el = entry.find("{http://www.w3.org/2005/Atom}title")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            pub_el = entry.find("{http://www.w3.org/2005/Atom}published")
            title = clean_title(title_el.text if title_el is not None else "")
            if not title:
                continue
            url = link_el.get("href", "") if link_el is not None else ""
            out.append((title, url, pub_el.text if pub_el is not None else ""))
        return out

    def _via_rss(self, top_n: int) -> list:
        items = []
        for name, cid in self.channels:
            for title, url, published in self._channel_feed(cid):
                score = keyword_score(title) + 8
                if score >= self.min_keywords + 8:
                    items.append(TrendingTopic(
                        title=title, channel=name, url=url,
                        published=published, source="rss",
                        score=score, topic_dict=build_topic_dict(title),
                    ))
        return items

    # ---- Public ------------------------------------------------------------

    def discover(self, top_n: int = 12, region: str = "US", field: str = "",
                 field_strict: bool = False) -> list:
        """
        Return up to top_n trending topics. Uses the Data API most-popular
        chart when a key is set, otherwise RSS channel feeds. Sorted by score.

        When `field` is a key in FIELD_KEYWORDS (e.g. "windows", "containers",
        "ai_ml"), items are ranked by how strongly they match that field's
        keywords. Only genuinely matching topics are returned for a field.

        When nothing matches:
          - field_strict=True  -> returns an empty list (caller decides the
            fallback, e.g. a curated FIELD_FALLBACKS topic or a message), so
            a user picking a category never sees irrelevant topics.
          - field_strict=False -> surfaces the best Science & Tech items so
            menus/categories are never empty.
        """
        # Combine both sources so the pool is diverse: the Data API gives the
        # current most-popular chart (consumer-heavy), while the RSS channels
        # give educational/technical uploads. Merging prevents the same handful
        # of consumer topics from dominating every run.
        items = []
        if self.api_key:
            items.extend(self._via_data_api(top_n, region))
            # Search supplements the mostPopular chart with recent tech videos.
            items.extend(self._via_search(top_n, queries=["technology news", "programming tutorial", "AI machine learning", "cybersecurity"]))

        # De-dupe by normalized title, keeping the higher score.
        seen = {}
        for it in items:
            key = re.sub(r"[^a-z0-9]+", " ", it.title.lower()).strip()[:60]
            prev = seen.get(key)
            if prev is None or it.score > prev.score:
                seen[key] = it
        items = list(seen.values())

        if field and field in FIELD_KEYWORDS:
            kw = FIELD_KEYWORDS[field]
            ranked = []
            for it in items:
                text = f"{it.title} {it.channel} {it.topic_dict.get('title', '')}".lower()
                hits = sum(1 for k in kw if _kw_match(text, [k]))
                ranked.append((hits, it))
            ranked.sort(key=lambda t: (t[0], t[1].score), reverse=True)
            matched = [it for hits, it in ranked if hits > 0]
            if matched:
                # Strongest field matches first.
                items = matched
            else:
                # No live match for this field: drop to the curated safety
                # net instead of mixing in unrelated general topics. The
                # general pool fallback is only for display contexts.
                if not field_strict:
                    items = sorted(items, key=lambda i: (i.score, i.published or ""), reverse=True)[:top_n]
                else:
                    items = []

        # Dedupe by normalized title.
        seen = set()
        unique = []
        for it in sorted(items, key=lambda i: (i.score, i.published or ""), reverse=True):
            key = it.title.lower()[:60]
            if key not in seen:
                seen.add(key)
                unique.append(it)

        # Ensure educational (RSS) topics surface alongside the API chart so the
        # pool isn't dominated by consumer tech and the same items don't repeat.
        api_items = [it for it in unique if it.source == "youtube-api"]
        sec_items = [it for it in unique if it.source != "youtube-api"]
        if sec_items and api_items:
            sec_quota = min(len(sec_items), max(1, top_n // 3))
            api_quota = top_n - sec_quota
            picked = api_items[:api_quota] + sec_items[:sec_quota]
            # Re-sort by score for display.
            picked.sort(key=lambda i: (i.score, i.published or ""), reverse=True)
            return picked[:top_n]
        return unique[:top_n]


def _resolve_api_key() -> str:
    """Resolve the YouTube Data API key from env vars (incl. .env), then config."""
    import os
    import yaml

    # .env file (if present) — same parsing as workflow._load_dotenv.
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass

    api_key = ""
    try:
        if os.path.exists("config.yaml"):
            with open("config.yaml", "r") as f:
                api_key = (yaml.safe_load(f).get("youtube", {}) or {}).get("data_api_key", "") or ""
    except Exception:
        pass
    # Correct env var; YT_DATA_API_KEY kept as a legacy fallback.
    return (os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()
            or os.environ.get("YT_DATA_API_KEY", "").strip()
            or api_key)


def main():
    """CLI: list trending topics + suggested concepts."""
    import os

    api_key = _resolve_api_key()
    t = YouTubeTrending(api_key=api_key)
    print(f"Source: {'YouTube Data API' if api_key else 'RSS feeds (free, no key)'}\n")
    for i, it in enumerate(t.discover(top_n=12), 1):
        print(f"{i}. {it.display()}\n")


if __name__ == "__main__":
    main()
