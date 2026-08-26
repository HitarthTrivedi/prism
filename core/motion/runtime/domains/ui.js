/**
 * Prism Motion Graphics Engine — UI & Product Demo Domain Component
 * ──────────────────────────────────────────────────────────────────
 * Renders interactive UI mockups: macOS-style window chrome, animated cursor,
 * stat cards, badges, buttons, input fields, toggle switches, and image placeholders.
 * Broken/missing image assets fall back to a glassmorphism placeholder silently.
 */

class DomainUIMockupNode extends Node {
  constructor(props = {}) {
    super(props);
    this.width          = props.width           || 880;
    this.height         = props.height          || 540;
    this.title          = props.title           || "App Preview";
    this.url            = props.url             || "";
    this.cursorActions  = props.cursor_actions  || [];
    this.elements       = props.elements        || [];
    this.bgColor        = props.bg_color        || null;  // AI overrides window bg
    this.headerColor    = props.header_color    || null;
    this.revealStart    = props.reveal_start    || 0.0;
    this.revealDuration = props.reveal_duration || 0.6;
    this._images        = {};  // cache of loaded Image objects
  }

  _loadImage(src) {
    if (!src) return null;
    if (!this._images[src]) {
      const img = new Image();
      this._images[src] = { img, state: "loading" };
      img.onload  = () => { this._images[src].state = "loaded"; };
      img.onerror = () => { this._images[src].state = "error"; };
      img.src = src;
    }
    return this._images[src];
  }

  draw(ctx, time) {
    const elapsed  = Math.max(0, time - this.revealStart);
    const revealP  = Math.min(1, EASINGS["back.out"](elapsed / Math.max(0.01, this.revealDuration)));
    if (revealP <= 0.001) return;

    const w = this.width, h = this.height;
    const halfW = w / 2, halfH = h / 2;
    const bgColor     = this.bgColor     || "#0F172A";
    const headerColor = this.headerColor || "#1A2540";

    ctx.save();
    ctx.globalAlpha *= revealP;
    ctx.scale(0.85 + 0.15 * revealP, 0.85 + 0.15 * revealP);

    // Window elevation shadow
    ctx.shadowColor   = "rgba(0,0,0,0.55)";
    ctx.shadowBlur    = 48;
    ctx.shadowOffsetY = 24;

    // Window body
    ctx.beginPath();
    ctx.roundRect(-halfW, -halfH, w, h, 18);
    ctx.fillStyle = bgColor;
    ctx.fill();
    ctx.shadowColor = "transparent";

    // 1px specular border
    const bGrad = ctx.createLinearGradient(-halfW, -halfH, -halfW, halfH);
    bGrad.addColorStop(0,   "rgba(255,255,255,0.22)");
    bGrad.addColorStop(0.5, "rgba(255,255,255,0.05)");
    bGrad.addColorStop(1,   "rgba(255,255,255,0.01)");
    ctx.strokeStyle = bGrad;
    ctx.lineWidth   = 1.5;
    ctx.stroke();

    // Header bar
    ctx.beginPath();
    ctx.roundRect(-halfW, -halfH, w, 52, [18, 18, 0, 0]);
    ctx.fillStyle = headerColor;
    ctx.fill();

    // Header bottom divider
    ctx.beginPath();
    ctx.moveTo(-halfW, -halfH + 52);
    ctx.lineTo(halfW,  -halfH + 52);
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.lineWidth   = 1;
    ctx.stroke();

    // macOS traffic light dots
    const dotColors = ["#EF4444", "#F59E0B", "#10B981"];
    for (let i = 0; i < 3; i++) {
      ctx.beginPath();
      ctx.arc(-halfW + 26 + i * 22, -halfH + 26, 6, 0, Math.PI * 2);
      ctx.fillStyle = dotColors[i];
      ctx.fill();
    }

    // Address / title pill
    const pillW = Math.min(w - 240, 340);
    ctx.beginPath();
    ctx.roundRect(-pillW / 2, -halfH + 13, pillW, 26, 7);
    ctx.fillStyle = "rgba(0,0,0,0.28)";
    ctx.fill();
    ctx.font      = "400 12px Inter, -apple-system, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.38)";
    ctx.textAlign    = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(this.url || this.title, 0, -halfH + 26);

    // Clip inner content to window body
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(-halfW, -halfH + 52, w, h - 52, [0, 0, 18, 18]);
    ctx.clip();

    // Render elements
    for (const elem of this.elements) {
      this._drawElement(ctx, elem, -halfW, -halfH + 52, w, h - 52, time);
    }

    ctx.restore(); // unclip

    // Animated cursor
    for (const act of this.cursorActions) {
      const actTime = act.time     || 0.0;
      const actDur  = act.duration || 0.7;
      if (time < actTime) continue;
      const p    = Math.min(1.0, (time - actTime) / Math.max(0.01, actDur));
      const ep   = EASINGS.easeInOutCubic(p);
      const from = act.from || [-halfW + 80, 0];
      const to   = act.to   || [0, 0];
      const cx   = from[0] + (to[0] - from[0]) * ep;
      const cy   = from[1] + (to[1] - from[1]) * ep;
      const clicking = act.click && p > 0.82 && p < 1.0;
      this._drawCursor(ctx, cx, cy, clicking, act.cursor_color || "#FFFFFF");
    }

    ctx.restore();
  }

  _drawElement(ctx, elem, originX, originY, availW, availH, time) {
    // Resolve position — AI can provide absolute [x, y] or relative offset
    const ex = originX + (Array.isArray(elem.position) ? elem.position[0] + availW / 2 : (elem.x || 30));
    const ey = originY + (Array.isArray(elem.position) ? elem.position[1] + availH / 2 : (elem.y || 30));
    const ew = elem.width  || elem.w || 200;
    const eh = elem.height || elem.h || 50;

    ctx.save();

    switch (elem.type) {

      case "badge": {
        const color = elem.color || "#10B981";
        const label = elem.label || "Active";
        const bw    = ctx.measureText(label).width + 28;
        ctx.beginPath();
        ctx.roundRect(ex - bw / 2, ey - 14, bw, 28, 14);
        ctx.fillStyle = color + "22";
        ctx.fill();
        ctx.strokeStyle = color + "88";
        ctx.lineWidth   = 1;
        ctx.stroke();

        // Status dot
        ctx.beginPath();
        ctx.arc(ex - bw / 2 + 14, ey, 4, 0, Math.PI * 2);
        ctx.fillStyle   = color;
        ctx.shadowColor = color;
        ctx.shadowBlur  = 8;
        ctx.fill();
        ctx.shadowColor = "transparent";

        ctx.font         = "600 13px Inter, sans-serif";
        ctx.fillStyle    = color;
        ctx.textAlign    = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(label, ex - bw / 2 + 24, ey);
        break;
      }

      case "stat_card": {
        const color = elem.color || "#38BDF8";
        if (window.MotionEffects) {
          window.MotionEffects.drawGlassCard(ctx, ex, ey, ew, eh, 14, {
            fill: "rgba(15,23,42,0.85)", anchorX: 0.5, anchorY: 0.5
          });
        }
        ctx.font         = "600 13px Inter, sans-serif";
        ctx.fillStyle    = "rgba(255,255,255,0.45)";
        ctx.textAlign    = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(elem.label || "Metric", ex, ey - 10);
        ctx.font      = "800 24px Inter, sans-serif";
        ctx.fillStyle = color;
        ctx.shadowColor = color;
        ctx.shadowBlur  = 14;
        ctx.fillText(elem.value || "—", ex, ey + 12);
        ctx.shadowColor = "transparent";
        break;
      }

      case "button": {
        const color = elem.color || "#38BDF8";
        ctx.beginPath();
        ctx.roundRect(ex - ew / 2, ey - eh / 2, ew, eh, 10);
        const bGrad = ctx.createLinearGradient(ex, ey - eh / 2, ex, ey + eh / 2);
        bGrad.addColorStop(0, color);
        bGrad.addColorStop(1, color + "BB");
        ctx.fillStyle   = bGrad;
        ctx.shadowColor = color;
        ctx.shadowBlur  = 16;
        ctx.fill();
        ctx.shadowColor = "transparent";
        ctx.font         = "700 16px Inter, sans-serif";
        ctx.fillStyle    = "#FFFFFF";
        ctx.textAlign    = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(elem.label || "Action", ex, ey);
        break;
      }

      case "card": {
        if (window.MotionEffects) {
          window.MotionEffects.drawGlassCard(ctx, ex, ey, ew, eh, 16, {
            fill: elem.fill || "rgba(20,30,55,0.82)", anchorX: 0.5, anchorY: 0.5
          });
        }
        if (elem.title) {
          ctx.font         = "700 18px Inter, sans-serif";
          ctx.fillStyle    = "#F8FAFC";
          ctx.textAlign    = "left";
          ctx.textBaseline = "top";
          ctx.fillText(elem.title, ex - ew / 2 + 18, ey - eh / 2 + 18);
        }
        if (elem.subtitle) {
          ctx.font      = "400 13px Inter, sans-serif";
          ctx.fillStyle = "rgba(255,255,255,0.45)";
          ctx.fillText(elem.subtitle, ex - ew / 2 + 18, ey - eh / 2 + 42);
        }
        break;
      }

      case "image": {
        // Load image — fall back to glassmorphism placeholder if broken/missing
        const entry = this._loadImage(elem.src);
        ctx.save();
        ctx.beginPath();
        ctx.roundRect(ex - ew / 2, ey - eh / 2, ew, eh, elem.radius || 12);
        ctx.clip();
        if (entry && entry.state === "loaded") {
          ctx.drawImage(entry.img, ex - ew / 2, ey - eh / 2, ew, eh);
        } else {
          // Animated shimmer placeholder
          if (window.MotionEffects) {
            window.MotionEffects.drawGlassCard(ctx, ex, ey, ew, eh, elem.radius || 12, {
              fill: "rgba(15,23,42,0.75)", anchorX: 0.5, anchorY: 0.5
            });
          }
          const shimX = (ex - ew / 2) + ((time * 0.5) % 1.0) * ew * 1.5 - ew * 0.5;
          const sg = ctx.createLinearGradient(shimX, 0, shimX + ew * 0.5, 0);
          sg.addColorStop(0,   "rgba(255,255,255,0)");
          sg.addColorStop(0.5, "rgba(255,255,255,0.06)");
          sg.addColorStop(1,   "rgba(255,255,255,0)");
          ctx.fillStyle = sg;
          ctx.fillRect(ex - ew / 2, ey - eh / 2, ew, eh);
        }
        ctx.restore();
        break;
      }

      case "input": {
        ctx.beginPath();
        ctx.roundRect(ex - ew / 2, ey - eh / 2, ew, eh, 8);
        ctx.fillStyle   = "rgba(255,255,255,0.05)";
        ctx.strokeStyle = "rgba(255,255,255,0.12)";
        ctx.lineWidth   = 1.5;
        ctx.fill();
        ctx.stroke();
        if (elem.placeholder) {
          ctx.font         = "400 14px Inter, sans-serif";
          ctx.fillStyle    = "rgba(255,255,255,0.28)";
          ctx.textAlign    = "left";
          ctx.textBaseline = "middle";
          ctx.fillText(elem.placeholder, ex - ew / 2 + 14, ey);
        }
        break;
      }

      case "toggle": {
        const on     = elem.on !== false;
        const tColor = on ? (elem.color || "#10B981") : "rgba(255,255,255,0.15)";
        const tw = 48, th = 26;
        ctx.beginPath();
        ctx.roundRect(ex - tw / 2, ey - th / 2, tw, th, th / 2);
        ctx.fillStyle   = tColor;
        ctx.shadowColor = on ? tColor : "transparent";
        ctx.shadowBlur  = on ? 10 : 0;
        ctx.fill();
        ctx.shadowColor = "transparent";
        const knobX = on ? ex + tw / 2 - 14 : ex - tw / 2 + 14;
        ctx.beginPath();
        ctx.arc(knobX, ey, 10, 0, Math.PI * 2);
        ctx.fillStyle = "#FFFFFF";
        ctx.fill();
        break;
      }

      default:
        break;
    }

    ctx.restore();
  }

  _drawCursor(ctx, x, y, isClicking, color = "#FFFFFF") {
    ctx.save();
    ctx.translate(x, y);
    if (isClicking) ctx.scale(0.85, 0.85);

    // Cursor arrow
    ctx.beginPath();
    ctx.moveTo(0,    0);
    ctx.lineTo(0,    24);
    ctx.lineTo(6.5,  18);
    ctx.lineTo(13.5, 29);
    ctx.lineTo(18,   26);
    ctx.lineTo(11,   15);
    ctx.lineTo(19,   15);
    ctx.closePath();
    ctx.fillStyle   = color;
    ctx.shadowColor = "rgba(0,0,0,0.6)";
    ctx.shadowBlur  = 8;
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.5)";
    ctx.lineWidth   = 1.5;
    ctx.stroke();
    ctx.shadowColor = "transparent";

    // Click ripple
    if (isClicking) {
      ctx.beginPath();
      ctx.arc(0, 0, 20, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(56,189,248,0.30)";
      ctx.fill();
      ctx.strokeStyle = "rgba(56,189,248,0.55)";
      ctx.lineWidth   = 1.5;
      ctx.stroke();
    }
    ctx.restore();
  }
}

window.DomainUIMockupNode = DomainUIMockupNode;
