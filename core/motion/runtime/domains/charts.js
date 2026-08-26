/**
 * Prism Motion Graphics Engine — Charts & Financial Data Domain
 * ─────────────────────────────────────────────────────────────
 * AI-driven chart renderer. Supports: bar, line, ring, area, sparkline, metric.
 * The AI freely specifies chart_type, colors, data points, and animation timing.
 */

class DomainChartNode extends Node {
  constructor(props = {}) {
    super(props);
    this.chartType   = props.chart_type   || "bar";
    this.width       = props.width        || 800;
    this.height      = props.height       || 400;
    this.data        = props.data         || [];
    this.valuePrefix = props.value_prefix || "";
    this.valueSuffix = props.value_suffix || "";
    this.startTime   = props.start_time   || 0.0;
    this.duration    = props.duration     || 1.4;
    this.accentColor = props.accent_color || "#38BDF8";
    this.labelColor  = props.label_color  || "#94A3B8";
    this.showGrid    = props.show_grid    !== false;

    // ring / area specific
    this.trackColor  = props.track_color  || "rgba(255,255,255,0.08)";
    this.strokeWidth = props.stroke_width || 22;
  }

  draw(ctx, time) {
    const elapsed  = Math.max(0, time - this.startTime);
    const raw      = Math.min(1.0, elapsed / Math.max(0.01, this.duration));
    const progress = EASINGS.easeOutCubic(raw);

    switch (this.chartType) {
      case "metric":    this._drawMetric(ctx, progress, time);   break;
      case "bar":       this._drawBar(ctx, progress);            break;
      case "line":      this._drawLine(ctx, raw);                break;
      case "area":      this._drawArea(ctx, raw);                break;
      case "ring":      this._drawRing(ctx, progress);           break;
      case "sparkline": this._drawSparkline(ctx, raw);           break;
      default:          this._drawBar(ctx, progress);
    }
  }

  // ── Metric Counter ──────────────────────────────────────────────────────────
  _drawMetric(ctx, progress, time) {
    const item     = this.data[0] || { label: "Metric", value: 100 };
    const target   = parseFloat(item.value) || 0;
    const current  = target * progress;
    const decimals = String(item.value).includes(".") ? String(item.value).split(".")[1].length : 0;
    const display  = this.valuePrefix + (decimals > 0
      ? current.toFixed(decimals)
      : Math.floor(current).toLocaleString()) + this.valueSuffix;

    const color = item.color || this.accentColor;
    ctx.save();
    ctx.textAlign    = "center";
    ctx.textBaseline = "middle";

    // Primary value
    ctx.font      = "800 88px Inter, -apple-system, sans-serif";
    ctx.fillStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur  = 24;
    ctx.fillText(display, 0, -18);
    ctx.shadowColor = "transparent";

    // Label beneath
    ctx.font      = "500 26px Inter, sans-serif";
    ctx.fillStyle = this.labelColor;
    ctx.fillText(item.label || "", 0, 50);

    // Optional growth badge
    if (item.change !== undefined) {
      const up    = item.change >= 0;
      const badge = (up ? "+" : "") + item.change + "%";
      ctx.font      = "700 20px Inter, sans-serif";
      ctx.fillStyle = up ? "#10B981" : "#EF4444";
      ctx.fillText(badge, 0, 88);
    }

    ctx.restore();
  }

  // ── Animated Gradient Bar Chart ─────────────────────────────────────────────
  _drawBar(ctx, progress) {
    const halfW  = this.width / 2;
    const halfH  = this.height / 2;
    const count  = this.data.length || 1;
    const maxVal = Math.max(...this.data.map(d => d.value || 0), 1);
    const barW   = Math.min(72, (this.width - 60) / count - 18);
    const areaH  = this.height - 90;
    const baseY  = halfH - 44;

    ctx.save();

    // Optional subtle grid lines
    if (this.showGrid) {
      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.lineWidth = 1;
      for (let g = 0.25; g <= 1.0; g += 0.25) {
        const gy = baseY - areaH * g;
        ctx.beginPath();
        ctx.moveTo(-halfW + 8, gy);
        ctx.lineTo(halfW - 8, gy);
        ctx.stroke();
      }
    }

    // Axis
    ctx.strokeStyle = "rgba(255,255,255,0.12)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(-halfW + 8, baseY);
    ctx.lineTo(halfW - 8, baseY);
    ctx.stroke();

    const step = (this.width - 80) / Math.max(1, count - 1);
    for (let i = 0; i < count; i++) {
      const d       = this.data[i];
      const val     = d.value || 0;
      const targetH = (val / maxVal) * areaH;
      const curH    = targetH * Math.min(1.0, progress);
      const color   = d.color || this.accentColor;
      const bx      = -halfW + 40 + i * step - barW / 2;
      const by      = baseY - curH;

      // Gradient fill
      const grad = ctx.createLinearGradient(bx, by + curH, bx, by);
      grad.addColorStop(0, color + "22");
      grad.addColorStop(0.5, color + "99");
      grad.addColorStop(1, color);
      ctx.beginPath();
      ctx.roundRect(bx, by, barW, curH, [6, 6, 0, 0]);
      ctx.fillStyle = grad;
      ctx.fill();

      // Top cap highlight
      ctx.beginPath();
      ctx.roundRect(bx, by, barW, 3, [2, 2, 0, 0]);
      ctx.fillStyle = "rgba(255,255,255,0.75)";
      ctx.fill();

      // Value label on top
      if (curH > 24) {
        ctx.font      = "600 16px Inter, sans-serif";
        ctx.fillStyle = "#F8FAFC";
        ctx.textAlign = "center";
        ctx.fillText(this.valuePrefix + (val >= 1000 ? (val/1000).toFixed(1) + "k" : val) + this.valueSuffix, bx + barW / 2, by - 12);
      }

      // X label
      ctx.font      = "500 16px Inter, sans-serif";
      ctx.fillStyle = this.labelColor;
      ctx.textAlign = "center";
      ctx.fillText(d.label || "", bx + barW / 2, baseY + 20);
    }
    ctx.restore();
  }

  // ── Animated Bézier Line Chart ──────────────────────────────────────────────
  _drawLine(ctx, rawProgress) {
    if (!this.data.length) return;
    const halfW  = this.width / 2;
    const halfH  = this.height / 2;
    const maxVal = Math.max(...this.data.map(d => d.value || 0), 1);
    const areaH  = this.height - 90;
    const baseY  = halfH - 44;
    const step   = (this.width - 80) / Math.max(1, this.data.length - 1);

    const pts = this.data.map((d, i) => ({
      x: -halfW + 40 + i * step,
      y: baseY - (d.value / maxVal) * areaH
    }));

    ctx.save();

    // Optional grid
    if (this.showGrid) {
      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.lineWidth = 1;
      for (let g = 0.25; g <= 1.0; g += 0.25) {
        ctx.beginPath();
        ctx.moveTo(-halfW + 8, baseY - areaH * g);
        ctx.lineTo(halfW - 8, baseY - areaH * g);
        ctx.stroke();
      }
    }

    // Clipping to animate the line drawing
    const drawX = pts[0].x + (pts[pts.length - 1].x - pts[0].x) * rawProgress;
    ctx.save();
    ctx.beginPath();
    ctx.rect(-halfW, -halfH, drawX - (-halfW), this.height + 4);
    ctx.clip();

    // Area fill under the line
    const areaGrad = ctx.createLinearGradient(0, baseY - areaH, 0, baseY);
    const c = this.accentColor;
    areaGrad.addColorStop(0, c + "44");
    areaGrad.addColorStop(1, c + "00");

    ctx.beginPath();
    ctx.moveTo(pts[0].x, baseY);
    ctx.lineTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) {
      const cp1x = (pts[i-1].x + pts[i].x) / 2;
      ctx.bezierCurveTo(cp1x, pts[i-1].y, cp1x, pts[i].y, pts[i].x, pts[i].y);
    }
    ctx.lineTo(pts[pts.length - 1].x, baseY);
    ctx.closePath();
    ctx.fillStyle = areaGrad;
    ctx.fill();

    // Line stroke
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) {
      const cp1x = (pts[i-1].x + pts[i].x) / 2;
      ctx.bezierCurveTo(cp1x, pts[i-1].y, cp1x, pts[i].y, pts[i].x, pts[i].y);
    }
    ctx.strokeStyle = this.accentColor;
    ctx.lineWidth   = 3;
    ctx.shadowColor = this.accentColor;
    ctx.shadowBlur  = 12;
    ctx.lineCap = "round";
    ctx.stroke();

    // Data point dots
    for (const pt of pts) {
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = "#FFFFFF";
      ctx.shadowBlur = 8;
      ctx.fill();
    }

    ctx.restore(); // unclip
    ctx.restore();
  }

  // ── Area Chart (same as line but heavier fill, no explicit line) ────────────
  _drawArea(ctx, rawProgress) {
    this._drawLine(ctx, rawProgress);
  }

  // ── Circular Ring Chart ─────────────────────────────────────────────────────
  _drawRing(ctx, progress) {
    const item     = this.data[0] || { label: "Progress", value: 75 };
    const pct      = Math.min(100, Math.max(0, item.value || 75));
    const animated = pct * progress;
    const color    = item.color || this.accentColor;
    const r        = Math.min(this.width, this.height) / 2 - this.strokeWidth;
    const startA   = -Math.PI / 2;
    const endA     = startA + (animated / 100) * Math.PI * 2;

    ctx.save();

    // Track (background arc)
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.strokeStyle = this.trackColor;
    ctx.lineWidth   = this.strokeWidth;
    ctx.stroke();

    // Progress arc with glow
    ctx.beginPath();
    ctx.arc(0, 0, r, startA, endA);
    ctx.strokeStyle = color;
    ctx.lineWidth   = this.strokeWidth;
    ctx.lineCap     = "round";
    ctx.shadowColor = color;
    ctx.shadowBlur  = 28;
    ctx.stroke();
    ctx.shadowColor = "transparent";

    // Center label
    ctx.textAlign    = "center";
    ctx.textBaseline = "middle";
    ctx.font         = "800 68px Inter, sans-serif";
    ctx.fillStyle    = color;
    ctx.fillText(Math.round(animated) + (this.valueSuffix || "%"), 0, -10);
    ctx.font         = "500 22px Inter, sans-serif";
    ctx.fillStyle    = this.labelColor;
    ctx.fillText(item.label || "", 0, 42);

    ctx.restore();
  }

  // ── Sparkline (compact inline trend) ────────────────────────────────────────
  _drawSparkline(ctx, rawProgress) {
    if (!this.data.length) return;
    const halfW  = this.width / 2;
    const halfH  = this.height / 2;
    const maxVal = Math.max(...this.data.map(d => d.value || 0), 1);
    const step   = this.width / Math.max(1, this.data.length - 1);

    const pts = this.data.map((d, i) => ({
      x: -halfW + i * step,
      y: halfH - (d.value / maxVal) * this.height
    }));

    const drawX = pts[0].x + (pts[pts.length - 1].x - pts[0].x) * rawProgress;
    ctx.save();
    ctx.beginPath();
    ctx.rect(-halfW, -halfH, drawX - (-halfW), this.height + 4);
    ctx.clip();

    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) {
      const cpx = (pts[i-1].x + pts[i].x) / 2;
      ctx.bezierCurveTo(cpx, pts[i-1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    ctx.strokeStyle = this.accentColor;
    ctx.lineWidth   = 2.5;
    ctx.lineCap     = "round";
    ctx.shadowColor = this.accentColor;
    ctx.shadowBlur  = 8;
    ctx.stroke();
    ctx.restore();
  }
}

window.DomainChartNode = DomainChartNode;
