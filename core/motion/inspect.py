"""
Prism Motion Graphics — per-scene verification
────────────────────────────────────────────────
Checks a SPECIFIC claim a scene's own spec makes against its resolved
geometry — not a generic style rule. This is the MoVer lesson, not the
motion_faults() one: core.reel_web's motion_faults() (reverted — see
motion-plan.md) checked "does one animation dominate" and got gamed by
scattering weak animations to dodge the number. Nothing here checks
whether a scene is *good*; each check answers a factual yes/no that a
model can't cheaply fake — a node is either inside the frame or it isn't,
an entrance either moves the value or it doesn't.

Pure Python, no browser. core.reel_web.inspect() has to launch Chromium
because its ground truth (real HTML/CSS layout) only exists once the page
renders. Motion's ground truth — position, size, animation timing — is
already sitting in the spec before a single frame is drawn, which is
exactly the advantage a from-scratch scene graph has over filming a page.
"""
from __future__ import annotations

from typing import Any


def _node_bounds(node: dict, canvas_w: int, canvas_h: int) -> tuple[float, float, float, float] | None:
    """Approximate (left, top, right, bottom) for one node's AUTHORED
    (pre-animation, settled) position — good enough to catch "this is
    nowhere near the frame", not meant to be pixel-exact. Returns None for
    a node with no usable position.
    """
    pos = node.get("position")
    if not (isinstance(pos, (list, tuple)) and len(pos) == 2):
        return None
    try:
        x, y = float(pos[0]), float(pos[1])
    except (TypeError, ValueError):
        return None
    anchor = node.get("anchor") or [0.5, 0.5]
    try:
        ax, ay = float(anchor[0]), float(anchor[1])
    except (TypeError, ValueError, IndexError):
        ax, ay = 0.5, 0.5

    node_type = node.get("type", "")
    if node_type == "text":
        content = str(node.get("content", ""))
        font_size = float(node.get("font_size", 48) or 48)
        # Rough average glyph width — this is a coarse proxy, not a real
        # text layout; it only needs to catch positions nowhere near the
        # canvas, not exact wrap points.
        w = max(font_size, len(content) * font_size * 0.55)
        h = font_size * 1.3
    elif node_type == "shape_arrow":
        frm = node.get("from") or [x, y]
        to = node.get("to") or [x, y]
        try:
            xs = [float(frm[0]), float(to[0])]
            ys = [float(frm[1]), float(to[1])]
        except (TypeError, ValueError, IndexError):
            xs, ys = [x], [y]
        return (min(xs), min(ys), max(xs), max(ys))
    else:
        # shape_rect / domain_chart / domain_ui_mockup / domain_diagram all
        # carry explicit width/height in the spec (defaults match
        # runtime/primitives.js's own JS defaults, so a check here means
        # the same thing the renderer would actually draw).
        w = float(node.get("width", 200) or 200)
        h = float(node.get("height", 200) or 200)

    left = x - w * ax
    top = y - h * ay
    return (left, top, left + w, top + h)


def _entrance_faults(node: dict, scene_duration: float) -> list[str]:
    """Does this node's declared enter/exit actually do what it claims?
    Every check here is about the DECLARATION being internally consistent
    with itself and the scene's own duration — not a taste judgment.
    """
    faults: list[str] = []
    nid = node.get("id", "?")
    anim = node.get("animation")
    if not isinstance(anim, dict):
        return faults

    enter = anim.get("enter")
    if isinstance(enter, dict):
        try:
            e_time = float(enter.get("time", 0.0) or 0.0)
            e_dur = float(enter.get("duration", 0.6) or 0.6)
        except (TypeError, ValueError):
            e_time, e_dur = 0.0, 0.6
        if e_time >= scene_duration:
            faults.append(f'node "{nid}" enters at {e_time:g}s but the '
                          f"scene is only {scene_duration:g}s — it never appears")
        elif e_dur <= 0.05:
            faults.append(f'node "{nid}" enters over {e_dur:g}s — that is '
                          "instant, not an entrance; give it real duration")
        e_type = enter.get("type")
        if e_type in ("slide_up", "slide_down"):
            try:
                distance = abs(float(enter.get("distance", 60) or 60))
            except (TypeError, ValueError):
                distance = 60
            if distance < 5:
                faults.append(f'node "{nid}"\'s {e_type} moves only '
                              f"{distance:g}px — too small to read as motion")
        if e_type == "fade_in":
            try:
                target_opacity = float(node.get("opacity", 1.0))
            except (TypeError, ValueError):
                target_opacity = 1.0
            if target_opacity <= 0.05:
                faults.append(f'node "{nid}" fades in to opacity '
                              f"{target_opacity:g} — effectively invisible")

    exit_ = anim.get("exit")
    if isinstance(exit_, dict) and "time" in exit_:
        try:
            x_time = float(exit_["time"])
            x_dur = float(exit_.get("duration", 0.6) or 0.6)
        except (TypeError, ValueError):
            x_time, x_dur = None, 0.6
        if x_time is not None:
            if x_time >= scene_duration:
                faults.append(f'node "{nid}" exits at {x_time:g}s but the '
                              f"scene ends at {scene_duration:g}s — the exit "
                              "never plays")
            elif x_time + x_dur > scene_duration + 0.05:
                faults.append(f'node "{nid}"\'s exit runs past the scene\'s '
                              f"own end ({x_time + x_dur:g}s vs "
                              f"{scene_duration:g}s) — it will be cut off "
                              "mid-motion")
    return faults


def inspect(spec: dict[str, Any]) -> list[str]:
    """Check one scene (spec["scenes"] has exactly one, the shape
    core.motion.generate.build_spec() checks with) and report concrete,
    checkable faults. Empty list means nothing objective was found wrong
    — not a claim that the scene is good, only that it isn't broken.
    """
    project = spec.get("project") or {}
    try:
        canvas_w = int(project.get("width", 1080) or 1080)
        canvas_h = int(project.get("height", 1920) or 1920)
    except (TypeError, ValueError):
        canvas_w, canvas_h = 1080, 1920

    faults: list[str] = []
    for scene in spec.get("scenes", []):
        try:
            duration = float(scene.get("duration", 0.0) or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        for node in scene.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            nid = node.get("id", "?")
            bounds = _node_bounds(node, canvas_w, canvas_h)
            if bounds:
                left, top, right, bottom = bounds
                if right <= 0 or bottom <= 0 or left >= canvas_w or top >= canvas_h:
                    faults.append(
                        f'node "{nid}" sits entirely outside the '
                        f"{canvas_w}x{canvas_h} frame (approx. bounds "
                        f"{left:.0f},{top:.0f} to {right:.0f},{bottom:.0f})")
            if duration:
                faults.extend(_entrance_faults(node, duration))
    return faults
