/**
 * DomainDiagramNode — real DOM+SVG (was dead Canvas2D). Nodes are styled
 * pill/circle divs; edges are SVG paths between node centers with an
 * optional stroke-dashoffset pulse draw-on, the same channel every other
 * SVG-drawn primitive (chart lines, arrows) uses.
 */
class DomainDiagramNode extends Node {
  constructor(props = {}) {
    super(props);
    this.nodes = Array.isArray(props.nodes) ? props.nodes : [];
    this.edges = Array.isArray(props.edges) ? props.edges : [];
    this.revealStart = props.reveal_start !== undefined ? props.reveal_start : 0.0;
  }

  initDOM(box) {
    box.style.position = "relative";
    const xs = this.nodes.map(n => n.position ? n.position[0] : 0);
    const ys = this.nodes.map(n => n.position ? n.position[1] : 0);
    const pad = 80;
    const minX = Math.min(0, ...xs) - pad, maxX = Math.max(0, ...xs) + pad;
    const minY = Math.min(0, ...ys) - pad, maxY = Math.max(0, ...ys) + pad;
    const w = maxX - minX, h = maxY - minY;
    box.style.width = `${w}px`; box.style.height = `${h}px`;
    box.style.left = `${-w / 2}px`; box.style.top = `${-h / 2}px`;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    svg.style.position = "absolute"; svg.style.left = "0"; svg.style.top = "0";
    box.appendChild(svg);

    const byId = {};
    this.nodes.forEach(n => { byId[n.id] = { x: n.position[0] - minX, y: n.position[1] - minY, color: n.color || "#38BDF8" }; });

    this._edgePaths = [];
    this.edges.forEach(e => {
      const a = byId[e.from], b = byId[e.to];
      if (!a || !b) return;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${a.x} ${a.y} L ${b.x} ${b.y}`);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", e.pulse_color || a.color);
      path.setAttribute("stroke-width", "2.5");
      path.setAttribute("stroke-opacity", "0.5");
      svg.appendChild(path);
      if (e.pulse) {
        const len = path.getTotalLength();
        const pulsePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
        pulsePath.setAttribute("d", path.getAttribute("d"));
        pulsePath.setAttribute("fill", "none");
        pulsePath.setAttribute("stroke", e.pulse_color || a.color);
        pulsePath.setAttribute("stroke-width", "3");
        pulsePath.setAttribute("stroke-linecap", "round");
        pulsePath.style.strokeDasharray = `${len * 0.18} ${len}`;
        pulsePath.style.strokeDashoffset = String(len);
        svg.appendChild(pulsePath);
        this._edgePaths.push({ el: pulsePath, length: len });
      }
    });

    this._nodeEls = [];
    this.nodes.forEach(n => {
      const p = byId[n.id];
      const el = document.createElement("div");
      const isCircle = n.shape === "circle";
      const nw = isCircle ? 64 : Math.max(90, (n.label || "").length * 11 + 32);
      const nh = isCircle ? 64 : 44;
      el.style.position = "absolute";
      el.style.left = `${p.x - nw / 2}px`; el.style.top = `${p.y - nh / 2}px`;
      el.style.width = `${nw}px`; el.style.height = `${nh}px`;
      el.style.display = "flex"; el.style.alignItems = "center"; el.style.justifyContent = "center";
      el.style.borderRadius = isCircle ? "50%" : `${nh / 2}px`;
      el.style.background = `linear-gradient(160deg, ${p.color}, ${p.color}bb)`;
      el.style.boxShadow = `0 0 20px ${p.color}66`;
      el.style.color = "#0A0E1A";
      el.style.fontFamily = "var(--motion-body-font, Inter, sans-serif)";
      el.style.fontWeight = "700";
      el.style.fontSize = "15px";
      el.style.opacity = "0";
      el.textContent = n.label || "";
      box.appendChild(el);
      this._nodeEls.push(el);
    });
  }

  registerContentAnimation(masterTl) {
    this._nodeEls.forEach((el, i) => {
      masterTl.fromTo(el, { opacity: 0, scale: 0 }, { opacity: 1, scale: 1, duration: 0.4, ease: "back.out(1.8)" },
        this.revealStart + i * 0.1);
    });
    this._edgePaths.forEach((p, i) => {
      proxyTween(masterTl, v => { p.el.style.strokeDashoffset = String(v); },
        p.length, -p.length, 1.4, "none", this.revealStart + 0.3 + i * 0.05);
      // repeat handled by re-seeking: the tween above runs once per full
      // spec seek range, which is enough for a short reel; a true
      // infinite pulse would need repeat:-1 the way the arrow's does.
    });
  }
}

window.DomainDiagramNode = DomainDiagramNode;
