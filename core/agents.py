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
    # Finding prospects is not research and must not have to fight it for the
    # same slot — exactly the reason audio was split out of media below. An
    # outreach run usually needs BOTH: research to work out which industries
    # to go after, THEN leads to pull the actual companies in them. Sharing
    # one stage forced a choice between a tool that reads the web and a tool
    # that reads a contact database, which are not substitutes.
    #
    # It sits AFTER research on purpose. Research narrows the segment; the
    # lead database then filters inside it. The other way round you are
    # picking companies before you know what you are looking for.
    "leads",          # real companies & verified contacts to approach
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
    # Its own category, not a corner of research. A contact database and a
    # web-search model are different machines: one returns rows that were
    # verified, the other returns prose it composed. Making the user pick one
    # for both jobs meant an outreach run could have facts or prospects, never
    # both.
    "leads": {
        "label": "Leads & Prospecting",
        "emoji": "🎯",
        "color": "leads",
        "desc": "Real companies and named contacts with verified emails, to approach",
        # Apollo first and by a distance — it is the only one here that
        # returns checked contact records. The other two are the honest
        # fallback for someone with no Apollo account: they read the public
        # web, which finds fewer addresses and cannot confirm them.
        "agents": ["Apollo", "Perplexity", "LAZYCOOK", "ChatGPT"],
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
        "desc": "Professional image generation, editable posts & consistent characters",
        # Canva is first because most business visual work is a post or a
        # brochure the customer will want to tweak later, and it is the only
        # tool here that hands back something still editable.
        "agents": ["Canva", "Leonardo.ai", "Adobe Firefly", "Midjourney",
                   "ChatGPT"],
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
        "agents": ["Gamma.app", "Canva", "Tome", "Claude", "Claude Design"],
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


# Appended verbatim to ChatGPT's visual/presentation prompts. Two jobs: make
# it actually reach for the connected Canva app, and make it hand back the
# share LINK — an editable design nobody can open is worth less than a flat
# image. The "if you cannot" clause matters: without it, a ChatGPT with no
# Canva app connected spends its reply explaining that it can't, instead of
# producing the picture that was asked for.
_CANVA_SUFFIX = (
    "DELIVERY FORMAT — read this before you start. If the Canva app is "
    "connected to this ChatGPT account, use it: build this as a real, "
    "editable Canva design in my account rather than a flat exported image, "
    "at the right size for the format asked for above. Then finish your reply "
    "with the Canva design URL on its own line, prefixed exactly with "
    "'CANVA LINK: '. The link is the deliverable — without it I cannot open "
    "or edit what you made. If the Canva app is NOT connected, do not explain "
    "that and do not ask me to connect it: just generate the image normally "
    "and say 'CANVA LINK: none' at the end."
)

# The other half of the switch, and the reason it has to be said out loud.
#
# Routing an image through the Canva app costs picture quality. Canva composes
# a template — stock layouts, its own type, its own arrangement — where DALL·E
# renders the scene that was actually described. For "da Vinci sipping Wagh
# Bakri chai" that is the whole job, and a template cannot do it. The Canva
# route earns its keep only when the client has to EDIT the result afterwards.
#
# Left to itself, a ChatGPT with the Canva app connected reaches for it far
# too readily — so when the user has not asked for an editable design, saying
# nothing is not enough. It has to be told not to.
_NO_CANVA_SUFFIX = (
    "DELIVERY FORMAT — generate the image yourself, directly, at the highest "
    "quality you can. Do NOT route this through the Canva app or any other "
    "design tool, even if one is connected to this account: a template-built "
    "layout is not what is being asked for here. Return the generated image "
    "in your reply."
)

# What counts as "I want this in Canva". Matched against the user's own words,
# never against the router's rewrite of them — the point is that the CUSTOMER
# asked, and the router paraphrasing a brief must not be able to opt them in.
CANVA_TRIGGERS = (
    "canva", "editable", "editable design", "edit it later", "edit later",
    "so i can edit", "so we can edit", "so they can edit", "client can edit",
    "template", "edit afterwards",
)


def wants_canva(text: str) -> bool:
    """Did the user actually ask for an editable design?

    Deliberately a plain substring check on the user's own task text. The
    alternative — letting the model decide — is what produced the complaint
    this exists to fix: every post came back as a Canva template because
    ChatGPT will always take that route if it is offered one.
    """
    lowered = (text or "").lower()
    return any(trigger in lowered for trigger in CANVA_TRIGGERS)


# Handed to the stage BEFORE Apollo, in place of the usual prose handoff.
#
# Apollo is not a chat box. It is a filter screen backed by an API that
# rejects any single field longer than 200 characters — feeding it the normal
# pipeline brief produced exactly that:
#     Value too long: 'Context from the previous pipeline stage (RESEA…'
#
# So the previous stage is told, literally, that its reader is a database and
# what shape the answer has to be. Fixed field names, one per line, because
# _run_apollo() parses them back out and turns them into Apollo's own search
# URL — free prose here means no filters get set at all.
_APOLLO_HANDOFF = (
    "\n\nSTRICT PIPELINE RULES — YOUR READER IS A DATABASE, NOT A CHATBOT:\n"
    "1. Perform ONLY the task above. Do not produce anything else.\n"
    "2. Your answer is passed to Apollo.io, a B2B contact database. Apollo "
    "does not read paragraphs — it takes search FILTERS, and it rejects any "
    "single value longer than 200 characters. Prose is discarded.\n"
    "3. Do NOT try to name individual companies or people. Finding those is "
    "Apollo's job, and a guessed company name returns nothing. Your job is "
    "to decide WHAT KIND of company and WHICH job titles to search for.\n"
    "4. End your answer with EXACTLY this block, as the last thing you write "
    "— one field per line, plain comma-separated values, no bullets, no "
    "bold, no commentary, nothing after it:\n\n"
    "HANDOFF FOR APOLLO\n"
    "TITLES: 2-6 job titles of the person worth reaching, e.g. Founder, CEO, "
    "Managing Director, Head of Operations\n"
    "INDUSTRIES: 2-6 industry keywords describing the company, e.g. "
    "manufacturing, industrial automation\n"
    "LOCATIONS: cities, states or countries, e.g. Gujarat, Maharashtra, India\n"
    "HEADCOUNT: one or more of 1-10, 11-20, 21-50, 51-100, 101-200, 201-500, "
    "501-1000, 1001-2000, 2001-5000, 5001-10000, 10001+\n"
    "KEYWORDS: up to 12 plain words further describing the company\n\n"
    "5. Every one of those lines must be under 150 characters. Write 'any' "
    "for a field you genuinely cannot narrow — never leave one out.\n"
    "6. Company SIZE words like 'small cap' and 'mid cap' describe listed "
    "shares and mean nothing to Apollo. Translate them into the HEADCOUNT "
    "ranges above."
)


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


# ── The full registry ─────────────────────────────────────────────────────────
# No count in this heading on purpose: it went stale every time a tool landed.
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
        "general intelligence, multimodal reasoning, brainstorming & DALL·E 3 "
        "visuals — a rendered, illustrated image of whatever scene is "
        "described, which is what most posts and creatives need. It can also "
        "build an EDITABLE Canva design instead, but ONLY mention that when "
        "the user's own words ask to edit or reuse the file afterwards; "
        "routing an ordinary image request through Canva returns a stock "
        "template and loses the picture they asked for",
        "Freemium", "5–20s", 120,
        textarea_selector="#prompt-textarea",
        response_selector="[data-message-author-role='assistant']",
        submit_selector="button[data-testid='send-button']",
        # A switch, not an instruction. Which way it falls is decided by the
        # user's own wording at run time — see wants_canva() and the resolver
        # in automation.py.
        #
        # It was unconditional at first, and that was wrong: every post came
        # back as a flat Canva template, including the ones that needed a
        # rendered illustration. Both branches are spelled out because
        # silence is not neutral here — a ChatGPT with the Canva app
        # connected will reach for it unless told otherwise.
        stage_suffix={
            "visual": {"when": CANVA_TRIGGERS,
                       "asked": _CANVA_SUFFIX, "otherwise": _NO_CANVA_SUFFIX},
            "presentation": {"when": CANVA_TRIGGERS,
                             "asked": _CANVA_SUFFIX,
                             "otherwise": _NO_CANVA_SUFFIX},
        },
    ),
    "Claude": _agent(
        "https://claude.ai",
        "advanced coding, complex documentation, UI artifacts & long-form reasoning",
        "Freemium", "10–30s", 300,
        textarea_selector="div[contenteditable='true']",
        response_selector=".font-claude-message, .prose, [data-is-streaming='false']",
        submit_selector="button[aria-label='Send Message']",
    ),
    # LAZYCOOK runs its own Generate → Analyze → Optimize → Validate loop and
    # does its own web scraping. That loop is the entire reason to route work
    # here rather than to Perplexity — and Prism's standard pipeline rules
    # switch it off. "Perform ONLY the task above, nothing more" reads to it as
    # "skip the analyse and optimise passes", so it answers in one shot and
    # comes back weaker than the tool it was chosen over.
    #
    # Asked the way a person would ask, it goes and does the work. Hence
    # prompt_style — see _resolve_handoff() in automation.py.
    "LAZYCOOK": _agent(
        "https://thelazycook.in",
        "4-stage automation (Generate → Analyze → Optimize → Validate) with "
        "its own live web scraping and no-hallucination checking. Give it a "
        "SUBJECT and let it work — it researches, cross-checks and refines by "
        "itself, and over-specifying the steps makes it worse, not better",
        "Free/Low", "20–45s", 400,
        prompt_style="natural",
    ),

    # ── Research & Academic ───────────────────────────────────────────────────
    # Apollo is a filter-and-table app, not a chat box: there is no assistant
    # bubble to scrape, so the response selector targets the results grid and
    # the stage after it is expected to turn rows into a recipients CSV.
    #
    # It is driven by URL rather than by typing, which is what `search_tool`
    # switches on (see automation._run_apollo). Apollo encodes every filter
    # into its own address bar, so setting them there sets exactly the same
    # state as clicking through the left rail — and needs no selector for the
    # filter widgets, which are the part of the page most likely to churn.
    # The selectors below are still used, but only for the results grid and
    # for the typed-search fallback when the previous stage gave us nothing
    # parseable.
    "Apollo": _agent(
        "https://app.apollo.io/#/people",
        "B2B lead database — real companies and named decision-makers with "
        "VERIFIED work email addresses, filtered by industry, job title, "
        "headcount, technology and location. Use this for any 'find "
        "prospects / leads / companies to email' task: it returns contact "
        "data that was checked, instead of a language model's best guess at "
        "an address. Needs the user's own logged-in Apollo account",
        "Freemium", "20–40s", 300,
        page_wait=14,
        input_wait=45,
        search_tool="apollo",
        # The whole point of the handoff spec: keep every value Apollo is
        # given comfortably inside the limit its API enforces.
        max_query_chars=180,
        handoff_spec=_APOLLO_HANDOFF,
        textarea_selector=("input[placeholder*='Search'], input[type='search'], "
                           "input[type='text'], textarea"),
        response_selector=("[data-cy='people-table'], [role='table'], table, "
                           "[data-cy-loaded='true'], .zp_tFLCQ"),
        submit_selector=("button[type='submit'], button[aria-label*='Search'], "
                         "button[aria-label*='Apply']"),
    ),
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
    # Sits in BOTH visual and presentation: the same Magic Studio prompt makes
    # a post or a deck, and the reason to pick it is the same either way.
    #
    # What makes Canva different from every other tool in this registry: the
    # result is not something Prism scrapes out and hands over, it is a real
    # editable design left in the customer's own Canva account. So the LINK is
    # the deliverable, not the text — a run that captures no prose here has
    # still succeeded, and CompletionDialog already treats a URL-only step
    # that way.
    "Canva": _agent(
        "https://www.canva.com/magic-design/",
        "social posts, brochures, decks and brand assets that stay EDITABLE — "
        "the design lands in the customer's own Canva account as a real file, "
        "so a price, caption, photo or logo can be changed afterwards without "
        "re-running anything. Choose this over a flat image generator whenever "
        "the client will want to tweak the result themselves, or needs it in "
        "their own brand kit. Output is the Canva design link",
        "Freemium", "30–60s", 400,
        # A heavy SPA behind a login: the prompt box mounts well after load and
        # the design opens on a new editor route. Same treatment as Gamma, with
        # more headroom.
        page_wait=14,
        input_wait=45,
        textarea_selector=("textarea, div[contenteditable='true'], "
                           "input[type='text'], input[type='search']"),
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


def alternatives_for(stage: str, tried: list | tuple = (),
                     cfg: dict | None = None, limit: int = 2) -> list[str]:
    """Other tools that could do this stage, best first.

    Used when a tool cannot finish — most often a free tier running out
    part-way through a long run. The alternative has to be able to do the same
    JOB, so candidates come from the stage's own category rather than from
    whatever else the user happens to have configured: swapping a failed image
    stage onto a research tool would produce an answer, and the answer would be
    an essay about pictures.

    Ordering, and the reasoning for it:

      1. **Tools the user already picked somewhere.** They chose them, which
         almost always means they are signed in — and a signed-out alternative
         fails exactly as fast as the tool it is replacing.
      2. **The rest of the category, registry order.** That order is roughly
         "most capable first" and is a reasonable second guess.

    Local agents are excluded. They are Prism's own renderers, not web tools,
    and a stage that failed in a browser is not fixed by handing it to one.
    """
    seen = {t for t in tried if t}
    picked = set((cfg or {}).get("agents", {}).values())
    catalogue = CATEGORIES.get(stage, {}).get("agents", [])

    preferred, rest = [], []
    for name in catalogue:
        if name in seen:
            continue
        entry = AGENT_REGISTRY.get(name)
        if not entry or entry.get("local"):
            continue
        (preferred if name in picked else rest).append(name)
    return (preferred + rest)[:limit]


def summary_agent_name(agents: dict) -> str | None:
    """Which agent should run the final 'summary' stage, given user selections."""
    for cat in SUMMARY_FALLBACK_ORDER:
        if agents.get(cat):
            return agents[cat]
    return None


def specialty_for(stage: str, name: str) -> str:
    cfg = resolve_agent(stage, name)
    return cfg["specialty"] if cfg else "general-purpose AI"
