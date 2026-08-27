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


def _open_tab(driver, agent_name: str = "") -> bool:
    """Put the next stage in a NEW tab, leaving every finished one on screen.

    Each stage gets its own tab so the whole run stays readable afterwards —
    Claude's answer still there when ChatGPT's arrives, and so on. That is the
    point of never calling quit(), and it only works if this actually gets a
    new tab every time.

    It was `execute_script("window.open('')")`, then `sleep(1)`, then
    `window_handles[-1]`, and that failed two ways — both silently, and both
    ending in the same place: no new tab, so `driver.get()` navigated the tab
    the PREVIOUS agent's answer was sitting in, and that answer was gone.

      · window.open() from injected script is not a user gesture, so Chrome's
        popup blocker is entitled to refuse it, and on a real profile with
        stricter settings it does.
      · Even when allowed, one second is a guess. If the handle had not
        registered yet, handles[-1] was still the OLD tab.

    switch_to.new_window() is the WebDriver command for this. It goes through
    the driver rather than the page, so the popup blocker has no say, and it
    returns once the tab exists rather than after a hopeful sleep.

    Returns whether a new tab was actually obtained. Falling back to the old
    trick, and then to reusing the current tab, is deliberate: losing an
    earlier answer from the screen is bad, but refusing to run the stage at
    all would be worse.
    """
    before = set(driver.window_handles)
    try:
        driver.switch_to.new_window("tab")
        if set(driver.window_handles) - before:
            return True
    except Exception:
        pass

    try:                                # older drivers, or a refusal above
        driver.execute_script("window.open('');")
        for _ in range(20):             # up to ~4s, checked rather than assumed
            time.sleep(0.2)
            new = set(driver.window_handles) - before
            if new:
                driver.switch_to.window(new.pop())
                return True
    except Exception:
        pass

    ui.warn(f"   ⚠️   couldn't open a new tab for {agent_name or 'this step'} — "
            f"reusing the current one, so the previous answer will be replaced. "
            f"Its text is still saved in this run.")
    return False


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


def _ensure_session_restore() -> None:
    """Keep session-only login cookies alive across a full Prism restart.

    Some tools (Kimi included) sign you in with a cookie that has no
    Expires/Max-Age — a "session cookie". Chrome deletes those the moment the
    browser process ends, UNLESS the profile's startup setting is "Continue
    where you left off" rather than the default "Open the New Tab page". The
    browser stays open across runs within one Prism session (nothing here
    calls driver.quit() until the app itself quits — see shutdown()), so this
    only bites after a full restart: everything else in the profile persisted
    fine, but a tool using a session cookie silently needs a fresh login.
    """
    import json
    path = os.path.join(PROFILE_DIR, "Default", "Preferences")
    try:
        with open(path) as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        prefs = {}
    if prefs.get("session", {}).get("restore_on_startup") == 1:
        return
    prefs.setdefault("session", {})["restore_on_startup"] = 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".restore-pref"
        with open(tmp, "w") as f:
            json.dump(prefs, f, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def _reset_to_blank_tab(driver) -> None:
    """Collapse a restored session back down to the single blank tab the
    pipeline expects a fresh launch to have.

    _ensure_session_restore() sets "Continue where you left off" so
    session-only login cookies survive a restart — but that setting also
    makes Chrome reopen every tab left over from the last time Prism quit,
    and stage one relies on a freshly launched browser opening on exactly
    one blank tab (see `first_tab` in run())."""
    handles = driver.window_handles
    if len(handles) <= 1:
        return
    for h in handles[1:]:
        try:
            driver.switch_to.window(h)
            driver.close()
        except Exception:
            pass
    try:
        driver.switch_to.window(handles[0])
        driver.get("about:blank")
    except Exception:
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
    _ensure_session_restore()
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

    try:
        drv = uc.Chrome(options=opts, user_data_dir=tmp,
                        version_main=version_main)
        _reset_to_blank_tab(drv)
        return drv
    except Exception as e:
        # Chrome updates itself about monthly, and for a day or two after a
        # major release there may be no matching driver to download — as there
        # is none on a machine behind a proxy that blocks the fetch. The raw
        # Selenium traceback tells a business owner nothing, and this is the
        # first thing that happens when they press Start the work.
        detail = str(e).strip().splitlines()[0][:180] if str(e).strip() else ""
        raise RuntimeError(
            "Prism couldn't start Chrome.\n\n"
            "This is almost always a version mismatch — Chrome updated itself "
            "and the matching driver isn't available yet.\n\n"
            "Two things fix it:\n"
            "  1. Update Chrome (Chrome menu → About Google Chrome) and try "
            "again.\n"
            "  2. If you have pinned a version in Settings → Chrome, clear "
            "that box so Prism detects it automatically.\n\n"
            + (f"Technical detail: {detail}" if detail else "")) from e


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
            # A guardrail (e.g. router.apply_studio_guardrail) may have
            # picked a different tool for THIS run only — a structural
            # mismatch between the brief and the configured tool, not
            # something worth changing the user's saved setting over. Their
            # own config still wins whenever no override was set.
            name = data.get("agent_override") or agents.get(stage)
        if not name:
            continue
        yield stage, name, questions


def _upload_files(driver, agent_cfg, attachments, agent_name: str = ""):
    """Push any attached files into the tool's <input type='file'>, if present.

    Returns the number of files that actually reached the page. A caller that
    ignores the return value still gets the failure through the usual channel
    — ui.warn() — because a customer's drawing or PO silently never reaching
    the agent is exactly the kind of thing that must not fail quietly: the
    pipeline would otherwise carry on as if the attachment had gone up, and
    nobody finds out until the response makes no sense.
    """
    if not attachments:
        return 0
    from selenium.webdriver.common.by import By
    from . import files as F

    who = agent_name or "this tool"
    sel = agent_cfg.get("upload_selector", "input[type='file']")
    inputs = driver.find_elements(By.CSS_SELECTOR, sel)
    if not inputs:
        ui.warn(f"   ⚠️   {who} has no file-upload field on this page — "
                f"{len(attachments)} attachment(s) were NOT sent; it will "
                "answer blind to them")
        return 0
    paths = F.upload_paths(attachments)
    target = inputs[0]
    uploaded = 0
    try:
        # Most multi-file inputs accept newline-separated paths in one send_keys.
        target.send_keys("\n".join(paths))
        uploaded = len(paths)
        ui.info(f"   📎  uploaded {uploaded} file(s)")
    except Exception as e:
        # Fall back to one-at-a-time (input may be replaced between sends).
        # Every failure here used to vanish into a bare `except: pass` — the
        # short reason is kept now so a customer wondering why a file never
        # showed up has something better than silence to go on.
        bulk_reason = str(e).strip().splitlines()[0][:120] if str(e).strip() else "no detail"
        reasons = []
        for p in paths:
            try:
                for inp in driver.find_elements(By.CSS_SELECTOR, sel):
                    inp.send_keys(p)
                    uploaded += 1
                    break
            except Exception as pe:
                detail = str(pe).strip().splitlines()[0][:120] if str(pe).strip() else "no detail"
                reasons.append(f"{os.path.basename(p)} ({detail})")
        if uploaded:
            ui.info(f"   📎  uploaded {uploaded} file(s)")
        if reasons:
            ui.warn(f"   couldn't upload {len(reasons)} of {len(paths)} "
                    f"file(s) to {who} — bulk upload failed ({bulk_reason}), "
                    "then one-at-a-time failed too: "
                    + "; ".join(reasons[:3])
                    + (f" (+{len(reasons) - 3} more)" if len(reasons) > 3 else ""))
    if not uploaded:
        ui.warn(f"   ⚠️   0 of {len(paths)} attachment(s) reached {who} — "
                "it will answer without ever seeing them")
        return 0   # nothing reached the page — no ingest to wait for
    if uploaded < len(paths):
        ui.warn(f"   ⚠️   only {uploaded} of {len(paths)} attachment(s) "
                f"uploaded to {who} — the rest never reached the page")
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
    return uploaded


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


# A link that plainly points at a real file — a document, deck, spreadsheet,
# archive or source file — rather than an ordinary navigational link. Kept
# narrow and shape-based on purpose, the same way _harvest_images only takes
# an <img> sized like a real picture: a chat reply that merely TALKS about a
# file must not count, only a link that plainly IS one.
_HARVESTABLE_EXTS = (
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv",
    ".odt", ".odp", ".ods", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".html", ".css",
    ".java", ".c", ".cpp", ".h", ".go", ".rb", ".php", ".sh", ".sql",
    ".ipynb", ".md", ".txt", ".rtf",
)


def _harvest_files(driver, agent_cfg, stage: str) -> list[dict]:
    """Non-image sibling of _harvest_images. A code, presentation or format
    stage's real deliverable is a DOCUMENT, DECK, CODE file or ARCHIVE, not a
    picture — that can't travel in a text handoff either, only the file
    itself can, and it joins the same pipeline_files list _harvest_images
    already feeds so a later producer stage receives it the same way.

    _harvest_images finds its candidates by shape — an <img> sized like a
    real picture rather than an icon or avatar. There is no equivalent
    universal signal for a document, so the candidate test is different (a
    link whose href plainly points at a file: a real extension, an explicit
    `download` attribute, or a `blob:` URL — how Code-Interpreter-style
    "Download" buttons usually work), but everything around that test is the
    SAME mechanism reused rather than reinvented: search the response area
    first, fall back to the whole page (a tool's download UI can sit outside
    the message bubble the same way ChatGPT's image canvas does), fetch the
    bytes through the page's OWN session so an auth-gated link still
    resolves, and hand back an attachment record built the normal way
    (core.files.attach, the same function a user's own upload goes through).

    Unlike an image, there is no "screenshot the element" fallback when the
    fetch fails — a rendered pixel grid can stand in for a picture; nothing
    can stand in for a PDF. A candidate that can't be fetched is skipped.
    """
    import base64
    from selenium.webdriver.common.by import By
    from . import files as F

    sel = agent_cfg.get("response_selector", "")
    links = []
    try:
        if sel:
            links = driver.find_elements(By.CSS_SELECTOR, f"{sel} a[href]")
        if not links:
            links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
    except Exception:
        return []

    out, seen = [], set()
    try:
        driver.set_script_timeout(20)
    except Exception:
        pass
    for a in links:
        try:
            href = a.get_attribute("href") or ""
            download_attr = a.get_attribute("download")
        except Exception:
            continue
        if not href or href in seen:
            continue
        clean = href.split("?")[0].split("#")[0]
        href_ext = os.path.splitext(clean)[1].lower()
        # The `download` attribute is the site's own suggested filename and,
        # when present, a more reliable source for the real extension than
        # the URL — a download endpoint's href is often extension-less.
        ext = os.path.splitext(download_attr or "")[1].lower() or href_ext
        if not (href.startswith("blob:") or download_attr is not None
                or href_ext in _HARVESTABLE_EXTS):
            continue   # an ordinary navigational link, not a deliverable
        seen.add(href)

        raw = None
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
                """, href)
            if data and data.startswith("data:"):
                header, b64 = data.split(",", 1)
                raw = base64.b64decode(b64)
        except Exception:
            raw = None
        if not raw:
            continue

        name = f"{stage}_file{len(out) + 1}{ext}"
        path = os.path.join(tempfile.gettempdir(), f"prism_{name}")
        try:
            with open(path, "wb") as f:
                f.write(raw)
            att = F.attach(path)
        except Exception:
            continue
        if att["kind"] == "image":
            continue   # _harvest_images already owns real pictures
        # Marked so a later stage can tell a file a model produced from one
        # the client actually supplied — same convention _harvest_images uses.
        att["_generated"] = True
        out.append(att)
        if len(out) >= 4:
            break
    return out


def _save_artifacts(items: list[dict], query: str, stage: str) -> None:
    """Copy what a stage just generated out of the temp directory `items`
    were harvested into, and into the one folder a customer can still find
    once Prism is closed — see config.ARTIFACTS_DIR.

    Best-effort and silent per item: a full disk or a permissions slip here
    must not cost the run its actual result, which the harvested temp file
    (and, for a producer stage with a next step, `pipeline_files`) already
    holds regardless of whether this copy succeeds.
    """
    from . import config
    for item in items:
        path = item.get("path")
        if not path:
            continue
        try:
            saved = config.save_artifact(path, query, kind=stage)
        except Exception:                               # noqa: BLE001
            continue
        ui.info(f"   💾  saved to {saved}")


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


# ── how firmly to talk to a tool ──────────────────────────────────────────────
# Most agents are chat models being used as pipeline components, and they need
# telling: do only this, hand over in this shape, do not ask me questions back.
# That block of STRICT PIPELINE RULES is what keeps a ten-stage run coherent.
#
# It is also actively harmful to a tool that does its own multi-pass work.
# LAZYCOOK runs Generate → Analyze → Optimize → Validate and scrapes the web
# itself; "perform ONLY the task above — nothing more" reads to it as an
# instruction to skip those passes, and it answers in one shot. The tool then
# underperforms the Perplexity it was picked over, which looks like Prism
# choosing badly rather than Prism asking badly.
#
# So an agent can declare prompt_style="natural" and get asked the way a person
# would ask. The pipeline still needs a handoff, so it is still requested — as
# a request, at the end, in a sentence rather than as rule 3 of 4.
def _is_natural(agent_cfg: dict) -> bool:
    return (agent_cfg or {}).get("prompt_style") == "natural"


# How much of the user's own request travels into every stage prompt. Generous
# on purpose: this is the one piece of text nothing else can reconstruct, and
# truncating the sentence that says what the product DOES is exactly the
# failure this exists to prevent.
_MAX_INTENT_CHARS = 2500


# Selenium's wordings for "there is no browser to talk to any more". Every one
# of these means the session is dead, not that this particular step went wrong.
_BROWSER_GONE = (
    "no such window",
    "target window already closed",
    "web view not found",
    "invalid session id",
    "session deleted because of page crash",
    "disconnected: not connected to devtools",
    "chrome not reachable",
    "browser has closed",
)


def _keep_failed_spec(sources) -> str:
    """Write the reply that would not parse to disk, and say where.

    A renderer failure is the one place where the evidence is both essential
    and momentary: the text lives in a local variable, the run moves on, and
    all anybody is left with is "No JSON found in the agent's reply" against a
    ChatGPT tab that visibly contains JSON. Those two facts cannot both be
    investigated from a log line.

    Best-effort in every direction — a full disk must not turn a bad design
    into a crash.
    """
    try:
        from . import config
        folder = os.path.join(os.path.dirname(config.CONFIG_PATH), "logs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"design-that-would-not-parse-{int(time.time())}.txt")
        with open(path, "w", encoding="utf-8") as f:
            for i, text in enumerate(sources, 1):
                f.write(f"───── candidate {i} ─────\n{text}\n\n")
        ui.warn(f"   saved what came back to {path}")
        return path
    except Exception:                                    # noqa: BLE001
        return ""


def _browser_is_gone(error: object) -> bool:
    """True when the failure is the browser itself, not the step.

    Matched on the message rather than the exception class because
    undetected_chromedriver re-raises through several of Selenium's types and
    the wording is the only thing common to all of them.
    """
    return any(marker in str(error).lower() for marker in _BROWSER_GONE)


def _intent_block(query: str) -> str:
    """The user's own words, verbatim, at the top of every stage prompt.

    The failure this fixes, in full, because it is not obvious and it cost a
    whole reel:

    Prism expands a request into a professional task brief, and the router
    writes each stage prompt FROM that brief. The router itself is given the
    raw request and told it wins on scope — but the STAGE PROMPTS it produces
    were only as good as what it chose to carry across, and the agents never
    saw the original at all.

    A customer asked for a reel about "Consiz, a mouse with a middle button
    that summarises whatever you have selected and lets you ask questions
    about it". The brief came back as "showcase the mouse, demonstrate its
    features, explain its benefits" — every specific, mechanical fact gone.
    Claude then wrote an excellent script about a generic productivity mouse,
    because a generic productivity mouse is all it was ever told about.

    Nothing downstream can recover from that. The brief is a summary, and a
    summary that drops the one fact the whole video is about is indetectable
    to everything after it: the words that came through read perfectly well.

    So the raw request rides along, first, marked as the human's own and
    authoritative over anything that follows. It costs a few hundred tokens a
    stage and removes a whole class of quietly-wrong output.
    """
    text = (query or "").strip()
    if not text:
        return ""
    if len(text) > _MAX_INTENT_CHARS:
        text = text[:_MAX_INTENT_CHARS].rstrip() + " […]"
    return (
        "WHAT THE PERSON ACTUALLY ASKED FOR — in their own words:\n"
        "---\n"
        f"{text}\n"
        "---\n"
        "Everything below is Prism's engineered version of that request. It "
        "is there to help you, but it is a SUMMARY and summaries lose things. "
        "Where the two differ, or where the text below is vaguer about what "
        "the thing actually does, the words above win. Specific facts above — "
        "how a product works, what a button does, what must be avoided — must "
        "survive into your answer even if the brief below does not repeat "
        "them.\n\n"
    )


def _context_header(agent_cfg: dict, prev_stage: str) -> str:
    """The line that introduces the previous stage's output."""
    if _is_natural(agent_cfg):
        return ("Here's what I've got so far on this — use whatever is useful "
                "and ignore the rest:\n\n")
    return (f"Context from the previous pipeline stage ({prev_stage.upper()}) — "
            "it already includes the distilled findings of every stage "
            "before it. Build directly on this brief:\n\n")


def _context_footer(agent_cfg: dict) -> str:
    if _is_natural(agent_cfg):
        return "\n\nWith that in mind:\n\n"
    return "\n\nNow continue the pipeline and complete the following:\n\n"


def _natural_handoff(nxt_agent: str, final: bool) -> str:
    """A request, not a rule sheet.

    Deliberately says nothing about performing only the task, nothing about
    the reader being a machine, and nothing about not asking questions. Those
    three are what suppress a self-directing tool. What it does keep is the
    one thing the pipeline genuinely cannot work without: a short summary at
    the end that the next tool can read on its own.
    """
    if final:
        return ("\n\nGo as deep as you think it needs — research it properly, "
                "check what you find, and give me the finished piece rather "
                "than an outline. Please don't finish by asking me what I'd "
                "like next; just give me your best work.")
    return (
        "\n\nTake your time with this one — research it properly and use your "
        "own judgement about what matters. Depth is welcome.\n\n"
        f"One thing I need at the end: I'm passing your answer straight to "
        f"{nxt_agent}, and it won't see any of this conversation. So finish "
        f"with a short section headed 'HANDOFF FOR {nxt_agent.upper()}' — just "
        f"the key facts, figures and decisions it would need to carry on "
        f"without me explaining anything. Keep it brief; the detailed work "
        f"goes above it.")


def _resolve_suffix(agent_cfg: dict, stage: str, query: str) -> str:
    """The literal text appended to this agent's prompt for this stage.

    A registry entry is either a plain string — always appended — or a switch:

        {"when": ("canva", "editable", …),
         "asked":     "…build it as an editable Canva design…",
         "otherwise": "…generate the image yourself, do not use Canva…"}

    which is resolved against THE USER'S OWN WORDS, not against the router's
    rewrite of them. That distinction is the whole point. ChatGPT with the
    Canva app connected will route every image through it given the chance,
    and the result is a stock template where an illustration was wanted — so
    the editable-design path has to be something the customer asked for out
    loud, and a router paraphrasing a brief must not be able to opt them in.

    Both branches are spelled out because silence is not neutral: leaving the
    prompt quiet is what let ChatGPT reach for Canva unprompted.
    """
    entry = (agent_cfg.get("stage_suffix") or {}).get(stage, "")
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return ""
    triggers = entry.get("when") or ()
    lowered = (query or "").lower()
    asked = any(str(t).lower() in lowered for t in triggers)
    return entry.get("asked" if asked else "otherwise", "") or ""


# ── Apollo: a filter screen, not a chat box ───────────────────────────────────
#
# Every other tool in the registry takes one blob of text. Apollo takes a set
# of FIELDS, and its API refuses any single field over 200 characters — which
# is how a normal pipeline brief ended a run with:
#     Value too long: 'Context from the previous pipeline stage (RESEA…'
#
# Two halves to the fix. agents._APOLLO_HANDOFF makes the previous stage write
# a fixed block of filter lines instead of prose; everything below parses that
# block and puts the values into Apollo's own search URL.
#
# Why the URL and not the filter widgets: Apollo mirrors its entire search
# state into the address bar, so navigating to a built URL sets the same
# filters as clicking through the left rail — without touching a single one
# of the class names that rail is built from. The results grid is the only
# part of the DOM this still depends on.
#
# The parameter names are Apollo's own, read off the address bar of a search
# built by hand in the UI. If Apollo renames one, that filter silently stops
# applying (the search still runs, just wider) — so a run that comes back
# with obviously unfiltered results means it is time to build the search in
# Apollo once and compare its URL against _APOLLO_PARAMS.
_APOLLO_PARAMS = {
    "TITLES":     "personTitles[]",
    "LOCATIONS":  "personLocations[]",
    "INDUSTRIES": "qOrganizationKeywordTags[]",
}

# Apollo expresses headcount as a "low,high" pair. The handoff spec asks for
# these exact labels so the mapping can be a lookup rather than a parser.
_APOLLO_HEADCOUNT = {
    "1-10": "1,10", "11-20": "11,20", "21-50": "21,50", "51-100": "51,100",
    "101-200": "101,200", "201-500": "201,500", "501-1000": "501,1000",
    "1001-2000": "1001,2000", "2001-5000": "2001,5000",
    "5001-10000": "5001,10000", "10001+": "10001,1000000",
}

_APOLLO_FIELDS = ("TITLES", "INDUSTRIES", "LOCATIONS", "HEADCOUNT", "KEYWORDS")


def _apollo_filters(text: str, cap: int = 180) -> dict[str, list[str]]:
    """Pull the 'HANDOFF FOR APOLLO' block out of the previous stage's answer.

    Tolerant on purpose: the block is looked for anywhere in the text, the
    field names are matched case-insensitively, and markdown bullets or bold
    the model added anyway are stripped off. A field it could not narrow comes
    back as "any", which is treated as absent rather than searched for
    literally — searching Apollo for the job title "any" returns nothing.

    Every value is truncated to `cap`, because the whole point of this path is
    that Apollo rejects long ones. Truncation is the last line of defence: the
    prompt already asked for short values, but a prompt is a request and this
    is a guarantee.
    """
    import re

    out: dict[str, list[str]] = {}
    for field in _APOLLO_FIELDS:
        # Last occurrence wins — models often restate the block, and the final
        # one is the considered answer rather than an example being echoed.
        hits = re.findall(rf"^\W*{field}\s*:\s*(.+)$", text,
                          re.IGNORECASE | re.MULTILINE)
        if not hits:
            continue
        raw = hits[-1].strip().strip("*_`").strip()
        values = []
        for part in raw.split(","):
            v = part.strip().strip("*_`[]").strip()
            # "any"/"n/a"/"none" all mean "the model declined to narrow this".
            if not v or v.lower() in ("any", "n/a", "na", "none", "all", "-"):
                continue
            values.append(v[:cap])
        if values:
            out[field] = values[:8]   # a 40-title search is not a search
    return out


def _apollo_url(base: str, filters: dict[str, list[str]]) -> str:
    """Turn parsed filters into an Apollo people-search URL.

    Apollo is a hash-routed SPA, so the query string lives after '#/people'.
    The '[]' in its repeated parameters is left literal rather than
    percent-encoded — that is how Apollo writes its own links.
    """
    from urllib.parse import quote

    parts = ["page=1",
             # The reason to use Apollo at all rather than ask a model to
             # guess addresses. Without it the table fills with rows whose
             # email column is a locked placeholder.
             "contactEmailStatusV2[]=verified"]

    for field, param in _APOLLO_PARAMS.items():
        for value in filters.get(field, []):
            parts.append(f"{param}={quote(value)}")
    if filters.get("INDUSTRIES"):
        # Tells Apollo which company fields the industry keywords above should
        # be matched against; without it the keyword filter is ignored.
        parts.append("includedOrganizationKeywordFields[]=tags")
        parts.append("includedOrganizationKeywordFields[]=name")
    for band in filters.get("HEADCOUNT", []):
        pair = _APOLLO_HEADCOUNT.get(band.replace(" ", ""))
        if pair:
            parts.append(f"organizationNumEmployeesRanges[]={quote(pair)}")
    if filters.get("KEYWORDS"):
        parts.append("qKeywords=" + quote(" ".join(filters["KEYWORDS"])))

    root = base.split("?")[0] or "https://app.apollo.io/#/people"
    return f"{root}?" + "&".join(parts)


def _run_apollo(driver, agent_cfg: dict, stage: str, brief: str) -> list[str]:
    """Drive Apollo by URL from the filter block the previous stage wrote.

    Falls back to typing a short query into the search box when there is no
    parseable block — an Apollo search on rough keywords still beats failing
    the stage, and the truncation keeps it inside the 200-character limit that
    caused the original error either way.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    cap = int(agent_cfg.get("max_query_chars", 180))
    filters = _apollo_filters(brief, cap)

    if filters:
        shown = "; ".join(f"{k.lower()}: {', '.join(v)}"
                          for k, v in filters.items())
        ui.info(f"   🎯  Apollo filters — {shown}")
        url = _apollo_url(agent_cfg.get("url", ""), filters)
        try:
            driver.get(url)
        except Exception as e:
            ui.warn(f"   couldn't open the filtered search ({e})")
    else:
        # Nothing structured came back. Take the longest line that looks like
        # prose about who to find, and search on that.
        ui.warn("   the previous stage sent no APOLLO filter block — falling "
                "back to a plain keyword search")
        words = [ln.strip() for ln in brief.splitlines()
                 if len(ln.strip()) > 20 and not ln.strip().startswith("#")]
        query = _bmp_safe((words[-1] if words else brief)[:cap])
        try:
            box = WebDriverWait(driver, agent_cfg.get("input_wait", 30)).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, agent_cfg["textarea_selector"])))
            box.clear()
            if not _fast_type(driver, box, query):
                box.send_keys(query)
            box.send_keys(Keys.ENTER)
        except Exception as e:
            ui.err(f"   couldn't run the Apollo search: {e}")
            return []

    # The grid loads asynchronously well after the URL settles.
    try:
        WebDriverWait(driver, agent_cfg.get("input_wait", 45)).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, agent_cfg["response_selector"])))
    except Exception:
        pass
    time.sleep(4)

    rows = _capture(driver, agent_cfg)
    if not rows:
        ui.warn("   Apollo returned no rows. Three things do this: the "
                "filters matched nobody, this Apollo account is signed out, "
                "or it is out of email credits for the month. Open Login "
                "tabs, check you can see the People table, and check your "
                "credit balance at the top of Apollo.")
    return rows


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


# A tool that has run out for now. Kept apart from _SIGNIN_MARKERS because the
# two need opposite advice: signing in is something only the user can do,
# whereas an exhausted quota is something Prism can route around by itself.
# Telling somebody to "sign in again" when Claude has merely used up its free
# messages sends them off to fix a thing that is not broken.
_EXHAUSTED_MARKERS = (
    "you've reached your limit", "you have reached your limit",
    "message limit reached", "reached the limit", "usage limit",
    "out of free messages", "no free messages", "free messages remaining",
    "daily limit", "limit resets", "limit will reset",
    "upgrade to continue", "upgrade your plan to continue",
    "subscribe to continue", "too many requests", "rate limit",
    "quota exceeded", "out of credits", "no credits remaining",
    "plan limit", "capacity constraints", "currently at capacity",
)


def _looks_exhausted(driver) -> str:
    """"" if the tool is fine, else why it cannot answer right now.

    The commonest way a long run dies at the last step: the free tier on
    whichever tool drew the heaviest stage runs out, and Prism reports "no
    response scraped" — which reads as Prism being broken rather than as
    "Claude is finished for the next few hours, use something else".

    Only consulted when a stage produced nothing, so a false positive costs one
    wrong sentence and one unnecessary attempt on another tool — never a good
    answer.
    """
    try:
        body = (driver.find_element("tag name", "body").text or "").lower()
    except Exception:
        return ""
    if not body:
        return ""
    # No length ceiling here, unlike the sign-in check below. A quota notice
    # appears ON a full conversation page with every previous turn still on it,
    # so the "a real page is long" heuristic that makes the sign-in check safe
    # would miss every one of these.
    for marker in _EXHAUSTED_MARKERS:
        if marker in body:
            return (f"This tool has hit its usage limit for now — the page "
                    f"says \"{marker}\".")
    return ""


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


def _looks_unreadable(driver, questions, timed_out: bool = False) -> str:
    """"" unless the page holds a real conversation we could not read.

    The third case, and the one that had no message of its own. A stage that
    captures nothing is one of four things: the tool ran out (_looks_exhausted),
    the page is a sign-in or robot wall (_looks_signed_out), the answer was
    still being written, or THE SITE CHANGED ITS MARKUP and our selector no
    longer matches. The last two both landed on "it returned nothing", which
    tells the customer only that Prism failed.

    Distinguished by our own prompt being on the page: if it is, we reached the
    tool, typed, and submitted successfully — so this is not a login problem and
    saying so sends the customer to fix something that is not broken.

    Note the length ceiling in _looks_signed_out is doing its job and is not the
    bug: it is what stops a long conversation page being reported as a sign-in
    wall. This function covers the case that ceiling deliberately lets through.
    """
    try:
        body = (driver.find_element("tag name", "body").text or "")
    except Exception:
        return ""
    if len(body) <= 4000:
        return ""                       # short page: the other two checks own it
    asked = [q for q in (questions or []) if q and len(q) > 40]
    if not any(q[:40].lower() in body.lower() for q in asked):
        return ""                       # cannot prove we got our prompt in
    if timed_out:
        return ("This tool was still writing its answer when Prism stopped "
                "waiting. The tab is still open and the answer will finish "
                "there — give it longer in Settings, or copy it from the tab.")
    return ("This tool answered, but Prism could not read the reply off the "
            "page — the site has most likely changed its layout. Your prompt "
            "went through, so the answer is in the open tab and can be copied "
            "from there. Please report it: this needs a Prism update, and it "
            "is not something signing in again will fix.")


def _match(driver, selector: str) -> list:
    from selenium.webdriver.common.by import By
    if not selector:
        return []
    try:
        return driver.find_elements(By.CSS_SELECTOR, selector)
    except Exception:
        return []                       # invalid selector, or the page moved


def _capture(driver, agent_cfg: dict) -> list[str]:
    """Everything on the page that reads as a reply, longest captures only.

    Falls back to the GENERIC selector when the hand-tuned one matches nothing.
    The tuned selectors are pinned to markup we do not own: these sites roll
    redesigns out in buckets, so the same version of Prism can be right for one
    customer and wrong for another on the same afternoon. A tuned selector that
    matches is always preferred — the generic one is broader and picks up more
    noise — but matching nothing at all is the one case where broader-and-noisy
    beats exact-and-empty, because empty is indistinguishable from failure.
    """
    elements = _match(driver, agent_cfg.get("response_selector", ""))
    if not elements:
        from . import agents as _agents
        generic = _agents._GENERIC["response_selector"]
        if generic != agent_cfg.get("response_selector", ""):
            elements = _match(driver, generic)
            if elements:
                ui.info("   🔁  tuned selector matched nothing; "
                        "read the reply with the generic one")
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


# Stages where "make it editable" means anything.
#
# Exactly the two the registry configures a Canva suffix for, and no more.
# This first shipped including "artwork" and "design" as well, which was
# wrong and showed up on the first real run: the Studio pipeline's design
# stage emits a JSON scene spec for Prism's own renderer, so the follow-up
# asked Canva to "import the image above" when the message above was a CSS
# blob. Canva answered "none", and the turn was pure waste on a conversation
# that had just been asked, twice, for strict JSON.
#
# Both of those stages are internal plumbing of the Studio pipeline. Neither
# is ever the thing a customer opens and edits.
_EDITABLE_STAGES = ("visual", "presentation")


def _make_editable(driver, agent_cfg: dict, stage: str, query: str,
                   responses: list, machine_shaped: bool = False) -> list:
    """Hand the image just generated to Canva, in the same conversation.

    Two prompts, not one. The first asked for the best picture the tool can
    draw; this one asks Canva to wrap that picture in something editable.

    Splitting it is the whole point. Asking for both in a single prompt makes
    Canva COMPOSE the image — a stock template where DALL-E would have
    rendered the scene — so the customer had to choose between a good picture
    and an editable one. Generate first, convert second, and they get both.

    Returns the responses to keep. The Canva reply is appended rather than
    substituted: the first answer holds the image, and dropping it to keep a
    link would lose the artwork the customer actually asked for.
    """
    if stage not in _EDITABLE_STAGES:
        return responses
    if machine_shaped:
        # This stage's answer is parsed by Prism, not read by a person. It was
        # just told to reply with ONLY a JSON object; adding a chat turn after
        # that both wastes a round trip and leaves prose sitting where the
        # parser expects to find the spec.
        return responses
    if not A.wants_canva(query):
        return responses
    if not responses:
        # Nothing was made, so there is nothing to convert. Asking anyway
        # would have Canva invent a design from the prompt alone, which is
        # exactly the template-instead-of-artwork failure this avoids.
        ui.warn("   nothing to make editable — skipping the Canva step")
        return responses

    ui.info("   🎨  asking Canva to make it editable…")
    reply = _reask(driver, agent_cfg, A._CANVA_FOLLOWUP, expect="CANVA LINK:")
    text = "\n\n".join(t for t in reply if t and t.strip()).strip()
    if not text:
        ui.warn("   Canva didn't answer — keeping the image on its own")
        return responses
    if "canva link: none" in text.lower():
        # Said plainly rather than swallowed: the customer asked for something
        # editable and is not getting it, and the reason is one they can fix.
        ui.warn("   the Canva app isn't connected to this ChatGPT account — "
                "connect it there and the design becomes editable next time")
        return responses
    ui.ok("   ✅  editable Canva design created")
    return responses + [text]


def _reask(driver, agent_cfg: dict, prompt: str, expect: str = "",
           wait: int = 0) -> list[str]:
    """Send one follow-up in the SAME tab and re-scrape.

    Used when a stage answered but not in the shape the next stage needs. The
    chat still holds everything it just wrote, so a one-line correction is far
    cheaper — and far likelier to work — than failing the run or starting the
    stage over.

    `wait` overrides the agent's own budget. A one-line correction settles in
    seconds; a whole reel scene is several thousand characters of markup and
    CSS and does not, and the default 60s cut them off mid-stylesheet."""
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
        _smart_wait(driver, agent_cfg,
                    wait or agent_cfg.get("wait_time", 60), expect=expect)
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
        # Keep what actually came back. Without this the one piece of evidence
        # that could explain the failure is discarded at the moment it becomes
        # interesting, and "No JSON found in the agent's reply" is unanswerable
        # — the customer can see JSON on the ChatGPT page and Prism cannot,
        # and there is no way to tell which of them is looking at the truth.
        kept = _keep_failed_spec(sources)
        return "", (f"{why_bad or 'Nothing was written for the renderer.'} "
                    "The art-direction stage has to return the design JSON."
                    + (f" What came back was saved to {kept}" if kept else ""))

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
    spec, why = reel.first_spec(sources)
    if spec is None:
        # The writing stage produced prose instead of a scene spec. Say so
        # plainly — the fix is a routing one, not something to paper over —
        # and KEEP what came back, because it is the only thing that can
        # explain the failure and it disappears with this local variable.
        kept = reel.keep_unparsed(sources)
        if kept:
            ui.warn(f"   saved what came back to {kept}")
        return "", (f"{why or 'Nothing was written for the renderer.'} The "
                    "stage before this one has to return the JSON scene spec "
                    "for the renderer to draw."
                    + (f" What came back was saved to {kept}" if kept else ""))

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
        should_stop=None, failover: bool = True,
        reel_design_stage: str = "", pipeline_files_out: list | None = None,
        motion_design_stage: str = ""):
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
    reel_design_stage: the label of a stage that art-directs a reel, when the
                 caller built the stages itself. That stage's reply is turn one
                 of a conversation — the look and the storyboard — and the
                 scenes are then asked for one at a time in the same tab. A
                 routed run finds this stage on its own; only a caller passing
                 `custom_stages` has to name it.
    motion_design_stage: the same idea as reel_design_stage, for core.motion
                 instead of core.reel_web — that stage's reply is turn one
                 (project, camera, storyboard rows), and core.motion.generate
                 asks for each scene's nodes one at a time in the same tab.
                 Motion has no routed-run auto-detection (it isn't in
                 core.agents' catalogue), so every caller names this stage
                 itself — there is no "a routed run finds this on its own"
                 case the way there is for reel_design_stage.
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
    failover: after the pipeline, hand any stage that produced NOTHING to a
                 different tool from the same category and try once more. On by
                 default, because the failure it covers — a free tier running
                 out at the last stage of a forty-minute run — otherwise costs
                 the whole run. Set False for the retry itself, so a category
                 where every tool is having a bad afternoon cannot recurse.
    pipeline_files_out: a caller-owned list that images/files generated during
                 this run are appended into, on top of being used internally.
                 Exists so a failover retry — which runs this whole function
                 again from scratch for one stage via `custom_stages` — can get
                 the images that ONE stage generated back out, when otherwise
                 they would be harvested into this function's own local
                 `pipeline_files` and vanish with it when the call returns.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from . import files as F

    from . import lang as L

    attachments = attachments or []
    attach_ctx = F.context_block(attachments)

    # "Reply in Gujarati", if the user asked for it. Resolved once per run
    # rather than per prompt: it is the same sentence every time, and reading
    # it here keeps the per-stage code to one `if`.
    answer_language = L.directive(cfg.get("output_language") or "")
    if answer_language:
        ui.info(f"   🌐  tools will answer in "
                f"{L.NAMES.get(cfg['output_language'], '')}")

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
        # The stage that actually WRITES the reel — content's job by
        # PIPELINE_ORDER's own description ("copy, docs, scripts"), brains
        # the fallback this function's own warning below already promises.
        # Not "whichever non-local stage sits closest to studio": visual
        # routinely sits between content and media/studio, and asking visual
        # — an image-direction stage, not a script one — to also carry the
        # script instructions left the actual script (which content wrote
        # correctly) stuck in prose with nothing to turn it into JSON.
        writer = next((i for i in range(studio_at - 1, -1, -1)
                       if stages[i][0] in ("content", "brains")),
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
            client_pics: list[str] = []   # asset names for THEIR files
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
                        client_pics = list(table)
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
                    _web.imagery_instructions(query, bool(asset_list),
                                              attached=client_pics)]))
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

    # A caller that built its own stages (the CLI's /studio) still gets the
    # scene-at-a-time conversation — it names the art-direction stage rather
    # than having it inferred from a renderer that isn't in its list.
    if design_feeder is None and reel_design_stage:
        design_feeder = next((i for i, (st, _, _) in enumerate(stages)
                              if st == reel_design_stage), None)
        if design_feeder:
            script_stage = script_stage or stages[design_feeder - 1][0]

    # Same idea, for core.motion. No routed-run auto-detection exists for
    # it (see motion_design_stage's docstring) — every caller names the
    # stage itself, unconditionally.
    motion_feeder = None
    if motion_design_stage:
        motion_feeder = next((i for i, (st, _, _) in enumerate(stages)
                              if st == motion_design_stage), None)

    local_reel_at = next((i for i, (_, an, _) in enumerate(stages)
                          if (A.resolve_agent("", an) or {}).get("local") == "reel"),
                         None)
    spec_feeder = None      # stage index that must answer in JSON, not prose
    if local_reel_at is not None:
        # Same fix as the Studio writer above, same reason: content is the
        # stage that writes the script (visual only ever describes imagery),
        # so search for content/brains specifically rather than accepting
        # whatever non-local stage happens to sit nearest to media.
        feeder = next((i for i in range(local_reel_at - 1, -1, -1)
                       if stages[i][0] in ("content", "brains")),
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
    # images/docs/decks/code GENERATED by earlier stages. Caller-supplied when
    # given, and mutated in place (never rebound) from here down, so a caller
    # holding onto pipeline_files_out sees every harvest as it happens rather
    # than only whatever this function's own local name pointed to last.
    pipeline_files: list[dict] = (
        pipeline_files_out if pipeline_files_out is not None else [])
    # A freshly launched browser opens on one blank tab — reuse it for stage
    # one. A REUSED browser still has the previous run's result tabs open;
    # always open a new tab in that case so nothing gets navigated away.
    first_tab = fresh

    def stopped() -> bool:
        return bool(should_stop and should_stop())

    # Stages that produced nothing, and why. Read by the failover pass after
    # the loop; see _retry_failed_stages().
    failures: dict[str, dict] = {}
    # Stages that DID produce something, but the cap ran out while the tool
    # was still writing — see the `timed_out` branch of _smart_wait's caller
    # below. Kept separate from `failures`: the answer is real and stays in
    # all_responses (never thrown away just for arriving at the deadline),
    # but "got something" and "got the finished answer" are not the same
    # claim, and this is what lets the run say so instead of quietly calling
    # a partial answer done.
    incomplete: dict[str, dict] = {}

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
                # Keep the internal reel_<timestamp> working name, but make
                # the customer-facing artifact describe the original request.
                try:
                    from . import config as _config
                    saved = _config.save_artifact(out, query, kind="reel")
                    ui.info(f"   💾  saved to {saved}")
                except Exception:                       # noqa: BLE001
                    pass       # rendering succeeded; copying is best-effort
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
                _open_tab(driver, agent_name)
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
            # "artwork" is Prism Studio's imagery stage. It makes pictures, so
            # it is a producer — but it is here for a second reason: it is the
            # only stage in a reel that can SEE, and it is asked on the way
            # past what the client's own pictures actually show. Nobody else
            # ever looks at them, and "make a reel, here are two screenshots"
            # is what a customer types.
            producer = stage in ("visual", "media", "development",
                                 "presentation", "format", "artwork")
            include_attachment = bool(attachments) and (
                stage_idx == 0 or not prior or producer)
            # Producers also receive files GENERATED by earlier stages
            # (e.g. the logo the visual stage just made) — those can't
            # travel in a text handoff at all.
            send_files = (attachments if include_attachment else []) + \
                         (pipeline_files if producer else [])
            if send_files:
                _upload_files(driver, agent_cfg, send_files, agent_name)

            # Relay hand-off: forward ONLY the most recent stage's output.
            # Every agent is instructed (below) to fold the key findings of
            # everything before it into its own answer, so the latest output
            # already carries the whole chain — re-sending every older stage
            # would only bloat and slow down the prompt.
            # The person's own words first, before the attachments and before
            # the previous stage's handoff. Whatever else gets crowded out of
            # a long prompt, this must not be it.
            context = _intent_block(query)
            context += attach_ctx if include_attachment else ""
            if producer and pipeline_files:
                # Not always pictures any more — _harvest_files also lands
                # generated documents, decks, code and archives here, so the
                # wording has to fit whatever actually came through rather
                # than always saying "image".
                names = ", ".join(f["name"] for f in pipeline_files)
                kinds = {f.get("kind", "image") for f in pipeline_files}
                noun = "image file(s)" if kinds == {"image"} else "file(s)"
                context += (
                    f"An earlier pipeline stage GENERATED these {noun}, "
                    f"uploaded to this chat: {names}. Use them as assets in "
                    "what you produce — do not recreate them from scratch.\n\n"
                )
            # The art director is handed the script verbatim further down, so
            # the relay's "here is the previous stage" block adds nothing it
            # needs — and the stage immediately before it is the image maker,
            # whose text is chatter about the pictures. Forwarding that as the
            # brief is how a design ends up answering the wrong question.
            # Motion's storyboard stage is self-contained the same way and
            # gets the same treatment, whether or not it happens to have a
            # preceding stage.
            if stage_idx == design_feeder or stage_idx == motion_feeder:
                prior = []

            if prior:
                prev_stage, prev_texts = prior[-1]
                prev_text = "\n\n".join(t for t in prev_texts if t.strip())
                if len(prev_text) > _MAX_FORWARD_CHARS:
                    prev_text = prev_text[-_MAX_FORWARD_CHARS:]
                context += (_context_header(agent_cfg, prev_stage)
                            + prev_text
                            + _context_footer(agent_cfg))

            # The stage feeding a LOCAL renderer is machine-read, so it gets
            # the final-stage rules even though a stage follows it. The normal
            # handoff rules below demand a prose "HANDOFF FOR …" section as the
            # LAST thing in the answer — flatly contradicting "reply with only
            # a JSON object", and a model resolving that contradiction writes
            # the handoff and drops the spec. That is exactly how a run ends up
            # with nothing to render.
            # Is this stage's answer read by a person, or parsed by something?
            # It decides whether the user's "write back in Gujarati" setting
            # applies further down: a JSON scene spec or an Apollo filter block
            # translated into another language parses as nothing at all.
            machine_shaped = (
                stage_idx in machine_stages
                or stage_idx == spec_feeder
                or (stage_idx + 1 < len(stages)
                    and A.AGENT_REGISTRY.get(stages[stage_idx + 1][1], {})
                         .get("handoff_spec")))

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
            elif machine_shaped and stage_idx + 1 < len(stages):
                # The next tool is not a chat box — it is a search screen with
                # its own idea of what an input looks like (Apollo's fields cap
                # at 200 characters and ignore prose). Such a tool ships the
                # exact handoff it can parse, and it replaces the generic prose
                # rules below wholesale rather than being appended to them:
                # asking for a prose summary AND a filter block gets the prose.
                handoff = A.AGENT_REGISTRY[stages[stage_idx + 1][1]]["handoff_spec"]
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
                handoff = (
                    _natural_handoff(nxt_agent, final=False)
                    if _is_natural(agent_cfg) else
                    "\n\nSTRICT PIPELINE RULES:\n" + "\n".join(
                        f"{i}. {r}" for i, r in enumerate(rules, 1)))
            else:
                handoff = _natural_handoff("", final=True) if _is_natural(agent_cfg) else (
                    "\n\nSTRICT PIPELINE RULES:\n"
                    "You are the FINAL stage. The context above is your complete "
                    "brief — everything important from earlier stages is already "
                    "distilled into it. Perform ONLY the task above and deliver the "
                    "polished final result. Do not add any handoff or summary "
                    "section, and do not ask any follow-up questions."
                )

            # The user asked for answers in their own language. Appended last
            # so it is the final instruction the model reads, and skipped for
            # machine-shaped stages, where translating the output would break
            # whatever is about to parse it.
            if answer_language and not machine_shaped:
                handoff += "\n\n" + answer_language

            if agent_cfg.get("search_tool") == "apollo":
                # Deliberately does NOT get `context`. That blob opens with
                # "Context from the previous pipeline stage (RESEARCH) —" and
                # is thousands of characters long; handing it to Apollo is the
                # exact call that failed with "Value too long". What Apollo
                # needs out of the previous stage is the filter block, which
                # _run_apollo parses for itself — and the questions carry the
                # user's own wording as the fallback if that block is missing.
                stage_responses = _run_apollo(
                    driver, agent_cfg, stage,
                    _bmp_safe("\n".join(questions) + "\n" + context))
            elif agent_name == "NotebookLM":
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
                    from . import reel_web as _web
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
                    # manifest() answers for the empty case too, and must: a
                    # design stage told nothing about pictures designs around
                    # the ones it assumes are there. See assets.NO_ARTWORK.
                    listing = _assets.manifest(table)
                    # What the imagery stage saw in the client's own pictures.
                    # Without it the art director knows a file's size and
                    # nothing else, and places it as decoration — which is all
                    # it knows the picture is.
                    said = _web.read_pictures(all_responses.get("artwork") or [])
                    if said:
                        listing = _web.describe_pictures(listing, said)
                        for name, line in said.items():
                            ui.info(f"   👁   asset:{name} — {line[:70]}")
                    if table:
                        ui.info(f"   🖼️   {len(table)} asset(s) for the design: "
                                + ", ".join(table))
                    else:
                        ui.warn("   🎨  no artwork — the design stage is being "
                                "told to build this from type and colour alone")
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

                # A tool's own capabilities are described to the ROUTER, which
                # then decides what to ask for — a nudge, not an instruction,
                # and router.py explicitly tells the model to weigh field notes
                # above those descriptions. So a capability that only exists
                # because the user connected something (ChatGPT + the Canva
                # app) needs saying literally, in the prompt, every time that
                # agent runs that stage. Keyed by stage so ChatGPT's brains
                # turn is not told to go and make a design.
                suffix = _resolve_suffix(agent_cfg, stage, query)
                if suffix:
                    questions = [q + "\n\n" + suffix for q in questions]

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
                            # Let this answer finish before sending the next
                            # prompt. should_stop was missing here, unlike the
                            # identical call below for the stage's final wait
                            # — Stop went unpolled for up to 120s per gap in a
                            # multi-prompt stage, which is where "Stop takes
                            # minutes" was coming from.
                            _smart_wait(driver, agent_cfg, 120,
                                       should_stop=should_stop)
                    except Exception as e:
                        ui.err(f"   prompt error: {e}")

                wait = agent_cfg.get("wait_time", 60)
                # A caller that knows what a finished answer looks like says
                # so (e.g. /email needs "SUBJECT:"), and a mid-answer pause
                # can no longer end the wait early.
                expect = (routing.get(stage) or {}).get("expect", "")
                if stage_idx == design_feeder:
                    expect = '"css"'
                elif stage_idx == motion_feeder:
                    expect = '"storyboard"'
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

                # THE REST OF THE DESIGN CONVERSATION.
                #
                # What came back is turn one — the look and the storyboard.
                # Every scene is now asked for on its own turn, in this same
                # tab, and laid out in a real browser before the next one is
                # asked for. Two things this buys, and the first is the whole
                # reason for it:
                #
                #   · budget. Asked for a whole reel in one reply, a model
                #     spread a few thousand characters over seven scenes and
                #     each one came out at ~278 characters, which is a
                #     headline and a subhead. That is a slide, and no prompt
                #     about motion can fix it because there is nothing in it
                #     to move. A scene with a whole reply to itself gets 20x
                #     the room.
                #   · correction that lands. A fault used to come back as
                #     "scene 3's headline is off the frame" against a reply
                #     the model had long since moved past. Now it is simply
                #     "this one", while the scene is still the subject.
                if stage_idx == design_feeder and texts:
                    # A scene is several thousand characters of markup and
                    # CSS. The agent's ordinary budget is sized for an answer,
                    # not for that, and cutting one off mid-stylesheet costs
                    # the whole scene.
                    scene_wait = max(int(agent_cfg.get("wait_time", 60)), 180)

                    def _ask(prompt, expect=_web.SCENE_EXPECT):
                        got = _reask(driver, agent_cfg, prompt, expect=expect,
                                     wait=scene_wait)
                        return got[-1] if got else ""

                    script_txt = "\n".join(all_responses.get(script_stage) or [])
                    try:
                        spec = _web.build_spec(
                            texts[-1], _ask,
                            script=script_txt, assets=listing,
                            assets_table=design_assets,
                            check=_web.inspect,
                            log=lambda m: ui.info(f"   {m}"),
                            should_stop=should_stop,
                            on_scene=lambda i, n: emit(
                                "reel_scene", {"index": i, "total": n}))
                    except Exception as e:
                        # Turn one did not parse, so there is no design to
                        # build scenes against. Whatever came back is kept as
                        # it is: it may still be a whole single-reply design
                        # from a model that answered the old way, and the
                        # renderer will happily parse that.
                        ui.err(f"   {e}")
                        kept = _keep_failed_spec(texts)
                        if kept:
                            ui.info(f"   what came back was saved to {kept}")
                    else:
                        import json as _json
                        ui.ok(f"   {len(spec['scenes'])} scene(s) written and "
                              "laid out clean at 1080x1920")
                        # Told not to change a word is not the same as
                        # prevented. Flagged, not blocked: a design may
                        # legitimately split a headline across elements.
                        for line in _web.script_drift(spec, script_txt)[:4]:
                            ui.warn(f'   the design dropped or reworded: '
                                    f'"{line}"')
                        stage_responses = [_json.dumps(spec, ensure_ascii=False)]

                # Same idea as design_feeder just above, for core.motion:
                # turn one is the storyboard, every scene's nodes are then
                # asked for one at a time in the same tab. No script/assets
                # substitution here — core.motion.generate's prompts are
                # self-contained, unlike reel_web's ASSET_TOKEN/BRAND_TOKEN
                # placeholders — so there is nothing to fill in before this
                # runs, only after the reply comes back.
                if stage_idx == motion_feeder and texts:
                    from . import motion as _motion_pkg
                    from .motion import generate as _motion
                    from .motion import inspect as _motion_inspect
                    from . import assets as _assets

                    scene_wait = max(int(agent_cfg.get("wait_time", 60)), 180)

                    def _ask(prompt, expect=_motion.SCENE_EXPECT):
                        got = _reask(driver, agent_cfg, prompt, expect=expect,
                                     wait=scene_wait)
                        return got[-1] if got else ""

                    # Same extraction Studio's design_feeder uses — logo/
                    # brand marks cut out of whatever the user attached, so
                    # the model can place the REAL mark via an "image" node
                    # rather than approximate one out of shapes and text.
                    motion_assets_table = {}
                    try:
                        motion_assets_table = _assets.collect(attachments or [])
                        if motion_assets_table:
                            ui.info(f"   🖼️   {len(motion_assets_table)} "
                                    "asset(s) prepared from the artwork: "
                                    + ", ".join(motion_assets_table))
                    except Exception as e:
                        ui.warn(f"   couldn't prepare the artwork ({e})")
                    motion_assets_listing = _assets.manifest(motion_assets_table)

                    try:
                        spec = _motion.build_spec(
                            texts[-1], _ask,
                            assets=motion_assets_listing,
                            assets_table=motion_assets_table,
                            check=_motion_inspect.inspect,
                            log=lambda m: ui.info(f"   {m}"),
                            should_stop=should_stop,
                            on_scene=lambda i, n: emit(
                                "motion_scene", {"index": i, "total": n}))
                    except Exception as e:
                        # Turn one did not parse, so there is no storyboard
                        # to build scenes against. Kept as-is: it may still
                        # be a whole single-reply spec from a model that
                        # answered the old way, and the renderer will
                        # happily parse that.
                        ui.err(f"   {e}")
                        kept = _keep_failed_spec(texts)
                        if kept:
                            ui.info(f"   what came back was saved to {kept}")
                    else:
                        import json as _json
                        try:
                            validated = _motion_pkg.validate_motion_spec(spec)
                        except Exception as e:
                            ui.err(f"   storyboard assembled but did not "
                                   f"validate ({e})")
                        else:
                            ui.ok(f"   {len(validated['scenes'])} scene(s) "
                                  "written")
                            stage_responses = [_json.dumps(validated, ensure_ascii=False)]

                # This stage feeds a renderer, so "it answered" isn't enough —
                # it has to have answered in JSON. Prefer a capture that
                # actually parses (the spec is often shorter than the prose
                # around it), and if none does, ask once more in the same tab
                # before the run reaches the renderer with nothing to draw.
                #
                # `texts` can be empty here even though the tab is still very
                # much alive — a spec is long, and the base wait above is
                # sized for an ordinary reply, not one that is also rendering
                # a DALL-E image on the side. Gating this whole block on
                # `and texts` meant that exact case — nothing captured yet,
                # tool still typing — skipped the one retry that exists for
                # it and fell straight through to the renderer with nothing
                # to draw, instead of asking again in the tab that was right
                # there.
                if stage_idx == spec_feeder:
                    from . import reel as _reel
                    spec_texts = [t for t in texts if _reel.has_spec(t)] if texts else []
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
                        if texts:
                            ui.warn(f"{agent_name} wrote about the reel instead "
                                    "of writing the spec — asking again for "
                                    "JSON only")
                        else:
                            ui.warn(f"{agent_name} was still writing when "
                                    "Prism stopped waiting — asking again for "
                                    "JSON only")
                        emit("retry", {"stage": stage, "reason": "no scene spec"})
                        again = _reask(
                            driver, agent_cfg,
                            "That reply cannot be rendered. Send the scene "
                            "spec itself now: reply with ONLY the JSON "
                            "object, wrapped in a ```json fenced code block "
                            "and nothing else — no preamble, no handoff. "
                            "Keep the fence: without it the chat window eats "
                            "the asterisks in your CSS and wraps long URLs "
                            "onto new lines, which is what broke the last "
                            "attempt.",
                            expect='"scenes"')
                        fixed = [t for t in again if _reel.has_spec(t)]
                        if fixed:
                            stage_responses = [fixed[-1]]
                            ui.ok("   got the scene spec on the second ask")
                        else:
                            ui.err("   still no scene spec — the renderer will "
                                   "have nothing to draw")
            # The artwork exists and is good. NOW make it editable — a second
            # prompt in the same chat rather than a different first prompt.
            stage_responses = _make_editable(
                driver, agent_cfg, stage, query, stage_responses,
                machine_shaped=machine_shaped)

            if stage_responses:
                ui.info(f"   📥  captured {sum(len(t) for t in stage_responses)} chars")

            all_links[stage] = driver.current_url
            all_responses[stage] = stage_responses

            # Image-making stages: pull the generated images off the page so
            # later stages can actually use them (text handoffs can't) —
            # and, regardless of whether a later stage exists, so the file
            # itself survives. It used to only be harvested when there was a
            # next stage to hand it to (or a failover retry's
            # pipeline_files_out asked explicitly), which meant a plain
            # "generate me an image" task — one stage, no retry — never had
            # its output downloaded at all: only the tool's own hosted link
            # remained, gone the moment that browser tab or session closed.
            if stage in ("visual", "media", "artwork"):
                made = _harvest_images(driver, agent_cfg, stage)
                if stage == "artwork":
                    # A reel wants a few strong images, not a contact sheet —
                    # and every extra one is another asset the art director
                    # has to find a place for.
                    from . import reel_web as _rw
                    made = made[:_rw.MAX_GENERATED]
                if made:
                    # In place, never rebound — see pipeline_files_out above.
                    pipeline_files[:] = (pipeline_files + made)[-6:]
                    ui.info(f"   🖼️   harvested {len(made)} generated image(s) "
                            "for the next stages")
                    _save_artifacts(made, query, stage)
            # Same idea for the producer stages whose deliverable is a
            # document, deck or code file rather than a picture — without
            # this, only their scraped TEXT reply ever reached a later stage,
            # and a genuinely generated PDF/DOCX/PPTX/code file/zip never did.
            elif stage in ("development", "presentation", "format"):
                made_files = _harvest_files(driver, agent_cfg, stage)
                if made_files:
                    pipeline_files[:] = (pipeline_files + made_files)[-6:]
                    ui.info(f"   📎  harvested {len(made_files)} generated "
                            "file(s) for the next stages")
                    _save_artifacts(made_files, query, stage)

            if stage_responses:
                ui.ok(f"captured {len(stage_responses)} response(s)")
                emit("stage_done", {"stage": stage, "count": len(stage_responses),
                                    "snippet": stage_responses[0][:200],
                                    "texts": stage_responses, "url": driver.current_url,
                                    "timed_out": timed_out})
                if timed_out:
                    # Real output, kept in all_responses above — but the cap,
                    # not the tool, ended this wait, so what got captured may
                    # be a sentence cut off mid-word rather than the finished
                    # answer. Flagged here rather than silently treated as a
                    # normal "done" stage; see the summary after the loop.
                    incomplete[stage] = {"agent": agent_name,
                                         "questions": questions}
            else:
                # Nothing came back. Before reporting that as a scrape miss,
                # ask the three far more likely questions: has this tool run
                # out, is this a sign-in wall, and — if neither, and our prompt
                # is visibly on the page — did the site change its markup?
                spent = _looks_exhausted(driver)
                blocked = (spent or _looks_signed_out(driver)
                           or _looks_unreadable(driver, questions, timed_out))
                if blocked:
                    ui.err(blocked)
                else:
                    ui.warn("no response scraped, but link saved")
                failures[stage] = {
                    "agent": agent_name, "questions": questions,
                    "reason": blocked or "it returned nothing",
                    "exhausted": bool(spent)}
                emit("stage_done", {"stage": stage, "count": 0, "texts": [],
                                    "url": driver.current_url,
                                    "timed_out": timed_out,
                                    "blocked": blocked,
                                    "exhausted": bool(spent)})
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

            # A closed browser is not a failed STAGE, it is a failed RUN.
            #
            # Every stage after this one would open the same dead session,
            # raise the same error and take its own timeout getting there — so
            # a run stopped at step two reports five identical failures over
            # several minutes, and the customer has to work out which of them
            # was the real one. Worse, the failover pass would then try each
            # of them again on a second tool, through the same dead driver.
            #
            # Stop here and say so once, keeping everything already finished.
            if _browser_is_gone(ex):
                ui.err("   the browser window is gone — stopping here and "
                       "keeping everything finished so far")
                emit("browser_lost", {"stage": stage, "error": str(ex),
                                      "done": len(all_responses)})
                return all_responses, all_links

            failures[stage] = {"agent": agent_name, "questions": questions,
                               "reason": str(ex), "exhausted": False}

    if incomplete:
        # A transient ui.warn already fired for each of these the moment its
        # cap ran out (mid-run, easy to miss); this is the persistent version
        # — printed once, right where the CLI's "Results" summary and the
        # GUI's completion state read from next — so a partial answer is
        # never confused for a finished one just because it showed up on
        # time. The text itself is untouched: it is already sitting in
        # all_responses, kept rather than thrown away.
        ui.warn(f"{len(incomplete)} stage(s) may be incomplete — the tool's "
                "time ran out before its answer finished (its tab is still "
                "open and may finish there): "
                + ", ".join(f"{s} ({i['agent']})" for s, i in incomplete.items()))
        emit("run_incomplete", {"stages": incomplete})

    if failover and failures and not stopped():
        _retry_failed_stages(
            failures, cfg, all_responses, all_links,
            attachments=attachments, query=query, emit=emit,
            should_stop=should_stop, stages=stages,
            pipeline_files=pipeline_files, brand=studio_brand)

    return all_responses, all_links


# ── when a tool cannot finish ────────────────────────────────────────────────

def _retry_failed_stages(failures: dict, cfg: dict, all_responses: dict,
                         all_links: dict, *, attachments, query, emit,
                         should_stop, stages=None, pipeline_files=None,
                         brand=None) -> None:
    """Give each empty stage to a different tool.

    The failure this exists for: forty minutes into a run, the free tier on
    whichever tool drew the heaviest stage runs out, and the customer is handed
    a pipeline that did nine tenths of the work and produced nothing usable.
    Another tool in the same category can almost always finish it.

    **It runs after the pipeline, not inside it, and that is a real
    limitation.** A stage that failed in the MIDDLE has already handed nothing
    to the stages after it, so those ran on thinner context and are not re-run
    here. The last stage — which is where a quota runs out, because that is
    where the most has been asked — is fully recovered. Doing better means
    retrying inline, which means restructuring a 500-line loop whose index-keyed
    maps (machine_stages, spec_feeder, design_feeder) all shift if the stage
    list changes underneath it.

    One case of that limitation IS fixed here, though, because it doesn't need
    the restructuring: a LOCAL renderer (Prism Reel, Prism Studio) runs as one
    self-contained function call, not a browser tab holding its place in the
    stage list — so if `visual` failed and only came back through this retry,
    the renderer already ran without the images it was waiting on, and calling
    that same function again is safe. See `_rerender_local_after_recovery()`,
    called at the end of this function once every stage here has been tried.

    Never raises: this is a rescue attempt, and a rescue that takes down the
    results it was rescuing would be worse than not trying.
    """
    recovered: set[str] = set()
    for stage, info in list(failures.items()):
        if should_stop and should_stop():
            return
        # A later pass may already have filled this in.
        if all_responses.get(stage):
            continue

        tried = [info.get("agent")]
        for alternative in A.alternatives_for(stage, tried, cfg):
            if should_stop and should_stop():
                return
            ui.rule(f"{stage.upper()}  ·  retrying with {alternative}",
                    style="yellow")
            ui.warn(f"   {info.get('agent')} couldn't finish: {info['reason']}")
            emit("stage_failover", {
                "stage": stage, "failed": info.get("agent"),
                "agent": alternative, "reason": info["reason"],
                "exhausted": info.get("exhausted", False)})
            # This stage's own harvested images, pulled back out of the
            # nested run() below rather than lost with its own local
            # pipeline_files when that call returns — see pipeline_files_out.
            recovered_files: list = []
            try:
                responses, links = run(
                    {}, cfg,
                    attachments=attachments,
                    custom_stages=[(stage, alternative, info["questions"])],
                    query=query,
                    # The file-analysis pre-stage would re-read every
                    # attachment before the one prompt we actually want.
                    chatgpt_analysis=False,
                    should_stop=should_stop,
                    # One level only. Without this a category where every tool
                    # is having a bad afternoon retries itself forever.
                    failover=False,
                    pipeline_files_out=recovered_files)
            except Exception as e:                       # noqa: BLE001
                ui.err(f"   {alternative} also failed: {e}")
                continue

            texts = responses.get(stage) or []
            if texts:
                all_responses[stage] = texts
                if links.get(stage):
                    all_links[stage] = links[stage]
                if recovered_files and pipeline_files is not None:
                    pipeline_files[:] = (pipeline_files + recovered_files)[-6:]
                recovered.add(stage)
                ui.ok(f"   ✅  {alternative} finished the {stage} step")
                emit("stage_recovered", {
                    "stage": stage, "agent": alternative,
                    "failed": info.get("agent"),
                    "texts": texts, "url": links.get(stage, "")})
                break
            ui.warn(f"   {alternative} returned nothing either")
        else:
            emit("stage_unrecovered", {
                "stage": stage, "failed": info.get("agent"),
                "reason": info["reason"]})

    if recovered and stages:
        _rerender_local_after_recovery(recovered, stages, cfg, all_responses,
                                       all_links, attachments=attachments,
                                       pipeline_files=pipeline_files,
                                       brand=brand, emit=emit)


def _rerender_local_after_recovery(recovered: set, stages, cfg: dict,
                                   all_responses: dict, all_links: dict, *,
                                   attachments, pipeline_files, brand, emit):
    """Give a local renderer a second pass once a stage feeding it has been
    recovered by failover.

    A LOCAL stage (Prism Reel, Prism Studio) runs synchronously, in its own
    turn in the main loop — so if `visual` failed there, the renderer already
    ran, without the images `visual` was going to hand it. `_retry_failed_stages`
    may then go on to recover `visual` through a different tool, but that
    happens after the whole pipeline finished; nothing about a plain text/URL
    handoff makes a video that already rendered start using pictures that
    showed up afterwards. Calling the same renderer function again is cheap
    and safe — unlike the browser stages, it isn't holding a tab or an index
    into `stages` that a second pass could disturb — so it is what gets called
    here rather than left as the limitation the rest of this failover accepts.

    Walks `stages` in order rather than jumping straight to the renderer, so a
    LOCAL stage only gets redone when something that actually ran BEFORE it
    was among the ones just recovered — a renderer with no recovered stage
    upstream of it has nothing new to draw from and is left alone.
    """
    seen_recovered: set[str] = set()
    for stage, agent_name, _questions in stages:
        if stage in recovered:
            seen_recovered.add(stage)
            continue
        if not seen_recovered or stage not in all_links:
            continue                    # nothing upstream recovered, or this
                                         # stage never ran in the first place
        agent_cfg = A.resolve_agent(stage, agent_name)
        if not (agent_cfg and agent_cfg.get("local")):
            continue
        ui.rule(f"{stage.upper()}  ·  re-rendering", style="yellow")
        ui.info(f"   🔁  {', '.join(sorted(seen_recovered))} came back after "
                f"{stage} finished the first time — rendering it again with "
                "what showed up")
        prior_text = [t for ts in reversed(list(all_responses.values()))
                      for t in ts if t.strip()]
        try:
            out, note = _run_local(agent_cfg["local"], prior_text,
                                   (attachments or []) + (pipeline_files or []),
                                   cfg, stage, brand=brand)
        except Exception as e:                            # noqa: BLE001
            ui.err(f"   re-render failed: {e}")
            continue
        if out:
            all_responses[stage] = [note]
            all_links[stage] = out
            ui.ok(f"   {note}")
            emit("stage_done", {"stage": stage, "count": 1, "texts": [note],
                                "url": out, "timed_out": False})
        else:
            ui.err(f"   re-render failed: {note}")


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
