"""
Prism Studio — fix the layout by hand, in the browser
──────────────────────────────────────────────────────
A Studio reel is an HTML page before it is a video, and that is the whole
trick here. When the AI's design puts a headline half off the frame or two
texts on top of each other, chasing it with another prompt is a coin toss —
but the page itself can simply be opened in Chrome with an edit layer on
top: click a thing, drag it into place, resize it, retype it, delete it.

The edits are saved into the SPEC (spec["edits"]) as small records —
which scene, which element, moved how far, scaled how much — and the
renderer applies exactly the same records with exactly the same script
before filming. Preview and film are the same page, so a fix made by eye
cannot come out differently in the video.

Two deliberate choices:

  · Edits use the CSS `translate` and `scale` PROPERTIES, not `transform`.
    They compose with whatever transform the scene's own animation drives,
    so a moved element keeps its entrance — it just lives somewhere else.
  · An element is addressed by its child-index path inside its scene
    (`#s2 > children[1] > children[0]`), computed by the same walk in the
    editor and in the apply script. No ids are added to the design's own
    markup, so the design stays byte-for-byte what the AI wrote.

The editor page talks back to Prism through a one-purpose local HTTP
server on 127.0.0.1 (loopback only, random port, alive only while the
editor is open): POST /save stores the edits, POST /render stores them and
asks Prism to render again. Everything arriving from the browser is
sanitised — it is user input.
"""
from __future__ import annotations

import json
import threading

from . import reel_web

EDITS_KEY = "edits"

# Bounds for what a browser may hand back. A drag of four thousand pixels on
# a 1080x1920 frame is off any edge; anything past these is a bug or mischief.
_MAX_SHIFT = 4000.0
_MIN_SCALE, _MAX_SCALE = 0.05, 20.0
_MAX_TEXT = 2000
_MAX_EDITS = 400
_MAX_PATH = 40

# ── the one script both sides share ──────────────────────────────────────────
# Defined once and injected into BOTH the editor page and the render page, so
# there is no second implementation to drift: what the editor shows is what
# the renderer applies.

_APPLY_JS = """
window.__edApply = function (edits) {
  for (const e of (edits || [])) {
    const scene = document.getElementById('s' + e.scene);
    if (!scene) continue;
    let n = scene;
    for (const i of (e.path || [])) { n = n && n.children[i]; }
    if (!n || n === scene) continue;
    if (e.hidden) { n.style.display = 'none'; continue; }
    n.style.display = '';
    if (e.dx || e.dy) {
      n.style.translate = (e.dx || 0) + 'px ' + (e.dy || 0) + 'px';
    } else {
      n.style.translate = '';
    }
    if (e.scale && Math.abs(e.scale - 1) > 0.001) {
      n.style.scale = String(e.scale);
    } else {
      n.style.scale = '';
    }
    if (typeof e.text === 'string') n.textContent = e.text;
  }
};
"""

_EDITOR_CSS = """
html, body { width: auto !important; height: auto !important;
             overflow: auto !important; background: #14181b !important; }
#stage { margin: 84px auto 48px; box-shadow: 0 12px 60px rgba(0,0,0,.55);
         flex: none; }
#__ed-bar { position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647;
  background: #1d2226; color: #e8ebe9; font: 14px/1.4 -apple-system,
  'Segoe UI', sans-serif; padding: 10px 14px; display: flex; flex-wrap: wrap;
  gap: 8px; align-items: center; box-shadow: 0 2px 14px rgba(0,0,0,.4); }
#__ed-bar b { margin-right: 4px; font-weight: 600; }
#__ed-bar button { background: #2c3338; color: #e8ebe9; border: 1px solid
  #3d454b; border-radius: 6px; padding: 6px 12px; font: inherit;
  cursor: pointer; }
#__ed-bar button:hover { background: #39424a; }
#__ed-bar button.cur { background: #3172b8; border-color: #3172b8; }
#__ed-bar button.go { background: #2e7d4f; border-color: #2e7d4f;
  font-weight: 600; }
#__ed-bar #__ed-hint { flex-basis: 100%; color: #9fb0a8; font-size: 12.5px; }
#__ed-flash { position: fixed; top: 72px; right: 16px; z-index: 2147483647;
  background: #2e7d4f; color: #fff; padding: 8px 14px; border-radius: 6px;
  font: 13px -apple-system, 'Segoe UI', sans-serif; opacity: 0;
  transition: opacity .2s; pointer-events: none; }
#stage .scene.on *:hover { outline: 1px dashed rgba(110, 170, 255, .8); }
.__ed-sel { outline: 2px solid #4ea1ff !important;
  outline-offset: 2px; cursor: move; }
#__ed-done { color: #e8ebe9; font: 18px/1.6 -apple-system, 'Segoe UI',
  sans-serif; max-width: 40em; margin: 20vh auto; text-align: center; }
"""

# Tokens, not %-formatting: the JS is full of braces and percent signs.
_EDITOR_JS = """
(function () {
  'use strict';
  const edits = new Map();
  for (const e of __ED_EDITS__) {
    edits.set(e.scene + '/' + (e.path || []).join('.'), e);
  }
  const S = window.__SCENES__ || [];
  const stage = document.getElementById('stage');
  let cur = 0, sel = null, drag = null;

  function pathOf(el) {
    const root = el.closest('#stage > .scene');
    const path = [];
    let n = el;
    while (n && n !== root) {
      const p = n.parentElement;
      path.unshift(Array.prototype.indexOf.call(p.children, n));
      n = p;
    }
    return path;
  }
  function editFor(el) {
    const path = pathOf(el);
    const key = cur + '/' + path.join('.');
    if (!edits.has(key)) {
      edits.set(key, { scene: cur, path: path, dx: 0, dy: 0, scale: 1 });
    }
    return edits.get(key);
  }
  function applyAll() { window.__edApply(Array.from(edits.values())); }
  function scale() { return parseFloat(stage.dataset.scale || '1'); }
  function fit() {
    const s = Math.min(1, (innerWidth - 48) / 1080,
                          (innerHeight - 150) / 1920);
    stage.style.transformOrigin = 'top center';
    stage.style.transform = 'scale(' + s + ')';
    stage.dataset.scale = s;
    stage.style.marginBottom = (-1920 * (1 - s) + 48) + 'px';
  }
  function label(el) {
    const t = (el.textContent || '').trim();
    if (t) return '\\u201c' + t.slice(0, 30) + (t.length > 30 ? '\\u2026' : '') + '\\u201d';
    return '<' + el.tagName.toLowerCase() + '>';
  }
  function select(el) {
    if (sel) sel.classList.remove('__ed-sel');
    sel = el || null;
    if (sel) sel.classList.add('__ed-sel');
    hint.textContent = sel
      ? label(sel) + '   \\u2014 drag to move \\u00b7 double-click to retype \\u00b7 Delete key removes it'
      : 'Click anything in the scene to select it. Drag to move. Double-click text to retype it.';
  }
  function show(i) {
    cur = Math.max(0, Math.min(S.length - 1, i));
    select(null);
    window.__seek(S[cur].start + S[cur].dur * 0.6);
    applyAll();
    for (const b of bar.querySelectorAll('[data-scene]')) {
      b.classList.toggle('cur', +b.dataset.scene === cur);
    }
  }
  function flash(text) {
    box.textContent = text;
    box.style.opacity = '1';
    clearTimeout(box._t);
    box._t = setTimeout(() => { box.style.opacity = '0'; }, 1800);
  }
  function post(where, then) {
    fetch(where, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits: Array.from(edits.values()) }),
    }).then(r => r.json()).then(then)
      .catch(() => alert('Could not reach Prism \\u2014 is the Prism window still open?'));
  }

  // ── toolbar ──────────────────────────────────────────────────────────
  const bar = document.createElement('div');
  bar.id = '__ed-bar';
  let scenes = '';
  for (let i = 0; i < S.length; i++) {
    scenes += '<button data-scene="' + i + '">Scene ' + (i + 1) + '</button>';
  }
  bar.innerHTML = '<b>Fix the layout</b>' + scenes +
    '<span style="width:12px"></span>' +
    '<button id="__ed-smaller" title="Make the selected thing smaller">Smaller</button>' +
    '<button id="__ed-bigger" title="Make the selected thing bigger">Bigger</button>' +
    '<button id="__ed-delete" title="Remove the selected thing">Delete</button>' +
    '<button id="__ed-reset" title="Undo every change to the selected thing">Undo this one</button>' +
    '<button id="__ed-reset-scene" title="Undo every change on this scene">Undo this scene</button>' +
    '<span style="flex:1"></span>' +
    '<button id="__ed-save">Save</button>' +
    '<button id="__ed-render" class="go">Save &amp; render</button>' +
    '<span id="__ed-hint"></span>';
  document.body.appendChild(bar);
  const hint = bar.querySelector('#__ed-hint');
  const box = document.createElement('div');
  box.id = '__ed-flash';
  document.body.appendChild(box);

  bar.addEventListener('click', function (ev) {
    const b = ev.target.closest('button');
    if (!b) return;
    if (b.dataset.scene !== undefined) { show(+b.dataset.scene); return; }
    if (b.id === '__ed-save') {
      post('/save', () => flash('Saved \\u2014 Prism has your changes.'));
      return;
    }
    if (b.id === '__ed-render') {
      post('/render', () => {
        document.body.innerHTML = '<div id="__ed-done"><h2>Rendering\\u2026</h2>' +
          '<p>You can close this tab. The progress bar is in the Prism window, ' +
          'and the finished reel lands in Desktop / Prism Artifacts as usual.</p></div>';
      });
      return;
    }
    if (!sel) { flash('Click something in the scene first.'); return; }
    const e = editFor(sel);
    if (b.id === '__ed-bigger') e.scale = Math.min(20, (e.scale || 1) * 1.1);
    if (b.id === '__ed-smaller') e.scale = Math.max(0.05, (e.scale || 1) / 1.1);
    if (b.id === '__ed-delete') { e.hidden = true; select(null); }
    if (b.id === '__ed-reset') {
      edits.delete(cur + '/' + pathOf(sel).join('.'));
      sel.style.translate = ''; sel.style.scale = '';
      sel.style.display = ''; select(sel);
    }
    if (b.id === '__ed-reset-scene') {
      for (const k of Array.from(edits.keys())) {
        if (k.startsWith(cur + '/')) edits.delete(k);
      }
      location.reload();
      return;
    }
    applyAll();
  });

  // ── selecting and dragging ───────────────────────────────────────────
  stage.addEventListener('mousedown', function (ev) {
    const el = ev.target.closest('#stage > .scene.on *');
    if (!el || el.isContentEditable) return;
    ev.preventDefault();
    select(el);
    const e = editFor(el);
    drag = { x: ev.clientX, y: ev.clientY, dx: e.dx || 0, dy: e.dy || 0, e: e };
  });
  document.addEventListener('mousemove', function (ev) {
    if (!drag) return;
    drag.e.dx = drag.dx + (ev.clientX - drag.x) / scale();
    drag.e.dy = drag.dy + (ev.clientY - drag.y) / scale();
    applyAll();
  });
  document.addEventListener('mouseup', function () {
    if (drag) {
      drag.e.dx = Math.round(drag.e.dx);
      drag.e.dy = Math.round(drag.e.dy);
      applyAll();
    }
    drag = null;
  });
  stage.addEventListener('dblclick', function (ev) {
    const el = ev.target.closest('#stage > .scene.on *');
    if (!el || el.children.length) return;   // only leaf text
    ev.preventDefault();
    el.contentEditable = 'true';
    el.focus();
    const done = function () {
      el.contentEditable = 'false';
      el.removeEventListener('blur', done);
      editFor(el).text = el.textContent;
      applyAll();
    };
    el.addEventListener('blur', done);
  });
  document.addEventListener('keydown', function (ev) {
    if (document.activeElement && document.activeElement.isContentEditable) return;
    if ((ev.key === 'Delete' || ev.key === 'Backspace') && sel) {
      ev.preventDefault();
      editFor(sel).hidden = true;
      select(null);
      applyAll();
    }
    if (ev.key === 'Escape') select(null);
  });

  addEventListener('resize', fit);
  fit();
  applyAll();
  show(0);
})();
"""


# ── sanitising what the browser sends back ───────────────────────────────────

def clean_edits(raw) -> list[dict]:
    """The browser's edits, checked field by field. Anything malformed is
    dropped silently — a broken record must cost one edit, not the save."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:_MAX_EDITS]:
        if not isinstance(item, dict):
            continue
        try:
            scene = int(item.get("scene"))
            path = [int(i) for i in (item.get("path") or [])]
        except (TypeError, ValueError):
            continue
        if scene < 0 or not path or len(path) > _MAX_PATH \
                or any(i < 0 or i > 500 for i in path):
            continue
        edit: dict = {"scene": scene, "path": path}
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
        if (edit["dx"] or edit["dy"] or edit["scale"] != 1
                or edit.get("hidden") or "text" in edit):
            out.append(edit)
    return out


# ── the two pages ────────────────────────────────────────────────────────────

def apply_edits(html: str, edits: list[dict]) -> str:
    """The render page: the same apply script the editor uses, with the
    saved edits, run before a single frame is filmed."""
    if not edits:
        return html
    script = ("<script>" + _APPLY_JS
              + "window.__edApply(" + json.dumps(edits) + ");</script>")
    return html.replace("</body></html>", script + "</body></html>")


def editable_html(spec: dict, fps: int | None = None) -> str:
    """The reel page with the edit layer on top."""
    fps = int(fps or spec.get("fps", reel_web.DEFAULT_FPS))
    html = reel_web.build_html(spec, fps)
    existing = clean_edits(spec.get(EDITS_KEY) or [])
    inject = ("<style>" + _EDITOR_CSS + "</style>"
              + "<script>" + _APPLY_JS
              + _EDITOR_JS.replace("__ED_EDITS__", json.dumps(existing))
              + "</script>")
    return html.replace("</body></html>", inject + "</body></html>")


# ── the local server the editor page talks to ────────────────────────────────

def serve(spec: dict, fps: int | None = None,
          on_save=None, on_render=None) -> tuple[str, callable]:
    """Serve the editor on 127.0.0.1 and hand edits back through callbacks.

    Returns (url, stop). `on_save(edits)` fires for the Save button,
    `on_render(edits)` for Save & render — both called on the server's
    thread, so a Qt caller must emit a signal rather than touch a widget.
    `stop()` shuts the server down; it is also safe to call twice.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
            callback = {"/save": on_save, "/render": on_render}.get(self.path)
            if callback is None and self.path not in ("/save", "/render"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                data = {}
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
