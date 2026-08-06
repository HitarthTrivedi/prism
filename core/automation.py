"""
Prism — browser automation
───────────────────────────
Drives the user's logged-in Chrome (via undetected-chromedriver) through each
needed pipeline stage: opens the tool, types the prompt(s), waits, scrapes the
response, and passes it forward as context to the next stage.

Ported from the original prism_new.py, generalised to N categories and decoupled
from Google Drive. Selenium/uc are imported lazily so the REPL and dry-runs work
even on machines where they aren't installed yet.
"""
from __future__ import annotations
import os
import time
import shutil
import tempfile
import subprocess
import platform
import webbrowser
from . import agents as A
from . import ui

# Common Chrome binary locations across platforms.
_CHROME_BINARIES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",   # macOS
    "/usr/bin/google-chrome",                                         # Linux
    "/usr/bin/google-chrome-stable",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",         # Windows
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# Windows opens a console window for every subprocess unless told not to, and
# in a frozen GUI build that is a black rectangle flashing over the app. Zero
# on every other platform, where the flag does not exist.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Cap on how much of the previous stage's output is forwarded. The tail is
# kept (not the head) because that's where the HANDOFF summary lives.
_MAX_FORWARD_CHARS = 8000

# The live browser, kept across runs. A slow producer (deck/video/app builder)
# often keeps rendering its result server-side after Prism gives up waiting on
# it, and the user still needs to go look at that tab — so nothing in this
# module ever calls driver.quit(). The same window is reused for the next
# command; a fresh one is only launched if the user has since closed it.
_active_driver = None


def _get_driver(cfg: dict):
    """Return (driver, fresh) — reusing the still-open browser from a
    previous run when possible, so results left on screen aren't disturbed."""
    global _active_driver
    if _active_driver is not None:
        try:
            _active_driver.window_handles  # cheap liveness probe
            return _active_driver, False
        except Exception:
            _active_driver = None
    _active_driver = _setup_chrome_driver(parse_chrome_version(cfg.get("chrome_version")))
    return _active_driver, True


def shutdown() -> None:
    """Close Prism's browser, if one is open.

    Nothing in a normal run calls this — the tabs are left up on purpose, since
    a slow tool often finishes in its tab after Prism stops watching. But when
    the APP itself is quitting there is no one left to read them, and leaving
    the driver running strands a headless Chrome and its profile lock, so the
    next launch fails with "profile appears to be in use".
    """
    global _active_driver
    if _active_driver is None:
        return
    try:
        _active_driver.quit()
    except Exception:
        pass
    finally:
        _active_driver = None


def _bmp_safe(text: str) -> str:
    """ChromeDriver's send_keys only accepts Basic-Multilingual-Plane characters
    (<= U+FFFF). Drop anything above it (emoji, etc.) so typing never crashes
    with 'ChromeDriver only supports characters in the BMP'."""
    return "".join(ch for ch in text if ord(ch) <= 0xFFFF)


def parse_chrome_version(raw) -> int | None:
    """Accept '147', '147.0.7727.139', 147 → 147. Blank/invalid → None."""
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip().split(".")[0])
    except (ValueError, IndexError):
        return None


def _windows_chrome_version() -> int | None:
    """The installed Chrome major version on Windows.

    `chrome.exe --version` cannot be used here. Chrome ships as a GUI-subsystem
    binary on Windows, so it has no console to print to: the command exits
    silently with empty stdout rather than failing, and the caller is left
    parsing an empty string. Ask Windows itself instead — the registry first,
    since Chrome writes its own version to BLBeacon on every update, then the
    executable's file version as a fallback for installs that lack the key.

    Getting None back here is not cosmetic: it disables the pinned-version
    guard in _setup_chrome_driver, which is what lets a stale pin drive the
    wrong chromedriver until Chrome dies a second after launch.
    """
    try:
        out = subprocess.check_output(
            ["reg", "query", r"HKCU\Software\Google\Chrome\BLBeacon",
             "/v", "version"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=_NO_WINDOW)
        # "    version    REG_SZ    147.0.7727.139"
        return int(out.strip().split()[-1].split(".")[0])
    except Exception:
        pass
    for path in _CHROME_BINARIES:
        if not os.path.exists(path):
            continue
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-Item '{path}').VersionInfo.ProductVersion"],
                text=True, stderr=subprocess.DEVNULL, timeout=20,
                creationflags=_NO_WINDOW)
            return int(out.strip().split(".")[0])
        except Exception:
            continue
    return None


def detect_chrome_version() -> int | None:
    """Return the installed Chrome major version, or None if it can't be found."""
    if platform.system() == "Windows":
        return _windows_chrome_version()
    for path in _CHROME_BINARIES:
        if not os.path.exists(path):
            continue
        try:
            out = subprocess.check_output([path, "--version"], text=True)
            # e.g. "Google Chrome 147.0.7727.139"
            return int(out.strip().split()[2].split(".")[0])
        except Exception:
            continue
    return None


# Prism's own browser profile. It lives beside the config (NOT in /tmp, which
# the OS clears on reboot) and it PERSISTS: every login you complete inside the
# automated window — including the ones a tool forces mid-run — is still there
# next time. It used to be wiped and re-cloned on every launch, which meant any
# session Prism itself established was thrown away, and a tool that had logged
# you out once stayed logged out forever.
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".prism", "chrome_profile")

# Caches are re-created on demand and are the bulk of a Chrome profile —
# copying them makes seeding take minutes and adds nothing. Logins live in
# Cookies / Login Data / Local Storage / IndexedDB, which are all kept.
_PROFILE_SKIP = shutil.ignore_patterns(
    "Singleton*", "*.lock", "Cache", "Cache*", "Code Cache", "GPUCache",
    "ShaderCache", "GrShaderCache", "DawnCache", "DawnGraphiteCache",
    "DawnWebGPUCache", "Service Worker", "Application Cache", "Media Cache",
    "component_crx_cache", "extensions_crx_cache", "optimization_guide*",
    "segmentation_platform", "Crashpad", "blob_storage",
)


def user_chrome_dir() -> str:
    """Where the real Chrome keeps its profiles on this OS."""
    system = platform.system()
    if system == "Linux":
        return os.path.expanduser("~/.config/google-chrome")
    if system == "Windows":
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google",
                            "Chrome", "User Data")
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    ui.err("prism yet doesnt support your OS")
    raise RuntimeError(f"Unsupported operating system: {system}")


def profile_is_seeded() -> bool:
    default = os.path.join(PROFILE_DIR, "Default")
    return any(os.path.exists(os.path.join(default, f))
               for f in ("Cookies", "Preferences", "Login Data"))


def seed_profile(force: bool = False) -> bool:
    """Copy the real Chrome profile into Prism's, once. Returns True if it
    copied. Call with force=True to refresh from the real browser — that's the
    fix for 'I logged into the tool in my normal Chrome but Prism still asks'."""
    if profile_is_seeded() and not force:
        return False
    src = user_chrome_dir()
    src_default = os.path.join(src, "Default")
    if not os.path.exists(src_default):
        os.makedirs(os.path.join(PROFILE_DIR, "Default"), exist_ok=True)
        ui.warn("No Chrome profile found to copy — starting a blank one. "
                "Sign in to your tools in the window Prism opens.")
        return False
    # A running Chrome hasn't flushed its newest cookies to disk, so a copy
    # taken now can be missing the login the user just completed.
    if os.path.exists(os.path.join(src, "SingletonLock")):
        ui.warn("Chrome is running — close it for the most reliable copy of "
                "your logins.")
    ui.info("   🧬  copying your Chrome logins into Prism's profile (once)…")
    if force and os.path.exists(PROFILE_DIR):
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    shutil.copytree(src_default, os.path.join(PROFILE_DIR, "Default"),
                    dirs_exist_ok=True, ignore=_PROFILE_SKIP)
    local_state = os.path.join(src, "Local State")
    if os.path.exists(local_state):
        shutil.copy2(local_state, os.path.join(PROFILE_DIR, "Local State"))
    return True


def _clear_profile_locks():
    """A run that was killed (or a crash) leaves SingletonLock behind, and the
    next launch then fails with 'profile appears to be in use'. The profile is
    Prism's alone, so a leftover lock is always stale."""
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = os.path.join(PROFILE_DIR, name)
        try:
            if os.path.islink(path) or os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# Preferences is a settings file. Anything past this is not settings.
_PREFS_SANE = 8_000_000
_PREFS_HOPELESS = 150_000_000
# Keys that grow without bound under automation and hold nothing worth
# keeping. DevTools state is appended to on every CDP session — which is
# every Prism run — and nothing ever prunes it.
_PREFS_JUNK = ("devtools", "media")


def _prune_preferences() -> None:
    """Stop Prism poisoning its own browser profile.

    Chrome parses Preferences at startup and CHECK-fails on a big enough one:
    the browser dies about a second after launch with a SIGTRAP in
    CrBrowserMain and a crash report that says nothing about why. Seen in the
    wild at 2.2 GB, of which 2.06 GB was the 'devtools' key alone, grown a
    little at a time over months of automated runs.

    Deliberately does NOT parse a hopeless file — a 2 GB JSON costs several
    gigabytes of RAM to load, and this runs on the way to launching a browser.
    Past that size the file is quarantined instead; Chrome writes a fresh one,
    and logins are unaffected because they live in Cookies and Login Data,
    not here.
    """
    import json
    path = os.path.join(PROFILE_DIR, "Default", "Preferences")
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < _PREFS_SANE:
        return

    if size > _PREFS_HOPELESS:
        spare = f"{path}.oversized-{int(time.time())}"
        try:
            os.replace(path, spare)
            ui.warn(f"Chrome's settings file had grown to {size/1e6:.0f} MB — "
                    "big enough to crash the browser on launch. Set aside; "
                    "your logins are untouched.")
        except OSError:
            pass
        return

    try:
        with open(path) as f:
            prefs = json.load(f)
    except Exception:
        return
    freed = 0
    for key in _PREFS_JUNK:
        if key in prefs:
            freed += len(json.dumps(prefs[key]))
            del prefs[key]
    if not freed:
        return
    try:
        tmp = path + ".pruned"
        with open(tmp, "w") as f:
            json.dump(prefs, f, separators=(",", ":"))
        os.replace(tmp, path)
        ui.info(f"   🧹  trimmed {freed/1e6:.0f} MB of accumulated DevTools "
                "state from Chrome's settings")
    except OSError:
        pass


def _uc_cache_dir() -> str:
    """Where undetected-chromedriver keeps the driver it patched last time.

    Mirrors Patcher.data_path in undetected_chromedriver/patcher.py. This was
    hardcoded to the macOS path, which silently made the staleness check in
    _setup_chrome_driver a no-op on Windows and Linux — the two platforms where
    it matters most, since a Chrome auto-update there leaves a version-behind
    driver sitting in this directory and uc reuses it forever.
    """
    system = platform.system()
    if system == "Windows":
        d = "~/appdata/roaming/undetected_chromedriver"
    elif system == "Linux":
        d = "~/.local/share/undetected_chromedriver"
    elif system == "Darwin":
        d = "~/Library/Application Support/undetected_chromedriver"
    else:
        d = "~/.undetected_chromedriver"
    return os.path.abspath(os.path.expanduser(d))


def _setup_chrome_driver(version_main=None, reseed: bool = False):
    """Launch undetected-chromedriver against Prism's own persistent profile,
    seeded from the user's real Chrome the first time so their logins carry
    over."""
    import undetected_chromedriver as uc

    seed_profile(force=reseed)
    if not reseed and profile_is_seeded():
        ui.info("   🍪  reusing Prism's browser profile (logins persist "
                "between runs)")
    _clear_profile_locks()
    _prune_preferences()
    tmp = PROFILE_DIR

    opts = uc.ChromeOptions()
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--disable-blink-features=AutomationControlled")

    # Match the driver to Chrome. A pin exists to work around DETECTION
    # failing, not to override what is actually installed — and Chrome
    # auto-updates, so a pin set months ago goes stale silently. Driving
    # Chrome 150 with a 149 driver does not fail politely: Chrome trips an
    # internal check and dies about a second after launch, with a crash
    # report and nothing in Prism's output to explain it.
    detected = detect_chrome_version()
    if version_main and detected and version_main != detected:
        ui.warn(f"your pinned Chrome version is v{version_main} but v{detected} "
                f"is installed — using v{detected}. Run /chrome to update or "
                "clear the pin.")
        version_main = detected
    elif version_main is None:
        version_main = detected
    if version_main:
        ui.info(f"   🌐  targeting Chrome v{version_main}")

    # Drop a cached chromedriver that cannot drive this Chrome: the wrong
    # architecture, or a version behind the browser. undetected-chromedriver
    # reuses whatever it patched last time, which is exactly how a stale one
    # survives a Chrome update.
    uc_cache = _uc_cache_dir()
    if os.path.exists(uc_cache):
        for f in [x for x in os.listdir(uc_cache) if "chromedriver" in x.lower()]:
            fp = os.path.join(uc_cache, f)
            if not os.path.isfile(fp):
                continue
            drop = None
            # Apple Silicon only. uc can end up with an x86 driver under
            # Rosetta there, which is what this catches. It must NOT run
            # elsewhere: on Linux x86 `file` reports "x86-64" for a perfectly
            # good native driver, and this would delete and re-download it on
            # every single launch.
            if platform.system() == "Darwin" and platform.machine() == "arm64":
                try:
                    r = subprocess.run(["file", fp], capture_output=True, text=True)
                    if "x86" in r.stdout and "arm" not in r.stdout.lower():
                        drop = "built for the wrong architecture"
                except Exception:
                    pass
            if drop is None and version_main:
                try:
                    # chromedriver, unlike chrome, is a console binary on
                    # Windows and does print its version.
                    r = subprocess.run([fp, "--version"], capture_output=True,
                                       text=True, timeout=10,
                                       creationflags=_NO_WINDOW)
                    have = int(r.stdout.strip().split()[1].split(".")[0])
                    if have != version_main:
                        drop = f"built for Chrome v{have}, not v{version_main}"
                except Exception:
                    pass
            if drop:
                try:
                    os.remove(fp)
                    ui.info(f"   ♻️   replacing the cached driver — {drop}")
                except OSError:
                    # Windows refuses to unlink a running executable. Nothing
                    # to do but let uc try it and report the real failure.
                    pass

    return uc.Chrome(options=opts, user_data_dir=tmp, version_main=version_main)


def _needed_stages(routing: dict, agents: dict):
    """Yield (stage, agent_name, questions) for every stage that should run."""
    for stage in A.PIPELINE_ORDER:
        data = routing.get(stage)
        if not data or not data.get("needed", False):
            continue
        questions = [q for q in data.get("questions", []) if q and q.strip()]
        if not questions:
            continue
        if stage == "summary":
            name = A.summary_agent_name(agents)
        else:
            name = agents.get(stage)
        if not name:
            continue
        yield stage, name, questions


def _upload_files(driver, agent_cfg, attachments):
    """Push any attached files into the tool's <input type='file'>, if present."""
    if not attachments:
        return
    from selenium.webdriver.common.by import By
    from . import files as F

    sel = agent_cfg.get("upload_selector", "input[type='file']")
    inputs = driver.find_elements(By.CSS_SELECTOR, sel)
    if not inputs:
        return
    paths = F.upload_paths(attachments)
    target = inputs[0]
    uploaded = 0
    try:
        # Most multi-file inputs accept newline-separated paths in one send_keys.
        target.send_keys("\n".join(paths))
        uploaded = len(paths)
        ui.info(f"   📎  uploaded {uploaded} file(s)")
    except Exception:
        # Fall back to one-at-a-time (input may be replaced between sends).
        for p in paths:
            try:
                for inp in driver.find_elements(By.CSS_SELECTOR, sel):
                    inp.send_keys(p)
                    uploaded += 1
                    break
            except Exception:
                pass
        if uploaded:
            ui.info(f"   📎  uploaded {uploaded} file(s)")
    if not uploaded:
        return   # nothing reached the page — no ingest to wait for
    # Big files / multiple files take a while to ingest — submitting before the
    # upload finishes silently drops the attachment. Wait a size-scaled floor,
    # then keep waiting while the page still shows an upload spinner/progress
    # bar, up to a size-scaled cap.
    total_mb = sum(a.get("size", 0) for a in attachments) / 1e6
    floor = min(15 + int(total_mb * 4), 120)          # 6.5 MB → ~41s
    cap = max(45, min(300, 30 + int(total_mb * 20)))  # 6.5 MB → 160s
    start = time.time()
    time.sleep(min(floor, cap))
    while time.time() - start < cap:
        try:
            busy = driver.execute_script(
                """
                const sels = "[role='progressbar'], progress, .animate-spin, [aria-busy='true']";
                return Array.from(document.querySelectorAll(sels))
                            .some(el => el.offsetParent !== null);
                """)
        except Exception:
            busy = False
        if not busy:
            break
        time.sleep(2)
    ui.info(f"   📎  upload settled after {int(time.time() - start)}s")


def _fast_type(driver, element, text: str) -> bool:
    """Insert the whole prompt at once via JavaScript instead of per-character
    send_keys (which crawls at ~20 chars/sec over the WebDriver wire — minutes
    for a long context). Handles <textarea>/<input> through the native value
    setter (so React/Vue notice the change) and contenteditable editors through
    execCommand('insertText'), which fires real input events.
    Returns False if the text didn't land, so the caller can fall back."""
    try:
        element.click()
    except Exception:
        pass
    try:
        return bool(driver.execute_script(
            """
            const el = arguments[0], text = arguments[1];
            el.focus();
            if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                const proto = el.tagName === 'TEXTAREA'
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
                el.dispatchEvent(new Event('input',  {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return el.value === text;
            }
            document.execCommand('selectAll', false, null);
            const ok = document.execCommand('insertText', false, text);
            return ok && (el.innerText || el.textContent || '').trim().length > 0;
            """,
            element, text,
        ))
    except Exception:
        return False


def _harvest_images(driver, agent_cfg, stage: str) -> list[dict]:
    """A generated image can't travel in a text handoff. Pull every real image
    out of the response area — fetched through the page's own session so
    auth-gated CDN links work — and return attachment records that later
    stages can re-upload. Falls back to screenshotting the rendered element."""
    import base64
    from selenium.webdriver.common.by import By

    # Inside the reply first — that is where a generated image usually sits and
    # the ordering there is the order it was asked for. But ChatGPT's image UI
    # opens a canvas pane BESIDE the conversation, putting the pictures outside
    # the message element entirely, so fall back to the whole page rather than
    # reporting that a stage produced nothing while three images are on screen.
    sel = agent_cfg.get("response_selector", "")
    imgs = []
    try:
        if sel:
            imgs = driver.find_elements(By.CSS_SELECTOR, f"{sel} img")
        if not imgs:
            imgs = driver.find_elements(By.CSS_SELECTOR, "img")
    except Exception:
        return []

    out, seen = [], set()
    try:
        driver.set_script_timeout(20)
    except Exception:
        pass
    for img in imgs:
        try:
            src = img.get_attribute("src") or ""
            w = driver.execute_script("return arguments[0].naturalWidth || 0", img)
            h = driver.execute_script("return arguments[0].naturalHeight || 0", img)
        except Exception:
            continue
        # Icons, avatars and citation thumbnails are small — real generated
        # images aren't.
        if not src or src in seen or w < 256 or h < 256:
            continue
        seen.add(src)
        raw, mime = None, "image/png"
        try:
            data = driver.execute_async_script(
                """
                const src = arguments[0], done = arguments[arguments.length - 1];
                fetch(src, {credentials: 'include'})
                    .then(r => r.blob())
                    .then(b => { const fr = new FileReader();
                                 fr.onloadend = () => done(fr.result);
                                 fr.readAsDataURL(b); })
                    .catch(() => done(null));
                """, src)
            if data and data.startswith("data:"):
                header, b64 = data.split(",", 1)
                raw = base64.b64decode(b64)
                mime = header[5:].split(";")[0] or "image/png"
        except Exception:
            raw = None
        if not raw:
            try:
                raw = img.screenshot_as_png   # rendered pixels — always works
            except Exception:
                continue
        if not mime.startswith("image/"):
            continue
        ext = {"image/png": ".png", "image/jpeg": ".jpg",
               "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".png")
        path = os.path.join(tempfile.gettempdir(),
                            f"prism_{stage}_img{len(out) + 1}{ext}")
        with open(path, "wb") as f:
            f.write(raw)
        out.append({"path": path, "name": os.path.basename(path), "size": len(raw),
                    "mime": mime, "kind": "image", "text": None,
                    "truncated": False,
                    # Marked so a later stage can tell a picture a model drew
                    # from a file the client actually owns — they are not
                    # interchangeable when one of them is a logo.
                    "_generated": True})
        if len(out) >= 4:
            break
    return out


# Phrases that only ever appear in what Prism types, never in a tool's answer.
# Every stage prompt ends with the pipeline rules, and /email's draft prompt
# carries the SUBJECT/BODY template — an element containing either is the user
# turn, not the reply.
_PROMPT_ECHO_MARKERS = (
    "strict pipeline rules:",
    "your only task is:",
    "reply with nothing except",
    "reply with only a json object describing the reel",
    "<one subject line>",
    "<the full email body>",
    "your output will be passed directly to",
    "context from the previous pipeline stage",
)


def _is_prompt_echo(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _PROMPT_ECHO_MARKERS)


def _safe_url(driver, exclude=()) -> str:
    """The current tab's URL, for the paths where the stage blew up. Never
    raises (the session itself may be what died) and never returns a link that
    isn't this stage's: a blank tab, or a page already credited to an earlier
    stage, means we failed before we ever got to the tool."""
    try:
        url = (driver.current_url or "").strip()
    except Exception:
        return ""
    if not url or url.startswith(("about:", "data:", "chrome:")):
        return ""
    return "" if url in exclude else url


def _sleep_interruptibly(seconds: float, should_stop=None) -> bool:
    """time.sleep, but it notices a cancel. Returns True if we were stopped.

    The waits in here are long by design (a tool can take five minutes), so a
    plain sleep makes a Stop button feel broken — the user presses it and
    nothing happens until the current poll interval ends. Sleeping in short
    slices keeps cancellation under a second without polling the page harder.
    """
    if should_stop is None:
        time.sleep(seconds)
        return False
    deadline = time.time() + seconds
    while time.time() < deadline:
        if should_stop():
            return True
        time.sleep(min(0.25, max(0.0, deadline - time.time())))
    return should_stop()


def _smart_wait(driver, agent_cfg, cap: int, poll: int = 5,
                stable_for: int = 25, min_wait: int = 35,
                expect: str = "", should_stop=None) -> tuple[int, bool]:
    """Wait for the agent to finish generating — but no longer than needed.
    Polls the response selector and returns once the total response text has
    stopped growing for `stable_for` seconds (after having grown at least
    once). `cap` is the hard maximum (the old fixed sleep), so a selector
    that never matches degrades to the previous behaviour, not a hang.

    Returns (seconds_waited, settled). settled is False when the cap ran out
    with the answer still growing — the tool has NOT failed, we just stopped
    watching, and it will keep working in its tab. Callers use this to say so
    and to hand the user the link instead of claiming the scrape missed.

    `expect` is a marker the finished answer must contain (e.g. "SUBJECT:" for
    an email draft). Tools routinely pause mid-answer — thinking, rendering a
    tool call, streaming in bursts — and a pause longer than `stable_for` reads
    exactly like being finished. When the marker is set, a lull that doesn't
    contain it is treated as the tool still working."""
    from selenium.webdriver.common.by import By
    sel = agent_cfg.get("response_selector", "")
    start = time.time()
    baseline = last_len = None
    last_change = start
    grown = False
    settled = False

    def has_marker() -> bool:
        if not expect:
            return True
        try:
            return any(expect.lower() in (el.text or "").lower()
                       for el in driver.find_elements(By.CSS_SELECTOR, sel))
        except Exception:
            return False

    while time.time() - start < cap:
        # Stopping here returns settled=False, which the caller already treats
        # as "we stopped watching, the tool didn't fail" — exactly the truth
        # after a cancel. It re-checks should_stop before scraping.
        if _sleep_interruptibly(poll, should_stop):
            break
        try:
            total = sum(len(el.text) for el in
                        driver.find_elements(By.CSS_SELECTOR, sel))
        except Exception:
            continue
        if baseline is None:
            # First reading — whatever is already on the page (our own typed
            # prompt, old chat turns) doesn't count as generation.
            baseline = last_len = total
            continue
        if total != last_len:
            grown = grown or total > baseline
            last_len = total
            last_change = time.time()
        elif (grown and time.time() - start >= min_wait
              and time.time() - last_change >= stable_for
              and has_marker()):
            settled = True
            break
    return int(time.time() - start), settled


def _wait_for_images(driver, agent_cfg, want: int, cap: int = 240) -> int:
    """Wait for generated images to actually appear, then stop growing.

    _smart_wait watches TEXT, and during image generation the text is finished
    long before the pictures are — the model says "here are three images" and
    then renders for another minute. Waiting on text alone scrapes the page
    while every canvas is still empty, which looks exactly like a stage that
    produced nothing.
    """
    sel = agent_cfg.get("response_selector", "")
    # The WHOLE page, not just the reply element. ChatGPT's image UI opens a
    # canvas pane beside the conversation, so the pictures live outside
    # [data-message-author-role='assistant'] — the harvester was looking in
    # the one place they are not, and reported that none had appeared while
    # three sat on screen.
    js = """
        let n = 0;
        for (const img of document.querySelectorAll('img')) {
          if ((img.naturalWidth || 0) >= 256 && (img.naturalHeight || 0) >= 256)
            n++;
        }
        return n;
    """
    start, last, steady = time.time(), 0, 0
    while time.time() - start < cap:
        time.sleep(4)
        try:
            n = int(driver.execute_script(js, sel) or 0)
        except Exception:
            continue
        if n > last:
            last, steady = n, 0
            ui.info(f"   🖼️   {n} image(s) so far…")
            if n >= want:
                steady = 0
        elif last:
            steady += 4
            # Two images that have been sitting there for 20s are all there is.
            if steady >= 20:
                break
    return last


def _click_by_text(driver, texts: list[str], timeout: int = 10) -> bool:
    """Best-effort: click the first visible, clickable element whose text
    matches one of `texts` (case-insensitive, substring). NotebookLM's UI
    doesn't expose stable ids/classes the way ChatGPT/Claude do, so matching
    on visible button/label TEXT is the more durable anchor here. Returns
    False (never raises) if nothing matched within `timeout`."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    parts = []
    for t in texts:
        tl = t.lower()
        parts.append(
            "//*[self::button or self::a or self::span or self::div or self::li]"
            "[contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
            f"'{tl}')]"
        )
    xpath = " | ".join(parts)
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath)))
        el.click()
        return True
    except Exception:
        return False


def _run_notebooklm(driver, agent_cfg: dict, stage: str, prompt: str) -> list[str]:
    """NotebookLM is not a chat box — it's a 'sources' notebook. This drives
    its multi-step UI as best-effort automation:
      1. start a fresh notebook (so this run's source doesn't mix with old ones)
      2. add a "Copied text" source and paste the engineered prompt/context —
         NotebookLM's only free-text input surface
      3. wait for that source to finish processing
      4. MEDIA stage → open Studio and trigger the Video Overview generator
         (a real, multi-minute async render — this only REQUESTS it and
         returns; the finished video appears in the notebook afterwards)
         any other stage → ask the actual question in NotebookLM's chat and
         scrape its answer

    UNVERIFIED against a live session — Google's Material UI class names
    churn often and this environment has no live browser to test against, so
    every step is wrapped to fail soft with a clear message instead of
    hanging or crashing the whole pipeline run. Expect to need real-world
    iteration on the exact button/label text if Google changes the UI."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    try:
        # 1) Fresh notebook.
        _click_by_text(driver, ["create new", "new notebook", "+ new"], timeout=15)
        time.sleep(3)

        # 2) Add a "Copied text" source with the engineered prompt as its content.
        if not _click_by_text(driver, ["copied text", "paste text"], timeout=15):
            return ["NotebookLM: couldn't find the 'Add source → Copied text' "
                    "option — the UI may have changed. Check the open tab; the "
                    "notebook may still be usable manually from here."]
        time.sleep(1)
        try:
            box = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "textarea")))
        except Exception:
            return ["NotebookLM: the paste-text box never appeared — check "
                    "the open tab and add the source manually if needed."]
        if not _fast_type(driver, box, prompt):
            box.send_keys(prompt)
        time.sleep(1)
        _click_by_text(driver, ["insert", "add source", "add"], timeout=10)

        # 3) Wait for the source to finish processing (spinner-based, capped).
        start = time.time()
        while time.time() - start < 90:
            try:
                busy = driver.execute_script(
                    "return !!document.querySelector("
                    "\"[role='progressbar'], .animate-spin, [aria-busy='true']\");")
            except Exception:
                busy = False
            if not busy:
                break
            time.sleep(3)

        if stage == "media":
            # 4a) Request the Video Overview — this is a long async render;
            # we trigger it and move on rather than blocking the whole
            # pipeline for the many minutes it can take.
            _click_by_text(driver, ["studio"], timeout=10)
            time.sleep(1)
            got = _click_by_text(
                driver, ["video overview", "generate video overview"], timeout=10)
            if not got:
                return ["NotebookLM: the source was added, but the Studio → "
                        "Video Overview button couldn't be found automatically "
                        "— open the tab and click Generate manually."]
            return ["NotebookLM Video Overview requested. Generation takes "
                    "several minutes — check the notebook tab afterwards for "
                    "the finished video."]

        # 4b) Any other stage: ask the actual question in NotebookLM's chat.
        try:
            chat = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "textarea, div[contenteditable='true']")))
        except Exception:
            return ["NotebookLM: the source was added, but no chat box was "
                    "found to ask the question — check the open tab."]
        if not _fast_type(driver, chat, prompt):
            chat.send_keys(prompt)
        chat.send_keys(Keys.ENTER)
        time.sleep(agent_cfg.get("wait_time", 45))
        texts = [e.text.strip() for e in driver.find_elements(
            By.CSS_SELECTOR, ".prose, .markdown, [role='article']") if e.text.strip()]
        texts = [t for t in texts if len(t) > 50]
        return texts or ["NotebookLM answered, but no response text could be "
                          "scraped automatically — check the open tab."]
    except Exception as e:
        return [f"NotebookLM automation stopped early at an unverified UI "
                f"step ({e}). Check the open tab — your source/prompt may "
                f"still be usable manually from here."]


# Words that only appear on a page asking you to identify yourself. Matched
# against the visible body text, lowercased. Kept deliberately specific: "sign
# in" alone also appears in the header of a page you ARE signed into, so every
# entry here has to be something a signed-in user would not be looking at.
_SIGNIN_MARKERS = (
    "sign in to continue", "log in to continue", "please sign in",
    "please log in", "sign in to your account", "create an account to",
    "you must be logged in", "session expired", "your session has expired",
    "continue with google", "sign in with google", "verify you are human",
    "are you a robot", "checking your browser",
)


def _looks_signed_out(driver) -> str:
    """"" if the page looks usable, else what the user has to do about it.

    Not being signed in is the single most common reason a run comes back with
    nothing, and it was indistinguishable from every other empty result: the
    scrape found no reply, so the user was told "Prism couldn't read the
    response off the page" — which reads as Prism being broken rather than as
    "log in to Perplexity". Cheap to check and only consulted when a stage
    produced nothing, so a false positive costs a wrong sentence, not a run.
    """
    try:
        body = (driver.find_element("tag name", "body").text or "").lower()
    except Exception:
        return ""
    if not body or len(body) > 4000:
        # A real conversation page is long; a sign-in wall is short. This keeps
        # the check off pages that merely mention signing in somewhere.
        return ""
    for marker in _SIGNIN_MARKERS:
        if marker in body:
            if "robot" in marker or "human" in marker or "browser" in marker:
                return ("This tool is showing a human-verification check. Open "
                        "the tab, clear it once, and run again — Prism reuses "
                        "the same browser profile, so it usually only asks once.")
            return ("You're not signed in to this tool. Use “Login tabs” in the "
                    "sidebar, sign in once in the window Prism opens, then run "
                    "again — the login is remembered from then on.")
    return ""


def _capture(driver, agent_cfg: dict) -> list[str]:
    """Everything on the page that reads as a reply, longest captures only."""
    from selenium.webdriver.common.by import By
    try:
        elements = driver.find_elements(
            By.CSS_SELECTOR, agent_cfg.get("response_selector", ""))
    except Exception:
        return []
    texts = []
    for el in elements:
        try:
            t = el.text.strip()
        except Exception:
            continue
        if len(t) > 50 and t not in texts:
            texts.append(t)
    # Response selectors often match a container AND pieces inside it
    # (sections, citation chips…). Keep only the fullest captures: drop any
    # text that is contained inside another element's text.
    texts = [t for t in texts if not any(t != u and t in u for u in texts)]
    # Several tools render OUR message with the same classes as the reply, so
    # the prompt comes back as a "response" — which then gets forwarded
    # downstream, or (for /email) parsed as a draft whose subject is the
    # template we typed.
    echoes = [t for t in texts if _is_prompt_echo(t)]
    if echoes:
        texts = [t for t in texts if t not in echoes]
        ui.info(f"   ↩️   ignored {len(echoes)} echo(es) of our own prompt")
    return texts


def _reask(driver, agent_cfg: dict, prompt: str, expect: str = "") -> list[str]:
    """Send one follow-up in the SAME tab and re-scrape.

    Used when a stage answered but not in the shape the next stage needs. The
    chat still holds everything it just wrote, so a one-line correction is far
    cheaper — and far likelier to work — than failing the run or starting the
    stage over."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    try:
        box = WebDriverWait(driver, agent_cfg.get("input_wait", 15)).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, agent_cfg["textarea_selector"])))
        if not _fast_type(driver, box, _bmp_safe(prompt)):
            box.send_keys(_bmp_safe(prompt).replace("\n", " "))
        time.sleep(1)
        sel = agent_cfg.get("submit_selector", "")
        clicked = False
        if sel:
            try:
                WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))).click()
                clicked = True
            except Exception:
                pass
        if not clicked:
            box.send_keys(Keys.ENTER)
        _smart_wait(driver, agent_cfg, agent_cfg.get("wait_time", 60), expect=expect)
        return _capture(driver, agent_cfg)
    except Exception as e:
        ui.err(f"   follow-up failed: {e}")
        return []


def _web_token() -> str:
    from . import reel_web
    return reel_web.ASSET_TOKEN


def _run_studio(prior_text, attachments, cfg: dict, brand: dict | None = None):
    """Film the page the art-direction stage wrote.

    The design is not trusted, it is measured: the page is laid out in the
    browser and every piece of text checked for being inside the frame and
    big enough to read, before a single frame is encoded. A design that fails
    is reported with the exact strings that are wrong.
    """
    try:
        from . import reel_web as web
    except Exception as e:
        return "", f"The web renderer isn't available ({e})."
    ok, why = web.available()
    if not ok:
        return "", why

    sources = [prior_text] if isinstance(prior_text, str) else list(prior_text)
    spec, why_bad = None, None
    for text in sources:
        try:
            spec = web.parse_spec(text)
            break
        except Exception as e:
            why_bad = why_bad or e
    if spec is None:
        return "", (f"{why_bad or 'Nothing was written for the renderer.'} "
                    "The art-direction stage has to return the design JSON.")

    imgs = [a["path"] for a in (attachments or [])
            if a.get("path", "").lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".bmp"))]
    # Whatever the design was actually shown: measured off an attachment, or
    # read off the client's website by the research stage. The design's own
    # 'brand' key is not trusted over either — it is the one thing it does not
    # get to choose.
    if brand:
        spec["brand"] = dict(brand)
    elif imgs and not spec.get("brand"):
        from . import reel as _pillow
        sampled = _pillow.sample_brand(imgs)
        if sampled:
            spec["brand"] = sampled

    # Rebuilt, not passed along: collect() names assets from the files in a
    # fixed order, so the table the design stage was shown and the table the
    # renderer resolves are the same one without either holding a reference.
    if attachments:
        try:
            from . import assets as _assets
            made = {a["path"] for a in attachments if a.get("_generated")}
            spec["_assets"] = {
                k: {kk: vv for kk, vv in v.items() if kk != "ink"}
                for k, v in _assets.collect(attachments,
                                            generated=made).items()}
        except Exception as e:
            ui.warn(f"   couldn't prepare the artwork ({e})")

    import json as _json
    import time as _time
    from . import config as C
    os.makedirs(C.RUNS_DIR, exist_ok=True)
    stamp = int(_time.time())
    out = os.path.join(C.RUNS_DIR, f"reel_{stamp}.mp4")
    _json.dump(spec, open(os.path.join(C.RUNS_DIR, f"reel_{stamp}.json"), "w"),
               indent=2)

    name = (spec.get("design") or {}).get("name", "")
    if name:
        ui.info(f"   🎨  design: {name}")
    secs = sum(float(sc.get("seconds", 4) or 4) for sc in spec["scenes"])
    ui.info(f"   🎬  filming {len(spec['scenes'])} scenes, ~{secs:.0f}s, "
            "1080x1920 — in a browser, locally")
    try:
        web.render(spec, out)
    except Exception as e:
        return "", f"Render failed: {e}"
    for fault in (spec.get("_faults") or [])[:5]:
        ui.warn(f"   layout: {fault}")
    return out, f"reel filmed — {os.path.basename(out)}"


def _run_local(kind: str, prior_text, attachments, cfg: dict, stage: str,
               brand: dict | None = None):
    """Execute an agent that lives in Prism rather than in a browser.

    prior_text is either a single string or the earlier stages' outputs,
    newest first — each is tried in turn so one prose-heavy stage can't hide
    the spec written by another.

    Returns (output_path, message). On failure the path is empty and the
    message explains what went wrong — a local stage must degrade the same
    way a scraped one does, never take the run down with it.
    """
    if kind == "reel_web":
        return _run_studio(prior_text, attachments, cfg, brand)
    if kind != "reel":
        return "", f"Unknown local agent {kind!r}."
    try:
        from . import reel
    except Exception as e:
        return "", f"The reel renderer isn't available ({e})."
    try:
        reel.ffmpeg_path()
    except Exception as e:
        return "", str(e)

    sources = [prior_text] if isinstance(prior_text, str) else list(prior_text)
    spec, why = None, None
    for text in sources:
        try:
            spec = reel.parse_spec(text)
            break
        except Exception as e:
            why = why or e
    if spec is None:
        # The writing stage produced prose instead of a scene spec. Say so
        # plainly — the fix is a routing one, not something to paper over.
        return "", (f"{why or 'Nothing was written for the renderer.'} The "
                    "stage before this one has to return the JSON scene spec "
                    "for the renderer to draw.")

    # Brand colour comes from the client's own artwork when they attached
    # any — measured, not described.
    imgs = [a["path"] for a in (attachments or [])
            if a.get("path", "").lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".bmp"))]
    if imgs and not spec.get("brand"):
        brand = reel.sample_brand(imgs)
        if brand:
            spec["brand"] = brand

    import json as _json
    import time as _time
    from . import config as C
    os.makedirs(C.RUNS_DIR, exist_ok=True)
    stamp = int(_time.time())
    out = os.path.join(C.RUNS_DIR, f"reel_{stamp}.mp4")
    _json.dump(spec, open(os.path.join(C.RUNS_DIR, f"reel_{stamp}.json"), "w"),
               indent=2)
    secs = sum(float(sc.get("seconds", 4)) for sc in spec["scenes"])
    ui.info(f"   🎬  drawing {len(spec['scenes'])} scenes, {secs:.0f}s, 1080x1920 "
            "— locally, no browser")
    if spec.get("_dropped"):
        ui.warn(f"   skipped {len(spec['_dropped'])} scene(s) this renderer "
                f"can't draw: {', '.join(spec['_dropped'][:4])}")
    try:
        reel.render(spec, out)
    except Exception as e:
        return "", f"Render failed: {e}"
    return out, f"reel rendered — {os.path.basename(out)}"


def run(routing: dict, cfg: dict, attachments=None, on_event=None,
        query: str = "", chatgpt_analysis: bool = True,
        custom_stages: list[tuple[str, str, list[str]]] | None = None,
        should_stop=None):
    """Execute the pipeline. Returns (responses, links).

    attachments: list of records from core.files.attach() — uploaded to each
                 tool and their extracted text prepended to the first prompt.
    query: the user's original task — gives the file-analysis stage its focus.
    chatgpt_analysis: when attachments exist, prepend a ChatGPT stage that
                 analyses the files first (skipped if the pipeline already
                 starts with ChatGPT, or when the caller routes its own
                 analysis, e.g. /email).
    custom_stages: an explicit, pre-built (stage_label, agent_name, questions)
                 list, used instead of deriving stages from `routing`. Unlike
                 `routing` — one agent per fixed PIPELINE_ORDER category — this
                 can name any agent any number of times in any order (e.g. a
                 research pass, a structuring pass, back to the same agent
                 again). Stage labels only need to be unique WITHIN this list
                 (they key the responses/links dicts and drive the next-stage
                 handoff); they don't need to match a real category name.
    on_event(kind, payload) is an optional callback for live UI updates.
    should_stop() is an optional predicate polled between and during stages.
                 A routed run drives a browser for minutes at a time, so a
                 caller with a Stop button needs a way to be let go of that
                 isn't killing the process. When it returns True the run
                 finishes the current poll, emits "cancelled", and RETURNS what
                 has already landed rather than raising — a cancelled run still
                 has real output in it, and throwing that away punishes the
                 user for stopping. The browser is deliberately left open, same
                 as every other exit from here.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from . import files as F

    attachments = attachments or []
    attach_ctx = F.context_block(attachments)

    def emit(kind, payload):
        if on_event:
            on_event(kind, payload)

    agents = {k: v for k, v in (cfg.get("agents") or {}).items() if v}
    stages = list(custom_stages) if custom_stages is not None else list(_needed_stages(routing, agents))
    if not stages:
        ui.warn("Router marked every stage as not-needed — nothing to run.")
        return {}, {}

    # ChatGPT is Prism's dedicated file analyst: whenever attachments ride
    # along, it reads them FIRST and hands a precise brief to the pipeline —
    # its file/vision handling is the most reliable of the web tools. If this
    # stage fails, `prior` stays empty and the next stage gets the raw files
    # re-supplied, so the run degrades gracefully to the old behaviour.
    if attachments and chatgpt_analysis and stages[0][1] != "ChatGPT":
        names = ", ".join(a["name"] for a in attachments)
        goal = f" for this task: {query}" if query.strip() else " for the user's task"
        q = (f"Your ONLY task is: analyse the attached file(s) ({names}) thoroughly — "
             "their content, structure, key facts, numbers, data and style — and "
             "produce a short, precise brief of everything the next AI needs to "
             f"use these files{goal}. Do NOT perform the task itself.")
        stages.insert(0, ("analysis", "ChatGPT", [q]))
        ui.info("📎  attachments present — ChatGPT will analyse the files first")

    # A local renderer draws whatever the stage before it hands over, so that
    # stage has to hand over a scene spec rather than prose. Rather than
    # making the router understand renderers, the requirement is appended to
    # the last writing stage here — the only place that knows both which
    # agent got picked for video AND what runs before it.
    # Prism Studio designs the reel instead of filling in a template, so it
    # needs TWO writing passes before it: one for the words, one for the art
    # direction. Splitting them is the point — a single reply that has to do
    # both produces a design that describes itself instead of one that exists.
    studio_at = next((i for i, (_, an, _) in enumerate(stages)
                      if (A.resolve_agent("", an) or {}).get("local") == "reel_web"),
                     None)
    design_feeder = None
    script_stage = ""
    research_stage = ""
    studio_brand: dict = {}
    design_assets: dict = {}
    # Stages whose reply is READ BY A PROGRAM, not handed to another chat.
    # The pipeline's normal rules demand a prose "HANDOFF FOR <next agent>"
    # section as the last thing in the answer, which flatly contradicts "reply
    # with only a JSON object" — and a model that spots the contradiction
    # refuses outright rather than picking one. Each entry here replaces those
    # rules for that stage.
    machine_stages: dict[int, str] = {}
    if studio_at is not None:
        writer = next((i for i in range(studio_at - 1, -1, -1)
                       if not (A.resolve_agent("", stages[i][1]) or {}).get("local")),
                      None)
        if writer is None:
            ui.warn("Prism Studio has no writing stage before it — turn on "
                    "content or brains so something can write the reel.")
        else:
            from . import reel_web as _web
            from . import reel as _pillow
            st, an, qs = stages[writer]
            stages[writer] = (st, an, qs[:-1] + [
                qs[-1] + "\n\n" + _web.script_instructions()])
            brand, asset_list = {}, ""
            if attachments:
                imgs = [a["path"] for a in attachments
                        if a.get("path", "").lower().endswith(
                            (".png", ".jpg", ".jpeg", ".webp", ".bmp"))]
                if imgs:
                    brand = _pillow.sample_brand(imgs) or {}
                    if brand:
                        ui.info(f"🎨  brand colours read from the artwork — "
                                f"accent {brand.get('accent')}, "
                                f"deep {brand.get('deep')}")
                    # Cut the client's own marks out of what they sent, so the
                    # art director can place the REAL logo rather than ask a
                    # model to draw one that looks nearly like it.
                    try:
                        from . import assets as _assets
                        table = _assets.collect(attachments)
                        asset_list = _assets.manifest(table)
                        if table:
                            ui.info(f"✂️   {len(table)} asset(s) prepared from "
                                    "the artwork: " + ", ".join(table))
                    except Exception as e:
                        ui.warn(f"couldn't prepare the artwork ({e}) — the "
                                "reel will be type and colour only")
            # No logo attached means no measured palette, and a reel in a
            # colour the client does not own is not their reel. The research
            # stage is already on their website — asking for the hex codes
            # while it is there costs nothing, where having Prism open the
            # site again to sample it would cost a whole extra page load.
            if not brand:
                # `ragent`, not `an`: reusing the writer's name here rebound it
                # and the plan banner then credited the script to whichever
                # tool happened to do the research.
                for i, (st, ragent, qs) in enumerate(stages):
                    if st == "research" and i < studio_at:
                        stages[i] = (st, ragent,
                                     qs[:-1] + [qs[-1] + _web.research_addendum()])
                        research_stage = st
                        ui.info(f"🎨  {ragent} will read the brand colours off "
                                "their website")
                        break

            # Most jobs arrive with NOTHING attached — a company name and a
            # sentence. So the reel's pictures are made here: the tool that
            # can search the web and draw looks the company up and produces a
            # few images, which are harvested off the page like any other
            # generated asset. Skipped only if the user turned it off.
            maker = agents.get("visual") or "ChatGPT"
            if cfg.get("reel_imagery", True) and A.resolve_agent("visual", maker):
                stages.insert(studio_at, ("artwork", maker, [
                    _web.imagery_instructions(query, bool(asset_list))]))
                machine_stages[studio_at] = (
                    "\n\nSTRICT PIPELINE RULES:\n"
                    "The images themselves are collected from this page "
                    "automatically — they ARE the deliverable. Produce them, "
                    "then write one short line per image saying what it is. "
                    "Do NOT add a handoff section, a summary, an explanation "
                    "or a follow-up question; nothing but the images and "
                    "those lines is read.")
                studio_at += 1
                ui.info(f"🖼️   {maker} will search the web and make up to "
                        f"{_web.MAX_GENERATED} images for it")

            # The art director is the same tool as the writer unless a
            # stronger one is switched on: this pass is the harder of the two.
            director = agents.get("brains") or agents.get("content") or an
            # The asset list cannot be known yet — the imagery stage has not
            # run. A token stands in and is substituted the moment before this
            # prompt is typed.
            studio_brand = dict(brand or {})
            stages.insert(studio_at, ("design", director,
                                      [_web.design_instructions(
                                          brand or None, query,
                                          _web.ASSET_TOKEN)]))
            studio_at += 1
            design_feeder = studio_at - 1
            script_stage = stages[writer][0]
            json_only = (
                "\n\nSTRICT PIPELINE RULES:\n"
                "Your answer is parsed by a program, not read by a person. "
                "Obey the OUTPUT FORMAT block above exactly: the whole reply "
                "is one JSON object and nothing else. Do NOT add a handoff "
                "section, a summary, an explanation or a follow-up question. "
                "If any earlier instruction asked for one, it does not apply "
                "here — this is the only formatting rule that counts.")
            # Both the writer and the art director answer in JSON, so both are
            # exempt from the handoff rules, not just the one nearest the
            # renderer. Missing the writer is what made it pad its JSON with
            # commentary; missing the art director made it refuse outright.
            machine_stages[writer] = json_only
            machine_stages[design_feeder] = json_only
            ui.info(f"🎬  {stages[studio_at][1]} films the page — {an} writes "
                    f"the script, {director} art-directs it")

    local_reel_at = next((i for i, (_, an, _) in enumerate(stages)
                          if (A.resolve_agent("", an) or {}).get("local") == "reel"),
                         None)
    spec_feeder = None      # stage index that must answer in JSON, not prose
    if local_reel_at is not None:
        feeder = next((i for i in range(local_reel_at - 1, -1, -1)
                       if not (A.resolve_agent("", stages[i][1]) or {}).get("local")),
                      None)
        if feeder is None:
            ui.warn("Prism Reel has no writing stage before it — turn on content "
                    "or brains so something can write the script.")
        else:
            try:
                from . import reel as _reel
                st, an, qs = stages[feeder]
                stages[feeder] = (st, an, qs[:-1] + [
                    qs[-1] + "\n\n" + _reel.spec_instructions()])
                spec_feeder = feeder
                ui.info(f"🎬  {stages[local_reel_at][1]} renders locally — "
                        f"{an} will write the scene spec for it")
            except Exception:
                pass

    # Before the browser, not after: launching Chrome is the single slowest
    # and most visible thing this function does, and a run cancelled while the
    # plan was still being assembled would otherwise still throw a window onto
    # the user's screen before noticing it had been called off.
    if should_stop and should_stop():
        if on_event:
            on_event("cancelled", {"stage": "", "done": 0})
        ui.warn("Stopped before anything ran.")
        return {}, {}

    driver, fresh = _get_driver(cfg)
    all_responses: dict[str, list[str]] = {}
    all_links: dict[str, str] = {}
    pipeline_files: list[dict] = []   # images GENERATED by earlier stages
    # A freshly launched browser opens on one blank tab — reuse it for stage
    # one. A REUSED browser still has the previous run's result tabs open;
    # always open a new tab in that case so nothing gets navigated away.
    first_tab = fresh

    def stopped() -> bool:
        return bool(should_stop and should_stop())

    for stage_idx, (stage, agent_name, questions) in enumerate(stages):
        if stopped():
            ui.warn("Stopped at your request — keeping everything finished so far.")
            emit("cancelled", {"stage": stage, "done": len(all_responses)})
            break

        agent_cfg = A.resolve_agent(stage, agent_name)
        if not agent_cfg:
            # This `continue` used to be invisible to anything but a terminal:
            # the GUI got no event, so the step simply never appeared and the
            # run looked like it had silently skipped part of the plan.
            ui.warn(f"No registry entry for {agent_name} — skipping {stage}.")
            emit("stage_skipped", {
                "stage": stage, "agent": agent_name,
                "reason": f"{agent_name} isn't in Prism's tool registry, so "
                          "this step was left out. Pick a different tool for "
                          "it in the plan."})
            continue

        emit("stage_start", {"stage": stage, "agent": agent_name})
        ui.rule(f"{stage.upper()}  ·  {agent_name}", style=A.CATEGORIES.get(stage, {}).get("color", "pink"))

        # A LOCAL agent runs inside Prism — no tab, no upload, no scrape. It
        # consumes the previous stage's text and produces a real file here.
        if agent_cfg.get("local"):
            # Newest stage first, each kept separate: the spec comes from the
            # stage right before this one, and merging every stage into one
            # blob only gives the parser more prose to trip over.
            prior_text = [t for ts in reversed(list(all_responses.values()))
                          for t in ts if t.strip()]
            # Images an earlier stage GENERATED count as artwork too — that is
            # the whole point of harvesting them. The client's own files come
            # first so their real mark wins the 'logo' slot over anything a
            # model drew.
            out, note = _run_local(agent_cfg["local"], prior_text,
                                   (attachments or []) + pipeline_files,
                                   cfg, stage, brand=studio_brand)
            if out:
                all_responses[stage] = [note]
                all_links[stage] = out
                ui.ok(note)
                ui.info(f"   📁  {out}")
                emit("stage_done", {"stage": stage, "count": 1, "texts": [note],
                                    "url": out, "timed_out": False})
            else:
                ui.err(note)
                emit("stage_error", {"stage": stage, "error": note, "url": ""})
            continue

        timed_out = False
        try:
            if not first_tab:
                driver.execute_script("window.open('');")
                time.sleep(1)
                driver.switch_to.window(driver.window_handles[-1])
            first_tab = False
            driver.get(agent_cfg["url"])
            time.sleep(agent_cfg.get("page_wait", 4))

            # Only NON-EMPTY prior outputs count — a failed scrape must not
            # inject an empty "[STAGE]" block downstream.
            prior = [(s, t) for s, t in all_responses.items()
                     if t and any(x.strip() for x in t)]

            # Attachments are analysed ONCE, by the first stage, which hands
            # its findings forward. Later stages build on those findings and
            # do NOT get the raw file again — with two exceptions:
            #   • nothing usable came back from earlier stages (a scrape
            #     failed), so the stage would otherwise be blind;
            #   • PRODUCER stages — the agents that actually make the
            #     deliverable (image, reel, app, deck). Text handoffs dilute
            #     a document's exact copy and can't carry images/video at
            #     all, so the maker gets the user's original files too.
            # "format" is /boq's custom-pipeline label (core.boq) — its writer
            # gets the raw source file directly for the same reason: a text
            # handoff can't carry a binary CAD file, only a paraphrase of it.
            producer = stage in ("visual", "media", "development", "presentation", "format")
            include_attachment = bool(attachments) and (
                stage_idx == 0 or not prior or producer)
            # Producers also receive files GENERATED by earlier stages
            # (e.g. the logo the visual stage just made) — those can't
            # travel in a text handoff at all.
            send_files = (attachments if include_attachment else []) + \
                         (pipeline_files if producer else [])
            if send_files:
                _upload_files(driver, agent_cfg, send_files)

            # Relay hand-off: forward ONLY the most recent stage's output.
            # Every agent is instructed (below) to fold the key findings of
            # everything before it into its own answer, so the latest output
            # already carries the whole chain — re-sending every older stage
            # would only bloat and slow down the prompt.
            context = attach_ctx if include_attachment else ""
            if producer and pipeline_files:
                names = ", ".join(f["name"] for f in pipeline_files)
                context += (
                    f"An earlier pipeline stage GENERATED these image file(s), "
                    f"uploaded to this chat: {names}. Use them as assets in "
                    "what you produce — do not recreate them from scratch.\n\n"
                )
            # The art director is handed the script verbatim further down, so
            # the relay's "here is the previous stage" block adds nothing it
            # needs — and the stage immediately before it is the image maker,
            # whose text is chatter about the pictures. Forwarding that as the
            # brief is how a design ends up answering the wrong question.
            if stage_idx == design_feeder:
                prior = []

            if prior:
                prev_stage, prev_texts = prior[-1]
                prev_text = "\n\n".join(t for t in prev_texts if t.strip())
                if len(prev_text) > _MAX_FORWARD_CHARS:
                    prev_text = prev_text[-_MAX_FORWARD_CHARS:]
                context += (
                    f"Context from the previous pipeline stage ({prev_stage.upper()}) — "
                    "it already includes the distilled findings of every stage "
                    "before it. Build directly on this brief:\n\n"
                    f"{prev_text}\n\n"
                    "Now continue the pipeline and complete the following:\n\n"
                )

            # The stage feeding a LOCAL renderer is machine-read, so it gets
            # the final-stage rules even though a stage follows it. The normal
            # handoff rules below demand a prose "HANDOFF FOR …" section as the
            # LAST thing in the answer — flatly contradicting "reply with only
            # a JSON object", and a model resolving that contradiction writes
            # the handoff and drops the spec. That is exactly how a run ends up
            # with nothing to render.
            if stage_idx in machine_stages:
                handoff = machine_stages[stage_idx]
            elif stage_idx == spec_feeder:
                handoff = (
                    "\n\nSTRICT PIPELINE RULES:\n"
                    "Your answer is consumed by a renderer, not by another "
                    "chat. Obey the OUTPUT FORMAT block above exactly: the "
                    "whole reply is one JSON object and nothing else. Do NOT "
                    "add a handoff section, a summary, an explanation or a "
                    "follow-up question — any of those and nothing can be "
                    "rendered."
                )
            elif stage_idx + 1 < len(stages):
                nxt_stage, nxt_agent, _ = stages[stage_idx + 1]
                rules = [
                    "Perform ONLY the task above — nothing more. Do not build, "
                    "design or produce anything that was not explicitly asked of you.",
                ]
                if prior:
                    rules.append(
                        "First analyse the context above from the previous stage and "
                        "extract its most important findings in a short, precise form — "
                        "they must survive into your handoff."
                    )
                rules.append(
                    f"Your output will be passed directly to {nxt_agent} (the "
                    f"'{nxt_stage}' stage of this pipeline), and {nxt_agent} will see "
                    f"ONLY your answer — nothing from earlier stages. End with a "
                    f"section titled 'HANDOFF FOR {nxt_agent.upper()}' containing a "
                    f"short, precise summary of every key finding, decision and "
                    f"constraint so far (earlier stages' AND your own) that "
                    f"{nxt_agent} needs to do its job."
                )
                rules.append(
                    "Your reader is another AI, not a human — never end with a "
                    "follow-up question or an offer of options. The handoff "
                    "section must be the LAST thing in your answer."
                )
                handoff = "\n\nSTRICT PIPELINE RULES:\n" + "\n".join(
                    f"{i}. {r}" for i, r in enumerate(rules, 1))
            else:
                handoff = (
                    "\n\nSTRICT PIPELINE RULES:\n"
                    "You are the FINAL stage. The context above is your complete "
                    "brief — everything important from earlier stages is already "
                    "distilled into it. Perform ONLY the task above and deliver the "
                    "polished final result. Do not add any handoff or summary "
                    "section, and do not ask any follow-up questions."
                )

            if agent_name == "NotebookLM":
                # NotebookLM is not a chat box — it's a "sources" notebook
                # (add a source, then either ask about it or generate a
                # Video/Audio Overview). Best-effort automation driven by
                # visible button TEXT rather than CSS classes, since
                # Google's Material UI class names churn too often to
                # hard-code reliably — see _run_notebooklm()'s docstring.
                nb_prompt = _bmp_safe((context + "\n\n".join(questions) + handoff))
                stage_responses = _run_notebooklm(driver, agent_cfg, stage, nb_prompt)
            else:
                if stage_idx == design_feeder:
                    # Two things this prompt could not know when the plan was
                    # made. The assets, because the imagery stage had not run
                    # yet — and the SCRIPT, because the relay only forwards
                    # the stage immediately before, which is now the image
                    # maker's chatter rather than the words of the reel.
                    made = {f["path"] for f in pipeline_files}
                    table = {}
                    try:
                        from . import assets as _assets
                        table = _assets.collect(
                            (attachments or []) + pipeline_files,
                            generated=made)
                    except Exception as e:
                        ui.warn(f"   couldn't prepare the artwork ({e})")
                    design_assets = table
                    listing = (_assets.manifest(table) if table else "")
                    if table:
                        ui.info(f"   🖼️   {len(table)} asset(s) for the design: "
                                + ", ".join(table))
                    if not studio_brand and research_stage:
                        studio_brand = _web.read_brand(
                            all_responses.get(research_stage) or [])
                        if studio_brand:
                            ui.ok("   🎨  brand colours from their website — "
                                  + ", ".join(f"{k} {v}" for k, v
                                              in studio_brand.items()))
                        else:
                            ui.info("   no brand colours came back — the "
                                    "design will choose its own")
                    questions = [q.replace(_web.BRAND_TOKEN,
                                           _web.brand_block(studio_brand))
                                 for q in questions]

                    script = "\n\n".join(
                        t for t in (all_responses.get(script_stage) or [])
                        if t.strip())
                    head = (f"THE SCRIPT — final, use these words, this order "
                            f"and these timings:\n\n{script}\n\n"
                            if script else "")
                    questions = [head + q.replace(_web_token(), listing)
                                 for q in questions]

                for idx, prompt in enumerate(questions, 1):
                    try:
                        ui.info(f"   → prompt {idx}/{len(questions)}: {prompt[:80]}…")
                        textarea = WebDriverWait(driver, agent_cfg.get("input_wait", 15)).until(
                            EC.presence_of_element_located(
                                (By.CSS_SELECTOR, agent_cfg["textarea_selector"]))
                        )
                        try:
                            textarea.clear()
                        except Exception:
                            pass

                        full_prompt = ((context + prompt) if (idx == 1 and context) else prompt) + handoff
                        full_prompt = _bmp_safe(full_prompt)  # strip emoji ChromeDriver can't type
                        if not _fast_type(driver, textarea, full_prompt):
                            # JS insertion didn't take on this site — fall back
                            # to per-keystroke typing (slow but universal).
                            lines = full_prompt.split("\n")
                            for i, line in enumerate(lines):
                                if line:
                                    textarea.send_keys(line)
                                if i < len(lines) - 1:
                                    textarea.send_keys(Keys.SHIFT, Keys.ENTER)
                        time.sleep(1)

                        # Submit — try the button, fall back to Enter.
                        submitted = False
                        sel = agent_cfg.get("submit_selector", "")
                        if sel:
                            try:
                                btn = WebDriverWait(driver, 5).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                                btn.click()
                                submitted = True
                            except Exception:
                                pass
                        if not submitted:
                            textarea.send_keys(Keys.ENTER)

                        if idx < len(questions):
                            # Let this answer finish before sending the next prompt.
                            _smart_wait(driver, agent_cfg, 120)
                    except Exception as e:
                        ui.err(f"   prompt error: {e}")

                wait = agent_cfg.get("wait_time", 60)
                # A caller that knows what a finished answer looks like says
                # so (e.g. /email needs "SUBJECT:"), and a mid-answer pause
                # can no longer end the wait early.
                expect = (routing.get(stage) or {}).get("expect", "")
                if stage_idx == design_feeder:
                    expect = '"css"'
                elif stage_idx == spec_feeder:
                    # A spec streams in over several seconds and pauses mid-way;
                    # without a marker a pause reads as "finished" and we scrape
                    # the preamble before the JSON has been written.
                    expect = '"scenes"'
                ui.info(f"   ⏳  waiting up to {wait}s for {agent_name} to finish…")
                emit("waiting", {"stage": stage, "seconds": wait})
                took, settled = _smart_wait(driver, agent_cfg, wait,
                                            expect=expect, should_stop=should_stop)
                if stopped():
                    # Scrape before leaving: the tool has been generating for
                    # however long the user waited before pressing Stop, and
                    # that partial answer is theirs. The link is kept too, so
                    # the tab they can still read is one click away.
                    ui.warn("Stopped at your request — keeping this step's "
                            "output so far.")
                    try:
                        captured = _capture(driver, agent_cfg)
                        partial = [max(captured, key=len)] if captured else []
                    except Exception:
                        partial = []
                    all_links[stage] = driver.current_url
                    if partial:
                        all_responses[stage] = partial
                    emit("stage_done", {"stage": stage, "count": len(partial),
                                        "texts": partial,
                                        "url": driver.current_url,
                                        "timed_out": True})
                    emit("cancelled", {"stage": stage, "done": len(all_responses)})
                    break
                if settled:
                    ui.info(f"   ✓  response settled after {took}s")
                else:
                    # The cap ran out, not the tool: it is still generating
                    # in its tab and will finish there. Whatever is on the
                    # page gets scraped anyway (a partial answer beats
                    # none), and the link below is the real deliverable.
                    timed_out = True
                    ui.warn(f"still generating after {took}s — scraping what "
                            f"is on the page and keeping the link")

                if stage == "artwork":
                    # The images are the deliverable here, not the text, so
                    # this stage gets its own budget ON TOP of the agent's —
                    # long only where it needs to be, rather than making every
                    # ChatGPT stage in the pipeline wait like an image render.
                    from . import reel_web as _rw
                    ui.info("   ⏳  waiting for the pictures to finish "
                            "rendering (up to 5 more minutes)…")
                    got = _wait_for_images(driver, agent_cfg,
                                           _rw.MAX_GENERATED, cap=300)
                    if got:
                        timed_out = False
                    else:
                        ui.warn("   no images appeared — the reel will be "
                                "type and colour only")

                texts = _capture(driver, agent_cfg)
                if not texts:
                    stage_responses = []
                elif len(questions) == 1:
                    # One prompt → one answer: the biggest surviving capture IS it.
                    stage_responses = [max(texts, key=len)]
                else:
                    stage_responses = texts[-len(questions):]

                # The art director's page is laid out in a real browser before
                # anything is filmed. Text off the frame or too small to read
                # is not a matter of opinion, so it goes straight back with
                # the offending strings quoted.
                if stage_idx == design_feeder and texts:
                    from . import reel_web as _web
                    for attempt in range(2):
                        try:
                            cand = _web.parse_spec(texts[-1])
                            # The design is judged against the SAME artwork it
                            # was offered. Without this the checker sees a spec
                            # with no assets at all, calls every correct
                            # reference a hole, and talks the art director out
                            # of the one picture it had.
                            cand["_assets"] = design_assets
                        except Exception as e:
                            faults = [str(e)]
                            cand = None
                        else:
                            stage_responses = [texts[-1]]
                            try:
                                faults = _web.inspect(cand)
                            except Exception as e:
                                ui.warn(f"   couldn't lay the design out ({e})")
                                faults = []
                        if not faults:
                            if cand is not None:
                                ui.ok("   design lays out clean at 1080x1920")
                                # Told not to change a word is not the same as
                                # prevented. Flagged, not blocked: a design may
                                # legitimately split a headline across elements.
                                script_txt = "\n".join(
                                    all_responses.get(script_stage) or [])
                                for line in _web.script_drift(cand, script_txt)[:4]:
                                    ui.warn(f'   the design dropped or reworded: '
                                            f'"{line}"')
                            break
                        if attempt:
                            ui.err("   still not laying out — filming it "
                                   "anyway, check the result")
                            break
                        ui.warn(f"the design has {len(faults)} layout "
                                "problem(s) — sending them back")
                        for f in faults[:5]:
                            ui.info(f"   · {f}")
                        again = _reask(
                            driver, agent_cfg,
                            "Your design was laid out at 1080x1920 and these "
                            "are wrong:\n\n"
                            + "\n".join(f"{n}. {x}" for n, x
                                        in enumerate(faults[:10], 1))
                            + "\n\nFix the CSS and send the corrected design: "
                              "ONLY the JSON object, first character '{', "
                              "last '}'.",
                            expect='"css"')
                        if not again:
                            break
                        texts = again

                # This stage feeds a renderer, so "it answered" isn't enough —
                # it has to have answered in JSON. Prefer a capture that
                # actually parses (the spec is often shorter than the prose
                # around it), and if none does, ask once more in the same tab
                # before the run reaches the renderer with nothing to draw.
                if stage_idx == spec_feeder and texts:
                    from . import reel as _reel
                    spec_texts = [t for t in texts if _reel.has_spec(t)]
                    if spec_texts:
                        # LAST, not longest: the prompt we typed carries an
                        # example spec, so "biggest thing that parses" can be
                        # our own echo. Chat DOM order is chronological, so
                        # the newest capture is the reply.
                        stage_responses = [spec_texts[-1]]
                        # It parses — but does it render as something worth
                        # posting? The faults that matter (a series typed into
                        # a caption, an asterisk with no footnote, a scene
                        # describing a shot) are all legal JSON, so the only
                        # way to catch them is to look, and the only way to fix
                        # them is to say precisely what is wrong.
                        faults = _reel.lint_spec(
                            _reel.parse_spec(stage_responses[0]))
                        if faults:
                            ui.warn(f"the scene spec has {len(faults)} problem(s) "
                                    "— sending them back to be fixed")
                            for fault in faults[:6]:
                                ui.info(f"   · {fault}")
                            again = _reask(
                                driver, agent_cfg,
                                "Your JSON renders, but these are wrong:\n\n"
                                + "\n".join(f"{n}. {x}" for n, x
                                            in enumerate(faults[:10], 1))
                                + "\n\nSend the corrected scene spec: ONLY the "
                                  "JSON object, first character '{', last '}'.",
                                expect='"scenes"')
                            better = [t for t in again if _reel.has_spec(t)]
                            if better:
                                left = _reel.lint_spec(_reel.parse_spec(better[-1]))
                                # Keep the second attempt only if it is
                                # genuinely cleaner — a "fix" that trades six
                                # faults for seven is not a fix.
                                if len(left) < len(faults):
                                    stage_responses = [better[-1]]
                                    ui.ok(f"   fixed — {len(faults)} problem(s) "
                                          f"down to {len(left)}")
                                else:
                                    ui.info("   the second attempt was no "
                                            "better — keeping the first")
                    else:
                        ui.warn(f"{agent_name} wrote about the reel instead of "
                                "writing the spec — asking again for JSON only")
                        emit("retry", {"stage": stage, "reason": "no scene spec"})
                        again = _reask(
                            driver, agent_cfg,
                            "That reply cannot be rendered. Send the scene "
                            "spec itself now: reply with ONLY the JSON object, "
                            "first character '{', last character '}', no "
                            "preamble, no handoff, no fences.",
                            expect='"scenes"')
                        fixed = [t for t in again if _reel.has_spec(t)]
                        if fixed:
                            stage_responses = [fixed[-1]]
                            ui.ok("   got the scene spec on the second ask")
                        else:
                            ui.err("   still no scene spec — the renderer will "
                                   "have nothing to draw")
            if stage_responses:
                ui.info(f"   📥  captured {sum(len(t) for t in stage_responses)} chars")

            all_links[stage] = driver.current_url
            all_responses[stage] = stage_responses

            # Image-making stages: pull the generated images off the page so
            # later stages can actually use them (text handoffs can't).
            if stage in ("visual", "media", "artwork") and stage_idx + 1 < len(stages):
                made = _harvest_images(driver, agent_cfg, stage)
                if stage == "artwork":
                    # A reel wants a few strong images, not a contact sheet —
                    # and every extra one is another asset the art director
                    # has to find a place for.
                    from . import reel_web as _rw
                    made = made[:_rw.MAX_GENERATED]
                if made:
                    pipeline_files = (pipeline_files + made)[-6:]
                    ui.info(f"   🖼️   harvested {len(made)} generated image(s) "
                            "for the next stages")

            if stage_responses:
                ui.ok(f"captured {len(stage_responses)} response(s)")
                emit("stage_done", {"stage": stage, "count": len(stage_responses),
                                    "snippet": stage_responses[0][:200],
                                    "texts": stage_responses, "url": driver.current_url,
                                    "timed_out": timed_out})
            else:
                # Nothing came back. Before reporting that as a scrape miss,
                # ask the far more likely question: is this a sign-in wall?
                blocked = _looks_signed_out(driver)
                if blocked:
                    ui.err(blocked)
                else:
                    ui.warn("no response scraped, but link saved")
                emit("stage_done", {"stage": stage, "count": 0, "texts": [],
                                    "url": driver.current_url,
                                    "timed_out": timed_out,
                                    "blocked": blocked})
            ui.info(f"   🔗  {driver.current_url}")

        except Exception as ex:
            # The tab is still open on whatever the tool was doing, and for
            # the slow producers (decks, video, apps) that page IS the
            # deliverable — it keeps rendering server-side after we gave up.
            # So the link goes out with the error, not instead of it.
            url = _safe_url(driver, exclude=set(all_links.values()))
            if url:
                all_links[stage] = url
                ui.info(f"   🔗  {url}  (still open — the tool may finish there)")
            ui.err(f"stage {stage} failed: {ex}")
            emit("stage_error", {"stage": stage, "error": str(ex), "url": url})

    return all_responses, all_links


def open_login_tabs(urls: list[str]):
    """Open each tool's URL so the user can sign in before a real run.

    Crucially this opens PRISM's profile, not the everyday one. Runs use
    PROFILE_DIR, so a login done in the normal browser lands in a different
    cookie jar and the run still hits a sign-in wall — which is exactly the
    'it isn't staying logged in' complaint. Signing in here writes to the same
    profile the automation drives, and it persists."""
    seed_profile()
    _clear_profile_locks()
    chrome = next((c for c in _CHROME_BINARIES if os.path.exists(c)), None)
    if not chrome:
        ui.warn("Chrome not found — opening in your default browser instead. "
                "Logins there will NOT carry into Prism's runs.")
        for url in urls:
            webbrowser.open(url)
        return

    args = [chrome, f"--user-data-dir={PROFILE_DIR}", "--profile-directory=Default"]
    ui.info("   🔐  opening Prism's browser profile — sign in here and it "
            "sticks for every run")
    first = True
    for url in urls:
        subprocess.Popen(args + [url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Chrome may be cold-starting on the first URL — give its singleton
        # lock time to settle so the remaining tabs join the same instance
        # instead of racing it and getting dropped.
        time.sleep(3.5 if first else 0.5)
        first = False

