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

import base64
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


def is_available() -> tuple[bool, str]:
    """Check if FFmpeg and a headless browser runner are available."""
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
      1. Evaluated in the browser via `window.runtime.seek(frame)`
      2. Captured as a JPEG screenshot
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

            # Wait for MotionRuntime to initialize
            page.wait_for_function("typeof window.MotionRuntime !== 'undefined'",
                                   timeout=15_000)

            # Boot the runtime with the full spec
            page.evaluate(f"""
                (() => {{
                    const spec = {spec_json};
                    const canvas = document.getElementById('canvas');
                    if (!canvas) {{
                        window.__motionError = 'No canvas element found';
                        return;
                    }}
                    window.runtime = new MotionRuntime(canvas);
                    window.runtime.loadSpec(spec);
                }})();
            """)

            err = page.evaluate("window.__motionError")
            if err:
                raise MotionRenderError(f"Runtime init error: {err}")

            # Seek frame-by-frame and capture JPEG
            for frame_idx in range(total_frames):
                page.evaluate(f"window.runtime.seek({frame_idx})")

                # Capture canvas as JPEG via JS
                jpeg_b64: str = page.evaluate("""
                    (() => {
                        const canvas = document.getElementById('canvas');
                        return canvas.toDataURL('image/jpeg', 0.92).split(',')[1];
                    })()
                """)

                jpeg_bytes = base64.b64decode(jpeg_b64)
                ff_proc.stdin.write(jpeg_bytes)

                if on_progress:
                    on_progress(frame_idx + 1, total_frames)

            browser.close()

        ff_proc.stdin.close()
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
