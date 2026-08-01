"""
Prism — programmatic reel renderer (/reel)
───────────────────────────────────────────
Makes a finished vertical reel locally: Pillow draws every frame, FFmpeg
encodes them. No browser, no Node, no Remotion licence, no per-render fee,
no watermark, no daily credit cap.

Why this exists rather than a generative video tool:

  · Generative models (Google Flow, Kling, Runway) return a few seconds of
    LANDSCAPE footage and cannot render readable text. Every caption, price
    and phone number comes out garbled. They make b-roll, not deliverables.
  · A reel is 9:16, has the client's exact brand colours, and is mostly
    typography and motion graphics — all of which are drawing operations,
    not generation problems.

So this module owns the ASSEMBLY step: the part that turns shots and words
into something a client can post. Generated footage can be composited in
later; the text, brand marks and layout are always drawn here, where they
are exact and repeatable.

Same split as core/boq.py: code produces the artefact deterministically, an
AI stage only decides what goes in it (the scene spec).
"""
from __future__ import annotations
import math
import os
import shutil
import subprocess

W, H, FPS = 1080, 1920, 30
SAFE_X, SAFE_Y = 90, 130

_FONT_CANDIDATES = {
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "regular": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
}


class ReelError(Exception):
    pass


def _font(weight: str, size: int):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES[weight]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ReelError(
            "FFmpeg is not installed — it does the encoding.\n"
            "  macOS:   brew install ffmpeg\n"
            "  Debian:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html")
    return exe


# ── easing & helpers ────────────────────────────────────────────────────────

def ease(t: float) -> float:
    """Same curve as the Remotion prototype (a strong ease-out) so motion
    feels deliberate rather than linear/robotic."""
    t = max(0.0, min(1.0, t))
    return 1 - pow(1 - t, 3)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def fade_in(frame: int, start: int, length: int = 14) -> float:
    return ease((frame - start) / max(1, length))


def rise(frame: int, start: int, dist: int = 40, length: int = 18) -> int:
    return int(dist * (1 - ease((frame - start) / max(1, length))))


def hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def blend(fg, bg, alpha: float):
    a = max(0.0, min(1.0, alpha))
    return tuple(int(round(f * a + b * (1 - a))) for f, b in zip(fg, bg))


# ── brand ───────────────────────────────────────────────────────────────────

DEFAULT_BRAND = {
    "accent": "#68C04F",
    "deep": "#4B8A5D",
    "ink": "#1A1A1A",
    "grey": "#6B6B6B",
    "line": "#D8E6DA",
    "bg": "#FFFFFF",
    "paper": "#FAFBFA",
}


class Brand:
    def __init__(self, **kw):
        d = dict(DEFAULT_BRAND)
        d.update({k: v for k, v in kw.items() if v})
        for k, v in d.items():
            setattr(self, k, hex_rgb(v))


# ── drawing primitives ──────────────────────────────────────────────────────

def blueprint_grid(draw, brand, opacity=0.9, offset=0):
    """Engineering paper: fine 40px grid, coarse 200px grid. Deliberately
    low-contrast — it is a background, not a sci-fi HUD."""
    fine = blend(brand.line, brand.bg, 0.55 * opacity)
    coarse = blend(brand.line, brand.bg, 0.95 * opacity)
    for x in range(0, W + 1, 40):
        draw.line([(x, 0), (x, H)], fill=fine, width=1)
    for y in range(-40 + offset % 40, H + 1, 40):
        draw.line([(0, y), (W, y)], fill=fine, width=1)
    for x in range(0, W + 1, 200):
        draw.line([(x, 0), (x, H)], fill=coarse, width=2)
    for y in range(-200 + offset % 200, H + 1, 200):
        draw.line([(0, y), (W, y)], fill=coarse, width=2)


def dotted_wave(draw, brand, x, y, rows=7, cols=12, cell=34, dot_max=15,
                progress=1.0, color=None):
    """The client's halftone mark, rebuilt parametrically.

    Read off the business card: a fan of dots, dense and saturated at the
    lower-left, dissolving up and to the right. Generated rather than traced
    so it can be scaled, animated and reused as transition, watermark and hub.
    """
    color = color or brand.deep
    for r in range(rows):
        for c in range(cols):
            sweep = c / max(1, cols - 1)
            riser = r / max(1, rows - 1)
            fall = max(0.0, 1 - (sweep * 1.25 + riser * 0.55))
            if fall <= 0.02:
                continue
            local = max(0.0, min(1.0, (progress - sweep * 0.55) * 2.4))
            if local <= 0:
                continue
            size = dot_max * (0.35 + fall * 0.65) * local
            cx = x + c * cell + cell / 2
            cy = y + r * cell + cell / 2 - sweep * cell * 1.6
            fill = blend(color, brand.bg, 0.18 + fall * 0.82)
            draw.ellipse([cx - size / 2, cy - size / 2,
                          cx + size / 2, cy + size / 2], fill=fill)


def text(draw, xy, s, font, fill, anchor="la", max_width=None, line_gap=10):
    """Draw text, wrapping to max_width. Returns the height used, so callers
    can stack blocks without hard-coding positions."""
    if not max_width:
        draw.text(xy, s, font=font, fill=fill, anchor=anchor)
        bb = draw.textbbox(xy, s, font=font, anchor=anchor)
        return bb[3] - bb[1]
    words, lines, cur = s.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    x, y = xy
    used = 0
    for ln in lines:
        draw.text((x, y + used), ln, font=font, fill=fill, anchor=anchor)
        bb = draw.textbbox((x, y + used), ln, font=font, anchor=anchor)
        used += (bb[3] - bb[1]) + line_gap
    return used


# ── thin-line icons ─────────────────────────────────────────────────────────
# Outlined only, rounded joins, one stroke weight — equipment, not app icons.

def _icon_server(d, x, y, s, col, w):
    for i in range(3):
        top = y + s * (0.13 + i * 0.28)
        d.rounded_rectangle([x + s * .12, top, x + s * .88, top + s * .2],
                            radius=s * .05, outline=col, width=w)
        d.ellipse([x + s * .2, top + s * .075, x + s * .26, top + s * .135], fill=col)


def _icon_storage(d, x, y, s, col, w):
    d.ellipse([x + s * .14, y + s * .1, x + s * .86, y + s * .34], outline=col, width=w)
    d.arc([x + s * .14, y + s * .38, x + s * .86, y + s * .62], 0, 180, fill=col, width=w)
    d.arc([x + s * .14, y + s * .62, x + s * .86, y + s * .86], 0, 180, fill=col, width=w)
    d.line([x + s * .14, y + s * .22, x + s * .14, y + s * .74], fill=col, width=w)
    d.line([x + s * .86, y + s * .22, x + s * .86, y + s * .74], fill=col, width=w)


def _icon_network(d, x, y, s, col, w):
    d.rounded_rectangle([x + s * .35, y + s * .12, x + s * .65, y + s * .3],
                        radius=s * .04, outline=col, width=w)
    for cx in (.1, .58):
        d.rounded_rectangle([x + s * cx, y + s * .68, x + s * (cx + .32), y + s * .86],
                            radius=s * .04, outline=col, width=w)
    d.line([x + s * .5, y + s * .3, x + s * .5, y + s * .52], fill=col, width=w)
    d.line([x + s * .26, y + s * .52, x + s * .74, y + s * .52], fill=col, width=w)
    d.line([x + s * .26, y + s * .52, x + s * .26, y + s * .68], fill=col, width=w)
    d.line([x + s * .74, y + s * .52, x + s * .74, y + s * .68], fill=col, width=w)


def _icon_security(d, x, y, s, col, w):
    pts = [(x + s * .5, y + s * .1), (x + s * .85, y + s * .26),
           (x + s * .85, y + s * .55), (x + s * .5, y + s * .9),
           (x + s * .15, y + s * .55), (x + s * .15, y + s * .26)]
    d.polygon(pts, outline=col, width=w)
    d.line([x + s * .35, y + s * .5, x + s * .46, y + s * .62], fill=col, width=w)
    d.line([x + s * .46, y + s * .62, x + s * .68, y + s * .38], fill=col, width=w)


def _icon_support(d, x, y, s, col, w):
    d.arc([x + s * .16, y + s * .18, x + s * .84, y + s * .78], 180, 360, fill=col, width=w)
    d.rounded_rectangle([x + s * .1, y + s * .48, x + s * .3, y + s * .78],
                        radius=s * .07, outline=col, width=w)
    d.rounded_rectangle([x + s * .7, y + s * .48, x + s * .9, y + s * .78],
                        radius=s * .07, outline=col, width=w)
    d.arc([x + s * .5, y + s * .62, x + s * .9, y + s * .96], 0, 90, fill=col, width=w)


def _icon_cctv(d, x, y, s, col, w):
    d.polygon([(x + s * .1, y + s * .34), (x + s * .72, y + s * .16),
               (x + s * .82, y + s * .42), (x + s * .2, y + s * .6)],
              outline=col, width=w)
    d.line([x + s * .3, y + s * .58, x + s * .3, y + s * .76], fill=col, width=w)
    d.line([x + s * .3, y + s * .76, x + s * .46, y + s * .84], fill=col, width=w)
    d.ellipse([x + s * .62, y + s * .58, x + s * .86, y + s * .82], outline=col, width=w)


def _icon_desktop(d, x, y, s, col, w):
    d.rounded_rectangle([x + s * .12, y + s * .18, x + s * .88, y + s * .66],
                        radius=s * .05, outline=col, width=w)
    d.line([x + s * .5, y + s * .66, x + s * .5, y + s * .8], fill=col, width=w)
    d.line([x + s * .34, y + s * .82, x + s * .66, y + s * .82], fill=col, width=w)


def _icon_fiber(d, x, y, s, col, w):
    d.ellipse([x + s * .32, y + s * .32, x + s * .68, y + s * .68], outline=col, width=w)
    d.arc([x + s * .32, y + s * .32, x + s * .68, y + s * .68], 180, 360, fill=col, width=w)
    d.line([x + s * .08, y + s * .5, x + s * .32, y + s * .5], fill=col, width=w)
    d.line([x + s * .68, y + s * .5, x + s * .92, y + s * .5], fill=col, width=w)


ICONS = {
    "server": _icon_server, "storage": _icon_storage, "network": _icon_network,
    "security": _icon_security, "support": _icon_support, "cctv": _icon_cctv,
    "desktop": _icon_desktop, "fiber": _icon_fiber,
}


def icon(draw, name, x, y, size, color, width=None):
    fn = ICONS.get(name)
    if not fn:
        return
    fn(draw, x, y, size, color, width or max(3, int(size * 0.045)))


def lower_third(draw, brand, y, heading, caption, frame, start):
    """White block, green accent bar, black heading, grey caption — the
    title system the brief specifies. Rises into a reserved slot."""
    a = fade_in(frame, start)
    if a <= 0:
        return
    dy = rise(frame, start, 60)
    x0, x1 = SAFE_X, W - SAFE_X
    hf, cf = _font("bold", 58), _font("regular", 34)
    pad = 36
    cap_h = 0
    if caption:
        tmp = caption.split()
        lines, cur = [], ""
        for wd in tmp:
            t = (cur + " " + wd).strip()
            if draw.textlength(t, font=cf) <= (x1 - x0 - pad * 2 - 30):
                cur = t
            else:
                lines.append(cur); cur = wd
        if cur:
            lines.append(cur)
        cap_h = len(lines) * 44
    box_h = pad * 2 + 70 + (cap_h + 12 if caption else 0)
    top = y + dy
    bg = blend((255, 255, 255), brand.bg, a)
    draw.rounded_rectangle([x0, top, x1, top + box_h], radius=18, fill=bg)
    bar = blend(brand.accent, bg, a)
    draw.rounded_rectangle([x0 + 26, top + pad, x0 + 34, top + box_h - pad],
                           radius=4, fill=bar)
    tx = x0 + 26 + 8 + 28
    draw.text((tx, top + pad - 4), heading, font=hf,
              fill=blend(brand.ink, bg, a))
    if caption:
        text(draw, (tx, top + pad + 74), caption, cf,
             blend(brand.grey, bg, a), max_width=x1 - tx - pad, line_gap=8)


# ── scenes ──────────────────────────────────────────────────────────────────
# Each takes (draw, brand, spec, frame_in_scene) and paints one frame.
# A scene is pure: same frame number always gives the same pixels, which is
# what makes a render reproducible and reviewable.

def scene_statement(d, b, s, f):
    """Big type, one idea per line, revealed line by line."""
    blueprint_grid(d, b, 0.5, offset=int(f * 0.4))
    lines = s.get("lines", [])
    bf = _font("bold", 100)
    y = H // 2 - (len(lines) * 118 + 90) // 2
    for i, ln in enumerate(lines):
        a = fade_in(f, i * 12)
        if a <= 0:
            continue
        d.text((SAFE_X, y + i * 118 + rise(f, i * 12)), ln, font=bf,
               fill=blend(b.ink, b.bg, a))
    tail = s.get("tail")
    if tail:
        a = fade_in(f, len(lines) * 12 + 10)
        if a > 0:
            d.text((SAFE_X, y + len(lines) * 118 + 40 + rise(f, len(lines) * 12 + 10)),
                   tail, font=_font("regular", 40), fill=blend(b.grey, b.bg, a))


def scene_brand(d, b, s, f):
    """Logo reveal: the dotted mark draws itself, then the name, then the
    capability stack."""
    prog = ease((f - 4) / 34)
    dotted_wave(d, b, W // 2 - 210, 430, rows=7, cols=12, cell=36, dot_max=16,
                progress=prog)
    a = fade_in(f, 24)
    if a > 0:
        dy = rise(f, 24)
        d.text((W // 2, 790 + dy), s.get("name", ""), font=_font("bold", 92),
               fill=blend(b.ink, b.bg, a), anchor="ma")
        d.text((W // 2, 900 + dy), s.get("tagline", ""), font=_font("regular", 36),
               fill=blend(b.grey, b.bg, a), anchor="ma")
    for i, item in enumerate(s.get("stack", [])):
        aa = fade_in(f, 46 + i * 9)
        if aa <= 0:
            continue
        dy = rise(f, 46 + i * 9, 24)
        y = 1030 + i * 106 + dy
        icon(d, item["icon"], W // 2 - 250, y, 72, blend(b.deep, b.bg, aa))
        d.text((W // 2 - 150, y + 14), item["label"], font=_font("regular", 46),
               fill=blend(b.ink, b.bg, aa))


def scene_pillar(d, b, s, f):
    """One trade: a row of equipment cards, then the title block."""
    blueprint_grid(d, b, 0.65, offset=int(f * 0.3))
    icons = s.get("icons", [])
    accent = s.get("accent_index", 0)
    n = len(icons)
    card = 268
    gap = 30
    total = n * card + (n - 1) * gap
    x0 = (W - total) // 2
    top = 560
    for i, name in enumerate(icons):
        a = fade_in(f, 8 + i * 9)
        if a <= 0:
            continue
        dy = rise(f, 8 + i * 9, 26)
        x = x0 + i * (card + gap)
        col = b.accent if i == accent else b.line
        d.rounded_rectangle([x, top + dy, x + card, top + card + dy], radius=26,
                            fill=blend((255, 255, 255), b.bg, a),
                            outline=blend(col, b.bg, a), width=3)
        icon(d, name, x + card * .17, top + dy + card * .17, card * .66,
             blend(b.accent if i == accent else b.deep, b.bg, a))
    lower_third(d, b, top + card + 90, s.get("heading", ""), s.get("caption"), f, 26)


def scene_hub(d, b, s, f):
    """Everything converges on one mark — the single-window message.

    The centre MUST be a visible object: an earlier version radiated lines
    from an invisible point and read as random crossings, not architecture.
    """
    blueprint_grid(d, b, 0.55, offset=int(f * 0.25))
    nodes = s.get("nodes", [])
    hub = (W // 2, 900)
    box_w, box_h = 210, 180
    slots = [(SAFE_X + 30, 480), (W - SAFE_X - 30 - box_w, 480),
             (SAFE_X + 30, 1230), (W - SAFE_X - 30 - box_w, 1230)]
    for i, node in enumerate(nodes[:4]):
        sx, sy = slots[i]
        cx, cy = sx + box_w / 2, sy + box_h / 2
        p = ease((f - (18 + i * 8)) / 22)
        if p > 0:
            d.line([cx, cy, lerp(cx, hub[0], p), lerp(cy, hub[1], p)],
                   fill=b.accent, width=3)
    hs = lerp(0.6, 1.0, ease(f / 20))
    r = 112 * hs
    d.ellipse([hub[0] - r, hub[1] - r, hub[0] + r, hub[1] + r],
              fill=b.bg, outline=b.accent, width=3)
    dotted_wave(d, b, hub[0] - 78, hub[1] - 62, rows=5, cols=8, cell=20,
                dot_max=11, progress=1.0)
    for i, node in enumerate(nodes[:4]):
        a = fade_in(f, 24 + i * 8)
        if a <= 0:
            continue
        sx, sy = slots[i]
        dy = rise(f, 24 + i * 8, 18)
        d.rounded_rectangle([sx, sy + dy, sx + box_w, sy + box_h + dy], radius=22,
                            fill=blend((255, 255, 255), b.bg, a),
                            outline=blend(b.line, b.bg, a), width=3)
        icon(d, node["icon"], sx + box_w * .26, sy + dy + box_h * .18, box_h * .62,
             blend(b.deep, b.bg, a))
        d.text((sx + box_w / 2, sy + box_h + dy + 22), node["label"],
               font=_font("regular", 34), fill=blend(b.ink, b.bg, a), anchor="ma")
    lower_third(d, b, 1500, s.get("heading", ""), s.get("caption"), f, 62)


def scene_endcard(d, b, s, f):
    """Final frame: mark, name, tagline, one line of contact. Nothing else."""
    prog = ease(f / 32)
    dotted_wave(d, b, W // 2 - 216, 470, rows=8, cols=13, cell=36, dot_max=17,
                progress=prog)
    a = fade_in(f, 22)
    if a > 0:
        dy = rise(f, 22)
        d.text((W // 2, 900 + dy), s.get("name", ""), font=_font("bold", 96),
               fill=blend(b.ink, b.bg, a), anchor="ma")
    a2 = fade_in(f, 38)
    if a2 > 0:
        dy = rise(f, 38)
        for i, ln in enumerate(s.get("tagline_lines", [])):
            d.text((W // 2, 1030 + i * 58 + dy), ln, font=_font("bold", 46),
                   fill=blend(b.accent, b.bg, a2), anchor="ma")
    a3 = fade_in(f, 56)
    if a3 > 0 and s.get("contact"):
        d.text((W // 2, 1230 + rise(f, 56)), s["contact"],
               font=_font("regular", 30), fill=blend(b.grey, b.bg, a3), anchor="ma")


SCENES = {
    "statement": scene_statement,
    "brand": scene_brand,
    "pillar": scene_pillar,
    "hub": scene_hub,
    "endcard": scene_endcard,
}


# ── the dotted-wave wipe between scenes ─────────────────────────────────────

def wipe(d, b, f, length=12):
    """Primary transition from the brief. Covers the cut so scene changes
    read as designed rather than as a hard jump."""
    if f >= length:
        return
    p = ease(f / length)
    x = int(W * p)
    d.rectangle([x, 0, W, H], fill=b.bg)
    dotted_wave(d, b, x - 260, H // 2 - 300, rows=14, cols=8, cell=44,
                dot_max=20, progress=1.0, color=b.deep)


# ── render ──────────────────────────────────────────────────────────────────

def render(spec: dict, out_path: str, on_progress=None) -> str:
    """Draw every frame with Pillow and pipe raw RGB straight into FFmpeg.

    Piping avoids writing thousands of PNGs to disk — a 40 s reel is 1,200
    frames, and the temp-file version was slower and messier for no gain.
    """
    from PIL import Image, ImageDraw

    exe = ffmpeg_path()
    brand = Brand(**(spec.get("brand") or {}))
    fps = int(spec.get("fps", FPS))
    scenes = spec.get("scenes", [])
    if not scenes:
        raise ReelError("The spec has no scenes.")

    plan = []
    for sc in scenes:
        kind = sc.get("type")
        if kind not in SCENES:
            raise ReelError(f"Unknown scene type {kind!r}. "
                            f"Known: {', '.join(sorted(SCENES))}")
        plan.append((SCENES[kind], sc, int(round(float(sc.get("seconds", 4)) * fps))))
    total = sum(n for _, _, n in plan)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cmd = [exe, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    done = 0
    try:
        for fn, sc, count in plan:
            for f in range(count):
                img = Image.new("RGB", (W, H), brand.bg)
                d = ImageDraw.Draw(img)
                fn(d, brand, sc, f)
                wipe(d, brand, f)
                proc.stdin.write(img.tobytes())
                done += 1
                if on_progress and done % fps == 0:
                    on_progress(done, total)
        proc.stdin.close()
    except BrokenPipeError:
        err = proc.stderr.read().decode("utf-8", "ignore")
        raise ReelError(f"FFmpeg stopped early: {err[:400]}")
    code = proc.wait()
    if code != 0:
        err = proc.stderr.read().decode("utf-8", "ignore")
        raise ReelError(f"FFmpeg failed (exit {code}): {err[:400]}")
    return out_path


def spec_instructions() -> str:
    """What an AI stage must produce. The agent decides the WORDS and the
    running order; this module decides the pixels — so the layout can never
    be broken by a model having an off day."""
    return (
        "Reply with ONLY a JSON object describing the reel. No commentary, no "
        "markdown fences. Shape:\n"
        '{\n'
        '  "fps": 30,\n'
        '  "brand": {"accent": "#68C04F", "deep": "#4B8A5D"},\n'
        '  "scenes": [\n'
        '    {"type":"statement","seconds":4,"lines":["Apps ship.","Systems scale."],'
        '"tail":"None of it runs on its own."},\n'
        '    {"type":"brand","seconds":5,"name":"Raj Infotech","tagline":"Complete IT Solution",'
        '"stack":[{"icon":"server","label":"Computing"}]},\n'
        '    {"type":"pillar","seconds":4,"heading":"Network Infrastructure",'
        '"caption":"Structured cabling, fibre and switching.",'
        '"icons":["network","fiber","server"],"accent_index":1},\n'
        '    {"type":"hub","seconds":5,"heading":"One partner. One architecture.",'
        '"caption":"Single-window IT infrastructure.",'
        '"nodes":[{"icon":"server","label":"Computing"}]},\n'
        '    {"type":"endcard","seconds":4,"name":"Raj Infotech",'
        '"tagline_lines":["The Foundation Beneath","Everything Digital"],'
        '"contact":"www.example.com"}\n'
        '  ]\n'
        '}\n\n'
        "Rules: icons must come from this list — " + ", ".join(sorted(ICONS)) + ". "
        "'statement' takes at most 3 short lines. 'pillar' takes exactly 3 icons. "
        "'hub' takes exactly 4 nodes. Keep headings under 34 characters and "
        "captions under 90. Never write a scene containing people; this renderer "
        "draws equipment and typography only."
    )


# ── brand extraction & spec parsing ─────────────────────────────────────────

def sample_brand(image_paths: list[str]) -> dict:
    """Read the client's palette off their own artwork — a business card, a
    logo, a brochure — instead of asking anyone to type hex codes.

    Deterministic on purpose. A model asked to "match the brand" eyeballs a
    colour and gets it close; this reads the actual pixels. Same principle as
    measuring a drawing rather than describing it.
    """
    try:
        from PIL import Image
    except Exception:
        return {}
    from collections import Counter

    accent_votes, deep_votes = Counter(), Counter()
    for p in image_paths:
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        if max(w, h) > 900:                      # speed: colour survives resizing
            im = im.resize((w // 2, h // 2))
        for px in im.getdata():
            r, g, b = px
            if g < 60 or g <= r + 18 or g <= b + 18:
                continue                          # not a green
            sat = g - max(r, b)
            # Only a genuinely saturated green counts as the brand colour.
            # Faded halftone print samples as a washed grey-green and would
            # otherwise be mistaken for the second brand tone.
            if sat > 45:
                accent_votes[px] += 1
            elif sat > 28 and g < 170:
                deep_votes[px] += 1

    out = {}
    if accent_votes:
        # Rank by frequency AND vividness, not frequency alone. A logo mark
        # covers more pixels than a heading, but the heading usually carries
        # the truer brand colour — weighting saturation finds it without
        # ignoring how much of the artwork the colour actually occupies.
        def score(item):
            (r, g, b), n = item
            return n * (g - max(r, b)) ** 1.6
        best = max(accent_votes.items(), key=score)[0]
        out["accent"] = "#%02X%02X%02X" % best
    # A single strong colour is enough; derive the muted partner from it.
    # Deriving beats sampling here — a printed card's second tone is usually
    # just the first one faded, and sampling that gives a muddy grey.
    if "accent" in out:
        r, g, b = hex_rgb(out["accent"])
        out["deep"] = "#%02X%02X%02X" % (int(r * .72), int(g * .72), int(b * .72))
    return out


def parse_spec(text: str) -> dict:
    """Pull the scene spec out of an agent's reply.

    Scrapes carry markdown fences, a preamble, sometimes a trailing note — so
    take the outermost {...} rather than trusting the whole response to be
    clean JSON.
    """
    import json
    if not text or not text.strip():
        raise ReelError("The agent returned nothing to render.")
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ReelError("No JSON scene spec found in the agent's reply.")
    try:
        spec = json.loads(s[start:end + 1])
    except Exception as e:
        raise ReelError(f"The scene spec isn't valid JSON: {e}")
    if not isinstance(spec.get("scenes"), list) or not spec["scenes"]:
        raise ReelError("The scene spec has no scenes.")
    # Drop anything the renderer can't draw rather than failing the whole run.
    clean, dropped = [], []
    for sc in spec["scenes"]:
        if isinstance(sc, dict) and sc.get("type") in SCENES:
            clean.append(sc)
        else:
            dropped.append(str(sc.get("type") if isinstance(sc, dict) else sc)[:24])
    if not clean:
        raise ReelError("None of the scenes are types this renderer knows.")
    spec["scenes"] = clean
    spec["_dropped"] = dropped
    return spec


def build_prompt(request: str, brand: dict, has_refs: bool) -> str:
    """The agent's whole job: choose the words and the running order. It never
    touches layout, colour or timing — those are code."""
    brand_note = ""
    if brand:
        brand_note = (
            f"\n\nThe brand colours have already been read from the client's own "
            f"artwork: accent {brand.get('accent')}, deep {brand.get('deep')}. "
            "They are already applied — do not change them, and do not put a "
            "brand block in your JSON.")
    ref_note = (
        "\n\nReference images of the client's material are attached. Use them "
        "to understand who this company is and how they present themselves — "
        "the wording, the tone, what they actually sell."
        if has_refs else "")
    return (
        "You are writing the script for a short vertical brand reel. A local "
        "renderer will draw it — you decide the WORDS and the running order, "
        "nothing else. Layout, colour, typography, motion and timing are "
        "already handled and are not yours to specify."
        f"\n\nWHAT THE CLIENT ASKED FOR:\n{request}"
        f"{ref_note}{brand_note}"
        "\n\nWrite copy a business owner would be happy to post: short, "
        "concrete, about what they actually sell. No marketing waffle, no "
        "invented claims, no statistics you cannot support.\n\n"
        + spec_instructions())
