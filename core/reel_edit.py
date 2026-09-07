"""
Prism Studio — fix the layout by hand, in the browser
──────────────────────────────────────────────────────
A Studio reel is an HTML page before it is a video, and that is the whole
trick here. When the AI's design puts a headline half off the frame or two
texts on top of each other, chasing it with another prompt is a coin toss —
but the page itself can simply be opened in Chrome with an edit layer on
top: click a thing, drag it into place, resize it, retype it, delete it —
and, since 2026-09-07, recolour it, set its type, add a picture or a line of
text, change a scene's length or background, and swap the reel's palette
and typeface.

The edits are saved into the SPEC (spec["edits"]) as small records, and the
renderer applies exactly the same records with exactly the same script
before filming. Preview and film are the same page, so a fix made by eye
cannot come out differently in the video. Four kinds of record:

  · an ELEMENT edit — which scene, which element (by its child-index path
    inside the scene), moved how far, scaled how much, retyped, hidden,
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
  · Nothing is added to the design's own markup: elements are addressed by
    child-index path, computed by the same walk in the editor and in the
    apply script, and added elements are appended AFTER the design's
    children so no existing path shifts.

The editor page talks back to Prism through a one-purpose local HTTP
server on 127.0.0.1 (loopback only, random port, alive only while the
editor is open): POST /save stores the edits, POST /render stores them and
asks Prism to render again. Everything arriving from the browser is
sanitised — it is user input.
"""
from __future__ import annotations

import copy
import json
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


# ── the one script both sides share ──────────────────────────────────────────
# Defined once and injected into BOTH the editor page and the render page, so
# there is no second implementation to drift: what the editor shows is what
# the renderer applies.

_APPLY_JS = """
window.__edFont = function (family) {
  if (!family) return;
  var sys = ['arial', 'helvetica', 'helvetica neue', 'georgia', 'times new roman',
             'verdana', 'courier new', 'sans-serif', 'serif', 'monospace',
             'system-ui', 'inherit'];
  if (sys.indexOf(String(family).toLowerCase()) !== -1) return;
  var id = '__ed-font-' + String(family).replace(/[^a-z0-9]/gi, '-').toLowerCase();
  if (document.getElementById(id)) return;
  var l = document.createElement('link');
  l.id = id; l.rel = 'stylesheet';
  l.href = 'https://fonts.googleapis.com/css2?family=' +
           encodeURIComponent(family).replace(/%20/g, '+') +
           ':ital,wght@0,400;0,600;0,700;0,800;1,400&display=block';
  document.head.appendChild(l);
};
window.__edStyle = function (n, st) {
  if (!st) return;
  var px = ['fontSize', 'letterSpacing', 'borderRadius', 'width', 'height', 'padding'];
  for (var k in st) {
    var v = st[k];
    if (v === null || v === undefined || v === '') { n.style[k] = ''; continue; }
    if (px.indexOf(k) !== -1) n.style[k] = v + 'px';
    else if (k === 'rotate') n.style.rotate = v + 'deg';
    else if (k === 'fontFamily') { n.style.fontFamily = "'" + v + "', sans-serif"; window.__edFont(v); }
    else n.style[k] = String(v);
  }
};
window.__edApply = function (edits) {
  for (var i = 0; i < (edits || []).length; i++) {
    var e = edits[i];
    if (e.design) {
      var root = document.documentElement;
      for (var k in (e.vars || {})) root.style.setProperty(k, e.vars[k]);
      if (e.font && e.font.to) {
        window.__edFont(e.font.to);
        var from = String(e.font.from || '').toLowerCase();
        var stage = document.getElementById('stage');
        var all = stage ? stage.querySelectorAll('*') : [];
        for (var j = 0; j < all.length; j++) {
          var fam = getComputedStyle(all[j]).fontFamily.toLowerCase();
          if (!from || fam.indexOf(from) !== -1) {
            all[j].style.fontFamily = "'" + e.font.to + "', sans-serif";
          }
        }
      }
      continue;
    }
    var scene = document.getElementById('s' + e.scene);
    if (!scene) continue;
    if (e.root) { window.__edStyle(scene, e.style); continue; }
    if (e.add) {
      var n = scene.querySelector('[data-ed-id="' + e.id + '"]');
      if (!n) {
        n = document.createElement('div');
        n.setAttribute('data-ed-id', e.id);
        n.className = '__ed-add __ed-' + e.add;
        n.style.position = 'absolute'; n.style.zIndex = '70';
        n.style.boxSizing = 'border-box';
        if (e.add === 'image') {
          var img = document.createElement('img');
          img.style.width = '100%'; img.style.display = 'block'; img.draggable = false;
          n.appendChild(img);
        } else if (e.add === 'text') {
          n.style.font = '700 64px/1.15 sans-serif'; n.style.color = '#111';
          n.style.whiteSpace = 'pre-wrap';
        } else {
          n.style.background = '#b03a2e';
        }
        scene.appendChild(n);
      }
      n.style.left = (e.x || 0) + 'px'; n.style.top = (e.y || 0) + 'px';
      n.style.width = e.w ? e.w + 'px' : (e.add === 'text' ? 'auto' : '300px');
      n.style.height = e.h ? e.h + 'px' : '';
      if (e.add === 'text') n.textContent = e.text || '';
      if (e.add === 'image' && e.src) {
        var im = n.querySelector('img');
        if (im && im.getAttribute('src') !== e.src) im.src = e.src;
      }
      n.style.display = e.hidden ? 'none' : '';
      n.style.scale = (e.scale && Math.abs(e.scale - 1) > 0.001) ? String(e.scale) : '';
      window.__edStyle(n, e.style);
      continue;
    }
    var node = scene;
    var path = e.path || [];
    for (var p = 0; p < path.length; p++) { node = node && node.children[path[p]]; }
    if (!node || node === scene) continue;
    if (e.hidden) { node.style.display = 'none'; continue; }
    node.style.display = '';
    node.style.translate = (e.dx || e.dy) ? (e.dx || 0) + 'px ' + (e.dy || 0) + 'px' : '';
    node.style.scale = (e.scale && Math.abs(e.scale - 1) > 0.001) ? String(e.scale) : '';
    if (typeof e.text === 'string') node.textContent = e.text;
    window.__edStyle(node, e.style);
  }
};
"""

_EDITOR_CSS = """
html, body { width: auto !important; height: auto !important;
             overflow: auto !important; background: #14181b !important; }
#stage { margin: 84px 0 48px 12px; box-shadow: 0 12px 60px rgba(0,0,0,.55);
         flex: none; }
#__ed-bar { position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647;
  background: #1d2226; color: #e8ebe9; font: 14px/1.4 -apple-system,
  'Segoe UI', sans-serif; padding: 8px 14px; display: flex; flex-wrap: wrap;
  gap: 6px; align-items: center; box-shadow: 0 2px 14px rgba(0,0,0,.4); }
#__ed-bar b { margin-right: 4px; font-weight: 600; }
#__ed-bar button, #__ed-side button { background: #2c3338; color: #e8ebe9;
  border: 1px solid #3d454b; border-radius: 6px; padding: 5px 10px;
  font: inherit; cursor: pointer; }
#__ed-bar button:hover, #__ed-side button:hover { background: #39424a; }
#__ed-bar button.cur { background: #3172b8; border-color: #3172b8; }
#__ed-bar button.go { background: #2e7d4f; border-color: #2e7d4f;
  font-weight: 600; }
#__ed-bar .sep { width: 1px; height: 22px; background: #3d454b; margin: 0 4px; }
#__ed-bar #__ed-hint { flex-basis: 100%; color: #9fb0a8; font-size: 12.5px; }
#__ed-side { position: fixed; top: 0; right: 0; bottom: 0; width: 300px;
  z-index: 2147483646; background: #1a1f23; color: #e8ebe9; overflow-y: auto;
  font: 13px/1.4 -apple-system, 'Segoe UI', sans-serif; padding: 96px 14px 24px;
  box-shadow: -2px 0 14px rgba(0,0,0,.4); box-sizing: border-box; }
#__ed-side h4 { margin: 14px 0 6px; font-size: 11px; letter-spacing: .12em;
  text-transform: uppercase; color: #9fb0a8; }
#__ed-side .row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
#__ed-side .row label { flex: 0 0 84px; color: #b7c2bc; }
#__ed-side input, #__ed-side select, #__ed-side textarea { flex: 1; min-width: 0;
  background: #0f1316; color: #e8ebe9; border: 1px solid #3d454b;
  border-radius: 5px; padding: 4px 6px; font: inherit; }
#__ed-side input[type=color] { flex: 0 0 44px; height: 28px; padding: 1px; }
#__ed-side input[type=range] { padding: 0; }
#__ed-side textarea { height: 64px; resize: vertical; }
#__ed-side .small { background: transparent; border-color: transparent;
  color: #9fb0a8; padding: 2px 6px; }
#__ed-side .muted { color: #7d8a85; font-size: 12px; }
#__ed-flash { position: fixed; top: 72px; right: 316px; z-index: 2147483647;
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
  var SIDE_W = 300;
  var FONTS = ['Inter', 'Roboto', 'Montserrat', 'Poppins', 'DM Sans', 'Work Sans',
    'Nunito', 'Space Grotesk', 'Oswald', 'Bebas Neue', 'Playfair Display',
    'Libre Baskerville', 'Lora', 'Merriweather', 'Fraunces', 'Source Serif 4',
    'Cormorant Garamond', 'Arial', 'Helvetica', 'Georgia', 'Times New Roman'];
  var edits = new Map();
  for (var i0 = 0; i0 < __ED_EDITS__.length; i0++) { var e0 = __ED_EDITS__[i0]; edits.set(keyOf(e0), e0); }
  var S = window.__SCENES__ || [];
  var stage = document.getElementById('stage');
  var cur = 0, sel = null, drag = null, filling = false, addCount = 0;

  function keyOf(e) {
    if (e.design) return 'design';
    if (e.root) return e.scene + '/root';
    if (e.add) return e.scene + '/#' + e.id;
    return e.scene + '/' + (e.path || []).join('.');
  }
  function pathOf(el) {
    var root = el.closest('#stage > .scene');
    var path = [];
    var n = el;
    while (n && n !== root) {
      var p = n.parentElement;
      path.unshift(Array.prototype.indexOf.call(p.children, n));
      n = p;
    }
    return path;
  }
  function targetOf(el) {
    var added = el.closest('[data-ed-id]');
    return added || el;
  }
  function editFor(el) {
    el = targetOf(el);
    var key;
    if (el.hasAttribute('data-ed-id')) {
      key = cur + '/#' + el.getAttribute('data-ed-id');
      return edits.get(key);
    }
    var path = pathOf(el);
    key = cur + '/' + path.join('.');
    if (!edits.has(key)) edits.set(key, { scene: cur, path: path, dx: 0, dy: 0, scale: 1 });
    return edits.get(key);
  }
  function sceneEdit() {
    var key = cur + '/root';
    if (!edits.has(key)) edits.set(key, { scene: cur, root: true, style: {} });
    return edits.get(key);
  }
  function designEdit() {
    if (!edits.has('design')) edits.set('design', { design: true, vars: {} });
    return edits.get('design');
  }
  function all() { return Array.from(edits.values()); }
  function applyAll() { window.__edApply(all()); }
  function scale() { return parseFloat(stage.dataset.scale || '1'); }
  function fit() {
    var barH = bar.offsetHeight || 84;
    var avail = innerWidth - SIDE_W - 24;
    var s = Math.min(1, avail / 1080, (innerHeight - barH - 36) / 1920);
    stage.style.transformOrigin = 'top left';
    stage.style.transform = 'scale(' + s + ')';
    stage.dataset.scale = s;
    stage.style.marginTop = (barH + 12) + 'px';
    stage.style.marginLeft = Math.max(12, (avail - 1080 * s) / 2) + 'px';
    stage.style.marginBottom = (-1920 * (1 - s) + 48) + 'px';
  }
  function label(el) {
    var t = (el.textContent || '').trim();
    if (t) return '\\u201c' + t.slice(0, 30) + (t.length > 30 ? '\\u2026' : '') + '\\u201d';
    return '<' + el.tagName.toLowerCase() + '>';
  }
  function select(el) {
    if (sel) sel.classList.remove('__ed-sel');
    sel = el ? targetOf(el) : null;
    if (sel) sel.classList.add('__ed-sel');
    hint.textContent = sel
      ? label(sel) + '   \\u2014 drag to move \\u00b7 double-click to retype \\u00b7 Delete key removes it \\u00b7 style it on the right'
      : 'Click anything in the scene to select it. Drag to move. Double-click text to retype it. Add text, a picture or a shape from the bar.';
    fillInspector();
  }
  function show(i) {
    cur = Math.max(0, Math.min(S.length - 1, i));
    select(null);
    window.__seek(S[cur].start + S[cur].dur * 0.6);
    applyAll();
    var bs = bar.querySelectorAll('[data-scene]');
    for (var b = 0; b < bs.length; b++) bs[b].classList.toggle('cur', +bs[b].dataset.scene === cur);
    fillScene();
  }
  function flash(text) {
    box.textContent = text;
    box.style.opacity = '1';
    clearTimeout(box._t);
    box._t = setTimeout(function () { box.style.opacity = '0'; }, 1800);
  }
  function post(where, then) {
    fetch(where, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits: all() })
    }).then(function (r) { return r.json(); }).then(then)
      .catch(function () { alert('Could not reach Prism \\u2014 is the Prism window still open?'); });
  }
  function toHex(c) {
    var m = /rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/.exec(c || '');
    if (!m) return /^#[0-9a-f]{6}$/i.test(c || '') ? c : '#000000';
    return '#' + [m[1], m[2], m[3]].map(function (v) { return ('0' + (+v).toString(16)).slice(-2); }).join('');
  }
  function firstFamily(fam) { return (fam || '').split(',')[0].replace(/["']/g, '').trim(); }
  function fontsInUse() {
    var seen = {};
    try { document.fonts.forEach(function (f) { seen[f.family.replace(/["']/g, '')] = 1; }); } catch (e) {}
    var els = stage.querySelectorAll('*');
    for (var i = 0; i < els.length; i++) {
      var t = (els[i].textContent || '').trim();
      if (t && !els[i].children.length) seen[firstFamily(getComputedStyle(els[i]).fontFamily)] = 1;
    }
    return Object.keys(seen).filter(Boolean);
  }
  function rootVars() {
    var out = {};
    for (var i = 0; i < document.styleSheets.length; i++) {
      var rules; try { rules = document.styleSheets[i].cssRules; } catch (e) { continue; }
      for (var j = 0; j < rules.length; j++) {
        var r = rules[j];
        if (!r.selectorText || !/(^|,)\\s*:root\\s*(,|$)/.test(r.selectorText)) continue;
        for (var k = 0; k < r.style.length; k++) {
          var p = r.style[k];
          if (p.indexOf('--') === 0) out[p] = r.style.getPropertyValue(p).trim();
        }
      }
    }
    var d = edits.get('design');
    if (d && d.vars) for (var v in d.vars) out[v] = d.vars[v];
    return out;
  }
  function isColour(v) { return /^(#[0-9a-f]{3,8}|rgba?\\(|hsla?\\()/i.test(v || ''); }

  // ── toolbar ──────────────────────────────────────────────────────────
  var bar = document.createElement('div');
  bar.id = '__ed-bar';
  var scenes = '';
  for (var i = 0; i < S.length; i++) scenes += '<button data-scene="' + i + '">Scene ' + (i + 1) + '</button>';
  bar.innerHTML = '<b>Fix the layout</b>' + scenes +
    '<span class="sep"></span>' +
    '<button id="__ed-add-text" title="Add a line of text to this scene">+ Text</button>' +
    '<button id="__ed-add-image" title="Add a picture from this computer">+ Picture</button>' +
    '<button id="__ed-add-box" title="Add a colour block">+ Shape</button>' +
    '<input id="__ed-file" type="file" accept="image/*" style="display:none">' +
    '<span class="sep"></span>' +
    '<button id="__ed-smaller" title="Make the selected thing smaller">Smaller</button>' +
    '<button id="__ed-bigger" title="Make the selected thing bigger">Bigger</button>' +
    '<button id="__ed-front" title="Bring the selected thing in front of everything">Front</button>' +
    '<button id="__ed-back" title="Send the selected thing behind everything">Back</button>' +
    '<button id="__ed-delete" title="Remove the selected thing">Delete</button>' +
    '<button id="__ed-reset" title="Undo every change to the selected thing">Undo this one</button>' +
    '<button id="__ed-reset-scene" title="Undo every change on this scene">Undo this scene</button>' +
    '<span style="flex:1"></span>' +
    '<button id="__ed-save">Save</button>' +
    '<button id="__ed-render" class="go">Save &amp; render</button>' +
    '<span id="__ed-hint"></span>';
  document.body.appendChild(bar);
  var hint = bar.querySelector('#__ed-hint');
  var box = document.createElement('div');
  box.id = '__ed-flash';
  document.body.appendChild(box);

  // ── inspector ────────────────────────────────────────────────────────
  var side = document.createElement('div');
  side.id = '__ed-side';
  function opts(list, current) {
    return list.map(function (f) { return '<option value="' + f + '"' + (f === current ? ' selected' : '') + '>' + f + '</option>'; }).join('');
  }
  side.innerHTML =
    '<h4>Selected</h4><div id="__ed-none" class="muted">Nothing selected.</div>' +
    '<div id="__ed-props">' +
    '<div class="row" id="__ed-text-row"><label>Text</label><textarea id="__ed-text"></textarea></div>' +
    '<div class="row"><label>Colour</label><input type="color" id="__ed-color"><button class="small" data-clear="color">reset</button></div>' +
    '<div class="row"><label>Background</label><input type="color" id="__ed-bg"><button class="small" data-clear="backgroundColor">none</button></div>' +
    '<div class="row"><label>Font</label><select id="__ed-font"></select></div>' +
    '<div class="row"><label>Size (px)</label><input type="number" id="__ed-size" min="8" max="400"></div>' +
    '<div class="row"><label>Weight</label><select id="__ed-weight">' + opts(['400', '500', '600', '700', '800', '900'], '') + '</select>' +
    '<label style="flex:0 0 auto"><input type="checkbox" id="__ed-italic" style="flex:none"> italic</label></div>' +
    '<div class="row"><label>Align</label><select id="__ed-align">' + opts(['left', 'center', 'right'], '') + '</select>' +
    '<select id="__ed-transform">' + opts(['none', 'uppercase', 'lowercase', 'capitalize'], '') + '</select></div>' +
    '<div class="row"><label>Spacing</label><input type="number" id="__ed-tracking" step="0.5" min="-20" max="100" title="letter spacing, px">' +
    '<input type="number" id="__ed-leading" step="0.05" min="0.5" max="4" title="line height"></div>' +
    '<div class="row"><label>Opacity</label><input type="range" id="__ed-opacity" min="0" max="1" step="0.05"></div>' +
    '<div class="row"><label>Radius / Turn</label><input type="number" id="__ed-radius" min="0" max="500" title="corner radius, px">' +
    '<input type="number" id="__ed-rotate" min="-360" max="360" title="rotation, degrees"></div>' +
    '<div class="row" id="__ed-width-row"><label>Width (px)</label><input type="number" id="__ed-width" min="1" max="2000"></div>' +
    '</div>' +
    '<h4>This scene</h4>' +
    '<div class="row"><label>Length (s)</label><input type="number" id="__ed-seconds" min="1.5" max="12" step="0.5"></div>' +
    '<div class="row"><label>Background</label><input type="color" id="__ed-scene-bg"><button class="small" id="__ed-scene-bg-clear">reset</button></div>' +
    '<h4>Whole reel</h4>' +
    '<div id="__ed-vars"></div>' +
    '<div class="row"><label>Typeface</label><select id="__ed-font-from"></select></div>' +
    '<div class="row"><label>\\u2192 becomes</label><select id="__ed-font-to"></select><button id="__ed-font-swap" class="small">apply</button></div>' +
    '<div class="muted">Everything set in the first typeface is set in the second.</div>';
  document.body.appendChild(side);
  var $ = function (id) { return side.querySelector('#' + id); };

  function styleOf(e) { if (!e.style) e.style = {}; return e.style; }
  function setStyle(key, val) {
    if (!sel) return;
    styleOf(editFor(sel))[key] = val;
    applyAll();
  }
  function fillInspector() {
    filling = true;
    var props = $('__ed-props'), none = $('__ed-none');
    if (!sel) { props.style.display = 'none'; none.style.display = ''; filling = false; return; }
    props.style.display = ''; none.style.display = 'none';
    var e = editFor(sel), st = e.style || {}, cs = getComputedStyle(sel);
    var isText = !sel.children.length && (sel.textContent || '').trim();
    $('__ed-text-row').style.display = (isText || e.add === 'text') ? '' : 'none';
    $('__ed-text').value = (e.add === 'text') ? (e.text || '') : (typeof e.text === 'string' ? e.text : sel.textContent);
    $('__ed-color').value = toHex(st.color || cs.color);
    $('__ed-bg').value = toHex(st.backgroundColor || cs.backgroundColor);
    var fam = st.fontFamily || firstFamily(cs.fontFamily);
    var families = fontsInUse().concat(FONTS).filter(function (f, i, a) { return a.indexOf(f) === i; });
    if (fam && families.indexOf(fam) === -1) families.unshift(fam);
    $('__ed-font').innerHTML = opts(families, fam);
    $('__ed-size').value = st.fontSize || Math.round(parseFloat(cs.fontSize));
    $('__ed-weight').value = st.fontWeight || (cs.fontWeight === 'bold' ? '700' : cs.fontWeight);
    $('__ed-italic').checked = (st.fontStyle || cs.fontStyle) === 'italic';
    $('__ed-align').value = st.textAlign || (cs.textAlign === 'start' ? 'left' : cs.textAlign);
    $('__ed-transform').value = st.textTransform || cs.textTransform;
    $('__ed-tracking').value = st.letterSpacing !== undefined ? st.letterSpacing : (cs.letterSpacing === 'normal' ? 0 : Math.round(parseFloat(cs.letterSpacing) * 10) / 10);
    $('__ed-leading').value = st.lineHeight || (cs.lineHeight === 'normal' ? 1.2 : Math.round(parseFloat(cs.lineHeight) / parseFloat(cs.fontSize) * 100) / 100);
    $('__ed-opacity').value = st.opacity !== undefined ? st.opacity : cs.opacity;
    $('__ed-radius').value = st.borderRadius !== undefined ? st.borderRadius : Math.round(parseFloat(cs.borderRadius) || 0);
    $('__ed-rotate').value = st.rotate || 0;
    $('__ed-width-row').style.display = e.add ? '' : 'none';
    $('__ed-width').value = e.w || Math.round(sel.getBoundingClientRect().width / scale());
    filling = false;
  }
  function fillScene() {
    filling = true;
    var se = edits.get(cur + '/root') || {};
    $('__ed-seconds').value = se.seconds || Math.round((S[cur].dur / 1000) * 2) / 2;
    var sc = document.getElementById('s' + cur);
    $('__ed-scene-bg').value = toHex((se.style && se.style.backgroundColor) || (sc ? getComputedStyle(sc).backgroundColor : ''));
    fillDesign();
    filling = false;
  }
  function fillDesign() {
    var vars = rootVars(), html = '';
    Object.keys(vars).forEach(function (v) {
      if (!isColour(vars[v])) return;
      html += '<div class="row"><label title="' + v + '">' + v.replace(/^--/, '') + '</label>' +
              '<input type="color" data-var="' + v + '" value="' + toHex(vars[v]) + '"></div>';
    });
    $('__ed-vars').innerHTML = html || '<div class="muted">This design sets no palette variables.</div>';
    var used = fontsInUse();
    $('__ed-font-from').innerHTML = opts(used, used[0]);
    var to = $('__ed-font-to');
    if (!to.options.length) to.innerHTML = opts(FONTS, FONTS[0]);
  }

  var bind = function (id, ev, fn) { $(id).addEventListener(ev, function (x) { if (!filling) fn(x); }); };
  bind('__ed-text', 'input', function () {
    if (!sel) return;
    var e = editFor(sel);
    e.text = $('__ed-text').value;
    applyAll();
  });
  bind('__ed-color', 'input', function () { setStyle('color', $('__ed-color').value); });
  bind('__ed-bg', 'input', function () { setStyle('backgroundColor', $('__ed-bg').value); });
  bind('__ed-font', 'change', function () { setStyle('fontFamily', $('__ed-font').value); });
  bind('__ed-size', 'input', function () { setStyle('fontSize', +$('__ed-size').value); });
  bind('__ed-weight', 'change', function () { setStyle('fontWeight', $('__ed-weight').value); });
  bind('__ed-italic', 'change', function () { setStyle('fontStyle', $('__ed-italic').checked ? 'italic' : 'normal'); });
  bind('__ed-align', 'change', function () { setStyle('textAlign', $('__ed-align').value); });
  bind('__ed-transform', 'change', function () { setStyle('textTransform', $('__ed-transform').value); });
  bind('__ed-tracking', 'input', function () { setStyle('letterSpacing', +$('__ed-tracking').value); });
  bind('__ed-leading', 'input', function () { setStyle('lineHeight', +$('__ed-leading').value); });
  bind('__ed-opacity', 'input', function () { setStyle('opacity', +$('__ed-opacity').value); });
  bind('__ed-radius', 'input', function () { setStyle('borderRadius', +$('__ed-radius').value); });
  bind('__ed-rotate', 'input', function () { setStyle('rotate', +$('__ed-rotate').value); });
  bind('__ed-width', 'input', function () {
    if (!sel) return;
    var e = editFor(sel);
    if (e.add) { e.w = +$('__ed-width').value; applyAll(); }
  });
  side.addEventListener('click', function (ev) {
    var b = ev.target.closest('button[data-clear]');
    if (b && sel) { setStyle(b.dataset.clear, ''); fillInspector(); }
  });
  bind('__ed-seconds', 'input', function () {
    var v = +$('__ed-seconds').value;
    if (v >= 1.5 && v <= 12) sceneEdit().seconds = v;
  });
  bind('__ed-scene-bg', 'input', function () {
    styleOf(sceneEdit()).backgroundColor = $('__ed-scene-bg').value; applyAll();
  });
  $('__ed-scene-bg-clear').addEventListener('click', function () {
    styleOf(sceneEdit()).backgroundColor = ''; applyAll(); fillScene();
  });
  $('__ed-vars').addEventListener('input', function (ev) {
    var inp = ev.target.closest('input[data-var]');
    if (!inp) return;
    designEdit().vars[inp.dataset.var] = inp.value;
    applyAll();
  });
  $('__ed-font-swap').addEventListener('click', function () {
    var d = designEdit();
    d.font = { from: $('__ed-font-from').value, to: $('__ed-font-to').value };
    applyAll();
    flash('Typeface changed across the reel.');
  });

  // ── adding things ────────────────────────────────────────────────────
  function newId() { addCount++; return 'add' + Date.now().toString(36) + addCount; }
  function addRecord(rec) {
    rec.scene = cur; rec.id = newId(); rec.scale = 1;
    edits.set(keyOf(rec), rec);
    applyAll();
    var el = document.querySelector('#s' + cur + ' [data-ed-id="' + rec.id + '"]');
    if (el) select(el);
  }
  bar.addEventListener('click', function (ev) {
    var b = ev.target.closest('button');
    if (!b) return;
    if (b.dataset.scene !== undefined) { show(+b.dataset.scene); return; }
    if (b.id === '__ed-add-text') { addRecord({ add: 'text', x: 120, y: 880, text: 'Your text' }); return; }
    if (b.id === '__ed-add-box') { addRecord({ add: 'box', x: 340, y: 760, w: 400, h: 400 }); return; }
    if (b.id === '__ed-add-image') { bar.querySelector('#__ed-file').click(); return; }
    if (b.id === '__ed-save') {
      post('/save', function () { flash('Saved \\u2014 Prism has your changes.'); });
      return;
    }
    if (b.id === '__ed-render') {
      post('/render', function () {
        document.body.innerHTML = '<div id="__ed-done"><h2>Rendering\\u2026</h2>' +
          '<p>You can close this tab. The progress bar is in the Prism window, ' +
          'and the finished reel lands in Desktop / Prism Artifacts as usual.</p></div>';
      });
      return;
    }
    if (!sel) { flash('Click something in the scene first.'); return; }
    var e = editFor(sel);
    if (b.id === '__ed-bigger') e.scale = Math.min(20, (e.scale || 1) * 1.1);
    if (b.id === '__ed-smaller') e.scale = Math.max(0.05, (e.scale || 1) / 1.1);
    if (b.id === '__ed-front') styleOf(e).zIndex = 100;
    if (b.id === '__ed-back') styleOf(e).zIndex = 0;
    if (b.id === '__ed-delete') {
      if (e.add) { edits.delete(keyOf(e)); sel.remove(); } else { e.hidden = true; }
      select(null);
    }
    if (b.id === '__ed-reset') {
      if (e.add) { edits.delete(keyOf(e)); sel.remove(); select(null); }
      else {
        edits.delete(cur + '/' + pathOf(sel).join('.'));
        sel.style.cssText = ''; select(sel);
      }
    }
    if (b.id === '__ed-reset-scene') {
      Array.from(edits.keys()).forEach(function (k) { if (k.indexOf(cur + '/') === 0) edits.delete(k); });
      location.reload();
      return;
    }
    applyAll();
    fillInspector();
  });
  bar.querySelector('#__ed-file').addEventListener('change', function (ev) {
    var f = ev.target.files && ev.target.files[0];
    if (!f) return;
    if (f.size > 8000000) { alert('That picture is over 8 MB \\u2014 please use a smaller one.'); return; }
    var reader = new FileReader();
    reader.onload = function () {
      var img = new Image();
      img.onload = function () {
        var w = Math.min(600, img.naturalWidth || 600);
        var h = Math.round(w * (img.naturalHeight || 1) / (img.naturalWidth || 1));
        addRecord({ add: 'image', src: reader.result, x: Math.round(540 - w / 2), y: Math.round(960 - h / 2), w: w });
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(f);
    ev.target.value = '';
  });

  // ── selecting and dragging ───────────────────────────────────────────
  stage.addEventListener('mousedown', function (ev) {
    var el = ev.target.closest('#stage > .scene.on *');
    if (!el || el.isContentEditable) return;
    ev.preventDefault();
    select(el);
    var e = editFor(sel);
    drag = e.add ? { x: ev.clientX, y: ev.clientY, dx: e.x || 0, dy: e.y || 0, e: e, add: true }
                 : { x: ev.clientX, y: ev.clientY, dx: e.dx || 0, dy: e.dy || 0, e: e };
  });
  document.addEventListener('mousemove', function (ev) {
    if (!drag) return;
    var nx = drag.dx + (ev.clientX - drag.x) / scale();
    var ny = drag.dy + (ev.clientY - drag.y) / scale();
    if (drag.add) { drag.e.x = nx; drag.e.y = ny; } else { drag.e.dx = nx; drag.e.dy = ny; }
    applyAll();
  });
  document.addEventListener('mouseup', function () {
    if (drag) {
      if (drag.add) { drag.e.x = Math.round(drag.e.x); drag.e.y = Math.round(drag.e.y); }
      else { drag.e.dx = Math.round(drag.e.dx); drag.e.dy = Math.round(drag.e.dy); }
      applyAll();
    }
    drag = null;
  });
  stage.addEventListener('dblclick', function (ev) {
    var el = ev.target.closest('#stage > .scene.on *');
    if (!el || el.children.length) return;   // only leaf text
    ev.preventDefault();
    el.contentEditable = 'true';
    el.focus();
    var done = function () {
      el.contentEditable = 'false';
      el.removeEventListener('blur', done);
      editFor(el).text = el.textContent;
      applyAll();
      fillInspector();
    };
    el.addEventListener('blur', done);
  });
  document.addEventListener('keydown', function (ev) {
    if (document.activeElement && (document.activeElement.isContentEditable ||
        /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName))) return;
    if ((ev.key === 'Delete' || ev.key === 'Backspace') && sel) {
      ev.preventDefault();
      var e = editFor(sel);
      if (e.add) { edits.delete(keyOf(e)); sel.remove(); } else { e.hidden = true; }
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

        # An element of the design, by its position in its scene.
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
    script = ("<script>" + _APPLY_JS
              + "window.__edApply(" + json.dumps(edits) + ");</script>")
    return html.replace("</body></html>", script + "</body></html>")


def editable_html(spec: dict, fps: int | None = None) -> str:
    """The reel page with the edit layer on top."""
    fps = int(fps or spec.get("fps", reel_web.DEFAULT_FPS))
    existing = clean_edits(spec.get(EDITS_KEY) or [])
    html = reel_web.build_html(with_timing(spec, existing), fps)
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
