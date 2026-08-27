"""
Prism Motion Graphics — scene-at-a-time generation
────────────────────────────────────────────────────
Turn one names the project, the camera's overall intent, and a storyboard
row per scene — not the scenes themselves. Every scene's actual nodes are
then asked for on their own turn, in the same conversation, and checked
before the next is asked for. This is the same shape as
core.reel_web.build_spec(), for the same two reasons: a model asked to
write a whole motion graphic in one reply spreads its budget thin across
every scene (measured on Studio: 278 chars/scene one-shot vs. 2,339
chars/scene one-turn-per-scene — see CHANGES.md "Round 6"), and a fault
raised while the model is still "on" that scene lands; raised later
against a reply it has moved past, it does not.
"""
from __future__ import annotations

from typing import Any, Callable

from .. import reel_web as _web
from .schema import MotionValidationError

SCENE_EXPECT = '"nodes"'

_PALETTE_GUIDANCE = """READ THE USER'S CONTEXT and autonomously decide the right visual language:
- Financial / data explainer  → deep navy (#06091A), emerald-green or gold accents, precise
  sans-serif, kinetic counter tickers, smooth line/ring charts
- SaaS / tech product demo    → obsidian (#07091A), electric cyan or violet, glassmorphism
  UI mockup, animated cursor, status badges, word-stagger headline
- Brand / social launch       → dark charcoal (#111215), warm coral or amber spotlight,
  bold heavy typography (900 weight), wide radial backlight, dynamic camera zoom
- Educational / explainer     → clean slate-navy (#0D1424), neutral accents, diagram nodes,
  sequential step arrows, readable body copy
- Healthcare / trust          → deep slate (#0A1020), soft teal (#14B8A6), high legibility,
  ring chart for KPI percentages, calm spring easing
- Consumer / lifestyle        → warm dark (#120E0A), coral-orange or amber, fast spring
  overshoots, energetic pacing, bold italic type

DO NOT use generic or safe designs. Make bold, modern, production-quality decisions."""

_NODE_CATALOGUE = """NODE TYPES (put these in "nodes"):
  text             — content, position, font_size, font_weight, fill, mode (see TEXT MODES)
  shape_rect       — position, width, height, radius, fill, is_glass (optional)
  shape_arrow      — from, to, curved, color, stroke_width, draw_start, draw_duration
  domain_chart     — chart_type (bar|line|ring|area|sparkline|metric), data, accent_color
  domain_ui_mockup — position, width, height, title, elements, cursor_actions
  domain_diagram   — nodes (id/label/position/shape/color), edges (from/to/pulse)

TEXT MODES (set on any text node via "mode"):
  word_stagger   — words pop in sequentially with spring overshoot (default, versatile)
  masked_reveal  — text rises from behind horizontal stencil clip (editorial, slow)
  shimmer_sweep  — fade in then light-beam sweeps across the headline (premium feel)
  char_cascade   — characters fall in with staggered bounce (energetic, consumer)
  split_slide    — text halves slide in from opposite sides (dramatic, brand)
  blur_pop       — blurs to sharp with scale pop (tech, SaaS)
  counter_tick   — numeric value ticks up from 0 to target (data, financial)
  typewriter     — characters appear left-to-right with cursor (developer, code)

ANIMATION, on any node via "animation" (all times are LOCAL to this scene, starting at 0):
  "enter": {"type": "fade_in|pop_in|slide_up|slide_down", "time":, "duration":,
            "easing":, "properties": {"scale": {"easing": "..."}, ...}}
  "exit":  same shape as "enter" — give at least one node per scene a real
           exit so the scene doesn't just settle and hold until the cut.
  "secondary_motion": {"property": "rotation|position.x|position.y|opacity|scale",
                        "freq":, "amount":, "seed": "<anything stable>"} — a small
           deterministic wiggle on top of the main animation.
  "follow": {"lag":, "damping":} — on a CHILD node, makes it trail the parent's
           recent motion instead of moving in rigid lockstep.

EASING VALUES:
  easeOutCubic, easeInCubic, easeInOutCubic, easeOutExpo, easeInOutExpo,
  back.in, back.out, back.inOut, elastic.in, elastic.out, elastic.inOut,
  bounce.in, bounce.out, bounce.inOut, spring, smooth, linear

  Pick by what the thing is doing, not by habit — reusing one easing for
  everything that moves is the fastest way to look like a slide deck, no
  matter how many elements are on screen. `.out` curves for things
  ARRIVING, `.in` curves for things LEAVING, `smooth`/linear/spring for
  motion still running when the scene hands over. Use at least two
  distinct easings in this scene."""


def storyboard_instructions(request: str) -> str:
    """Turn one: the look, the camera's overall intent, and a storyboard
    row per scene. Mirrors core.reel_web.design_instructions()'s split."""
    return (
        "You are Prism's Senior Visual Director and Motion Designer, "
        "planning a short vertical motion graphic.\n\n"
        f"WHAT THE CLIENT ASKED FOR:\n{request}\n\n"
        + _PALETTE_GUIDANCE + "\n\n"
        "This is turn one of a conversation. Right now, name the project "
        "settings, the camera's overall intent, and a STORYBOARD — one row "
        "per scene, words only, no nodes yet. Each scene's actual content "
        "is asked for on its own turn, right after this one, so it gets "
        "your full attention rather than a fraction of one reply split "
        "across everything.\n\n"
        "Reply with ONLY this JSON object, in a ```json fenced code block, "
        "nothing before or after it:\n"
        "{\n"
        '  "project": {"width": 1080, "height": 1920, "fps": 30, '
        '"duration": 8.0, "background": "#07091A"},\n'
        '  "camera": {"tracks": [\n'
        '    {"time": 0.0, "position": [540, 960], "zoom": 1.0},\n'
        '    {"time": 1.2, "position": [540, 900], "zoom": 1.35, '
        '"duration": 1.1, "easing": "easeInOutCubic"}\n'
        "  ]},\n"
        '  "storyboard": [\n'
        '    {"scene": 1, "seconds": 2.5,\n'
        '     "job": "what this scene is FOR in the argument",\n'
        '     "look": "what is on screen and how it is composed — what is '
        'big, what is a supporting label, what kind of node carries it",\n'
        '     "motion": "what moves, in what order, from where — and what '
        'is still moving when the scene hands over"}\n'
        "  ]\n"
        "}\n\n"
        "3-6 scenes, 6-15 seconds total. ONE STORYBOARD ROW PER SCENE. Give "
        "each one a different job and a different composition — several "
        "scenes that are all a centred headline over the same background "
        "is the failure this stage exists to prevent. `camera.tracks` is "
        "for the WHOLE graphic — rule 3 below still applies."
    )


def scene_instructions(idx: int, total: int, row: dict, assets: str = "") -> str:
    """Ask for ONE scene's nodes. The rest of the conversation already
    knows the palette and camera from turn one; this only needs the row."""
    job = str(row.get("job", "")).strip() or "carry the argument forward"
    look = str(row.get("look", "")).strip()
    motion = str(row.get("motion", "")).strip()
    try:
        seconds = float(row.get("seconds") or 3.0)
    except (TypeError, ValueError):
        seconds = 3.0
    return (
        f"SCENE {idx + 1} of {total}.\n\n"
        f"ITS JOB: {job}\n"
        + (f"THE LOOK: {look}\n" if look else "")
        + (f"THE MOTION: {motion}\n" if motion else "")
        + f"\nDuration: {seconds:g} seconds. All this scene's animation "
        "times count from 0 at this scene's own start.\n\n"
        + _NODE_CATALOGUE + "\n\n"
        "DESIGN RULES FOR THIS SCENE:\n"
        "1. 3-7 nodes. Do not overcrowd.\n"
        "2. Vary the text mode — use at most 2 different modes.\n"
        '3. Give at least one node a real "exit", not only "enter".\n'
        "4. The most common way a scene ends up reading as a PowerPoint "
        "slide, however busy it is, is every element using the same "
        "easing curve. Treat that as the failure to design against.\n"
        "5. Never use generic emojis in text content.\n\n"
        + (f"ARTWORK YOU MAY USE:\n{assets}\n\n" if assets else "")
        + "Reply with ONLY this JSON object, in a ```json fenced code "
        "block, nothing before or after it:\n"
        '{\n  "nodes": [ /* 3-7 node objects, as above */ ]\n}'
    )


def parse_storyboard(text: str) -> tuple[dict, dict, list[dict]]:
    """Turn one's reply: (project, camera, storyboard rows).

    A reply that answers whole scenes anyway is not an error — its scene
    list is a perfectly good storyboard if none was written, the same
    accommodation core.reel_web.parse_design() makes.
    """
    project: dict = {}
    camera: dict = {}
    board: list[dict] = []
    for got in _web._json_objects(text):
        p = got.get("project")
        if isinstance(p, dict) and not project:
            project = p
        c = got.get("camera")
        if isinstance(c, dict) and not camera:
            camera = c
        rows = got.get("storyboard")
        if isinstance(rows, list) and not board:
            board = [r for r in rows if isinstance(r, dict)]
        if not board and isinstance(got.get("scenes"), list):
            board = [{"job": "", "look": "", "motion": "",
                      "seconds": s.get("duration")}
                     for s in got["scenes"] if isinstance(s, dict)]
        if project and board:
            break
    if not project:
        raise MotionValidationError(
            "The storyboard stage returned no project settings.")
    return project, camera, board


def parse_scene(text: str) -> dict | None:
    """One scene's reply: {"nodes": [...]}, or None if unusable."""
    for got in _web._json_objects(text):
        if isinstance(got.get("scenes"), list) and got["scenes"]:
            inner = got["scenes"][0]
            if isinstance(inner, dict) and isinstance(inner.get("nodes"), list):
                got = inner
        nodes = got.get("nodes")
        if isinstance(nodes, list) and nodes:
            return {"nodes": [n for n in nodes if isinstance(n, dict)]}
    return None


def _scene_count(text: str) -> int:
    """How many scenes-with-nodes a reply actually contained — same
    over-eager-reply detector as core.reel_web._scene_count()."""
    n = 0
    for got in _web._json_objects(text):
        rows = got.get("scenes")
        if isinstance(rows, list):
            n += sum(1 for s in rows
                      if isinstance(s, dict) and isinstance(s.get("nodes"), list))
    return n or (1 if parse_scene(text) else 0)


def fallback_scene(row: dict) -> dict:
    """A plain, code-authored scene for when this one's reply can't be
    recovered — mirrors core.reel_web.fallback_scene(): a graphic with one
    dull scene ships, one with a hole in it does not."""
    words = str(row.get("job", "")).strip() or "…"
    return {
        "nodes": [{
            "type": "text", "content": words, "position": [540, 960],
            "font_size": 64, "font_weight": 700, "fill": "#F8FAFC",
            "mode": "word_stagger",
            "animation": {"enter": {"type": "fade_in", "duration": 0.6,
                                     "easing": "easeOutCubic"}},
        }],
    }


def build_spec(first_reply: str, ask: Callable[..., str], assets: str = "",
               check=None, log=None, should_stop=None, on_scene=None) -> dict:
    """Run the rest of the design conversation and return the finished spec.

    `ask(prompt, expect) -> str` sends a follow-up in the tab turn one is
    already sitting in. `check(spec) -> list[str]` reports concrete faults
    for a one-scene spec. Both are injected rather than imported, so this
    can be exercised without a browser — see core.reel_web.build_spec()'s
    docstring, which this mirrors exactly. `on_scene(index, total)` fires
    before each scene is asked for, for the same reason: this loop takes
    minutes, and nothing here raises once turn one has parsed.
    """
    def say(msg):
        if log:
            log(msg)

    project, camera, board = parse_storyboard(first_reply)
    total = len(board)
    if not total:
        raise MotionValidationError(
            "The storyboard names no scenes — there is nothing to build.")

    say(f"storyboard: {total} scene(s) — writing them one at a time")
    scenes: list[dict] = []
    overflow_notice = ""
    for i in range(total):
        if should_stop and should_stop():
            say("stopped — keeping the scenes written so far")
            break
        if on_scene:
            try:
                on_scene(i, total)
            except Exception:                        # noqa: BLE001
                pass          # a progress listener must never fail the run
        prompt = overflow_notice + scene_instructions(i, total, board[i], assets)
        overflow_notice = ""
        raw = ask(prompt, SCENE_EXPECT) or ""
        scene = parse_scene(raw)
        if scene is None:
            scene = parse_scene(ask(
                f"Send scene {i + 1} again as JSON only — first character "
                "'{', last '}', key \"nodes\", wrapped in a ```json fenced "
                "block. Nothing before or after.",
                SCENE_EXPECT) or "")
        elif _scene_count(raw) > 1:
            n = _scene_count(raw)
            say(f"scene {i + 1} came back with {n} scenes in it — only the "
                "first was kept; telling it to slow down before the next ask")
            overflow_notice = (
                f"Before the next scene: that last reply answered {n} "
                f"scenes at once. Only scene {i + 1} was kept — the rest "
                "were discarded, not saved for later, so nothing from them "
                "will appear in the motion graphic. From here on, answer "
                "EXACTLY ONE scene per reply, the one actually asked "
                "for.\n\n")
        if scene is None:
            say(f"scene {i + 1} never came back as JSON — using a plain one")
            scene = fallback_scene(board[i])

        try:
            seconds = float(board[i].get("seconds") or 3.0)
        except (TypeError, ValueError):
            seconds = 3.0
        scene["id"] = f"scene_{i}"
        scene["duration"] = seconds

        if check:
            try:
                faults = check({"project": project, "camera": camera,
                                 "scenes": [scene]})
            except Exception as e:
                say(f"couldn't check scene {i + 1} ({e})")
                faults = []
            if faults:
                say(f"scene {i + 1} has {len(faults)} problem(s) — "
                    "sending them back")
                fixed = parse_scene(ask(
                    f"Scene {i + 1} was checked and these are wrong:\n\n"
                    + "\n".join(f"{n}. {x}" for n, x in enumerate(faults[:8], 1))
                    + "\n\nSend the corrected scene: ONLY the JSON object, "
                      "same shape, in a ```json fenced block.",
                    SCENE_EXPECT) or "")
                if fixed:
                    fixed["id"] = scene["id"]
                    fixed["duration"] = scene["duration"]
                    try:
                        left = check({"project": project, "camera": camera,
                                       "scenes": [fixed]})
                    except Exception:
                        left = []
                    # Kept only if genuinely cleaner — same rule as reel_web:
                    # a "fix" trading four faults for five is not a fix.
                    if len(left) < len(faults):
                        scene = fixed
                        say(f"   fixed — {len(faults)} down to {len(left)}")
                    else:
                        say("   the correction was no better — keeping the "
                            "first")
        scenes.append(scene)
        say(f"scene {i + 1}/{total} written — "
            f"{len(scene.get('nodes', []))} node(s)")

    if not scenes:
        raise MotionValidationError("No scenes were written.")
    spec: dict[str, Any] = {"project": project, "scenes": scenes}
    if camera:
        spec["camera"] = camera
    return spec
