"""
Prism — the headless browser, found the same way everywhere
───────────────────────────────────────────────────────────
Studio (core.reel_web), the design inspector and Motion (core.motion.render)
all film an HTML page with Playwright's Chromium. Each of them used to call
`p.chromium.launch()` directly, and that is the line that broke every packaged
Windows build:

    Render failed: Executable doesn't exist at …\\_internal\\playwright\\driver\\
    package\\.local-browsers\\chromium_headless_shell-1234\\chrome-headless-shell
    -win64\\chrome-headless-shell.exe

Playwright 1.49 split the headless browser in two. `launch(headless=True)` —
the default — no longer runs the Chromium that `playwright install chromium`
puts on disk; it runs a SEPARATE, smaller binary called chrome-headless-shell.
Its own driver decides this, in registry.getExecutableName():

    return options.headless ? "chromium-headless-shell" : "chromium";

packaging/prism.spec deliberately trims chrome-headless-shell out of the
bundle (~120MB on Windows) on the stated grounds that "nothing in this
codebase calls it — reel_web and motion/render both just do
p.chromium.launch()". That was true of the code and false of Playwright: a
plain launch() is exactly how you ask for the headless shell. So every build
shipped a Chromium it never used and omitted the one binary it did.

Passing `channel="chromium"` is what asks for the full build instead — same
branch in the driver, one line up. It renders identically (it is the
new-headless mode of the real browser rather than the old shell) and it is
the binary the bundle actually contains.

Everything to do with locating and launching that browser lives here now, so
there is one answer to "is a browser available" and one launch path, rather
than four copies that drift.
"""
from __future__ import annotations

import os
import sys

# Resolved once per process: starting Playwright's Node driver just to ask
# where Chromium is costs the better part of a second, and `available()` is
# called on the way into every render and every dialog that offers one.
_UNSET = object()
_cached_path: object = _UNSET

INSTALL_HINT = ("The web renderer needs Playwright:\n"
                "    pip install playwright && playwright install chromium")


def chromium_path() -> str | None:
    """Full path to the Chromium build a launch would use, or None.

    Asks Playwright rather than globbing for it. The old glob (in
    core/motion/render.py) only ever matched Linux layouts —
    `chromium-*/chrome-linux64/chrome` under `~/.cache/ms-playwright` — so on
    Windows and macOS it reported "no browser" with the browser sitting right
    there, and in a frozen build it looked in the OS cache directory while
    the bundled copy lives inside the playwright package
    (PLAYWRIGHT_BROWSERS_PATH=0, set in prism_gui/core_bridge.py).
    `executable_path` handles all of that, and it stays right across a
    Playwright upgrade that moves things.
    """
    global _cached_path
    if _cached_path is not _UNSET:
        return _cached_path  # type: ignore[return-value]
    _cached_path = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        if path and os.path.exists(path):
            _cached_path = path
    except Exception:
        pass
    return _cached_path  # type: ignore[return-value]


def available() -> tuple[bool, str]:
    """(ready, why not) for the browser half of a render.

    Checks that the BINARY is there, not just that `import playwright`
    works. Only checking the import is what let a packaged build answer
    "Studio is ready", accept the job, and then fail in the middle of the
    render with Playwright's own "Executable doesn't exist at …" — the
    error a customer actually reported.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return False, INSTALL_HINT
    if chromium_path():
        return True, ""
    return False, ("Chromium isn't installed for the web renderer:\n"
                   "    playwright install chromium")


def selftest() -> tuple[bool, str]:
    """Actually start the browser and paint a frame. (ready, why not).

    `available()` above only proves a FILE EXISTS at the path Playwright
    would resolve. That is worth checking and it is not the thing that
    broke: the shipped bug was a launch resolving to a DIFFERENT binary
    (chrome-headless-shell) that the bundle did not carry, and a
    file-exists check on Chromium would have passed cheerfully while every
    render died.

    So the build gate does the whole round trip — launch, load a page,
    screenshot, close. It is the smallest thing that exercises the same
    path a real render takes, it costs a second or two in
    packaging/smoke_test.py, and it is the difference between CI saying
    "the browser is in the bundle" and CI saying "a render works on this
    platform".

    --no-sandbox because CI runners and locked-down corporate Windows both
    refuse the sandbox, and this is a liveness check rather than a place to
    be running untrusted pages.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False, INSTALL_HINT
    try:
        with sync_playwright() as pw:
            browser = launch_chromium(pw, args=["--no-sandbox", "--disable-gpu",
                                                "--disable-dev-shm-usage"])
            try:
                page = browser.new_page(viewport={"width": 200, "height": 120})
                page.set_content(
                    "<div style='width:200px;height:120px;background:#123'></div>")
                try:
                    frame = page.screenshot(type="jpeg", quality=60)
                except Exception:
                    # Chromium can acknowledge set_content just before its
                    # first off-screen surface is paintable (seen once during
                    # a clean frozen-build smoke test as
                    # Page.captureScreenshot: Unable to capture screenshot).
                    # A real render owns the page for much longer; give this
                    # intentionally tiny liveness probe one bounded retry.
                    page.wait_for_timeout(100)
                    frame = page.screenshot(type="jpeg", quality=60)
            finally:
                browser.close()
    except Exception as e:
        detail = str(e).strip().splitlines()[0] if str(e).strip() else ""
        return False, detail[:200] or "the browser would not start"
    if not frame:
        return False, "the browser started but produced no frame"
    return True, ""


def launch_chromium(pw, args=None, **kwargs):
    """`pw.chromium.launch()` that runs the browser we actually ship.

    `channel="chromium"` selects the full Chromium build instead of the
    chrome-headless-shell binary a bare headless launch resolves to — see
    the module docstring for why that distinction is the whole point of
    this function.

    Falls back to a plain launch if the channel is refused, so a machine
    that only has the shell (someone who ran `playwright install
    --only-shell`, or a Playwright older than the channel option) still
    renders instead of being told it cannot.
    """
    args = list(args or [])
    kwargs.setdefault("env", _child_env())
    try:
        return pw.chromium.launch(channel="chromium", args=args, **kwargs)
    except Exception:
        return pw.chromium.launch(args=args, **kwargs)


def _child_env() -> dict:
    """The environment Chromium should start in, not the one Prism is in.

    PyInstaller points LD_LIBRARY_PATH at its own bundle so the frozen app
    finds the libraries it ships, and stashes the real one in
    LD_LIBRARY_PATH_ORIG. Every child process inherits that — so a browser
    launched from inside a frozen Prism loads Qt's bundled libstdc++/glib
    instead of the system's, and dies on the spot.

    What that looks like is NOT a missing-library error, which is why it
    took two goes to find: the browser starts, exits immediately, and
    Playwright reports "Target page, context or browser has been closed".
    Adding the system dependencies (playwright install --with-deps) did not
    change it, because they were never missing.

    Only ever a Linux question — Windows and macOS resolve libraries by
    other means and both were green throughout.
    """
    env = dict(os.environ)
    if not sys.platform.startswith("linux"):
        return env
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:            # frozen: put the real one back
        env["LD_LIBRARY_PATH"] = original
    elif getattr(sys, "frozen", False):  # frozen with nothing stashed: drop it
        env.pop("LD_LIBRARY_PATH", None)
    return env
