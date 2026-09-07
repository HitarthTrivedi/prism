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

# ── surviving Groq ────────────────────────────────────────────────────────────
# Hosted open models get retired on a few weeks' notice, and every install
# carries the same default. A single hardcoded model name means the day Groq
# drops it, every customer stops being able to plan anything, at the same hour,
# with "model_not_found" in a dialog box.
#
# So the model is a LIST, tried in order. The first one that answers is written
# back into the config, so the fallback costs one wasted request once and
# nothing afterwards. Ordered by capability: routing is the most demanding
# thing Prism asks a model to do, and a weaker one produces worse plans rather
# than visible errors.
# Verified against the live API on 18 Aug 2026 — every entry answered, and
# every entry returned parseable JSON for a routing-shaped prompt. Do not add a
# model to this tuple without checking BOTH: three of the four that used to be
# here were dead, and the survivors are not interchangeable.
#
#   llama-3.3-70b-versatile   404, retired
#   llama-3.1-8b-instant      404, retired
#   gemma2-9b-it              400, decommissioned
#
# So the chain had one working model left and it was third, meaning every plan
# paid for two failed round trips first, and one more retirement would have
# stopped every customer planning anything.
#
# qwen/qwen3.6-27b is deliberately ABSENT although it answers. It is a
# reasoning model that writes a <think> block into `content` — 1531 characters
# of it for a prompt asking only for JSON — so routing would get a plan it
# cannot parse. Answering is not the same as being usable.
#
# Note the reasoning models here put their working-out in a separate
# `reasoning` field and can return EMPTY `content` if max_tokens runs out
# first. Give them room; a short cap reads as "the model returned nothing".
MODEL_FALLBACKS = (
    "openai/gpt-oss-120b",      # largest; routing is the most demanding call
    "openai/gpt-oss-20b",
    "groq/compound-mini",
)

# Set from a licence-server payload, so a retirement is a database row rather
# than a release every customer has to install. Empty until one arrives.
_SERVED_CHAIN: list[str] = []


def apply_model_chain(models) -> int:
    """Replace the server-published model chain. Returns how many are in use.

    Replace rather than merge: the payload is the whole intended list, so a
    model dropped from it must stop being tried. Rubbish is ignored rather
    than raising — a bad publish must not be able to stop Prism planning.
    """
    _SERVED_CHAIN.clear()
    for name in (models or []):
        if isinstance(name, str) and name.strip():
            _SERVED_CHAIN.append(name.strip())
    return len(_SERVED_CHAIN)

# HTTP statuses worth trying again rather than surfacing. 429 is Groq's rate
# limit, which a queue of tasks hits routinely on the free tier; 5xx is Groq
# having a moment. Anything else is a real answer and is reported.
_RETRY_STATUS = (429, 500, 502, 503, 504)


def model_chain(preferred: str = "") -> list[str]:
    """The models to try, the caller's choice first and never duplicated.

    A server-published chain, when there is one, comes before the built-in
    tuple but still after the caller's own preference — the customer's setting
    is theirs, and the payload exists to fix OUR stale list, not to override
    what somebody deliberately chose.
    """
    chain = [m for m in ([preferred] if preferred else []) if m]
    for source in (_SERVED_CHAIN, MODEL_FALLBACKS):
        chain += [m for m in source if m not in chain]
    return chain


def _remember_model(model: str, preferred: str) -> None:
    """Persist a fallback that worked, so the next run starts there.

    Written through config.load/save rather than the caller's dict: route() is
    handed a cfg the GUI keeps in memory across dialogs, and writing that back
    wholesale is how a stale copy erases someone's API key.
    """
    if not model or model == preferred:
        return
    try:
        from . import config as C
        saved = C.load()
        if saved.get("model") != model:
            saved["model"] = model
            C.save(saved)
        ui.warn(f"Groq no longer offers {preferred or 'the saved model'} — "
                f"switched to {model} and saved it.")
    except Exception:                                   # noqa: BLE001
        pass        # a config we cannot write must not fail the run


def _model_is_gone(status: int, body: dict) -> bool:
    """Did Groq refuse because THIS MODEL is unavailable, as opposed to
    because the key, the quota or the request was wrong?"""
    if status not in (400, 404):
        return False
    blob = json.dumps(body).lower()
    return ("model" in blob
            and any(w in blob for w in ("not found", "does not exist",
                                        "decommission", "deprecat",
                                        "no longer", "unavailable")))


def groq_chat(api_key: str, model: str, prompt: str, *, temperature: float = 0.3,
              timeout: int = 60, retries: int = 1, json_mode: bool = False) -> str:
    """One Groq completion, with the two failures a daily user actually hits.

    Rate limits are retried after the wait Groq asks for; a retired model falls
    through to the next in the chain and the working one is saved. Everything
    else raises with the server's own words, because those are usually
    actionable ("invalid api key") and paraphrasing them loses that.

    `json_mode` asks Groq to constrain the whole reply to a single JSON object
    (`response_format={"type": "json_object"}`). This is the point of the API
    path for machine-consumed stages: unlike a scraped browser answer, the
    model cannot wrap the JSON in prose, refuse the format as an "injection",
    or trail off — so a renderer/parser downstream gets valid JSON or a clean
    error, with a real completion signal instead of a 300s stability guess.
    The prompt must itself mention JSON somewhere or Groq rejects the request;
    Prism's machine-stage prompts already say "reply with only a JSON object".
    """
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    last = ""
    for candidate in model_chain(model):
        for attempt in range(retries + 1):
            try:
                payload = {"model": candidate,
                           "messages": [{"role": "user", "content": prompt}],
                           "temperature": temperature}
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                resp = requests.post(
                    GROQ_URL, headers=headers, json=payload, timeout=timeout)
            except requests.RequestException as e:
                raise RuntimeError(
                    f"Couldn't reach Groq — check your internet connection. "
                    f"({e})") from e

            try:
                body = resp.json()
            except ValueError:
                body = {}

            if resp.status_code == 200 and "choices" in body:
                _remember_model(candidate, model)
                return body["choices"][0]["message"]["content"]

            if resp.status_code in _RETRY_STATUS and attempt < retries:
                # Groq names the wait in a header; honour it, but cap it —
                # nobody wants the window to sit there for two minutes.
                import time
                delay = 5
                try:
                    delay = min(int(float(resp.headers.get("retry-after", 5))), 20)
                except (TypeError, ValueError):
                    pass
                ui.warn(f"Groq is rate-limiting this key — waiting {delay}s "
                        f"and trying once more.")
                time.sleep(delay)
                continue

            if _model_is_gone(resp.status_code, body):
                ui.warn(f"Groq has retired {candidate} — trying the next model.")
                break               # next candidate in the chain

            if resp.status_code == 429:
                raise RuntimeError(
                    "Groq is rate-limiting your API key. Wait a minute and try "
                    "again, or raise your limits at console.groq.com.")
            if resp.status_code == 401:
                raise RuntimeError(
                    "Groq rejected your API key. Re-enter it in Setup → Groq "
                    "API key.")
            last = f"HTTP {resp.status_code}: {json.dumps(body)[:300]}"
            raise RuntimeError(f"Groq API error ({last})")

    raise RuntimeError(
        "None of the models Prism knows about are available on your Groq key. "
        "Check console.groq.com for the current model list, then set it in "
        f"Setup. Tried: {', '.join(model_chain(model))}")

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
    """Every list this checks (_BUILD_VERBS, _ARTEFACT_STAGES) is written in
    the singular/base form — "image", "generate" — but real requests say
    "images" or "it generates images" as often as not. \\b alone treats the
    plural 's' as a boundary violation and misses those entirely, which is
    how "also use Images ... in the reel too" fails to trigger the visual
    guardrail even though it says exactly what it means."""
    for t in terms:
        if " " in t or "-" in t:
            if t in text_lc:
                return True
        elif re.search(r"\b" + re.escape(t) + r"s?\b", text_lc):
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


# Prism Reel's whole house style is icons and typography, drawn in code —
# real photographs never appear in a frame, only their pixels sampled once for
# an accent colour. A brief that needs actual photography ON SCREEN (a grid
# mockup, a before/after comparison, real product or lifestyle shots) is
# asking for something that renderer structurally cannot draw. Prism Studio
# can: it places images by a real `asset:name` reference. Terms are about
# photography APPEARING in the video, not about "make an image" as a
# separate deliverable — that is _ARTEFACT_STAGES["visual"]'s job, a
# different question with a different answer.
_PHOTO_REEL_TERMS = [
    "instagram grid", "grid mockup", "mockup", "before/after",
    "before and after", "lifestyle photo", "lifestyle shot",
    "product photo", "product shot", "stock photo", "real photo",
    "photograph", "photoshoot", "phone mockup",
]


def apply_studio_guardrail(query: str, routing: dict, agents: dict) -> str:
    """Swap Prism Reel for Prism Studio when the brief clearly needs real
    photography drawn INTO the reel, not just brand colour sampled off one.

    Deterministic for the same reason apply_make_guardrail is: this is a
    structural mismatch between what was asked for and what the configured
    tool can physically draw, not a judgment call worth leaving to whichever
    way an LLM router happens to read the brief that day.

    A per-run swap only — mutates `routing`, not `agents` or the user's saved
    config, so the next run still defaults back to whatever they configured.
    Returns the message to log, or "" if nothing changed."""
    if agents.get("media") != "Prism Reel":
        return ""                          # nothing to swap, or already Studio
    if "Prism Studio" not in A.CATEGORIES.get("media", {}).get("agents", []):
        return ""
    m = routing.get("media") or {}
    if not (m.get("needed") and m.get("questions")):
        return ""
    if not _mentions(query.lower(), _PHOTO_REEL_TERMS):
        return ""
    routing["media"]["agent_override"] = "Prism Studio"
    return ("this brief needs real photography in the reel, which Prism "
            "Reel's code-drawn house style can't display — using Prism "
            "Studio instead for this run")


# One-line description of what each stage is FOR, injected into the prompt only
# for the stages the user actually enabled.
_STAGE_HELP = {
    "research": "HEAVY tasks only — genuinely NEW external facts/citations/papers/prices the model "
                "wouldn't already know, e.g. a complex build needing current docs or real market data. "
                "NOT for analysing given material and NOT for simple asks."
                "For research purpose where you'll need the factual evidences on recent events or when webscraping will be needed along with writing something about that information in depth",
    "leads": "finding WHO to approach — real companies, named decision-makers and their "
             "contact email addresses. Turn this on for anything shaped like 'find "
             "customers / prospects / leads / companies in <place or industry>', or an "
             "outreach task that names no recipients. It answers WHO, never WHAT: it "
             "does not write the email, that is content or brains. Independent of "
             "research — a run may need research to decide which industry to target "
             "and leads to pull the companies in it, so turning BOTH on is normal.",
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
        # The inner quotes are single on purpose: reusing double quotes inside a
        # double-quoted f-string is PEP 701 syntax and only parses on Python
        # 3.12+. Prism supports 3.10+, and on anything older this is a
        # SyntaxError raised at IMPORT time — so the whole engine, and the GUI
        # that imports it through core_bridge, failed to start at all.
        lines.append(f"- {stage.upper()} → {name}: {spec}{star}\n"
                     f"    USE FOR: {_STAGE_HELP.get(stage, '')}")
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
    # Enrichment is a nicety — routing works without it — so a failure here
    # returns empty rather than raising and taking the plan down with it.
    try:
        return groq_chat(api_key, model, prompt, timeout=45).strip()
    except Exception:                                   # noqa: BLE001
        return ""


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
        text = groq_chat(api_key, model, prompt, temperature=0, timeout=45,
                         retries=0)
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



# Tools that run their own multi-pass research loop. Prism's house style —
# "Your ONLY task is", an exhaustive deliverable spec, a fixed section list —
# reads to these as an instruction to stop after one pass, and they come back
# thinner than the tool they were chosen over. They need the goal and the
# quality bar, not the procedure.
#
# Read off the registry rather than listed here, so marking a new tool
# self-directing is one flag in agents.py and nothing else.
def _self_directing_names() -> list[str]:
    return sorted(name for name, cfg in A.AGENT_REGISTRY.items()
                  if cfg.get("prompt_style") == "natural")


def _self_directing_rule(agents: dict) -> str:
    """The carve-out, included only when such a tool is actually in the plan."""
    names = [n for n in _self_directing_names() if n in set(agents.values())]
    if not names:
        return ""
    listed = " and ".join(names)
    return (
        f"- SELF-DIRECTING TOOLS ({listed}). {listed} runs its own multi-pass\n"
        f"  research loop and scrapes the live web itself. For its stage ONLY,\n"
        f"  write the prompt as a well-briefed human would ask a specialist:\n"
        f"    • Do NOT use the \"Your ONLY task is:\" opener.\n"
        f"    • Give it the SUBJECT, the audience, and what a good answer must\n"
        f"      cover — then let it choose how to get there.\n"
        f"    • Do NOT prescribe the steps, the section list, or the word count.\n"
        f"    • Keep ROLE and CONTEXT; drop the rigid DELIVERABLE SPEC.\n"
        f"  Over-specifying makes {listed} skip its analyse and optimise passes,\n"
        f"  which is the entire reason it was picked over a plain search tool.\n")


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
    self_directing_block = _self_directing_rule(agents)
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
{self_directing_block}- PROMPT CRAFT (this is why Prism exists — every stage prompt must read like
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


def verify_key(api_key: str, model: str = "") -> str:
    """Check a Groq key against Groq. Returns "" if it works, else why not.

    Both the CLI and the GUI only ever checked that a key starts with 'gsk_'
    and is long enough, which a typo, a revoked key and a key pasted with half
    a newline in it all pass. The first sign of trouble was then the first real
    task failing with a raw HTTP 401 body — minutes after setup, and worded for
    a developer. This is the same question asked at the moment the key is
    entered, when it is still obvious what to do about the answer.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return "No key entered."
    if not api_key.startswith("gsk_"):
        return "A Groq key starts with 'gsk_' — this doesn't look like one."
    try:
        resp = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    except Exception as e:
        return (f"Couldn't reach Groq to check the key ({e}).\nIf you're behind "
                "a proxy or offline, the key may still be fine.")
    if resp.status_code == 401:
        return ("Groq rejected this key. Make a new one at console.groq.com/keys "
                "and paste it again — copying it twice by accident is the usual "
                "cause.")
    if resp.status_code == 429:
        return ("The key is valid, but Groq is rate-limiting it right now. "
                "Routing may be slow until that clears.")
    if resp.status_code != 200:
        return f"Groq answered HTTP {resp.status_code} — the key may not be usable."
    if model:
        try:
            names = {m.get("id") for m in resp.json().get("data", [])}
            if names and model not in names:
                return (f"The key works, but '{model}' isn't available on it. "
                        "Prism will fall back to whatever Groq allows.")
        except Exception:
            pass
    return ""


def _escape_inner_quotes(block: str) -> str:
    """Escape a raw double quote a model left INSIDE a JSON string.

    The plan's prompts are about writing, so they quote things — `Act as a
    "senior" strategist` — and a model that has just been told to write
    engaging copy does not always remember that the quote has to be `\\"`
    once it is inside a JSON string. Walks the text tracking whether it is
    inside a string; a quote there that is not followed (after whitespace)
    by `,` `}` `]` or `:` cannot be the string's end, so it is escaped.
    """
    out, in_str, esc = [], False, False
    n = len(block)
    for i, ch in enumerate(block):
        if in_str:
            if esc:
                esc = False
                out.append(ch)
                continue
            if ch == "\\":
                esc = True
                out.append(ch)
                continue
            if ch == '"':
                j = i + 1
                while j < n and block[j] in " \t\r\n":
                    j += 1
                if j >= n or block[j] in ",}]:":
                    in_str = False
                    out.append(ch)
                else:
                    out.append('\\"')
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def _parse_plan(text: str):
    """The routing dict out of a planner reply — (dict, None), or (None, why).

    Tries the reply as written, then the repairs a scraped browser reply
    already gets (comments, curly quotes, trailing commas, control
    characters in strings — core.reel._loosen), then the one fault a
    planner makes that those do not cover: a raw quote inside a string.
    """
    from . import reel as _reel
    text = text or ""
    blocks = _reel._blocks(text, "{", "}")
    s, e = text.find("{"), text.rfind("}") + 1
    if s != -1 and e > s and text[s:e] not in blocks:
        # An unbalanced quote throws the string-aware brace scan off; the
        # outermost slice is the plain-minded fallback.
        blocks.append(text[s:e])
    if not blocks:
        return None, "no JSON object in the reply"
    why = None
    for block in blocks:
        for candidate in (block, _reel._loosen(block),
                          _escape_inner_quotes(block),
                          _escape_inner_quotes(_reel._loosen(block))):
            try:
                got = json.loads(candidate)
            except Exception as err:                       # noqa: BLE001
                why = why or err
                continue
            if isinstance(got, dict):
                return got, None
    return None, why or "the reply was not a JSON object"


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
    # The one call the whole plan depends on, so this is the one that gets a
    # retry and the full model chain.
    text = groq_chat(api_key, model, prompt, timeout=60)
    routing, why = _parse_plan(text)
    if routing is None:
        # "Expecting ',' delimiter: line 16 column 49" — twice in a row on
        # 2026-09-07, a raw quote inside a prompt string, and each time the
        # whole plan was thrown away behind a "Something went wrong" dialog
        # with the parser's words in it. The repairs in _parse_plan catch
        # most of that; what they cannot, one more ask with the error quoted
        # back does — in JSON mode, where Groq itself refuses to hand back
        # anything that does not parse.
        ui.warn(f"the plan came back as JSON that would not parse ({why}) "
                "— asking once more")
        again = (prompt + "\n\nYOUR PREVIOUS REPLY WAS NOT VALID JSON — the "
                 f"parser said: {why}. Reply again with ONLY the JSON "
                 "object: every double quote inside a string escaped as "
                 "\\\", no comments, no trailing commas, nothing before or "
                 "after it.")
        try:
            text = groq_chat(api_key, model, again, timeout=60, json_mode=True)
        except RuntimeError:
            text = groq_chat(api_key, model, again, timeout=60)
        routing, why = _parse_plan(text)
    if routing is None:
        raise RuntimeError(
            "Prism's planner wrote a plan it could not read back "
            f"({why}). This is usually momentary — press Make a plan again.")

    # Deterministic safety net: force make-stages the user clearly asked for.
    forced = apply_make_guardrail(query, routing, agents)
    if forced:
        pretty = ", ".join(f"{s} ({agents.get(s) or A.summary_agent_name(agents)})" for s in forced)
        ui.info(f"🛡️  guardrail enabled required stage(s): {pretty}")
    if apply_script_guardrail(routing, agents):
        ui.info(f"🛡️  guardrail enabled content ({agents['content']}) — "
                "the reel/deck's script is a content job, not a brains job")
    studio_swap = apply_studio_guardrail(query, routing, agents)
    if studio_swap:
        ui.info(f"🛡️  guardrail: {studio_swap}")
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
