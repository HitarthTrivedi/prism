"""
Prism — reel renderer, HTML/CSS edition
───────────────────────────────────────
The agent designs the video. This module only guarantees it is a video.

core/reel.py draws every pixel in Pillow, which is why every reel it makes
shares one look: the layouts, the background, the palette structure and the
motion are all Python. Two clients get the same film with different words.

This renderer takes the opposite position, and it is the same one Claude
Design and Remotion take: the design surface is a browser. The writing stage
sends CSS and markup it wrote itself — its own background, type, colour,
composition and motion — and the only things fixed here are the frame size,
the seeking, the encoding, and a legibility check that runs before anything
is written to disk.

How the seeking works (the part that makes a web page filmable):

  · the page declares ordinary CSS @keyframes animations
  · nothing is allowed to run in real time — every animation is PAUSED and
    its currentTime is set explicitly for each frame
  · so frame 240 is identical whether it was reached in sequence, out of
    order, or a week later. A recording would not be; a seek is.

That is why this is a renderer and not a screen capture.

Licence note: no Remotion code is used or required. The technique — a paused
browser timeline, screenshotted per frame, piped to FFmpeg — is not anyone's
property, and this implementation is ours.
"""
from __future__ import annotations
import json
import math
import os
import shutil
import subprocess

W, H = 1080, 1920
SAFE_X, SAFE_Y = 90, 130
DEFAULT_FPS = 30

# Minimum type sizes for a 1080-wide frame, unchanged from the Pillow
# renderer: a phone is watched at arm's length for under a second a scene.
# The agent may design anything it likes above these.
T_HEADLINE, T_SUPPORT, T_LABEL = 84, 44, 32


class ReelError(Exception):
    pass


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ReelError(
            "FFmpeg is not installed — it does the encoding.\n"
            "  macOS:   brew install ffmpeg\n"
            "  Debian:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html")
    return exe


def available() -> tuple[bool, str]:
    """(ready, why not). Checked before a run so the failure is a sentence,
    not a stack trace in the middle of a pipeline."""
    try:
        ffmpeg_path()
    except ReelError as e:
        return False, str(e)
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return False, ("The web renderer needs Playwright:\n"
                       "    pip install playwright && playwright install chromium")
    return True, ""


# ── the harness ─────────────────────────────────────────────────────────────
# Everything the page can rely on, and everything it is not allowed to break.

_HARNESS_CSS = f"""
*, *::before, *::after {{ box-sizing: border-box; }}
html, body {{
  margin: 0; padding: 0;
  width: {W}px; height: {H}px;
  overflow: hidden;
  background: var(--bg, #ffffff);
  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;
}}
#stage {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; }}
/* A scene is a full-frame layer. Only the ones in play are painted, so an
   off-screen scene can never leak a stray pixel into the frame. */
.scene {{ position: absolute; inset: 0; visibility: hidden; }}
.scene.on {{ visibility: visible; }}
/* The safe area is advisory for the designer and enforced by the checker. */
:root {{ --safe-x: {SAFE_X}px; --safe-y: {SAFE_Y}px; --W: {W}px; --H: {H}px; }}
.safe {{ position: absolute; inset: var(--safe-y) var(--safe-x); }}
/* Default cut, overridable: whatever the design says about .leaving and
   .entering wins, because these are the weakest possible rules. */
.scene.leaving {{ opacity: calc(1 - var(--x, 0)); }}
.scene.entering {{ opacity: var(--e, 1); }}
img, svg, video {{ max-width: 100%; }}
"""

# Pausing every animation and setting its time by hand is the whole trick.
# Nothing on the page is permitted to depend on wall-clock time.
_HARNESS_JS = """
window.__SCENES__ = %s;
window.__ready = false;

window.__seek = function (t) {
  const S = window.__SCENES__;
  for (let i = 0; i < S.length; i++) {
    const s = S[i];
    const el = document.getElementById('s' + i);
    if (!el) continue;
    const on = t >= s.start && t < s.start + s.dur;
    el.classList.toggle('on', on);
    el.classList.remove('leaving', 'entering');
    if (!on) continue;

    const local = t - s.start;
    el.style.setProperty('--p', (local / s.dur).toFixed(5));
    el.style.setProperty('--ms', Math.round(local));

    // Overlap windows: the outgoing scene is 'leaving' with --x running 0->1,
    // the incoming one is 'entering' with --e running 0->1.
    if (s.outFrom != null && local >= s.outFrom) {
      el.classList.add('leaving');
      el.style.setProperty('--x',
        Math.min(1, (local - s.outFrom) / Math.max(1, s.outLen)).toFixed(5));
    }
    if (s.inLen && local < s.inLen) {
      el.classList.add('entering');
      el.style.setProperty('--e', Math.min(1, local / s.inLen).toFixed(5));
    }

    // Deterministic frames: pause every animation and place it by hand.
    let anims = [];
    try { anims = el.getAnimations({ subtree: true }); } catch (e) {}
    for (const a of anims) {
      try { a.pause(); a.currentTime = local; } catch (e) {}
    }
  }
  return true;
};

// Legibility check, run on the page rather than guessed at from Python: the
// browser is the only thing that knows where the text actually landed.
window.__check = function () {
  const out = [];
  const seen = new Set();
  const scenes = document.querySelectorAll('.scene.on');
  for (const scene of scenes) {
    for (const el of scene.querySelectorAll('*')) {
      const txt = (el.textContent || '').trim();
      if (!txt || el.children.length) continue;      // leaf text nodes only
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05) continue;
      const label = txt.slice(0, 34);
      const key = label + '|';
      if (r.left < 20 || r.right > %d - 20 || r.top < 20 || r.bottom > %d - 20) {
        if (!seen.has(key + 'box')) {
          seen.add(key + 'box');
          out.push('"' + label + '" is outside the frame');
        }
      }
      const fs = parseFloat(cs.fontSize);
      if (fs < %d && !seen.has(key + 'sz')) {
        seen.add(key + 'sz');
        out.push('"' + label + '" is ' + Math.round(fs) +
                 'px — under the %dpx minimum for video');
      }
    }

    // Images are checked too: a logo half off the frame is exactly as broken
    // as a headline half off it, and only the browser knows where it landed.
    for (const el of scene.querySelectorAll('img, svg, picture')) {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05) continue;
      if (r.left < -2 || r.right > %d + 2 || r.top < -2 || r.bottom > %d + 2) {
        const what = el.getAttribute('alt') || el.tagName.toLowerCase();
        const key = 'img|' + what + Math.round(r.left);
        if (!seen.has(key)) {
          seen.add(key);
          out.push('an image (' + what + ', ' + Math.round(r.width) + 'x' +
                   Math.round(r.height) + ') runs off the frame');
        }
      }
    }
  }
  const d = document.documentElement;
  if (d.scrollWidth > %d || d.scrollHeight > %d) {
    out.push('the page is bigger than the frame (' + d.scrollWidth + 'x' +
             d.scrollHeight + ') — something is overflowing');
  }
  return out;
};
""" % ("%s", W, H, T_LABEL, T_LABEL, W, H, W, H)


def _plan(spec: dict, fps: int):
    """Scene windows in milliseconds, with cuts overlapping their neighbours.

    Returns (scenes_for_js, total_frames). Overlap is capped at a third of
    either neighbour so a short scene is never swallowed by its own cut.
    """
    scenes = spec.get("scenes") or []
    if not scenes:
        raise ReelError("The spec has no scenes.")
    durs = []
    for sc in scenes:
        try:
            secs = float(sc.get("seconds", 4))
        except (TypeError, ValueError):
            secs = 4.0
        durs.append(max(1.5, min(12.0, secs)) * 1000.0)

    cut_ms = float(spec.get("design", {}).get("cut_ms", 420))
    cut_ms = max(0.0, min(1200.0, cut_ms))

    out, t = [], 0.0
    for i, dur in enumerate(durs):
        overlap = 0.0
        if i + 1 < len(durs):
            overlap = min(cut_ms, durs[i] / 3, durs[i + 1] / 3)
        out.append({"start": round(t), "dur": round(dur),
                    "outFrom": round(dur - overlap) if overlap else None,
                    "outLen": round(overlap), "inLen": round(overlap)})
        t += dur - overlap
    total_ms = out[-1]["start"] + out[-1]["dur"]
    return out, int(round(total_ms / 1000.0 * fps))


def _asset_uris(table: dict) -> dict:
    """Every asset as a data: URI.

    Inlined rather than linked because the page is loaded with set_content and
    has no base URL — a file:// path or a bare filename simply does not
    resolve, and the image silently fails to appear. Inlining also means the
    saved design JSON is the whole reel: re-render it next year and the logo
    is still there.
    """
    import base64
    out = {}
    for name, a in (table or {}).items():
        path = a.get("path") if isinstance(a, dict) else a
        try:
            if not path or os.path.getsize(path) > 6_000_000:
                continue
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        ext = os.path.splitext(path)[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/png")
        out[name] = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    return out


def _place_assets(text: str, uris: dict) -> str:
    """Swap asset:name for the real thing. Longest name first, so asset:art1
    can never eat the front of asset:art10."""
    for name in sorted(uris, key=len, reverse=True):
        text = text.replace(f"asset:{name}", uris[name])
    return text


def missing_assets(spec: dict) -> list[str]:
    """Asset names the design asks for that were never made."""
    import re
    have = set((spec.get("_assets") or {}).keys())
    used = set()
    blobs = [(spec.get("design") or {}).get("css", "")]
    blobs += [sc.get("html", "") for sc in (spec.get("scenes") or [])]
    for blob in blobs:
        used.update(re.findall(r"asset:([A-Za-z0-9_-]+)", str(blob)))
    return sorted(used - have)


def _drop_missing(text: str) -> str:
    """Remove what points at artwork that does not exist.

    Asked-for-but-absent assets are stripped rather than left in place,
    because an unresolved src renders as a broken-image glyph and an
    unresolved url() renders as a blank box — both of which end up in the
    finished video. A missing picture should leave no trace, not a hole with
    an icon in it.
    """
    import re
    if "asset:" not in text:
        return text
    # Whole elements whose source is missing.
    text = re.sub(r"<(img|image|picture|video)\b[^>]*\basset:[^>]*?>", "",
                  text, flags=re.I)
    # A missing background is simply no background.
    text = re.sub(r"url\(\s*['\"]?asset:[A-Za-z0-9_-]+['\"]?\s*\)", "none",
                  text, flags=re.I)
    # Anything left is an attribute we do not know; empty it.
    text = re.sub(r"asset:[A-Za-z0-9_-]+", "", text)
    return text


def build_html(spec: dict, fps: int = DEFAULT_FPS) -> str:
    """The whole reel as one self-contained page.

    The design's CSS is placed AFTER the harness so the designer can override
    anything except the frame itself, and the scene markup is inserted as
    written — this is the part that is meant to differ from client to client.
    """
    design = spec.get("design") or {}
    scenes = spec.get("scenes") or []
    plan, _ = _plan(spec, fps)

    fonts = ""
    for family in (design.get("google_fonts") or [])[:3]:
        fam = str(family).strip().replace(" ", "+")
        if fam:
            fonts += (f'<link rel="stylesheet" href="https://fonts.googleapis.com'
                      f'/css2?family={fam}&display=block">')

    brand = spec.get("brand") or {}
    root_vars = ";".join(f"--{k}:{v}" for k, v in brand.items()
                         if isinstance(v, str) and v.strip())

    uris = _asset_uris(spec.get("_assets") or {})
    body = []
    for i, sc in enumerate(scenes):
        html = _drop_missing(_place_assets(sc.get("html") or "", uris))
        body.append(f'<section class="scene" id="s{i}" '
                    f'data-type="{sc.get("type", "")}">{html}</section>')

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{fonts}"
        f"<style>{_HARNESS_CSS}</style>"
        f"<style>:root{{{root_vars}}}</style>"
        f"<style>{_drop_missing(_place_assets(design.get('css', ''), uris))}</style>"
        "</head><body>"
        f"<div id='stage'>{''.join(body)}</div>"
        f"<script>{_HARNESS_JS % json.dumps(plan)}</script>"
        "</body></html>"
    )


def render(spec: dict, out_path: str, on_progress=None,
           check: bool = True) -> str:
    """Draw the reel in a browser and encode it.

    PNG frames are piped straight into FFmpeg — no temp directory of a
    thousand images, and no re-encode of anything.
    """
    ok, why = available()
    if not ok:
        raise ReelError(why)
    from playwright.sync_api import sync_playwright

    fps = int(spec.get("fps", DEFAULT_FPS))
    plan, total = _plan(spec, fps)
    html = build_html(spec, fps)
    exe = ffmpeg_path()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cmd = [exe, "-y", "-loglevel", "error",
           "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]

    faults: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--force-color-profile=srgb",
            "--font-render-hinting=none",
            "--disable-lcd-text",
            "--hide-scrollbars",
        ])
        page = browser.new_page(viewport={"width": W, "height": H},
                                device_scale_factor=1)
        try:
            page.set_content(html, wait_until="load")
            try:
                page.wait_for_function("document.fonts.ready.then(()=>true)",
                                       timeout=8000)
            except Exception:
                pass   # a web font that never arrives must not stop the render

            if check:
                # Look at a settled frame of every scene BEFORE encoding
                # anything: a reel that fails the check is worth catching in
                # seconds rather than after a minute of rendering.
                for s in plan:
                    page.evaluate("t => window.__seek(t)",
                                  s["start"] + s["dur"] * 0.75)
                    for fault in page.evaluate("() => window.__check()") or []:
                        if fault not in faults:
                            faults.append(fault)

            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            try:
                for f in range(total):
                    page.evaluate("t => window.__seek(t)", f * 1000.0 / fps)
                    proc.stdin.write(page.screenshot(type="png"))
                    if on_progress and (f + 1) % fps == 0:
                        on_progress(f + 1, total)
                proc.stdin.close()
            except BrokenPipeError:
                err = proc.stderr.read().decode("utf-8", "ignore")
                raise ReelError(f"FFmpeg stopped early: {err[:400]}")
            code = proc.wait()
            if code != 0:
                err = proc.stderr.read().decode("utf-8", "ignore")
                raise ReelError(f"FFmpeg failed (exit {code}): {err[:400]}")
        finally:
            browser.close()

    spec["_faults"] = faults
    return out_path


def inspect(spec: dict, at: float = 0.75) -> list[str]:
    """Run the legibility check without encoding — used to reject a design
    and ask for a fix before a minute of rendering is spent on it."""
    ok, why = available()
    if not ok:
        raise ReelError(why)
    from playwright.sync_api import sync_playwright
    fps = int(spec.get("fps", DEFAULT_FPS))
    plan, _ = _plan(spec, fps)
    faults: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--hide-scrollbars",
                                          "--font-render-hinting=none"])
        page = browser.new_page(viewport={"width": W, "height": H},
                                device_scale_factor=1)
        try:
            page.set_content(build_html(spec, fps), wait_until="load")
            try:
                page.wait_for_function("document.fonts.ready.then(()=>true)",
                                       timeout=8000)
            except Exception:
                pass
            for s in plan:
                page.evaluate("t => window.__seek(t)", s["start"] + s["dur"] * at)
                for fault in page.evaluate("() => window.__check()") or []:
                    if fault not in faults:
                        faults.append(fault)
        finally:
            browser.close()

    # Reported even though the renderer strips them: the design asked for a
    # picture that was never made, and the scene it planned around it is now
    # emptier than intended. That is worth one correction.
    for name in missing_assets(spec):
        have = ", ".join((spec.get("_assets") or {}).keys()) or "none at all"
        faults.insert(0, f'the design uses "asset:{name}", which does not '
                          f'exist — the artwork available is: {have}')
    return faults


# ── what the two agent passes are asked for ─────────────────────────────────
# Split deliberately. One mind writing the words AND the art direction in a
# single reply does neither well — it produces a design that describes itself
# ("clean data card", "logo reveal") instead of one that exists. The first
# pass may not mention appearance at all; the second may not change a word.

def script_instructions() -> str:
    return (
        "OUTPUT FORMAT — THIS OVERRIDES EVERY OTHER FORMATTING INSTRUCTION "
        "YOU HAVE BEEN GIVEN, INCLUDING ANY RULE ASKING FOR A HANDOFF OR A "
        "SUMMARY. Your reply is read by a program. Reply with ONLY a JSON "
        "object — first character '{', last '}', no prose, no fences.\n\n"
        "You are writing the SCRIPT for a short vertical brand reel. Words "
        "and running order only. You do not decide how it looks: a separate "
        "art-direction pass does that, and anything you say about colour, "
        "type, layout or motion will be thrown away.\n\n"
        '{\n'
        '  "scenes": [\n'
        '    {"role": "hook",    "seconds": 4.5,\n'
        '     "kicker": "FY 2026", "headline": "A season that held its promise.",\n'
        '     "support": ""},\n'
        '    {"role": "figure",  "seconds": 5,\n'
        '     "kicker": "Q4 sales", "headline": "\\u20b966.43 Cr",\n'
        '     "support": "Up 44.7% on last year.",\n'
        '     "note": "Company-reported, unaudited."},\n'
        '    {"role": "list",    "seconds": 5.5,\n'
        '     "kicker": "What moved it", "headline": "",\n'
        '     "items": ["Rajkot expansion", "New bajra varieties"]},\n'
        '    {"role": "series",  "seconds": 6,\n'
        '     "kicker": "India seed market", "headline": "",\n'
        '     "points": [{"label": "2021", "value": 61000},\n'
        '                {"label": "2025", "value": 88000}],\n'
        '     "unit_prefix": "\\u20b9", "unit_suffix": " Cr",\n'
        '     "note": "2025 is a published projection."},\n'
        '    {"role": "endcard", "seconds": 4,\n'
        '     "headline": "Bombay Super Hybrid Seeds",\n'
        '     "support": "Rooted in research. Growing for India.",\n'
        '     "contact": "www.example.com"}\n'
        '  ]\n'
        '}\n\n'
        "ROLES: hook · figure (one number) · series (numbers over time) · "
        "list (things or people named) · statement · brand · endcard (always "
        "last). Use only the roles the story needs; repeat any of them.\n\n"
        "RULES\n"
        "· 4 to 7 scenes, 20 to 35 seconds in total. A viewer reads three "
        "words in under two seconds.\n"
        "· headline under 60 characters, support under 100, kicker under 22, "
        "at most 4 items, at most 6 points.\n"
        "· A number that moves over time goes in 'points' as real numbers. "
        "Never write a series into a sentence.\n"
        "· Every disclaimer, source or asterisk goes in 'note'. An asterisk "
        "with no note points at nothing.\n"
        "· No invented figures, no unsupported claims, no marketing waffle.\n"
        "· Say nothing about how it should look. Not one word."
    )


# The design prompt is built before the imagery stage has run, so the asset
# list cannot be known yet. This stands in its place and is substituted the
# moment before the prompt is typed.
ASSET_TOKEN = "{{ASSETS}}"

MAX_GENERATED = 3


def imagery_instructions(request: str, has_own_artwork: bool = False,
                         research: str = "") -> str:
    """The stage that MAKES the pictures when the client supplied none.

    Most jobs arrive with nothing attached — a company name and a sentence.
    Research has already found out who they are by this point, so the tool
    that can both search and draw is asked for a small, specific set of
    images rather than a mood board.
    """
    return (
        "You are producing the ARTWORK for a short vertical brand reel. Do "
        f"this in two steps.\n\n"
        "STEP 1 — SEARCH. Look the company up on the web before drawing "
        "anything: what they actually sell, what their existing branding "
        "looks like, their colours, the physical things their work involves. "
        "Do not guess from the name.\n\n"
        f"STEP 2 — GENERATE EXACTLY {MAX_GENERATED} SEPARATE IMAGES.\n"
        f"  · {MAX_GENERATED} DIFFERENT IMAGE FILES. Run the image tool "
        f"{MAX_GENERATED} times, once per subject.\n"
        "  · NOT one image containing several things. No grid, no sheet, no "
        "collage, no side-by-side comparison, no 'here are three options' "
        "layout, no mockup board. A single image with three subjects in it "
        "is a failed result and is discarded.\n"
        "  · Each image is ONE subject, centred, with room around it.\n\n"
        "WHAT THE THREE ARE:\n"
        + ("  1. NO logo — the client's real mark was supplied and is already "
           "in hand, so anything you draw would be a lookalike and would be "
           "thrown away. Make three SUBJECT images instead.\n"
           if has_own_artwork else
           "  1. A wordmark or emblem for the company, in the spirit of what "
           "your search found — their colours, their industry. The company "
           "name must be spelled EXACTLY as it is written above; check it "
           "character by character before you finish.\n")
        + "  2-3. The SUBJECT of their business — the actual equipment, "
        "produce or material. A seed company wants seed, crop, a field; an "
        "IT firm wants racks, cabling, cameras; a workshop wants its "
        "machines.\n\n"
        "EVERY IMAGE MUST:\n"
        "  · have a TRANSPARENT background — a PNG with alpha, the subject "
        "cut out and nothing behind it. No white card, no scene, no desk, no "
        "drop shadow, no rounded panel. The reel places these on its own "
        "background, so anything behind the subject shows up as a rectangle "
        "stuck in the middle of the frame. If transparency is genuinely not "
        "possible, use ONE flat solid colour and nothing else — that can be "
        "removed afterwards; a gradient or a scene cannot.\n"
        "  · contain no people and no faces.\n"
        "  · be square or portrait.\n\n"
        f"WHAT THE REEL IS ABOUT:\n{request}\n\n"
        + (f"WHAT RESEARCH ALREADY FOUND:\n{research[:1500]}\n\n"
           if research.strip() else "")
        + "When the images are done, reply with one short line per image "
        "saying what it is — nothing else. The images themselves are "
        "collected from this page automatically; do not describe how they "
        "should be used."
    )


def design_instructions(brand: dict | None = None, request: str = "",
                        assets: str = "") -> str:
    """The art-direction pass. This is the one that makes two clients' reels
    different films rather than one template with new words."""
    brand = brand or {}
    swatch = ""
    if brand:
        swatch = ("\n\nThe client's own colours, measured from their artwork "
                  "(available as CSS variables --accent, --deep, --ink, --bg): "
                  + ", ".join(f"{k} {v}" for k, v in brand.items()) +
                  ". Build the palette around these — they are the only part "
                  "of the design that is not yours to choose.")
    return (
        "OUTPUT FORMAT — THIS OVERRIDES EVERY OTHER FORMATTING INSTRUCTION, "
        "INCLUDING ANY RULE ASKING FOR A HANDOFF OR A SUMMARY. Reply with "
        "ONLY a JSON object — first character '{', last '}', no prose, no "
        "fences.\n\n"
        "You are the ART DIRECTOR for a 1080x1920 vertical reel. The script "
        "above is final: use its words exactly, in its order, with its "
        "timings. Everything about how it LOOKS and MOVES is yours — "
        "background, palette, typography, composition, motion, the lot. "
        "There is no house style and no template to follow."
        f"{swatch}"
        f"{(chr(10) + chr(10) + 'What the client asked for: ' + request) if request else ''}"
        "\n\nYou are writing a real web page. It is rendered in Chromium at "
        "1080x1920 and filmed frame by frame, so ordinary CSS is what you "
        "have — gradients, grid, flexbox, clip-path, masks, filters, SVG, "
        "pseudo-elements, web fonts.\n\n"
        + (("ARTWORK ON HAND — this is the complete list:\n"
            + assets +
            "\n\nUse them by name, exactly like a URL: "
            '<img src="asset:logo" alt=""> or '
            "background-image: url(asset:logo).\n"
            "· THOSE NAMES ARE THE ONLY ONES THAT EXIST. Referring to any "
            "other — asset:art2 when only asset:art1 is listed, asset:photo, "
            "asset:bg — leaves a hole in the frame. Count the list above and "
            "use only what is on it.\n"
            "· Every scene does not need a picture. A scene with none must "
            "still look finished: fill it with the background, type and "
            "colour rather than leaving a gap where an image would have been. "
            "An empty rectangle reads as a bug; deliberate space does not.\n"
            "· Put the logo where a logo belongs — the endcard at least — and "
            "size it in CSS rather than trusting its pixel dimensions.\n"
            "· Never redraw or approximate a mark you have been given.\n\n")
           if assets else
           "NO ARTWORK IS AVAILABLE — not one image. Build the entire reel "
           "from type, colour, shape and motion, and make that look "
           "deliberate. Do not reference asset:anything; every such reference "
           "resolves to nothing and leaves a hole. Do not draw a logo out of "
           "CSS boxes either — set the company name in good type instead.\n\n")
        + '{\n'
        '  "design": {\n'
        '    "name": "one line describing the look you chose",\n'
        '    "google_fonts": ["Fraunces:opsz,wght@9..144,700", "Inter:wght@400;600"],\n'
        '    "cut_ms": 500,\n'
        '    "css": "…every rule the reel needs…"\n'
        '  },\n'
        '  "scenes": [\n'
        '    {"type": "hook", "seconds": 4.5,\n'
        '     "html": "<div class=\'grow\'><div class=\'kicker\'>FY 2026</div>'
        '<h1>A season that held its promise.</h1></div>"}\n'
        '  ]\n'
        '}\n\n'
        "HTML ATTRIBUTES MUST USE SINGLE QUOTES — <div class='content'> and "
        "<img src='asset:logo' alt=''>. Your markup lives inside a JSON "
        "string, so a double quote in it has to be escaped, and an unescaped "
        "one makes the whole reply unparseable. Single quotes are valid HTML "
        "and sidestep the problem entirely. This is the single most common "
        "way this stage fails.\n\n"
        "HOW MOTION WORKS — read this, it is the one unusual part:\n"
        "· Write ordinary CSS @keyframes and animation declarations. The "
        "renderer PAUSES the page and sets each animation's time by hand for "
        "every frame, so the result is identical on every render.\n"
        "· Every animation MUST use `both` fill-mode "
        "(`animation: rise 900ms cubic-bezier(.16,1,.3,1) both`) or it will "
        "snap when the frame is seeked.\n"
        "· Animation time restarts at 0 for each scene. Stagger with "
        "`animation-delay`.\n"
        "· Never use transitions, JavaScript, `:hover`, or anything that "
        "depends on real time — none of it will be filmed.\n"
        "· Each scene element gets `--p` (0→1 through the scene) and `--ms` "
        "if you prefer to drive something off progress directly.\n"
        "· Cuts: the outgoing scene has class `leaving` with `--x` 0→1, the "
        "incoming one `entering` with `--e` 0→1. Style them however you like "
        "— a fade is only the default.\n\n"
        "WHAT THE RENDERER GUARANTEES, SO YOU DO NOT HAVE TO:\n"
        "the frame size, the seeking, the cuts' timing, and the encode. "
        "`.scene` is already a full-frame absolutely-positioned layer, and "
        "`--safe-x`/`--safe-y` (90px/130px) are the margins to keep text "
        "inside.\n\n"
        "WHAT WILL BE REJECTED — the page is measured before it is filmed:\n"
        "· any text whose box falls outside the 1080x1920 frame\n"
        f"· any text rendered under {T_LABEL}px; headlines want "
        f"{T_HEADLINE}px+, supporting text {T_SUPPORT}px+. A phone is watched "
        "at arm's length for under a second a scene.\n"
        "· anything that makes the page scroll\n\n"
        "MAKE IT SPECIFIC TO THIS CLIENT. A seed company at dusk and a "
        "cardiology clinic should not come out looking like the same film. "
        "Choose a background that means something — a deep field gradient, "
        "paper, a colour block, a fine rule system — and typography with a "
        "point of view. Do not default to white with a grid."
    )


def script_drift(spec: dict, script_text: str) -> list[str]:
    """Lines the script wrote that the design did not carry over.

    Two agents, and the second is told not to change a word — but told is not
    the same as prevented. Reported rather than blocked: sometimes the design
    legitimately splits a headline across elements, so this is a flag for a
    person, not a rule for a machine.
    """
    import json as _json
    import re
    if not script_text.strip():
        return []
    try:
        from . import reel as _pillow
        blocks = _pillow._blocks(script_text, "{", "}")
        script = None
        for b in blocks:
            try:
                got = _json.loads(b)
            except Exception:
                continue
            if isinstance(got, dict) and got.get("scenes"):
                script = got
                break
        if not script:
            return []
    except Exception:
        return []

    page = " ".join(str(sc.get("html", "")) for sc in (spec.get("scenes") or []))
    page = re.sub(r"<[^>]+>", " ", page)
    page = re.sub(r"\s+", " ", page).lower()

    lost = []
    for sc in script["scenes"]:
        for key in ("headline", "support"):
            line = str(sc.get(key, "")).strip()
            if len(line) < 12:
                continue
            words = [w for w in re.findall(r"[a-z0-9₹%.]+", line.lower())
                     if len(w) > 3]
            if not words:
                continue
            hits = sum(1 for w in words if w in page)
            if hits < max(1, len(words) // 2):
                lost.append(line[:60])
    return lost


def _fix_markup_quotes(block: str) -> str:
    """Escape the double quotes an art director left raw inside its markup.

    A design is JSON whose values are HTML, and the reply that comes back very
    often contains  "html": "<div class="content">…"  — valid HTML, invalid
    JSON, and the whole design lost over punctuation. The prompt asks for
    single quotes; this is what happens when it doesn't get them.

    Only quotes that look like HTML attributes are touched: `="` and the `"`
    that closes it. A quote that is genuinely ending a JSON string is followed
    by a comma, brace or colon, and is left alone.
    """
    import re
    return re.sub(r'=\\?"([^"\\]*?)\\?"(?=[\s>/])',
                  lambda m: "='" + m.group(1) + "'", block)


def parse_spec(text: str) -> dict:
    """Pull the design/scene JSON out of an agent's reply — same balanced-brace
    scan as the Pillow renderer, since scrapes carry fences and preambles."""
    from . import reel as _pillow
    if not text or not text.strip():
        raise ReelError("The agent returned nothing to render.")
    spec = None
    for block in _pillow._blocks(text, "{", "}"):
        for candidate in (block, _fix_markup_quotes(block),
                          _pillow._loosen(block),
                          _fix_markup_quotes(_pillow._loosen(block))):
            try:
                got = json.loads(candidate)
            except Exception:
                continue
            if isinstance(got, dict) and isinstance(got.get("scenes"), list) \
                    and got["scenes"]:
                spec = got
                break
        if spec:
            break
    if spec is None:
        raise ReelError("No JSON found in the agent's reply.")
    keep = [sc for sc in spec["scenes"]
            if isinstance(sc, dict) and str(sc.get("html", "")).strip()]
    if not keep:
        raise ReelError("The scenes carry no markup — nothing to render.")
    spec["scenes"] = keep
    return spec


def still(spec: dict, at_frame: int, out_path: str) -> str:
    """One frame as a PNG. For looking at a design before committing to it."""
    from playwright.sync_api import sync_playwright
    fps = int(spec.get("fps", DEFAULT_FPS))
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--hide-scrollbars",
                                          "--font-render-hinting=none",
                                          "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": W, "height": H},
                                device_scale_factor=1)
        try:
            page.set_content(build_html(spec, fps), wait_until="load")
            try:
                page.wait_for_function("document.fonts.ready.then(()=>true)",
                                       timeout=8000)
            except Exception:
                pass
            page.evaluate("t => window.__seek(t)", at_frame * 1000.0 / fps)
            page.screenshot(path=out_path, type="png")
        finally:
            browser.close()
    return out_path
