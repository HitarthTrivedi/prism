"""
Prism Studio — fix the layout by hand, in the browser
──────────────────────────────────────────────────────
A Studio reel is an HTML page before it is a video, and that is the whole
trick here. When the AI's design puts a headline half off the frame or two
texts on top of each other, chasing it with another prompt is a coin toss —
but the page itself can simply be opened in Chrome with an edit layer on
top: click a thing, drag it into place, resize it, retype it, delete it —
recolour it, set its type, add a picture or a line of text, change a
scene's length or background, and swap the reel's palette and typeface.
Since 2026-09-08 that layer is a proper workspace (Studio V2): scene rail,
layer list, inspector, scrubbable timeline, undo/redo, autosave, and a
Refine box that hands a sentence back to the design conversation.

The edits are saved into the SPEC (spec["edits"]) as small records, and the
renderer applies exactly the same records with exactly the same script
before filming. Preview and film are the same page, so a fix made by eye
cannot come out differently in the video. Four kinds of record:

  · an ELEMENT edit — which scene, which durable data-prism-id layer,
    moved how far, scaled how much, retyped, hidden,
    and a `style` of whitelisted properties (colour, background, font…);
  · an ADDED element — `add: "text" | "image" | "box"`, addressed by an
    id of its own, placed at x/y with a width; an image travels as a data:
    URI inside the record, so the saved spec is still the whole reel;
  · a SCENE record — `root: true` — the scene's own length in seconds and
    the style of its root layer (its background, typically);
  · a DESIGN record — `design: true` — the reel's CSS variables (the
    palette) and a typeface swap applied to everything set in the old one.

Two deliberate choices:

  · Moves and scales use the CSS `translate` / `scale` / `rotate`
    PROPERTIES, not `transform`. They compose with whatever transform the
    scene's own animation drives, so a moved element keeps its entrance —
    it just lives somewhere else.
  · Existing elements receive permanent data-prism-id attributes in the
    saved scene source. Old path records are read once for compatibility,
    but new edits never depend on sibling order or DOM nesting.

The editor page talks back to Prism through a one-purpose local HTTP
server on 127.0.0.1 (loopback only, random port, alive only while the
editor is open): POST /save (and /autosave) stores the edits, POST /render
stores them and asks Prism to render again, POST /refine carries a sentence
plus the selected layer back to the design conversation. Everything
arriving from the browser is sanitised — it is user input.
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import threading

from . import reel_web

EDITS_KEY = "edits"


def is_studio_spec(spec) -> bool:
    """Was this reel filmed from a web page? Only those can be edited —
    the Quick renderer draws from templates and its spec carries no HTML."""
    if not isinstance(spec, dict):
        return False
    scenes = spec.get("scenes")
    return (isinstance(scenes, list) and bool(scenes)
            and any(isinstance(sc, dict) and sc.get("html") for sc in scenes))

# Bounds for what a browser may hand back. A drag of four thousand pixels on
# a 1080x1920 frame is off any edge; anything past these is a bug or mischief.
_MAX_SHIFT = 4000.0
_MIN_SCALE, _MAX_SCALE = 0.05, 20.0
_MAX_TEXT = 2000
_MAX_EDITS = 400
_MAX_PATH = 40
_MAX_IMAGE = 8_000_000            # bytes of data: URI — a phone photo, not a film
_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_VAR = re.compile(r"^--[a-z0-9-]{1,40}$", re.I)
_COLOUR = re.compile(r"^(#[0-9a-f]{3,8}|rgba?\([\d\s.,%]+\)|hsla?\([\d\s.,%]+\)"
                     r"|[a-z]{3,20}|transparent)$", re.I)
_DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|webp|gif);base64,[A-Za-z0-9+/=]+$")
_OPEN_TAG = re.compile(r"<(?!/|!|\?)([A-Za-z][\w:-]*)(\s[^<>]*?)?(/?)>")


def _studio_asset(name: str) -> str:
    """Load browser modules from real files rather than Python string blobs."""
    return (pathlib.Path(__file__).with_name("studio_assets") / name).read_text(
        encoding="utf-8")


def ensure_stable_ids(spec: dict) -> None:
    """Persist durable IDs in scene HTML so DOM nesting never retargets edits.

    Mutates `spec` in place: every open tag in every scene's HTML gets a
    `data-prism-id="el-<scene>-<n>"` unless it already has one, and every
    scene gets a `studio_id`. Idempotent — running it again changes nothing,
    which is what lets parse_spec() and editable_html() both call it."""
    if not isinstance(spec, dict):
        return
    for scene_no, scene in enumerate(spec.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        scene.setdefault("studio_id", f"scene-{scene_no + 1}")
        html = scene.get("html")
        if not isinstance(html, str):
            continue
        counter = 0
        def tagged(match):
            nonlocal counter
            tag, attrs, slash = match.group(1), match.group(2) or "", match.group(3)
            if "data-prism-id=" in attrs:
                return match.group(0)
            counter += 1
            return f'<{tag}{attrs} data-prism-id="el-{scene_no + 1}-{counter}"{slash}>'
        scene["html"] = _OPEN_TAG.sub(tagged, html)

# The style an edit may carry: CSS property name → how its value is checked.
# Numbers are stored as numbers (the apply script adds the unit), so a value
# can never smuggle a second declaration in after a semicolon.
_STYLE_RULES = {
    "color": ("colour",),
    "backgroundColor": ("colour",),
    "fontFamily": ("text", 60),
    "fontSize": ("num", 8, 400),
    "fontWeight": ("enum", "100", "200", "300", "400", "500", "600", "700",
                   "800", "900", "normal", "bold"),
    "fontStyle": ("enum", "normal", "italic"),
    "textAlign": ("enum", "left", "center", "right", "justify"),
    "textTransform": ("enum", "none", "uppercase", "lowercase", "capitalize"),
    "letterSpacing": ("num", -20, 100),
    "lineHeight": ("num", 0.5, 4),
    "opacity": ("num", 0, 1),
    "zIndex": ("int", -10, 1000),
    "borderRadius": ("num", 0, 500),
    "rotate": ("num", -360, 360),
    "width": ("num", 1, 2000),
    "height": ("num", 1, 4000),
    "padding": ("num", 0, 400),
}


def _clean_name(val, limit: int) -> str:
    """A font family as the browser sent it, made safe for a stylesheet: a
    name never contains a semicolon, so everything from the first one on is
    somebody else's declaration and goes; braces and quotes go too."""
    s = str(val).split(";")[0]
    return re.sub(r"[{}<>\"\\]", "", s).strip()[:limit]


def _clean_style(raw) -> dict:
    """The whitelisted, bounded subset of a style the browser sent."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, rule in _STYLE_RULES.items():
        if key not in raw:
            continue
        val = raw[key]
        if val is None or val == "":
            out[key] = ""                 # an explicit "back to the design's"
            continue
        kind = rule[0]
        if kind == "colour":
            s = str(val).strip()
            if _COLOUR.match(s) and len(s) <= 40:
                out[key] = s
        elif kind == "text":
            s = _clean_name(val, rule[1])
            if s:
                out[key] = s
        elif kind == "enum":
            s = str(val).strip().lower()
            if s in rule[1:]:
                out[key] = s
        elif kind in ("num", "int"):
            try:
                n = float(val)
            except (TypeError, ValueError):
                continue
            lo, hi = rule[1], rule[2]
            n = min(hi, max(lo, n))
            out[key] = int(round(n)) if kind == "int" else round(n, 3)
    return out


# ── the browser side ─────────────────────────────────────────────────────────
# Lives in core/studio_assets/ as real files (see _studio_asset above):
#   apply.js   — turns records into styles. Injected into BOTH the editor page
#                and the render page, so there is no second implementation to
#                drift: what the editor shows is what the renderer applies.
#   editor.js  — the workspace (scenes, layers, inspector, timeline, tools).
#   editor.css — its chrome.
# ── sanitising what the browser sends back ───────────────────────────────────

def _num(item, key, default, lo, hi, digits=1):
    try:
        v = float(item.get(key, default) if item.get(key) is not None else default)
    except (TypeError, ValueError):
        return None
    if v < lo or v > hi:
        return None
    return round(v, digits)


def clean_edits(raw) -> list[dict]:
    """The browser's edits, checked field by field. Anything malformed is
    dropped silently — a broken record must cost one edit, not the save."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:_MAX_EDITS]:
        if not isinstance(item, dict):
            continue

        # The reel's palette and typeface.
        if item.get("design"):
            edit: dict = {"design": True}
            vars_ = {}
            for name, val in (item.get("vars") or {}).items() \
                    if isinstance(item.get("vars"), dict) else []:
                name, val = str(name).strip(), str(val).strip()
                if _VAR.match(name) and _COLOUR.match(val) and len(val) <= 40:
                    vars_[name] = val
            if vars_:
                edit["vars"] = vars_
            font = item.get("font")
            if isinstance(font, dict):
                to = _clean_name(font.get("to", ""), 60)
                frm = _clean_name(font.get("from", ""), 60)
                if to:
                    edit["font"] = {"from": frm, "to": to}
            if len(edit) > 1:
                out.append(edit)
            continue

        try:
            scene = int(item.get("scene"))
        except (TypeError, ValueError):
            continue
        if scene < 0:
            continue

        # A scene's length and its root layer's style.
        if item.get("root"):
            edit = {"scene": scene, "root": True}
            secs = _num(item, "seconds", 0, 1.5, 12.0)
            if secs:
                edit["seconds"] = secs
            style = _clean_style(item.get("style"))
            if style:
                edit["style"] = style
            if len(edit) > 2:
                out.append(edit)
            continue

        # Something the owner added.
        if item.get("add"):
            kind = str(item.get("add", "")).strip().lower()
            ident = str(item.get("id", "")).strip()
            if kind not in ("text", "image", "box") or not _ID.match(ident):
                continue
            x = _num(item, "x", 0, -_MAX_SHIFT, _MAX_SHIFT, 0)
            y = _num(item, "y", 0, -_MAX_SHIFT, _MAX_SHIFT, 0)
            if x is None or y is None:
                continue
            edit = {"scene": scene, "add": kind, "id": ident,
                    "x": int(x), "y": int(y)}
            for dim in ("w", "h"):
                v = _num(item, dim, 0, 1, _MAX_SHIFT, 0)
                if v:
                    edit[dim] = int(v)
            if kind == "image":
                src = str(item.get("src", ""))
                if len(src) > _MAX_IMAGE or not _DATA_IMAGE.match(src):
                    continue
                edit["src"] = src
            if kind == "text":
                edit["text"] = str(item.get("text", ""))[:_MAX_TEXT]
            sc = _num(item, "scale", 1, _MIN_SCALE, _MAX_SCALE, 3)
            if sc and abs(sc - 1) > 0.001:
                edit["scale"] = sc
            if item.get("hidden"):
                edit["hidden"] = True
            style = _clean_style(item.get("style"))
            if style:
                edit["style"] = style
            out.append(edit)
            continue

        # V2 targets a permanent element ID. Old saved layouts remain
        # readable by retaining the path form as a migration fallback.
        element_id = str(item.get("element_id", "")).strip()
        if element_id:
            if not _ID.match(element_id):
                continue
            edit = {"scene": scene, "element_id": element_id}
        else:
            try:
                path = [int(i) for i in (item.get("path") or [])]
            except (TypeError, ValueError):
                continue
            if not path or len(path) > _MAX_PATH \
                    or any(i < 0 or i > 500 for i in path):
                continue
            edit = {"scene": scene, "path": path}
        try:
            dx = float(item.get("dx") or 0)
            dy = float(item.get("dy") or 0)
            sc = float(item.get("scale") or 1)
        except (TypeError, ValueError):
            continue
        if abs(dx) > _MAX_SHIFT or abs(dy) > _MAX_SHIFT:
            continue
        edit["dx"], edit["dy"] = round(dx, 1), round(dy, 1)
        edit["scale"] = min(_MAX_SCALE, max(_MIN_SCALE, round(sc, 3)))
        if item.get("hidden"):
            edit["hidden"] = True
        if isinstance(item.get("text"), str):
            edit["text"] = item["text"][:_MAX_TEXT]
        style = _clean_style(item.get("style"))
        if style:
            edit["style"] = style
        if (edit["dx"] or edit["dy"] or edit["scale"] != 1
                or edit.get("hidden") or "text" in edit or "style" in edit):
            out.append(edit)
    return out


# ── the two pages ────────────────────────────────────────────────────────────

def with_timing(spec: dict, edits: list[dict]) -> dict:
    """The spec with any scene lengths the owner set in the editor. Timing
    is a plan-time fact, not a page-time one — the seeker's windows come
    from the spec — so it is applied to the spec, not by the script."""
    timed = [e for e in edits if e.get("root") and e.get("seconds")]
    if not timed:
        return spec
    out = copy.deepcopy(spec)
    scenes = out.get("scenes") or []
    for e in timed:
        if 0 <= e["scene"] < len(scenes) and isinstance(scenes[e["scene"]], dict):
            scenes[e["scene"]]["seconds"] = float(e["seconds"])
    return out


def apply_edits(html: str, edits: list[dict]) -> str:
    """The render page: the same apply script the editor uses, with the
    saved edits, run before a single frame is filmed."""
    if not edits:
        return html
    script = ("<script>" + _studio_asset("apply.js")
              + "window.__edApply(" + json.dumps(edits) + ");</script>")
    return html.replace("</body></html>", script + "</body></html>")


def editable_html(spec: dict, fps: int | None = None) -> str:
    """The reel page with the edit layer on top."""
    ensure_stable_ids(spec)
    fps = int(fps or spec.get("fps", reel_web.DEFAULT_FPS))
    existing = clean_edits(spec.get(EDITS_KEY) or [])
    html = reel_web.build_html(with_timing(spec, existing), fps)
    inject = ("<style>" + _studio_asset("editor.css") + "</style>"
              + "<script>window.__STUDIO_EDITS__=" + json.dumps(existing)
              + ";</script><script>" + _studio_asset("apply.js")
              + "</script><script>" + _studio_asset("editor.js") + "</script>")
    return html.replace("</body></html>", inject + "</body></html>")


# ── the local server the editor page talks to ────────────────────────────────

def serve(spec: dict, fps: int | None = None,
          on_save=None, on_render=None, on_refine=None) -> tuple[str, callable]:
    """Serve the editor on 127.0.0.1 and hand edits back through callbacks.

    Returns (url, stop). `on_save(edits)` fires for the Save button,
    `on_render(edits)` for Save & render — both called on the server's
    thread, so a Qt caller must emit a signal rather than touch a widget.
    `stop()` shuts the server down; it is also safe to call twice.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    ensure_stable_ids(spec)
    html = editable_html(spec, fps).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):          # silence per-request stderr
            pass

        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            callback = {"/save": on_save, "/autosave": on_save,
                        "/render": on_render}.get(self.path)
            if self.path == "/refine":
                self._refine()
                return
            if callback is None and self.path not in ("/save", "/autosave", "/render"):
                self.send_response(404)
                self.end_headers()
                return
            # Only the editor page may post here. A page from any other
            # origin can reach a loopback port with a plain form POST, and
            # an empty body used to be accepted as "no edits" — wiping the
            # saved ones and starting a render.
            origin = self.headers.get("Origin", "")
            if origin and not origin.startswith("http://127.0.0.1:"):
                self.send_response(403)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                data = {}
            if not isinstance(data, dict) or "edits" not in data:
                self.send_response(400)
                self.end_headers()
                return
            edits = clean_edits(data.get("edits"))
            body = json.dumps({"ok": True, "edits": len(edits)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if callback is not None:
                try:
                    callback(edits)
                except Exception:                       # noqa: BLE001
                    pass    # a broken handler must not kill the server

        def _refine(self):
            origin = self.headers.get("Origin", "")
            if origin and not origin.startswith("http://127.0.0.1:"):
                self.send_response(403)
                self.end_headers()
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 16_000)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                data = {}
            change = str(data.get("change", "")).strip()[:2000] if isinstance(data, dict) else ""
            context = data.get("context") if isinstance(data, dict) else {}
            if not isinstance(context, dict):
                context = {}
            try:
                scene_index = max(0, int(context.get("scene_index", 0) or 0))
            except (TypeError, ValueError):
                scene_index = 0
            clean_context = {
                "scene_index": scene_index,
                "scene_id": str(context.get("scene_id", ""))[:32],
                "element_id": str(context.get("element_id", ""))[:32],
                "label": str(context.get("label", ""))[:160],
            }
            ok = bool(change and on_refine)
            body = json.dumps({"ok": ok}).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if ok:
                try:
                    on_refine(change, clean_context)
                except Exception:                       # noqa: BLE001
                    pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="prism-reel-edit")
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    def stop():
        try:
            server.shutdown()
            server.server_close()
        except Exception:                               # noqa: BLE001
            pass

    return url, stop
