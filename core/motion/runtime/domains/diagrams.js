/**
 * Prism Motion Graphics Engine — System Diagram & Pipeline Domain Component
 * ──────────────────────────────────────────────────────────────────────────
 * Renders node-and-edge architecture diagrams. AI freely specifies:
 * node shapes (pill, circle, hexagon, square, diamond), per-node colors,
 * per-edge pulse colors and speeds, and animated reveal timing.
 */

class DomainDiagramNode extends Node {
  constructor(props = {}) {
    super(props);
    this.diagramNodes = props.nodes       || [];
    this.edges        = props.edges       || [];
    this.revealStart  = props.reveal_start  || 0.0;
    this.revealDuration = props.reveal_duration || 1.0;
    this.nodePadX     = props.node_pad_x  || 180;
    this.nodePadY     = props.node_pad_y  || 120;
    this.defaultNodeW = props.node_width  || 160;
    this.defaultNodeH = props.node_height || 60;
  }

  _nodePos(n) {
    // AI can provide position: [x, y] directly, or layout by column/row
    if (Array.isArray(n.position)) return { x: n.position[0], y: n.position[1] };
    const col = n.col !== undefined ? n.col : 0;
    const row = n.row !== undefined ? n.row : 0;
    return { x: col * this.nodePadX, y: row * this.nodePadY };
  }

  draw(ctx, time) {
    const elapsed  = Math.max(0, time - this.revealStart);
    const revealP  = Math.min(1, EASINGS.easeOutCubic(elapsed / Math.max(0.01, this.revealDuration)));

    const nodeMap = {};
    for (const n of this.diagramNodes) {
      nodeMap[n.id] = { ...n, ...(this._nodePos(n)) };
    }

    ctx.save();

    // ── 1. Edges ──────────────────────────────────────────────────────────────
    for (const e of this.edges) {
      const from = nodeMap[e.from];
      const to   = nodeMap[e.to];
      if (!from || !to) continue;

      const x1 = from.x, y1 = from.y;
      const x2 = to.x,   y2 = to.y;

      const edgeRevealIdx = this.edges.indexOf(e);
      const edgeRevealStart = this.revealStart + edgeRevealIdx * 0.12;
      const ep = Math.max(0, Math.min(1, EASINGS.easeOutCubic(
        (time - edgeRevealStart) / Math.max(0.01, this.revealDuration * 0.6)
      )));
      if (ep <= 0.001) continue;

      // Optional curved Bézier edge
      const curved = e.curved !== false;
      const mid = { x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
      const nx = -(y2 - y1) / Math.max(1, Math.hypot(x2 - x1, y2 - y1));
      const ny =  (x2 - x1) / Math.max(1, Math.hypot(x2 - x1, y2 - y1));
      const curve = e.curve_height || 0;
      const cpx = mid.x + nx * curve;
      const cpy = mid.y + ny * curve;

      const edgeColor = e.color || "rgba(255,255,255,0.12)";
      const edgeW     = e.stroke_width || 2.5;

      // Clipping for animated draw-on
      const drawX = x1 + (x2 - x1) * ep;
      const drawY = y1 + (y2 - y1) * ep;
      ctx.save();
      ctx.beginPath();
      ctx.rect(
        Math.min(x1, x2) - 20, Math.min(y1, y2) - 20,
        Math.abs(x2 - x1) * ep + 40, Math.abs(y2 - y1) * ep + 40
      );
      ctx.clip();

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      if (curved && curve !== 0) {
        ctx.quadraticCurveTo(cpx, cpy, x2, y2);
      } else {
        ctx.lineTo(x2, y2);
      }
      ctx.strokeStyle = edgeColor;
      ctx.lineWidth   = edgeW;
      ctx.lineCap     = "round";
      ctx.stroke();
      ctx.restore();

      // Traveling data pulse
      if ((e.pulse || e.animated) && ep >= 1.0) {
        const speed   = e.pulse_speed || 0.8;
        const offset  = e.pulse_offset || 0;
        const t       = ((time * speed + offset) % 1.0);
        const pulseColor = e.pulse_color || "#38BDF8";
        let px, py;
        if (curved && curve !== 0) {
          const it = 1 - t;
          px = it * it * x1 + 2 * it * t * cpx + t * t * x2;
          py = it * it * y1 + 2 * it * t * cpy + t * t * y2;
        } else {
          px = x1 + (x2 - x1) * t;
          py = y1 + (y2 - y1) * t;
        }
        ctx.save();
        ctx.globalCompositeOperation = "screen";
        ctx.beginPath();
        ctx.arc(px, py, 7, 0, Math.PI * 2);
        ctx.fillStyle   = pulseColor;
        ctx.shadowColor = pulseColor;
        ctx.shadowBlur  = 20;
        ctx.fill();
        // Trailing glow
        ctx.globalAlpha *= 0.4;
        const t2 = ((t - 0.06 + 1) % 1);
        let tx2, ty2;
        if (curved && curve !== 0) {
          const it2 = 1 - t2;
          tx2 = it2 * it2 * x1 + 2 * it2 * t2 * cpx + t2 * t2 * x2;
          ty2 = it2 * it2 * y1 + 2 * it2 * t2 * cpy + t2 * t2 * y2;
        } else {
          tx2 = x1 + (x2 - x1) * t2;
          ty2 = y1 + (y2 - y1) * t2;
        }
        ctx.beginPath();
        ctx.arc(tx2, ty2, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      // Arrowhead at destination
      if (e.arrow !== false) {
        const angle = Math.atan2(y2 - (curved ? cpy : y1), x2 - (curved ? cpx : x1));
        const hs = e.arrow_size || 12;
        ctx.save();
        ctx.translate(x2, y2);
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(-hs * 1.4, -hs * 0.55);
        ctx.lineTo(-hs * 0.9, 0);
        ctx.lineTo(-hs * 1.4,  hs * 0.55);
        ctx.closePath();
        ctx.fillStyle = edgeColor;
        ctx.globalAlpha *= ep;
        ctx.fill();
        ctx.restore();
      }
    }

    // ── 2. Nodes ──────────────────────────────────────────────────────────────
    for (let ni = 0; ni < this.diagramNodes.length; ni++) {
      const n   = this.diagramNodes[ni];
      const pos = nodeMap[n.id];
      if (!pos) continue;

      const nodeRevealStart = this.revealStart + ni * 0.10;
      const np = Math.max(0, Math.min(1, EASINGS["back.out"](
        (time - nodeRevealStart) / Math.max(0.01, this.revealDuration * 0.55)
      )));
      if (np <= 0.001) continue;

      const nw     = n.width  || this.defaultNodeW;
      const nh     = n.height || this.defaultNodeH;
      const color  = n.color  || "#1E293B";
      const stroke = n.stroke || (n.color ? _lighten(n.color, 0.3) : "#38BDF8");
      const shape  = n.shape  || "pill";

      ctx.save();
      ctx.translate(pos.x, pos.y);
      ctx.globalAlpha *= np;
      ctx.scale(np, np);

      // Node elevation shadow
      ctx.shadowColor   = "rgba(0,0,0,0.5)";
      ctx.shadowBlur    = 20;
      ctx.shadowOffsetY = 8;

      // Draw shape
      ctx.beginPath();
      switch (shape) {
        case "circle":
          ctx.arc(0, 0, Math.min(nw, nh) / 2, 0, Math.PI * 2);
          break;
        case "hexagon": {
          const hr = Math.min(nw, nh) / 2;
          for (let i = 0; i < 6; i++) {
            const a = (Math.PI / 3) * i - Math.PI / 6;
            i === 0 ? ctx.moveTo(Math.cos(a) * hr, Math.sin(a) * hr)
                    : ctx.lineTo(Math.cos(a) * hr, Math.sin(a) * hr);
          }
          ctx.closePath();
          break;
        }
        case "diamond": {
          const dx = nw / 2, dy = nh / 2;
          ctx.moveTo(0, -dy);
          ctx.lineTo(dx, 0);
          ctx.lineTo(0, dy);
          ctx.lineTo(-dx, 0);
          ctx.closePath();
          break;
        }
        case "square":
          ctx.rect(-nw / 2, -nh / 2, nw, nh);
          break;
        case "pill":
        default:
          ctx.roundRect(-nw / 2, -nh / 2, nw, nh, Math.min(nw, nh) / 2);
          break;
      }

      // Glass fill
      const fillGrad = ctx.createLinearGradient(0, -nh / 2, 0, nh / 2);
      fillGrad.addColorStop(0, color + "EE");
      fillGrad.addColorStop(1, color + "AA");
      ctx.fillStyle = fillGrad;
      ctx.fill();
      ctx.shadowColor = "transparent";

      // Specular border with glow
      ctx.strokeStyle = stroke;
      ctx.lineWidth   = 2;
      ctx.shadowColor = stroke;
      ctx.shadowBlur  = 14;
      ctx.stroke();
      ctx.shadowColor = "transparent";

      // Icon (optional emoji alternative: a simple colored dot indicator)
      const statusColor = n.status_color || null;
      if (statusColor) {
        ctx.beginPath();
        ctx.arc(-nw / 2 + 20, 0, 5, 0, Math.PI * 2);
        ctx.fillStyle   = statusColor;
        ctx.shadowColor = statusColor;
        ctx.shadowBlur  = 10;
        ctx.fill();
        ctx.shadowColor = "transparent";
      }

      // Label
      ctx.font         = `${n.font_weight || 700} ${n.font_size || 16}px Inter, sans-serif`;
      ctx.fillStyle    = n.label_color || "#F8FAFC";
      ctx.textAlign    = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(n.label || n.id, 0, 0);

      // Sub-label (optional)
      if (n.sublabel) {
        ctx.font      = "400 12px Inter, sans-serif";
        ctx.fillStyle = "rgba(255,255,255,0.5)";
        ctx.fillText(n.sublabel, 0, (nh / 2) + 16);
      }

      ctx.restore();
    }

    ctx.restore();
  }
}

window.DomainDiagramNode = DomainDiagramNode;
