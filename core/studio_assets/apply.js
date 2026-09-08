/* Shared by Studio preview and the deterministic export page. */
(function () {
  'use strict';
  // __edFont / fonts.googleapis.com are retained as a compatibility marker
  // for old saved inspector records; a browser can safely fall back to its
  // local font when offline.
  window.__edFont = function (family) {
    if (!family || document.getElementById('__ed-font-' + family)) return;
    var link = document.createElement('link'); link.id = '__ed-font-' + family;
    link.rel = 'stylesheet'; link.href = 'https://fonts.googleapis.com/css2?family=' + encodeURIComponent(family) + '&display=swap';
    document.head.appendChild(link);
  };
  window.__edStyle = function (node, style) {
    if (!node || !style) return;
    var px = ['fontSize', 'letterSpacing', 'borderRadius', 'width', 'height', 'padding'];
    Object.keys(style).forEach(function (key) {
      var value = style[key];
      if (value === null || value === undefined || value === '') { node.style[key] = ''; return; }
      if (px.indexOf(key) !== -1) node.style[key] = value + 'px';
      else if (key === 'rotate') node.style.rotate = value + 'deg';
      else if (key === 'fontFamily') { node.style.fontFamily = value + ', sans-serif'; window.__edFont(value); }
      else node.style[key] = String(value);
    });
  };
  function byId(scene, edit) {
    if (edit.element_id) {
      return scene.querySelector('[data-prism-id="' + CSS.escape(edit.element_id) + '"]');
    }
    /* Reads saved pre-V2 layouts while migrated layouts use element_id. */
    var node = scene, path = edit.path || [];
    for (var i = 0; node && i < path.length; i++) node = node.children[path[i]];
    return node === scene ? null : node;
  }
  window.__edApply = function (edits) {
    (edits || []).forEach(function (edit) {
      if (edit.design) {
        Object.keys(edit.vars || {}).forEach(function (key) {
          document.documentElement.style.setProperty(key, edit.vars[key]);
        });
        return;
      }
      var scene = document.getElementById('s' + edit.scene);
      if (!scene) return;
      if (edit.root) { /* e.root legacy spelling */ window.__edStyle(scene, edit.style); return; }
      if (edit.add) {
        var added = scene.querySelector('[data-ed-id="' + CSS.escape(edit.id) + '"]');
        if (!added) {
          added = document.createElement(edit.add === 'image' ? 'figure' : 'div');
          added.dataset.edId = edit.id;
          added.dataset.prismId = 'added-' + edit.id;
          added.className = '__ed-add __ed-' + edit.add;
          added.style.cssText = 'position:absolute;z-index:70;box-sizing:border-box';
          if (edit.add === 'image') { var img = document.createElement('img'); img.draggable = false; img.style.cssText = 'width:100%;display:block'; added.appendChild(img); }
          scene.appendChild(added);
        }
        added.style.left = (edit.x || 0) + 'px'; added.style.top = (edit.y || 0) + 'px';
        added.style.width = edit.w ? edit.w + 'px' : (edit.add === 'text' ? 'auto' : '300px');
        added.style.height = edit.h ? edit.h + 'px' : '';
        added.style.display = edit.hidden ? 'none' : '';
        added.style.translate = ''; added.style.scale = edit.scale && edit.scale !== 1 ? String(edit.scale) : '';
        if (edit.add === 'text') added.textContent = edit.text || '';
        if (edit.add === 'image' && edit.src) { var image = added.querySelector('img'); if (image) image.src = edit.src; }
        window.__edStyle(added, edit.style); return;
      }
      var node = byId(scene, edit);
      if (!node) return;
      node.style.display = edit.hidden ? 'none' : '';
      node.style.translate = (edit.dx || edit.dy) ? (edit.dx || 0) + 'px ' + (edit.dy || 0) + 'px' : '';
      node.style.scale = edit.scale && edit.scale !== 1 ? String(edit.scale) : '';
      if (typeof edit.text === 'string') node.textContent = edit.text;
      window.__edStyle(node, edit.style);
    });
  };
})();
