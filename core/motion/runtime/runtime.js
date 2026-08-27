/**
 * Prism Motion Graphics Engine — Core Runtime
 * ─────────────────────────────────────────────
 * Deterministic, frame-accurate animation engine with 2D Affine Matrix math,
 * virtual Camera projection, RK4 Spring Physics solver, and Scene Graph.
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
  // elastic.in/.inOut, bounce.in/.inOut and "smooth" were listed in
  // schema.py's SUPPORTED_EASINGS but had no entry here, so getEase()
  // silently collapsed all five to easeOutCubic — the schema promised the
  // AI 20 curves and the renderer only ever drew 15 of them.
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
  // Standard smoothstep (3t²-2t³) — a gentler, more "settled" alternative
  // to easeInOutCubic with no inflection snap at the midpoint.
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

// Curated fallback rotation — replaces the old single hardcoded
// easeOutCubic default. Every node/track that omitted or misspelled an
// easing used to collapse onto the exact same curve, which is the same
// anchoring mechanism measured to produce 88% one-curve dominance in
// reel_web.py's output. Hashing a seed (e.g. node id + which animation)
// keeps rendering fully deterministic — same spec, same video, always —
// without reintroducing that problem here.
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
    this.anchor = props.anchor ? [...props.anchor] : [0.5, 0.5];
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

    // Per-property easing: `properties` optionally overrides the ease for
    // opacity/position/scale independently of the block's own flat
    // `easing` — e.g. opacity settles gently while scale snaps with
    // overshoot. Falls back to the block's `easing`, then to a seeded
    // rotation (getEase's fallback) rather than one hardcoded curve, so a
    // spec that never sets `easing` at all still gets some variety across
    // properties instead of collapsing every node onto the same curve.
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

    // `exit` was validated by schema.py but never evaluated here — every
    // scene was structurally unable to animate anything OUT, guaranteeing
    // "settles, then holds until the cut" regardless of what the spec
    // asked for. Mirrors `enter`: an explicit absolute `time`, defaulting
    // to Infinity (never exits) rather than 0, so a node without an
    // authored exit is unaffected.
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

    // Small seeded secondary-motion primitive — additive, deterministic
    // (seeded hash, never Math.random(), matching this engine's own
    // determinism requirement: same spec, same video, every render),
    // applied after the main animation so it layers organic drift on top
    // rather than replacing authored timing.
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

    // Opt-in child lag/overshoot. Parenting alone gives NONE of this — a
    // child's world transform is a rigid, instant multiply of the parent's
    // (confirmed against how After Effects itself works: follow-through is
    // always explicit, never automatic from hierarchy). `follow` samples
    // the parent's own position at an earlier time via a pure, non-
    // mutating helper (so the parent's real eval state for THIS frame is
    // untouched) and trails the child toward it — a real drag effect,
    // still a pure function of time. Covers slide_up/slide_down entrances
    // on the parent only; exits and tracks-driven parent motion aren't
    // sampled by this MVP.
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

  render(ctx, time) {
    if (!this.visible || this.evalOpacity <= 0.001) return;
    // Root-node scene window (see loadSpec()) — undefined for anything
    // that isn't a direct child of a scene, so nested nodes are unaffected
    // and gated only by whether their parent's render() runs at all.
    if (this.sceneStart !== undefined && (time < this.sceneStart || time >= this.sceneEnd)) return;
    ctx.save();
    ctx.globalAlpha = Math.max(0, Math.min(1, ctx.globalAlpha * this.evalOpacity));
    ctx.globalCompositeOperation = this.blendMode;
    this.worldMatrix.applyToContext(ctx);

    this.draw(ctx, time);

    const sorted = [...this.children].sort((a, b) => a.zIndex - b.zIndex);
    for (const child of sorted) {
      child.render(ctx, time);
    }
    ctx.restore();
  }

  draw(ctx, time) {
    // Abstract override
  }
}

// ── 5. Motion Graphics Runtime Host ──────────────────────────────────────────
class MotionRuntime {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
    this.width = 1080;
    this.height = 1920;
    this.fps = 30;
    this.duration = 10.0;
    this.background = "#090D16";
    this.camera = new Camera(this.width, this.height);
    this.effects = window.MotionEffects ? new window.MotionEffects(this.width, this.height) : null;
    this.rootNodes = [];
    this.spec = null;
    this.assets = {};
  }

  loadSpec(spec) {
    this.spec = spec;
    const p = spec.project || {};
    this.width = p.width || 1080;
    this.height = p.height || 1920;
    this.fps = p.fps || 30;
    this.duration = p.duration || 10.0;
    this.background = p.background || "#090D16";

    this.canvas.width = this.width;
    this.canvas.height = this.height;
    this.camera = new Camera(this.width, this.height);
    this.effects = window.MotionEffects ? new window.MotionEffects(this.width, this.height) : null;
    if (spec.camera && spec.camera.tracks) {
      this.camera.setTracks(spec.camera.tracks);
    }

    // Scenes have no clock of their own — resolve_motion_spec() (Python)
    // already shifted every node's authored, scene-local times onto this
    // one global timeline and gave each scene a real `start`. What's left
    // for the renderer is visibility: a scene's nodes should only draw
    // during [start, start+duration), or scene 2 renders on top of scene 1
    // for the whole video instead of after it. sceneStart/sceneEnd are
    // stamped onto each ROOT node only (see render()'s gate below) — a
    // node inside a `children` array is gated by its parent's render call
    // never running, not by carrying its own copy.
    this.rootNodes = [];
    const scenes = spec.scenes || [];
    for (const scene of scenes) {
      const sceneStart = scene.start || 0;
      const sceneEnd = sceneStart + (scene.duration || 0);
      for (const nodeData of scene.nodes || []) {
        const node = createNodeFromSpec(nodeData);
        if (node) {
          node.sceneStart = sceneStart;
          node.sceneEnd = sceneEnd;
          this.rootNodes.push(node);
        }
      }
    }
  }

  seek(frame) {
    const time = frame / this.fps;
    this.camera.evaluate(time);

    for (const root of this.rootNodes) {
      root.updateTransforms(time, Matrix2D.identity());
    }

    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);

    // Resolve AI visual config (from spec.visual, with background fallback)
    const visual = Object.assign(
      { background: this.background },
      (this.spec && this.spec.visual) ? this.spec.visual : {}
    );

    // Layer 1: Parameterized studio backdrop (spotlight, grid) — AI-driven
    if (this.effects) {
      this.effects.drawStudioBackdrop(ctx, visual, this.camera, time);
    } else {
      ctx.fillStyle = visual.background || this.background;
      ctx.fillRect(0, 0, this.width, this.height);
    }

    // Layer 2: Atmospheric bokeh particles — AI-driven color, count, speed
    if (this.effects) {
      this.effects.drawAmbientParticles(ctx, visual, this.camera, time);
    }

    // Layer 3: Camera Matrix & Scene Graph Render
    const camMat = this.camera.getViewMatrix();
    ctx.save();
    camMat.applyToContext(ctx);

    const sortedRoots = [...this.rootNodes].sort((a, b) => a.zIndex - b.zIndex);
    for (const root of sortedRoots) {
      root.render(ctx, time);
    }
    ctx.restore();

    // Layer 4: Post-Processing — film grain & vignette with AI-specified intensities
    if (this.effects) {
      this.effects.applyPostProcessing(ctx, visual);
    }
  }

}

window.Matrix2D = Matrix2D;
window.EASINGS = EASINGS;
window.getEase = getEase;
window.Camera = Camera;
window.Node = Node;
window.MotionRuntime = MotionRuntime;
