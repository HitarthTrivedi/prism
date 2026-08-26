"""
Prism Motion Graphics Semantic Resolver
────────────────────────────────────────
Translates domain-specific targets, UI element IDs, and auto-framing targets
into unified scene-graph coordinates and animations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def resolve_motion_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Preprocesses and enriches a validated Motion Specification.
    - Resolves semantic targets in camera tracks
    - Computes bounding envelopes for auto-framing
    """
    spec = dict(spec)
    scenes = spec.get("scenes", [])
    camera = spec.get("camera", {})

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
