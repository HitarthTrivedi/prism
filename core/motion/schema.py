"""
Prism Motion Graphics Schema & Validator
────────────────────────────────────────
Defines and validates structured Motion Specifications (Motion JSON).
Decouples AI high-level intent from low-level frame rendering.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


class MotionValidationError(Exception):
    """Raised when a motion spec violates schema or structural rules."""
    pass


# GSAP's own core ease vocabulary (gsap.min.js, no plugins) — replaces the
# old hand-rolled easeInCubic/etc. names now that runtime.js hands these
# strings straight to gsap.to()'s own `ease` option instead of interpreting
# them itself. A base name optionally followed by GSAP's own parenthesized
# config, e.g. "back.out(1.7)" or "elastic.out(1,0.3)" — see _valid_easing.
SUPPORTED_EASINGS = {
    "none",
    "power1.in", "power1.out", "power1.inOut",
    "power2.in", "power2.out", "power2.inOut",
    "power3.in", "power3.out", "power3.inOut",
    "power4.in", "power4.out", "power4.inOut",
    "back.in", "back.out", "back.inOut",
    "elastic.in", "elastic.out", "elastic.inOut",
    "bounce.in", "bounce.out", "bounce.inOut",
    "circ.in", "circ.out", "circ.inOut",
    "expo.in", "expo.out", "expo.inOut",
    "sine.in", "sine.out", "sine.inOut",
}

_EASE_RE = re.compile(r"^([a-zA-Z0-9]+(?:\.[a-zA-Z]+)?)(\([0-9.,\s-]*\))?$")


def _valid_easing(value: Any) -> bool:
    """True for a known GSAP ease base name, with or without GSAP's own
    parenthesized config (back.out(1.7), elastic.out(1,0.3)) — the paren
    group is only shape-checked (digits/commas/dot/minus), GSAP itself
    validates the actual parameter values at tween time.
    """
    if not isinstance(value, str):
        return False
    m = _EASE_RE.match(value.strip())
    return bool(m) and m.group(1) in SUPPORTED_EASINGS


# The animatable surface every primitive shares — replaces the old closed
# enter/exit "type" enum (fade_in/pop_in/slide_up/slide_down) with channels
# a tween can target on ANY node, not just the four archetypes runtime.js
# used to hand-branch on. Each maps to a real CSS-expressible property in
# runtime.js's tween builder (opacity, transform components, filter:blur,
# clip-path inset, background-position-x for shimmer sweeps, and
# stroke-dashoffset for SVG draw-ins shared by charts/arrows/the brand mark).
TWEEN_CHANNELS = {
    "opacity", "x", "y", "scale", "scaleX", "scaleY", "rotation",
    "skewX", "skewY", "blur", "clipInset", "backgroundPositionX",
    "strokeDashoffset",
}

# Named, curated cross-scene transitions — ported from core.reel_web's own
# `.cut-*` library (push/push_up/squeeze/zoom, themselves adapted from
# HyperFrames under Apache 2.0, see prism_gui/NOTICE) plus two new ones
# translated from the Meridian HyperFrames reference (blur_swoosh,
# light_leak). A curated set, not free-text, for the same reason the
# easing fallback rotation is curated rather than open: a bounded, varied
# menu beats an unconstrained surface for consistency across scenes.
TRANSITION_NAMES = {
    "push", "push_up", "squeeze", "zoom", "blur_swoosh", "light_leak",
}

# Keyed by the keyword with every space/hyphen/underscore stripped and
# lowercased, so "top-left", "top_left", "Top Left" and "topleft" all match
# the same entry — an LLM asked for an anchor is about as likely to write
# any of those forms as the others.
# A node's "layer" is an optional semantic label — background / midground /
# foreground / accent / finish — that, if given, sets a sensible z_index
# band automatically so the model can say WHAT something is instead of
# picking an arbitrary stacking number. Entirely opt-in: a node with no
# "layer" behaves exactly as before (z_index still defaults to 0). This
# only exists to back the "brand_launch" skeleton in generate.py — every
# other request path never sets it and is unaffected.
_LAYER_Z_DEFAULT = {
    "background": 0,
    "midground": 10,
    "foreground": 20,
    "accent": 30,
    "finish": 40,
}

_ANCHOR_KEYWORDS = {
    "center": (0.5, 0.5), "middle": (0.5, 0.5),
    "top": (0.5, 0.0), "topcenter": (0.5, 0.0), "topmiddle": (0.5, 0.0),
    "bottom": (0.5, 1.0), "bottomcenter": (0.5, 1.0), "bottommiddle": (0.5, 1.0),
    "left": (0.0, 0.5), "centerleft": (0.0, 0.5), "middleleft": (0.0, 0.5),
    "right": (1.0, 0.5), "centerright": (1.0, 0.5), "middleright": (1.0, 0.5),
    "topleft": (0.0, 0.0), "lefttop": (0.0, 0.0),
    "topright": (1.0, 0.0), "righttop": (1.0, 0.0),
    "bottomleft": (0.0, 1.0), "leftbottom": (0.0, 1.0),
    "bottomright": (1.0, 1.0), "rightbottom": (1.0, 1.0),
}


def _normalize_anchor(value: Any) -> list[float]:
    """A node's anchor, coerced to a real [x, y] pair.

    Accepts what schema.py always accepted (a 2-number list/tuple) and,
    now, common keyword strings a CSS-literate model reaches for instead
    ("center", "top-left", ...) — mapped to the equivalent fraction rather
    than just rejected, since that's what was actually meant. Anything
    else (missing, malformed, an unrecognized word) falls back to
    [0.5, 0.5] instead of passing a broken value through: see
    _validate_node's comment for exactly how destructive that is
    downstream (NaN positions from spreading a string in JS).
    """
    if isinstance(value, str):
        key = value.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        if key in _ANCHOR_KEYWORDS:
            x, y = _ANCHOR_KEYWORDS[key]
            return [x, y]
        return [0.5, 0.5]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return [float(value[0]), float(value[1])]
        except (TypeError, ValueError):
            pass
    return [0.5, 0.5]


def _validate_tween(tween: Any) -> Optional[dict]:
    """One {channel, from, to, easing, delay} entry inside an enter/exit
    block's "tweens" list. Returns None (drop silently) if the channel is
    missing/unrecognized or from/to aren't numbers — one bad tween must
    not invalidate its siblings, same tolerant-but-structural philosophy
    as the rest of this file.
    """
    if not isinstance(tween, dict):
        return None
    channel = tween.get("channel")
    if channel not in TWEEN_CHANNELS:
        return None
    try:
        frm = float(tween["from"])
        to = float(tween["to"])
    except (KeyError, TypeError, ValueError):
        return None
    out: dict[str, Any] = {"channel": channel, "from": frm, "to": to}
    easing = tween.get("easing")
    if easing and _valid_easing(easing):
        out["easing"] = easing
    if tween.get("delay") is not None:
        try:
            out["delay"] = max(0.0, float(tween["delay"]))
        except (TypeError, ValueError):
            pass
    return out


def _validate_animation_block(block: Any) -> Optional[dict]:
    """One enter/exit block: {time, duration, tweens: [...]}. A block with
    no valid tweens left after filtering is dropped entirely — an enter/
    exit that ends up animating nothing is not a real block, same as
    before when a malformed "type" fell through.
    """
    if not isinstance(block, dict):
        return None
    tweens_in = block.get("tweens")
    if not isinstance(tweens_in, list):
        return None
    tweens = [t for t in (_validate_tween(t) for t in tweens_in) if t]
    if not tweens:
        return None
    out: dict[str, Any] = {"tweens": tweens}
    try:
        out["time"] = float(block.get("time", 0.0))
    except (TypeError, ValueError):
        out["time"] = 0.0
    try:
        out["duration"] = max(0.05, float(block.get("duration", 0.6)))
    except (TypeError, ValueError):
        out["duration"] = 0.6
    return out


def validate_motion_spec(data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Validate raw JSON or dict against the Prism Motion Graphics Schema.
    Returns a cleaned, normalized specification dict or raises MotionValidationError.

    Design principle: be maximally tolerant. Unknown fields are preserved as-is
    so the AI can freely express visual parameters. Only structural invariants are enforced.
    """
    if isinstance(data, str):
        try:
            spec = json.loads(data)
        except json.JSONDecodeError as e:
            raise MotionValidationError(f"Invalid JSON string: {e}") from e
    elif isinstance(data, dict):
        spec = dict(data)
    else:
        raise MotionValidationError(f"Expected dict or JSON string, got {type(data).__name__}")

    # ── Visual block: fully open passthrough ─────────────────────────────────
    # The AI specifies any visual parameters it wants. We never restrict this.
    visual = spec.get("visual")
    if visual is not None and not isinstance(visual, dict):
        spec["visual"] = {}  # safe reset if AI sent a non-dict by mistake

    # ── 1. Project metadata ───────────────────────────────────────────────────
    project = spec.get("project")
    if not isinstance(project, dict):
        project = {}
        spec["project"] = project

    # If visual.background is set, let it populate project.background too
    if visual and isinstance(visual, dict) and "background" in visual:
        project.setdefault("background", visual["background"])

    project.setdefault("width", 1080)
    project.setdefault("height", 1920)
    project.setdefault("fps", 30)
    project.setdefault("duration", 10.0)
    project.setdefault("background", "#090D16")

    # A small NAMED palette (not just background+one accent) and a real
    # display/body font pairing — threaded through every scene so a whole
    # generation shares one identity instead of each scene picking its own
    # colors/font fresh. Both are open string values (any hex, any Google
    # Font name) — the model's actual choice is steered by doctrine in
    # generate.py's prompt, not constrained here; this layer only enforces
    # the SHAPE (right keys, string values) so downstream CSS generation
    # never chokes on a non-string.
    palette = project.get("palette")
    if not isinstance(palette, dict):
        palette = {}
    for key in ("bg_a", "bg_b", "ink", "accent", "accent2"):
        if key in palette and not isinstance(palette[key], str):
            del palette[key]
    project["palette"] = palette

    type_cfg = project.get("type")
    if not isinstance(type_cfg, dict):
        type_cfg = {}
    for key in ("display_font", "body_font", "google_fonts_url"):
        if key in type_cfg and not isinstance(type_cfg[key], str):
            del type_cfg[key]
    project["type"] = type_cfg

    width    = int(project["width"])
    height   = int(project["height"])
    fps      = int(project["fps"])
    duration = float(project["duration"])

    if width < 320 or width > 3840:
        raise MotionValidationError(f"Invalid width {width}. Must be between 320 and 3840.")
    if height < 320 or height > 3840:
        raise MotionValidationError(f"Invalid height {height}. Must be between 320 and 3840.")
    if fps < 10 or fps > 120:
        raise MotionValidationError(f"Invalid fps {fps}. Must be between 10 and 120.")
    if duration <= 0.5 or duration > 300.0:
        raise MotionValidationError(f"Invalid duration {duration}s. Must be between 0.5s and 300s.")

    # ── 2. Camera validation ──────────────────────────────────────────────────
    camera = spec.get("camera")
    if camera is not None and not isinstance(camera, dict):
        raise MotionValidationError("camera must be an object if provided.")
    if camera:
        tracks = camera.get("tracks", [])
        if not isinstance(tracks, list):
            raise MotionValidationError("camera.tracks must be a list.")
        for i, track in enumerate(tracks):
            if not isinstance(track, dict):
                raise MotionValidationError(f"camera.tracks[{i}] must be an object.")
            track.setdefault("time", 0.0)
            if "zoom" in track:
                try:
                    z = float(track["zoom"])
                    track["zoom"] = max(0.1, min(8.0, z))  # clamp silently
                except (ValueError, TypeError):
                    track["zoom"] = 1.0  # safe default
            if "easing" in track:
                if not _valid_easing(track["easing"]):
                    track["easing"] = "power2.inOut"  # silent fallback, GSAP-native
            if "position" in track:
                pos = track["position"]
                if not (isinstance(pos, (list, tuple)) and len(pos) == 2):
                    raise MotionValidationError(f"camera track position must be [x, y], got {pos}")

    # ── 3. Scenes / Nodes ─────────────────────────────────────────────────────
    scenes = spec.get("scenes")
    if scenes is None:
        if "nodes" in spec and isinstance(spec["nodes"], list):
            spec["scenes"] = [{
                "id": "scene_0",
                "start": 0.0,
                "duration": duration,
                "nodes": spec["nodes"]
            }]
            scenes = spec["scenes"]
        else:
            raise MotionValidationError("Motion specification must contain a 'scenes' list or 'nodes' list.")

    if not isinstance(scenes, list) or not scenes:
        raise MotionValidationError("'scenes' must be a non-empty list.")

    node_ids: set[str] = set()

    def _validate_node(node: Any, path: str):
        if not isinstance(node, dict):
            raise MotionValidationError(f"{path} must be an object.")
        node_id = str(node.get("id") or f"node_{len(node_ids)}")
        node["id"] = node_id
        if node_id in node_ids:
            node_id = f"{node_id}_{len(node_ids)}"
            node["id"] = node_id
        node_ids.add(node_id)

        node_type = str(node.get("type", "group"))
        node["type"] = node_type

        node.setdefault("position", [0, 0])
        node.setdefault("scale",    [1.0, 1.0])
        node.setdefault("rotation", 0.0)
        node.setdefault("opacity",  1.0)
        # Layer-based z_index default has to run BEFORE the plain
        # setdefault below, since setdefault only fills a missing key —
        # whichever runs first wins. An unrecognized "layer" string is
        # dropped rather than guessed at, same as an invalid easing name.
        layer = node.get("layer")
        if isinstance(layer, str) and layer in _LAYER_Z_DEFAULT:
            node.setdefault("z_index", _LAYER_Z_DEFAULT[layer])
        elif "layer" in node:
            del node["layer"]
        node.setdefault("z_index",  0)
        # setdefault alone isn't enough for anchor — it only fills a MISSING
        # key, and a wrong-TYPE one (present, just broken) sails through
        # untouched. Measured on a real generated reel: every image node
        # used anchor: "center" (a bare string, not [0.5, 0.5]) — a
        # reasonable guess for someone used to CSS-style keyword anchors,
        # and genuinely destructive downstream: runtime.js's Node
        # constructor does `[...props.anchor]`, and spreading a STRING
        # produces an array of its individual CHARACTERS ('c','e','n',...),
        # so anchor[0]/[1] become non-numeric and every position multiply
        # against them is NaN — the node silently never appears anywhere.
        node["anchor"] = _normalize_anchor(node.get("anchor"))

        # animation.enter / animation.exit are now a composable list of
        # {channel, from, to, easing, delay} tweens rather than a closed
        # "type" enum (fade_in/pop_in/slide_up/slide_down) — see
        # TWEEN_CHANNELS above. A block that fails validation entirely
        # (missing/malformed) is dropped, not coerced to a default type,
        # since there's no longer a "type" to fall back to.
        anim = node.get("animation")
        if isinstance(anim, dict):
            for block_name in ("enter", "exit"):
                validated = _validate_animation_block(anim.get(block_name))
                if validated:
                    anim[block_name] = validated
                elif block_name in anim:
                    del anim[block_name]

            secondary = anim.get("secondary_motion")
            if isinstance(secondary, dict):
                for numeric_key in ("freq", "amount"):
                    if numeric_key in secondary:
                        try:
                            secondary[numeric_key] = float(secondary[numeric_key])
                        except (TypeError, ValueError):
                            del secondary[numeric_key]

            follow = anim.get("follow")
            if isinstance(follow, dict):
                for numeric_key in ("lag", "damping"):
                    if numeric_key in follow:
                        try:
                            follow[numeric_key] = float(follow[numeric_key])
                        except (TypeError, ValueError):
                            del follow[numeric_key]

        if "children" in node:
            if not isinstance(node["children"], list):
                raise MotionValidationError(f"{path}.children must be a list.")
            for c_idx, child in enumerate(node["children"]):
                _validate_node(child, f"{path}.children[{c_idx}]")

    for s_idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise MotionValidationError(f"scenes[{s_idx}] must be an object.")
        scene.setdefault("id", f"scene_{s_idx}")
        scene.setdefault("start", 0.0)
        scene.setdefault("duration", duration)
        # Which named transition (see TRANSITION_NAMES) plays as this scene
        # cuts in from the PREVIOUS one — absent/invalid means resolver.py
        # picks one via the same seeded-rotation approach the easing
        # fallback already uses, rather than defaulting every unset scene
        # to the identical transition. Meaningless (and dropped) on scene 0,
        # which has nothing before it to cut in from.
        t_in = scene.get("transition_in")
        if s_idx == 0 or t_in not in TRANSITION_NAMES:
            scene.pop("transition_in", None)
        nodes = scene.get("nodes", [])
        if not isinstance(nodes, list):
            raise MotionValidationError(f"scenes[{s_idx}].nodes must be a list.")
        for n_idx, node in enumerate(nodes):
            _validate_node(node, f"scenes[{s_idx}].nodes[{n_idx}]")

    return spec



@dataclass
class MotionProject:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    duration: float = 10.0
    background: str = "#090D16"
    scenes: List[Dict[str, Any]] = field(default_factory=list)
    camera: Optional[Dict[str, Any]] = None
    assets: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MotionProject":
        valid = validate_motion_spec(data)
        p = valid["project"]
        return cls(
            width=p["width"],
            height=p["height"],
            fps=p["fps"],
            duration=p["duration"],
            background=p["background"],
            scenes=valid.get("scenes", []),
            camera=valid.get("camera"),
            assets=valid.get("assets", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "duration": self.duration,
                "background": self.background,
            },
            "scenes": self.scenes,
            "camera": self.camera,
            "assets": self.assets,
        }
