"""
Prism Motion Graphics Schema & Validator
────────────────────────────────────────
Defines and validates structured Motion Specifications (Motion JSON).
Decouples AI high-level intent from low-level frame rendering.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


class MotionValidationError(Exception):
    """Raised when a motion spec violates schema or structural rules."""
    pass


SUPPORTED_EASINGS = {
    "linear",
    "easeInQuad", "easeOutQuad", "easeInOutQuad",
    "easeInCubic", "easeOutCubic", "easeInOutCubic",
    "easeInExpo", "easeOutExpo", "easeInOutExpo",
    "back.in", "back.out", "back.inOut",
    "elastic.in", "elastic.out", "elastic.inOut",
    "bounce.in", "bounce.out", "bounce.inOut",
    "spring", "smooth"
}

# Keyed by the keyword with every space/hyphen/underscore stripped and
# lowercased, so "top-left", "top_left", "Top Left" and "topleft" all match
# the same entry — an LLM asked for an anchor is about as likely to write
# any of those forms as the others.
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
                if track["easing"] not in SUPPORTED_EASINGS:
                    track["easing"] = "easeInOutCubic"  # silent fallback
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

        # Sanitize easing strings in animation blocks silently. An invalid
        # name is DROPPED, not rewritten to a fixed curve — forcing every
        # bad value to the same "easeOutCubic" was a second, code-level
        # copy of the exact anchoring bug measured in reel_web.py's prompt
        # (every unclear case collapsing onto one identical curve). Leaving
        # it unset lets runtime.js's own seeded rotation pick a fallback
        # instead, which varies per node/property rather than reintroducing
        # a single dominant curve from the Python side.
        anim = node.get("animation")
        if isinstance(anim, dict):
            for block in ("enter", "exit"):
                b = anim.get(block)
                if not isinstance(b, dict):
                    continue
                e = b.get("easing")
                if e and e not in SUPPORTED_EASINGS:
                    del b["easing"]
                # Per-property easing overrides — e.g. {"scale": {"easing":
                # "back.out"}} — validated the same way, property by
                # property, so one bad value doesn't drop the whole map.
                props = b.get("properties")
                if isinstance(props, dict):
                    for prop_name, prop_cfg in list(props.items()):
                        if not isinstance(prop_cfg, dict):
                            continue
                        pe = prop_cfg.get("easing")
                        if pe and pe not in SUPPORTED_EASINGS:
                            del prop_cfg["easing"]

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
