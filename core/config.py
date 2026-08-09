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
