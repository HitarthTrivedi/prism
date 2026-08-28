/**
 * DomainChartNode — real inline SVG (was dead Canvas2D). line/bar/ring/
 * metric, modeled directly on the Meridian HyperFrames reference's own
 * chart.html technique: gridlines + axis labels generated from a plain
 * data array, stroke-dashoffset line-draw, a landing dot with back.out
 * overshoot, and a count-up value label — the same proxyTween pattern
 * every other primitive's numeric animation uses.
 */
class DomainChartNode extends Node {
  constructor(props = {}) {
    super(props);
    this.chartType   = props.chart_type   || "line"; // line | bar | ring | metric
    this.width       = props.width        || 800;
    this.height       = props.height       || 400;
    this.data        = Array.isArray(props.data) ? props.data : [];
    this.accentColor = props.accent_color || "#B9863E";
    this.gridColor   = props.grid_color   || "rgba(244,238,225,0.12)";
    this.title       = props.title        || "";
    // Whether THIS primitive draws its own count-up value label is opt-in
    // (value_suffix given at all, even "") — a spec that puts the value in
    // its own separately-styled text node instead (as the Meridian
    // reference does, and this engine's own test spec mirrors) must not
    // get a second, unwanted value label from the chart itself.
    this._showValue = props.value_suffix !== undefined;
    this.valueSuffix = props.value_suffix || "";
    this.startTime   = props.start_time !== undefined ? props.start_time : 0.0;
    this.duration    = props.duration || 1.6;
  }

  _num(entry) {
    if (typeof entry === "number") return entry;
    if (entry && typeof entry.value === "number") return entry.value;
    return parseFloat(entry && entry.value) || 0;
  }
  _label(entry) {
    return entry && entry.label !== undefined ? String(entry.label) : "";
  }

  initDOM(box) {
    const w = this.width, h = this.height;
    box.style.transform = "";
    box.style.left = `${-w * this.anchor[0]}px`;
    box.style.top = `${-h * this.anchor[1]}px`;
    box.style.width = `${w}px`;
    box.style.height = `${h}px`;

    if (this.title) {
      const titleEl = document.createElement("div");
      titleEl.textContent = this.title;
      titleEl.style.fontFamily = "var(--motion-display-font, Georgia, serif)";
      titleEl.style.fontSize = `${Math.round(h * 0.09)}px`;
      titleEl.style.fontWeight = "500";
      titleEl.style.color = "#F4EEE1";
      titleEl.style.marginBottom = `${Math.round(h * 0.08)}px`;
      box.appendChild(titleEl);
    }

    if (this.chartType === "metric") { this._initMetric(box, w, h); return; }
    if (this.chartType === "ring") { this._initRing(box, w, h); return; }

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const chartH = h - (this.title ? h * 0.2 : 0);
    svg.setAttribute("width", w);
    svg.setAttribute("height", chartH);
    svg.setAttribute("viewBox", `0 0 ${w} ${chartH}`);
    svg.style.overflow = "visible";
    box.appendChild(svg);

    const padL = w * 0.02, padR = w * 0.02, padTop = chartH * 0.08, padBottom = chartH * 0.16;
    const plotW = w - padL - padR, plotH = chartH - padTop - padBottom;
    const values = this.data.map(d => this._num(d));
    const maxVal = Math.max(...values, 1) * 1.1;

    const grid = document.createElementNS("http://www.w3.org/2000/svg", "g");
    for (let i = 1; i <= 3; i++) {
      const y = padTop + (plotH / 4) * i;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", padL); line.setAttribute("y1", y);
      line.setAttribute("x2", padL + plotW); line.setAttribute("y2", y);
      line.setAttribute("stroke", this.gridColor);
      line.setAttribute("stroke-width", "1");
      grid.appendChild(line);
    }
    svg.appendChild(grid);

    const axis = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const n = Math.max(1, this.data.length - 1);
    const getX = (i) => padL + (n === 0 ? plotW / 2 : (i / n) * plotW);
    const getY = (v) => padTop + plotH - (v / maxVal) * plotH;
    this.data.forEach((d, i) => {
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", getX(i));
      label.setAttribute("y", chartH - padBottom * 0.35);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("fill", "#F4EEE1");
      label.setAttribute("fill-opacity", "0.55");
      label.setAttribute("font-size", String(Math.max(12, w * 0.018)));
      label.textContent = this._label(d);
      axis.appendChild(label);
    });
    svg.appendChild(axis);

    if (this.chartType === "bar") {
      const barW = (plotW / this.data.length) * 0.55;
      this._bars = [];
      this.data.forEach((d, i) => {
        const val = this._num(d);
        const x = getX(i) - barW / 2;
        const yTop = getY(val);
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", x);
        rect.setAttribute("width", barW);
        rect.setAttribute("y", padTop + plotH); // starts flat at the baseline
        rect.setAttribute("height", 0);
        rect.setAttribute("rx", Math.min(6, barW / 4));
        rect.setAttribute("fill", this.accentColor);
        svg.appendChild(rect);
        this._bars.push({ el: rect, top: yTop, height: (padTop + plotH) - yTop });
      });
      return;
    }

    // line (default)
    let d = "";
    this.data.forEach((entry, i) => {
      const x = getX(i), y = getY(this._num(entry));
      d += (i === 0 ? "M " : "L ") + x + "," + y + " ";
    });
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d.trim());
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#7C9B82");
    path.setAttribute("stroke-width", String(Math.max(2, w * 0.005)));
    path.setAttribute("stroke-linecap", "round");
    svg.appendChild(path);
    const pathLength = path.getTotalLength();
    path.style.strokeDasharray = String(pathLength);
    path.style.strokeDashoffset = String(pathLength);
    this._path = path;
    this._pathLength = pathLength;
    this._strokeTarget = path;

    const lastIdx = this.data.length - 1;
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", getX(lastIdx));
    dot.setAttribute("cy", getY(this._num(this.data[lastIdx])));
    dot.setAttribute("r", String(Math.max(4, w * 0.009)));
    dot.setAttribute("fill", this.accentColor);
    dot.style.opacity = "0";
    svg.appendChild(dot);
    this._dot = dot;

    if (this._showValue) {
      const valueEl = document.createElement("div");
      valueEl.style.fontFamily = "var(--motion-display-font, Georgia, serif)";
      valueEl.style.fontWeight = "600";
      valueEl.style.fontSize = `${Math.round(h * 0.24)}px`;
      valueEl.style.color = this.accentColor;
      valueEl.style.marginTop = `${Math.round(h * 0.06)}px`;
      valueEl.textContent = "0" + this.valueSuffix;
      box.appendChild(valueEl);
      this._valueEl = valueEl;
      this._finalValue = this._num(this.data[lastIdx]);
    }
  }

  _initMetric(box, w, h) {
    const valueEl = document.createElement("div");
    valueEl.style.fontFamily = "var(--motion-display-font, Georgia, serif)";
    valueEl.style.fontWeight = "600";
    valueEl.style.fontSize = `${Math.round(h * 0.4)}px`;
    valueEl.style.color = this.accentColor;
    valueEl.textContent = "0" + this.valueSuffix;
    box.appendChild(valueEl);
    this._valueEl = valueEl;
    this._finalValue = this._num(this.data[0]);
  }

  _initRing(box, w, h) {
    const size = Math.min(w, h);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", size); svg.setAttribute("height", size);
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    box.appendChild(svg);
    const r = size * 0.38, stroke = size * 0.07, c = size / 2;
    const circumference = 2 * Math.PI * r;

    const track = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    track.setAttribute("cx", c); track.setAttribute("cy", c); track.setAttribute("r", r);
    track.setAttribute("fill", "none"); track.setAttribute("stroke", this.gridColor);
    track.setAttribute("stroke-width", stroke);
    svg.appendChild(track);

    const arc = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    arc.setAttribute("cx", c); arc.setAttribute("cy", c); arc.setAttribute("r", r);
    arc.setAttribute("fill", "none"); arc.setAttribute("stroke", this.accentColor);
    arc.setAttribute("stroke-width", stroke);
    arc.setAttribute("stroke-linecap", "round");
    arc.setAttribute("transform", `rotate(-90 ${c} ${c})`);
    arc.style.strokeDasharray = String(circumference);
    arc.style.strokeDashoffset = String(circumference);
    svg.appendChild(arc);
    this._ringArc = arc;
    this._ringCircumference = circumference;
    this._strokeTarget = arc;

    const valueEl = document.createElement("div");
    valueEl.style.position = "absolute";
    valueEl.style.left = "0"; valueEl.style.top = "0"; valueEl.style.width = `${size}px`; valueEl.style.height = `${size}px`;
    valueEl.style.display = "flex"; valueEl.style.alignItems = "center"; valueEl.style.justifyContent = "center";
    valueEl.style.fontFamily = "var(--motion-display-font, Georgia, serif)";
    valueEl.style.fontWeight = "600";
    valueEl.style.fontSize = `${Math.round(size * 0.2)}px`;
    valueEl.style.color = "#F4EEE1";
    valueEl.textContent = "0" + this.valueSuffix;
    box.style.position = "relative";
    box.appendChild(valueEl);
    this._valueEl = valueEl;
    this._finalValue = this._num(this.data[0]);
    this._ringMax = this._finalValue > 100 ? this._finalValue : 100; // percent-style rings default to a 100 ceiling
  }

  registerContentAnimation(masterTl) {
    const at = this.startTime, dur = Math.max(0.1, this.duration);

    if (this.chartType === "bar" && this._bars) {
      this._bars.forEach((b, i) => {
        const bAt = at + i * Math.min(0.1, dur / (this._bars.length + 1));
        proxyTween(masterTl, v => {
          b.el.setAttribute("y", b.top + b.height * (1 - v));
          b.el.setAttribute("height", b.height * v);
        }, 0, 1, dur * 0.7, "back.out(1.4)", bAt);
      });
      return;
    }

    if (this.chartType === "ring") {
      proxyTween(masterTl, v => { this._ringArc.style.strokeDashoffset = String(this._ringCircumference * (1 - v / this._ringMax)); },
        0, this._finalValue, dur, "power2.out", at);
      if (this._valueEl) {
        proxyTween(masterTl, v => { this._valueEl.textContent = Math.round(v) + this.valueSuffix; },
          0, this._finalValue, dur, "power2.out", at);
      }
      return;
    }

    if (this.chartType === "metric") {
      if (this._valueEl) {
        const decimals = String(this._finalValue).includes(".") ? String(this._finalValue).split(".")[1].length : 0;
        proxyTween(masterTl, v => { this._valueEl.textContent = (decimals ? v.toFixed(decimals) : Math.round(v)) + this.valueSuffix; },
          0, this._finalValue, dur, "power2.out", at);
      }
      return;
    }

    // line
    if (this._path) {
      proxyTween(masterTl, v => { this._path.style.strokeDashoffset = String(v); }, this._pathLength, 0, dur, "power1.inOut", at);
      masterTl.fromTo(this._dot, { opacity: 0, scale: 0 }, { opacity: 1, scale: 1, duration: 0.3, ease: "back.out(2)" }, at + dur * 0.85);
    }
    if (this._valueEl) {
      const decimals = String(this._finalValue).includes(".") ? String(this._finalValue).split(".")[1].length : 0;
      proxyTween(masterTl, v => { this._valueEl.textContent = (decimals ? v.toFixed(decimals) : Math.round(v)) + this.valueSuffix; },
        0, this._finalValue, dur * 0.75, "power2.out", at + dur * 0.2);
    }
  }
}

window.DomainChartNode = DomainChartNode;
