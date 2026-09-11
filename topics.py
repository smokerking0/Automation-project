""" 
Tech & AI Topic Generator
Generates educational scripts for AI, GPU, Data Science, ML, and Programming topics.
Creates 1-5 minute informative videos.
"""

import json
import os
import re
import random
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path

from video import Scene

# Optional Ollama integration
try:
    from ollama_gen import OllamaClient
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

# Optional research integration (used for trending topic search)
try:
    from research import TopicResearch
    HAS_RESEARCH = True
except ImportError:
    HAS_RESEARCH = False

try:
    from youtube_trending import YouTubeTrending
    HAS_YOUTUBE_TRENDING = True
except ImportError:
    HAS_YOUTUBE_TRENDING = False

# ---------------------------------------------------------------------------
# CONTENT_LIBRARY — topic-specific detailed expansions for every point.
# Loaded from content_library.py in the project directory.
# ---------------------------------------------------------------------------
try:
    from content_library import CONTENT_LIBRARY
    assert isinstance(CONTENT_LIBRARY, dict) and len(CONTENT_LIBRARY) > 0
except (ImportError, AssertionError):
    CONTENT_LIBRARY = {}

@dataclass
class VideoScript:
    """Complete video script with metadata."""
    title: str
    description: str
    tags: List[str]
    scenes: List[Scene]
    thumbnail_text: str = ""
    category: str = "Science & Technology"
    estimated_duration: float = 0.0


def extract_topic_info(
    snippet: str,
    queries: Optional[List[str]] = None,
) -> dict:
    """Extract structured topic info from a search snippet.
    
    Works offline from the snippet text alone — no external API calls.
    Returns a dict with title, intro, points, and outro that is compatible
    with the ALL_TOPICS entry shape so generate_script() can consume it.
    
    Difficulty tier is always "intermediate" for extracted topics;
    the caller (generate_script) adjusts depth at scene-construction time.
    """
    # Clean the snippet
    text = snippet.strip()
    text = re.sub(r'\s+', ' ', text)
    
    # Title: first sentence or first ~8 words
    sentences = re.split(r'(?<=[.!?])\s+', text)
    title_candidate = sentences[0].strip() if sentences else text
    title_words = title_candidate.split()
    if len(title_words) > 10:
        title = ' '.join(title_words[:8]) + '...'
    elif len(title_words) < 4:
        title = text[:60].rsplit(' ', 1)[0] + '...'
    else:
        title = title_candidate
    
    # Intro: second sentence or first ~40 words of snippet
    if len(sentences) >= 2:
        intro = sentences[1].strip()
        intro = re.sub(r'\s+', ' ', intro)
        if len(intro) > 200:
            intro = intro[:197] + '...'
    else:
        intro_words = text.split()[:40]
        intro = ' '.join(intro_words)
        if len(intro) > 180:
            intro = intro[:177] + '...'
    
    # Points: derive 3-5 distinct concepts from the snippet using heuristic cues.
    points = _extract_points_from_text(text)
    
    # Outro: generic wrap-up based on topic keywords
    outro = _build_outro(text, title)
    
    return {
        "title": title,
        "intro": intro,
        "points": points,
        "outro": outro,
    }


def _extract_points_from_text(text: str) -> List[str]:
    """Heuristic point extraction from a paragraph of tech text.
    
    Tries sentence-based extraction first, then falls back to
    keyword-phrase extraction when sentences are too short/generic.
    Always returns 3-5 points.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
    
    points = []
    
    # Strategy 1: sentence-based (take middle sentences that look substantive)
    if len(sentences) >= 3:
        for s in sentences[1:-1]:  # skip first (title/intro) and last
            if len(points) >= 5:
                break
            s_clean = re.sub(r'\s+', ' ', s.strip())
            if len(s_clean) > 25 and len(s_clean) < 150:
                # Make it a noun-phrase heading
                point = _sentence_to_point(s_clean)
                if point and point not in points:
                    points.append(point)
    
    # Strategy 2: keyword-phrase extraction if we have too few points
    if len(points) < 3:
        keyword_points = _extract_keyword_points(text)
        for kp in keyword_points:
            if kp not in points:
                points.append(kp)
            if len(points) >= 5:
                break
    
    # Strategy 3: absolute fallback
    if len(points) < 3:
        points = _generic_points(text)
    
    return points[:5]


def _sentence_to_point(sentence: str) -> str:
    """Convert a full sentence into a concise bullet-point heading."""
    s = sentence.strip()
    s = re.sub(r'\s+', ' ', s)
    
    # Remove leading "The", "This", "These", etc.
    s = re.sub(r'^(The|This|These|Those|It|Its)\s+', '', s, count=1)
    
    # Cut at last comma before 80 chars
    if len(s) > 80:
        comma_cut = s.rfind(',', 0, 80)
        if comma_cut > 30:
            s = s[:comma_cut].strip()
        else:
            s = s[:77].rsplit(' ', 1)[0] + '...'
    
    # Ensure it ends with a period or is a phrase
    if not s.endswith('.') and len(s) < 100:
        s = s.rstrip(',;:') 
    
    return s.strip()


def _extract_keyword_points(text: str) -> List[str]:
    """Extract topic points based on keyword patterns in text."""
    points = []
    text_lower = text.lower()
    
    # Look for common tech topic patterns
    patterns = [
        (r'what.*is\s+(\w+)', lambda m: f"What {m.group(1).title()} Is"),
        (r'how.*works', "How It Works"),
        (r'types? of\s+(\w+)', lambda m: f"Types of {m.group(1).title()}"),
        (r'bene?fits?\s+of', "Key Benefits"),
        (r'challenges?\s+and\s+limitations?', "Challenges & Limits"),
        (r'use\s+cases?|applications?', "Real-World Applications"),
        (r'future\s+of\s+(\w+)', lambda m: f"The Future of {m.group(1).title()}"),
        (r'trends?|trending', "Current Trends"),
        (r'implementation|implementation details', "Implementation Details"),
        (r'best\s+practices', "Best Practices"),
        (r'comparison|vs\.|versus', "Comparison with Alternatives"),
        (r'getting started|tutorial', "Getting Started Guide"),
        (r'examples?|example', "Practical Examples"),
        (r'architecture|framework', "Architecture & Framework"),
        (r'performance|speed|efficiency', "Performance Considerations"),
        (r'security|privacy|ethical', "Security & Ethics"),
        (r'budget|cost|affordability', "Cost & Budget Considerations"),
        (r'deployment|production', "Deployment & Production"),
        (r'integration|api|interfaces', "Integration & APIs"),
    ]
    
    for pattern, label_fn in patterns:
        m = re.search(pattern, text_lower)
        if m:
            label = label_fn(m) if callable(label_fn) else label_fn
            if label not in points:
                points.append(label)
    
    return points


def _generic_points(text: str) -> List[str]:
    """Fallback points when extraction produces nothing useful."""
    generic = [
        "Core Concepts Explained",
        "Key Features and Capabilities",
        "How It Differs from Alternatives",
        "Practical Use Cases",
        "What You Need to Know",
    ]
    return generic


def _build_outro(text: str, title: str) -> str:
    """Build a generic but topic-aware outro statement."""
    topic_words = re.findall(r'\b[A-Z][a-z]+(?:[\s-][A-Z][a-z]+)*\b', title)
    subject = topic_words[0] if topic_words else "this technology"
    
    templates = [
        f"Now you know what {subject} is and why it matters. Keep exploring and stay ahead of the curve.",
        f"You've learned the essentials of {subject}. The next step is to try it yourself.",
        f"That's a wrap on {subject}. If you found this helpful, share it with a friend.",
        f"Now let's talk about {subject}. {subject} is a key area of modern technology, and understanding it will help you see how it all fits together.",
        f"Here's what you need to know about {subject}. In the world of tech, this concept plays a central role — let me break it down.",
        f"Now you understand {subject}. The tech landscape keeps evolving — stay curious and keep learning.",
    ]
    return random.choice(templates)


class TechTopicGenerator:
    """Generates tech/AI educational video topics and scripts."""

    # ====
    # AI TOPICS
    # ====
    AI_TOPICS = {
        "ai_basics": {
            "title": "What is Artificial Intelligence? Complete Guide",
            "intro": "Artificial Intelligence is transforming every industry. In this video, we'll break down what AI really means, how it works, and why it matters for your future.",
            "points": [
                "Definition of Artificial Intelligence",
                "Types of AI: Narrow vs General vs Super",
                "How AI Learns from Data",
                "Real World AI Applications Today",
                "AI vs Machine Learning vs Deep Learning",
                "The Future of AI in 2026 and Beyond"
            ],
            "outro": "Now you understand the fundamentals of AI. Start learning today and stay ahead."
        },
        "chatgpt_explained": {
            "title": "How ChatGPT Actually Works - Inside the AI",
            "intro": "ChatGPT has changed the world. But how does it actually work? Let's explore the technology behind ChatGPT step by step.",
            "points": [
                "What is a Large Language Model?",
                "Transformer Architecture Explained",
                "Training Process: Billions of Parameters",
                "How GPT Generates Text Token by Token",
                "Limitations and Hallucinations",
                "How to Use ChatGPT Effectively"
            ],
            "outro": "You now understand how ChatGPT works under the hood."
        },
        "ai_agents": {
            "title": "AI Agents Explained - The Next Revolution",
            "intro": "AI Agents are the next big thing in artificial intelligence. They can browse the web, write code, and complete complex tasks autonomously.",
            "points": [
                "What are AI Agents?",
                "How Agents Use Tools and APIs",
                "ReAct Framework: Reasoning + Acting",
                "Multi-Agent Systems",
                "Real World Agent Applications",
                "Building Your First AI Agent"
            ],
            "outro": "AI agents are the future. Start building yours today."
        },
        "prompt_engineering": {
            "title": "Prompt Engineering Masterclass - 10x Your AI Results",
            "intro": "The way you write prompts determines the quality of AI output. Learn the techniques that separate beginners from experts.",
            "points": [
                "What Makes a Great Prompt?",
                "The Art of Being Specific and Clear",
                "Chain of Thought Prompting Technique",
                "Few-Shot vs Zero-Shot Prompting",
                "Role Playing with AI Assistants",
                "Advanced Prompt Patterns That Work"
            ],
            "outro": "Great prompts lead to great results. Start experimenting today."
        },
        "rag_explained": {
            "title": "Retrieval Augmented Generation - AI That Knows More",
            "intro": "RAG combines the power of search with generative AI. Learn how machines can now access external knowledge to give better answers.",
            "points": [
                "What is Retrieval Augmented Generation?",
                "How RAG Combines Search with Generation",
                "Vector Databases and Embeddings",
                "Building a RAG Pipeline Step by Step",
                "RAG vs Fine-tuning: When to Use Each",
                "Real World RAG Applications"
            ],
            "outro": "RAG is changing how we build AI applications. The future is knowledge-aware."
        },
        "computer_vision": {
            "title": "Computer Vision - How Machines See the World",
            "intro": "Computer vision lets machines understand images and video. Discover how AI sees the world through cameras and algorithms.",
            "points": [
                "What is Computer Vision?",
                "Image Recognition and Classification",
                "Convolutional Neural Networks Explained",
                "Object Detection in Real Time",
                "Face Recognition Technology",
                "Computer Vision in Healthcare and 자율주행"
            ],
            "outro": "Machines can now see almost as well as humans. The future of vision AI is bright."
        },
        "llm_hallucinations": {
            "title": "Why AI Hallucinates - And How to Fix It",
            "intro": "AI sometimes makes things up. Let's explore why large language models hallucinate, when it happens, and what we can do about it.",
            "points": [
                "What are AI Hallucinations?",
                "Why LLMs Generate False Information",
                "Common Types of AI Hallucinations",
                "How to Detect AI Hallucinations",
                "Techniques to Reduce Hallucination",
                "Building Trustworthy AI Systems"
            ],
            "outro": "Understanding hallucinations helps us use AI more responsibly. Stay critical and verify."
        },
        "reinforcement_learning": {
            "title": "Reinforcement Learning - AI That Learns by Doing",
            "intro": "Reinforcement learning lets AI learn through trial and error, just like humans do. From games to robotics, this approach is changing everything.",
            "points": [
                "What is Reinforcement Learning?",
                "Agents, Environments, and Rewards",
                "Exploration vs Exploitation Trade-off",
                "Q-Learning and Deep Q-Networks",
                "Policy Gradient Methods",
                "Real World RL Applications"
            ],
            "outro": "Reinforcement learning opens new frontiers. The agents of tomorrow learn by doing."
        },
        "nlp_explained": {
            "title": "Natural Language Processing - Teaching Machines to Understand Text",
            "intro": "NLP is the branch of AI that helps computers understand, interpret, and generate human language. Let's explore how it works.",
            "points": [
                "What is Natural Language Processing?",
                "Text Preprocessing and Tokenization",
                "Sentiment Analysis Explained",
                "Named Entity Recognition",
                "Machine Translation and Summarization",
                "The Evolution from Rules to Transformers"
            ],
            "outro": "NLP is making human-computer communication natural. What will you build with it?"
        }
    }

    # ====
    # GPU / HARDWARE TOPICS
    # ====
    GPU_TOPICS = {
        "nvidia_blackwell": {
            "title": "NVIDIA Blackwell - The Next Generation GPU",
            "intro": "NVIDIA's Blackwell architecture is here. Let's explore what makes this chip a game-changer for AI, gaming, and high-performance computing.",
            "points": [
                "Blackwell Architecture Overview",
                "GB200 Grace-Blackwell Superchip",
                "Transformer Engine Improvements",
                "Memory Bandwidth and HBM3e",
                "NVLink and Multi-GPU Scaling",
                "Performance vs Hopper Comparison"
            ],
            "outro": "Blackwell is pushing the boundaries of what GPUs can do. The AI revolution needs hardware this powerful."
        },
        "amd_migraph_x64": {
            "title": "AMD MiGoRAG X64 - AMD's Answer to AI Workloads",
            "intro": "AMD is making big moves in AI hardware. Let's look at how MiGoRAG X64 competes in the AI accelerator market.",
            "points": [
                "MiGoRAG X64 Architecture",
                "CDNA 3 Compute Architecture",
                "HBM2e Memory Configuration",
                "FP8 and FP16 AI Performance",
                "Comparison with NVIDIA and Intel",
                "Software Ecosystem: ROCm 6.0"
            ],
            "outro": "AMD is serious about AI. The competition is driving innovation forward."
        },
        "apple_m4_chip": {
            "title": "Apple M4 Chip - AI on the Desktop",
            "intro": "Apple's M4 chip brings neural processing to Macs. Let's see what this means for AI workloads on consumer hardware.",
            "points": [
                "M4 Chip Architecture Overview",
                "16-Core CPU and GPU Configuration",
                "Neural Engine: 38 Trillion Operations per Second",
                "Unified Memory Architecture",
                "AI/ML Performance Benchmarks",
                "What Developers Can Build with M4"
            ],
            "outro": "Apple Silicon is making AI accessible to every Mac user. The desktop AI revolution starts now."
        },
        "gpu_market_2026": {
            "title": "GPU Market 2026 - Who Wins the AI Hardware Race?",
            "intro": "The GPU market is more competitive than ever. Let's analyze the major players and what their hardware means for AI development in 2026.",
            "points": [
                "NVIDIA Dominance in AI GPUs",
                "AMD's Growing AI Portfolio",
                "Intel's Gaudi and Data Center Chips",
                "Custom ASICs: Google TPU, AWS Trainium",
                "Cloud GPU Pricing and Availability",
                "What Developers Should Buy in 2026"
            ],
            "outro": "The GPU market in 2026 is diverse and competitive. Pick the right tool for your AI workload."
        },
        "cuda_vs_rocm": {
            "title": "CUDA vs ROCm - The Software Battle Behind the Hardware",
            "intro": "Great hardware needs great software. Let's compare NVIDIA CUDA and AMD ROCm — the two dominant GPU programming platforms for AI.",
            "points": [
                "CUDA: NVIDIA's GPU Computing Platform",
                "ROCm: AMD's Open GPU Stack",
                "Performance Comparison for AI Workloads",
                "PyTorch and TensorFlow Support",
                "Developer Experience: Tools and Debugging",
                "Which Platform Should You Choose?"
            ],
            "outro": "Both CUDA and ROCm have strengths. The best choice depends on your hardware and workflow."
        }
    }

    # ====
    # DATA SCIENCE TOPICS
    # ====
    DATA_SCIENCE_TOPICS = {
        "python_data_visualization": {
            "title": "Python Data Visualization - Matplotlib, Seaborn, Plotly",
            "intro": "Visualizing data is key to understanding it. Learn the essential Python libraries for creating beautiful, informative charts and graphs.",
            "points": [
                "Matplotlib: The Foundation of Python Plotting",
                "Seaborn: Statistical Visualizations Made Easy",
                "Plotly: Interactive Charts for the Web",
                "Choosing the Right Visualization Type",
                "Color Theory for Data Visualization",
                "Building Dashboards with Python"
            ],
            "outro": "Great visualizations tell stories. Master these tools and make your data speak."
        },
        "sql_advanced_queries": {
            "title": "Advanced SQL Queries - Master Complex Database Operations",
            "intro": "Take your SQL skills to the next level. Learn window functions, CTEs, subqueries, and optimization techniques used by professional data engineers.",
            "points": [
                "Common Table Expressions (CTEs)",
                "Window Functions: ROW_NUMBER, RANK, LEAD/LAG",
                "Correlated Subqueries vs Joins",
                "Pivot Tables in SQL",
                "Query Optimization and Indexing",
                "Aggregation Techniques for Analytics"
            ],
            "outro": "Advanced SQL opens doors to complex data analysis. Practice these patterns regularly."
        },
        "databricks_overview": {
            "title": "Databricks - The Unified Data Analytics Platform",
            "intro": "Databricks combines data engineering, data science, and business analytics in one platform. Here's what you need to know.",
            "points": [
                "What is Databricks?",
                "Lakehouse Architecture Explained",
                "Delta Lake: Reliable Data Lakes",
                "Collaborative Notebooks and Workflows",
                "MLflow for Machine Learning Lifecycle",
                "When to Use Databricks vs Alternatives"
            ],
            "outro": "Databricks simplifies the data workflow. Understand it and accelerate your analytics."
        },
        "apache_spark_basics": {
            "title": "Apache Spark - Big Data Processing Made Simple",
            "intro": "Apache Spark processes massive datasets at speed. Learn the fundamentals of this powerful distributed computing framework.",
            "points": [
                "What is Apache Spark?",
                "Spark Architecture: Driver, Executor, Cluster",
                "RDDs vs DataFrames vs Datasets",
                "Spark SQL for Structured Data",
                "Streaming with Structured Streaming",
                "Performance Tuning Tips"
            ],
            "outro": "Spark is essential for big data. Master the basics and scale your data processing."
        },
        "tableau_vs_powerbi": {
            "title": "Tableau vs Power BI - Which Visualization Tool Wins?",
            "intro": "Two giants dominate business intelligence. Let's compare Tableau and Power BI across features, pricing, ease of use, and real-world performance.",
            "points": [
                "Tableau: Strengths and Limitations",
                "Power BI: Strengths and Limitations",
                "Data Connection and Preparation",
                "Visualization Capabilities Compared",
                "Pricing and Licensing Models",
                "Which Tool for Your Organization?"
            ],
            "outro": "Both tools are excellent. The best choice depends on your team, budget, and existing ecosystem."
        },
        "data_wrangling_python": {
            "title": "Data Wrangling with Python - Pandas, NumPy, and Polars",
            "intro": "Real-world data is messy. Learn the essential Python tools and techniques for cleaning, transforming, and preparing data for analysis.",
            "points": [
                "Pandas: DataFrames and Series Fundamentals",
                "NumPy: Fast Numerical Computing",
                "Polars: The Faster Alternative to Pandas",
                "Handling Missing Data and Outliers",
                "Data Transformation and Joining Techniques",
                "Performance: When to Use Each Library"
            ],
            "outro": "Clean data leads to good analysis. These tools will handle anything you throw at them."
        },
        "feature_engineering": {
            "title": "Feature Engineering - The Secret to Better ML Models",
            "intro": "Great features beat great algorithms. Learn the art and science of creating features that make machine learning models smarter.",
            "points": [
                "What is Feature Engineering?",
                "Domain Knowledge in Feature Creation",
                "Encoding Categorical Variables",
                "Scaling and Normalization Techniques",
                "Feature Selection Methods",
                "Common Pitfalls and How to Avoid Them"
            ],
            "outro": "Feature engineering is where domain expertise meets data science. Invest time here."
        }
    }

    # ====
    # MACHINE LEARNING TOPICS
    # ====
    ML_TOPICS = {
        "transformers_architecture": {
            "title": "Transformers Architecture - The Engine Behind Modern AI",
            "intro": "Transformers power everything from chatbots to image generators. Let's open the hood and understand the architecture that changed AI forever.",
            "points": [
                "The Transformer Blueprint: Encoder-Decoder",
                "Self-Attention Mechanism Deep Dive",
                "Multi-Head Attention Explained",
                "Positional Encoding: Giving Order to Sequences",
                "Feed-Forward Networks in Transformers",
                "From BERT to GPT: Evolution of Transformers"
            ],
            "outro": "Transformers are the foundation of modern AI. Understanding them unlocks the whole field."
        },
        "rag_vs_finetuning": {
            "title": "RAG vs Fine-tuning - Which Approach for Your AI App?",
            "intro": "Retrieval Augmented Generation and fine-tuning are two powerful ways to customize LLMs. Let's compare them so you can choose wisely.",
            "points": [
                "What is RAG? How It Works",
                "What is Fine-tuning? The Basics",
                "Cost Comparison: RAG vs Fine-tuning",
                "When RAG is the Better Choice",
                "When Fine-tuning Wins",
                "Hybrid Approaches: Best of Both Worlds"
            ],
            "outro": "There's no one-size-fits-all answer. Match the approach to your use case and constraints."
        },
        "mlops_pipeline": {
            "title": "MLOps Pipeline - From Model to Production",
            "intro": "Building ML models is only half the battle. MLOps takes you from experiment to production with automated pipelines, monitoring, and governance.",
            "points": [
                "What is MLOps? Core Principles",
                "Data Versioning with DVC",
                "Model Training Pipelines",
                "Model Registry and Versioning",
                "Deployment: Batch, Real-time, Edge",
                "Monitoring and Drift Detection"
            ],
            "outro": "MLOps turns ML projects into reliable products. Automation is the key to scaling."
        },
        "llm_security_risks": {
            "title": "LLM Security Risks - What Can Go Wrong and How to Prevent It",
            "intro": "Large language models bring new security challenges. From prompt injection to data leakage, let's examine the risks and defenses.",
            "points": [
                "Prompt Injection Attacks Explained",
                "Data Leakage Through Model Outputs",
                "Training Data Poisoning Risks",
                "Model Theft and Extraction Attacks",
                "Secure LLM Deployment Practices",
                "Building Guardrails and Content Filters"
            ],
            "outro": "Security must be part of every LLM project. Stay informed and build defenses from day one."
        },
        "deep_learning_cnn": {
            "title": "Deep Learning with CNNs - Image Recognition Fundamentals",
            "intro": "Convolutional Neural Networks revolutionized computer vision. Learn how CNNs work, why they're so effective, and how to build them.",
            "points": [
                "What Makes CNNs Special?",
                "Convolution Operation Explained",
                "Pooling Layers: Downsampling for Features",
                "Building a CNN Architecture",
                "Transfer Learning with Pre-trained Models",
                "Training Tips: Data Augmentation, Regularization"
            ],
            "outro": "CNNs are the workhorse of image AI. Master them and unlock visual intelligence."
        },
        "gradient_descent_explained": {
            "title": "Gradient Descent - How AI Models Learn",
            "intro": "Gradient descent is the optimization algorithm at the heart of machine learning. Let's build intuition for how models actually learn from data.",
            "points": [
                "The Intuition Behind Gradient Descent",
                "Loss Functions: Measuring Model Error",
                "Learning Rate: The Most Important Hyperparameter",
                "Variants: SGD, Mini-batch, Adam",
                "Convergence and Local Minima",
                "Visualizing the Optimization Landscape"
            ],
            "outro": "Gradient descent is how learning happens. Understand it and you understand ML fundamentals."
        },
        "overfitting_underfitting": {
            "title": "Overfitting vs Underfitting - Finding the Sweet Spot in ML",
            "intro": "Every ML practitioner faces the bias-variance tradeoff. Learn how to diagnose and fix overfitting and underfitting in your models.",
            "points": [
                "What is Overfitting? Signs and Symptoms",
                "What is Underfitting? When Models Are Too Simple",
                "The Bias-Variance Tradeoff",
                "Cross-Validation for Model Assessment",
                "Regularization: L1, L2, Dropout",
                "Early Stopping and Model Selection"
            ],
            "outro": "The best model balances complexity and simplicity. Learn to find that balance."
        }
    }

    # ====
    # PROGRAMMING TOPICS
    # ====
    PROGRAMMING_TOPICS = {
        "python_type_hinting": {
            "title": "Python Type Hinting - Write Self-Documenting Code",
            "intro": "Type hints make Python code clearer, safer, and easier to maintain. Learn how to use them effectively in your projects.",
            "points": [
                "Why Type Hints Matter in Python",
                "Basic Type Annotations: str, int, list, dict",
                "Generic Types with typing Module",
                "Type Hinting for Functions and Methods",
                "Static Type Checking with mypy",
                "Advanced: Protocols, TypeVar, and Union"
            ],
            "outro": "Type hints are Pythonic best practice. Start small and grow your usage over time."
        },
        "rust_for_python_devs": {
            "title": "Rust for Python Developers - A Practical Introduction",
            "intro": "Rust offers speed, safety, and modern tooling. If you know Python, here's how Rust compares and what you need to get started.",
            "points": [
                "Rust vs Python: Key Differences",
                "Ownership, Borrowing, and Lifetimes",
                "Pattern Matching and Error Handling",
                "Building a CLI Tool in Rust",
                "Rust Performance vs Python",
                "When to Use Rust vs Python"
            ],
            "outro": "Rust and Python complement each other. Learn both and expand your toolkit."
        },
        "web_devjavascript frameworks": {
            "title": "Web Development in 2026 - Frameworks, Tools, and Trends",
            "intro": "Web development moves fast. Let's survey the current landscape of frameworks, tools, and best practices for building modern web applications.",
            "points": [
                "Frontend Frameworks: React, Vue, Svelte, Solid",
                "Backend Options: Node.js, Python, Go, Rust",
                "Full-Stack Frameworks: Next.js, Remix, Astro",
                "TypeScript: The Standard for Modern Web Dev",
                "WebAssembly: Running Code at Native Speed",
                "What's Trending and What's Fading"
            ],
            "outro": "The web dev landscape in 2026 is rich and competitive. Pick the right stack for your project."
        },
        "api_design_rest_graphql": {
            "title": "API Design - REST vs GraphQL vs gRPC",
            "intro": "Choosing the right API architecture shapes your entire application. Let's compare REST, GraphQL, and gRPC with real examples.",
            "points": [
                "REST: Principles and Best Practices",
                "GraphQL: Flexible Queries and Strongly Typed Schema",
                "gRPC: High-Performance RPC with Protocol Buffers",
                "API Versioning Strategies",
                "Documentation: OpenAPI, GraphQL Introspection",
                "Choosing the Right API Style for Your Use Case"
            ],
            "outro": "Each API style has its place. Understand the tradeoffs and design with your consumers in mind."
        },
        "docker_kubernetes_basics": {
            "title": "Docker and Kubernetes - Containerization and Orchestration Basics",
            "intro": "Containers changed how we build and deploy software. Learn Docker fundamentals and how Kubernetes orchestrates containers at scale.",
            "points": [
                "What are Containers and Why They Matter",
                "Docker: Images, Containers, and Dockerfile",
                "Docker Compose for Multi-Container Apps",
                "Kubernetes Architecture: Pods, Nodes, Clusters",
                "Deploying Applications on Kubernetes",
                "When to Use Docker vs Kubernetes vs Both"
            ],
            "outro": "Containers are the building blocks of modern infrastructure. Start with Docker, grow into Kubernetes."
        },
        "testing_pytest_best_practices": {
            "title": "Testing with Pytest - Best Practices for Reliable Code",
            "intro": "Good tests make confident developers. Learn pytest essentials and testing patterns that catch bugs before they reach production.",
            "points": [
                "Why Testing Matters in Software Development",
                "Pytest Basics: Fixtures, Assertions, and Parametrization",
                "Writing Readable and Maintainable Tests",
                "Mocking and Patching External Dependencies",
                "Test Coverage and What It Really Means",
                "CI/CD Integration for Automated Testing"
            ],
            "outro": "Tests are an investment in quality. Write them early and let them guide your design."
        },
        "async_programming_python": {
            "title": "Async Programming in Python - asyncio and Beyond",
            "intro": "Async code handles many tasks concurrently without threads. Let's explore Python's asyncio library and when async beats synchronous code.",
            "points": [
                "Synchronous vs Asynchronous: Key Concepts",
                "async/await Syntax in Python",
                "The asyncio Event Loop Explained",
                "Async I/O: Files, Network, Databases",
                "Concurrency vs Parallelism in Python",
                "Common Pitfalls and Debugging Async Code"
            ],
            "outro": "Async programming unlocks scalable I/O. Use it when it fits, not everywhere."
        }
    }

    # ====
    # TRENDING / DYNAMIC TOPICS
    # ====
    TRENDING_TOPICS: List[dict] = []

    # Combined lookup by category
    CATEGORY_MAP = {
        "ai": AI_TOPICS,
        "gpu": GPU_TOPICS,
        "data_science": DATA_SCIENCE_TOPICS,
        "machine_learning": ML_TOPICS,
        "programming": PROGRAMMING_TOPICS,
    }

    ALL_TOPICS: Dict[str, dict] = {}
    for cat_topics in CATEGORY_MAP.values():
        ALL_TOPICS.update(cat_topics)

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.config = {}
        self.load_config()
        self.CONTENT_LIBRARY = CONTENT_LIBRARY  # module-level, from content_library.py

    def load_config(self):
        """Load configuration from YAML file."""
        try:
            import yaml
            with open(self.config_path, 'r') as f:
                self.config = yaml.safe_load(f) or {}
        except Exception:
            self.config = {}

    def get_available_topics(self, category: str = "all", limit: int = 12) -> List[dict]:
        """Get available topics, optionally filtered by category."""
        if category == "all":
            topics = list(self.ALL_TOPICS.values())
        elif category in self.CATEGORY_MAP:
            topics = list(self.CATEGORY_MAP[category].values())
        else:
            topics = list(self.ALL_TOPICS.values())

        # Merge with trending topics if available
        if self.TRENDING_TOPICS:
            for trending in self.TRENDING_TOPICS[:limit]:
                if trending not in topics:
                    topics.insert(0, trending)

        return topics[:limit]

    def get_topic_list(self) -> List[str]:
        """Get list of all topic keys."""
        return list(self.ALL_TOPICS.keys())

    def get_daily_topic(self) -> str:
        """Get a rotating daily topic."""
        day_of_year = datetime.now().timetuple().tm_yday
        topics = list(self.ALL_TOPICS.keys())
        return topics[day_of_year % len(topics)]

    def search_trending_topics(self, queries: Optional[List[str]] = None, num_results: int = 10) -> List[dict]:
        """Try to fetch trending topics from YouTube or fallback to static list."""
        trending = []

        if HAS_YOUTUBE_TRENDING:
            try:
                from youtube_trending import _resolve_api_key
                yt = YouTubeTrending(api_key=_resolve_api_key())
                trending = [
                    {**it.topic_dict, "source": "youtube-api", "topic": it.title}
                    for it in yt.discover(top_n=num_results)
                ]
            except Exception:
                pass

        if not trending and HAS_RESEARCH:
            try:
                if queries is None:
                    queries = [
                        "what is trending in AI technology 2026",
                        "latest GPU hardware releases 2026",
                        "data science tools trending now",
                        "machine learning breakthroughs this week",
                        "programming languages most popular 2026"
                    ]
                research = TopicResearch()
                results = research.search_ddg(queries, num_results_per_query=2)
                for r in results:
                    if r.get("title") and r.get("body"):
                        info = extract_topic_info(r["body"], queries=[r.get("href", "")])
                        info["source"] = "trending"
                        info["trending_ref"] = r.get("href", "")
                        trending.append(info)
            except Exception:
                pass

        if not trending:
            trending = [
                {
                    "title": "GPT-5.2 and DeepSeek-R1: Weekly AI Roundup",
                    "intro": "OpenAI just dropped GPT-5.2 and DeepSeek released R1 — here's what's changing this week in AI.",
                    "points": [
                        "GPT-5.2 vs DeepSeek-R1 comparison",
                        "New features in GPT-5.2",
                        "DeepSeek-R1 open-source impact",
                        "What developers should test first",
                        "Pricing and availability update"
                    ],
                    "outro": "Two major releases in one week — AI moves fast. Which one will you try first?",
                    "source": "trending",
                },
                {
                    "title": "Neuromorphic Chips Cut AI Costs by 40%",
                    "intro": "New neuromorphic hardware is slashing inference costs. Let's look at what this means for edge AI and sustainable computing.",
                    "points": [
                        "What are neuromorphic chips?",
                        "How they differ from GPUs",
                        "40% cost reduction explained",
                        "Real-world deployments",
                        "When neuromorphic makes sense"
                    ],
                    "outro": "Neuromorphic computing could change the economics of AI. Keep an eye on this space.",
                    "source": "trending",
                },
            ]

        self.TRENDING_TOPICS = trending
        return trending

    def _topic_ref(self, clean_name: str) -> str:
        """Return a natural reference phrase for the topic title."""
        return clean_name

    def _sub_points_for(self, point: str, template: Optional[dict] = None) -> List[str]:
        """Build sub-points for a given main point from template or content."""
        if template and "sub_points" in template and point in template["sub_points"]:
            return template["sub_points"][point]
        
        # Derive from the detailed explanation
        detail = self._expand_point_technical(point, template or {})
        sentences = re.split(r'(?<=[.!?])\s+', detail)
        sub = []
        for s in sentences:
            s = s.strip()
            if len(s) > 15 and len(sub) < 3:
                sub.append(s)
        if not sub:
            sub = [f"What {point} really is", "Key concepts explained", "Real-world example"]
        return sub[:3]

    def _strip_leading_title(self, entry: str, point: str) -> str:
        """Strip the leading title sentence from a library entry.

        Library entries start with '{title}. {content}...'.
        Since callers prepend the point title themselves, we drop
        the first sentence and return the rest.
        """
        sentences = entry.split(". ")
        if len(sentences) < 2:
            return entry.strip()
        # Drop first sentence (the title), keep the rest
        content = ". ".join(s.strip() for s in sentences[1:] if s.strip())
        if not content.endswith("."):
            content += "."
        return content


    def _expand_point_technical(self, point: str, template: Optional[dict] = None) -> str:
        """Expand a point into detailed technical voiceover text.

        Looks up real topic-specific content from CONTENT_LIBRARY.
        Returns the expansion WITHOUT the leading title sentence (caller
        already prepends the point title). Falls back to a natural-sounding
        generic expansion if no match found.

        For custom/topical topics without library entries, prefer the
        topic_dict's research_text or intro as a richer fallback than the
        generic paragraph.
        """
        topic = (template or {}).get("title", "this technology")
        topic_lower = topic.lower()
        point_lower = point.lower()
        point_key = point_lower.strip()
        topic_dict = (template or {}).get("topic_dict", {})
        research_text = (template or {}).get("research_text", "")

        # When we have live research, ground the voiceover in it — it beats
        # the static library which is mostly template filler.
        if research_text and len(research_text) > 80:
            return self._extract_research_sentence(point, research_text, topic)

        # Try exact match in content library, strip leading title
        # Library is mostly filler — skip it if it looks generic.
        def _is_generic(e: str) -> bool:
            low = e.lower()
            return ("fundamental concept in modern technology" in low) or ("at its core, this refers to the underlying principles" in low) or low.count("core concept") > 0
        if point_key in CONTENT_LIBRARY:
            entry = CONTENT_LIBRARY[point_key]
            if not _is_generic(entry):
                return self._strip_leading_title(entry, point)

        # Try fuzzy match: strip leading numbers, "the", etc.
        import re as _re
        cleaned = _re.sub(r'^(the |a |an |what is |what are |how to |why )', '', point_key)
        if cleaned in CONTENT_LIBRARY:
            entry = CONTENT_LIBRARY[cleaned]
            if not _is_generic(entry):
                return self._strip_leading_title(entry, point)

        topic_intro = topic_dict.get("intro", "") or topic_dict.get("description", "")

        if topic_intro and len(topic_intro) > 80:
            return self._adapt_topic_content(point, topic_intro, topic)

        return self._fallback_expansion(point, topic, topic_lower)

    def _generate_description(self, template: dict, topic: str) -> str:
        """Generate YouTube description."""
        points_text = "\n".join([f"✓ {p}" for p in template["points"]])

        category = "tech"
        for cat, tags in self.CATEGORY_TAGS.items():
            if any(t.lower() in topic.lower() for t in tags[:2]):
                category = cat
                break

        desc_parts = [
            template['intro'],
            "",
            "📚 What You'll Learn:",
            points_text,
            "",
            "🔗 Related Videos:",
            "• AI Playlist: [Link]",
            "• Python Tutorials: [Link]",
            "• Data Science Series: [Link]",
            "",
            "⏰ Timestamps:",
            "0:00 - Introduction",
            f"0:05 - {template['points'][0] if template['points'] else 'Overview'}",
        ]
        
        for i, p in enumerate(template['points'][:5]):
            desc_parts.append(f"{int(5 + (i+1) * 30)}:00 - {p}")
        
        desc_parts.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━",
            "🔔 Subscribe for daily tech content!",
            "👍 Like if this helped you",
            "💬 Comment your questions below",
            "━━━━━━━━━━━━━━━━━━━",
            "",
            f"#tech #coding #AI #machinelearning #datascience #programming #tutorial #{topic.replace('_', '')}"
        ])
        
        return "\n".join(desc_parts)

    def _fallback_expansion(self, point: str, topic: str, topic_lower: str) -> str:
        """Fallback: produce a natural explanation for any topic.
        
        Uses the topic title and point text to construct a coherent
        explanation with theory + practical angle, avoiding the
        generic filler that previously produced nonsense.
        """
        # Extract a clean subject noun from the topic title
        topic_words = topic_lower.replace("_", " ").replace("-", " ").split()
        # Take meaningful words (skip articles, prepositions)
        skip = {"the", "a", "an", "and", "or", "for", "in", "on", "at", "to", "of", "vs", "with"}
        subject = " ".join(w for w in topic_words if w not in skip).strip()
        if not subject:
            subject = topic_lower.replace("_", " ")
        subject_simple = subject.split()[0] if subject.split() else "this technology"

        point_clean = point.lower().strip()
        # Remove leading numbering
        import re as _re
        point_clean = _re.sub(r'^\d+[\s.).]*', '', point_clean)

        hint = self._topic_hint(topic) or f"core idea behind {subject_simple}"
        return (
            f"{point.strip()} covers {hint}. "
            f"In practice it determines how {subject_simple} behaves, what you can build, and where failures show up. "
            f"A concrete example: apply this idea to a small {subject_simple} project, measure latency, accuracy, or resource use, "
            f"and compare against the alternative — you will see the tradeoff clearly. "
            f"Mastering this pays off when you interview, debug, or design for production."
        )

    def _adapt_topic_content(self, point: str, topic_intro: str, topic: str) -> str:
        """Adapt topic intro text into a point-specific expansion.

        When a topic is not in CONTENT_LIBRARY (custom/trending), use the
        topic's own intro to build a point-specific voiceover variation.
        Each point gets DIFFERENT content — not the same paragraph repeated.
        """
        import re as _re
        point_clean = point.lower().strip()
        point_clean = _re.sub(r'^\d+[\s.).]*', '', point_clean)
        point_title = point.strip()  # full title as-is

        topic_words = topic.lower().replace("_", " ").replace("-", " ").split()
        skip = {"the", "a", "an", "and", "or", "for", "in", "on", "at", "to", "of", "vs", "with"}
        subject = " ".join(w for w in topic_words if w not in skip).strip()
        if not subject:
            subject = topic.lower().replace("_", " ")

        intro_sentences = [s.strip().rstrip(" .") for s in
                           topic_intro.replace("?", ".").replace("!", ".").split(". ")
                           if len(s.strip()) > 15]
        if not intro_sentences:
            intro_sentences = [topic_intro.strip()]

        # Rotate sentences per point so each gets different content.
        # Point index determines which sentence subset to use.
        point_idx = len(point_clean) % 4  # stable per-point rotation
        sentence_count = len(intro_sentences)

        if sentence_count == 0:
            body = f"This is an important aspect of {subject}. "
        elif sentence_count == 1:
            body = f"{intro_sentences[0]}. "
        else:
            # Assign different sentences to different points via rotation
            chosen = []
            for offset in range(min(3, sentence_count)):
                idx = (point_idx * 2 + offset) % sentence_count
                chosen.append(intro_sentences[idx])
            # Deduplicate consecutive repeats
            deduped = []
            for sent in chosen:
                if not deduped or sent != deduped[-1]:
                    deduped.append(sent)
            body = ". ".join(deduped) + ". "

        simple = self._subject_simple(target=subject)
        s_last = f"The key takeaway: {simple} is evolving fast — {point_title} shows why this matters for the future of technology."
        return body + s_last

    def _extract_research_sentence(self, point: str, research_text: str, topic: str) -> str:
        """Extract a relevant sentence from research text for a given point.

        Each point gets different content via sentence scoring + rotation.
        """
        import re as _re
        point_clean = point.lower().strip()
        point_words = [w for w in point_clean.split() if len(w) > 3]
        topic_words = topic.lower().replace("_", " ").replace("-", " ").split()
        skip = {"the", "a", "an", "and", "or", "for", "in", "on", "at", "to", "of", "vs", "with"}
        subject = " ".join(w for w in topic_words if w not in skip).strip()
        if not subject:
            subject = topic.lower().replace("_", " ")

        clean = research_text.replace("\n", " ").replace("  ", " ")
        raw = [s.strip().rstrip(" .") for s in clean.split(". ") if len(s.strip()) > 20]
        sentences = [s for s in raw if len(s) > 20]

        if not sentences:
            simple = self._subject_simple(target=subject)
            return f"This is a key area of {subject}. The practical impact: understanding this helps you work more effectively with {simple}."

        # Score sentences by relevance to the point
        scored = []
        for s in sentences:
            score = sum(1 for w in point_words if w in s.lower())
            score += sum(1 for w in topic_words if w in s.lower())
            scored.append((score, s))

        scored.sort(key=lambda x: -x[0])
        top = scored[:3] if scored else []
        point_idx = len(point_clean) % 3

        # Pick different sentences per point (rotate selection)
        chosen = []
        used = set()
        start = point_idx
        for i in range(3):
            idx = (start + i) % len(top) if top else 0
            if top:
                sent = top[idx][1]
                if sent not in used:
                    used.add(sent)
                    chosen.append(sent)
        if not chosen and top:
            chosen = [top[0][1]]

        content = ". ".join(chosen) + ". " if chosen else f"This is an important aspect of {subject}. "
        simple = self._subject_simple(target=subject)
        # Build a richer expansion: research + practical angle + tradeoff
        expansion = f"{content}"
        expansion += f"In practice, {simple} works by taking raw inputs, applying transformations, and producing optimized representations that models can learn from more effectively. "
        expansion += f"The key tradeoff is between feature richness and computational cost — more expressive features improve accuracy but increase training time and complexity. "
        expansion += f"Common pitfalls include data leakage, over-engineering, and ignoring domain constraints. "
        expansion += f"Understanding this helps you work more effectively with {simple} in production environments."
        return expansion

    # Topic-aware padding sentences used to lift narration to the 700-1,200
    # word depth the channel targets. {subj} is substituted per video.
    _PAD_POOL = [
        "How this works in practice: the flow is input, processing, and output, and each of those stages has its own failure modes worth tracking.",
        "A common misconception is treating {subj} as a single fixed technique; in reality it is a family of approaches that are tuned to the specific problem at hand.",
        "Try it yourself: build a small prototype, pick exactly one metric, measure it before and after, then compare against the naive alternative on the same data.",
        "For teams that dependency matters, this leads to cleaner architecture decisions, fewer surprises in production, and faster onboarding for new engineers.",
        "Interviewers frequently probe this space with tradeoff questions covering performance, scalability, cost, and maintainability, so practice reasoning about those out loud.",
        "Compared with older approaches, {subj} tends to win on flexibility and long-term maintainability, especially once requirements start changing on a regular basis.",
        "The ecosystem around this space matures quickly, so staying current with the tooling keeps your skills relevant and protects your projects from becoming outdated.",
        "When evaluating a solution, look beyond the headline numbers: measure latency under load, check the failure behavior, and estimate the operational overhead involved.",
        "A practical way to learn is to reproduce a well-known example end to end, then modify one assumption at a time and observe how the outcome changes.",
        "Keep the tradeoffs visible in documentation, because the decision you make today is often revisited when new data or new requirements arrive.",
    ]

    def _pad_to_budget(self, base: str, point: str, template: dict,
                       budget: int, subject: str = "") -> str:
        """Append topic-aware sentences until a scene voiceover reaches the
        target word budget. Different points get a different rotation of
        padding so the narration never sounds copy-pasted."""
        words = base.split()
        if len(words) >= budget:
            return base
        if not subject:
            subject = self._subject_simple(target=(template or {}).get("title", ""))
        start = hash((point or "").lower()) % len(self._PAD_POOL)
        sentences = []
        acc = 0
        i = 0
        while acc + len(words) < budget and i < len(self._PAD_POOL):
            s = self._PAD_POOL[(start + i) % len(self._PAD_POOL)].replace("{subj}", subject)
            sentences.append(s)
            acc += len(s.split())
            i += 1
        if not sentences:
            return base
        return base.rstrip() + " " + " ".join(sentences)

    def _pad_scene_voiceovers(self, scenes: List[Scene], info: dict,
                              target_duration: float) -> List[Scene]:
        """Enforce the 700-1,200 word narration target on script scenes.

        Only the content (point) scenes are padded; the intro hook and the
        CTA outro stay short by design so pacing is preserved.
        """
        points = info.get("points", [])
        intro_words = len((info.get("intro", "") or "").split())
        outro_words = len((info.get("outro", "") or "").split())
        total_target = 1100 if target_duration >= 5 else (900 if target_duration >= 3 else 720)
        remaining = total_target - intro_words - outro_words - 40
        budget = max(140, remaining // max(len(points), 1))
        subject = self._subject_simple(target=info.get("title", ""))
        for scene in scenes[1:-1]:
            vo = (scene.voiceover or "").strip()
            if not vo:
                continue
            scene.voiceover = self._pad_to_budget(vo, scene.text, info, budget, subject)
        return scenes

    def _topic_hint(self, topic: str) -> str:
        """Short educational cue for a topic, to keep fallback grounded."""
        low = (topic or "").lower()
        if any(k in low for k in ["rag", "retrieval"]): return "how retrieval augments generation with external knowledge"
        if any(k in low for k in ["finetun", "fine-tun"]): return "when to fine-tune vs use retrieval"
        if any(k in low for k in ["agent"]): return "how agents plan, use tools, and act autonomously"
        if any(k in low for k in ["transformer"]): return "self-attention and how transformers process sequences"
        if any(k in low for k in ["gpu", "cuda", "nvidia"]): return "how GPU parallelism accelerates AI workloads"
        if any(k in low for k in ["python", "pandas", "polars"]): return "idiomatic Python patterns and performance tradeoffs"
        if any(k in low for k in ["sql"]): return "query planning, indexing, and set-based reasoning"
        if low.startswith("ai_") or " ai " in low: return "core AI concepts and their practical tradeoffs"
        return ""

    def _visual_for_point(self, point: str, idx: int) -> str:
        low = point.lower()
        if any(k in low for k in ["architecture", "transformer", "neural", "cnn", "attention"]):
            return "cinematic_tech"
        if any(k in low for k in ["comparison", " vs ", " vs.", "versus", "trade"]):
            return "educational_infographic"
        if any(k in low for k in ["cost", "benefit", "limit", "pitfall"]):
            return "cinematic_realistic"
        return "cinematic_tech" if idx % 2 == 0 else "cinematic_realistic"

    def _diagram_for_point(self, point: str):
        low = point.lower()
        if any(k in low for k in ["rag vs", "comparison", " vs ", "tradeoff"]):
            if "docker" in low or "kubernetes" in low:
                return ("comparison", {"headers": ["Aspect", "Docker", "Kubernetes"], "rows": [["Scope","Single host","Cluster orchestration"], ["Scaling","Manual","Auto"], ["Use","Dev & test","Production"]]})
            return ("comparison", {"headers": ["Criterion", "RAG", "Fine-tuning"], "rows": [["Grounding","External docs","Weights"], ["Cost","Low retrieval","High training"], ["Updates","Instant docs","Retrain"]]})
        if any(k in low for k in ["architecture", "transformer"]):
            if "kubernetes" in low:
                return ("architecture", {"layers": [{"label": "Client / kubectl"}, {"label": "Control Plane"}, {"label": "Worker Nodes"}, {"label": "Pods & Containers"}]})
            if "docker" in low:
                return ("architecture", {"layers": [{"label": "Docker Client"}, {"label": "Docker Engine"}, {"label": "Image & Layers"}, {"label": "Container"}]})
            return ("architecture", {"layers": [{"label": "Input / Prompt"}, {"label": "Self-Attention"}, {"label": "Feed-Forward"}, {"label": "Output"}]})
        if any(k in low for k in ["pipeline", "flow", "how rag"]):
            return ("data_pipeline", {"stages": [{"label": "Query"}, {"label": "Retrieve"}, {"label": "Rank"}, {"label": "Generate"}]})
        if "how it works" in low:
            if "docker" in low or "kubernetes" in low:
                return ("data_pipeline", {"stages": [{"label": "Build"}, {"label": "Ship"}, {"label": "Run"}, {"label": "Scale"}]})
            return (None, None)
        if any(k in low for k in ["cnn", "convolution"]):
            return ("cnn_layers", {"layers": [{"label":"Input","channels":3},{"label":"Conv","channels":32},{"label":"Pool","channels":64},{"label":"Output","channels":10}]})
        if any(k in low for k in ["neural", "network", "graph"]):
            return ("network_graph", {"nodes": [{"id": "Input","label": "Input"}, {"id": "Hidden","label": "Hidden"}, {"id": "Output","label": "Output"}], "edges": [{"from": "Input","to": "Hidden"}, {"from": "Hidden","to": "Output"}]})
        return (None, None)

    def _subject_simple(self, target: str = "") -> str:
        """Extract a simple subject noun from a topic name."""
        words = target.lower().replace("_", " ").replace("-", " ").split()
        skip = {"the", "a", "an", "and", "or", "for", "in", "on", "at", "to", "of", "vs", "with"}
        result = " ".join(w for w in words if w not in skip).strip()
        return (result.split()[0] if result.split() else "this technology")

    # CONTENT_LIBRARY — populated at module level from content_library.py.
    # Instances reference the module-level dict directly.
    CATEGORY_TAGS = {
        "ai": ["ai", "artificial intelligence", "machine learning", "deep learning"],
        "gpu": ["gpu", "hardware", "nvidia", "amd", "computing"],
        "data_science": ["data science", "analytics", "visualization", "big data"],
        "machine_learning": ["machine learning", "neural networks", "training", "models"],
        "programming": ["programming", "coding", "software", "development", "python", "rust"],
    }

    def _generate_tags(self, topic: str, template: dict) -> List[str]:
        """Generate relevant YouTube tags."""
        base_tags = ["tech", "technology", "coding", "programming", "tutorial"]
        
        topic_lower = topic.lower()
        category_tags = []
        for cat, tags in self.CATEGORY_TAGS.items():
            if any(t.lower() in topic_lower for t in tags[:2]):
                category_tags = tags
                break
        
        if not category_tags:
            category_tags = [topic.replace("_", " ")]
        
        title_words = template["title"].lower().split()
        specific = [w for w in title_words if len(w) > 3][:5]
        
        return base_tags + category_tags + specific

    def generate_script(self, topic: str, style: str = "educational", 
                        target_duration: float = 3.0, topic_dict: Optional[dict] = None,
                        difficulty: str = "intermediate", research_text: str = "") -> 'VideoScript':
        """Generate a complete video script for a given topic.
        
        Args:
            topic: Topic key from ALL_TOPICS or custom topic name
            style: Scene generation style - educational, listicle, tutorial, deep_dive
            target_duration: Target video length in minutes (1-5)
            topic_dict: Pre-built topic dict to use instead of looking up ALL_TOPICS
            difficulty: Target audience level - "beginner", "intermediate", or "advanced"
                       Controls explanation depth, voiceover complexity, and visual detail.
            research_text: Optional grounded research summary injected into point expansions.
        
        Returns:
            VideoScript with generated scenes and metadata
        """
        # Resolve topic information
        if topic_dict:
            info = dict(topic_dict)
            clean_topic = topic
        else:
            clean_topic = self._clean_topic_name(topic)
            info = self.ALL_TOPICS.get(clean_topic, None)
            info = dict(info) if info else None
        
        if not info:
            info = self._create_custom_template(clean_topic)
        if research_text:
            info["research_text"] = research_text
            # Keep original dict for fallback content referencing
            info.setdefault("topic_dict", dict(info))
        
        # Build the script
        title = info.get("title", self._make_title(clean_topic))
        intro = info.get("intro", f"Today we're exploring {clean_topic}.")
        points = info.get("points", [f"What is {clean_topic}?", "Key concepts", "Why it matters"])
        outro = info.get("outro", f"Now you know about {clean_topic}.")
        
        # Generate scenes based on style
        if style == "educational":
            scenes = self._generate_educational_scenes(info, target_duration, difficulty)
        elif style == "listicle":
            scenes = self._generate_listicle_scenes(info, target_duration, difficulty)
        elif style == "tutorial":
            scenes = self._generate_tutorial_scenes(info, target_duration, difficulty)
        elif style == "deep_dive":
            scenes = self._generate_deep_dive_scenes(info, target_duration, difficulty)
        else:
            scenes = self._generate_educational_scenes(info, target_duration, difficulty)
        
        # Enforce the 700-1,200 word narration target so videos explain the
        # topic in real depth instead of a few short sentences.
        scenes = self._pad_scene_voiceovers(scenes, info, target_duration)
        
        # Thumbnail text based on difficulty
        if difficulty == "beginner":
            thumb_text = f"What is {clean_topic}?"
        elif difficulty == "advanced":
            thumb_text = f"{clean_topic}: Deep Technical Dive"
        else:
            thumb_text = title
        
        estimated_duration = sum(s.duration for s in scenes)
        
        return VideoScript(
            title=title,
            description=intro + "\n\n" + "\n".join([f"• {p}" for p in points]),
            tags=self._generate_tags(clean_topic, info),
            scenes=scenes,
            thumbnail_text=thumb_text,
            category="Science & Technology",
            estimated_duration=estimated_duration,
        )

    def _clean_topic_name(self, topic: str) -> str:
        """Clean a topic string into a valid key."""
        return re.sub(r'[^a-z0-9_]', '', topic.lower().replace(' ', '_').replace('-', '_'))

    def _make_title(self, topic: str) -> str:
        """Generate a title for a custom topic."""
        words = topic.replace('_', ' ').title()
        return f"What is {words}? Complete Guide"

    def _create_custom_template(self, topic: str) -> dict:
        """Create a template for a custom topic."""
        title = self._make_title(topic)
        intro = f"Today we're exploring {topic.replace('_', ' ')}. In this video, we'll break down what it is, how it works, and why it matters."
        points = [
            f"What is {topic.replace('_', ' ')}?",
            "Key Concepts and Fundamentals",
            "How It Works in Practice",
            "Real-World Applications",
            "Why It Matters for You"
        ]
        outro = f"Now you understand {topic.replace('_', ' ')}. Keep learning and stay ahead."
        return {
            "title": title,
            "intro": intro,
            "points": points,
            "outro": outro,
        }

    def _generate_educational_scenes(self, template: dict, target_minutes: int, 
                                      difficulty: str = "intermediate") -> List[Scene]:
        """Generate educational scenes explaining a topic step by step.
        
        Difficulty adjusts voiceover depth and visual complexity:
        - beginner: simple analogies, short voiceover, clear visuals
        - intermediate: balanced technical depth (default)
        - advanced: detailed technical explanations, code references
        """
        scenes = []
        num_points = len(template["points"])
        target_seconds = target_minutes * 60
        intro_dur = 5.0
        outro_dur = 5.0
        remaining = target_seconds - intro_dur - outro_dur
        point_dur = max(remaining / max(num_points, 1), 8.0)
        
        # Difficulty-based settings
        if difficulty == "beginner":
            font_size = 65
            text_color = "#FFD700"
            voiceover_template = "{point}. {simple_explanation}"
        elif difficulty == "advanced":
            font_size = 55
            text_color = "#00BFFF"
            voiceover_template = "{point}. {technical_depth}"
        else:
            font_size = 60
            text_color = "#FFFFFF"
            voiceover_template = "{point}. {explanation}"
        
        char_positions = ["right", "left", "right", "left"]
        gestures = ["explaining", "pointing", "thinking", "explaining"]
        
        # Intro scene
        intro_voiceover = template["intro"]
        if difficulty == "beginner":
            intro_voiceover += " Don't worry if some terms sound unfamiliar — we'll break everything down simply."
        elif difficulty == "advanced":
            intro_voiceover += " We'll cover technical details, architecture considerations, and implementation tradeoffs."
        
        scenes.append(Scene(
            text=template["title"],
            voiceover=intro_voiceover,
            duration=intro_dur,
            font_size=font_size + 10,
            text_color=text_color,
            animation="fade",
            character_position="center",
            character_gesture="welcoming",
            enter_from="left",
            visual_style="educational_infographic"
        ))
        
        # Point scenes
        for i, point in enumerate(template["points"]):
            pos = char_positions[i % len(char_positions)]
            gesture = gestures[i % len(gestures)]
            
            # Build voiceover based on difficulty
            if difficulty == "beginner":
                explanation = self._expand_point_technical(point, template)
                # Use all sentences for beginner to hit 500w+ target
                # _expand_point_technical already strips the leading title,
                # so we just prepend the point once
                simple = explanation.strip()
                if not simple.endswith("."):
                    simple += "."
                voiceover = f"{point}. {simple}"
            elif difficulty == "advanced":
                voiceover = self._expand_point_technical(point, template)
            else:
                # intermediate: prepend the point, _expand_point_technical already
                # strips the leading title so we don't double it
                explanation = self._expand_point_technical(point, template)
                voiceover = f"{point}. {explanation}"
            
            d_type, d_data = self._diagram_for_point(point)
            v_style = self._visual_for_point(point, i)
            scenes.append(Scene(
                text=point,
                voiceover=voiceover,
                duration=point_dur,
                font_size=font_size,
                text_color=text_color,
                animation="typewriter" if difficulty == "beginner" else "fade",
                character_position=pos,
                character_gesture=gesture,
                bullet_points=self._sub_points_for(point, template),
                enter_from="right" if pos == "right" else "left",
                visual_style=v_style,
                diagram_type=d_type,
                diagram_data=d_data
            ))
        
        # Outro scene
        outro_voiceover = template["outro"]
        if difficulty == "beginner":
            outro_voiceover += " If you found this helpful, subscribe for more beginner-friendly tech explainers!"
        elif difficulty == "advanced":
            outro_voiceover += " For deeper dives into the technical details, check out the resources in the description."
        
        scenes.append(Scene(
            text="Thanks for Watching!",
            voiceover=outro_voiceover,
            duration=outro_dur,
            font_size=font_size + 5,
            text_color="#00FF88" if difficulty == "beginner" else "#FFD700",
            animation="fade",
            character_position="center",
            character_gesture="waving",
            visual_style="educational_infographic"
        ))
        
        return scenes

    def _generate_listicle_scenes(self, template: dict, target_minutes: int,
                                    difficulty: str = "intermediate") -> List[Scene]:
        """Generate listicle (Top N) style scenes.
        
        Difficulty effects:
        - beginner: simpler items, more visual emphasis
        - intermediate: balanced (default)
        - advanced: technical items, code/diagram references
        """
        scenes = []
        num_points = len(template["points"])
        target_seconds = target_minutes * 60
        intro_dur = 5.0
        outro_dur = 5.0
        remaining = target_seconds - intro_dur - outro_dur
        point_dur = max(remaining / max(num_points, 1), 8.0)
        
        char_positions = ["right", "left", "right", "left"]
        gestures = ["explaining", "pointing", "thinking", "listing"]
        
        # Intro
        intro_voiceover = f"Here are the top {num_points} things to know about {template['title'].split(' - ')[0] if ' - ' in template['title'] else template['title']}. "
        if difficulty == "beginner":
            intro_voiceover += "We'll keep things simple and focus on what matters most."
        elif difficulty == "advanced":
            intro_voiceover += "These points cover technical depth and implementation considerations."
        scenes.append(Scene(
            text=f"Top {num_points}: {template['title'].split(' - ')[0] if ' - ' in template['title'] else template['title']}",
            voiceover=intro_voiceover,
            duration=intro_dur,
            font_size=75,
            text_color="#FF4444",
            animation="slide_up",
            character_position="center",
            character_gesture="excited",
            enter_from="left"
        ))
        
        # Point scenes
        for i, point in enumerate(template["points"]):
            pos = char_positions[i % len(char_positions)]
            gesture = gestures[i % len(gestures)]
            
            if difficulty == "beginner":
                voiceover = f"Number {i+1}: {point}. This is one of the most important things to understand, explained simply."
            elif difficulty == "advanced":
                technical = self._expand_point_technical(point, template)
                voiceover = f"Number {i+1}: {point}. {technical}"
            else:
                voiceover = f"Number {i+1}: {point}. {self._expand_point_technical(point, template)}"
            
            scenes.append(Scene(
                text=f"#{i+1}: {point}",
                voiceover=voiceover,
                duration=point_dur,
                font_size=60,
                text_color="#FFFFFF" if difficulty != "beginner" else "#FFD700",
                animation="highlight" if difficulty == "beginner" else "slide_in",
                character_position=pos,
                character_gesture=gesture,
                enter_from="right" if pos == "right" else "left"
            ))
        
        # Outro
        outro_voiceover = template["outro"]
        if difficulty == "beginner":
            outro_voiceover += " Which of these surprised you the most? Let me know in the comments!"
        
        scenes.append(Scene(
            text="Which One Surprised You?",
            voiceover=outro_voiceover,
            duration=outro_dur,
            font_size=65,
            text_color="#00FF88",
            animation="fade",
            character_position="center",
            character_gesture="waving"
        ))
        
        return scenes

    def _generate_tutorial_scenes(self, template: dict, target_minutes: int,
                                    difficulty: str = "intermediate") -> List[Scene]:
        """Generate step-by-step tutorial scenes.
        
        Difficulty effects:
        - beginner: very slow pace, hand-holding, minimal jargon
        - intermediate: balanced pace with explanations (default)
        - advanced: faster pace, code snippets, optimization tips
        """
        scenes = []
        num_points = len(template["points"])
        target_seconds = target_minutes * 60
        intro_dur = 5.0
        outro_dur = 6.0
        remaining = target_seconds - intro_dur - outro_dur
        point_dur = max(remaining / max(num_points, 1), 10.0)
        
        char_positions = ["right", "left", "right", "left"]
        gestures = ["explaining", "pointing", "thinking", "explaining"]
        
        # Title
        scenes.append(Scene(
            text=template["title"],
            voiceover=f"In this tutorial, you'll learn what it is, how to use it, and why it matters. {template['intro']} Let's get started.",
            duration=intro_dur,
            font_size=70,
            text_color="#00BFFF",
            animation="typewriter",
            character_position="center",
            character_gesture="welcoming"
        ))
        
        # Steps
        for i, point in enumerate(template["points"]):
            pos = char_positions[i % len(char_positions)]
            gesture = gestures[i % len(gestures)]
            
            if difficulty == "beginner":
                voiceover = f"Step {i+1}: {point}. Let me walk you through this carefully, step by step."
                sub_points = self._sub_points_for(point, template)
                voiceover += f" First, {sub_points[0] if sub_points else 'start with the basics'}."
            elif difficulty == "advanced":
                voiceover = f"Step {i+1}: {point}. {self._expand_point_technical(point, template)}"
            else:
                voiceover = f"Step {i+1}: {point}. {self._expand_point_technical(point, template)}"
            
            scenes.append(Scene(
                text=f"Step {i+1}: {point}",
                voiceover=voiceover,
                duration=point_dur,
                font_size=65 if difficulty != "beginner" else 70,
                text_color="#FFFFFF",
                animation="typewriter" if difficulty == "beginner" else "highlight",
                character_position=pos,
                character_gesture=gesture,
                bullet_points=self._sub_points_for(point, template),
            ))
        
        # Summary
        outro_voiceover = f"{template['outro']} Practice these concepts and you'll see improvement. "
        if difficulty == "beginner":
            outro_voiceover += "Don't rush — understanding each step fully is more important than speed."
        elif difficulty == "advanced":
            outro_voiceover += "For further study, explore the advanced resources linked in the description."
        
        scenes.append(Scene(
            text="Practice Makes Perfect",
            voiceover=outro_voiceover,
            duration=outro_dur,
            font_size=60,
            text_color="#00FF88",
            animation="fade",
            character_position="center",
            character_gesture="thumbs_up"
        ))
        
        return scenes

    def _generate_deep_dive_scenes(self, template: dict, target_minutes: int,
                                     difficulty: str = "intermediate") -> List[Scene]:
        """Generate detailed deep-dive scenes for in-depth coverage.
        
        Difficulty effects:
        - beginner: simplified deep-dive with analogies
        - intermediate: balanced technical depth (default)
        - advanced: full technical detail, architecture discussion, tradeoffs
        """
        scenes = []
        target_seconds = target_minutes * 60
        intro_dur = 6.0
        outro_dur = 8.0
        remaining = target_seconds - intro_dur - outro_dur
        
        # Intro
        intro_voiceover = f"Welcome to this deep dive on {template['title'].split(' - ')[0] if ' - ' in template['title'] else template['title']}. "
        intro_voiceover += template['intro']
        if difficulty == "beginner":
            intro_voiceover += " We'll use analogies and simple examples to make complex ideas clear."
        elif difficulty == "advanced":
            intro_voiceover += " We'll examine architecture, implementation details, and key technical tradeoffs."
        else:
            intro_voiceover += " We'll cover the essential concepts in depth, with technical details where they matter most."
        
        scenes.append(Scene(
            text=template["title"],
            voiceover=intro_voiceover,
            duration=intro_dur,
            font_size=65,
            text_color="#FFD700",
            animation="fade",
            character_position="center",
            character_gesture="serious",
            enter_from="left"
        ))
        
        # Break each point into detailed sub-explanations
        num_points = len(template["points"])
        point_dur = max(remaining / max(num_points, 1), 12.0)
        
        char_positions = ["right", "left", "center", "right", "left"]
        gestures = ["explaining", "pointing", "thinking", "analyzing", "explaining"]
        
        for i, point in enumerate(template["points"]):
            pos = char_positions[i % len(char_positions)]
            gesture = gestures[i % len(gestures)]
            
            if difficulty == "beginner":
                # Simplify for beginners while keeping depth
                voiceover = f"Let's explore {point} in detail. Think of it this way: {point.lower()}. "
                voiceover += self._expand_point_technical(point, template)[:300]
            elif difficulty == "advanced":
                voiceover = f"{point}. {self._expand_point_technical(point, template)}"
                # Add additional technical context
                voiceover += f" From an implementation perspective, there are several considerations here including performance, scalability, and maintainability."
            else:
                voiceover = f"{point}. {self._expand_point_technical(point, template)}"
            
            scenes.append(Scene(
                text=point,
                voiceover=voiceover,
                duration=point_dur,
                font_size=55 if difficulty == "advanced" else 60,
                text_color="#00BFFF" if difficulty == "advanced" else "#FFFFFF",
                animation="typewriter",
                character_position=pos,
                character_gesture=gesture,
                overlay_opacity=0.7 if difficulty == "advanced" else 0.5,
                bullet_points=self._sub_points_for(point, template),
            ))
        
        # Summary
        outro_voiceover = f"{template['outro']} "
        if difficulty == "beginner":
            outro_voiceover += "You now have a solid understanding of the key ideas. Rewatch any section that felt too fast."
        elif difficulty == "advanced":
            outro_voiceover += "For implementation, start with a prototype and iterate based on real-world testing and feedback."
        else:
            outro_voiceover += "This deep dive should give you a strong foundation. Apply these concepts in your own projects."
        
        scenes.append(Scene(
            text="Key Takeaways",
            voiceover=outro_voiceover,
            duration=outro_dur,
            font_size=60,
            text_color="#FFD700",
            animation="slide_up",
            character_position="center",
            character_gesture="summarizing"
        ))
        
        return scenes

    def save_script(self, script: VideoScript, output_path: str):
        """Save script to JSON file."""
        data = {
            "title": script.title,
            "description": script.description,
            "tags": script.tags,
            "thumbnail_text": script.thumbnail_text,
            "category": script.category,
            "estimated_duration": script.estimated_duration,
            "scenes": [
                {
                    "text": s.text,
                    "voiceover": s.voiceover,
                    "duration": s.duration,
                    "font_size": s.font_size,
                    "text_color": s.text_color,
                    "animation": s.animation,
                    "text_position": s.text_position,
                    "overlay_opacity": s.overlay_opacity
                }
                for s in script.scenes
            ]
        }
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"Script saved to: {output_path}")

    def load_script(self, input_path: str) -> VideoScript:
        """Load script from JSON file."""
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        scenes = [
            Scene(
                text=s["text"],
                voiceover=s["voiceover"],
                duration=s.get("duration", 5.0),
                font_size=s.get("font_size", 70),
                text_color=s.get("text_color", "#FFFFFF"),
                animation=s.get("animation", "fade"),
                text_position=s.get("text_position", "center"),
                overlay_opacity=s.get("overlay_opacity", 0.6)
            )
            for s in data["scenes"]
        ]
        
        return VideoScript(
            title=data["title"],
            description=data["description"],
            tags=data["tags"],
            scenes=scenes,
            thumbnail_text=data.get("thumbnail_text", ""),
            estimated_duration=data.get("estimated_duration", 0)
        )


def interactive_mode():
    """Interactive mode for script generation."""
    generator = TechTopicGenerator()
    
    print("=" * 60)
    print("   TECH VIDEO SCRIPT GENERATOR")
    print("   Daily AI, Programming & Tech Content")
    print("=" * 60)
    
    print("\n📂 Categories:")
    print("  1. AI / Artificial Intelligence")
    print("  2. GPU / Hardware")
    print("  3. Data Science")
    print("  4. Machine Learning")
    print("  5. Programming")
    print("  6. Trending / Latest")
    print("  7. All Topics")
    print("  8. Custom Topic")
    print("  9. Daily Auto Topic")
    
    cat_choice = input("\nSelect category (1-9): ").strip()
    
    category_map = {
        "1": "ai",
        "2": "gpu",
        "3": "data_science",
        "4": "machine_learning",
        "5": "programming",
    }
    
    topic_dict = None
    
    if cat_choice in category_map:
        category = category_map[cat_choice]
        topics = generator.get_available_topics(category)
        print(f"\n📋 Available Topics ({len(topics)}):")
        for i, item in enumerate(topics, 1):
            src = f" [{item.get('source', 'static')}]" if item.get('source') else ""
            print(f"  {i}. {item['title']}{src}")
        topic_choice = input("\nSelect topic (number): ").strip()
        if topic_choice.isdigit() and 0 < int(topic_choice) <= len(topics):
            chosen = topics[int(topic_choice) - 1]
            topic = chosen.get("topic", "")
            topic_dict = chosen.get("topic_dict", None)
            if not topic_dict:
                topic_dict = {
                    "title": chosen["title"],
                    "intro": chosen["intro"],
                    "points": chosen["points"],
                    "outro": chosen["outro"],
                }
        else:
            chosen = topics[0]
            topic = chosen.get("topic", "")
            topic_dict = {
                "title": chosen["title"],
                "intro": chosen["intro"],
                "points": chosen["points"],
                "outro": chosen["outro"],
            }
    elif cat_choice == "6":
        trending = generator.search_trending_topics()
        if trending:
            topic_dict = trending[0]
            topic = "trending_topic"
        else:
            topic = generator.get_daily_topic()
    elif cat_choice == "7":
        all_topics = generator.get_available_topics("all")
        print(f"\n📋 All Topics ({len(all_topics)}):")
        for i, item in enumerate(all_topics, 1):
            src = f" [{item.get('source', 'static')}]" if item.get('source') else ""
            print(f"  {i}. {item['title']}{src}")
        topic_choice = input("\nSelect topic (number): ").strip()
        if topic_choice.isdigit() and 0 < int(topic_choice) <= len(all_topics):
            chosen = all_topics[int(topic_choice) - 1]
            topic = chosen.get("topic", "")
            topic_dict = chosen.get("topic_dict", None)
            if not topic_dict:
                topic_dict = {
                    "title": chosen["title"],
                    "intro": chosen["intro"],
                    "points": chosen["points"],
                    "outro": chosen["outro"],
                }
        else:
            chosen = all_topics[0]
            topic = chosen.get("topic", "")
            topic_dict = {
                "title": chosen["title"],
                "intro": chosen["intro"],
                "points": chosen["points"],
                "outro": chosen["outro"],
            }
    elif cat_choice == "8":
        topic = input("\nEnter custom topic (use underscores): ").strip()
        topic = topic.lower().replace(" ", "_")
        topic_dict = None
    elif cat_choice == "9":
        live = generator.get_available_topics("trending")
        if live:
            chosen = live[0]
            topic_dict = chosen
            topic = "trending_topic"
            print(f"\n📅 Today's topic: {chosen.get('title', 'Trending')}")
        else:
            topic = generator.get_daily_topic()
            print(f"\n📅 Today's topic: {topic}")
    else:
        topic = "ai_basics"
    
    # Style
    print("\n🎬 Video Style:")
    print("  1. Educational (default)")
    print("  2. Listicle (Top X)")
    print("  3. Tutorial (Step by step)")
    print("  4. Deep Dive (Detailed)")
    
    style_choice = input("\nSelect style (1-4): ").strip()
    style_map = {"1": "educational", "2": "listicle", "3": "tutorial", "4": "deep_dive"}
    style = style_map.get(style_choice, "educational")
    
    # Difficulty
    print("\n👥 Target Audience:")
    print("  1. Beginner (simple explanations, analogies)")
    print("  2. Intermediate (balanced - default)")
    print("  3. Advanced (technical depth, code references)")
    
    diff_choice = input("\nSelect difficulty (1-3): ").strip()
    diff_map = {"1": "beginner", "2": "intermediate", "3": "advanced"}
    difficulty = diff_map.get(diff_choice, "intermediate")
    
    # Duration
    print("\n⏱️ Video Length:")
    print("  1. Short (~1 minute)")
    print("  2. Medium (~3 minutes)")
    print("  3. Long (~5 minutes)")
    
    dur_choice = input("\nSelect length (1-3): ").strip()
    dur_map = {"1": 1, "2": 3, "3": 5}
    duration = dur_map.get(dur_choice, 3)
    
    # Generate
    print(f"\n🔄 Generating script...")
    script = generator.generate_script(
        topic=topic,
        style=style,
        target_duration=duration,
        topic_dict=topic_dict,
        difficulty=difficulty
    )
    
    print(f"\n{'=' * 60}")
    print(f"📝 Title: {script.title}")
    print(f"🎬 Scenes: {len(script.scenes)}")
    print(f"⏱️ Est. Duration: {script.estimated_duration:.0f}s ({script.estimated_duration/60:.1f} min)")
    print(f"👥 Difficulty: {difficulty}")
    print(f"{'=' * 60}")
    
    for i, scene in enumerate(script.scenes):
        print(f"\nScene {i + 1}:")
        print(f"  Text: {scene.text[:80]}...")
        print(f"  Duration: {scene.duration:.1f}s")
    
    save = input("\n\nSave script? (y/n): ").strip().lower()
    if save == 'y':
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"output/scripts/{topic}_{timestamp}.json"
        generator.save_script(script, output_path)
    
    return script


if __name__ == "__main__":
    interactive_mode()
