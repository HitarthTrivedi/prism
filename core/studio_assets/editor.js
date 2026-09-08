/* Studio V2 browser workspace: one edit state feeds preview and export.
 *
 * Everything the owner can do here is one of the four record kinds
 * core/reel_edit.py sanitises (element / added / scene / design), applied
 * through the SAME apply.js the renderer runs before filming. This file only
 * builds records and the chrome around them; it never styles a scene node
 * directly, so nothing the preview shows can differ from the export.
 *
 * Layout: the harness keeps #stage at its real 1080x1920. The workspace wraps
 * it in a fixed viewport and scales the whole stage as one unit (V1 did the
 * same) — never the scenes, whose `transform` the cut transitions own. */
(function () {
  'use strict';
  var FONTS = ['Inter', 'Roboto', 'Montserrat', 'Poppins', 'DM Sans', 'Work Sans',
    'Nunito', 'Space Grotesk', 'Oswald', 'Bebas Neue', 'Playfair Display',
    'Libre Baskerville', 'Lora', 'Merriweather', 'Fraunces', 'Source Serif 4',
    'Cormorant Garamond', 'Arial', 'Helvetica', 'Georgia', 'Times New Roman'];
  var W = 1080, H = 1920;
  var scenes = window.__SCENES__ || [], stage = document.getElementById('stage');
  var edits = new Map(), undo = [], redo = [], current = 0, selected = null;
  var playing = false, time = 0, zoom = 1, base = 1, snap = true, drag, saveTimer, addCount = 0;
  (window.__STUDIO_EDITS__ || []).forEach(function (e) { edits.set(key(e), e); });

  // ── records ──────────────────────────────────────────────────────────────
  function q(s, root) { return (root || document).querySelector(s); }
  function all() { return Array.from(edits.values()); }
  function key(e) {
    if (e.design) return 'design';
    if (e.root) return e.scene + '/root';
    if (e.add) return e.scene + '/+' + e.id;
    return e.scene + '/' + (e.element_id || (e.path || []).join('.'));
  }
  function record(el) {
    el = el && el.closest('[data-prism-id],[data-ed-id]');
    if (!el) return null;
    if (el.dataset.edId) {
      // apply.js built this node from a record, so the record exists unless
      // the page was edited by hand; infer the kind from the class it set.
      var ak = current + '/+' + el.dataset.edId;
      if (!edits.has(ak)) {
        var kind = /__ed-image/.test(el.className) ? 'image' : /__ed-box/.test(el.className) ? 'box' : 'text';
        edits.set(ak, {scene: current, add: kind, id: el.dataset.edId, x: 0, y: 0});
      }
      return edits.get(ak);
    }
    var k = current + '/' + el.dataset.prismId;
    if (!edits.has(k)) edits.set(k, {scene: current, element_id: el.dataset.prismId, dx: 0, dy: 0, scale: 1});
    return edits.get(k);
  }
  function sceneRecord() {
    var k = current + '/root';
    if (!edits.has(k)) edits.set(k, {scene: current, root: true});
    return edits.get(k);
  }
  function designRecord() {
    if (!edits.has('design')) edits.set('design', {design: true, vars: {}});
    var d = edits.get('design'); d.vars = d.vars || {}; return d;
  }
  function styleOf(e) { e.style = e.style || {}; return e.style; }
  function newId() { addCount++; return 'add' + Date.now().toString(36) + addCount; }

  // ── undo / apply / save ──────────────────────────────────────────────────
  function apply() { window.__edApply(all()); layers(); autosave(); }
  function saveState() { undo.push(JSON.stringify(all())); if (undo.length > 80) undo.shift(); redo = []; buttons(); }
  function restore(state) {
    edits.clear(); JSON.parse(state).forEach(function (e) { edits.set(key(e), e); });
    // An added element that no longer has a record must leave the page too.
    document.querySelectorAll('#stage [data-ed-id]').forEach(function (el) {
      var sceneNo = +el.closest('.scene').id.slice(1);
      if (!edits.has(sceneNo + '/+' + el.dataset.edId)) el.remove();
    });
    window.__edApply(all()); pick(null); refresh(); autosave();
  }
  function buttons() { q('#studio-undo').disabled = !undo.length; q('#studio-redo').disabled = !redo.length; }
  function post(path, extra, done) {
    var body = {edits: all()}; Object.assign(body, extra || {});
    fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function () { done(true); }).catch(function () { done(false); });
  }
  function status(text) { q('#studio-status').textContent = text; }
  function autosave() {
    status('Saving…'); clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { post('/autosave', null, function (ok) { status(ok ? 'Saved' : 'Save failed'); }); }, 700);
  }

  // ── time ─────────────────────────────────────────────────────────────────
  function total() { var s = scenes[scenes.length - 1]; return s ? s.start + s.dur : 0; }
  function fmt(ms) { var n = Math.max(0, ms) / 1000; return Math.floor(n / 60) + ':' + ('0' + Math.floor(n % 60)).slice(-2) + '.' + ('00' + Math.floor(n % 1 * 100)).slice(-2); }
  function sceneAt(ms) { for (var i = 0; i < scenes.length; i++) if (ms < scenes[i].start + scenes[i].dur) return i; return scenes.length - 1; }
  function seek(ms) {
    time = Math.max(0, Math.min(total(), ms)); window.__seek(time);
    var next = sceneAt(time);
    if (next !== current) { current = next; pick(null); sceneUI(); }
    q('#studio-time').textContent = fmt(time) + ' / ' + fmt(total());
    q('#studio-scrub').value = time;
    q('#studio-timeline').style.setProperty('--playhead', (100 * time / Math.max(1, total())) + '%');
  }
  function showScene(i) {
    current = Math.max(0, Math.min(scenes.length - 1, i));
    pick(null);
    // 60% in: past the entrance animations, before the exit.
    seek(scenes[current].start + scenes[current].dur * 0.6);
    refresh();
  }
  function toggle() {
    if (playing) { playing = false; return; }
    playing = true; q('#studio-play').textContent = '❚❚ Pause';
    var previous = performance.now();
    (function loop(now) {
      if (!playing) { q('#studio-play').textContent = '▶ Play'; return; }
      seek(time + now - previous); previous = now;
      if (time < total()) requestAnimationFrame(loop); else playing = false;
    })(previous);
  }

  // ── the canvas ───────────────────────────────────────────────────────────
  var viewport = document.createElement('div'); viewport.id = 'studio-viewport';
  stage.parentNode.insertBefore(viewport, stage); viewport.appendChild(stage);
  function fit() {
    var w = viewport.clientWidth - 48, h = viewport.clientHeight - 36;
    base = Math.max(0.05, Math.min(w / W, h / H));
    var scale = base * zoom;
    stage.style.transformOrigin = '0 0';
    stage.style.transform = 'translate(' + Math.max(24, (viewport.clientWidth - W * scale) / 2) + 'px,18px) scale(' + scale + ')';
  }

  // ── selection ────────────────────────────────────────────────────────────
  function pick(el) {
    if (selected) selected.classList.remove('__studio-sel');
    selected = el && el.closest('[data-prism-id],[data-ed-id]');
    if (selected) selected.classList.add('__studio-sel');
    layers(); inspector();
  }
  function label(el) {
    if (el.dataset.edId) return el.className.indexOf('__ed-image') !== -1 ? 'Added picture' : el.className.indexOf('__ed-box') !== -1 ? 'Added shape' : 'Added text';
    var t = (el.textContent || '').trim().replace(/\s+/g, ' ');
    return t ? '“' + t.slice(0, 34) + (t.length > 34 ? '…' : '') + '”' : '<' + el.tagName.toLowerCase() + '>';
  }

  // ── colour / font helpers ────────────────────────────────────────────────
  function toHex(c) {
    var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c || '');
    if (!m) return /^#[0-9a-f]{6}$/i.test(c || '') ? c : '#000000';
    return '#' + [m[1], m[2], m[3]].map(function (v) { return ('0' + (+v).toString(16)).slice(-2); }).join('');
  }
  function isColour(v) { return /^(#[0-9a-f]{3,8}|rgba?\(|hsla?\()/i.test(v || ''); }
  function firstFamily(fam) { return (fam || '').split(',')[0].replace(/["']/g, '').trim(); }
  function fontsInUse() {
    var seen = {};
    try { document.fonts.forEach(function (f) { seen[f.family.replace(/["']/g, '')] = 1; }); } catch (e) {}
    stage.querySelectorAll('*').forEach(function (el) {
      var t = (el.textContent || '').trim();
      if (t && !el.children.length) seen[firstFamily(getComputedStyle(el).fontFamily)] = 1;
    });
    return Object.keys(seen).filter(Boolean);
  }
  function rootVars() {
    var out = {};
    for (var i = 0; i < document.styleSheets.length; i++) {
      var rules; try { rules = document.styleSheets[i].cssRules; } catch (e) { continue; }
      for (var j = 0; j < rules.length; j++) {
        var r = rules[j];
        if (!r.selectorText || !/(^|,)\s*:root\s*(,|$)/.test(r.selectorText)) continue;
        for (var k = 0; k < r.style.length; k++) {
          var p = r.style[k];
          if (p.indexOf('--') === 0) out[p] = r.style.getPropertyValue(p).trim();
        }
      }
    }
    var d = edits.get('design');
    if (d && d.vars) Object.keys(d.vars).forEach(function (v) { out[v] = d.vars[v]; });
    return out;
  }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }
  function opts(list, cur) { return list.map(function (v) { return '<option' + (v === cur ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join(''); }
  function field(id, labelText, control) { return '<div class="studio-f"><label class="studio-label" for="' + id + '">' + labelText + '</label>' + control + '</div>'; }

  // ── the inspector: the selected layer ────────────────────────────────────
  function inspector() {
    var h = q('#studio-inspector');
    if (!selected) { h.innerHTML = '<p class="studio-muted">Select a layer to move it, retype it, restyle it, or hide it. Add text, a picture or a shape from the top bar.</p>'; return; }
    var e = record(selected), st = e.style || {}, cs = getComputedStyle(selected);
    var isAdd = !!e.add, isImage = e.add === 'image', isBox = e.add === 'box';
    var text = typeof e.text === 'string' ? e.text : (!selected.children.length ? selected.textContent : '');
    var families = fontsInUse().concat(FONTS).filter(function (f, i, a) { return a.indexOf(f) === i; });
    var fam = st.fontFamily || firstFamily(cs.fontFamily);
    if (families.indexOf(fam) === -1) families.unshift(fam);
    var html = '<p class="studio-kicker">Selected layer</p><p class="studio-muted">' + esc(label(selected)) + '</p>';
    if (!isImage && !isBox) html += field('studio-text', 'Text', '<input class="studio-field" id="studio-text" value="' + esc(text) + '">');
    html += '<div class="studio-row">' + field('studio-x', isAdd ? 'X' : 'Move X', '<input class="studio-field" id="studio-x" type="number" value="' + (isAdd ? (e.x || 0) : (e.dx || 0)) + '">')
      + field('studio-y', isAdd ? 'Y' : 'Move Y', '<input class="studio-field" id="studio-y" type="number" value="' + (isAdd ? (e.y || 0) : (e.dy || 0)) + '">') + '</div>';
    html += '<div class="studio-row">' + field('studio-scale-input', 'Scale', '<input class="studio-field" id="studio-scale-input" type="number" step=".05" min=".05" max="20" value="' + (e.scale || 1) + '">')
      + '<button class="studio-btn" id="studio-smaller" title="Smaller">−</button><button class="studio-btn" id="studio-bigger" title="Bigger">+</button></div>';
    if (isAdd) html += field('studio-width', 'Width (px)', '<input class="studio-field" id="studio-width" type="number" min="1" max="2000" value="' + (e.w || Math.round(selected.getBoundingClientRect().width / (base * zoom))) + '">');
    if (!isImage) {
      html += '<div class="studio-row">' + field('studio-color', 'Colour', '<input class="studio-color" id="studio-color" type="color" value="' + toHex(st.color || cs.color) + '">')
        + field('studio-bg', 'Background', '<input class="studio-color" id="studio-bg" type="color" value="' + toHex(st.backgroundColor || cs.backgroundColor) + '">')
        + '<button class="studio-btn" data-clear="backgroundColor" title="No background">none</button></div>';
    }
    if (!isImage && !isBox) {
      html += field('studio-font', 'Font', '<select class="studio-field" id="studio-font">' + opts(families, fam) + '</select>');
      html += '<div class="studio-row">' + field('studio-size', 'Size (px)', '<input class="studio-field" id="studio-size" type="number" min="8" max="400" value="' + (st.fontSize || Math.round(parseFloat(cs.fontSize))) + '">')
        + field('studio-weight', 'Weight', '<select class="studio-field" id="studio-weight">' + opts(['400', '500', '600', '700', '800', '900'], String(st.fontWeight || (cs.fontWeight === 'bold' ? '700' : cs.fontWeight))) + '</select>')
        + field('studio-align', 'Align', '<select class="studio-field" id="studio-align">' + opts(['left', 'center', 'right'], st.textAlign || (cs.textAlign === 'start' ? 'left' : cs.textAlign)) + '</select>') + '</div>';
    }
    html += field('studio-opacity', 'Opacity', '<input class="studio-range" id="studio-opacity" type="range" min="0" max="1" step=".05" value="' + (st.opacity != null ? st.opacity : cs.opacity || 1) + '">');
    html += '<div class="studio-row"><button class="studio-btn" id="studio-front">Bring to front</button><button class="studio-btn" id="studio-back">Send back</button></div>';
    html += '<div class="studio-row"><button class="studio-btn" id="studio-hide">' + (e.hidden ? 'Show layer' : 'Hide layer') + '</button>'
      + '<button class="studio-btn" id="studio-reset" title="Back to how the design had it">Reset</button>'
      + '<button class="studio-btn danger" id="studio-delete">Delete</button></div>';
    h.innerHTML = html;

    // One undo step per control the owner touches: snapshot on focus, then
    // every input/change on that control mutates the live record.
    h.querySelectorAll('input,select').forEach(function (c) { c.addEventListener('focus', saveState); });
    function on(id, ev, fn) { var c = q('#' + id); if (c) c.addEventListener(ev, function () { var r = record(selected); if (r) { fn(r, this.value); apply(); } }); }
    on('studio-text', 'input', function (r, v) { r.text = v; });
    on('studio-x', 'input', function (r, v) { r[isAdd ? 'x' : 'dx'] = +v || 0; });
    on('studio-y', 'input', function (r, v) { r[isAdd ? 'y' : 'dy'] = +v || 0; });
    on('studio-scale-input', 'input', function (r, v) { r.scale = Math.min(20, Math.max(0.05, +v || 1)); });
    on('studio-width', 'input', function (r, v) { r.w = Math.max(1, +v || 1); });
    on('studio-color', 'input', function (r, v) { styleOf(r).color = v; });
    on('studio-bg', 'input', function (r, v) { styleOf(r).backgroundColor = v; });
    on('studio-font', 'change', function (r, v) { styleOf(r).fontFamily = v; });
    on('studio-size', 'input', function (r, v) { styleOf(r).fontSize = +v; });
    on('studio-weight', 'change', function (r, v) { styleOf(r).fontWeight = v; });
    on('studio-align', 'change', function (r, v) { styleOf(r).textAlign = v; });
    on('studio-opacity', 'input', function (r, v) { styleOf(r).opacity = +v; });
    h.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () {
        var r = record(selected); if (!r) return;
        saveState();
        if (b.dataset.clear) styleOf(r)[b.dataset.clear] = '';
        if (b.id === 'studio-bigger') r.scale = Math.min(20, (r.scale || 1) * 1.1);
        if (b.id === 'studio-smaller') r.scale = Math.max(0.05, (r.scale || 1) / 1.1);
        if (b.id === 'studio-front') styleOf(r).zIndex = 100;
        if (b.id === 'studio-back') styleOf(r).zIndex = 0;
        if (b.id === 'studio-hide') r.hidden = !r.hidden;
        if (b.id === 'studio-delete') { if (r.add) { edits.delete(key(r)); selected.remove(); } else r.hidden = true; }
        if (b.id === 'studio-reset') { edits.delete(key(r)); if (r.add) selected.remove(); else selected.style.cssText = ''; }
        var keep = selected && selected.isConnected && b.id !== 'studio-delete' && b.id !== 'studio-reset';
        apply(); pick(keep ? selected : null); if (keep) inspector();
      });
    });
  }

  // ── the scene and the design, always on the right ────────────────────────
  function sceneUI() {
    var se = edits.get(current + '/root') || {}, sc = q('#s' + current), s = scenes[current] || {dur: 3000};
    var secs = se.seconds || Math.round((s.dur / 1000) * 2) / 2;
    q('#studio-seconds').value = secs;
    q('#studio-scene-bg').value = toHex((se.style && se.style.backgroundColor) || (sc ? getComputedStyle(sc).backgroundColor : ''));
    var vars = rootVars(), html = '';
    Object.keys(vars).forEach(function (v) {
      if (!isColour(vars[v])) return;
      html += '<div class="studio-row"><span class="studio-label" title="' + esc(v) + '">' + esc(v.replace(/^--/, '')) + '</span><input class="studio-color" data-var="' + esc(v) + '" type="color" value="' + toHex(vars[v]) + '"></div>';
    });
    q('#studio-vars').innerHTML = html || '<p class="studio-muted">This design sets no palette variables.</p>';
    var used = fontsInUse();
    q('#studio-font-from').innerHTML = opts(used, used[0]);
    if (!q('#studio-font-to').options.length) q('#studio-font-to').innerHTML = opts(FONTS, FONTS[0]);
  }

  // ── lists ────────────────────────────────────────────────────────────────
  function scenesList() {
    var h = q('#studio-scenes'); h.innerHTML = '';
    scenes.forEach(function (s, i) {
      var b = document.createElement('button'); b.className = 'scene-card ' + (i === current ? 'active' : '');
      b.innerHTML = '<i class="scene-thumb"></i><span><strong>Scene ' + (i + 1) + '</strong><small>' + fmt(s.dur) + ' · ' + esc(s.type || 'scene') + '</small></span>';
      b.onclick = function () { showScene(i); }; h.appendChild(b);
    });
  }
  function layers() {
    var h = q('#studio-layers'); if (!h) return; h.innerHTML = '';
    document.querySelectorAll('#s' + current + ' [data-prism-id],#s' + current + ' [data-ed-id]').forEach(function (el) {
      var b = document.createElement('button'); b.className = 'layer-row ' + (el === selected ? 'active' : '');
      b.innerHTML = '<i class="layer-dot"></i><span>' + esc(label(el)) + '</span>';
      b.onclick = function () { pick(el); }; h.appendChild(b);
    });
  }
  function timeline() {
    var h = q('#studio-timeline'); h.innerHTML = '';
    scenes.forEach(function (s, i) {
      var b = document.createElement('button'); b.className = 'scene-block ' + (i === current ? 'active' : '');
      b.style.flex = Math.max(.2, s.dur / 1000); b.textContent = (i + 1) + ' · ' + fmt(s.dur);
      b.onclick = function () { showScene(i); }; h.appendChild(b);
    });
  }
  function refresh() { scenesList(); layers(); inspector(); timeline(); sceneUI(); fit(); buttons(); seek(time); }

  // ── chrome ───────────────────────────────────────────────────────────────
  var top = document.createElement('header'); top.id = 'studio-top';
  top.innerHTML = '<span class="brand">Prism <i>Studio</i></span>'
    + '<button class="studio-btn" id="studio-play">▶ Play</button>'
    + '<button class="studio-btn" id="studio-undo">Undo</button><button class="studio-btn" id="studio-redo">Redo</button>'
    + '<button class="studio-btn active" id="studio-snap">Snap 8px</button>'
    + '<span class="studio-sep"></span>'
    + '<button class="studio-btn" id="studio-add-text" title="Add a line of text to this scene">+ Text</button>'
    + '<button class="studio-btn" id="studio-add-image" title="Add a picture from this computer">+ Picture</button>'
    + '<button class="studio-btn" id="studio-add-box" title="Add a colour block">+ Shape</button>'
    + '<input id="studio-file" type="file" accept="image/*" hidden>'
    + '<span class="studio-spacer"></span>'
    + '<button class="studio-btn" id="studio-zoom-out">−</button><button class="studio-btn" id="studio-zoom-in">+</button>'
    + '<span class="studio-status" id="studio-status">Ready</span>'
    + '<button class="studio-btn" id="studio-save">Save</button>'
    + '<button class="studio-btn primary" id="studio-render">Render MP4</button>';
  document.body.appendChild(top);
  var left = document.createElement('aside'); left.id = 'studio-left';
  left.innerHTML = '<section class="studio-section"><p class="studio-kicker">Scenes</p><div id="studio-scenes"></div></section>'
    + '<section class="studio-section"><p class="studio-kicker">Layers</p><div id="studio-layers"></div></section>';
  document.body.appendChild(left);
  var right = document.createElement('aside'); right.id = 'studio-right';
  right.innerHTML = '<section class="studio-section"><div id="studio-inspector"></div></section>'
    + '<section class="studio-section"><p class="studio-kicker">This scene</p>'
    + '<div class="studio-row">' + field('studio-seconds', 'Length (s)', '<input class="studio-field" id="studio-seconds" type="number" min="1.5" max="12" step="0.5">')
    + field('studio-scene-bg', 'Background', '<input class="studio-color" id="studio-scene-bg" type="color">')
    + '<button class="studio-btn" id="studio-scene-bg-clear" title="Back to the design\'s background">reset</button></div></section>'
    + '<section class="studio-section"><p class="studio-kicker">Whole reel</p><div id="studio-vars"></div>'
    + '<div class="studio-row">' + field('studio-font-from', 'Typeface', '<select class="studio-field" id="studio-font-from"></select>')
    + field('studio-font-to', '→ becomes', '<select class="studio-field" id="studio-font-to"></select>')
    + '<button class="studio-btn" id="studio-font-swap">apply</button></div></section>';
  document.body.appendChild(right);
  var bottom = document.createElement('footer'); bottom.id = 'studio-bottom';
  bottom.innerHTML = '<div class="transport"><button class="studio-btn" id="studio-play-2">▶</button><span class="time" id="studio-time"></span>'
    + '<input class="studio-range" id="studio-scrub" type="range" min="0" step="1"><span class="studio-spacer"></span><span class="studio-muted">Scene timeline</span></div>'
    + '<div class="timeline" id="studio-timeline"></div>'
    + '<div class="prompt-row"><input class="studio-field" id="studio-prompt" placeholder="Describe a change to the selected layer or scene…"><button class="studio-btn primary" id="studio-refine">Refine</button></div>'
    + '<div class="studio-help">Space play/pause · Ctrl/⌘ Z undo · arrows nudge · Shift arrows 10px · Delete removes · +/- zoom</div>';
  document.body.appendChild(bottom);

  // ── wiring ───────────────────────────────────────────────────────────────
  q('#studio-scrub').max = total(); q('#studio-scrub').oninput = function () { seek(+this.value); };
  q('#studio-play').onclick = toggle; q('#studio-play-2').onclick = toggle;
  q('#studio-save').onclick = function () { post('/save', null, function (ok) { status(ok ? 'Saved' : 'Save failed'); }); };
  q('#studio-render').onclick = function () { post('/render', null, function (ok) { status(ok ? 'Rendering…' : 'Render failed'); }); };
  q('#studio-undo').onclick = function () { if (undo.length) { redo.push(JSON.stringify(all())); restore(undo.pop()); } };
  q('#studio-redo').onclick = function () { if (redo.length) { undo.push(JSON.stringify(all())); restore(redo.pop()); } };
  q('#studio-snap').onclick = function () { snap = !snap; this.classList.toggle('active', snap); };
  q('#studio-zoom-in').onclick = function () { zoom = Math.min(2, zoom + .1); fit(); };
  q('#studio-zoom-out').onclick = function () { zoom = Math.max(.25, zoom - .1); fit(); };

  function addRecord(rec) {
    saveState();
    rec.scene = current; rec.id = newId(); rec.scale = 1;
    edits.set(key(rec), rec); apply();
    var el = q('#s' + current + ' [data-ed-id="' + rec.id + '"]');
    if (el) pick(el);
  }
  q('#studio-add-text').onclick = function () { addRecord({add: 'text', x: 120, y: 880, text: 'Your text', style: {fontSize: 64, color: '#ffffff'}}); };
  q('#studio-add-box').onclick = function () { addRecord({add: 'box', x: 340, y: 760, w: 400, h: 400, style: {backgroundColor: '#ffffff', opacity: 0.85}}); };
  q('#studio-add-image').onclick = function () { q('#studio-file').click(); };
  q('#studio-file').onchange = function () {
    var f = this.files && this.files[0]; this.value = ''; if (!f) return;
    if (f.size > 8000000) { status('That picture is too big (8 MB max)'); return; }
    var reader = new FileReader();
    reader.onload = function () { addRecord({add: 'image', x: 140, y: 600, w: 800, src: String(reader.result)}); };
    reader.readAsDataURL(f);
  };

  // Scene and design controls.
  q('#studio-seconds').addEventListener('focus', saveState);
  q('#studio-seconds').addEventListener('input', function () { var v = +this.value; if (v >= 1.5 && v <= 12) { sceneRecord().seconds = v; autosave(); status('Length applies on the next render'); } });
  q('#studio-scene-bg').addEventListener('focus', saveState);
  q('#studio-scene-bg').addEventListener('input', function () { styleOf(sceneRecord()).backgroundColor = this.value; apply(); });
  q('#studio-scene-bg-clear').onclick = function () { saveState(); styleOf(sceneRecord()).backgroundColor = ''; apply(); sceneUI(); };
  q('#studio-vars').addEventListener('focusin', function (ev) { if (ev.target.closest('input[data-var]')) saveState(); });
  q('#studio-vars').addEventListener('input', function (ev) { var inp = ev.target.closest('input[data-var]'); if (!inp) return; designRecord().vars[inp.dataset.var] = inp.value; apply(); });
  q('#studio-font-swap').onclick = function () { saveState(); designRecord().font = {from: q('#studio-font-from').value, to: q('#studio-font-to').value}; apply(); status('Typeface changed across the reel'); };

  q('#studio-refine').onclick = function () {
    var change = q('#studio-prompt').value.trim(); if (!change) return;
    post('/refine', {change: change, context: {scene_index: current, scene_id: q('#s' + current).dataset.prismScene || '',
      element_id: selected ? (selected.dataset.prismId || selected.dataset.edId || '') : '', label: selected ? label(selected) : 'scene'}},
      function (ok) { status(ok ? 'Follow-up started' : 'Follow-up unavailable'); if (ok) q('#studio-prompt').value = ''; });
  };

  // Drag to move. Added elements move by x/y, design elements by dx/dy.
  stage.addEventListener('mousedown', function (event) {
    var el = event.target.closest('[data-prism-id],[data-ed-id]'); if (!el) return;
    event.preventDefault(); pick(el); saveState();
    drag = {x: event.clientX, y: event.clientY, r: record(selected), moved: false};
  });
  addEventListener('mousemove', function (event) {
    if (!drag) return;
    var dx = (event.clientX - drag.x) / (base * zoom), dy = (event.clientY - drag.y) / (base * zoom);
    if (snap) { dx = Math.round(dx / 8) * 8; dy = Math.round(dy / 8) * 8; }
    if (!dx && !dy) return;
    var r = drag.r, kx = r.add ? 'x' : 'dx', ky = r.add ? 'y' : 'dy';
    r[kx] = (r[kx] || 0) + dx; r[ky] = (r[ky] || 0) + dy;
    drag.x = event.clientX; drag.y = event.clientY; drag.moved = true;
    window.__edApply(all());
  });
  addEventListener('mouseup', function () { if (drag) { var moved = drag.moved; drag = null; if (moved) { apply(); inspector(); } } });
  stage.addEventListener('dblclick', function (event) {
    if (event.target.closest('[data-prism-id],[data-ed-id]')) { var t = q('#studio-text'); if (t) { t.focus(); t.select(); } }
  });
  addEventListener('keydown', function (event) {
    if (/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName)) return;
    var k = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && k === 'z') { event.preventDefault(); q(event.shiftKey ? '#studio-redo' : '#studio-undo').click(); }
    else if ((event.ctrlKey || event.metaKey) && k === 'y') { event.preventDefault(); q('#studio-redo').click(); }
    else if (event.key === ' ') { event.preventDefault(); toggle(); }
    else if (selected && (event.key === 'Delete' || event.key === 'Backspace')) { event.preventDefault(); var d = q('#studio-delete'); if (d) d.click(); }
    else if (selected && /^Arrow/.test(event.key)) {
      event.preventDefault(); saveState();
      var r = record(selected), n = event.shiftKey ? 10 : 1, kx = r.add ? 'x' : 'dx', ky = r.add ? 'y' : 'dy';
      r[kx] = (r[kx] || 0) + (event.key === 'ArrowRight' ? n : event.key === 'ArrowLeft' ? -n : 0);
      r[ky] = (r[ky] || 0) + (event.key === 'ArrowDown' ? n : event.key === 'ArrowUp' ? -n : 0);
      apply(); inspector();
    }
    else if (event.key === '+' || event.key === '=') q('#studio-zoom-in').click();
    else if (event.key === '-') q('#studio-zoom-out').click();
    else if (event.key === 'Escape') pick(null);
  });
  addEventListener('resize', fit);
  window.__edApply(all()); showScene(0);
})();
