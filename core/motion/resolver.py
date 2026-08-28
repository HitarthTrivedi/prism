"""
Prism Motion Graphics Semantic Resolver
────────────────────────────────────────
Translates domain-specific targets, UI element IDs, and auto-framing targets
into unified scene-graph coordinates and animations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Same curated set schema.py validates transition_in against. Rotation
# (hash of the scene's own id, not Math.random/an incrementing counter) so
# a scene missing transition_in still gets a real one deterministically —
# every cut gets a genuine transition by default, matching the reference
# this engine targets (the Meridian benchmark has zero hard cuts), the same
# "don't let an unset value silently mean the flattest option" fix already
# applied to easing fallback in schema.py/runtime.js.
_TRANSITION_ROTATION = ["push", "blur_swoosh", "light_leak", "push_up", "squeeze", "zoom"]


def _hash_seed(seed: str) -> int:
    h = 0
    for ch in str(seed):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def _desugar_ui_mockup(node: Dict[str, Any]) -> Dict[str, Any]:
    """A domain_ui_mockup node was never a real runtime primitive — it's
    always been composable from shape_rect (the window chrome) + shape_rect/
    text children (its `elements`), so it's expanded here, before the spec
    ever reaches the runtime, rather than teaching runtime.js a type it
    doesn't need. Keeps the real primitive count small while the schema/
    prompt vocabulary the model sees stays rich.
    """
    width = float(node.get("width", 860))
    height = float(node.get("height", 520))
    title = node.get("title", "")
    chrome: Dict[str, Any] = {
        "id": f"{node.get('id', 'ui')}_chrome",
        "type": "shape_rect",
        "position": list(node.get("position", [0, 0])),
        "width": width, "height": height, "radius": 18,
        "is_glass": True,
        "z_index": node.get("z_index", 0),
        "anchor": list(node.get("anchor", [0.5, 0.5])),
        "animation": node.get("animation"),
        "children": [],
    }
    if title:
        chrome["children"].append({
            "id": f"{node.get('id', 'ui')}_title",
            "type": "text", "content": title, "font_size": 22, "font_weight": 600,
            "fill": "rgba(255,255,255,0.85)",
            "position": [-width / 2 + 24, -height / 2 + 30], "anchor": [0, 0.5],
        })
    for i, el in enumerate(node.get("elements", []) or []):
        el_pos = el.get("position", [0, 0])
        if el.get("type") == "stat_card":
            chrome["children"].append({
                "id": f"{node.get('id', 'ui')}_el{i}_card",
                "type": "shape_rect", "position": list(el_pos), "width": 200, "height": 90,
                "radius": 12, "fill": "rgba(255,255,255,0.06)",
            })
            chrome["children"].append({
                "id": f"{node.get('id', 'ui')}_el{i}_val",
                "type": "text", "content": str(el.get("value", "")), "font_size": 28,
                "font_weight": 700, "fill": el.get("color", "#FFFFFF"), "position": list(el_pos),
            })
        else:  # badge / generic label
            chrome["children"].append({
                "id": f"{node.get('id', 'ui')}_el{i}",
                "type": "text", "content": str(el.get("label", "")), "font_size": 18,
                "font_weight": 600, "fill": el.get("color", "#FFFFFF"), "position": list(el_pos),
            })
    return chrome


def _resolve_asset_srcs(node: Dict[str, Any], uris: Dict[str, str]) -> None:
    """Swap an `image` node's `src: "asset:<name>"` for the real data: URI,
    in place, recursively. Naturally idempotent — once resolved, `src` no
    longer starts with `asset:`, so re-running this (e.g. re-rendering an
    already-resolved saved spec) is a no-op, unlike the time-offset step
    below, which needs an explicit guard because it's additive rather than
    a plain replace.
    """
    src = node.get("src")
    if isinstance(src, str) and src.startswith("asset:"):
        name = src[len("asset:"):]
        if name in uris:
            node["src"] = uris[name]
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _resolve_asset_srcs(child, uris)


def _offset_node_times(node: Dict[str, Any], offset: float) -> None:
    """Shift a node's (and its children's) authored animation times by
    `offset`, in place. Scenes are written scene-local (a node's `enter`/
    `exit`/track keyframes count from 0 at that scene's own start,
    matching how core.reel_web's per-scene prompts work) — this is what
    turns that into the single global timeline runtime.js actually plays.
    """
    anim = node.get("animation")
    if isinstance(anim, dict):
        for block_name in ("enter", "exit"):
            block = anim.get(block_name)
            if isinstance(block, dict) and "time" in block:
                try:
                    block["time"] = float(block["time"]) + offset
                except (TypeError, ValueError):
                    pass
        tracks = anim.get("tracks")
        if isinstance(tracks, list):
            for track in tracks:
                keyframes = track.get("keyframes") if isinstance(track, dict) else None
                if isinstance(keyframes, list):
                    for kf in keyframes:
                        if isinstance(kf, dict) and "time" in kf:
                            try:
                                kf["time"] = float(kf["time"]) + offset
                            except (TypeError, ValueError):
                                pass
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _offset_node_times(child, offset)


def resolve_motion_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Preprocesses and enriches a validated Motion Specification.
    - Resolves semantic targets in camera tracks
    - Computes bounding envelopes for auto-framing
    - Lays scenes out sequentially on one global timeline: each scene's
      `start` is recomputed as a running total of the scenes before it
      (schema.py defaults every scene's `start` to 0.0, which is only ever
      right for the first one), and every node's authored, scene-local
      animation times are shifted to match — the renderer has no per-scene
      clock of its own, it plays one continuous timeline and uses
      `start`/`duration` only to decide which scene's nodes are visible
      when.
    - Swaps every `image` node's `src: "asset:<name>"` for the real file,
      inlined as a data: URI (same reasoning as core.reel_web's
      _asset_uris(): the runtime has no base URL to resolve a file:// path
      or bare filename against, and inlining means a saved spec is the
      whole motion graphic — re-render it later with no AI or filesystem
      dependency).
    """
    spec = dict(spec)
    scenes = spec.get("scenes", [])
    camera = spec.get("camera", {})

    assets_table = spec.get("_assets")
    if assets_table:
        from .. import reel_web as _web
        uris = _web._asset_uris(assets_table)
        if uris:
            for scene in scenes:
                for node in scene.get("nodes", []) or []:
                    if isinstance(node, dict):
                        _resolve_asset_srcs(node, uris)

    # Guarded the same way camera focus_target resolution already is below
    # (`if target and "position" not in track`) — a saved spec is meant to
    # re-render identically forever, including a second pass through THIS
    # function on an already-resolved spec (e.g. render() calling it fresh
    # from a .json file that build_spec() already resolved once). Without
    # the guard, node times shift by each scene's start a second time and
    # drift further every re-render.
    if not spec.get("_scene_times_resolved"):
        cursor = 0.0
        for scene in scenes:
            try:
                duration = float(scene.get("duration", 0.0) or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            scene["start"] = cursor
            # domain_ui_mockup was never a real primitive runtime.js knows
            # how to paint — expand it into shape_rect + text children here,
            # once, before node times get offset below (so its children's
            # own animation, if any, is offset along with everything else).
            nodes = scene.get("nodes", []) or []
            for i, node in enumerate(nodes):
                if isinstance(node, dict) and node.get("type") in ("domain_ui_mockup", "domain_ui", "ui_mockup"):
                    nodes[i] = _desugar_ui_mockup(node)
            for node in nodes:
                if isinstance(node, dict):
                    _offset_node_times(node, cursor)
            cursor += duration
        spec["_scene_times_resolved"] = True

    # Cross-scene transitions: each scene (after the first) that names a
    # transition_in — or, absent one, gets a deterministic default so every
    # cut is a real transition rather than silently defaulting to a hard
    # one — gets a computed overlap window with the scene before it. This
    # does NOT move where a scene's own content is timed (still `scene.
    # start`, offset above); it only widens each scene's VISIBILITY window
    # so both scenes coexist during the cut, mirroring core.reel_web.py's
    # own _plan() (same overlap cap: a third of either neighbor's own
    # duration, so a short scene is never swallowed by its own transition).
    if not spec.get("_transitions_resolved"):
        for i in range(1, len(scenes)):
            this_scene, prev_scene = scenes[i], scenes[i - 1]
            try:
                prev_dur = float(prev_scene.get("duration", 0.0) or 0.0)
                this_dur = float(this_scene.get("duration", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            overlap = min(0.5, prev_dur / 3, this_dur / 3)
            if overlap <= 0.02:
                continue  # a scene too short to safely overlap keeps a hard cut
            t_in = this_scene.get("transition_in")
            if not t_in:
                t_in = _TRANSITION_ROTATION[_hash_seed(this_scene.get("id", i)) % len(_TRANSITION_ROTATION)]
                this_scene["transition_in"] = t_in
            cut_at = this_scene["start"]
            this_scene["transitionInStart"] = round(cut_at - overlap, 3)
            prev_scene["transitionOutEnd"] = round(cut_at + overlap * 0.4, 3)
            this_scene["_transitionOverlap"] = round(overlap, 3)
        spec["_transitions_resolved"] = True

    node_registry: Dict[str, Dict[str, Any]] = {}

    def _index(nodes: List[Dict[str, Any]], parent_pos: Tuple[float, float] = (0.0, 0.0)):
        for node in nodes:
            nid = node.get("id", "")
            pos = node.get("position", [0, 0])
            abs_x = parent_pos[0] + float(pos[0])
            abs_y = parent_pos[1] + float(pos[1])
            node_registry[nid] = {
                "node": node,
                "world_pos": (abs_x, abs_y),
            }
            if "children" in node and isinstance(node["children"], list):
                _index(node["children"], (abs_x, abs_y))

    for scene in scenes:
        _index(scene.get("nodes", []))

    if camera and "tracks" in camera and isinstance(camera["tracks"], list):
        for track in camera["tracks"]:
            target = track.get("focus_target")
            if target and "position" not in track:
                if target in node_registry:
                    wx, wy = node_registry[target]["world_pos"]
                    track["position"] = [round(wx, 2), round(wy, 2)]

    return spec
