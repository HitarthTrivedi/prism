"""
Prism — persistent configuration
────────────────────────────────
Everything the user sets during onboarding lives in ~/.prism/config.json so it
survives across runs and is independent of the current working directory.
Once written, Prism never asks for these again (unless the user edits them).
"""
import os
import json

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".prism")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
RUNS_DIR = os.path.join(CONFIG_DIR, "runs")
# Where a generated file goes to be found again. RUNS_DIR is hidden on macOS
# and easy to walk past on Windows/Linux, and anything only harvested
# mid-pipeline used to land in the OS temp directory — gone the moment Prism,
# or the OS, cleans up. A rendered video, a generated image, a written
# document: all of it belongs somewhere a customer looks without being told
# where — same idea as gerber_dialog.py's own "Prism Gerber" folder on the
# Desktop, generalised past just Gerber's CSVs.
ARTIFACTS_DIR = os.path.join(os.path.expanduser("~/Desktop"), "Prism Artifacts")

DEFAULT = {
    "api_key": "",        # Groq key (gsk_...)
    "profile": "",        # free-text "what do you do" — steers routing
    "agents": {},         # {category: agent_name} — only categories the user enabled
    "chrome_version": "", # pinned Chrome major version; "" = auto-detect
    "onboarded": False,
    "model": "llama-3.3-70b-versatile",
}


def load() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {**DEFAULT, **data}
        except Exception:
            # An unreadable config used to fall through to DEFAULT in silence,
            # which looks exactly like a fresh install: the user is asked to
            # onboard again and concludes Prism "forgot" their key. Keep the
            # bad file — it still contains their API key and agent choices,
            # recoverable by hand — and leave a trail saying so.
            _quarantine(CONFIG_PATH)
    return dict(DEFAULT)


def _quarantine(path: str) -> str:
    """Move a file that failed to parse aside, and say where it went."""
    import time
    kept = f"{path}.corrupt-{int(time.time())}"
    try:
        os.replace(path, kept)
    except OSError:
        return ""
    print(f"⚠️  {os.path.basename(path)} could not be read and was kept as "
          f"{kept}\n    Starting from defaults — your settings are still in "
          f"that file if you need them back.")
    return kept


def save(cfg: dict) -> None:
    """Write the config so that an interrupted write cannot destroy it.

    The old version truncated config.json and then wrote into it. A crash,
    a full disk or a pulled power cable anywhere in between left a half-written
    file, load() silently discarded it, and the user's API key, profile and
    agent choices were gone — from a routine settings save. Writing a complete
    temporary file first and swapping it in with os.replace (atomic on POSIX
    and on Windows) means the config is only ever the old one or the new one.
    """
    # STALE-COPY GUARD. The GUI keeps a cfg dict in memory and hands copies to
    # dialogs that live a while; a dialog saving its OWN older copy back would
    # blank fields the user set meanwhile — most painfully the Groq key, the
    # onboarded flag and the agent map, which reads as "why do I have to set
    # Prism up AGAIN every launch". So before writing, any of those three this
    # cfg would clear but that are still populated on disk are kept from disk.
    # A real new value still overrides; only an accidental blanking is refused.
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
                _disk = json.load(_f)
            cfg = dict(cfg)
            for _k in ("api_key", "onboarded", "agents"):
                if not cfg.get(_k) and _disk.get(_k):
                    cfg[_k] = _disk[_k]
    except Exception:
        pass        # an unreadable disk copy must not stop a legitimate save
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = f"{CONFIG_PATH}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.flush()
            # The rename is atomic, but only orders against data that has
            # actually reached the disk — without this the swap can land while
            # the contents are still buffered, and a power loss then leaves an
            # empty file where the config used to be.
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)  # key is sensitive — owner-only, before it lands
        except OSError:
            pass
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("api_key")) and bool(cfg.get("onboarded"))


def active_agents(cfg: dict) -> dict:
    """Categories the user actually assigned an agent to."""
    return {k: v for k, v in (cfg.get("agents") or {}).items() if v}


def save_run(record: dict, runs_dir: str = "") -> str:
    """Persist one query's routing + responses to <runs_dir>/run_<ts>.json.

    `runs_dir` defaults to ~/.prism/runs, which is where the CLI has always
    written and still does. The GUI passes a per-member folder instead when
    the copy belongs to a company team, so one person's history does not land
    in another's — see prism_gui/workspace.py.
    """
    import time
    runs_dir = runs_dir or RUNS_DIR
    os.makedirs(runs_dir, exist_ok=True)
    path = os.path.join(runs_dir, f"run_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    return path


def _clean_name(text: str, limit: int = 60) -> str:
    """Strip a string down to something safe as a Windows file or folder
    name: no reserved characters, no unprintables, collapsed whitespace,
    trimmed trailing dots/spaces (Windows rejects both)."""
    illegal = '<>:"/\\|?*'   # reserved on Windows; harmless to strip elsewhere
    clean = "".join(c for c in (text or "").strip()
                    if c not in illegal and c.isprintable())
    return " ".join(clean.split())[:limit].strip(" .")


def _artifact_stem(prompt: str, kind: str) -> str:
    """A filename a person can actually read in Finder/Explorer without
    opening it: what they asked for, what kind of thing this is, and when —
    not a sanitized-to-underscores prompt fragment glued to a raw unix
    timestamp."""
    import datetime
    clean = _clean_name(prompt)
    when = datetime.datetime.now().strftime("%Y-%m-%d %I-%M %p")
    label = (kind or "artifact").replace("_", " ").capitalize()
    return " - ".join(p for p in (clean, label, when) if p)


def artifact_task_dir(task: str) -> str:
    """The per-task subfolder under ARTIFACTS_DIR that `save_artifact()` also
    writes into when passed this same `task` string — grouping everything one
    New Task (or one BOQ/Gerber/quote job) produced into one folder a customer
    can open, instead of every image/doc/video for every run landing loose in
    one ever-growing list.

    Exposed on its own (not only through save_artifact) for a caller whose
    deliverable is a whole directory tree — Gerber's cleaned-copy output,
    which is a folder of layers plus a report and preview images, not a
    single file `shutil.copy2` can land in one call.

    Empty `task` returns ARTIFACTS_DIR itself: today's flat top-level
    behavior, so a caller nobody has updated yet keeps working unchanged.
    """
    folder = (os.path.join(ARTIFACTS_DIR, _clean_name(task, limit=80) or "Task")
             if task else ARTIFACTS_DIR)
    os.makedirs(folder, exist_ok=True)
    return folder


def save_artifact(src_path: str, prompt: str, kind: str = "artifact",
                  link: str = "", task: str = "") -> str:
    """Copy a file an agent generated into the one folder a customer will
    actually look in again — see ARTIFACTS_DIR above for why this exists.

    A copy, not a move: whatever already has `src_path` (a pipeline stage,
    the render worker) keeps working with the path it knows, and losing this
    copy afterwards (a full disk, a permissions slip) never breaks the
    caller's own view of what it produced.

    `link`, when given, is the live tab URL of the AI conversation that made
    this — real for a ChatGPT/Claude stage, empty for a local render (Reel,
    Motion) that was never a chat at all. Written as a tiny sidecar next to
    the artifact rather than baked into the filename, since a URL is
    something to open, not something to read at a glance in Finder/Explorer.

    `task`, when given, is the run/job this artifact belongs to (the same
    string already used to title it in History and in its own filename) —
    see `artifact_task_dir()`. Everything from one task then lands in one
    subfolder instead of loose at the top level.
    """
    dest_dir = artifact_task_dir(task)
    _, ext = os.path.splitext(src_path)
    stem = _artifact_stem(prompt, kind)
    dest = os.path.join(dest_dir, f"{stem}{ext}")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(dest_dir, f"{stem}_{n}{ext}")
    import shutil
    shutil.copy2(src_path, dest)
    if link:
        try:
            with open(dest + ".link.txt", "w", encoding="utf-8") as f:
                f.write(link.strip() + "\n")
        except OSError:
            pass   # the artifact itself is already safely saved either way
    return dest
