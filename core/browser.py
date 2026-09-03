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
    try:
        return pw.chromium.launch(channel="chromium", args=args, **kwargs)
    except Exception:
        return pw.chromium.launch(args=args, **kwargs)
