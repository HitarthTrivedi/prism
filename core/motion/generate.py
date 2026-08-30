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

_PALETTE_GUIDANCE = """Choose a PALETTE and a TYPE PAIRING for this specific brief — not from a
fixed industry lookup. "Financial" does not mean navy, "SaaS" does not mean
cyan-on-obsidian — those are reflexes, and every video that reaches for the
same reflex for the same kind of request looks like it came from a template
library, no matter how different the copy is.

Decide instead from what the brief is actually asking to FEEL like: is it
warm/editorial and trustworthy (cream or ivory background, a dark ink text
colour, one warm metal or earth accent, a serif display face), cool/precise
and technical (deep charcoal or ink background, one saturated accent,
condensed sans display face), bright/energetic and consumer-facing (a light
or mid-tone ground, a bold saturated accent, a heavy display weight), or
something else the brief itself suggests — pick the mood, then commit to a
small NAMED set for the whole piece:
  project.palette: { bg_a, bg_b, ink, accent, accent2 } — bg_a/bg_b are the
    two backgrounds scenes alternate between (see rule 8 below), ink is the
    text colour that reads on whichever background is light, accent is the
    one colour used sparingly and consistently as the piece's signature.
  project.type: { display_font, body_font } — a real two-font pairing (a
    display/serif or condensed face for headlines and numbers, a plain
    sans for body copy and labels) picked for the SAME mood as the
    palette, not the same font doing both jobs.

DO NOT use generic or safe designs. Make bold, specific, production-quality
decisions — and make a genuinely different decision than the last brief
that felt similar, the same way rule 4 below asks for genuinely different
easing choices scene to scene."""

_NODE_CATALOGUE = """NODE TYPES (put these in "nodes"):
  text             — content, position, font_size, font_weight, fill, mode (see TEXT MODES).
                      A long headline may use "\\n" for a manual line break —
                      each line is centred and stacked automatically.
  shape_rect       — position, width, height, radius, fill, is_glass (optional)
  shape_arrow      — from, to, curved, color, stroke_width, draw_start, draw_duration
  domain_chart     — chart_type (bar|line|ring|area|sparkline|metric), data, accent_color
  domain_ui_mockup — position, width, height, title, elements, cursor_actions
  domain_diagram   — nodes (id/label/position/shape/color), edges (from/to/pulse)
  image            — position, width, height, radius (corner rounding), anchor;
                      "src" is `asset:<name>` for one of the client's own images
                      below (never write a real URL or invent a name) — a logo,
                      a product photo, a screenshot. Drawn clipped to a rounded
                      rect; SVG marks work the same way as photos.

"anchor" (on any node) is ALWAYS a two-number [x, y] fraction of the node's
own box, e.g. [0.5, 0.5] for its centre, [0, 0] for its top-left corner —
never a keyword string like "center" or "top-left".

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
  "enter": {"time":, "duration":, "tweens": [
              {"channel": "opacity", "from": 0, "to": 1, "easing": "power2.out"},
              {"channel": "y", "from": 20, "to": 0, "easing": "power2.out", "delay": 0.05},
              ...
           ]}
  "exit":  same shape as "enter" — give at least one node per scene a real
           exit so the scene doesn't just settle and hold until the cut.
  Put as many tweens in one enter/exit as the moment needs — this is how a
  blur-focus reveal, a directional slide, a scale-pop and a plain fade are
  all really just different CHANNEL COMBINATIONS of the same mechanism, not
  different fixed "types" to pick from. "from"/"to" for x/y are relative
  OFFSETS from the node's own resting position (20 means 20px away from
  where it settles, not an absolute coordinate); scale/scaleX/scaleY/
  rotation/skewX/skewY/opacity work the same way; blur/clipInset/
  backgroundPositionX/strokeDashoffset are absolute values in their own
  units (blur in px, clipInset 0-100 as percent revealed, strokeDashoffset
  in the node's own path-length units — leave it to the runtime's own
  full-length value, tween TO 0 to fully draw a line/arrow/mark).
  TWEEN CHANNELS: opacity, x, y, scale, scaleX, scaleY, rotation, skewX,
    skewY, blur, clipInset, backgroundPositionX, strokeDashoffset.
  "secondary_motion": {"property": "<any channel above>", "freq":, "amount":,
                        "seed": "<anything stable>"} — a small deterministic
           wiggle on top of the main animation, running the WHOLE time this
           node is on screen (see the background layer's rule below).
  "follow": {"lag":, "damping":} — on a CHILD node, makes it trail the parent's
           recent motion instead of moving in rigid lockstep. (Not yet
           supported by the current runtime — avoid relying on it.)

TRANSITIONS, on a SCENE (not a node) via "transition_in" — how this scene
cuts in from the one before it. Omit it and one is still picked for you
(never a silent hard cut), but naming one on purpose usually reads better:
  push        — both scenes travel together, new one pushing the old off. Neutral, use for an ordinary beat.
  push_up     — same as push, vertical instead of horizontal.
  squeeze     — the old scene compresses away, the new one opens out. Mechanical, precise — industrial/technical subjects.
  zoom        — the old scene rushes past and blurs, the new one rises from behind it. Reserve it — it reads as pushing deeper into the same thought.
  blur_swoosh — both scenes blur/skew past each other, directional. Editorial, motion-forward.
  light_leak  — a warm light wash bridges the cut. Editorial, warm, premium — good between two beats of the same argument rather than a hard scene change.

EASING VALUES (GSAP's own — the runtime hands these straight to the tween engine):
  power1.in/out/inOut, power2.in/out/inOut, power3.in/out/inOut, power4.in/out/inOut,
  back.in/out/inOut, elastic.in/out/inOut, bounce.in/out/inOut,
  circ.in/out/inOut, expo.in/out/inOut, sine.in/out/inOut, none

  Pick by what the thing is doing, not by habit — reusing one easing for
  everything that moves is the fastest way to look like a slide deck, no
  matter how many elements are on screen. `.out` curves for things
  ARRIVING, `.in` curves for things LEAVING, `sine.inOut`/`none` for
  motion still running when the scene hands over. Use at least two
  distinct easings in this scene."""


SCENE_ROLES = ("HOOK", "REVEAL", "PROOF", "SIGNOFF")

_LAYER_DOCTRINE = """LAYERS — give every node a "layer" (not just a z_index guess):
  background — full-bleed wash, glow or gradient behind everything else.
               MUST carry "secondary_motion" running for the WHOLE scene
               (not just enter/exit) — a slow drift, pulse or rotation.
               A background that just sits there once it's in is exactly
               what makes a scene read as a held slide, not a shot.
  midground  — a supporting shape or glass panel that gives the scene
               depth. Move it slower than the foreground (a smaller
               secondary_motion amount, or a longer enter duration) so it
               reads as further back, not flat with everything else.
  foreground — the hero of the scene: the headline, the product/logo
               image. MUST either really "exit" or carry its own
               "secondary_motion" while it holds — appearing once and then
               going completely still until the cut is the one thing this
               layer is not allowed to do. A closing scene settling on a
               logo is fine; settling DEAD is not — give it a slow
               breathe/drift even then.
  accent     — small floating detail(s): a badge, a stat, a short label.
               Fast, energetic motion — this is what makes a frame feel
               busy/alive without competing with the foreground.
  finish     — an overlay (vignette, radial darken at the edges) that sits
               on top of everything. Static — it's a treatment, not a
               character.

Not every layer needs a node every scene, but background and foreground
are required — a scene with no background layer has no depth, and a
scene with no foreground has no subject."""


def _scene_role(idx: int) -> str:
    return SCENE_ROLES[idx] if 0 <= idx < len(SCENE_ROLES) else "SCENE"


_ROLE_BRIEF = {
    "HOOK": "The cold open. One bold claim or the brand/product name, "
            "nothing else competing for attention. Fast — this scene "
            "should feel like it's already moving when it appears.",
    "REVEAL": "The payoff. Whatever the hook promised, shown large — the "
              "product, the logo, the number. This is the scene the "
              "other three exist to set up and close out.",
    "PROOF": "One supporting detail that earns the claim — a benefit, a "
             "stat, a second angle. Calmer than the hook, still moving.",
    "SIGNOFF": "Logo lockup and/or a short tagline/CTA. The close — "
               "energy settles here, it does not spike.",
}


def _scene_handoff(scene: dict) -> dict | None:
    """What the LAST foreground node in a finished scene exits with — fed
    to the next scene's instructions so the cut continues a motion/colour
    instead of resetting cold. Returns None if there's nothing to hand
    off (no foreground node, or it never exits)."""
    best = None
    for node in scene.get("nodes", []) or []:
        if not isinstance(node, dict) or node.get("layer") != "foreground":
            continue
        anim = node.get("animation")
        exit_ = anim.get("exit") if isinstance(anim, dict) else None
        if isinstance(exit_, dict):
            best = {
                "type": exit_.get("type", "fade_in"),
                "fill": node.get("fill") or node.get("accent_color"),
            }
    return best


def storyboard_instructions(request: str, brand: dict | None = None,
                            skeleton: str | None = None) -> str:
    """Turn one: the look, the camera's overall intent, and a storyboard
    row per scene. Mirrors core.reel_web.design_instructions()'s split.

    `brand`: colours already measured off the client's own artwork (see
    core.reel.sample_brand — the same pixel-level measurement Reel/Studio
    use, not re-implemented here). Unlike Reel's Pillow renderer, nothing
    here applies these automatically — Motion's palette is the model's own
    choice — so it's told to use them as ITS accent rather than inventing
    one, the same way Studio is told to.
    """
    brand_note = ""
    if brand:
        brand_note = (
            f"\n\nThe brand colours have already been measured from the "
            f"client's own artwork: accent {brand.get('accent')}, deep "
            f"{brand.get('deep')}. Use these as the accent colour running "
            "through the piece rather than inventing your own — this is "
            "their actual brand, not a suggestion.")
    return (
        "You are Prism's Senior Visual Director and Motion Designer, "
        "planning a short vertical motion graphic.\n\n"
        f"WHAT THE CLIENT ASKED FOR:\n{request}"
        + brand_note + "\n\n"
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
        '"duration": 8.0, "background": "#07091A",\n'
        '    "palette": {"bg_a": "...", "bg_b": "...", "ink": "...", '
        '"accent": "...", "accent2": "..."},\n'
        '    "type": {"display_font": "...", "body_font": "...", '
        '"google_fonts_url": "https://fonts.googleapis.com/css2?family=...&display=block"}\n'
        "  },\n"
        '  "camera": {"tracks": [\n'
        '    {"time": 0.0, "position": [540, 960], "zoom": 1.0},\n'
        '    {"time": 1.2, "position": [540, 900], "zoom": 1.35, '
        '"duration": 1.1, "easing": "power2.inOut"}\n'
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
        + (_brand_launch_storyboard_close() if skeleton == "brand_launch"
           else
        "3-6 scenes, 6-15 seconds total. ONE STORYBOARD ROW PER SCENE. Give "
        "each one a different job and a different composition — several "
        "scenes that are all a centred headline over the same background "
        "is the failure this stage exists to prevent. `camera.tracks` is "
        "for the WHOLE graphic — rule 3 below still applies.")
    )


def _brand_launch_storyboard_close() -> str:
    roles = "\n".join(f'  {i + 1}. {r} — {_ROLE_BRIEF[r]}'
                       for i, r in enumerate(SCENE_ROLES))
    return (
        "EXACTLY 4 scenes, in this fixed order, 8-14 seconds total — do "
        "not add, drop, reorder or rename them:\n" + roles + "\n\n"
        '"job" for each row IS its role above, in your own words for this '
        "brand. `camera.tracks` is for the WHOLE graphic — rule 3 below "
        "still applies."
    )


def scene_instructions(idx: int, total: int, row: dict, assets: str = "",
                       skeleton: str | None = None,
                       handoff: dict | None = None) -> str:
    """Ask for ONE scene's nodes. The rest of the conversation already
    knows the palette and camera from turn one; this only needs the row.

    `skeleton="brand_launch"` swaps the freeform node catalogue for the
    layer doctrine (background/midground/foreground/accent/finish) and
    pins this scene to its fixed HOOK/REVEAL/PROOF/SIGNOFF role.
    `handoff` is what core.motion.generate._scene_handoff() read off the
    PREVIOUS scene — how it exited — so this one can continue that motion
    or colour instead of cutting cold; None for the first scene.
    """
    job = str(row.get("job", "")).strip() or "carry the argument forward"
    look = str(row.get("look", "")).strip()
    motion = str(row.get("motion", "")).strip()
    try:
        seconds = float(row.get("seconds") or 3.0)
    except (TypeError, ValueError):
        seconds = 3.0
    catalogue = _NODE_CATALOGUE
    rules = (
        "DESIGN RULES FOR THIS SCENE:\n"
        "1. 3-7 nodes. Do not overcrowd.\n"
        "2. Vary the text mode — use at most 2 different modes.\n"
        '3. Give at least one node a real "exit", not only "enter".\n'
        "4. The most common way a scene ends up reading as a PowerPoint "
        "slide, however busy it is, is every element using the same "
        "easing curve. Treat that as the failure to design against.\n"
        "5. Never use generic emojis in text content.\n\n"
        "6. The brand's accent colour should recur, not repeat identically —\n"
        "   the same one or two colours framing every single scene the same\n"
        "   way (same white headline, same accent subtitle, same background)\n"
        "   reads as one template stamped four times, not four scenes of one\n"
        "   film. Let where and how the accent is used change: a highlighted\n"
        "   word instead of a whole line, a filled shape instead of an\n"
        "   outline, a background wash instead of just text — same brand,\n"
        "   different weight each time.\n"
        "7. Use the palette/type chosen in turn one BY NAME — a full-bleed "
        "background node filled with project.palette.bg_a or bg_b (alternate "
        "which one scene to scene rather than repeating the same one every "
        "time), text filled with project.palette.ink or accent, "
        'font_family "var(--motion-display-font)" for headlines/numbers and '
        '"var(--motion-body-font)" for body/labels — never invent a fresh '
        "hex or font mid-scene that ignores what turn one already chose.\n"
        "8. This scene may name a \"transition_in\" (see TRANSITIONS above) "
        "for how it cuts in from the one before it — pick one that fits "
        "the beat, or leave it unset and a real one is still chosen for "
        "you rather than a hard cut.\n"
        "9. Before placing a text or image node, sketch its actual box — "
        "position ± roughly half its width/height — against every OTHER "
        "text/image node's box already placed this scene. Two photos, or "
        "a headline and a photo, sharing the same region reads as debris, "
        "not layout, no matter how good either looks alone. Give each one "
        "its own clear region of the 1080x1920 frame (stack vertically, "
        "or split left/right) rather than centering everything on the "
        "same point. A shape_rect used as an intentional backdrop directly "
        "behind one specific node (a badge behind its own label, a card "
        "behind its own photo) is the one exception — that pairing is "
        "supposed to share a position.\n\n"
    )
    role_header = ""
    if skeleton == "brand_launch":
        role = _scene_role(idx)
        catalogue = _NODE_CATALOGUE + "\n\n" + _LAYER_DOCTRINE
        rules = rules + (
            '7. Give every node a "layer" (see LAYERS above) — background '
            "and foreground are both required this scene.\n"
        )
        role_header = f"ROLE: {role} — {_ROLE_BRIEF[role]}\n\n"
        if handoff:
            role_header += (
                f'CONTINUING FROM THE LAST SCENE: it exited with a '
                f'"{handoff["type"]}"'
                + (f' in {handoff["fill"]}' if handoff.get("fill") else "")
                + ". Open THIS scene picking that motion or colour up — "
                "reverse the exit direction, or carry the colour into "
                "this scene's foreground — rather than starting cold.\n\n"
            )
    return (
        f"SCENE {idx + 1} of {total}.\n\n"
        + role_header
        + f"ITS JOB: {job}\n"
        + (f"THE LOOK: {look}\n" if look else "")
        + (f"THE MOTION: {motion}\n" if motion else "")
        + f"\nDuration: {seconds:g} seconds. All this scene's animation "
        "times count from 0 at this scene's own start.\n\n"
        + catalogue + "\n\n"
        + rules
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
               assets_table: dict | None = None,
               check=None, log=None, should_stop=None, on_scene=None,
               skeleton: str | None = None) -> dict:
    """Run the rest of the design conversation and return the finished spec.

    `ask(prompt, expect) -> str` sends a follow-up in the tab turn one is
    already sitting in. `check(spec) -> list[str]` reports concrete faults
    for a one-scene spec. Both are injected rather than imported, so this
    can be exercised without a browser — see core.reel_web.build_spec()'s
    docstring, which this mirrors exactly. `on_scene(index, total)` fires
    before each scene is asked for, for the same reason: this loop takes
    minutes, and nothing here raises once turn one has parsed.

    `assets` is the text description (core.assets.manifest()'s output)
    telling the model what `asset:<name>` it may put in an "image" node's
    "src". `assets_table` is the real {name: {"path": ...}} table behind
    those names — stashed on the returned spec as `_assets` (same
    convention core.reel_web uses) so resolve_motion_spec() can swap each
    `asset:name` for the real file before anything tries to render it.

    `skeleton="brand_launch"` must match what storyboard_instructions() was
    called with for `first_reply` — it switches each scene's prompt to the
    layer doctrine and pins the fixed HOOK/REVEAL/PROOF/SIGNOFF roles, and
    threads each finished scene's exit into the next scene's prompt as a
    handoff so consecutive cuts continue a motion instead of resetting.
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
    handoff: dict | None = None
    for i in range(total):
        if should_stop and should_stop():
            say("stopped — keeping the scenes written so far")
            break
        if on_scene:
            try:
                on_scene(i, total)
            except Exception:                        # noqa: BLE001
                pass          # a progress listener must never fail the run
        prompt = overflow_notice + scene_instructions(
            i, total, board[i], assets, skeleton=skeleton, handoff=handoff)
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
        if skeleton == "brand_launch":
            handoff = _scene_handoff(scene) or handoff
        say(f"scene {i + 1}/{total} written — "
            f"{len(scene.get('nodes', []))} node(s)")

    if not scenes:
        raise MotionValidationError("No scenes were written.")
    spec: dict[str, Any] = {"project": project, "scenes": scenes}
    if camera:
        spec["camera"] = camera
    if assets_table:
        spec["_assets"] = assets_table
    return spec
