/**
 * Prism Motion Graphics Engine — Kinetic Typography & Vector Primitives
 * ─────────────────────────────────────────────────────────────────────
 * Full modern kinetic typography toolkit: the AI freely picks any mode per text node.
 * Paint is real DOM/CSS (see Node.mount/renderDOM in runtime.js) — a node's
 * `initDOM(box)` builds its STATIC structure once, `updateDOM(time)` writes
 * only what actually changes per frame.
 *
 * ShapeCircleNode and ShapeArrowNode still carry their old Canvas2D
 * `draw(ctx,time)` methods below, unused for now — Phase 2 of the DOM
 * rewrite ports these (SVG stroke-dasharray for the arrow's draw-on) and
 * the domain/{charts,diagrams,ui}.js node types; keeping the canvas
 * versions in place is a deliberate reference for that port, not a bug.
 */

// ── ShapeRectNode ─────────────────────────────────────────────────────────────
class ShapeRectNode extends Node {
  constructor(props = {}) {
    super(props);
    this.width        = props.width        || 200;
    this.height       = props.height       || 100;
    this.radius       = props.radius       || 0;
    this.fill         = props.fill         || "#FFFFFF";
    this.stroke       = props.stroke       || null;
    this.strokeWidth  = props.stroke_width || 0;
    this.shadowColor  = props.shadow_color || null;
    this.shadowBlur   = props.shadow_blur  || 0;
    this.shadowOffsetY= props.shadow_offset_y || 0;
    this.isGlass      = props.is_glass     !== undefined ? props.is_glass : false;
    this.glowColor    = props.glow_color   || null;
    this.glowBlur     = props.glow_blur    || 0;
  }

  initDOM(box) {
    const w = this.width, h = this.height;
    box.style.transform = "";
    box.style.left   = `${-w * this.anchor[0]}px`;
    box.style.top    = `${-h * this.anchor[1]}px`;
    box.style.width  = `${w}px`;
    box.style.height = `${h}px`;
    const r = Math.min(this.radius, w / 2, h / 2);
    box.style.borderRadius = `${r}px`;

    if (this.isGlass) {
      // Real glassmorphism — backdrop-filter genuinely blurs whatever's
      // behind this box (the backdrop canvas / other DOM content), not a
      // flat translucent shape standing in for it. This is the direct fix
      // for shape_rect's "flat 2015 circle" complaint.
      box.style.backdropFilter = "blur(20px)";
      box.style.webkitBackdropFilter = "blur(20px)";
      box.style.background = this.fill || "rgba(13,18,38,0.55)";
      box.style.border = "1px solid rgba(255,255,255,0.10)";
      box.style.boxShadow =
        "0 20px 42px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.06)";
      return;
    }

    if (this.fill && this.fill !== "none") box.style.background = this.fill;
    if (this.stroke && this.strokeWidth > 0) {
      box.style.boxSizing = "border-box";
      box.style.border = `${this.strokeWidth}px solid ${this.stroke}`;
    }

    // Drop-shadow AND glow together, as two comma-separated box-shadow
    // layers — the old canvas code shared one ctx.shadow* state for both,
    // so glow silently overwrote a configured drop-shadow. CSS box-shadow
    // supports both simultaneously; this is a real fix, not just a port.
    const shadows = [];
    if (this.shadowColor && this.shadowBlur > 0) {
      shadows.push(`0 ${this.shadowOffsetY}px ${this.shadowBlur}px ${this.shadowColor}`);
    }
    if (this.glowColor && this.glowBlur > 0) {
      shadows.push(`0 0 ${this.glowBlur}px ${this.glowColor}`);
    }
    if (shadows.length) box.style.boxShadow = shadows.join(", ");
  }
}

// ── ShapeCircleNode ───────────────────────────────────────────────────────────
// Phase 2: not yet ported to DOM (see file header). Old canvas draw() kept
// as the reference for that port.
class ShapeCircleNode extends Node {
  constructor(props = {}) {
    super(props);
    this.radius      = props.radius      || 50;
    this.fill        = props.fill        || "#FFFFFF";
    this.stroke      = props.stroke      || null;
    this.strokeWidth = props.stroke_width || 0;
    this.glowColor   = props.glow_color  || null;
    this.glowBlur    = props.glow_blur   || 0;
  }

  draw(ctx, time) {
    ctx.beginPath();
    ctx.arc(0, 0, this.radius, 0, Math.PI * 2);
    if (this.glowColor && this.glowBlur > 0) {
      ctx.shadowColor = this.glowColor;
      ctx.shadowBlur  = this.glowBlur;
    }
    if (this.fill && this.fill !== "none") { ctx.fillStyle = this.fill; ctx.fill(); }
    ctx.shadowColor = "transparent";
    if (this.stroke && this.strokeWidth > 0) {
      ctx.lineWidth = this.strokeWidth; ctx.strokeStyle = this.stroke; ctx.stroke();
    }
  }
}

// ── ShapeArrowNode — Bézier laser arrow with stroke trim & neon bloom ─────────
// Phase 2: not yet ported to DOM (needs SVG — see file header).
class ShapeArrowNode extends Node {
  constructor(props = {}) {
    super(props);
    this.from         = props.from         || [0, 0];
    this.to           = props.to           || [100, 0];
    this.curved       = props.curved       || false;
    this.curveHeight  = props.curve_height || 40;
    this.color        = props.color        || "#38BDF8";
    this.strokeWidth  = props.stroke_width || 6;
    this.headSize     = props.head_size    || 22;
    this.glowBlur     = props.glow_blur    || 14;
    this.trimEnd      = props.trim_end     !== undefined ? props.trim_end : 1.0;
    this.drawStart    = props.draw_start   || 0.0;
    this.drawDuration = props.draw_duration || 0.65;
    this.pulse        = props.pulse        || false;
    this.pulseColor   = props.pulse_color  || "#FFFFFF";
    this.pulseSpeed   = props.pulse_speed  || 0.9;
  }

  draw(ctx, time) {
    let progress = this.trimEnd;
    if (this.drawDuration > 0 && time >= this.drawStart) {
      const p = Math.min(1.0, (time - this.drawStart) / this.drawDuration);
      progress = EASINGS.easeOutCubic(p);
    }
    if (progress <= 0.001) return;

    const [x1, y1] = this.from;
    const [x2, y2] = this.to;
    const dx = x2 - x1, dy = y2 - y1;
    const dist = Math.hypot(dx, dy);
    if (dist < 1) return;

    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    const nx = -dy / dist,    ny = dx / dist;
    const cx = this.curved ? mx + nx * this.curveHeight : mx;
    const cy = this.curved ? my + ny * this.curveHeight : my;

    const bezier = t => {
      const it = 1 - t;
      return {
        x: it*it*x1 + 2*it*t*cx + t*t*x2,
        y: it*it*y1 + 2*it*t*cy + t*t*y2
      };
    };

    ctx.save();
    ctx.strokeStyle = this.color;
    ctx.fillStyle   = this.color;
    ctx.lineWidth   = this.strokeWidth;
    ctx.lineCap     = "round";
    ctx.lineJoin    = "round";
    ctx.shadowColor = this.color;
    ctx.shadowBlur  = this.glowBlur;

    const STEPS  = 48;
    const count  = Math.max(2, Math.floor(STEPS * progress));
    let lastPt   = { x: x1, y: y1 };
    let tangentAngle = 0;

    ctx.beginPath();
    ctx.moveTo(x1, y1);
    for (let i = 1; i <= count; i++) {
      const t  = (i / STEPS) * progress;
      const pt = bezier(t);
      ctx.lineTo(pt.x, pt.y);
      if (i === count) tangentAngle = Math.atan2(pt.y - lastPt.y, pt.x - lastPt.x);
      lastPt = pt;
    }
    ctx.stroke();

    if (progress > 0.4) {
      const hs = this.headSize * EASINGS["back.out"](Math.min(1, (progress - 0.4) / 0.3));
      ctx.save();
      ctx.translate(lastPt.x, lastPt.y);
      ctx.rotate(tangentAngle);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(-hs * 1.3, -hs * 0.6);
      ctx.lineTo(-hs * 0.85, 0);
      ctx.lineTo(-hs * 1.3,  hs * 0.6);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }

    if (this.pulse && progress >= 1.0) {
      const pt = bezier((time * this.pulseSpeed) % 1.0);
      ctx.save();
      ctx.globalCompositeOperation = "screen";
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, this.strokeWidth * 1.8, 0, Math.PI * 2);
      ctx.fillStyle   = this.pulseColor;
      ctx.shadowColor = this.pulseColor;
      ctx.shadowBlur  = 22;
      ctx.fill();
      ctx.restore();
    }

    ctx.restore();
  }
}

// ── TextNode — Full Kinetic Typography Toolkit ────────────────────────────────
class TextNode extends Node {
  constructor(props = {}) {
    super(props);
    this.content        = String(props.content || "");
    this.fontFamily     = props.font_family    || "Inter, -apple-system, sans-serif";
    this.fontSize       = props.font_size      || 48;
    this.fontWeight     = props.font_weight    || 700;
    this.fill           = props.fill           || "#FFFFFF";
    this.align          = props.align          || "center";
    this.lineHeight     = props.line_height    || 1.25;
    this.mode           = props.mode           || "standard";
    this.revealStart    = props.reveal_start   || 0.0;
    this.revealDuration = props.reveal_duration || 0.75;
    this.gradient       = props.gradient       || null;  // { from, to, stops }
    this.shadow         = props.text_shadow    || null;  // { color, blur, offsetY }
    this.letterSpacing  = props.letter_spacing || 0;

    this.staggerDelay   = props.stagger_delay  || 0.075;
    this.shimmerColor   = props.shimmer_color  || "rgba(255,255,255,0.55)";
    this.shimmerWidth   = props.shimmer_width  || 0.35;
    this.splitGap       = props.split_gap      || 40;
    this.blurAmount     = props.blur_amount    || 24;

    // Read but never wired to an actual draw call in the old engine
    // (dead config) — fixed here since counter_tick genuinely needs them.
    this.prefix = props.prefix || "";
    this.suffix = props.suffix || "";
  }

  _applyBaseTextStyle(el) {
    el.style.fontFamily = this.fontFamily;
    el.style.fontSize = `${this.fontSize}px`;
    el.style.fontWeight = String(this.fontWeight);
    el.style.lineHeight = String(this.lineHeight);
    el.style.whiteSpace = "pre";
    if (this.letterSpacing) el.style.letterSpacing = `${this.letterSpacing}px`;
    if (this.gradient && this.gradient.stops) {
      const stops = this.gradient.stops
        .slice().sort((a, b) => a.pos - b.pos)
        .map(s => `${s.color} ${Math.round(s.pos * 100)}%`).join(", ");
      el.style.backgroundImage = `linear-gradient(90deg, ${stops})`;
      el.style.webkitBackgroundClip = "text";
      el.style.backgroundClip = "text";
      el.style.color = "transparent";
    } else {
      el.style.color = this.fill;
    }
    if (this.shadow) {
      const c = this.shadow.color !== undefined ? this.shadow.color : "rgba(0,0,0,0.5)";
      const b = this.shadow.blur !== undefined ? this.shadow.blur : 12;
      const oy = this.shadow.offsetY !== undefined ? this.shadow.offsetY : 4;
      el.style.textShadow = `0 ${oy}px ${b}px ${c}`;
    }
  }

  initDOM(box) {
    box.style.transform = "translate(-50%,-50%)";
    box.style.textAlign = this.align;

    // Multi-line support the canvas engine had to hand-roll (ctx.fillText
    // never respected \n) is free here — one <div> per line, stacked by
    // lineHeight. Every line shares the same reveal clock, same as before
    // (not a true per-line cascade — see the original comment this ports).
    this._lines = this.content ? String(this.content).split(/\r?\n/) : [];
    this._lineEls = [];
    this._lineState = [];
    for (const line of this._lines) {
      const el = document.createElement("div");
      this._applyBaseTextStyle(el);
      box.appendChild(el);
      this._lineEls.push(el);
      this._lineState.push(this._initLineMode(el, line));
    }
  }

  // Builds this line's STATIC per-mode structure once. Returns whatever
  // per-line state updateDOM needs (span arrays etc.) — never rebuilt
  // per frame; only opacity/transform/textContent on existing elements
  // change (span identity must stay stable across seeks, or the staggered
  // modes would thrash layout and could flicker).
  _initLineMode(el, line) {
    switch (this.mode) {
      case "word_stagger": {
        const words = line.split(" ");
        const spans = words.map((w, i) => {
          const s = document.createElement("span");
          s.style.display = "inline-block";
          s.style.whiteSpace = "pre";
          s.textContent = w + (i < words.length - 1 ? " " : "");
          el.appendChild(s);
          return s;
        });
        return { spans };
      }
      case "char_cascade": {
        const chars = line.split("");
        const spans = chars.map(c => {
          const s = document.createElement("span");
          s.style.display = "inline-block";
          s.style.whiteSpace = "pre";
          s.textContent = c;
          el.appendChild(s);
          return s;
        });
        return { spans };
      }
      case "split_slide": {
        el.style.position = "relative";
        el.textContent = "";
        const mk = (side) => {
          const s = document.createElement("span");
          s.style.position = "absolute";
          s.style.left = "0"; s.style.top = "0"; s.style.width = "100%";
          s.style.whiteSpace = "pre";
          s.style.clipPath = side === "left"
            ? "inset(0 50% 0 0)" : "inset(0 0 0 50%)";
          s.textContent = line;
          el.appendChild(s);
          return s;
        };
        return { left: mk("left"), right: mk("right") };
      }
      case "shimmer_sweep": {
        el.style.position = "relative";
        el.textContent = "";
        const base = document.createElement("span");
        base.style.whiteSpace = "pre";
        base.textContent = line;
        el.appendChild(base);

        const shimmer = document.createElement("span");
        shimmer.style.position = "absolute";
        shimmer.style.left = "0"; shimmer.style.top = "0";
        shimmer.style.whiteSpace = "pre";
        shimmer.textContent = line;
        shimmer.style.backgroundRepeat = "no-repeat";
        shimmer.style.webkitBackgroundClip = "text";
        shimmer.style.backgroundClip = "text";
        shimmer.style.color = "transparent";
        shimmer.style.opacity = "0";
        el.appendChild(shimmer);
        return { base, shimmer };
      }
      case "typewriter": {
        el.textContent = "";
        const textSpan = document.createElement("span");
        textSpan.style.whiteSpace = "pre";
        const cursor = document.createElement("span");
        cursor.style.display = "inline-block";
        cursor.style.width = "3px";
        cursor.style.height = `${this.fontSize * 0.85}px`;
        cursor.style.verticalAlign = "-0.1em";
        cursor.style.marginLeft = "4px";
        cursor.style.background = "currentColor";
        el.appendChild(textSpan);
        el.appendChild(cursor);
        return { textSpan, cursor };
      }
      case "masked_reveal": {
        el.textContent = line;
        return {};
      }
      case "blur_pop":
      case "counter_tick":
        el.textContent = line;
        return {};
      case "standard":
      default:
        el.textContent = line;
        return {};
    }
  }

  updateDOM(time) {
    for (let i = 0; i < this._lineEls.length; i++) {
      this._updateLineMode(this._lineEls[i], this._lines[i], this._lineState[i], time, i);
    }
  }

  _updateLineMode(el, line, state, time, lineIdx) {
    switch (this.mode) {
      case "word_stagger": {
        const wDur = 0.48;
        for (let i = 0; i < state.spans.length; i++) {
          const wStart = this.revealStart + i * this.staggerDelay;
          const p = Math.max(0, Math.min(1, (time - wStart) / wDur));
          const ep = EASINGS["back.out"](p);
          const s = state.spans[i];
          s.style.opacity = String(p);
          s.style.transform = `translateY(${(1 - ep) * 30}px) scale(${0.78 + 0.22 * ep})`;
        }
        return;
      }
      case "char_cascade": {
        const cDur = 0.38;
        for (let i = 0; i < state.spans.length; i++) {
          const cStart = this.revealStart + i * (this.staggerDelay * 0.55);
          const p = Math.max(0, Math.min(1, (time - cStart) / cDur));
          const ep = EASINGS["bounce.out"](p);
          const s = state.spans[i];
          s.style.opacity = String(p);
          s.style.transform = `translateY(${-(1 - ep) * 80}px)`;
        }
        return;
      }
      case "split_slide": {
        const p = EASINGS.easeOutExpo(Math.max(0, Math.min(1,
          (time - this.revealStart) / Math.max(0.1, this.revealDuration)
        )));
        const off = (1 - p) * (this.splitGap + 200);
        state.left.style.transform = `translateX(${-off}px)`;
        state.right.style.transform = `translateX(${off}px)`;
        return;
      }
      case "shimmer_sweep": {
        const fadeEnd = this.revealStart + this.revealDuration * 0.5;
        const p = Math.max(0, Math.min(1, (time - this.revealStart) / (this.revealDuration * 0.5)));
        el.style.opacity = String(EASINGS.easeOutCubic(p));
        if (time > fadeEnd) {
          const sweepT = Math.min(1, (time - fadeEnd) / (this.revealDuration * 0.6));
          state.shimmer.style.opacity = "1";
          state.shimmer.style.backgroundImage =
            `linear-gradient(90deg, rgba(255,255,255,0) 0%, ${this.shimmerColor} 40%, ` +
            `${this.shimmerColor} 60%, rgba(255,255,255,0) 100%)`;
          state.shimmer.style.backgroundSize = "250% 100%";
          state.shimmer.style.backgroundPositionX = `${100 - sweepT * 200}%`;
        } else {
          state.shimmer.style.opacity = "0";
        }
        return;
      }
      case "typewriter": {
        const elapsed = Math.max(0, time - this.revealStart);
        const rate = Math.max(1, line.length) / Math.max(0.1, this.revealDuration);
        const visible = Math.min(line.length, Math.floor(elapsed * rate));
        state.textSpan.textContent = line.substring(0, visible);
        const done = lineIdx === this._lineEls.length - 1;
        const stillTyping = visible < line.length;
        state.cursor.style.opacity =
          (stillTyping || (done && (time * 2) % 1.0 < 0.5)) ? "1" : "0";
        return;
      }
      case "masked_reveal": {
        const p = Math.max(0, Math.min(1,
          EASINGS.easeOutCubic((time - this.revealStart) / Math.max(0.1, this.revealDuration))
        ));
        el.style.clipPath = `inset(${(1 - p) * 100}% 0 0 0)`;
        return;
      }
      case "blur_pop": {
        const p = EASINGS["back.out"](Math.max(0, Math.min(1,
          (time - this.revealStart) / Math.max(0.1, this.revealDuration)
        )));
        el.style.opacity = String(Math.max(0, Math.min(1, p)));
        el.style.filter = `blur(${Math.max(0, this.blurAmount * (1 - p))}px)`;
        el.style.transform = `scale(${0.7 + 0.3 * p})`;
        return;
      }
      case "counter_tick": {
        const target = parseFloat(line) || 0;
        const elapsed = Math.max(0, time - this.revealStart);
        const p = Math.min(1, EASINGS.easeOutCubic(elapsed / Math.max(0.1, this.revealDuration)));
        const current = target * p;
        const decimals = line.includes(".") ? line.split(".")[1].length : 0;
        const display = this.prefix +
          (decimals > 0 ? current.toFixed(decimals) : Math.floor(current).toLocaleString()) +
          this.suffix;
        el.textContent = display;
        return;
      }
      case "standard":
      default:
        return; // static — set once in _initLineMode, nothing changes per frame
    }
  }
}

// ── ImageNode — real <img>, browser-native loading ────────────────────────────
class ImageNode extends Node {
  constructor(props = {}) {
    super(props);
    this.src    = props.src    || null;
    this.width  = props.width  || 200;
    this.height = props.height || 200;
    this.radius = props.radius || 12;
  }

  initDOM(box) {
    const w = this.width, h = this.height;
    box.style.transform = "";
    box.style.left   = `${-w * this.anchor[0]}px`;
    box.style.top    = `${-h * this.anchor[1]}px`;
    box.style.width  = `${w}px`;
    box.style.height = `${h}px`;
    const r = Math.min(this.radius, w / 2, h / 2);
    box.style.borderRadius = `${r}px`;
    box.style.overflow = "hidden";
    // Placeholder look while loading (or if the src never resolves) — a
    // flat glass-style box, not Chromium's native broken-image glyph.
    box.style.background = "rgba(15,23,42,0.75)";
    box.style.backdropFilter = "blur(20px)";
    box.style.webkitBackdropFilter = "blur(20px)";

    const img = document.createElement("img");
    img.style.width = "100%";
    img.style.height = "100%";
    // Matches the old engine's drawImage(img,x,y,w,h) exactly — stretched
    // to the box, aspect ratio not preserved. object-fit:cover/contain
    // would be a real behavior change, not a faithful port.
    img.style.objectFit = "fill";
    img.style.display = "block";
    img.addEventListener("load", () => {
      box.style.background = "";
      box.style.backdropFilter = "";
      box.style.webkitBackdropFilter = "";
    });
    img.addEventListener("error", () => {
      img.style.display = "none"; // never show the native broken-image glyph
    });
    if (this.src) img.src = this.src;
    box.appendChild(img);
    this._img = img;
  }
}

// ── Node Factory ──────────────────────────────────────────────────────────────
function createNodeFromSpec(nodeData) {
  if (!nodeData) return null;
  const type = nodeData.type;
  let node;
  if      (type === "shape_rect"  || type === "rect")        node = new ShapeRectNode(nodeData);
  else if (type === "shape_circle"|| type === "circle")      node = new ShapeCircleNode(nodeData);
  else if (type === "shape_arrow" || type === "arrow")       node = new ShapeArrowNode(nodeData);
  else if (type === "text")                                  node = new TextNode(nodeData);
  else if (type === "image")                                 node = new ImageNode(nodeData);
  else if ((type === "domain_chart" || type === "chart") && window.DomainChartNode)
    node = new window.DomainChartNode(nodeData);
  else if ((type === "domain_ui" || type === "domain_ui_mockup" || type === "ui_mockup") && window.DomainUIMockupNode)
    node = new window.DomainUIMockupNode(nodeData);
  else if ((type === "domain_diagram" || type === "diagram" || type === "workflow") && window.DomainDiagramNode)
    node = new window.DomainDiagramNode(nodeData);
  else
    node = new Node(nodeData);

  if (Array.isArray(nodeData.children)) {
    for (const childData of nodeData.children) {
      const child = createNodeFromSpec(childData);
      if (child) node.addChild(child);
    }
  }
  return node;
}

window.ShapeRectNode    = ShapeRectNode;
window.ShapeCircleNode  = ShapeCircleNode;
window.ShapeArrowNode   = ShapeArrowNode;
window.TextNode         = TextNode;
window.ImageNode        = ImageNode;
window.createNodeFromSpec = createNodeFromSpec;
