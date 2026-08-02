"""
Prism — Agent Registry & Category Map
─────────────────────────────────────
The single source of truth for every AI tool Prism can drive, and how the
six selectable categories map onto the sequential automation pipeline.

A tool lives ONCE in AGENT_REGISTRY. Its category is contextual — the same
entry (e.g. "Claude", "ChatGPT", "LAZYCOOK", "Kimi 2.6") is offered under
multiple categories because it is genuinely multi-domain. The stage it runs
under is decided by the user's selection + the router, not by the registry.
"""

# ── Pipeline order ────────────────────────────────────────────────────────────
# Stages run top-to-bottom. Each stage feeds its output forward as context to
# the next. A stage only runs if (a) the user assigned an agent to it AND
# (b) the router marks it "needed" for the current query.
PIPELINE_ORDER = [
    "research",       # ground the task in facts / papers / computation
    "brains",         # strategy, reasoning, architecture
    "content",        # copy, docs, scripts
    "visual",         # images
    "media",          # video
    "audio",          # voice-over, music, narration
    "development",    # build & deploy apps, UIs, tools
    "presentation",   # slide decks & pitch presentations
    "summary",        # final synthesis (uses the 'brains' agent)
]

# ── Selectable categories (shown in onboarding & /agents) ─────────────────────
CATEGORIES = {
    "brains": {
        "label": "Orchestration & Brains",
        "emoji": "🧠",
        "color": "brains",
        "desc": "Strategy, factual grounding, deep reasoning & technical architecture",
        "agents": ["Perplexity", "ChatGPT", "Claude", "LAZYCOOK"],
    },
    "research": {
        "label": "Research & Academic",
        "emoji": "📚",
        "color": "research",
        "desc": "Peer-reviewed data, citations & symbolic mathematics",
        # Perplexity leads here: live web search with sources is the research
        # job most tasks actually need, and /boq's design-standards stage
        # picks whatever is set for this category.
        "agents": ["Perplexity", "Consensus", "WolframAlpha", "Semantic Scholar",
                   "NotebookLM", "LAZYCOOK"],
    },
    "content": {
        "label": "Content, Post & Docs",
        "emoji": "✍️",
        "color": "content",
        "desc": "Marketing copy, SEO, long-form & no-hallucination scripts",
        # Perplexity earns a place for the "no-hallucination" half of this
        # category — source-cited factual copy — not for creative marketing.
        "agents": ["Jasper", "Copy.ai", "Kimi 2.6", "Writesonic", "Claude",
                   "Perplexity", "LAZYCOOK"],
    },
    "visual": {
        "label": "Visual & Image",
        "emoji": "🎨",
        "color": "visual",
        "desc": "Professional image generation & consistent characters",
        "agents": ["Leonardo.ai", "Adobe Firefly", "Midjourney", "ChatGPT"],
    },
    "media": {
        "label": "Video & Reels",
        "emoji": "🎬",
        "color": "media",
        "desc": "Generated footage, reels from your own clips & AI avatars",
        "agents": ["Prism Studio", "Prism Reel", "Google Flow", "Claude Design",
                   "InVideo AI", "Runway", "Pika Labs", "HeyGen"],
    },
    # Split out from video on purpose: a voice-over and a reel are different
    # jobs with different tools, and a run often needs BOTH — one stage each,
    # rather than forcing a single pick between ElevenLabs and Runway.
    "audio": {
        "label": "Voice & Audio",
        "emoji": "🔊",
        "color": "audio",
        "desc": "Voice-over, narration, music & audio explainers",
        "agents": ["ElevenLabs", "Suno", "NotebookLM"],
    },
    "development": {
        "label": "Web, App & Tools",
        "emoji": "🛠️",
        "color": "development",
        "desc": "Deployed apps, generative UI & tools straight from prompts",
        "agents": ["omma.build", "emergent.sh", "v0.dev", "Claude", "Kimi 2.6"],
    },
    "presentation": {
        "label": "Presentations & Decks",
        "emoji": "📊",
        "color": "presentation",
        "desc": "Slide decks, pitch presentations & narrative sites from a prompt",
        "agents": ["Gamma.app", "Tome", "Claude", "Claude Design"],
    },
}

# The "summary" stage is not user-selectable; it reuses whichever agent the
# user picked for "brains" (falling back to content / research).
SUMMARY_FALLBACK_ORDER = ["brains", "content", "research"]

# ── Sensible generic web-UI selectors for tools we don't hand-tune ────────────
_GENERIC = {
    "textarea_selector": "textarea, div[contenteditable='true'][role='textbox'], div[contenteditable='true']",
    "response_selector": "[data-message-author-role='assistant'], .response, .message, .prose, .markdown",
    "submit_selector": "button[type='submit'], button[aria-label*='Send'], button[data-testid='send-button']",
}


# How generous the caps are. wait_time is a CEILING, not a sleep: _smart_wait
# returns the moment the answer settles, so a bigger number costs a fast tool
# nothing and only buys headroom for a slow one. The old floor of 120s was
# routinely hit by reasoning models and by any tool that renders a document,
# and hitting it means the run walks away from an answer that was seconds out.
WAIT_FLOOR = 300          # 5 min for even the quickest tool
WAIT_MULTIPLIER = 2.0     # slow tools scale from their registered estimate
WAIT_CEILING = 1800       # 30 min — past this something is genuinely stuck


def _agent(url, specialty, cost, avg, wait, **overrides):
    base = {
        "url": url,
        "specialty": specialty,
        "cost": cost,
        "avg": avg,
        "wait_time": int(min(max(wait * WAIT_MULTIPLIER, WAIT_FLOOR), WAIT_CEILING)),
        **_GENERIC,
    }
    base.update(overrides)
    return base


# ── The full registry (25 tools) ──────────────────────────────────────────────
AGENT_REGISTRY = {
    # ── Orchestration / Brains ────────────────────────────────────────────────
    "Perplexity": _agent(
        "https://www.perplexity.ai",
        "real-time factual grounding, source verification, citations, current events",
        "Freemium", "5–15s", 120,
        textarea_selector="div[contenteditable='true']#ask-input, textarea",
        response_selector=".prose, .break-words",
        submit_selector="button[aria-label='Submit']",
    ),
    "ChatGPT": _agent(
        "https://chatgpt.com",
        "general intelligence, multimodal reasoning, brainstorming & DALL·E 3 visuals",
        "Freemium", "5–20s", 120,
        textarea_selector="#prompt-textarea",
        response_selector="[data-message-author-role='assistant']",
        submit_selector="button[data-testid='send-button']",
    ),
    "Claude": _agent(
        "https://claude.ai",
        "advanced coding, complex documentation, UI artifacts & long-form reasoning",
        "Freemium", "10–30s", 300,
        textarea_selector="div[contenteditable='true']",
        response_selector=".font-claude-message, .prose, [data-is-streaming='false']",
        submit_selector="button[aria-label='Send Message']",
    ),
    "LAZYCOOK": _agent(
        "https://thelazycook.in",
        "4-stage automation (Generate → Analyze → Optimize → Validate); no-hallucination web search & scripting",
        "Free/Low", "20–45s", 400,
    ),

    # ── Research & Academic ───────────────────────────────────────────────────
    "Consensus": _agent(
        "https://consensus.app/search",
        "evidence-based answers extracted from 200M+ peer-reviewed research papers",
        "Freemium", "10–20s", 45,
    ),
    "WolframAlpha": _agent(
        "https://www.wolframalpha.com",
        "computational knowledge for physics, chemistry, and hard mathematics",
        "Free/Paid", "2–5s", 25,
        textarea_selector="input[type='text'], textarea",
        response_selector=".output, section, img",
        submit_selector="button[type='submit'], input[type='submit']",
    ),
    "Semantic Scholar": _agent(
        "https://www.semanticscholar.org",
        "AI-driven literature mapping and academic discovery",
        "Free", "5–15s", 35,
        textarea_selector="input[type='search'], input[name='q'], textarea",
    ),
    "NotebookLM": _agent(
        "https://notebooklm.google.com",
        "grounding AI in your own uploaded documents for faithful synthesis; "
        "handles LARGE volumes of source material (many long docs/videos/notes "
        "at once) and turns them into explainer output via its built-in Video "
        "Overview and Audio Overview (podcast-style) generators — the best fit "
        "for 'explain everything we have' style requests, not just Q&A",
        "Free", "10–20s", 45,
    ),

    # ── Content, Post & Documentation ─────────────────────────────────────────
    "Jasper": _agent(
        "https://app.jasper.ai",
        "enterprise-grade marketing copy and consistent brand voice",
        "Paid/Trial", "5–15s", 35,
    ),
    "Copy.ai": _agent(
        "https://app.copy.ai",
        "high-conversion sales copy and go-to-market assets",
        "Freemium", "5–10s", 30,
    ),
    "Kimi 2.6": _agent(
        "https://kimi.moonshot.cn",
        "massive context window for analysing 100+ page documents; multilingual",
        "Free/Low", "15–40s", 500,
        textarea_selector="div[contenteditable='true'], textarea",
        response_selector=".chat-message, .markdown",
    ),
    "Writesonic": _agent(
        "https://app.writesonic.com",
        "SEO-optimised long-form articles and landing-page copy",
        "Freemium", "10–20s", 45,
    ),

    # ── Visual & Image ────────────────────────────────────────────────────────
    "Leonardo.ai": _agent(
        "https://app.leonardo.ai",
        "fine-tuned model control and consistent character generation",
        "Freemium", "15–30s", 70,
    ),
    "Adobe Firefly": _agent(
        "https://firefly.adobe.com",
        "commercial-safe imagery with advanced generative fill",
        "Freemium", "10–20s", 50,
    ),
    "Midjourney": _agent(
        "https://www.midjourney.com/imagine",
        "the gold standard for cinematic photorealism (web alpha)",
        "Paid", "30–60s", 100,
    ),

    # ── Video & Audio ─────────────────────────────────────────────────────────
    "Runway": _agent(
        "https://app.runwayml.com",
        "high-end cinematic video generation and motion control (Gen-3)",
        "Freemium", "1–3 min", 240,
    ),
    "InVideo AI": _agent(
        "https://ai.invideo.io",
        "prompt-driven assembly of REAL uploaded footage into finished promo "
        "reels — branded intro/outro, captions, music, animated overlays, "
        "platform-specific cuts (Instagram/LinkedIn/pitch); unlike Runway it "
        "edits your actual clips together instead of generating new ones",
        "Freemium", "2–5 min", 300,
    ),
    "Pika Labs": _agent(
        "https://pika.art",
        "stylised animation and precise regional video editing",
        "Freemium", "1–2 min", 180,
    ),
    "HeyGen": _agent(
        "https://app.heygen.com",
        "AI avatars for professional pitch videos and presentations",
        "Freemium", "5–15 min", 360,
    ),
    "ElevenLabs": _agent(
        "https://elevenlabs.io/app/speech-synthesis",
        "industry-leading emotive voice cloning and text-to-speech",
        "Freemium", "5–15s", 45,
    ),
    # Runs INSIDE Prism — no browser, no account, no upload. Marked local so
    # automation.py renders it here instead of driving a Chrome tab.
    "Prism Studio": _agent(
        "local://reel_web",
        "the reel is DESIGNED, not templated: one pass writes the script, a "
        "second art-directs it — its own background, typography, palette and "
        "motion as real CSS — and Prism films the page frame by frame. Two "
        "clients never get the same-looking film. No watermark, no credits",
        "Included", "40–90s", 60,
        local="reel_web",
    ),
    "Prism Reel": _agent(
        "local://reel",
        "the fixed house style, drawn in code: always 9:16, text always "
        "legible, brand colours measured from the client's artwork. Faster "
        "and never surprising, but every reel shares one look",
        "Included", "15–30s", 60,
        local="reel",
    ),
    "Google Flow": _agent(
        "https://labs.google/fx/tools/flow",
        "Veo-powered video on a free daily credit allowance — the cheapest way "
        "to make short reels. CANNOT render readable on-screen text, so any "
        "caption, price or phone number must be added afterwards, never asked "
        "for in the prompt",
        "Free tier", "1–3 min", 300,
    ),
    "Suno": _agent(
        "https://suno.com/create",
        "full-scale music and jingle generation for content",
        "Freemium", "30–60s", 100,
    ),

    # ── Web, App & Presentation Development ────────────────────────────────────
    "omma.build": _agent(
        "https://omma.build",
        "rapid full-stack application generation from prompts",
        "Freemium", "30–90s", 600,
        response_selector=".output, .code, .message",
    ),
    "emergent.sh": _agent(
        "https://emergent.sh",
        "agentic deployment of code into live environments",
        "Freemium", "1–3 min", 1200,
        response_selector=".message, .response",
    ),
    "v0.dev": _agent(
        "https://v0.dev",
        "generative UI components using Tailwind and shadcn/ui",
        "Freemium", "10–30s", 120,
    ),
    "Gamma.app": _agent(
        "https://gamma.app/create/generate",
        "one-click transformation of text into professional decks / sites",
        "Freemium", "20–40s", 400,
        # Gamma is a slow-loading SPA — give it extra time before hunting
        # for the prompt box, and accept plain <input> fields too.
        page_wait=12,
        input_wait=40,
        textarea_selector="textarea, div[contenteditable='true'], input[type='text']",
    ),
    "Tome": _agent(
        "https://tome.app",
        "AI-driven storytelling for pitch decks and narratives",
        "Freemium", "15–30s", 70,
    ),
    "Claude Design": _agent(
        "https://claude.ai/design",
        "Claude's design surface — slide decks, video edit designs & polished visual assets",
        "Freemium", "1–3 min", 300,
        # Same claude.ai frontend as the chat — reuse its hand-tuned selectors,
        # with extra load time since the design surface renders a canvas.
        page_wait=8,
        textarea_selector="div[contenteditable='true']",
        response_selector=".font-claude-message, .prose, [data-is-streaming='false']",
        submit_selector="button[aria-label='Send Message']",
    ),
}


def resolve_agent(stage: str, name: str) -> dict | None:
    """Return the registry entry for an agent (name is category-independent)."""
    if not name:
        return None
    return AGENT_REGISTRY.get(name)


def summary_agent_name(agents: dict) -> str | None:
    """Which agent should run the final 'summary' stage, given user selections."""
    for cat in SUMMARY_FALLBACK_ORDER:
        if agents.get(cat):
            return agents[cat]
    return None


def specialty_for(stage: str, name: str) -> str:
    cfg = resolve_agent(stage, name)
    return cfg["specialty"] if cfg else "general-purpose AI"
