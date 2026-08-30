/**
 * MotionTransitions — the cross-scene cut library Motion was missing
 * entirely (every cut used to be a hard visibility toggle). `push`/
 * `push_up`/`squeeze`/`zoom` carry the same motion design as
 * core.reel_web.py's own `.cut-push/-push-up/-squeeze/-zoom` (itself
 * adapted from HyperFrames under Apache 2.0 — see prism_gui/NOTICE), now
 * expressed as real GSAP tweens rather than that file's pure-CSS
 * calc()-of-progress-variables reimplementation, since Motion has GSAP
 * throughout and doesn't need the dependency-free workaround Studio built
 * specifically because IT has no GSAP. `blur_swoosh`/`light_leak`
 * translate the Meridian HyperFrames reference's own two transitions
 * directly (its blur-swoosh: filter/skew/x/opacity; its light-leak:
 * overlapping radial-gradient blobs) — same motion, GSAP tween shape
 * matching every other tween in this runtime instead of that project's
 * own gsap.timeline() calls (translated, not copied — Meridian is a
 * reference example, not a dependency).
 *
 * Every transition is keyed off the SAME already-computed timing fields
 * resolver.py stamps onto the resolved spec (scene.start,
 * scene.transitionInStart, scene._transitionOverlap) — nothing here
 * recomputes overlap timing.
 */
(function () {
  let leakContainer = null;
  function getLeakContainer(stageEl) {
    if (leakContainer) return leakContainer;
    leakContainer = document.createElement("div");
    leakContainer.id = "leakContainer";
    leakContainer.style.position = "absolute";
    leakContainer.style.inset = "0";
    leakContainer.style.zIndex = "1";
    leakContainer.style.pointerEvents = "none";
    leakContainer.style.overflow = "hidden";
    stageEl.parentNode.insertBefore(leakContainer, stageEl.nextSibling);
    return leakContainer;
  }

  // Every tween in this engine is explicit fromTo — a timeline that is
  // ONLY EVER seeked, never played forward from 0 in real time, can't
  // reliably rely on a plain .to()'s implicit starting value (GSAP
  // resolves that lazily, tied to normal playback reaching the tween,
  // not to an arbitrary .seek() landing inside its window). push() and
  // squeeze() used to be the one gsap.set()+.to() pair in the whole
  // codebase — everything else (node tweens, zoom, blurSwoosh) was
  // already fromTo, which is exactly why only these two broke.
  function push(masterTl, outEl, inEl, cutAt, overlap, w, h, vertical) {
    const prop = vertical ? "y" : "x";
    const dim = vertical ? h : w;
    masterTl.fromTo(outEl, { [prop]: 0 }, { [prop]: -dim, duration: overlap, ease: "power2.inOut" }, cutAt - overlap);
    masterTl.fromTo(inEl, { [prop]: dim }, { [prop]: 0, duration: overlap, ease: "power2.inOut" }, cutAt - overlap);
  }

  function squeeze(masterTl, outEl, inEl, cutAt, overlap) {
    outEl.style.transformOrigin = "left center";
    inEl.style.transformOrigin = "right center";
    masterTl.fromTo(outEl, { scaleX: 1 }, { scaleX: 0, duration: overlap, ease: "power2.inOut" }, cutAt - overlap);
    masterTl.fromTo(inEl, { scaleX: 0 }, { scaleX: 1, duration: overlap, ease: "power2.inOut" }, cutAt - overlap);
  }

  function zoom(masterTl, outEl, inEl, cutAt, overlap) {
    proxyTween(masterTl, v => { outEl.style.filter = `blur(${v}px)`; }, 0, 8, overlap, "power2.in", cutAt - overlap);
    masterTl.fromTo(outEl, { scale: 1, opacity: 1 }, { scale: 2.5, opacity: 0, duration: overlap, ease: "power2.in" }, cutAt - overlap);
    proxyTween(masterTl, v => { inEl.style.filter = `blur(${v}px)`; }, 8, 0, overlap, "power2.out", cutAt - overlap * 0.5);
    masterTl.fromTo(inEl, { scale: 0.5, opacity: 0 }, { scale: 1, opacity: 1, duration: overlap, ease: "power2.out" }, cutAt - overlap * 0.5);
  }

  // Directional blur-swoosh — Meridian's own signature cut: the outgoing
  // scene blurs/skews/slides out one direction, the incoming scene mirrors
  // in from the other, starting partway through the outgoing tween.
  function blurSwoosh(masterTl, outEl, inEl, cutAt, overlap) {
    const half = overlap * 0.8;
    proxyTween(masterTl, v => { outEl.style.filter = `blur(${v}px)`; }, 0, 12, half, "power3.in", cutAt - overlap);
    masterTl.fromTo(outEl, { x: 0, skewX: 0, opacity: 1 }, { x: -60, skewX: -8, opacity: 0, duration: half, ease: "power3.in" }, cutAt - overlap);

    const inAt = cutAt - overlap + overlap * 0.3;
    proxyTween(masterTl, v => { inEl.style.filter = `blur(${v}px)`; }, 12, 0, half, "power3.out", inAt);
    masterTl.fromTo(inEl, { x: 60, skewX: 8, opacity: 0 }, { x: 0, skewX: 0, opacity: 1, duration: half, ease: "power3.out" }, inAt);
  }

  // Warm radial light-leak — Meridian's second transition: a soft
  // overall warm wash plus two larger offset color blobs, each animated
  // independently so the leak doesn't read as one mechanical pulse.
  function lightLeak(masterTl, outEl, inEl, cutAt, overlap, stageEl, w, h) {
    const container = getLeakContainer(stageEl);
    const wash = document.createElement("div");
    wash.style.cssText = `position:absolute;inset:-${h * 0.1}px;background:#b9863e;opacity:0;`;
    const blob1 = document.createElement("div");
    blob1.style.cssText = `position:absolute;width:${w * 1.6}px;height:${w * 1.6}px;left:${-w * 0.45}px;top:${h * 0.15}px;border-radius:50%;opacity:0;background:radial-gradient(circle, rgba(217,176,108,0.95) 0%, rgba(217,176,108,0) 65%);`;
    const blob2 = document.createElement("div");
    blob2.style.cssText = `position:absolute;width:${w * 1.4}px;height:${w * 1.4}px;right:${-w * 0.5}px;top:${h * 0.35}px;border-radius:50%;opacity:0;background:radial-gradient(circle, rgba(185,134,62,0.9) 0%, rgba(185,134,62,0) 65%);`;
    container.appendChild(wash); container.appendChild(blob1); container.appendChild(blob2);

    const start = cutAt - overlap;
    const peak = start + overlap * 0.42;
    masterTl.fromTo(wash, { opacity: 0 }, { opacity: 0.55, duration: overlap * 0.35, ease: "power1.in" }, start);
    masterTl.to(wash, { opacity: 0, duration: overlap * 0.4, ease: "power2.out" }, peak);
    masterTl.fromTo(blob1, { opacity: 0, x: 0 }, { opacity: 0.9, x: w * 0.18, duration: overlap * 0.33, ease: "sine.in" }, start + overlap * 0.05);
    masterTl.to(blob1, { opacity: 0, x: w * 0.36, duration: overlap * 0.35, ease: "power1.out" }, peak);
    masterTl.fromTo(blob2, { opacity: 0, x: 0 }, { opacity: 0.8, x: -w * 0.16, duration: overlap * 0.35, ease: "sine.in" }, start + overlap * 0.08);
    masterTl.to(blob2, { opacity: 0, x: -w * 0.32, duration: overlap * 0.33, ease: "power1.out" }, peak);

    // The scene swap itself sits at the leak's brightest point, hidden
    // under full opacity — this is the one transition where out/in never
    // actually animate their own transform/opacity; the wash covers it.
    masterTl.set(outEl, { opacity: 0 }, peak - 0.01);
    masterTl.set(inEl, { opacity: 1 }, peak - 0.01);
    masterTl.set(outEl, { opacity: 1 }, start); // resting opacity going in, in case this scene is re-seeked from further back than its own leak
    masterTl.set(inEl, { opacity: 0 }, start);
  }

  const HANDLERS = {
    push: (tl, out, inn, at, ov, w, h) => push(tl, out, inn, at, ov, w, h, false),
    push_up: (tl, out, inn, at, ov, w, h) => push(tl, out, inn, at, ov, w, h, true),
    squeeze: (tl, out, inn, at, ov) => squeeze(tl, out, inn, at, ov),
    zoom: (tl, out, inn, at, ov) => zoom(tl, out, inn, at, ov),
    blur_swoosh: (tl, out, inn, at, ov) => blurSwoosh(tl, out, inn, at, ov),
    light_leak: (tl, out, inn, at, ov, w, h, stageEl) => lightLeak(tl, out, inn, at, ov, stageEl, w, h),
  };

  function registerAll(masterTl, scenes, sceneWindows, width, height, stageEl) {
    for (let i = 1; i < scenes.length; i++) {
      const scene = scenes[i];
      const name = scene.transition_in;
      const overlap = scene._transitionOverlap;
      if (!name || !overlap || !HANDLERS[name]) continue;
      const outEl = sceneWindows[i - 1].el;
      const inEl = sceneWindows[i].el;
      HANDLERS[name](masterTl, outEl, inEl, scene.start, overlap, width, height, stageEl);
    }
  }

  window.MotionTransitions = { registerAll };
})();
