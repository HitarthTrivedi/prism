/**
 * Prism Motion Graphics Engine — Core Runtime
 * ─────────────────────────────────────────────
 * Deterministic, frame-accurate animation engine with 2D Affine Matrix math,
 * virtual Camera projection, RK4 Spring Physics solver, and Scene Graph.
 *
 * Paint layer: real DOM/CSS (see Node.mount/renderDOM/initDOM/updateDOM and
 * primitives.js), not Canvas2D. Everything ABOVE the paint layer — Matrix2D,
 * EASINGS, getEase, Camera, Node.evaluateAnimation/updateTransforms, the
 * scene-timing model — is unchanged, pure math with zero paint calls; only
 * `draw(ctx,time)` used to be canvas-specific, and that's what moved.
 */

// ── 1. 2D Affine Transformation Matrix ───────────────────────────────────────
class Matrix2D {
  constructor(a = 1, b = 0, c = 0, d = 1, tx = 0, ty = 0) {
    this.a = a;   // scale x / cos
    this.b = b;   // shear y / sin
    this.c = c;   // shear x / -sin
    this.d = d;   // scale y / cos
    this.tx = tx; // translate x
    this.ty = ty; // translate y
  }

  static identity() {
    return new Matrix2D(1, 0, 0, 1, 0, 0);
  }

  static translation(tx, ty) {
    return new Matrix2D(1, 0, 0, 1, tx, ty);
  }

  static rotation(rad) {
    const cos = Math.cos(rad);
    const sin = Math.sin(rad);
    return new Matrix2D(cos, sin, -sin, cos, 0, 0);
  }

  static scaling(sx, sy) {
    return new Matrix2D(sx, 0, 0, sy, 0, 0);
  }

  multiply(m) {
    return new Matrix2D(
      this.a * m.a + this.c * m.b,
      this.b * m.a + this.d * m.b,
      this.a * m.c + this.c * m.d,
      this.b * m.c + this.d * m.d,
      this.a * m.tx + this.c * m.ty + this.tx,
      this.b * m.tx + this.d * m.ty + this.ty
    );
  }

  invert() {
    const det = this.a * this.d - this.b * this.c;
    if (Math.abs(det) < 1e-12) return Matrix2D.identity();
    const invDet = 1.0 / det;
    return new Matrix2D(
      this.d * invDet,
      -this.b * invDet,
      -this.c * invDet,
      this.a * invDet,
      (this.c * this.ty - this.d * this.tx) * invDet,
      (this.b * this.tx - this.a * this.ty) * invDet
    );
  }

  transformPoint(x, y) {
    return {
      x: this.a * x + this.c * y + this.tx,
      y: this.b * x + this.d * y + this.ty
    };
  }

  applyToContext(ctx) {
    ctx.setTransform(this.a, this.b, this.c, this.d, this.tx, this.ty);
  }

  // CSS's matrix(a,b,c,d,tx,ty) uses the exact same 2D affine convention as
  // this class, so a Matrix2D maps onto it directly — used for the camera's
  // #stage transform (a single, non-nested element, so no double-transform
  // risk the way per-node world matrices would have — see Node.renderDOM).
  toCSSMatrix() {
    return `matrix(${this.a},${this.b},${this.c},${this.d},${this.tx},${this.ty})`;
  }
}

// ── 2. Easing & Spring Dynamics ──────────────────────────────────────────────
const EASINGS = {
  linear: t => t,
  easeInQuad: t => t * t,
  easeOutQuad: t => t * (2 - t),
  easeInOutQuad: t => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t,
  easeInCubic: t => t * t * t,
  easeOutCubic: t => (--t) * t * t + 1,
  easeInOutCubic: t => t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1,
  easeInExpo: t => t === 0 ? 0 : Math.pow(2, 10 * (t - 1)),
  easeOutExpo: t => t === 1 ? 1 : 1 - Math.pow(2, -10 * t),
  easeInOutExpo: t => {
    if (t === 0) return 0;
    if (t === 1) return 1;
    if ((t *= 2) < 1) return 0.5 * Math.pow(2, 10 * (t - 1));
    return 0.5 * (-Math.pow(2, -10 * --t) + 2);
  },
  "back.in": (t, s = 1.70158) => t * t * ((s + 1) * t - s),
  "back.out": (t, s = 1.70158) => --t * t * ((s + 1) * t + s) + 1,
  "back.inOut": (t, s = 1.70158 * 1.525) => {
    if ((t *= 2) < 1) return 0.5 * (t * t * ((s + 1) * t - s));
    return 0.5 * ((t -= 2) * t * ((s + 1) * t + s) + 2);
  },
  "elastic.out": (t, p = 0.3) => Math.pow(2, -10 * t) * Math.sin((t - p / 4) * (2 * Math.PI) / p) + 1,
  "elastic.in": (t, p = 0.3) => t === 0 ? 0 : t === 1 ? 1 :
    -Math.pow(2, 10 * (t - 1)) * Math.sin((t - 1 - p / 4) * (2 * Math.PI) / p),
  "elastic.inOut": (t, p = 0.45) => {
    if (t === 0) return 0;
    if (t === 1) return 1;
    if ((t *= 2) < 1) {
      return -0.5 * Math.pow(2, 10 * (t - 1)) * Math.sin((t - 1 - p / 4) * (2 * Math.PI) / p);
    }
    t -= 1;
    return 0.5 * Math.pow(2, -10 * t) * Math.sin((t - p / 4) * (2 * Math.PI) / p) + 1;
  },
  "bounce.out": t => {
    if (t < (1 / 2.75)) return 7.5625 * t * t;
    if (t < (2 / 2.75)) return 7.5625 * (t -= (1.5 / 2.75)) * t + 0.75;
    if (t < (2.5 / 2.75)) return 7.5625 * (t -= (2.25 / 2.75)) * t + 0.9375;
    return 7.5625 * (t -= (2.625 / 2.75)) * t + 0.984375;
  },
  "bounce.in": t => 1 - EASINGS["bounce.out"](1 - t),
  "bounce.inOut": t => t < 0.5
    ? 0.5 * (1 - EASINGS["bounce.out"](1 - 2 * t))
    : 0.5 * EASINGS["bounce.out"](2 * t - 1) + 0.5,
  smooth: t => t * t * (3 - 2 * t),
  spring: (t, mass = 1.0, stiffness = 160.0, damping = 12.0) => {
    const w0 = Math.sqrt(stiffness / mass);
    const zeta = damping / (2 * Math.sqrt(stiffness * mass));
    if (zeta < 1.0) {
      const wd = w0 * Math.sqrt(1 - zeta * zeta);
      const envelope = Math.exp(-zeta * w0 * t * 4.0);
      return 1.0 - envelope * (Math.cos(wd * t * 4.0) + (zeta / Math.sqrt(1 - zeta * zeta)) * Math.sin(wd * t * 4.0));
    } else {
      return 1.0 - Math.exp(-w0 * t * 4.0) * (1.0 + w0 * t * 4.0);
    }
  }
};

const FALLBACK_EASE_ROTATION = ["easeOutCubic", "easeOutQuad", "back.out", "easeOutExpo"];

function _hashSeed(seed) {
  let h = 0;
  const s = String(seed || "");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function getEase(name, seed) {
  if (typeof name === "function") return name;
  if (name && name.startsWith("spring")) return EASINGS.spring;
  if (name) {
    const found = EASINGS[name] || EASINGS[name.toLowerCase()];
    if (found) return found;
  }
  const fallback = FALLBACK_EASE_ROTATION[_hashSeed(seed) % FALLBACK_EASE_ROTATION.length];
  return EASINGS[fallback];
}

// ── 3. Virtual Camera System ─────────────────────────────────────────────────
class Camera {
  constructor(viewportWidth = 1080, viewportHeight = 1920) {
    this.w = viewportWidth;
    this.h = viewportHeight;
    this.x = viewportWidth / 2;
    this.y = viewportHeight / 2;
    this.zoom = 1.0;
    this.rotation = 0.0;
    this.tracks = [];
    this.time = 0;
  }

  setTracks(tracks) {
    this.tracks = [...(tracks || [])].sort((a, b) => (a.time || 0) - (b.time || 0));
  }

  evaluate(time) {
    this.time = time;
    if (!this.tracks.length) return;

    let curX = this.w / 2;
    let curY = this.h / 2;
    let curZoom = 1.0;
    let curRot = 0.0;

    for (let i = 0; i < this.tracks.length; i++) {
      const tr = this.tracks[i];
      const startTime = tr.time || 0.0;
      const duration = tr.duration || 0.0;
      const easeFn = getEase(tr.easing || "easeInOutCubic", `camera:${i}`);

      if (time <= startTime && i === 0) {
        if (tr.position) { curX = tr.position[0]; curY = tr.position[1]; }
        if (tr.zoom !== undefined) curZoom = tr.zoom;
        if (tr.rotation !== undefined) curRot = tr.rotation * (Math.PI / 180);
        break;
      }

      if (time >= startTime + duration) {
        if (tr.position) { curX = tr.position[0]; curY = tr.position[1]; }
        if (tr.zoom !== undefined) curZoom = tr.zoom;
        if (tr.rotation !== undefined) curRot = tr.rotation * (Math.PI / 180);
      } else if (time > startTime) {
        const p = easeFn((time - startTime) / Math.max(0.001, duration));
        if (tr.position) {
          curX = curX + (tr.position[0] - curX) * p;
          curY = curY + (tr.position[1] - curY) * p;
        }
        if (tr.zoom !== undefined) {
          curZoom = curZoom + (tr.zoom - curZoom) * p;
        }
        if (tr.rotation !== undefined) {
          const targetRot = tr.rotation * (Math.PI / 180);
          curRot = curRot + (targetRot - curRot) * p;
        }
        break;
      }
    }

    this.x = curX;
    this.y = curY;
    this.zoom = curZoom;
    this.rotation = curRot;
  }

  getViewMatrix() {
    const driftX = Math.sin((this.time || 0) * 0.8) * 3.5 * (1 / Math.max(0.5, this.zoom));
    const driftY = Math.cos((this.time || 0) * 0.6) * 2.5 * (1 / Math.max(0.5, this.zoom));

    const tCenter = Matrix2D.translation(this.w / 2, this.h / 2);
    const sZoom = Matrix2D.scaling(this.zoom, this.zoom);
    const rRot = Matrix2D.rotation(-this.rotation);
    const tCam = Matrix2D.translation(-(this.x + driftX), -(this.y + driftY));

    return tCenter.multiply(sZoom).multiply(rRot).multiply(tCam);
  }
}

// Pure (no mutation) position-only re-evaluation of a node's `enter` block
// at an arbitrary time — used by child `follow` lag above to sample a
// node's position at a DIFFERENT time than the one it's actually being
// rendered at, without disturbing its real evalPos/evalOpacity/etc. for
// the current frame.
function _resolveLocalPositionAt(node, time) {
  const pos = [...node.position];
  const e = node.animation && node.animation.enter;
  if (!e) return pos;
  const startTime = e.time || 0.0;
  const duration = e.duration || 0.6;
  if (time < startTime) return pos;
  const raw = Math.min(1, (time - startTime) / duration);
  const props = e.properties || {};
  const perProp = props.position && props.position.easing;
  const ease = getEase(perProp || e.easing, `${node.id}:enter:position`)(raw);
  if (e.type === "slide_up") pos[1] += (e.distance || 60) * (1 - ease);
  else if (e.type === "slide_down") pos[1] -= (e.distance || 60) * (1 - ease);
  return pos;
}

// ── 4. Scene Graph Node ──────────────────────────────────────────────────────
class Node {
  constructor(props = {}) {
    this.id = props.id || `node_${Math.random().toString(36).substr(2, 9)}`;
    this.type = props.type || "group";
    this.position = props.position ? [...props.position] : [0, 0];
    this.scale = props.scale ? [...props.scale] : [1, 1];
    this.rotation = props.rotation || 0;
    this.anchor = (Array.isArray(props.anchor) && props.anchor.length === 2
      && typeof props.anchor[0] === "number" && typeof props.anchor[1] === "number")
      ? [...props.anchor] : [0.5, 0.5];
    this.opacity = props.opacity !== undefined ? props.opacity : 1.0;
    this.zIndex = props.z_index || 0;
    this.visible = props.visible !== undefined ? props.visible : true;
    this.blendMode = props.blend_mode || "source-over";
    this.animation = props.animation || null;
    this.children = [];
    this.parent = null;

    this.evalPos = [0, 0];
    this.evalScale = [1, 1];
    this.evalRotation = 0;
    this.evalOpacity = 1.0;
    this.worldMatrix = Matrix2D.identity();

    this._host = null;
    this._box = null;
  }

  addChild(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  evaluateAnimation(time) {
    this.evalPos = [...this.position];
    this.evalScale = [...this.scale];
    this.evalRotation = this.rotation;
    this.evalOpacity = this.opacity;

    if (!this.animation) return;

    const easeForBlock = (block, blockName, propName) => {
      const props = block.properties || {};
      const perProp = props[propName] && props[propName].easing;
      return getEase(perProp || block.easing, `${this.id}:${blockName}:${propName}`);
    };

    if (this.animation.enter) {
      const e = this.animation.enter;
      const startTime = e.time || 0.0;
      const duration = e.duration || 0.6;

      if (time < startTime) {
        this.evalOpacity = 0.0;
      } else if (time < startTime + duration) {
        const raw = (time - startTime) / duration;
        const pOpacity = easeForBlock(e, "enter", "opacity")(raw);
        const pMove = easeForBlock(e, "enter", "position")(raw);
        const pScale = easeForBlock(e, "enter", "scale")(raw);
        if (e.type === "fade_in") {
          this.evalOpacity = this.opacity * pOpacity;
        } else if (e.type === "pop_in" || e.type === "scale_up") {
          this.evalOpacity = this.opacity * Math.min(1.0, pOpacity * 1.5);
          this.evalScale = [this.scale[0] * pScale, this.scale[1] * pScale];
        } else if (e.type === "slide_up") {
          const dy = (e.distance || 60) * (1 - pMove);
          this.evalPos[1] += dy;
          this.evalOpacity = this.opacity * pOpacity;
        } else if (e.type === "slide_down") {
          const dy = -(e.distance || 60) * (1 - pMove);
          this.evalPos[1] += dy;
          this.evalOpacity = this.opacity * pOpacity;
        }
      }
    }

    if (this.animation.exit) {
      const x = this.animation.exit;
      const startTime = x.time !== undefined ? x.time : Infinity;
      const duration = x.duration || 0.6;

      if (time >= startTime && time < startTime + duration) {
        const raw = (time - startTime) / duration;
        const pOpacity = easeForBlock(x, "exit", "opacity")(raw);
        const pMove = easeForBlock(x, "exit", "position")(raw);
        const pScale = easeForBlock(x, "exit", "scale")(raw);
        if (x.type === "fade_out") {
          this.evalOpacity = this.evalOpacity * (1 - pOpacity);
        } else if (x.type === "pop_out" || x.type === "scale_down") {
          this.evalOpacity = this.evalOpacity * (1 - Math.min(1.0, pOpacity * 1.5));
          this.evalScale = [this.evalScale[0] * (1 - pScale), this.evalScale[1] * (1 - pScale)];
        } else if (x.type === "slide_up") {
          this.evalPos[1] -= (x.distance || 60) * pMove;
          this.evalOpacity = this.evalOpacity * (1 - pOpacity);
        } else if (x.type === "slide_down") {
          this.evalPos[1] += (x.distance || 60) * pMove;
          this.evalOpacity = this.evalOpacity * (1 - pOpacity);
        }
      } else if (time >= startTime + duration) {
        this.evalOpacity = 0.0;
      }
    }

    if (Array.isArray(this.animation.tracks)) {
      for (const track of this.animation.tracks) {
        const prop = track.property;
        const keyframes = track.keyframes || [];
        if (keyframes.length < 2) continue;
        for (let i = 0; i < keyframes.length - 1; i++) {
          const k1 = keyframes[i];
          const k2 = keyframes[i + 1];
          if (time >= k1.time && time <= k2.time) {
            const easeFn = getEase(k2.easing, `${this.id}:track:${prop}:${i}`);
            const p = easeFn((time - k1.time) / Math.max(0.0001, k2.time - k1.time));
            if (prop === "position.x") this.evalPos[0] = k1.value + (k2.value - k1.value) * p;
            else if (prop === "position.y") this.evalPos[1] = k1.value + (k2.value - k1.value) * p;
            else if (prop === "opacity") this.evalOpacity = k1.value + (k2.value - k1.value) * p;
            else if (prop === "scale") this.evalScale = [k1.value + (k2.value - k1.value) * p, k1.value + (k2.value - k1.value) * p];
            else if (prop === "rotation") this.evalRotation = k1.value + (k2.value - k1.value) * p;
            break;
          }
        }
      }
    }

    const w = this.animation.secondary_motion;
    if (w && w.property) {
      const seed = _hashSeed(w.seed !== undefined ? w.seed : this.id);
      const freq = w.freq || 1.0;
      const amount = w.amount || 0;
      const phase = (seed % 1000) / 1000 * Math.PI * 2;
      const offset = Math.sin(time * freq * Math.PI * 2 + phase) * amount;
      if (w.property === "rotation") this.evalRotation += offset;
      else if (w.property === "position.x") this.evalPos[0] += offset;
      else if (w.property === "position.y") this.evalPos[1] += offset;
      else if (w.property === "opacity") this.evalOpacity = Math.max(0, Math.min(1, this.evalOpacity + offset));
      else if (w.property === "scale") this.evalScale = [this.evalScale[0] + offset, this.evalScale[1] + offset];
    }

    const follow = this.animation.follow;
    if (follow && this.parent) {
      const lag = follow.lag || 0.08;
      const damping = follow.damping !== undefined ? follow.damping : 0.5;
      const parentNow = _resolveLocalPositionAt(this.parent, time);
      const parentPast = _resolveLocalPositionAt(this.parent, Math.max(0, time - lag));
      this.evalPos[0] += (parentPast[0] - parentNow[0]) * damping;
      this.evalPos[1] += (parentPast[1] - parentNow[1]) * damping;
    }
  }

  updateTransforms(time, parentMatrix = Matrix2D.identity()) {
    if (!this.visible) return;
    this.evaluateAnimation(time);

    const rad = this.evalRotation * (Math.PI / 180);
    const local = Matrix2D.translation(this.evalPos[0], this.evalPos[1])
      .multiply(Matrix2D.rotation(rad))
      .multiply(Matrix2D.scaling(this.evalScale[0], this.evalScale[1]));

    this.worldMatrix = parentMatrix.multiply(local);

    for (const child of this.children) {
      child.updateTransforms(time, this.worldMatrix);
    }
  }

  // ── DOM mounting — once per node, not per frame ──────────────────────────
  // Two elements: a zero-size *host* (carries the per-frame transform/
  // opacity, real children attach here so the browser composes nested
  // transforms/opacity for free) and a *box* inside it (sized, anchor-offset,
  // carries the actual paint). Collapsing these into one element would make
  // rotation/scale pivot at the box's center instead of the node's own
  // evalPos — wrong the moment a non-center-anchored node (a corner badge,
  // an edge label) has children.
  mount(parentEl) {
    const host = document.createElement("div");
    host.style.position = "absolute";
    host.style.left = "0";
    host.style.top = "0";
    host.style.transformOrigin = "0 0";
    host.style.zIndex = String(this.zIndex);
    host.style.mixBlendMode = this.blendMode === "source-over" ? "normal" : this.blendMode;
    if (!this.visible) host.style.display = "none";

    const box = document.createElement("div");
    box.style.position = "absolute";
    box.style.left = "50%";
    box.style.top = "50%";
    box.style.transform = "translate(-50%,-50%)";
    host.appendChild(box);

    this._host = host;
    this._box = box;
    parentEl.appendChild(host);

    this.initDOM(box);

    const sorted = [...this.children].sort((a, b) => a.zIndex - b.zIndex);
    for (const child of sorted) child.mount(host);
  }

  // Subclass hook — build this node's STATIC inner DOM/CSS once (text
  // spans, an <img> tag, box sizing/anchor offset). Base "group" node has
  // no paint of its own.
  initDOM(box) {}

  // ── Per-frame DOM write ──────────────────────────────────────────────────
  // Position/opacity are handled generically here (every frame, even
  // identity, deliberately — CSS z-index does nothing without `position`,
  // and an element with no `transform` doesn't establish its own stacking
  // context, so skipping this write would let z-order leak across siblings
  // in a way the old canvas engine's linear paint order never allowed).
  renderDOM(time) {
    const host = this._host;
    if (!host) return;
    if (!this.visible) { host.style.display = "none"; return; }
    host.style.display = "";
    host.style.opacity = String(Math.max(0, Math.min(1, this.evalOpacity)));
    host.style.transform =
      `translate(${this.evalPos[0]}px,${this.evalPos[1]}px) ` +
      `rotate(${this.evalRotation}deg) ` +
      `scale(${this.evalScale[0]},${this.evalScale[1]})`;
    this.updateDOM(time);
    for (const child of this.children) child.renderDOM(time);
  }

  // Subclass hook — write this node's per-frame PAINT (the box's own
  // properties only; host's transform/opacity are handled above).
  updateDOM(time) {}
}

// ── 5. Motion Graphics Runtime Host ──────────────────────────────────────────
class MotionRuntime {
  constructor(backdropCanvas, vignetteEl, grainEl, stageEl) {
    this.backdropCanvas = backdropCanvas;
    this.backdropCtx = backdropCanvas.getContext("2d", { alpha: false, desynchronized: true });
    // Grain/vignette strength is static per spec (visual.grain_opacity /
    // visual.vignette_strength never change with time), so these are real
    // CSS mix-blend-mode overlays set once in loadSpec, not a per-frame
    // canvas draw. They MUST be genuine CSS blending, not a second
    // transparent <canvas> composited with ctx.globalCompositeOperation
    // "multiply"/"overlay" — those blend against whatever's ALREADY
    // painted on that SAME canvas; on an empty transparent one there's
    // nothing to multiply against, so the vignette's own full-opacity
    // white center paints through as an opaque blob instead of darkening
    // the edges the way it did against the old engine's single opaque
    // canvas. (Found by actually rendering and looking — first attempt at
    // this split used a transparent postCanvas with ctx compositing and
    // produced exactly that white-out.)
    this.vignetteEl = vignetteEl;
    this.grainEl = grainEl;
    this.stageEl = stageEl;
    this.width = 1080;
    this.height = 1920;
    this.fps = 30;
    this.duration = 10.0;
    this.background = "#090D16";
    this.camera = new Camera(this.width, this.height);
    this.effects = window.MotionEffects ? new window.MotionEffects(this.width, this.height) : null;
    this.rootNodes = [];
    this.sceneWindows = [];
    this.spec = null;
  }

  loadSpec(spec) {
    this.spec = spec;
    const p = spec.project || {};
    this.width = p.width || 1080;
    this.height = p.height || 1920;
    this.fps = p.fps || 30;
    this.duration = p.duration || 10.0;
    this.background = p.background || "#090D16";

    this.backdropCanvas.width = this.width;
    this.backdropCanvas.height = this.height;

    const px = `${this.width}px`, pyh = `${this.height}px`;
    document.documentElement.style.width = px;
    document.documentElement.style.height = pyh;
    document.body.style.width = px;
    document.body.style.height = pyh;
    for (const el of [this.backdropCanvas, this.stageEl, this.vignetteEl, this.grainEl]) {
      el.style.width = px;
      el.style.height = pyh;
    }

    this.camera = new Camera(this.width, this.height);
    this.effects = window.MotionEffects ? new window.MotionEffects(this.width, this.height) : null;
    if (spec.camera && spec.camera.tracks) {
      this.camera.setTracks(spec.camera.tracks);
    }

    this._setupPostProcessing(spec.visual || {});

    // Scenes have no clock of their own — resolve_motion_spec() (Python)
    // already shifted every node's authored, scene-local times onto this
    // one global timeline and gave each scene a real `start`. Each scene
    // gets its own DOM container now (rather than a per-node time check),
    // toggled visible/hidden in seek() — visibility cascades to every
    // node mounted inside it for free, the same way the old per-root
    // sceneStart/sceneEnd gate transitively hid a whole subtree by simply
    // never calling render() on its root.
    this.stageEl.innerHTML = "";
    this.rootNodes = [];
    this.sceneWindows = [];
    const scenes = spec.scenes || [];
    for (const scene of scenes) {
      const sceneStart = scene.start || 0;
      const sceneEnd = sceneStart + (scene.duration || 0);
      const sceneEl = document.createElement("div");
      sceneEl.className = "scene";
      sceneEl.style.position = "absolute";
      sceneEl.style.inset = "0";
      sceneEl.style.visibility = "hidden";
      this.stageEl.appendChild(sceneEl);
      this.sceneWindows.push({ el: sceneEl, start: sceneStart, end: sceneEnd });

      const sceneRoots = [];
      for (const nodeData of scene.nodes || []) {
        const node = createNodeFromSpec(nodeData);
        if (node) { sceneRoots.push(node); this.rootNodes.push(node); }
      }
      sceneRoots.sort((a, b) => a.zIndex - b.zIndex);
      for (const root of sceneRoots) root.mount(sceneEl);
    }
  }

  // Grain + vignette as real CSS mix-blend-mode overlays — genuine
  // blending against the actual page content behind them (backdrop AND
  // the DOM scene graph), computed once since neither varies with time.
  // The vignette reuses the exact gradient stops applyPostProcessing()
  // already used; the grain reuses effects.js's own noise canvas as a
  // data: URI tile instead of redrawing it as a canvas pattern.
  _setupPostProcessing(visual) {
    const vigStrength = visual.vignette_strength !== undefined ? visual.vignette_strength : 0.58;
    if (vigStrength > 0.001) {
      this.vignetteEl.style.background =
        `radial-gradient(circle at 50% 50%, rgba(255,255,255,1.0) 0%, ` +
        `rgba(210,218,235,0.97) 62%, rgba(8,10,18,${vigStrength}) 100%)`;
      this.vignetteEl.style.display = "block";
    } else {
      this.vignetteEl.style.display = "none";
    }

    const grainOpacity = visual.grain_opacity !== undefined ? visual.grain_opacity : 0.05;
    if (grainOpacity > 0.001 && this.effects && this.effects._noiseCanvas) {
      this.grainEl.style.backgroundImage = `url(${this.effects._noiseCanvas.toDataURL()})`;
      this.grainEl.style.backgroundRepeat = "repeat";
      this.grainEl.style.opacity = String(grainOpacity);
      this.grainEl.style.display = "block";
    } else {
      this.grainEl.style.display = "none";
    }
  }

  // Real <img> elements now (see ImageNode.initDOM in primitives.js) — this
  // is the browser's own loading signal, not a hand-rolled state machine.
  // "Pending" = not yet .complete; that becomes true once an image has
  // either loaded OR errored, so a broken asset can never hang this forever
  // — the same "wait for everything ready before frame 0" discipline
  // core.reel_web's own harness uses for web fonts.
  pendingImageCount() {
    const imgs = this.stageEl.querySelectorAll("img");
    let pending = 0;
    for (const img of imgs) if (!img.complete) pending++;
    return pending;
  }

  seek(frame) {
    const time = frame / this.fps;
    this.camera.evaluate(time);

    for (const root of this.rootNodes) {
      root.updateTransforms(time, Matrix2D.identity());
      root.renderDOM(time);
    }

    for (const sw of this.sceneWindows) {
      const on = time >= sw.start && time < sw.end;
      sw.el.style.visibility = on ? "visible" : "hidden";
    }

    // Camera applies to the DOM scene graph only — #stage's own transform,
    // composed with every node's own (local, not world) transform via
    // ordinary nested-element transform inheritance. transform-origin:0 0
    // on #stage matters here: getViewMatrix()'s matrix is built relative to
    // true (0,0) (it bakes in its own translate-to-center term), so a
    // default 50%/50% CSS origin would double-center it the moment
        // camera pan/zoom/rotation is non-identity.
    this.stageEl.style.transform = this.camera.getViewMatrix().toCSSMatrix();

    const visual = Object.assign(
      { background: this.background },
      (this.spec && this.spec.visual) ? this.spec.visual : {}
    );

    // Layer 1+2: backdrop + ambient particles, BEHIND #stage, unaffected by
    // camera transform (matches the old engine: these use camera.x/y only
    // as a subtle parallax offset, never the full view matrix).
    const bctx = this.backdropCtx;
    bctx.setTransform(1, 0, 0, 1, 0, 0);
    if (this.effects) {
      this.effects.drawStudioBackdrop(bctx, visual, this.camera, time);
      this.effects.drawAmbientParticles(bctx, visual, this.camera, time);
    } else {
      bctx.fillStyle = visual.background || this.background;
      bctx.fillRect(0, 0, this.width, this.height);
    }
    // Layer 4 (grain/vignette) is set once in _setupPostProcessing(),
    // not drawn per frame — see that method's comment.
  }
}

window.Matrix2D = Matrix2D;
window.EASINGS = EASINGS;
window.getEase = getEase;
window.Camera = Camera;
window.Node = Node;
window.MotionRuntime = MotionRuntime;
