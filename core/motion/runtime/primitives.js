/**
 * Prism Motion Graphics Engine — Node subclasses (GSAP rebuild)
 * ─────────────────────────────────────────────────────────────
 * Paint is real DOM/CSS (unchanged from the Aug-28 pivot). What changed
 * with the GSAP rewrite: any primitive with its OWN internal content
 * animation (TextNode's 8 modes, ShapeArrowNode's draw-on) now builds real
 * GSAP tweens ONCE via registerContentAnimation(masterTl), instead of
 * computing a value every frame in an updateDOM(time) override — see
 * runtime.js's Node.registerContentAnimation for the hook shape, and
 * proxyTween()/window.proxyTween for the shared non-GSAP-native-property
 * helper (blur, clip-path, background-position-x, stroke-dashoffset).
 *
 * ShapeCircleNode and ShapeArrowNode previously carried unused Canvas2D
 * draw(ctx,time) methods, kept only as reference for this exact port —
 * both are real DOM/SVG now, and draw() is gone.
 */

// ── ShapeRectNode — unchanged, paint-only (no animation of its own) ───────────
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

// ── ShapeCircleNode — real DOM now (was dead Canvas2D; trivial to port) ──────
class ShapeCircleNode extends Node {
  constructor(props = {}) {
    super(props);
    this.radius      = props.radius      || 50;
    this.fill         = props.fill         || "#FFFFFF";
    this.stroke       = props.stroke       || null;
    this.strokeWidth  = props.stroke_width || 0;
    this.glowColor    = props.glow_color   || null;
    this.glowBlur     = props.glow_blur    || 0;
  }

  initDOM(box) {
    const d = this.radius * 2;
    box.style.transform = "";
    box.style.left   = `${-d * this.anchor[0]}px`;
    box.style.top    = `${-d * this.anchor[1]}px`;
    box.style.width  = `${d}px`;
    box.style.height = `${d}px`;
    box.style.borderRadius = "50%";
    if (this.fill && this.fill !== "none") box.style.background = this.fill;
    if (this.stroke && this.strokeWidth > 0) {
      box.style.boxSizing = "border-box";
      box.style.border = `${this.strokeWidth}px solid ${this.stroke}`;
    }
    if (this.glowColor && this.glowBlur > 0) {
      box.style.boxShadow = `0 0 ${this.glowBlur}px ${this.glowColor}`;
    }
  }
}

// ── ShapeArrowNode — real SVG now (was dead Canvas2D) ─────────────────────────
// The bezier control-point/point-sampling math below is the OLD engine's
// own draw() geometry, unchanged — pure math, never canvas-specific — just
// building an SVG path `d` string instead of ctx.lineTo calls.
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
    this.drawStart    = props.draw_start   || 0.0;
    this.drawDuration = props.draw_duration || 0.65;
    this.pulse        = props.pulse        || false;
    this.pulseColor   = props.pulse_color  || "#FFFFFF";
    this.pulseSpeed   = props.pulse_speed  || 0.9;
  }

  initDOM(box) {
    const [x1, y1] = this.from, [x2, y2] = this.to;
    const dx = x2 - x1, dy = y2 - y1;
    const dist = Math.hypot(dx, dy) || 1;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    const nx = -dy / dist, ny = dx / dist;
    const cx = this.curved ? mx + nx * this.curveHeight : mx;
    const cy = this.curved ? my + ny * this.curveHeight : my;
    const tangentEnd = Math.atan2(y2 - cy, x2 - cx) * (180 / Math.PI);

    const minX = Math.min(x1, x2, cx) - this.headSize - this.glowBlur;
    const minY = Math.min(y1, y2, cy) - this.headSize - this.glowBlur;
    const maxX = Math.max(x1, x2, cx) + this.headSize + this.glowBlur;
    const maxY = Math.max(y1, y2, cy) + this.headSize + this.glowBlur;
    const w = maxX - minX, h = maxY - minY;

    box.style.transform = "";
    box.style.left = `${minX}px`; box.style.top = `${minY}px`;
    box.style.width = `${w}px`; box.style.height = `${h}px`;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.style.overflow = "visible";

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const d = this.curved
      ? `M ${x1 - minX} ${y1 - minY} Q ${cx - minX} ${cy - minY} ${x2 - minX} ${y2 - minY}`
      : `M ${x1 - minX} ${y1 - minY} L ${x2 - minX} ${y2 - minY}`;
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", this.color);
    path.setAttribute("stroke-width", String(this.strokeWidth));
    path.setAttribute("stroke-linecap", "round");
    if (this.glowBlur > 0) path.style.filter = `drop-shadow(0 0 ${this.glowBlur}px ${this.color})`;
    svg.appendChild(path);

    const head = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    const hs = this.headSize;
    head.setAttribute("points", `0,0 ${-hs * 1.3},${-hs * 0.6} ${-hs * 0.85},0 ${-hs * 1.3},${hs * 0.6}`);
    head.setAttribute("fill", this.color);
    head.setAttribute("transform", `translate(${x2 - minX},${y2 - minY}) rotate(${tangentEnd})`);
    head.style.opacity = "0"; // fades in with the draw-on, see registerContentAnimation
    svg.appendChild(head);

    box.appendChild(svg);
    this._path = path;
    this._head = head;
    this._pathLength = path.getTotalLength();
    path.style.strokeDasharray = String(this._pathLength);
    path.style.strokeDashoffset = String(this._pathLength);
    this._strokeTarget = path; // lets a spec-authored strokeDashoffset tween (see TWEEN_CHANNELS) target this path directly too
  }

  registerContentAnimation(masterTl) {
    const at = this.drawStart, dur = Math.max(0.05, this.drawDuration);
    proxyTween(masterTl, v => { this._path.style.strokeDashoffset = String(v); },
      this._pathLength, 0, dur, "power2.out", at);
    masterTl.fromTo(this._head, { opacity: 0, scale: 0 }, { opacity: 1, scale: 1, duration: 0.3, ease: "back.out(2)" }, at + dur * 0.6);
    if (this.pulse) {
      // A traveling highlight once the line is fully drawn — a small dot
      // orbiting the path via GSAP's MotionPathPlugin isn't in core
      // gsap.min.js, so this samples the SAME bezier by hand (identical
      // geometry to initDOM's own d= computation) and drives x/y directly.
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("r", String(this.strokeWidth * 1.4));
      dot.setAttribute("fill", this.pulseColor);
      dot.style.filter = `drop-shadow(0 0 10px ${this.pulseColor})`;
      this._path.parentNode.appendChild(dot);
      const proxy = { t: 0 };
      const [x1, y1] = this.from, [x2, y2] = this.to;
      const dx = x2 - x1, dy = y2 - y1;
      const dist = Math.hypot(dx, dy) || 1;
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      const nx = -dy / dist, ny = dx / dist;
      const cx = this.curved ? mx + nx * this.curveHeight : mx;
      const cy = this.curved ? my + ny * this.curveHeight : my;
      const place = (t) => {
        const it = 1 - t;
        const px = it * it * x1 + 2 * it * t * cx + t * t * x2;
        const py = it * it * y1 + 2 * it * t * cy + t * t * y2;
        const boxLeft = parseFloat(this._path.parentNode.parentNode.style.left || "0");
        dot.setAttribute("cx", String(px - boxLeft));
        dot.setAttribute("cy", String(py - parseFloat(this._path.parentNode.parentNode.style.top || "0")));
      };
      masterTl.fromTo(proxy, { t: 0 }, {
        t: 1, duration: 1 / this.pulseSpeed, ease: "none", repeat: -1,
        onUpdate: () => place(proxy.t),
      }, at + dur);
    }
  }
}

// ── TextNode — Full Kinetic Typography Toolkit (GSAP rebuild) ────────────────
class TextNode extends Node {
  constructor(props = {}) {
    super(props);
    this.content        = String(props.content || "");
    this.fontFamily     = props.font_family    || "var(--motion-body-font, Inter, -apple-system, sans-serif)";
    this.fontSize       = props.font_size      || 48;
    this.fontWeight     = props.font_weight    || 700;
    this.fill           = props.fill           || "#FFFFFF";
    this.align          = props.align          || "center";
    this.lineHeight     = props.line_height    || 1.25;
    this.mode           = props.mode           || "standard";
    this.revealStart    = props.reveal_start   || 0.0;
    this.revealDuration = props.reveal_duration || 0.75;
    this.gradient       = props.gradient       || null;
    this.shadow         = props.text_shadow    || null;
    this.letterSpacing  = props.letter_spacing || 0;

    this.staggerDelay   = props.stagger_delay  || 0.075;
    this.shimmerColor   = props.shimmer_color  || "rgba(255,255,255,0.55)";
    this.shimmerWidth   = props.shimmer_width  || 0.35;
    this.splitGap       = props.split_gap      || 40;
    this.blurAmount     = props.blur_amount    || 24;
    this.prefix = props.prefix || "";
    this.suffix = props.suffix || "";
    this.locale = props.locale || undefined; // e.g. "en-IN" for ₹1,00,000-style grouping, matching the Meridian reference's own toLocaleString("en-IN")
  }

  _applyBaseTextStyle(el) {
    el.style.fontFamily = this.fontFamily;
    el.style.fontSize = `${this.fontSize}px`;
    el.style.fontWeight = String(this.fontWeight);
    el.style.lineHeight = String(this.lineHeight);
    el.style.whiteSpace = "pre";
    if (this.letterSpacing) el.style.letterSpacing = `${this.letterSpacing}px`;
    if (this.gradient && this.gradient.stops) {
      const stops = this.gradient.stops.slice().sort((a, b) => a.pos - b.pos)
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

  // Builds this line's STATIC per-mode DOM once — unchanged in spirit from
  // the previous engine; only what drives the numbers changed.
  _initLineMode(el, line) {
    switch (this.mode) {
      case "word_stagger": {
        const words = line.split(" ");
        const spans = words.map((w, i) => {
          const s = document.createElement("span");
          s.style.display = "inline-block";
          s.style.whiteSpace = "pre";
          s.style.opacity = "0";
          s.textContent = w + (i < words.length - 1 ? " " : "");
          el.appendChild(s);
          return s;
        });
        return { spans };
      }
      case "char_cascade": {
        const spans = line.split("").map(c => {
          const s = document.createElement("span");
          s.style.display = "inline-block";
          s.style.whiteSpace = "pre";
          s.style.opacity = "0";
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
          s.style.clipPath = side === "left" ? "inset(0 50% 0 0)" : "inset(0 0 0 50%)";
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
        base.style.opacity = "0";
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
        shimmer.style.backgroundImage =
          `linear-gradient(90deg, rgba(255,255,255,0) 0%, ${this.shimmerColor} 40%, ` +
          `${this.shimmerColor} 60%, rgba(255,255,255,0) 100%)`;
        shimmer.style.backgroundSize = "250% 100%";
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
        el.appendChild(textSpan); el.appendChild(cursor);
        return { textSpan, cursor };
      }
      case "masked_reveal":
        el.textContent = line;
        el.style.clipPath = "inset(100% 0 0 0)";
        return {};
      case "blur_pop":
        el.textContent = line;
        el.style.opacity = "0";
        return {};
      case "counter_tick":
        el.textContent = this.prefix + "0" + this.suffix;
        return {};
      case "standard":
      default:
        el.textContent = line;
        return {};
    }
  }

  registerContentAnimation(masterTl) {
    for (let i = 0; i < this._lineEls.length; i++) {
      this._registerLineMode(this._lineEls[i], this._lines[i], this._lineState[i], i, masterTl);
    }
  }

  _registerLineMode(el, line, state, lineIdx, masterTl) {
    switch (this.mode) {
      case "word_stagger": {
        const wDur = 0.48;
        state.spans.forEach((s, i) => {
          const at = this.revealStart + i * this.staggerDelay;
          masterTl.fromTo(s, { opacity: 0, y: 30, scale: 0.78 },
            { opacity: 1, y: 0, scale: 1, duration: wDur, ease: "back.out(1.7)" }, at);
        });
        return;
      }
      case "char_cascade": {
        const cDur = 0.38;
        state.spans.forEach((s, i) => {
          const at = this.revealStart + i * (this.staggerDelay * 0.55);
          masterTl.fromTo(s, { opacity: 0, y: -80 },
            { opacity: 1, y: 0, duration: cDur, ease: "bounce.out" }, at);
        });
        return;
      }
      case "split_slide": {
        const off = this.splitGap + 200;
        masterTl.fromTo(state.left, { x: -off }, { x: 0, duration: this.revealDuration, ease: "expo.out" }, this.revealStart);
        masterTl.fromTo(state.right, { x: off }, { x: 0, duration: this.revealDuration, ease: "expo.out" }, this.revealStart);
        return;
      }
      case "shimmer_sweep": {
        const fadeDur = this.revealDuration * 0.5;
        masterTl.fromTo(state.base, { opacity: 0 }, { opacity: 1, duration: fadeDur, ease: "power2.out" }, this.revealStart);
        const sweepAt = this.revealStart + fadeDur;
        masterTl.set(state.shimmer, { opacity: 1 }, sweepAt);
        proxyTween(masterTl, v => { state.shimmer.style.backgroundPositionX = `${v}%`; },
          100, -100, this.revealDuration * 0.6, "power2.inOut", sweepAt);
        return;
      }
      case "typewriter": {
        const rate = Math.max(1, line.length) / Math.max(0.1, this.revealDuration);
        proxyTween(masterTl, v => { state.textSpan.textContent = line.substring(0, Math.floor(v)); },
          0, line.length, Math.max(0.1, this.revealDuration), "none", this.revealStart);
        // Cursor blink is its own repeat:-1 tween so a seek anywhere still
        // shows the correct phase — never a live/wall-clock blink.
        masterTl.fromTo(state.cursor, { opacity: 1 }, { opacity: 0, duration: 0.5, ease: "steps(1)", repeat: -1, yoyo: true }, this.revealStart);
        return;
      }
      case "masked_reveal":
        proxyTween(masterTl, v => { el.style.clipPath = `inset(${Math.max(0, 100 - v)}% 0 0 0)`; },
          0, 100, this.revealDuration, "power2.out", this.revealStart);
        return;
      case "blur_pop":
        masterTl.fromTo(el, { opacity: 0, scale: 0.7 }, { opacity: 1, scale: 1, duration: this.revealDuration, ease: "back.out(1.7)" }, this.revealStart);
        proxyTween(masterTl, v => { el.style.filter = `blur(${Math.max(0, v)}px)`; },
          this.blurAmount, 0, this.revealDuration, "back.out(1.7)", this.revealStart);
        return;
      case "counter_tick": {
        const target = parseFloat(line) || 0;
        const decimals = line.includes(".") ? line.split(".")[1].length : 0;
        proxyTween(masterTl, v => {
          const num = decimals > 0 ? v.toFixed(decimals) : Math.floor(v).toLocaleString(this.locale);
          el.textContent = this.prefix + num + this.suffix;
        }, 0, target, this.revealDuration, "power2.out", this.revealStart);
        return;
      }
      case "standard":
      default:
        return; // static — set once in _initLineMode
    }
  }
}

// ── ImageNode — unchanged, paint/load-state only ──────────────────────────────
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
    box.style.background = "rgba(15,23,42,0.75)";
    box.style.backdropFilter = "blur(20px)";
    box.style.webkitBackdropFilter = "blur(20px)";

    const img = document.createElement("img");
    img.style.width = "100%";
    img.style.height = "100%";
    img.style.objectFit = "fill";
    img.style.display = "block";
    img.addEventListener("load", () => {
      box.style.background = "";
      box.style.backdropFilter = "";
      box.style.webkitBackdropFilter = "";
    });
    img.addEventListener("error", () => { img.style.display = "none"; });
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
