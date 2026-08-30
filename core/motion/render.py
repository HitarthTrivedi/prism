"""
Prism Motion Graphics Deterministic Frame Renderer
──────────────────────────────────────────────────
Renders Motion Specifications to MP4 video by seeking through every frame
in a headless browser and piping raw JPEG frames into FFmpeg.

Runner priority:
  1. Playwright Chromium (installed via `playwright install chromium`)
  2. Electron (from prism-desktop node_modules or system PATH)
  3. System Chromium / Google Chrome
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Dict, Optional, Union

from .schema import validate_motion_spec
from .resolver import resolve_motion_spec


class MotionRenderError(Exception):
    pass


# ── Runner Discovery ──────────────────────────────────────────────────────────

def _find_electron_binary() -> Optional[str]:
    """Look for Electron binary in node_modules or system PATH."""
    candidates = [
        os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            "../../../../../prism-desktop/node_modules/electron/dist/electron")),
        os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            "../../../../prism-desktop/node_modules/electron/dist/electron")),
        os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            "../../../prism-desktop/node_modules/electron/dist/electron")),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return shutil.which("electron")


def _find_playwright_chromium() -> Optional[str]:
    """Locate the Playwright-managed Chromium binary."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        cache_root = pathlib.Path.home() / ".cache" / "ms-playwright"
        if cache_root.exists():
            patterns = [
                "chromium-*/chrome-linux64/chrome",
                "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                "chromium-*/chrome-linux/chrome",
                "chromium_headless_shell-*/chrome-linux/headless_shell",
            ]
            for pattern in patterns:
                matches = sorted(cache_root.glob(pattern))
                for m in reversed(matches):
                    if m.is_file() and os.access(m, os.X_OK):
                        return str(m)
    except ImportError:
        pass
    return None


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return _find_playwright_chromium() is not None
    except ImportError:
        return False


# Temporary kill-switch, 2026-08-30: the render pipeline has a known bug
# (image nodes resolving to asset paths that don't exist at render time —
# a broken-image icon and a blank frame, baked into the actual output, not
# a log-only glitch) that makes Motion unsafe to show a client today. This
# is the single chokepoint BOTH ways Motion gets reached funnel through —
# main_window._open_motion_dialog() (core_bridge.motion_available()) and
# the AI-router's own pipeline execution (automation._run_motion()) both
# call this function and already degrade gracefully on a (False, message)
# result, exactly like Reel does when FFmpeg is missing. Flip this back to
# False once the asset-path bug is fixed and re-verified with a real
# attached-image render — do not just delete this without confirming that.
_DISABLED_PENDING_ASSET_FIX = True


def is_available() -> tuple[bool, str]:
    """Check if FFmpeg and a headless browser runner are available."""
    if _DISABLED_PENDING_ASSET_FIX:
        return False, ("Motion Graphics isn't available in this release yet "
                       "— it's still being finished.")
    try:
        from core import ffmpeg
        ff = ffmpeg.locate()
        if not ff or not os.path.exists(ff):
            return False, "FFmpeg binary is required to render motion graphics."
    except Exception as e:
        return False, f"FFmpeg error: {e}"

    if _playwright_available():
        return True, ""
    if _find_electron_binary() or shutil.which("node"):
        return True, ""
    return False, (
        "No headless browser available. Install Playwright: "
        "pip install playwright && playwright install chromium"
    )


# ── Playwright Python Renderer ────────────────────────────────────────────────

def _render_via_playwright(
    resolved_spec: Dict[str, Any],
    ff: str,
    output_path: str,
    fps: int,
    total_frames: int,
    crf: int,
    preset: str,
    on_progress: Optional[Callable[[int, int], None]],
) -> str:
    """Render every frame using the Playwright Python API (headless Chromium).

    Each frame is:
      1. Evaluated in the browser via `window.__seek(frame)`
      2. Captured as a JPEG page screenshot
      3. Written to FFmpeg stdin in MJPEG stream format
    """
    from playwright.sync_api import sync_playwright

    runtime_dir = os.path.join(os.path.dirname(__file__), "runtime")
    index_html  = pathlib.Path(runtime_dir) / "index.html"
    if not index_html.exists():
        raise MotionRenderError(f"Runtime index.html not found: {index_html}")

    width  = resolved_spec["project"]["width"]
    height = resolved_spec["project"]["height"]
    spec_json = json.dumps(resolved_spec)

    ffmpeg_cmd = [
        ff, "-y", "-loglevel", "error",
        "-f", "image2pipe",
        "-c:v", "mjpeg",
        "-framerate", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(crf),
        "-preset", preset,
        "-movflags", "+faststart",
        output_path,
    ]

    ff_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-software-rasterizer",
                      "--disable-dev-shm-usage", "--disable-setuid-sandbox"],
            )
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(index_html.as_uri(), wait_until="domcontentloaded")

            # Wait for the runtime's own bootstrap (index.html's inline
            # script) to have run, then hand it the spec through the same
            # window.__loadSpec/__seek/__pendingImages API the Electron
            # path already used — previously this function reconstructed
            # its own separate MotionRuntime instance instead of using it,
            # a second bootstrap path that could silently drift from this
            # one.
            page.wait_for_function("typeof window.__loadSpec === 'function'",
                                   timeout=15_000)
            page.evaluate("spec => window.__loadSpec(spec)", resolved_spec)

            # A web font that never arrives must not stop the render —
            # index.html's Google Fonts link uses display=block, so
            # without this wait, frame 0 (and likely several after it)
            # can screenshot genuinely blank text. Mirrors core.reel_web's
            # own harness.
            try:
                page.wait_for_function(
                    "document.fonts.ready.then(() => true)", timeout=8000)
            except Exception:
                pass

            # Every <img> loads asynchronously — wait for each one to
            # settle (loaded OR errored, a broken asset must not hang
            # forever) before seeking frame 0. Without this, a
            # slow-loading brand asset can still be showing its loading
            # placeholder on frames where it should already be visible.
            try:
                page.wait_for_function(
                    "window.__pendingImages() === 0", timeout=15_000)
            except Exception:
                pass  # best-effort — a genuinely stuck image renders as its placeholder, not a crash

            # Seek frame-by-frame and capture. A full page screenshot, not
            # a canvas readback — the scene graph is real DOM now, there's
            # no single element to read pixels from the way there was
            # with a <canvas>. This also drops the base64 JS<->Python
            # round-trip the old canvas.toDataURL() capture needed.
            for frame_idx in range(total_frames):
                page.evaluate(f"window.__seek({frame_idx})")
                try:
                    ff_proc.stdin.write(page.screenshot(type="jpeg", quality=95))
                except BrokenPipeError:
                    err = ff_proc.stderr.read().decode("utf-8", "ignore")
                    raise MotionRenderError(f"FFmpeg stopped early: {err[:400]}")

                if on_progress:
                    on_progress(frame_idx + 1, total_frames)

            browser.close()

        # communicate() unconditionally flushes self.stdin if it isn't None,
        # even with no input to send — that raises "flush of closed file" on
        # a pipe we already closed ourselves. Clearing the attribute (not
        # just closing the file) is what makes communicate() skip it.
        ff_proc.stdin.close()
        ff_proc.stdin = None
        _, ff_err = ff_proc.communicate()

        if ff_proc.returncode != 0:
            raise MotionRenderError(
                f"FFmpeg encoding failed: {ff_err.decode(errors='replace')}"
            )

    except Exception:
        try:
            ff_proc.stdin.close()
        except Exception:
            pass
        ff_proc.wait()
        raise

    return output_path


# ── Electron Runner (fallback) ────────────────────────────────────────────────

def _render_via_electron(
    resolved_spec: Dict[str, Any],
    electron_bin: str,
    ff: str,
    output_path: str,
    fps: int,
    total_frames: int,
    crf: int,
    preset: str,
    on_progress: Optional[Callable[[int, int], None]],
) -> str:
    runtime_dir   = os.path.join(os.path.dirname(__file__), "runtime")
    runner_script = os.path.join(runtime_dir, "render_runner.js")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        json.dump(resolved_spec, tf, indent=2)
        spec_temp_path = tf.name

    ffmpeg_cmd = [
        ff, "-y",
        "-f", "image2pipe",
        "-c:v", "mjpeg",
        "-framerate", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(crf),
        "-preset", preset,
        "-movflags", "+faststart",
        output_path,
    ]

    runner_cmd = [
        electron_bin,
        runner_script,
        spec_temp_path,
        "--no-sandbox",
        "--disable-gpu",
        "--disable-software-rasterizer",
    ]

    try:
        ff_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        runner_proc = subprocess.Popen(
            runner_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        frames_rendered = 0
        chunk_size = 65536
        jpeg_soi   = b"\xff\xd8"

        while True:
            chunk = runner_proc.stdout.read(chunk_size)
            if not chunk:
                break
            ff_proc.stdin.write(chunk)
            soi_count = chunk.count(jpeg_soi)
            if soi_count > 0:
                frames_rendered += soi_count
                if on_progress:
                    on_progress(min(total_frames, frames_rendered), total_frames)

        runner_proc.stdout.close()
        runner_proc.wait()
        _, ff_err = ff_proc.communicate()

        if ff_proc.returncode != 0:
            raise MotionRenderError(
                f"FFmpeg encoding failed: {ff_err.decode(errors='replace')}"
            )

    finally:
        if os.path.exists(spec_temp_path):
            try:
                os.remove(spec_temp_path)
            except OSError:
                pass

    return output_path


# ── Public API ─────────────────────────────────────────────────────────────────

def render(
    spec: Union[str, Dict[str, Any]],
    output_path: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    fps: Optional[int] = None,
    crf: int = 19,
    preset: str = "medium",
) -> str:
    """Render a Motion Specification to an MP4 video.

    Uses Playwright (preferred) or Electron (fallback) as the headless renderer.
    """
    valid_spec    = validate_motion_spec(spec)
    resolved_spec = resolve_motion_spec(valid_spec)

    p            = resolved_spec["project"]
    width        = p["width"]
    height       = p["height"]
    fps          = fps or p["fps"]
    duration     = p["duration"]
    total_frames = int(round(duration * fps))

    from core import ffmpeg as _ffmpeg
    ff = _ffmpeg.locate()
    if not ff or not os.path.exists(ff):
        raise MotionRenderError("FFmpeg executable not found.")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # ── Playwright path (preferred) ───────────────────────────────────────────
    if _playwright_available():
        result = _render_via_playwright(
            resolved_spec, ff, output_path, fps, total_frames, crf, preset, on_progress
        )
        if on_progress:
            on_progress(total_frames, total_frames)
        return result

    # ── Electron fallback ─────────────────────────────────────────────────────
    electron_bin = _find_electron_binary()
    if electron_bin:
        result = _render_via_electron(
            resolved_spec, electron_bin, ff, output_path,
            fps, total_frames, crf, preset, on_progress
        )
        if on_progress:
            on_progress(total_frames, total_frames)
        return result

    raise MotionRenderError(
        "No headless browser available. "
        "Run: pip install playwright && playwright install chromium"
    )
