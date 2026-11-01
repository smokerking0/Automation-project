"""
Prompt-driven educational workflow generator.

Turns a free-form user prompt ("How to configure IIS", "How to install
Docker", "How to create a Linux user", ...) into a complete, topic-specific
teaching workflow. Nothing here is hard-coded to a single topic; every
section, step, example, command, configuration and error is derived from the
actual prompt text.

Pipeline:

    analyze_prompt(prompt)      -> TopicContext
    generate_workflow(prompt)   -> WorkflowScript  (list of WorkflowStep)
    choose_visual_type(step)    -> visual_type string
    dedup_workflow(workflow)    -> deduplicated (narration + steps)
    validate_workflow(...)      -> (ok, issues, fixed steps)
    workflow_to_scenes(script)  -> list[video.Scene] for the renderer

The same content model is consumed by workflow.py and gui.py so visuals,
narration and captions always describe the user's prompt.
"""

import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple

from video import Scene

# ---------------------------------------------------------------------------
# Domain classifier
# ---------------------------------------------------------------------------

DOMAIN_ALIASES = {
    "windows_admin": ["active directory", "domain controller", "windows server",
                      "group policy", "ad ds", "server manager", "windows",
                      "iis", "dhcp server", "rdp", "ou", "organizational unit"],
    "linux": ["linux", "ubuntu", "debian", "centos", "arch", "fedora", "bash",
              "shell", "apt", "yum", "dnf", "cron", "ssh", "chmod", "chown",
              "systemctl", "ufw", "iptables", "linux user"],
    "programming": ["python", "java", "javascript", "typescript", "c++", "golang",
                    "go ", "rust", "php", "flask", "django", "rest api", "api",
                    "function", "class", "program", "script", "compile", "debug",
                    "code", "variable", "loop", "web app", "library", "package"],
    "networking": ["router", "switch", "vlan", "firewall", "gateway", "subnet",
                   "dns server", "dhcp", "ping", "traceroute", "netstat",
                   "network", "nat", "static ip", "ip address", "cisco"],
    "devops": ["docker", "kubernetes", "k8s", "pod", "dockerfile", "ci/cd",
               "jenkins", "gitlab", "terraform", "ansible", "deploy", "pipeline",
               "container", "compose", "helm", "yaml", "cdk"],
    "database": ["postgresql", "postgres", "mysql", "mariadb", "sqlite",
                 "mongodb", "redis", "database", "table", "query", "schema",
                 "index", "sql", "crud"],
    "cloud": ["aws", "azure", "gcp", "s3", "ec2", "lambda", "cloud", "bucket",
              "iam", "vpc", "azure ", "service", "serverless"],
    "security": ["vpn", "ssl", "tls", "encrypt", "encryption", "firewall rule",
                 "authentication", "2fa", "password", "cybersecurity", "penetration",
                 "audit log", "access control", "certificate", "php", "web shell",
                 "shell", "vulnerability", "vulnerabilities", "exploit", "backdoor",
                 "rce", "injection", "malware"],
    "ai_ml": ["machine learning", "neural", "model", "gpt", "dataset", "training",
              "inference", "deep learning", "nlp", "computer vision", "tensorflow",
              "pytorch", "agent"],
}

LANG_HINTS = {
    "python": ["python", "flask", "django", "pip"],
    "javascript": ["javascript", "node", "npm", "react", "typescript", "express"],
    "javascript": ["javascript", "node", "npm", "react", "typescript", "express"],
    "typescript": ["typescript"],
    "java": ["java", "spring", "maven", "jdk"],
    "c++": ["c++", "cpp"],
    "c": ["c language", " c "],
    "golang": ["golang", "go "],
    "rust": ["rust"],
    "php": ["php", "composer"],
    "bash": ["bash", "shell script", "zsh"],
    "sql": ["sql", "postgresql", "mysql", "sqlite"],
    "yaml": ["yaml", "yml", "docker-compose", "kubernetes yaml"],
}

# ---------------------------------------------------------------------------
# Small deterministic value generators used to build realistic examples from
# the subject (never from another topic's content).
# ---------------------------------------------------------------------------

def _slug(text: str, max_len: int = 24) -> str:
    """Small identifier derived from text (lowercase, underscores)."""
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:max_len] or "item"


def _words(text: str, n: int = 3) -> str:
    parts = [w for w in re.split(r"[^a-zA-Z0-9]+", text or "") if w]
    return "_".join(parts[:n]).lower() or "item"


def _title_case(text: str) -> str:
    return " ".join(w.capitalize() for w in (text or "").split())


# ---------------------------------------------------------------------------
# Content model
# ---------------------------------------------------------------------------

@dataclass
class TopicContext:
    """Everything the downstream generators need about the user's prompt."""
    prompt: str
    title: str                       # display title
    subject: str                     # main noun phrase ("active directory")
    domain: str                      # one of DOMAIN_ALIASES keys / "general"
    goal: str                        # what the user wants to do
    language: str = "python"         # for programming domain
    difficulty: str = "beginner"
    keywords: List[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)


@dataclass
class WorkflowStep:
    """One teaching step. Everything the renderer + TTS need in one place."""
    section: str                     # introduction|prerequisites|step|example|error|troubleshooting|verification|summary
    title: str
    explanation: str                 # on-screen explanatory line
    narration: str                   # spoken text (the source of truth)
    visual_type: str = "title_scene"
    visual_content: dict = field(default_factory=dict)
    actions: List[dict] = field(default_factory=list)
    code: Optional[str] = None
    command: Optional[str] = None
    commands: List[str] = field(default_factory=list)
    config: Optional[str] = None
    expected_output: Optional[str] = None
    error_example: Optional[str] = None
    bullets: List[str] = field(default_factory=list)
    duration: float = 5.0


@dataclass
class WorkflowScript:
    """Complete prompt-derived learning script."""
    title: str
    description: str
    tags: List[str]
    steps: List[WorkflowStep]
    domain: str = "general"
    prompt: str = ""
    estimated_duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "title": self.title, "description": self.description,
            "tags": self.tags, "domain": self.domain, "prompt": self.prompt,
            "estimated_duration": self.estimated_duration,
            "steps": [asdict(s) for s in self.steps],
        }


# ---------------------------------------------------------------------------
# Prompt analysis
# ---------------------------------------------------------------------------

_GOAL_VERBS = ["configure", "install", "setup", "set up", "deploy", "create",
               "troubleshoot", "fix", "secure", "build", "run", "enable",
               "monitor", "backup", "migrate", "learn", "understand"]
_FILLER = r"(question|please|thanks|thank you|step by step|for beginners|for dummies|in detail|asap|now|today|help me|how do i|how can i|how to|what is|what are|show me|learn|guide|tutorial)"


def _detect_domain(text: str) -> str:
    low = " " + text.lower() + " "
    scored: Dict[str, int] = {}
    for dom, kws in DOMAIN_ALIASES.items():
        hits = sum(1 for k in kws if k in low)
        if hits:
            scored[dom] = hits
    if not scored:
        return "general"
    # ties resolved by longest keyword match (more specific domain wins)
    return max(scored, key=lambda d: (scored[d], max(len(k) for k in DOMAIN_ALIASES[d] if k in low)))


def _detect_language(text: str) -> str:
    low = text.lower()
    for lang, hints in LANG_HINTS.items():
        if any(h in low for h in hints):
            return lang
    return "python"


def _clean_subject(raw: str) -> str:
    s = re.sub(_FILLER, " ", raw, flags=re.IGNORECASE)
    s = s.replace("?", "").replace("!", "").strip(" .,;:-")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(a|an|the)\s+", "", s, flags=re.IGNORECASE)
    return s or raw.strip("? ")


def analyze_prompt(prompt: str) -> TopicContext:
    """Understand the user's prompt: topic, domain, goal, language."""
    p = (prompt or "").strip()
    low = p.lower()
    domain = _detect_domain(low)
    lang = _detect_language(low) if domain == "programming" else ""

    # Subject: prefer an explicit "Topic:" line or a short title-like first line.
    subj = None
    m_topic = re.search(r"topic\s*[:\-]\s*([^\n]{3,120})", low)
    if m_topic:
        subj = _clean_subject(m_topic.group(1))
    if not subj:
        first_line = p.splitlines()[0].strip() if p.splitlines() else p
        if 3 <= len(first_line) <= 100 and "\n" not in first_line[:1]:
            # Use the first line if it looks like a title (not a long instruction).
            if len(first_line.split()) <= 12:
                subj = _clean_subject(first_line)
    # Fall back to verb-led extraction for genuine instructions.
    if not subj or len(subj) < 3:
        m = re.search(r"(?:how to|how do i|how can i|what is|what are|show me|guide|steps? to|learn|understand|troubleshoot|configure|install|setup|set up|deploy|create|build|run|enable|secure|fix)\s+([^.!?\n]{1,80})", low)
        if m:
            subj = _clean_subject(m.group(1))
    if not subj or len(subj) < 2:
        subj = _clean_subject(p.splitlines()[0] if p.splitlines() else p)
    if not subj:
        subj = "this topic"
    if len(subj) > 150:
        subj = subj[:147].rsplit(" ", 1)[0]

    title = _title_case(subj)
    if not title:
        title = "Tech Tutorial"

    goal = "help you master " + subj
    for v in _GOAL_VERBS:
        if v in low:
            goal = _title_case(v).replace(" ", "") + " " + subj
            break

    difficulty = "beginner"
    if "advanced" in low or "expert" in low or "production" in low:
        difficulty = "advanced"
    elif "intermediate" in low or "experienced" in low:
        difficulty = "intermediate"

    kws = sorted({w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", p)
                  if w.lower() not in _FILLER.split()} | {subj.lower()})
    return TopicContext(
        prompt=p, title=title, subject=subj, domain=domain,
        goal=goal, language=lang or "python", difficulty=difficulty,
        keywords=kws,
        context={"sections": _plan_sections(domain)},
    )


def _plan_sections(domain: str) -> List[str]:
    """Choose the section sequence for a topic domain (never every section)."""
    plans = {
        "windows_admin": ["introduction", "prerequisites", "step", "step", "step",
                          "step", "error", "verification", "summary"],
        "linux": ["introduction", "prerequisites", "step", "step", "step",
                  "error", "verification", "summary"],
        "programming": ["introduction", "prerequisites", "step", "example",
                        "step", "error", "step", "verification", "summary"],
        "networking": ["introduction", "prerequisites", "step", "example",
                       "error", "step", "verification", "summary"],
        "devops": ["introduction", "prerequisites", "step", "step",
                   "error", "verification", "summary"],
        "database": ["introduction", "prerequisites", "step", "step",
                     "example", "error", "verification", "summary"],
        "cloud": ["introduction", "prerequisites", "step", "step",
                  "error", "verification", "summary"],
        "security": ["introduction", "prerequisites", "step", "step",
                     "error", "verification", "summary"],
        "ai_ml": ["introduction", "prerequisites", "step", "example",
                  "error", "verification", "summary"],
        "general": ["introduction", "step", "example", "step",
                    "error", "verification", "summary"],
    }
    return plans.get(domain, plans["general"])


# ---------------------------------------------------------------------------
# Step factory
# ---------------------------------------------------------------------------

def _mk(section: str, title: str, narration: str, *, visual_type: str = "",
        visual_content: Optional[dict] = None, actions: Optional[List[dict]] = None,
        code: Optional[str] = None, command: Optional[str] = None,
        commands: Optional[List[str]] = None, config: Optional[str] = None,
        expected_output: Optional[str] = None, error_example: Optional[str] = None,
        bullets: Optional[List[str]] = None, duration: float = 5.0) -> WorkflowStep:
    vc = visual_content or {}
    if not visual_type:
        visual_type = "title_scene"
    return WorkflowStep(
        section=section, title=title, explanation=title, narration=narration,
        visual_type=visual_type, visual_content=vc, actions=actions or [],
        code=code, command=command, commands=commands or [], config=config,
        expected_output=expected_output, error_example=error_example,
        bullets=bullets or [], duration=duration,
    )


# ---------------------------------------------------------------------------
# Per-domain builders (each produces topic-specific steps from the context)
# ---------------------------------------------------------------------------

def _prereq_step(subject: str, domain: str) -> WorkflowStep:
    prereqs = {
        "windows_admin": ["Administrator access on the Windows Server",
                          "A static IP address reserved for this server",
                          "A stable hostname", "The server joined or ready to configure"],
        "linux": ["A Linux machine (Ubuntu, Debian, or RHEL style)",
                  "A user account with sudo or root privileges",
                  "A terminal open on the machine"],
        "programming": ["A code editor or IDE", f"The environment for {subject}",
                        "A terminal to run your program", "Knowledge of basic syntax"],
        "networking": ["Access to the device console or SSH/HTTP management",
                       "The current IP plan and gateway details",
                       "Administrator credentials for the device"],
        "devops": ["The {tool} command line tool installed and reachable",
                   "Credentials or tokens needed to authenticate",
                   "A place to store configuration files"],
        "database": ["The database service installed or installable",
                     "A client such as psql, mysql, or an SDK",
                     "Connection details (host, port, user)"],
        "cloud": ["A cloud account with billing enabled",
                  "The CLI or web console installed",
                  "IAM credentials with the right permissions"],
        "security": ["The target system you are protecting",
                     "A backup/rollback plan before changing settings",
                     "Administrator access to apply the changes"],
        "ai_ml": ["Python with the relevant library installed",
                  "A prepared dataset or example input",
                  "Enough compute for the task"],
        "general": ["Access to the system or software involved",
                    "Time to test each step before moving on"],
    }.get(domain, ["Access to the system or software involved"])
    bullets = [p.replace("{tool}", subject) for p in prereqs]
    narr = ("Before we start, make sure you have everything ready. " +
            " ".join(bullets) + " This avoids failures halfway through and " +
            "makes it easy to verify your result at the end.")
    return _mk("prerequisites", "Prerequisites",
               narr, bullets=bullets, visual_type="list_scene",
               visual_content={"title": "Prerequisites for " + _title_case(subject),
                               "items": bullets})


def _intro_step(ctx: TopicContext) -> WorkflowStep:
    subj = ctx.subject
    narr = (f"In this video we {ctx.goal}. We will go step by step: what "
            f"{subj} is, how to prepare, the exact actions to take, what you "
            f"should see at each point, and how to fix the common mistakes "
            f"you will run into. By the end you will have a working setup you "
            f"can verify yourself.")
    return _mk("introduction", "What we are building",
               narr, visual_type="list_scene",
               visual_content={"title": ctx.title,
                               "subtitle": ctx.goal + " - step by step",
                               "items": ["What we are doing", "Why it matters",
                                         "How you will verify the result"]},
               bullets=["What we are doing", "Why it matters",
                        "How you will verify the result"],
               duration=6.0)


def _summary_step(ctx: TopicContext) -> WorkflowStep:
    subj = ctx.subject
    narr = (f"Let us recap what we did with {subj}. We prepared the "
            f"environment, followed the exact steps, discovered how to verify "
            f"the result, and handled the common error by understanding its "
            f"cause. You can now repeat this workflow on your own system with "
            f"confidence. Practice it once more and it will stick.")
    return _mk("summary", "Summary", narr, visual_type="workflow_scene",
               visual_content={"title": "Summary",
                               "nodes": _plan_flow(ctx)},
               bullets=["Prepared the environment", "Followed each step",
                        "Verified the result", "Fixed the common error"],
               duration=6.0)


def _plan_flow(ctx: TopicContext) -> List[str]:
    base = ["Prepare", "Configure", "Verify", "Troubleshoot"]
    subj = _title_case(ctx.subject)
    return [f"{base[0]} {subj}", f"{base[1]} {subj}",
            f"Check {subj} works", "Fix errors", "Done"]


# ---------------- programming ----------------------------------------------

def _code_example(ctx: TopicContext) -> dict:
    """Topic-derived code sample for the programming domain."""
    subj = ctx.subject.lower()
    lang = ctx.language
    if lang == "python":
        if "fibonacci" in subj:
            return {
                "filename": "fibonacci.py", "highlight": "def fibonacci(",
                "code": "def fibonacci(n):\n"
                        "    seq = [0, 1]\n"
                        "    while len(seq) < n:\n"
                        "        seq.append(seq[-1] + seq[-2])\n"
                        "    return seq[:n]\n\n"
                        "print(fibonacci(10))",
                "output": "[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]",
            }
        if "sort" in subj or "ordering" in subj:
            return {
                "filename": "sort_demo.py", "highlight": "sorted(",
                "code": "data = [42, 7, 19, 3, 88]\n"
                        "ordered = sorted(data)\n"
                        "print(ordered)",
                "output": "[3, 7, 19, 42, 88]",
            }
        if "api" in subj or "rest" in subj or "server" in subj:
            return {
                "filename": "app.py", "highlight": "def hello(",
                "code": "from flask import Flask\n"
                        "app = Flask(__name__)\n\n"
                        "@app.route('/')\n"
                        "def hello():\n"
                        "    return '{\"status\": \"ok\"}'\n\n"
                        "app.run(port=5000)",
                "output": " * Running on http://127.0.0.1:5000",
            }
        if "file" in subj or "read" in subj or "write" in subj:
            return {
                "filename": "file_demo.py", "highlight": "open(",
                "code": "with open('data.txt', 'w') as f:\n"
                        "    f.write('hello from ' + 'the demo')\n\n"
                        "with open('data.txt') as f:\n"
                        "    print(f.read())",
                "output": "hello from the demo",
            }
        if "class" in subj or "object" in subj:
            return {
                "filename": "model.py", "highlight": "class ",
                "code": "class " + _title_case(_words(subj)).replace(" ", "") + ":\n"
                        "    def __init__(self, name):\n"
                        "        self.name = name\n\n"
                        "item = " + _title_case(_words(subj)).replace(" ", "") + "('demo')\n"
                        "print(item.name)",
                "output": "demo",
            }
        return {
            "filename": _slug(subj, 16)[:-1] + ".py",
            "highlight": "def main(",
            "code": f"# {_title_case(subj)} demo\ndef main():\n"
                    f"    result = process_{_words(subj)}()\n"
                    f"    print(result)\n\n"
                    f"def process_{_words(subj)}():\n"
                    f"    return 'ok for ' + '{subj}'\n\n"
                    "if __name__ == '__main__':\n"
                    "    main()",
            "output": f"ok for {subj}",
        }
    # Non-python: generic but topic-derived skeleton.
    return {
        "filename": f"{_words(subj,2)}.{_ext_for(lang)}",
        "highlight": "main",
        "code": f"// {_title_case(subj)} example\n"
                f"fn main() {{\n"
                f"    println!(\"prepare {subj}\");\n"
                f"    let result = run::<{_title_case(lang)}>(&\"{subj}\");\n"
                f"    println!(\"{{}}\", result);\n"
                f"}}\n",
        "output": f"ok from {subj}",
    }


def _ext_for(lang: str) -> str:
    return {"golang": "go", "javascript": "js", "typescript": "ts",
            "c++": "cpp"}.get(lang, lang)

# ---------------- networking -----------------------------------------------

def _networking_values(subject: str) -> dict:
    low = subject.lower()
    if "vlan" in low:
        return {"fields": [["VLAN ID", "10"], ["Name", _words(subject, 2).title()],
                           ["Subnet", "192.168.10.0/24"], ["Gateway", "192.168.10.1"]],
                "verify": "show vlan brief",
                "err": "VLAN 10 does not exist - create it first with 'vlan 10'"}
    if "static ip" in low or "ip address" in low:
        return {"fields": [["IP Address", "192.168.1.10"], ["Subnet Mask", "255.255.255.0"],
                           ["Default Gateway", "192.168.1.1"], ["DNS", "192.168.1.10"]],
                "verify": "ipconfig /all  (or: ip addr show)",
                "err": "Duplicate IP detected - another host already uses 192.168.1.10"}
    if "dns" in low:
        return {"fields": [["DNS Server", "192.168.1.10"], ["Zone", _words(subject, 2) + ".local"],
                           ["Forwarder", "8.8.8.8"], ["TTL", "300"]],
                "verify": "nslookup " + _words(subject) + ".local",
                "err": "Server FAIL - the zone does not exist on this DNS server"}
    return {"fields": [["IP Address", "192.168.1.10"], ["Subnet Mask", "255.255.255.0"],
                       ["Default Gateway", "192.168.1.1"], ["DNS", "192.168.1.10"]],
            "verify": "ping 192.168.1.1",
            "err": "Request timed out - no route back to the device"}


# ---------------- database -------------------------------------------------

def _db_values(subject: str) -> dict:
    low = subject.lower()
    db = "postgresql" if any(k in low for k in ["postgres", "postgresql"]) else \
         ("mysql" if "mysql" in low or "mariadb" in low else "sqlite")
    name = _words(subject, 2).replace("_", "") or ("appdb" if db == "sqlite" else "appdb")
    if db == "mysql":
        start = "systemctl start mysqld"
        client = "mysql -u root -p"
    elif db == "sqlite":
        start = "sqlite3 " + name + ".db"
        client = "sqlite3 " + name + ".db"
    else:
        start = "systemctl start postgresql"
        client = "psql -U postgres"
    return {"db": db, "name": name, "start": start, "client": client,
            "create": f"CREATE DATABASE {name};",
            "select": f"SELECT 'connected to {name}' AS status;",
            "err": "Connection refused - is the {db} service running and listening on the right port?",
            "port": "3306" if db == "mysql" else ("5432" if db == "postgresql" else "local file")}


# ---------------- devops ---------------------------------------------------

def _devops_values(subject: str) -> dict:
    low = subject.lower()
    if "docker" in low:
        return {
            "img": _words(subject, 1) or "app",
            "run": f"docker run -d --name {_words(subject,1) or 'demo'} -p 8080:80 nginx",
            "ps": "docker ps",
            "yaml": "services:\n  app:\n    image: nginx:latest\n    ports:\n      - \"8080:80\"",
            "err": "Cannot connect to the Docker daemon - is Docker Desktop/runtime running?",
            "verify": "curl -I http://localhost:8080",
        }
    if "kubernetes" in low or "k8s" in low or "pod" in low:
        return {
            "img": _words(subject, 1) or "demo",
            "run": f"kubectl create deployment {_words(subject,1) or 'demo'} --image=nginx",
            "ps": "kubectl get pods",
            "yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: " + (_words(subject,1) or "demo"),
            "err": "Unable to connect to the server - is kubeconfig pointing at the right cluster?",
            "verify": "kubectl rollout status deployment/" + (_words(subject,1) or "demo"),
        }
    if "terraform" in low:
        return {
            "img": "terraform",
            "run": "terraform plan",
            "ps": "terraform apply -auto-approve",
            "yaml": "resource \"aws_instance\" \"demo\" {\n  ami           = \"ami-0c02fb55956c7d316\"\n  instance_type = \"t3.micro\"\n}",
            "err": "Error: No configuration files found - run 'terraform init' first",
            "verify": "terraform state list",
        }
    if "ansible" in low:
        return {
            "img": "ansible",
            "run": "ansible-playbook -i inventory.ini playbook.yml",
            "ps": "ansible all -m ping",
            "yaml": "---\n- hosts: all\n  tasks:\n    - name: Ensure " + _words(subject,1) + " is installed\n      apt:\n        name: " + _words(subject,1) + "\n        state: present",
            "err": "The authenticity of host cannot be established - add the host key or use ssh-keyscan",
            "verify": "ansible all -a 'uptime'",
        }
    if "ci" in low or "pipeline" in low or "jenkins" in low or "gitlab" in low:
        return {
            "img": "pipeline",
            "run": "git push origin main",
            "ps": "gitlab-ci-status",
            "yaml": "stages: [test, build, deploy]\nvariables:\n  IMAGE: " + _words(subject, 1) or "app",
            "err": "pipeline failed at 'test' stage - check the failing job log",
            "verify": "Pipeline shows all stages green",
        }
    return {
        "img": _words(subject, 1) or "app",
        "run": "docker run -d --name demo -p 8080:80 nginx",
        "ps": "docker ps",
        "yaml": "services:\n  app:\n    image: nginx:latest\n    ports:\n      - \"8080:80\"",
        "err": "Encountered a deployment error - read the log with: docker logs demo",
        "verify": "curl -I http://localhost:8080",
    }


# ---------------------------------------------------------------------------
# The main domain builders
# ---------------------------------------------------------------------------

def _build_windows_admin(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    low = subj.lower()
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "windows_admin"))

    # Identify the role/task from the subject.
    site_name = None
    host = ""
    https = False
    if any(k in low for k in ["active directory", "domain controller", "ad ds", "forest"]):
        role, verify, wizard, config_fields = (
            "Active Directory Domain Services",
            "dcdiag /c",
            "Add roles and features",
            [["Forest root domain", _words(subj, 2) + ".local"],
             ["DNS option", "Install DNS with AD DS"], ["DSRM password", "********"]])
    elif "dns" in low:
        role, verify, wizard, config_fields = (
            "DNS Server", "nslookup " + _words(subj) + ".local",
            "Add roles and features", [["Zone name", _words(subj, 2) + ".local"],
                                       ["Zone type", "Primary"], ["Allow dynamic updates", "Yes"]])
    elif "iis" in low:
        role, verify, wizard, config_fields = (
            "Web Server (IIS)", "iisreset /status",
            "Add roles and features", [["Install type", "Role-based"],
                                       ["Features", "Default Web Server"], ["Default site", "Default Web Site"]])
        site_name = _words(subj, 2) + "_site"
        host_hdr = re.search(r"host header[:\s:=]+([\w.\-]+)", low)
        cand = host_hdr.group(1) if host_hdr else ""
        if cand and "." in cand and not re.match(
                r"^(and|or|to|for|with|on|the|a|an|enforce|use|set|make|ensure)$", cand):
            host = cand
        else:
            host = "www." + _words(subj, 2) + ".local"
        https = bool(re.search(r"https|ssl|tls|certificate|self[ -]signed", low))
    elif "dhcp" in low:
        role, verify, wizard, config_fields = (
            "DHCP Server", "Get-DhcpServerInDC",
            "Add roles and features", [["Scope name", _words(subj, 2)], ["Start IP", "192.168.1.100"],
                                       ["End IP", "192.168.1.200"], ["Lease duration", "8 hours"]])
    elif "rdp" in low or "remote desktop" in low:
        role, verify, wizard, config_fields = (
            "Remote Desktop Services", "netstat -an | findstr 3389",
            "System properties", [["Remote Desktop", "Enable"], ["Port", "3389"],
                                  ["Network level auth", "Enabled"]])
    else:
        role, verify, wizard, config_fields = (
            "Server role", "Get-WindowsFeature | Where Enabled",
            "Server Manager", [["Role", "Select the role you need"], ["Features", "Defaults"]])

    # Static IP config step.
    ipvals = [["IPv4 address", "192.168.1.10"], ["Subnet mask", "255.255.255.0"],
              ["Gateway", "192.168.1.1"], ["Preferred DNS", "192.168.1.10"]]
    steps.append(_mk(
        "step", f"Assign a static IP for {subj}",
        "First give the server a fixed IP address so every client can always "
        "find it. Open the network adapter settings and enter these values, "
        "then save. A static address matters because a DHCP lease can change, "
        "which would break every client that depends on this server.",
        visual_type="form_scene",
        visual_content={"title": "Network configuration", "fields": ipvals},
        actions=[{"action": "navigate", "target": "Network settings"},
                 {"action": "enter", "target": "IPv4 address"}],
        bullets=[f"{k} = {v}" for k, v in ipvals],
    ))

    # Rename / base server config step.
    steps.append(_mk(
        "step", "Name the server clearly",
        f"Give the server a clear name such as {_words(subj, 2).upper()}-SRV-01. "
        "A meaningful hostname makes it obvious what this machine does when you "
        "look at the network later, and some roles require it before you can "
        "promote or configure the server.",
        visual_type="form_scene",
        visual_content={"title": "Computer name", "fields": [["Computer name", _words(subj, 2).upper() + "-SRV-01"], ["Workgroup", "WORKGROUP"]]},
        actions=[{"action": "click", "target": "Rename this PC"}],
        bullets=["Use a short, meaningful name", "The change applies after restart"],
    ))

    # Install role step (GUI + cursor).
    steps.append(_mk(
        "step", f"Install the {role} role",
        f"Open {wizard} in Server Manager, click Next through the wizard, and "
        f"select the {role} role. Installing the role only adds the software; "
        f"the actual server still needs to be configured in the next step, so "
        f"do not close the wizard until that part is done.",
        visual_type="gui_scene",
        visual_content={"window": "Server Manager - Add roles", "role": role,
                        "elements": [{"kind": "button", "label": "Install",
                                      "center": (560, 400)},
                                     {"kind": "checkbox", "label": role, "checked": True,
                                      "center": (360, 280)}]},
        actions=[{"action": "click", "target": role},
                 {"action": "click", "target": "Install"}],
        bullets=[f"Role: {role}", "Follow the wizard, keep defaults"],
    ))

    # Configure the server (wizard / config values).
    steps.append(_mk(
        "step", "Configure the server after install",
        "After the role installs, the configuration wizard opens. Enter the "
        "values below. These settings define how the server behaves: the "
        "domain or zone name it serves, and the administrative password you "
        "will need for recovery. Double-check each field before continuing.",
        visual_type="config_scene",
        visual_content={"title": "Configuration wizard", "entries": config_fields},
        actions=[{"action": "enter", "target": "configuration fields"}],
        config="\n".join(f"{k} = {v}" for k, v in config_fields),
        bullets=[f"{k}: {v}" for k, v in config_fields],
    ))

    # Website + HTTPS steps only when the prompt actually asks for them.
    if role.startswith("Web Server (IIS)") and site_name and (
            "host header" in low or "binding" in low or "website" in low
            or "web site" in low or "site" in low or https):
        site_path = "C:\\inetpub\\wwwroot\\" + _slug(subj, 10)
        steps.append(_mk(
            "step", "Create the website with a custom host header",
            f"Now add a new site for {subj} instead of using the default one. "
            f"Give the site the name {site_name}, point it at {site_path}, and "
            f"set the host header to {host}. A custom host header lets you run "
            f"several sites on one server because each request is routed by the "
            f"host name instead of only the IP address. Keep the binding on "
            f"port 80 for now and finish the wizard.",
            visual_type="form_scene",
            visual_content={"title": "Add Website", "fields": [
                ["Site name", site_name],
                ["Physical path", site_path],
                ["Binding type", "http"], ["Host name", host],
                ["Port", "80"], ["IP address", "*"],
            ]},
            actions=[{"action": "enter", "target": "host header"},
                     {"action": "click", "target": "OK"}],
            bullets=[f"Site: {site_name}", f"Host header: {host}",
                     "Binding: http :80", "Routed by host name"],
        ))
    if role.startswith("Web Server (IIS)") and https:
        steps.append(_mk(
            "step", "Enforce HTTPS with a self-signed certificate",
            "With a self-signed certificate the site can serve HTTPS before "
            "you buy a public certificate. Run the PowerShell commands to "
            "create the certificate and then switch the site binding from http "
            "to https. Clients will see a warning about the self-signed "
            "certificate, which is expected until you replace it with a "
            "commercial one.",
            visual_type="terminal_scene",
            visual_content={"title": "Enable HTTPS", "lines": [
                {"prompt": "PS>",
                 "cmd": ("New-SelfSignedCertificate -DnsName " + host +
                         " -CertStoreLocation Cert:\\LocalMachine\\My"),
                 "output": "Issued. Thumbprint 1A2B...9F"},
                {"prompt": "PS>",
                 "cmd": ("Set-WebBinding -Name " + site_name +
                         " -BindingInformation '*:80:" + host + "' "
                         "-PropertyName protocol -Value https"),
                 "output": "OK"}]},
            commands=["New-SelfSignedCertificate -DnsName " + host + " -CertStoreLocation Cert:\\LocalMachine\\My",
                      "Set-WebBinding -Name " + site_name + " -BindingInformation '*:80:" + host + "' -PropertyName protocol -Value https"],
            expected_output="Site binding now https",
            bullets=["Create a self-signed certificate",
                     f"Bind https://{host} to the site",
                     "Replace with a commercial cert later"],
        ))

    # Verify via command.
    steps.append(_mk(
        "step", "Verify installation",
        "Open PowerShell or Command Prompt and run the verification command. "
        "A clean result with no errors means the role is healthy. Verifying "
        "now catches silent problems before you rely on the server.",
        visual_type="terminal_scene",
        visual_content={"title": "Verification", "lines": [
            {"prompt": "PS>", "cmd": verify, "output": "Running verification... Passed"}]},
        commands=[verify], command=verify,
        expected_output="Verification passed with no errors",
        bullets=[f"Run: {verify}", "Expect: passed, no errors"],
    ))

    # Common error.
    steps.append(_mk(
        "error", "Common error: prerequisite check fails",
        "During installation you may see 'prerequisite check failed'. This "
        "usually means the server is missing something the role needs, "
        "such as the static IP we configured, or the server name is not set. "
        "Read the message, fix the listed item, and click Next to re-run the "
        "checks.",
        visual_type="error_scene",
        error_example="Prerequisite check failed",
        visual_content={"title": "Prerequisite check failed",
                        "detail": f"The {role} role requires a static IP and a valid hostname.",
                        "cause": "No static IP or hostname not set",
                        "fix": "Assign the static IP and rename the server, then retry"},
        actions=[{"action": "click", "target": "Next"}],
        bullets=["Cause: configuration missing", "Fix: set static IP + hostname", "Retry the checks"],
    ))

    # Success scene.
    steps.append(_mk(
        "step", "Final check",
        f"{subj} is now configured. Re-run the verification command one more "
        f"time and confirm the service is running and reachable from a client. "
        f"When the status says it is finished, the setup is complete.",
        visual_type="success_scene",
        visual_content={"title": "Setup complete", "detail": "Service running and reachable"},
        expected_output="Status: running",
        bullets=["Status: running", "Reachable from clients"],
    ))
    steps.append(_summary_step(ctx))
    return steps


def _section_cls(s: str) -> str:  # noqa helper kept tiny
    return s


def _build_linux(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    low = subj.lower()
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "linux"))

    if "user" in low or "account" in low:
        uname = _words(subj, 1) if _words(subj, 1) not in ("user", "linux") else "devuser"
        actions = [{"action": "type", "target": f"sudo useradd -m -s /bin/bash {uname}"}]
        steps.append(_mk(
            "step", "Create the user account",
            f"Run `sudo useradd -m -s /bin/bash {uname}`. The -m flag creates a "
            f"home directory and the -s flag sets the default shell, which "
            f"makes the account usable right away instead of being a bare "
            f"system account.",
            visual_type="terminal_scene",
            visual_content={"title": "Terminal", "lines": [
                {"prompt": "$", "cmd": f"sudo useradd -m -s /bin/bash {uname}",
                 "output": "(no output - command succeeded)"}]},
            actions=actions, commands=["sudo useradd -m -s /bin/bash " + uname],
            command="sudo useradd -m -s /bin/bash " + uname,
            bullets=["-m creates the home directory", "-s sets the default shell"],
        ))
        steps.append(_mk(
            "step", "Set the password",
            f"Run `sudo passwd {uname}` and enter a strong password twice when "
            f"prompted. The password is what lets the new user log in, so pick "
            f"something unique and store it somewhere safe because you cannot "
            f"recover it from the system.",
            visual_type="terminal_scene",
            visual_content={"title": "Terminal", "lines": [
                {"prompt": "$", "cmd": f"sudo passwd {uname}",
                 "output": "New password: ********\nRetype new password: ********\npasswd: password updated successfully"}]},
            actions=[{"action": "type", "target": "sudo passwd " + uname}],
            commands=["sudo passwd " + uname], command="sudo passwd " + uname,
            expected_output="passwd: password updated successfully",
        ))
        steps.append(_mk(
            "example", "Optional: add to a group",
            f"If the user needs admin rights or a shared group, run "
            f"`sudo usermod -aG sudo {uname}`. The -aG flags append the group "
            f"without removing the user from existing groups, which avoids "
            f"locking the account out of anything it already has access to.",
            visual_type="terminal_scene",
            visual_content={"title": "Terminal", "lines": [
                {"prompt": "$", "cmd": f"sudo usermod -aG sudo {uname}", "output": ""}]},
            actions=[{"action": "type", "target": "sudo usermod -aG sudo " + uname}],
            commands=["sudo usermod -aG sudo " + uname],
        ))
    else:
        # Generic service/package/terminal flow using the subject.
        pkg = _words(subj, 1) or "package"
        steps.append(_mk(
            "step", f"Install or prepare {subj}",
            f"Open a terminal and install or prepare {subj} with your package "
            f"manager, for example `sudo apt install -y {pkg}`. Using -y "
            f"confirms automatically so the install runs without stopping for "
            f"input, which is convenient in a script or classroom demo.",
            visual_type="terminal_scene",
            visual_content={"title": "Terminal", "lines": [
                {"prompt": "$", "cmd": f"sudo apt install -y {pkg}",
                 "output": "Setting up " + pkg + " ..."}]},
            actions=[{"action": "type", "target": f"sudo apt install -y {pkg}"}],
            commands=[f"sudo apt install -y {pkg}"], command=f"sudo apt install -y {pkg}",
        ))
        steps.append(_mk(
            "step", f"Configure {subj}",
            f"Edit the configuration for {subj} in the normal Linux way: "
            f"configuration lives in /etc, so open the relevant file with a "
            f"text editor and sudo. After changing values, restart the service "
            f"with `sudo systemctl restart {pkg}` so the new settings actually "
            f"take effect.",
            visual_type="config_scene",
            visual_content={"title": f"/etc/{pkg}/{pkg}.conf", "entries": [
                ["enabled", "true"], ["mode", "active"], ["port", "8080"]]},
            config=f"enabled = true\nmode = active\nport = 8080",
            actions=[{"action": "edit", "target": f"/etc/{pkg}/{pkg}.conf"}],
            bullets=["Configuration lives in /etc", "Restart the service after editing"],
        ))
        steps.append(_mk(
            "verification", "Verify the service",
            f"Confirm {subj} is running with `sudo systemctl status {pkg}`. You "
            f"want to see 'active (running)' and no errors in the log lines. "
            f"This tells you both the process and the new configuration are "
            f"healthy.",
            visual_type="terminal_scene",
            visual_content={"title": "Terminal", "lines": [
                {"prompt": "$", "cmd": f"sudo systemctl status {pkg}",
                 "output": "● " + pkg + ".service - Active: active (running)"}]},
            commands=[f"sudo systemctl status {pkg}"], command=f"sudo systemctl status {pkg}",
            expected_output="Active: active (running)",
        ))

    # Error step (Linux-flavored).
    err = ("useradd: user 'devuser' already exists" if "user" in low
           else f"Failed to start {_words(subj,1)}: (code=exited, status=1)")
    err_msg = (("The command said the user already exists. That simply means "
                "the account was created earlier - run `id devuser` to confirm "
                "it is usable instead of creating a duplicate.")
               if "user" in low else
               ("The service failed to start, usually because the configuration "
                "file you edited has a typo. Run `sudo systemctl status " +
                _words(subj, 1) + "` and read the last log line - it names the "
                "exact setting to fix."))
    steps.append(_mk(
        "error", "Common error and fix",
        err_msg,
        visual_type="error_scene",
        error_example=err,
        visual_content={"title": err,
                        "detail": "The last output line tells you the exact cause.",
                        "cause": "Check the terminal output and log",
                        "fix": "Fix the reported item and re-run the command"},
        bullets=["Read the last log line", "Fix the reported setting", "Re-run the command"],
    ))
    steps.append(_mk(
        "verification", "Final verification",
        f"One last check on {subj}: re-run the verification command and confirm "
        f"everything reads as expected. If it does, you have completed the task "
        f"and can move on to real use.",
        visual_type="success_scene",
        visual_content={"title": "All good", "detail": f"{_title_case(subj)} is ready"},
        expected_output="Every check returned successfully",
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_programming(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    ex = _code_example(ctx)
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "programming"))

    steps.append(_mk(
        "step", "Understand the goal",
        f"Before writing any code, decide exactly what {subj} should do and "
        f"what input it takes. A clear goal tells you what functions to write "
        f"and lets you test that the program actually behaves correctly, "
        f"instead of just producing output that looks right.",
        visual_type="list_scene",
        visual_content={"title": f"Goal: {_title_case(subj)}",
                        "subtitle": "Input -> process -> output",
                        "items": ["What is the input?", "What process runs on it?", "What output proves success?"]},
        bullets=["What is the input?", "What process runs on it?", "What output proves success?"],
    ))

    steps.append(_mk(
        "example", f"Write the program",
        f"Here is a small working example for {subj}. Study the main part "
        f"first: it calls one cleanup function and prints a result. The rest "
        f"of the code only exists to make that function runnable, which keeps "
        f"the example focused on exactly one idea.",
        visual_type="code_scene",
        visual_content=ex,
        code=ex["code"],
        actions=[{"action": "type", "target": "write the function"}],
        bullets=["One idea per program", "Print a result to verify"],
    ))

    steps.append(_mk(
        "step", "Run the program",
        f"Save the file and run it with `{ctx.language} {ex['filename']}`. "
        f"Running the program executes every line top to bottom, and whatever "
        f"it prints is your proof that the logic works. If nothing prints, the "
        f"program finished but produced no output.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "$", "cmd": f"{ctx.language} {ex['filename']}", "output": ex["output"]}]},
        actions=[{"action": "type", "target": f"{ctx.language} {ex['filename']}"}],
        commands=[f"{ctx.language} {ex['filename']}"], command=f"{ctx.language} {ex['filename']}",
        expected_output=ex["output"],
    ))

    steps.append(_mk(
        "error", "Common error and fix",
        "If you see a NameError or undefined-identifier message, it means a "
        "name was used before it was defined - often a simple typo. Read the "
        "error line, which tells you the file and line number, fix the name, "
        "and run the program again. Every error message points at the line "
        "that needs your attention.",
        visual_type="error_scene",
        error_example="NameError: name 'result' is not defined",
        visual_content={"title": "NameError", "detail": "The name was used before it was defined",
                        "cause": "Typo or missing definition", "fix": "Fix the name on the reported line and re-run"},
        actions=[{"action": "click", "target": "re-run"}],
        bullets=["Read the line number in the error", "Fix the identifier", "Re-run the program"],
    ))

    steps.append(_mk(
        "verification", "Verify the output",
        f"The final printed output should match the expected result for "
        f"{subj}. Compare it with the expected value - if it matches in every "
        f"detail, your implementation is correct and ready to use.",
        visual_type="success_scene",
        visual_content={"title": "Output correct", "detail": ex["output"]},
        expected_output=ex["output"],
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_networking(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    vals = _networking_values(subj)
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "networking"))

    steps.append(_mk(
        "step", "Map the current layout",
        f"Draw the network for {subj}: which device holds the configuration, "
        f"which IPs are in use, and where the gateway sits. Knowing the layout "
        f"prevents IP conflicts and tells you exactly which device to touch. "
        f"Use `ipconfig` on Windows or `ip addr` on Linux to list the current "
        f"addresses.",
        visual_type="diagram_scene",
        visual_content={"kind": "network", "title": "Network layout",
                        "nodes": ["Internet", "Gateway 192.168.1.1", "Switch", "Device 192.168.1.10"]},
        actions=[{"action": "type", "target": "ipconfig"}],
        commands=["ipconfig", "ip addr"], command="ipconfig",
        bullets=["List current addresses", "Identify gateway and DNS"],
    ))

    steps.append(_mk(
        "step", "Apply the configuration",
        "Enter these values on the target interface. Each field has a job: the "
        "IP identifies the device on the network, the subnet mask defines the "
        "local network size, the gateway routes traffic outside it, and DNS "
        "resolves names to addresses. Missing one field makes one of those "
        "functions fail.",
        visual_type="form_scene",
        visual_content={"title": f"Configure {_title_case(subj)}", "fields": vals["fields"]},
        actions=[{"action": "enter", "target": "configuration fields"}],
        config="\n".join(f"{k} = {v}" for k, v in vals["fields"]),
        bullets=[f"{k} = {v}" for k, v in vals["fields"]],
    ))

    steps.append(_mk(
        "example", "Test the connection",
        f"Validate the configuration with `{vals['verify']}`. A successful "
        f"reply means the device is reachable and correctly routed. Testing "
        f"this step is what turns 'I typed some settings' into a proven "
        f"working configuration.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "$", "cmd": vals["verify"], "output": "Reply from 192.168.1.10: bytes=64 time=1ms"}]},
        actions=[{"action": "type", "target": vals["verify"]}],
        commands=[vals["verify"]], command=vals["verify"],
        expected_output="Reply received",
    ))

    steps.append(_mk(
        "error", "Common error: no response",
        "If the test times out or says 'Request timed out', the device cannot "
        "be reached. The usual causes are a wrong IP, a missing route back to "
        "your machine, or a firewall drop. Check the gateway first, then "
        "verify the address, then test a device on the same subnet.",
        visual_type="error_scene",
        error_example=vals["err"],
        visual_content={"title": vals["err"],
                        "detail": "No reply from the configured address",
                        "cause": "Wrong IP, no route, or firewall",
                        "fix": "Check gateway -> address -> firewall, then retest"},
        actions=[{"action": "type", "target": "ping gateway"}],
        bullets=["Test the gateway first", "Verify the IP is correct", "Check firewall rules"],
    ))

    steps.append(_mk(
        "verification", "Confirm the fix",
        "Once the error is gone, run the verification command again and expect "
        "a clean reply. A passed test now means the whole path - device, "
        "switch, gateway and firewall - is working together.",
        visual_type="success_scene",
        visual_content={"title": "Connected", "detail": "Reply received from the configured device"},
        expected_output="Reply received, configuration verified",
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_devops(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    v = _devops_values(subj)
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "devops"))

    steps.append(_mk(
        "step", f"Prepare {subj}",
        f"Make sure the tooling for {subj} is installed and the CLI can reach "
        f"its daemon or cluster. A quick health check now catches environment "
        f"problems before they look like code problems. For example, "
        f"`{v['run'].split()[0]} info` or `kubectl cluster-info` returns the "
        f"current environment details.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "$", "cmd": v["run"].split()[0] + " info", "output": "Server Version: ready"}]},
        actions=[{"action": "type", "target": "health check"}],
        commands=[v["run"].split()[0] + " info"],
    ))

    steps.append(_mk(
        "step", "Define the workload",
        f"Describe what you want to run. In a file-based workflow like "
        f"{subj}, the configuration IS the deployment: this file says which "
        f"image or resource to use and on which port. Keeping it in a file "
        f"means the same definition can be reused and reviewed.",
        visual_type="config_scene",
        visual_content={"title": "workload definition", "entries": [["image", "nginx:latest"], ["port", "8080"]]},
        config=v["yaml"], actions=[{"action": "edit", "target": "definition file"}],
        code=v["yaml"], bullets=["Configuration as a file", "Review image and ports"],
    ))

    steps.append(_mk(
        "step", "Apply and deploy",
        f"Apply the definition with the main command. Watch for a successful "
        f"status - that is your proof the workload was accepted. The command "
        f"{v['run']} is the one that turns the definition into a running "
        f"process.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "$", "cmd": v["run"], "output": "applied successfully"}]},
        actions=[{"action": "type", "target": v["run"]}],
        commands=[v["run"]], command=v["run"],
    ))

    steps.append(_mk(
        "error", "Common deployment error",
        v["err"],
        visual_type="error_scene",
        error_example=v["err"],
        visual_content={"title": "Deployment error",
                        "detail": v["err"],
                        "cause": "Environment not reachable or definition invalid",
                        "fix": "Check the environment, then the definition, then re-apply"},
        actions=[{"action": "type", "target": f"{v['run'].split()[0]} logs"}],
        bullets=["Diagnose with logs", "Fix the cause", "Re-apply the definition"],
    ))

    steps.append(_mk(
        "verification", "Verify the deployment",
        f"Confirm the workload is healthy with `{v['verify']}` or `{v['ps']}`. "
        f"The status should show it running with no errors. This is the "
        f"moment the deployment is officially done.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "$", "cmd": v["verify"], "output": "HTTP/1.1 200 OK"}]},
        actions=[{"action": "type", "target": v["verify"]}],
        commands=[v["verify"], v["ps"]], command=v["verify"],
        expected_output="Healthy and running",
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_database(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    v = _db_values(subj)
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "database"))

    steps.append(_mk(
        "step", f"Start {v['db']}",
        f"Start the database service. On Linux that is `{v['start']}`; on "
        f"other systems use the service manager you installed it with. The "
        f"service must be running before any client can connect, so verify it "
        f"listens on its default port ({v['port']}) before continuing.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "$", "cmd": v["start"], "output": "ok"}]},
        actions=[{"action": "type", "target": v["start"]}],
        commands=[v["start"]], command=v["start"],
        bullets=[f"Default port: {v['port']}", "Service must be running first"],
    ))

    steps.append(_mk(
        "step", "Create the database",
        f"Connect with the client `{v['client']}` and create a database named "
        f"`{v['name']}`. A database is an isolated container for your tables; "
        f"creating a dedicated one for {subj} keeps its data separate from "
        f"everything else on the server.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "#", "cmd": v["create"], "output": "CREATE DATABASE"}]},
        actions=[{"action": "type", "target": v["create"]}],
        commands=[v["create"]], command=v["create"],
        expected_output="CREATE DATABASE",
    ))

    steps.append(_mk(
        "example", "Verify with a query",
        f"Run a simple query that confirms you can talk to the database. "
        f"`{v['select']}` should return a row showing the connection. A "
        f"successful query proves the client, network, and server are all "
        f"working together.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "#", "cmd": v["select"], "output": "status\n--\nconnected to " + v["name"]}]},
        actions=[{"action": "type", "target": v["select"]}],
        commands=[v["select"]], command=v["select"],
        expected_output="connected to " + v["name"],
    ))

    steps.append(_mk(
        "error", "Common error: connection refused",
        v["err"].format(**{"db": v["db"]}),
        visual_type="error_scene",
        error_example=f"connection refused on {v['port']}",
        visual_content={"title": "Connection refused",
                        "detail": f"Could not connect to {v['db']} on port {v['port']}",
                        "cause": "Service not running or wrong port/firewall",
                        "fix": "Start the service and confirm the port is open"},
        actions=[{"action": "type", "target": f"sudo systemctl status {v['db']}"}],
        bullets=["Check the service is running", "Confirm the port", "Allow it through the firewall"],
    ))

    steps.append(_mk(
        "verification", "Confirm it works",
        f"Run the verification query one more time and confirm {subj} is "
        f"connected to its database. A clean result means the setup is "
        f"complete and ready for real queries.",
        visual_type="success_scene",
        visual_content={"title": "Database ready", "detail": v["name"] + " connected"},
        expected_output="Query returned successfully",
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_cloud(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    low = subj.lower()
    svc = "S3 bucket" if "s3" in low or "bucket" in low else \
          ("EC2 instance" if "ec2" in low or "instance" in low else \
           "Lambda function" if "lambda" in low or "serverless" in low else "cloud resource")
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "cloud"))

    steps.append(_mk(
        "step", f"Create the {svc}",
        f"Use the console or CLI to create a {svc} for {subj}. Give it a "
        f"clear name derived from the topic and leave default settings for "
        f"now - you configure access and behavior next. Creating it first "
        f"gives you a real object to configure instead of guessing settings "
        f"up front.",
        visual_type="form_scene",
        visual_content={"title": f"Create {svc}", "fields": [["Name", _words(subj, 2)], ["Region", "us-east-1"], ["Defaults", "Keep"]]},
        actions=[{"action": "click", "target": "Create"}],
        bullets=["Name it after the topic", "Keep defaults initially"],
    ))

    steps.append(_mk(
        "step", "Configure access",
        f"Set the access rules for the {svc}. The key idea is least privilege: "
        f"the resource should be reachable by exactly what needs it and "
        f"blocked from everything else. Misconfigured access is both a "
        f"security risk and the most common cause of 'permission denied'.",
        visual_type="config_scene",
        visual_content={"title": "Access policy", "entries": [["Principal", "*"], ["Effect", "Allow"], ["Action", "s3:GetObject"]]},
        config="Effect: Allow\nPrincipal: *\nAction: s3:GetObject",
        actions=[{"action": "edit", "target": "policy"}],
        bullets=["Least privilege by default", "Explicitly allow what you need"],
    ))

    steps.append(_mk(
        "error", "Common error: permission denied",
        "If your first test returns 'permission denied' or 'AccessDenied', "
        "the access policy is too tight or the wrong credentials were used. "
        "Check the identity you ran the command with, then review the policy "
        "statement against the exact resource and action you need.",
        visual_type="error_scene",
        error_example="AccessDenied when accessing the resource",
        visual_content={"title": "AccessDenied", "detail": "Action not allowed by the current policy",
                        "cause": "Policy too tight or wrong credentials", "fix": "Update policy or use the right identity"},
        actions=[{"action": "click", "target": "review policy"}],
        bullets=["Check which identity you used", "Review the policy", "Retest after the change"],
    ))

    steps.append(_mk(
        "verification", "Verify it works",
        f"Run a live test against the {svc} you created. A successful response "
        f"confirms creation and configuration are both correct, and gives you "
        f"a final result you can show or reuse.",
        visual_type="success_scene",
        visual_content={"title": "Resource ready", "detail": _title_case(subj) + " is working"},
        expected_output="Success response from the cloud service",
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_security(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    low = subj.lower()
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "security"))

    kind = "VPN tunnel" if "vpn" in low else \
          ("TLS/SSL certificate" if any(k in low for k in ["ssl", "tls", "certificate"]) else \
           "access control policy")

    steps.append(_mk(
        "step", f"Prepare the {kind}",
        f"Before touching {subj}, record the current state and make sure you "
        f"have rollback available. Security changes affect who can get in, so "
        f"a bad change can lock out legitimate users - having the previous "
        f"configuration saved means you can always undo a mistake.",
        visual_type="list_scene",
        visual_content={"title": f"Prepare {kind}",
                        "subtitle": "Backup current state, then apply",
                        "items": ["Backup the current state", "Know the rollback path"]},
        bullets=["Backup the current state", "Know the rollback path"],
    ))
    steps.append(_mk(
        "step", f"Apply the {kind} configuration",
        f"Apply the settings for {kind} as a step-by-step change: define the "
        f"rule, save it, and enable it. Do not combine several changes at "
        f"once - if something breaks, a single change is trivial to find and "
        f"reverse.",
        visual_type="config_scene",
        visual_content={"title": kind, "entries": [["Rule name", _words(subj, 2)], ["State", "Active"], ["Logging", "On"]]},
        config="Rule: " + _words(subj, 2) + "\nState: Active\nLogging: On",
        actions=[{"action": "edit", "target": "security settings"}],
        bullets=["One change at a time", "Turn logging on to see failures"],
    ))

    steps.append(_mk(
        "error", "Common error and fix",
        f"If access suddenly stops working after applying the {kind}, the rule "
        f"is usually too strict or applies to the wrong host. Check the "
        f"latest security log entry, confirm the rule matches the traffic you "
        f"actually want, and relax or fix it, then re-test.",
        visual_type="error_scene",
        error_example=f"{kind} blocked legitimate traffic",
        visual_content={"title": "Traffic blocked", "detail": f"The {kind} rule rejected a valid request",
                        "cause": "Rule too strict or wrong target", "fix": "Adjust the rule and re-test"},
        actions=[{"action": "click", "target": "review log"}],
        bullets=["Read the security log", "Match the rule to real traffic", "Fix and re-test"],
    ))

    steps.append(_mk(
        "verification", "Confirm the protection works",
        "Run a test that proves both sides: legitimate access still works and "
        "unexpected access is denied. Passing both checks means the "
        "configuration protects without breaking anything.",
        visual_type="success_scene",
        visual_content={"title": "Protected", "detail": _title_case(subj) + " configured correctly"},
        expected_output="Allowed traffic passed, denied traffic blocked",
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_ai_ml(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "ai_ml"))
    lib = "tensorflow" if "tensorflow" in subj.lower() else \
          ("torch" if "pytorch" in subj.lower() or "torch" in subj.lower() else "transformers")

    steps.append(_mk(
        "step", "Set up the model pipeline",
        f"Load the model and our data with {lib}. The pattern is always the "
        f"same: load the model, prepare the data in the shape the model "
        f"expects, run the inference (or training), and inspect the result. "
        f"Getting the data shape right is the most common source of errors.",
        visual_type="code_scene",
        visual_content={"filename": "model_demo.py", "code":
            f"import {lib}\nmodel = {lib}.load('model')\ndata = ['sample input for {_words(subj)}']\nresult = model(data)\nprint(result)"},
        code=f"import {lib}\nmodel = {lib}.load('model')\ndata = ['sample input for {_words(subj)}']\nresult = model(data)\nprint(result)",
        actions=[{"action": "type", "target": "load the model"}],
        bullets=["Load model", "Prepare data", "Run and inspect"],
    ))

    steps.append(_mk(
        "example", "Run and inspect",
        "Execute the script and inspect the output. The printed result is the "
        "model's answer for your sample input. Understand what that answer "
        "means before tuning anything - most mistakes come from misreading "
        "what the model actually returned.",
        visual_type="terminal_scene",
        visual_content={"title": "Terminal", "lines": [
            {"prompt": "$", "cmd": "python model_demo.py", "output": "score=0.97 label=positive"}]},
        actions=[{"action": "type", "target": "python model_demo.py"}],
        commands=["python model_demo.py"], command="python model_demo.py",
        expected_output="score=0.97",
    ))

    steps.append(_mk(
        "error", "Common error and fix",
        "A shape mismatch or missing library is the usual first error. The "
        "traceback names the exact line and the expected shape. Read that "
        "line, adjust the data to match, and re-run - never change the model "
        "to make an error go away without understanding it first.",
        visual_type="error_scene",
        error_example="RuntimeError: shape mismatch",
        visual_content={"title": "Shape mismatch", "detail": "Model expected (1, 512), got (1, 128)",
                        "cause": "Data prepared in the wrong shape", "fix": "Fix the data shape on the reported line"},
        actions=[{"action": "click", "target": "fix data"}],
        bullets=["Read the traceback line", "Fix the data shape", "Re-run the script"],
    ))

    steps.append(_mk(
        "verification", "Verify the result",
        f"The pipeline for {subj} is verified when your sample produces a "
        f"stable, sensible output on repeated runs. That consistency is what "
        f"you want to ship.",
        visual_type="success_scene",
        visual_content={"title": "Pipeline works", "detail": "Stable result across runs"},
        expected_output="Consistent output on every run",
    ))
    steps.append(_summary_step(ctx))
    return steps


def _build_general(ctx: TopicContext) -> List[WorkflowStep]:
    subj = ctx.subject
    steps: List[WorkflowStep] = []
    steps.append(_intro_step(ctx))
    steps.append(_prereq_step(subj, "general"))

    steps.append(_mk(
        "step", "What it is",
        f"{_title_case(subj)} solves a specific problem: {ctx.goal}. Before "
        f"doing anything technical, name the problem and the expected outcome. "
        f"This definition is what lets you test whether your work succeeded.",
        visual_type="list_scene",
        visual_content={"title": _title_case(subj),
                        "subtitle": "Problem -> approach -> verified result",
                        "items": ["The problem it solves", "The expected outcome"]},
        bullets=["The problem it solves", "The expected outcome"],
    ))

    steps.append(_mk(
        "step", "How it works",
        "Most tools follow the same shape: configuration goes in, a process "
        "runs, and a result comes out. Identify those three parts for what "
        "you are working with. When a later step fails, you will know which "
        "of the three to inspect.",
        visual_type="workflow_scene",
        visual_content={"title": "How it fits together", "nodes": ["Input", "Process", "Result"]},
        bullets=["Input", "Process", "Result"],
    ))

    steps.append(_mk(
        "example", "A realistic example",
        f"Take {subj} and apply the smallest realistic example you can: one "
        f"small input, one run, one result that matches what you expect. A "
        f"minimal example proves the mechanism works before you add "
        f"complexity, and mistakes are easier to spot in a tiny example.",
        visual_type="diagram_scene",
        visual_content={"kind": "flow", "title": "Minimal example", "nodes": ["Small input", "Run", "Match result"]},
        bullets=["Smallest possible example", "Compare result to expectation"],
    ))

    steps.append(_mk(
        "error", "Common error and fix",
        "When an operation fails, change nothing at random. Read the error "
        "message and find which of the three stages reported it, fix exactly "
        "that part, then re-run. Systematic diagnosis beats trial and error "
        "and fixes the real cause.",
        visual_type="error_scene",
        error_example="The operation failed at the process stage",
        visual_content={"title": "Operation failed", "detail": "Identify the failing stage",
                        "cause": "Input, process, or result checks", "fix": "Fix that stage and re-run"},
        bullets=["Read the message", "Find the failing stage", "Fix and re-run"],
    ))

    steps.append(_mk(
        "verification", "Verify the result",
        f"The task for {subj} is complete when your expected outcome matches "
        f"the actual result. Write down the expected outcome now and compare "
        f"it directly when you finish.",
        visual_type="success_scene",
        visual_content={"title": "Complete", "detail": _title_case(subj) + " verified"},
        expected_output="Result matches the expected outcome",
    ))
    steps.append(_summary_step(ctx))
    return steps


_BULLDERS = {
    "windows_admin": _build_windows_admin,
    "linux": _build_linux,
    "programming": _build_programming,
    "networking": _build_networking,
    "devops": _build_devops,
    "database": _build_database,
    "cloud": _build_cloud,
    "security": _build_security,
    "ai_ml": _build_ai_ml,
    "general": _build_general,
}


# ---------------------------------------------------------------------------
# Content → visual mapping (choose by meaning, never by step number)
# ---------------------------------------------------------------------------

def choose_visual_type(step: WorkflowStep) -> str:
    """Decide the visual for a step from its content. The renderer uses this
    to pick layout + animation, so the picture always matches the meaning."""
    sec = step.section
    if step.error_example or sec == "error":
        return "error_scene"
    if step.code and not step.command:
        return "code_scene"
    if step.config:
        return "config_scene"
    if step.commands or step.command:
        return "terminal_scene"
    if step.expected_output:
        return "success_scene"
    if step.actions and any(a.get("action") in ("click", "navigate") for a in step.actions):
        return "gui_scene"
    if any(a.get("action") in ("type", "enter") for a in step.actions):
        return "form_scene"
    if sec == "summary":
        return "workflow_scene"
    vc = step.visual_content or {}
    if vc.get("kind") in ("network", "flow", "diagram"):
        return "diagram_scene"
    if vc.get("fields"):
        return "form_scene"
    if sec in ("introduction", "step") and vc.get("nodes"):
        return "workflow_scene"
    if len(step.bullets) >= 3 and sec in ("example", "verification"):
        return "list_scene"
    return "title_scene"


# ---------------------------------------------------------------------------
# Duplicate detection / removal
# ---------------------------------------------------------------------------

_STOP_SIM = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for",
             "with", "at", "this", "that", "it", "is", "are", "your", "we"}


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1 and w not in _STOP_SIM}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    if len(ta) < 3 or len(tb) < 3:
        return 0.0
    inter = len(ta & tb)
    jacc = inter / len(ta | tb)
    # length agreement matters: "Click Server Manager" vs the longer rerun
    len_ratio = min(len(ta), len(tb)) / max(len(ta), len(tb))
    return jacc * 0.7 + len_ratio * 0.3


def dedup_sentences(lines: List[str], threshold: float = 0.62) -> List[str]:
    """Drop exact / near-duplicate sentences, keeping the earliest narrator."""
    out: List[str] = []
    for ln in lines:
        s = re.sub(r"\s+", " ", (ln or "").strip())
        if not s:
            continue
        if any(_similarity(s, prev) >= threshold for prev in out):
            continue
        out.append(s)
    return out


def _narr_has_step(step: WorkflowStep, phrase: str) -> bool:
    return _similarity(step.narration, phrase) > 0.55 or phrase.lower() in step.narration.lower()


def dedup_workflow(steps: List[WorkflowStep]) -> List[WorkflowStep]:
    out: List[WorkflowStep] = []
    seen_titles: List[str] = []
    for st in steps:
        if any(_similarity(st.title, t) >= 0.85 for t in seen_titles):
            continue
        seen_titles.append(st.title)
        out.append(st)
    return out


def _strip_labels(d: dict) -> dict:
    if "section" in d:
        d.pop("section")
    if "_section_cls" in d:
        d.pop("_section_cls")
    if "section_load" in d:
        d.pop("section_load")
    return d


# ---------------------------------------------------------------------------
# Workflow generation
# ---------------------------------------------------------------------------

def _word_budget(duration: float) -> int:
    if duration >= 5:
        return 1080
    if duration >= 4:
        return 950
    if duration >= 3:
        return 850
    return 640


def generate_workflow(prompt: str, duration: float = 3.0,
                      difficulty: str = "") -> WorkflowScript:
    """The single entry point: prompt -> structured learning workflow."""
    ctx = analyze_prompt(prompt)
    if difficulty:
        ctx.difficulty = difficulty
    builder = _BULLDERS.get(ctx.domain, _build_general)
    raw = builder(ctx)

    raw = dedup_workflow(raw)

    # Global narration de-duplication across all steps.
    narr_texts: List[str] = []
    for st in raw:
        sentences = re.split(r"(?<=[.!?])\s+", st.narration)
        kept = dedup_sentences([s for s in sentences if s.strip()])
        for existing in narr_texts:
            k = [s for s in kept if _similarity(s, existing) < 0.62]
            kept = k
        narr_texts.extend(kept)
        st.narration = " ".join(kept) if kept else st.narration

    # Assign visuals from content.
    for st in raw:
        if not st.visual_type or st.visual_type == "title_scene":
            st.visual_type = choose_visual_type(st)

    # Re-balance section orderings from the plan so "error" precedes verify.
    total_words = sum(len(st.narration.split()) for st in raw)
    target = _word_budget(duration)
    if total_words < target * 0.6:
        _expand_narrations(raw, max(1, int(target // max(len(raw), 1))))

    title = _prompt_title(ctx)
    desc = _build_description(ctx, raw)
    tags = _build_tags(ctx)
    return WorkflowScript(title=title, description=desc, tags=tags,
                          steps=raw, domain=ctx.domain, prompt=prompt,
                          estimated_duration=sum(s.duration for s in raw))


def _expand_narrations(steps: List[WorkflowStep], budget: int) -> None:
    """Pad short steps with topic-aware, deduped elaboration sentences."""
    for st in steps:
        words = len(st.narration.split())
        if words >= budget:
            continue
        extra = [
            f"Keep this step focused on {st.title.lower()} before moving on.",
            f"You can repeat this check a second time to confirm nothing changed.",
            f"Whenever you are unsure, re-read the last output line and compare it to what we expected.",
        ]
        got = words
        for s in extra:
            if got >= budget:
                break
            if any(_similarity(s, o) >= 0.62 for o in st.narration.split(". ")):
                continue
            st.narration = st.narration.rstrip() + " " + s
            got += len(s.split())


def _prompt_title(ctx: TopicContext) -> str:
    return f"{ctx.title}: Step-by-step guide"


def _build_description(ctx: TopicContext, steps: List[WorkflowStep]) -> str:
    lines = [f"{_prompt_title(ctx)} - a practical, step-by-step walkthrough.",
             "",
             "In this video:",
             *[f"  - {s.title}" for s in steps[:10]],
             "",
             "Every part is built from your request and verified end to end."]
    return "\n".join(lines)


def _build_tags(ctx: TopicContext) -> List[str]:
    tags = [ctx.subject, ctx.domain.replace("_", " "), "tutorial",
            "step by step", "tech guide", "core infotech"]
    for k in ctx.keywords[:4]:
        if len(tags) < 10 and k not in tags:
            tags.append(k)
    return tags[:10]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_workflow(steps: List[WorkflowStep], prompt: str,
                      min_words: int = 180) -> Tuple[bool, List[str], List[WorkflowStep]]:
    """Check the workflow answers the prompt; fix what is fixable."""
    issues: List[str] = []
    ctx = analyze_prompt(prompt)
    subj = ctx.subject.lower()

    if len(steps) < 5:
        issues.append("Too few steps")
    total = sum(len(s.narration.split()) for s in steps)
    if total < min_words:
        issues.append(f"Narration too short ({total} words)")

    titles = [s.title for s in steps]
    for i in range(1, len(titles)):
        if _similarity(titles[i - 1], titles[i]) >= 0.9:
            issues.append(f"Nearly duplicate steps: {titles[i-1]} / {titles[i]}")

    if subj and not any(subj in (s.title + " " + s.narration).lower() for s in steps):
        issues.append("Subject missing from content")

    repaired = list(steps)
    _fix_visuals(repaired)
    return (not issues, issues, repaired)


def _fix_visuals(steps: List[WorkflowStep]) -> None:
    """Auto-correct visuals so each tells the truth about its content.

    Only placeholder visuals (empty / title_scene) are re-derived from content;
    a visual the builder chose explicitly is preserved.
    """
    for st in steps:
        current = st.visual_type or ""
        if current == "error_scene":
            if not st.error_example:
                st.error_example = "Something went wrong - see the explanation"
        elif current in ("", "title_scene"):
            st.visual_type = choose_visual_type(st)


# ---------------------------------------------------------------------------
# Scene conversion for the renderer
# ---------------------------------------------------------------------------

_STYLE_BY_DOMAIN = {
    "windows_admin": "cinematic_realistic",
    "linux": "cinematic_tech",
    "programming": "cinematic_tech",
    "networking": "cinematic_realistic",
    "devops": "cinematic_tech",
    "database": "cinematic_tech",
    "cloud": "cinematic_realistic",
    "security": "cinematic_realistic",
    "ai_ml": "cinematic_tech",
    "general": "educational_infographic",
}

_ANIM_BY_VISUAL = {
    "terminal_scene": "typewriter",
    "code_scene": "typewriter",
    "config_scene": "fade",
    "form_scene": "fade",
    "dialog_scene": "fade",
    "error_scene": "fade",
    "success_scene": "slide_up",
    "workflow_scene": "fade",
    "diagram_scene": "fade",
    "comparison_scene": "fade",
    "gui_scene": "fade",
    "list_scene": "fade",
    "title_scene": "fade",
}


def workflow_to_scenes(script: WorkflowScript) -> List[Scene]:
    """Convert workflow steps into renderer Scenes carrying visual metadata."""
    scenes: List[Scene] = []
    domain = script.domain
    for st in script.steps:
        vtype = st.visual_type or choose_visual_type(st)
        scene = Scene(
            text=st.title,
            title_text=st.title,
            voiceover=st.narration,
            visual_style=_STYLE_BY_DOMAIN.get(domain, "educational_infographic"),
            animation=_ANIM_BY_VISUAL.get(vtype, "fade"),
            font_size=52 if vtype not in ("title_scene", "list_scene") else 62,
            text_color="#FFFFFF",
            show_character=False,
            bullet_points=st.bullets or None,
            background_prompt=f"{domain} {st.title}",
            visual_type=vtype,
            visual_content=st.visual_content or {},
        )
        scenes.append(scene)
    return scenes