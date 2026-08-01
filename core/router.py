"""
Prism — the routing brain (Groq)
────────────────────────────────
Takes the user's raw query + their profile + the agents they enabled, and asks
Groq to split the task into a self-contained prompt (or several) for each
pipeline stage — marking stages "needed": false when they don't apply.

Generalised from the original 4-stage version to the full six categories, and
tailored by the user's "what do you do" profile.
"""
from __future__ import annotations
import json
import re
import requests

from . import agents as A
from . import ui

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Human field notes about the tools — written by the user from real experience
# (pros, cons, "use this one for X, avoid for Y"). If this file exists, its
# contents are injected into every routing prompt so Groq routes with the
# user's judgement, not just the generic specialty strings.
_NOTES_MAX_CHARS = 14000   # fits tool_notes.md + pros_cons.txt merged, with headroom to grow


def _tool_notes() -> str:
    """Merge EVERY notes file found (~/.prism/ takes precedence, then the app
    folder) — first-match-wins silently shadowed pros_cons.txt whenever
    tool_notes.md existed, dropping half the user's guidance."""
    from . import config as C
    import os
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parts = []
    for folder in (C.CONFIG_DIR, app_dir):
        for fname in ("tool_notes.md", "tool_notes.txt", "pros_cons.txt"):
            path = os.path.join(folder, fname)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read().strip()
                if text:
                    parts.append(text)
            except Exception:
                continue
    return "\n\n".join(parts)[:_NOTES_MAX_CHARS]

# ── Deterministic make-stage guardrail ────────────────────────────────────────
# If the query clearly asks to BUILD an artefact, we force the matching make-stage
# on even when the model skipped it — so "design a PPT" can never come back as a
# plan-only answer. Only stages the user actually configured an agent for.
_BUILD_VERBS = [
    "make", "build", "create", "design", "generate", "produce", "develop",
    "compose", "draw", "render", "write", "code", "craft", "prepare",
    "put together", "come up with", "whip up", "mock up", "prototype",
]

_ARTEFACT_STAGES = {
    "presentation": ["ppt", "powerpoint", "power point", "slide", "slides", "deck",
                     "slidedeck", "presentation", "keynote", "pitch deck"],
    "visual": ["logo", "image", "illustration", "artwork", "icon", "poster",
               "banner", "graphic", "picture", "photo", "drawing", "wallpaper",
               "thumbnail", "sticker", "mockup"],
    "media": ["video", "animation", "animate", "voiceover", "voice over",
              "voice-over", "narration", "music", "song", "jingle", "soundtrack",
              "audio", "podcast", "tts", "avatar"],
    # NOTE: no bare "prototype" here — it's usually the SUBJECT of a task
    # ("pitch the prototype"), not a request to build software.
    "development": ["web app", "webapp", "website", "web site", "web page",
                    "webpage", "landing page", "mobile app", "app", "ui component",
                    "dashboard", "frontend", "front-end", "backend", "back-end",
                    "api", "web tool", "saas", "platform"],
    "content": ["article", "essay", "blog post", "blog", "whitepaper",
                "white paper", "newsletter", "ebook", "e-book", "screenplay",
                "script", "story", "novel", "documentation", "manuscript",
                "specification", "spec", "technical spec", "requirements document",
                "prd", "srs", "design document", "design doc"],
}

_MAKE_LABEL = {
    "presentation": "slide deck / presentation",
    "visual": "image",
    "media": "video / audio asset",
    "development": "web app / tool",
    "content": "written piece",
}


def _mentions(text_lc: str, terms: list[str]) -> bool:
    for t in terms:
        if " " in t or "-" in t:
            if t in text_lc:
                return True
        elif re.search(r"\b" + re.escape(t) + r"\b", text_lc):
            return True
    return False


def apply_make_guardrail(query: str, routing: dict, agents: dict) -> list[str]:
    """Force make-stages the user clearly asked for. Returns the stages forced on.
    Mutates `routing` in place."""
    q = query.lower()
    if not _mentions(q, _BUILD_VERBS):
        return []
    forced = []
    for stage, terms in _ARTEFACT_STAGES.items():
        if not agents.get(stage):
            continue                      # user has no tool for this stage
        if not _mentions(q, terms):
            continue
        data = routing.get(stage) or {}
        if data.get("needed") and data.get("questions"):
            continue                      # already on — nothing to force
        label = _MAKE_LABEL.get(stage, stage)
        qs = data.get("questions") or [
            f"Create the requested {label} for this task, using any earlier "
            f"pipeline output as your brief. Original request: {query.strip()}"
        ]
        routing[stage] = {"needed": True, "questions": qs}
        forced.append(stage)

    # If we forced a make-stage and the user has a brains agent that isn't running,
    # turn on brains too so the deck/app/etc. is planned before it's built.
    if forced and agents.get("brains"):
        b = routing.get("brains") or {}
        if not (b.get("needed") and b.get("questions")):
            routing["brains"] = {"needed": True, "questions": [
                f"Plan and outline the following before it gets built: {query.strip()}"
            ]}
            forced.insert(0, "brains")
    return forced


def apply_script_guardrail(routing: dict, agents: dict) -> bool:
    """A reel/video/deck needs WORDS — script, narration, captions, slide copy.
    That's the CONTENT agent's job. If MEDIA or PRESENTATION is about to
    produce the deliverable and the user configured a CONTENT agent that the
    model skipped, force CONTENT on between the plan and the make-stage.
    Mutates `routing`; returns True if content was forced."""
    if not agents.get("content"):
        return False
    making = any((routing.get(s) or {}).get("needed") and (routing.get(s) or {}).get("questions")
                 for s in ("media", "presentation"))
    c = routing.get("content") or {}
    if not making or (c.get("needed") and c.get("questions")):
        return False
    routing["content"] = {"needed": True, "questions": [
        "Your ONLY task is: using the plan from the previous stage, write the "
        "COMPLETE script for the deliverable — narration / voiceover lines, "
        "on-screen text, captions, scene-by-scene wording, and every exact word "
        "that will appear or be spoken. Do NOT produce the video, reel or deck "
        "itself — output the words only; a later stage builds it."
    ]}
    return True

# One-line description of what each stage is FOR, injected into the prompt only
# for the stages the user actually enabled.
_STAGE_HELP = {
    "research": "HEAVY tasks only — genuinely NEW external facts/citations/papers/prices the model "
                "wouldn't already know, e.g. a complex build needing current docs or real market data. "
                "NOT for analysing given material and NOT for simple asks."
                "For research purpose where you'll need the factual evidences on recent events or when webscraping will be needed along with writing something about that information in depth",
    "brains": "the DEFAULT workhorse — analysis, reasoning, strategy, architecture, planning, AND short"
              "written outputs like briefs, plans, explanations or prompts for the next stage. Small tasks "
              "usually need ONLY this stage.",
    "content": "ONLY when the deliverable is a SUBSTANTIAL written piece (full article, essay, long-form "
               "copy, script, documentation). Short text, answers and briefs belong to brains, not here.",
    "visual": "generating images, art, character designs, logos, illustrations.",
    "media": "generating VIDEO only — footage, animation, reels, AI avatars. "
             "Voice-over and music are a SEPARATE stage (audio), so a reel that "
             "needs narration should turn BOTH on, not one.",
    "audio": "generating AUDIO only — voice-over, narration, dubbing, music or an "
             "audio explainer. Turn this on alongside media when a video needs a "
             "voice, or alone when the deliverable is sound.",
    "development": "building/deploying a web app, website, UI, or software tool from a spec.",
    "presentation": "building an actual slide deck / PowerPoint / pitch presentation or narrative site.",
    "summary": "synthesising ALL earlier stage outputs into one clean final answer.",
}


def _stage_lines(agents: dict, premium: list | None = None) -> str:
    premium = premium or []
    lines = []
    for stage in A.PIPELINE_ORDER:
        if stage == "summary":
            name = A.summary_agent_name(agents)
            if not name:
                continue
        else:
            name = agents.get(stage)
            if not name:
                continue
        spec = A.specialty_for(stage, name)
        star = "  ⭐ PREMIUM (the user pays for this tool)" if name in premium else ""
        lines.append(f"- {stage.upper()} → {name}: {spec}{star}\n    USE FOR: {_STAGE_HELP.get(stage, "")}")
    return "\n".join(lines)


def _schema_stub(agents: dict) -> str:
    parts = []
    for stage in A.PIPELINE_ORDER:
        if stage == "summary":
            if not A.summary_agent_name(agents):
                continue
        elif not agents.get(stage):
            continue
        parts.append(f'  "{stage}": {{ "questions": ["..."], "needed": false }}')
    return "{\n" + ",\n".join(parts) + "\n}"


def enrich_query(query: str, profile: str, api_key: str, model: str) -> str:
    """Pre-pass: expand the user's raw request into a professional task brief.
    This is what separates a human's one-liner from an engineered prompt — the
    router then writes every stage prompt FROM this brief. Returns "" on any
    failure so routing still works without it."""
    profile_line = f'The user describes themselves as: "{profile}".\n' if profile else ""
    prompt = f"""You are a senior prompt engineer. Expand the raw request below into a crisp
professional TASK BRIEF that a downstream AI pipeline will use to write prompts.
Do NOT answer or perform the task itself.

{profile_line}Cover, in at most 220 words, as plain bullet lines:
- GOAL: the outcome the user actually wants (read intent, not just words)
- DELIVERABLE & FORMAT: exact artefact(s) and the structure/sections expected
- AUDIENCE & TONE
- SCOPE: explicitly IN and explicitly OUT (respect words like "only" / "don't")
- CONSTRAINTS & GIVENS: tech, languages, budget, sources, attached material
- QUALITY BAR: 2-3 measurable criteria a professional result must meet
- IMPLICIT NEEDS: things the user didn't say but a professional would include

Raw request:
{query}

Return ONLY the brief as plain text bullets — no preamble, no commentary."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=45)
    rj = resp.json()
    if "choices" not in rj:
        return ""
    return rj["choices"][0]["message"]["content"].strip()


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def detect_named_tools(query: str) -> dict:
    """If the user explicitly NAMES one of Prism's own tools in their query
    ("using NotebookLM", "notebook lm video generation", "via Claude Design")
    that's a direct order, not a maybe — the suggestion mechanism's LLM
    judgement call shouldn't be the only path to honouring it. Fuzzy-matched
    (spacing/case-insensitive) so "notebook lm" still matches "NotebookLM".
    Returns {stage: tool_name} for every category that tool belongs to.
    Short/common names (<4 normalized chars) are skipped to avoid false hits."""
    q_norm = _norm_name(query)
    out = {}
    for name in A.AGENT_REGISTRY:
        n_norm = _norm_name(name)
        if len(n_norm) < 4 or n_norm not in q_norm:
            continue
        for stage, cat in A.CATEGORIES.items():
            if name in cat["agents"]:
                out.setdefault(stage, name)
    return out


def suggest_alternatives(query: str, brief: str, routing: dict, agents: dict,
                         api_key: str, model: str) -> list[dict]:
    """For each stage the router marked needed, check whether one of the
    OTHER tools listed under that stage's category (ones the user did NOT
    default to) would clearly suit THIS specific task better — e.g. NotebookLM
    over Perplexity when the task is really about grounding in the user's own
    uploaded documents. Deliberately conservative: returns [] unless there's a
    strong, concrete reason, so most runs get no suggestion at all. Fails
    soft — any error just means no suggestions this time."""
    candidates = []
    for stage in A.PIPELINE_ORDER:
        data = routing.get(stage)
        if not data or not data.get("needed"):
            continue
        current = A.summary_agent_name(agents) if stage == "summary" else agents.get(stage)
        if not current or current not in A.AGENT_REGISTRY:
            continue
        cat = A.CATEGORIES.get(stage)
        if not cat:
            continue
        alts = [n for n in cat["agents"] if n != current and n in A.AGENT_REGISTRY]
        if not alts:
            continue
        alt_lines = "\n".join(f"  - {n}: {A.AGENT_REGISTRY[n]['specialty']}" for n in alts)
        candidates.append(
            f"STAGE: {stage}\nCurrently assigned: {current} "
            f"({A.AGENT_REGISTRY[current]['specialty']})\n"
            f"Other tools available for this stage:\n{alt_lines}"
        )
    if not candidates:
        return []

    notes = _tool_notes()
    notes_block = (
        f"\nThe user's OWN field notes on these tools, written from real hands-on\n"
        f"experience — these are FACTS about the tools, not generic marketing text.\n"
        f"Weigh them more heavily than the one-line specialty strings above, and\n"
        f"apply any routing rule in them that matches this task:\n{notes}\n"
        if notes else ""
    )

    prompt = f"""You are auditing an AI-tool routing plan before it runs.
TASK BRIEF:
{brief or query}
{notes_block}
For EACH stage below, decide if one of the "other tools available" would
CLEARLY perform this SPECIFIC task better than the currently assigned tool —
not marginally, only for a strong, concrete reason (e.g. the task is really
about grounding in the user's OWN uploaded documents, which fits NotebookLM
far better than a general web-search tool). If the current pick is perfectly
fine, suggest nothing for that stage — most stages should get NO suggestion.

{chr(10).join(candidates)}

Return ONLY a JSON array (empty if nothing stands out):
[{{"stage": "...", "current": "...", "suggested": "...", "reason": "one sentence"}}]"""
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0},
            timeout=45,
        )
        rj = resp.json()
        text = rj["choices"][0]["message"]["content"]
        s, e = text.find("["), text.rfind("]") + 1
        data = json.loads(text[s:e]) if s != -1 and e > s else []
        out = []
        for item in data:
            stage, suggested = item.get("stage"), item.get("suggested")
            if (stage in routing and suggested in A.AGENT_REGISTRY
                    and suggested != item.get("current")):
                out.append({
                    "stage": stage,
                    "current": item.get("current") or agents.get(stage, ""),
                    "suggested": suggested,
                    "reason": (item.get("reason") or "").strip(),
                })
        return out
    except Exception:
        return []


def build_prompt(query: str, profile: str, agents: dict, attachments: list | None = None,
                 premium: list | None = None, brief: str = "") -> str:
    profile_line = (
        f"The user describes themselves / their work as: \"{profile}\".\n"
        "Tailor every prompt to that context.\n\n" if profile else ""
    )
    from . import files as F
    attach_line = F.routing_note(attachments or [])
    notes = _tool_notes()
    notes_block = (
        "═══ FIELD NOTES — HIGHEST PRIORITY (written by the user from real "
        "hands-on experience with these exact tools) ═══\n"
        "If anything in the RULES section below conflicts with these notes, "
        "THE NOTES WIN. When deciding WHICH stage should carry a piece of "
        "work, follow the notes' 'Use for / Avoid for / My take' lines over "
        "the generic tool descriptions above and over the rules below.\n"
        f"{notes}\n\n" if notes else ""
    )
    premium = premium or []
    enabled_premium = sorted({n for n in agents.values() if n in premium})
    premium_rule = (
        f"- PREMIUM PREFERENCE: the user PAYS for {', '.join(enabled_premium)}. When a "
        "piece of work could reasonably be carried by more than one enabled stage, give "
        "the bulk of it to the premium tool's stage — paid plans mean higher limits and "
        "better output. This only breaks ties: never violate the DELIVERABLE RULE or "
        "SCOPE LOCK, and never enable a stage the task doesn't need just because its "
        "tool is premium.\n" if enabled_premium else ""
    )
    brief_block = (
        "\n═══ TASK BRIEF (auto-expanded from the raw request by a prompt-"
        "engineering pass; mine it for context, deliverable specs, quality "
        "criteria and non-goals when writing each stage prompt) ═══\n"
        f"{brief}\n" if brief else ""
    )
    return f"""You are the routing brain of Prism — a multi-agent AI pipeline.

{profile_line}{attach_line}The user has enabled these pipeline stages (each backed by a specialist AI).
Stages run in this exact order, and each one receives the previous stages'
outputs as context:

{_stage_lines(agents, premium)}

{notes_block}═══ RULES ═══
{premium_rule}- DELIVERABLE RULE (overrides brains-first): if the user asks you to MAKE/BUILD/
  CREATE/DESIGN/GENERATE a concrete artefact, the matching MAKE-STAGE MUST run —
  brains alone only PLANS it, it does not produce it. Map the artefact to its stage:
    • image / logo / art / illustration ............ VISUAL
    • video / animation / voiceover / music ........ MEDIA
    • web app / website / UI / software tool ....... DEVELOPMENT
    • slide deck / PowerPoint / PPT / pitch deck ... PRESENTATION
    • full article / essay / long-form copy / script  CONTENT
  Typically pair BRAINS (plan/outline) → the make-stage (produce it). Never answer a
  "make me an X" request with brains only.
- SCRIPT RULE: a reel, video or deck needs WORDS — script, narration, captions,
  slide copy. Writing those words is CONTENT's job, not BRAINS'. Whenever MEDIA
  or PRESENTATION will produce the deliverable and CONTENT is enabled, add a
  CONTENT stage between the plan and the make-stage to write the exact words.
  BRAINS plans the concept; CONTENT writes the script; the make-stage produces it.
- SCOPE LOCK: every prompt you write MUST begin with the exact words "Your ONLY
  task is:" followed by that stage's job and nothing else. The agent must NEVER
  be asked to produce a deliverable that belongs to another stage. Example: if
  CONTENT is asked for webpage copy and DEVELOPMENT builds the page, the CONTENT
  prompt must end with "Do NOT design or build the webpage itself — output text
  only; the build happens in a later stage." Agents like Claude will build whole
  apps if you leave the door open, so close it explicitly.
- HAND-OFF AWARENESS: for every stage EXCEPT the last one you enable, the prompt
  must state that its output is not for the user — it will be passed verbatim to
  the next enabled stage as that stage's working brief. Instruct the agent to end
  its answer with a concise summary of every fact, decision and constraint the
  next stage needs (names, specs, style choices, wording that must be kept).
- FINAL STAGE: the LAST enabled stage's prompt must say the opposite — "you are
  the final stage; deliver the polished end result for the user, no hand-off."
- BRAINS-FIRST DEFAULT (for non-deliverables): if the task is analysis, a question,
  reasoning, planning, or a short written brief, use BRAINS ONLY.
- Small/simple tasks with no artefact → BRAINS ALONE.
- BRAINS also does the analysis + short brief that would otherwise look like RESEARCH
  or CONTENT. Do NOT add RESEARCH to "analyse this and design a logo" — that is
  BRAINS (analyse + brief) → VISUAL (make the image). Nothing else.
- RESEARCH is reserved for HEAVY tasks needing genuinely new external facts the model
  wouldn't know (complex web builds needing current docs, real market/price data,
  academic citations). Never for analysing given material or simple requests.
- CONTENT is reserved for SUBSTANTIAL writing deliverables. A short brief or plan is
  brains, not content.
- SUMMARY is OFF unless 3+ other stages ran AND need consolidating. With 1–2 stages,
  the final stage's own output IS the answer.
- Prefer ONE stage. Two is common. Three+ should be rare and clearly justified.
- Each stage receives the PREVIOUS stage's output as context (Prism injects it),
  so later prompts should say "using the previous stage's output, do X" rather
  than re-deriving from scratch. Don't ask a later stage to re-analyse raw input
  the earlier stage already handled.
- Set "needed": false (and "questions": []) for stages that don't apply.
- Never invent stages that aren't listed above.
- Each entry in "questions" must be a COMPLETE, self-contained prompt.
- Return an ARRAY of prompts per stage: usually ONE; use multiple only when the
  stage genuinely needs distinct prompts.
- DEVELOPMENT prompts must include full specs so the agent can ship a working
  result. SUMMARY must explicitly reference and combine the earlier outputs.
- PROMPT CRAFT (this is why Prism exists — every stage prompt must read like
  professional prompt engineering, never a paraphrase of the user's words).
  After the mandatory "Your ONLY task is:" opener, every prompt MUST contain:
    • ROLE: cast the agent as a specific senior expert matched to the task
      (e.g. "Act as a senior speech-ML researcher who has shipped multilingual
      ASR systems"), not a generic assistant.
    • CONTEXT: the situation plus every relevant fact, constraint and given
      from the task brief — the agent must never have to guess what's known.
    • DELIVERABLE SPEC: the exact output structure — named sections, tables,
      comparisons, word counts, format. Never just "provide a specification";
      list WHICH sections the specification must contain.
    • QUALITY BAR: 2–3 concrete success criteria the output must satisfy
      (e.g. "every model named must include its licence and hardware needs").
    • NON-GOALS: what the agent must NOT do, taken from SCOPE and scope lock.
  A well-crafted stage prompt is typically 120–250 words. A one-line prompt
  that restates the user's request is a routing failure.
{brief_block}
User's raw request (authoritative on scope — if the brief conflicts, this wins):
{query}

Return ONLY this JSON (no markdown, no commentary), using exactly these keys:
{_schema_stub(agents)}"""


def route(query: str, cfg: dict, attachments: list | None = None) -> dict:
    """Call Groq and return the routing dict (stage -> {questions, needed})."""
    agents = {k: v for k, v in (cfg.get("agents") or {}).items() if v}
    if not agents:
        raise ValueError("No agents configured. Run /agents to pick some first.")

    api_key = cfg.get("api_key")
    if not api_key:
        raise ValueError("No Groq API key configured. Run /key to add one.")

    # Pass 1 — enrichment: expand the raw ask into a professional task brief.
    model = cfg.get("model", "llama-3.3-70b-versatile")
    brief = ""
    try:
        brief = enrich_query(query, cfg.get("profile", ""), api_key, model)
        if brief:
            ui.info("🪄  expanded your request into a professional task brief")
    except Exception:
        brief = ""  # routing still works without the brief

    # Pass 2 — routing: pick stages and write engineered prompts from the brief.
    prompt = build_prompt(query, cfg.get("profile", ""), agents, attachments,
                          premium=cfg.get("premium") or [], brief=brief)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
    rj = resp.json()
    if "choices" not in rj:
        raise RuntimeError(f"Groq API error (HTTP {resp.status_code}): {json.dumps(rj)[:400]}")
    text = rj["choices"][0]["message"]["content"]
    s, e = text.find("{"), text.rfind("}") + 1
    if s == -1 or e <= s:
        raise RuntimeError(f"Groq returned no JSON:\n{text[:400]}")
    routing = json.loads(text[s:e])

    # Deterministic safety net: force make-stages the user clearly asked for.
    forced = apply_make_guardrail(query, routing, agents)
    if forced:
        pretty = ", ".join(f"{s} ({agents.get(s) or A.summary_agent_name(agents)})" for s in forced)
        ui.info(f"🛡️  guardrail enabled required stage(s): {pretty}")
    if apply_script_guardrail(routing, agents):
        ui.info(f"🛡️  guardrail enabled content ({agents['content']}) — "
                "the reel/deck's script is a content job, not a brains job")
    # Surface the enrichment brief so the UI can show the full transformation
    # chain (raw words → brief → stage prompts). Consumers iterate
    # PIPELINE_ORDER, so this extra key is invisible to them.
    routing["_brief"] = brief
    try:
        routing["_named_tools"] = detect_named_tools(query)
    except Exception:
        routing["_named_tools"] = {}
    try:
        routing["_suggestions"] = suggest_alternatives(query, brief, routing, agents,
                                                        api_key, model)
    except Exception:
        routing["_suggestions"] = []
    return routing
