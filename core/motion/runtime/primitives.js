/**
 * Prism Motion Graphics Engine — Kinetic Typography & Vector Primitives
 * ─────────────────────────────────────────────────────────────────────
 * Full modern kinetic typography toolkit: the AI freely picks any mode per text node.
 * Also contains high-fidelity vector shapes, Bézier laser arrows with stroke-trimming,
 * and an image node with built-in glassmorphism placeholder fallback.
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

  draw(ctx, time) {
    const w = this.width;
    const h = this.height;
    const x = -w * this.anchor[0];
    const y = -h * this.anchor[1];
    const r = Math.min(this.radius, w / 2, h / 2);

    if (this.isGlass && window.MotionEffects) {
      window.MotionEffects.drawGlassCard(ctx, 0, 0, w, h, r, {
        fill: this.fill, anchorX: this.anchor[0], anchorY: this.anchor[1]
      });
      return;
    }

    ctx.beginPath();
    r > 0 ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);

    if (this.shadowColor && this.shadowBlur > 0) {
      ctx.shadowColor = this.shadowColor;
      ctx.shadowBlur  = this.shadowBlur;
      ctx.shadowOffsetY = this.shadowOffsetY;
    }
    if (this.glowColor && this.glowBlur > 0) {
      ctx.shadowColor = this.glowColor;
      ctx.shadowBlur  = this.glowBlur;
    }

    if (this.fill && this.fill !== "none") {
      ctx.fillStyle = this.fill;
      ctx.fill();
    }
    ctx.shadowColor = "transparent";

    if (this.stroke && this.strokeWidth > 0) {
      ctx.lineWidth   = this.strokeWidth;
      ctx.strokeStyle = this.stroke;
      ctx.stroke();
    }
  }
}

// ── ShapeCircleNode ───────────────────────────────────────────────────────────
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
    this.pulse        = props.pulse        || false;  // traveling data pulse
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

    // Helper: evaluate Bézier at t
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

    // Arrow head
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

    // Traveling data pulse along the path
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

    // Mode-specific config
    this.staggerDelay   = props.stagger_delay  || 0.075; // word_stagger / char_cascade
    this.shimmerColor   = props.shimmer_color  || "rgba(255,255,255,0.55)";
    this.shimmerWidth   = props.shimmer_width  || 0.35;  // fraction of text width
    this.splitGap       = props.split_gap      || 40;    // split_slide gap in px
    this.blurAmount     = props.blur_amount    || 24;    // blur_pop initial blur
  }

  _setFont(ctx) {
    ctx.font         = `${this.fontWeight} ${this.fontSize}px ${this.fontFamily}`;
    ctx.textAlign    = this.align;
    ctx.textBaseline = "middle";
  }

  _setFill(ctx, textWidth = 0) {
    if (this.gradient && this.gradient.stops) {
      const gx1 = this.align === "center" ? -textWidth / 2 : 0;
      const g = ctx.createLinearGradient(gx1, -this.fontSize / 2, gx1 + textWidth, this.fontSize / 2);
      for (const s of this.gradient.stops) {
        g.addColorStop(s.pos, s.color);
      }
      ctx.fillStyle = g;
    } else {
      ctx.fillStyle = this.fill;
    }
  }

  _applyTextShadow(ctx) {
    if (this.shadow) {
      ctx.shadowColor   = this.shadow.color   || "rgba(0,0,0,0.5)";
      ctx.shadowBlur    = this.shadow.blur    || 12;
      ctx.shadowOffsetY = this.shadow.offsetY || 4;
    }
  }

  draw(ctx, time) {
    if (!this.content) return;
    const lines = String(this.content).split(/\r?\n/);
    if (lines.length === 1) {
      this._drawSingleLine(ctx, time);
      return;
    }
    // Multi-line content, stacked vertically by lineHeight. Every mode
    // below gets this for free — canvas ctx.fillText() has never respected
    // \n on its own, so a multi-line headline (which the AI writes
    // constantly — see core/motion/generate.py's storyboard prompt
    // examples) silently rendered as one unbroken line, usually wider than
    // the frame. Measured on a real generated reel: a 2-line, font-size-86
    // headline rendered as one ~1400px line on a 1080px canvas, clipped on
    // both edges.
    //
    // Simplification, not a bug: every line shares the same reveal clock —
    // this is NOT a true per-line cascade (line 2 doesn't wait for line 1
    // to finish). Good enough to fix the actual reported symptom
    // (invisible/clipped text); a real staggered multi-line reveal is a
    // separate enhancement, not required to stop text running off-frame.
    const original = this.content;
    const lineHeightPx = this.fontSize * this.lineHeight;
    const totalH = lineHeightPx * (lines.length - 1);
    try {
      for (let i = 0; i < lines.length; i++) {
        this.content = lines[i];
        const y = -totalH / 2 + i * lineHeightPx;
        ctx.save();
        ctx.translate(0, y);
        this._drawSingleLine(ctx, time);
        ctx.restore();
      }
    } finally {
      // Always restored, even on a thrown error — this.content is shared,
      // mutable state re-read every frame; leaving it stuck on one line
      // would corrupt every subsequent draw() call for this node.
      this.content = original;
    }
  }

  _drawSingleLine(ctx, time) {
    this._setFont(ctx);
    const fullWidth = ctx.measureText(this.content).width;

    switch (this.mode) {

      // ── Standard: just draw it
      case "standard":
      default: {
        this._setFill(ctx, fullWidth);
        this._applyTextShadow(ctx);
        ctx.fillText(this.content, 0, 0);
        ctx.shadowColor = "transparent";
        break;
      }

      // ── Typewriter: characters appear left-to-right with blinking cursor
      case "typewriter": {
        const elapsed = Math.max(0, time - this.revealStart);
        const rate    = this.content.length / Math.max(0.1, this.revealDuration);
        const visible = Math.min(this.content.length, Math.floor(elapsed * rate));
        const str     = this.content.substring(0, visible);
        this._setFill(ctx, fullWidth);
        this._applyTextShadow(ctx);
        ctx.fillText(str, 0, 0);
        ctx.shadowColor = "transparent";
        if (visible < this.content.length || (time * 2) % 1.0 < 0.5) {
          const m   = ctx.measureText(str);
          const curX = this.align === "center" ? m.width / 2 + 6 : m.width + 4;
          ctx.fillRect(curX, -this.fontSize * 0.42, 3, this.fontSize * 0.85);
        }
        break;
      }

      // ── Word stagger: words pop in one-by-one with spring back.out overshoot
      case "word_stagger": {
        const words  = this.content.split(" ");
        const wDur   = 0.48;
        const metrics = words.map(w => ctx.measureText(w + " ").width);
        const totalW  = metrics.reduce((s, m) => s + m, 0);
        let curX = this.align === "center" ? -totalW / 2 : 0;
        ctx.save();
        for (let i = 0; i < words.length; i++) {
          const wStart = this.revealStart + i * this.staggerDelay;
          const p = Math.max(0, Math.min(1, (time - wStart) / wDur));
          if (p > 0.001) {
            const ep = EASINGS["back.out"](p);
            ctx.save();
            ctx.globalAlpha *= p;
            ctx.translate(curX + metrics[i] / 2, (1 - ep) * 30);
            ctx.scale(0.78 + 0.22 * ep, 0.78 + 0.22 * ep);
            ctx.textAlign = "center";
            this._setFill(ctx, fullWidth);
            this._applyTextShadow(ctx);
            ctx.fillText(words[i], 0, 0);
            ctx.shadowColor = "transparent";
            ctx.restore();
          }
          curX += metrics[i];
        }
        ctx.restore();
        break;
      }

      // ── Masked reveal: text rises from behind a horizontal clip mask — editorial
      case "masked_reveal": {
        const p  = Math.max(0, Math.min(1,
          EASINGS.easeOutCubic((time - this.revealStart) / Math.max(0.1, this.revealDuration))
        ));
        const maskH = this.fontSize * 1.3;
        const clipY = maskH * (1 - p);
        ctx.save();
        ctx.beginPath();
        const rx = this.align === "center" ? -fullWidth / 2 - 10 : -10;
        ctx.rect(rx, -maskH / 2, fullWidth + 20, maskH);
        ctx.clip();
        ctx.translate(0, clipY);
        this._setFill(ctx, fullWidth);
        this._applyTextShadow(ctx);
        ctx.fillText(this.content, 0, 0);
        ctx.shadowColor = "transparent";
        ctx.restore();
        break;
      }

      // ── Shimmer sweep: text fades in, then a light beam sweeps across it
      case "shimmer_sweep": {
        // Phase 1: fade in
        const fadeEnd = this.revealStart + this.revealDuration * 0.5;
        const p = Math.max(0, Math.min(1, (time - this.revealStart) / (this.revealDuration * 0.5)));
        ctx.save();
        ctx.globalAlpha *= EASINGS.easeOutCubic(p);
        this._setFill(ctx, fullWidth);
        this._applyTextShadow(ctx);
        ctx.fillText(this.content, 0, 0);
        ctx.shadowColor = "transparent";

        // Phase 2: shimmer sweep over the text
        if (time > fadeEnd) {
          const sweepT = Math.min(1, (time - fadeEnd) / (this.revealDuration * 0.6));
          const sw     = fullWidth * (1 + this.shimmerWidth);
          const sx     = (this.align === "center" ? -fullWidth / 2 : 0) - fullWidth * this.shimmerWidth + sw * sweepT;
          const shimGrad = ctx.createLinearGradient(sx, 0, sx + fullWidth * this.shimmerWidth, 0);
          shimGrad.addColorStop(0,    "rgba(255,255,255,0)");
          shimGrad.addColorStop(0.4,  this.shimmerColor);
          shimGrad.addColorStop(0.6,  this.shimmerColor);
          shimGrad.addColorStop(1,    "rgba(255,255,255,0)");
          ctx.globalCompositeOperation = "source-atop";
          ctx.globalAlpha = 1.0;
          const tw = fullWidth, gx = this.align === "center" ? -tw / 2 : 0;
          ctx.fillStyle = shimGrad;
          ctx.fillRect(gx, -this.fontSize * 0.6, tw, this.fontSize * 1.2);
        }
        ctx.restore();
        break;
      }

      // ── Char cascade: characters fall in with staggered gravity bounce
      case "char_cascade": {
        const chars = this.content.split("");
        const charMetrics = chars.map(c => ctx.measureText(c).width);
        const totalW = charMetrics.reduce((s, m) => s + m, 0);
        let cx = this.align === "center" ? -totalW / 2 : 0;
        const cDur = 0.38;
        ctx.save();
        for (let i = 0; i < chars.length; i++) {
          const cStart = this.revealStart + i * (this.staggerDelay * 0.55);
          const p = Math.max(0, Math.min(1, (time - cStart) / cDur));
          if (p > 0.001) {
            const ep = EASINGS["bounce.out"](p);
            ctx.save();
            ctx.globalAlpha *= p;
            ctx.translate(cx + charMetrics[i] / 2, -(1 - ep) * 80);
            ctx.textAlign = "center";
            this._setFill(ctx, fullWidth);
            ctx.fillText(chars[i], 0, 0);
            ctx.restore();
          }
          cx += charMetrics[i];
        }
        ctx.restore();
        break;
      }

      // ── Split slide: text splits into two halves that slide in from opposite sides
      case "split_slide": {
        const p  = EASINGS.easeOutExpo(Math.max(0, Math.min(1,
          (time - this.revealStart) / Math.max(0.1, this.revealDuration)
        )));
        const gx = this.align === "center" ? -fullWidth / 2 : 0;

        ctx.save();
        // Left half
        ctx.save();
        ctx.beginPath();
        ctx.rect(gx - 2, -this.fontSize, fullWidth / 2 + 2, this.fontSize * 2);
        ctx.clip();
        ctx.translate(-(1 - p) * (this.splitGap + fullWidth / 2), 0);
        this._setFill(ctx, fullWidth);
        this._applyTextShadow(ctx);
        ctx.fillText(this.content, 0, 0);
        ctx.shadowColor = "transparent";
        ctx.restore();
        // Right half
        ctx.save();
        ctx.beginPath();
        ctx.rect(gx + fullWidth / 2, -this.fontSize, fullWidth / 2 + 2, this.fontSize * 2);
        ctx.clip();
        ctx.translate((1 - p) * (this.splitGap + fullWidth / 2), 0);
        this._setFill(ctx, fullWidth);
        this._applyTextShadow(ctx);
        ctx.fillText(this.content, 0, 0);
        ctx.shadowColor = "transparent";
        ctx.restore();
        ctx.restore();
        break;
      }

      // ── Blur pop: fades in from heavy Gaussian blur to sharp with scale pop
      case "blur_pop": {
        const p  = EASINGS["back.out"](Math.max(0, Math.min(1,
          (time - this.revealStart) / Math.max(0.1, this.revealDuration)
        )));
        // Canvas 2D doesn't have native per-draw blur, so we simulate with shadow blur + scale
        const scl = 0.7 + 0.3 * p;
        ctx.save();
        ctx.globalAlpha *= p;
        ctx.scale(scl, scl);
        ctx.shadowColor = this.fill;
        ctx.shadowBlur  = this.blurAmount * (1 - p);
        this._setFill(ctx, fullWidth / scl);
        ctx.fillText(this.content, 0, 0);
        ctx.restore();
        break;
      }

      // ── Counter tick: kinetic number ticks up from 0 to target
      case "counter_tick": {
        const target  = parseFloat(this.content) || 0;
        const elapsed = Math.max(0, time - this.revealStart);
        const p       = Math.min(1, EASINGS.easeOutCubic(elapsed / Math.max(0.1, this.revealDuration)));
        const current = target * p;
        const decimals = String(this.content).includes(".") ? String(this.content).split(".")[1].length : 0;
        const prefix  = this.prefix  || "";
        const suffix  = this.suffix  || "";
        const display = prefix + (decimals > 0 ? current.toFixed(decimals) : Math.floor(current).toLocaleString()) + suffix;
        this._setFill(ctx, ctx.measureText(display).width);
        this._applyTextShadow(ctx);
        ctx.fillText(display, 0, 0);
        ctx.shadowColor = "transparent";
        break;
      }
    }
  }
}

// ── ImageNode — with glassmorphism placeholder fallback ───────────────────────
class ImageNode extends Node {
  constructor(props = {}) {
    super(props);
    this.src    = props.src    || null;
    this.width  = props.width  || 200;
    this.height = props.height || 200;
    this.radius = props.radius || 12;
    this._img   = null;
    this._state = "idle"; // idle | loading | loaded | error

    if (this.src) this._load();
  }

  _load() {
    this._state = "loading";
    const img = new Image();
    img.onload  = () => { this._img = img; this._state = "loaded"; };
    img.onerror = () => { this._state = "error"; };
    img.src = this.src;
  }

  draw(ctx, time) {
    const w = this.width, h = this.height;
    const x = -w * this.anchor[0], y = -h * this.anchor[1];
    const r = Math.min(this.radius, w / 2, h / 2);

    if (this._state === "loaded" && this._img) {
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, r);
      ctx.clip();
      ctx.drawImage(this._img, x, y, w, h);
      ctx.restore();
    } else {
      // Glassmorphism placeholder fallback
      if (window.MotionEffects) {
        window.MotionEffects.drawGlassCard(ctx, 0, 0, w, h, r, {
          fill: "rgba(15,23,42,0.75)",
          anchorX: this.anchor[0],
          anchorY: this.anchor[1]
        });
      }
      // Animated loading shimmer
      const shimX = x + (((time * 0.6) % 1.0) * (w * 2)) - w * 0.5;
      const sg = ctx.createLinearGradient(shimX, 0, shimX + w * 0.5, 0);
      sg.addColorStop(0,   "rgba(255,255,255,0)");
      sg.addColorStop(0.5, "rgba(255,255,255,0.06)");
      sg.addColorStop(1,   "rgba(255,255,255,0)");
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, r);
      ctx.clip();
      ctx.fillStyle = sg;
      ctx.fillRect(x, y, w, h);
      ctx.restore();
    }
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
