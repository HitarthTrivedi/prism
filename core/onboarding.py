"""
Prism — first-run onboarding
────────────────────────────
Runs once. Collects the Groq key, the user's profile ("what do you do"), and
one agent per category they care about; explains the mandatory login step; and
offers a dummy (dry) run or a real run. Everything is persisted to ~/.prism so
the user is never asked again.
"""
from __future__ import annotations

from . import agents as A
from . import config as C
from . import ui

try:
    import questionary
    from questionary import Style as _QStyle
    _Q = True
    _QSTYLE = _QStyle([
        ("qmark", "fg:#EF4B77 bold"),
        ("question", "bold"),
        ("answer", "fg:#4CD9B4 bold"),
        ("pointer", "fg:#FF8A4B bold"),
        ("highlighted", "fg:#FF8A4B bold"),
        ("selected", "fg:#4CD9B4"),
    ])
except Exception:
    questionary = None   # keep the name bound so `if _Q:`-guarded refs resolve
    _Q = False
    _QSTYLE = None

SKIP = "— skip this category —"


def _ask_text(msg: str, default: str = "", secret: bool = False,
              multiline: bool = False) -> str:
    if _Q:
        fn = questionary.password if secret else questionary.text
        kwargs = {"style": _QSTYLE, "default": default if not secret else ""}
        if multiline and not secret:
            kwargs["multiline"] = True   # Esc then Enter to submit
        return (fn(msg, **kwargs).ask() or "").strip()
    if multiline:
        print(f"{msg} (blank line to finish)")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line == "" and lines:
                break
            lines.append(line)
        return "\n".join(lines).strip() or default
    return input(f"{msg} ").strip() or default


def _ask_select(msg: str, choices: list[str], default: str | None = None) -> str:
    if _Q:
        return questionary.select(msg, choices=choices, default=default or choices[0],
                                  style=_QSTYLE).ask() or (default or choices[0])
    print(f"\n{msg}")
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    raw = input("Pick a number: ").strip()
    try:
        return choices[int(raw) - 1]
    except Exception:
        return default or choices[0]


def _ask_confirm(msg: str, default: bool = True) -> bool:
    if _Q:
        return bool(questionary.confirm(msg, default=default, style=_QSTYLE).ask())
    raw = input(f"{msg} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


def _validate_key(key: str) -> bool:
    return key.startswith("gsk_") and len(key) > 20


def collect_key(default: str = "") -> str:
    ui.rule("Step 1 · Groq API key", "pink")
    ui.panel(
        "Prism uses Groq (free) as its routing brain — it splits your prompt into\n"
        "targeted tasks for each specialist AI.\n\n"
        "  1. Visit  [bold]console.groq.com[/bold]  → sign up (free)\n"
        "  2. Left sidebar → [bold]API Keys[/bold] → [bold]Create API Key[/bold]\n"
        "  3. Copy the key — it starts with  [bold]gsk_[/bold]",
        title="🔑  Get your key", style="pink",
    )
    while True:
        key = _ask_text("Paste your Groq API key:", default=default, secret=True) or default
        if _validate_key(key):
            ui.ok("Key looks valid.")
            return key
        ui.warn("That doesn't look like a Groq key (should start with 'gsk_'). Try again.")


def collect_profile(default: str = "") -> str:
    ui.rule("Step 2 · What do you do?", "blue")
    ui.info("This tailors every prompt Prism writes. One line is enough.")
    ui.info("e.g. \"indie game dev\", \"PhD researcher in biology\", \"startup marketer\".")
    return _ask_text("What do you do / what will you use Prism for?", default=default) or default


def collect_agents(defaults: dict | None = None) -> dict:
    defaults = defaults or {}
    ui.rule("Step 3 · Choose your specialists", "orange")
    ui.info("Pick ONE tool per category (or skip categories you won't use).")
    ui.info("Tools like Claude / ChatGPT / LAZYCOOK / Kimi appear in several — that's intentional.\n")
    ui.catalog_table()

    chosen: dict[str, str] = {}
    for cat, meta in A.CATEGORIES.items():
        choices = list(meta["agents"]) + [SKIP]
        default = defaults.get(cat) if defaults.get(cat) in meta["agents"] else meta["agents"][0]
        pick = _ask_select(f"{meta['emoji']}  {meta['label']}", choices, default=default)
        if pick != SKIP:
            chosen[cat] = pick
    if not chosen:
        ui.warn("You skipped every category — enabling Brains → Claude so Prism has something to do.")
        chosen["brains"] = "Claude"
    return chosen


def collect_premium(chosen: dict, defaults: list | None = None) -> list:
    """Which of the chosen tools does the user PAY for? Premium tools get the
    bulk of the routed work — higher limits, better output."""
    defaults = defaults or []
    names = sorted(set(chosen.values()))
    if not names:
        return []
    ui.rule("Premium plans", "teal")
    ui.info("If you pay for any of these tools, Prism routes the heavy work "
            "through them first — premium plans mean higher limits and better output.")
    if _Q:
        picks = questionary.checkbox(
            "Which do you have a premium / paid plan for? (space to tick, enter to confirm)",
            choices=[questionary.Choice(n, checked=(n in defaults)) for n in names],
            style=_QSTYLE).ask()
        return picks or []
    premium = []
    for n in names:
        if _ask_confirm(f"Premium plan for {n}?", default=(n in defaults)):
            premium.append(n)
    return premium


def collect_chrome(default="") -> str:
    from . import automation
    ui.rule("Step 4 · Chrome version", "blue")
    try:
        detected = automation.detect_chrome_version()
    except Exception:
        detected = None
    det_txt = f"detected as v{detected}" if detected else "could not auto-detect"
    ui.panel(
        "Prism drives Chrome through a version-matched driver. It normally\n"
        "auto-detects your Chrome, but if you ever hit a \"version mismatch\" error\n"
        "you can pin your version explicitly here.\n\n"
        f"  This machine's Chrome: [bold]{det_txt}[/bold]\n"
        "  Not sure? Open  [bold]chrome://settings/help[/bold]  to see it.",
        title="🌐  Chrome version", style="blue",
    )
    hint = f", currently pinned to {default}" if default else ""
    raw = _ask_text(f"Chrome major version (leave blank to auto-detect{hint}):",
                    default=str(default) if default else "")
    v = automation.parse_chrome_version(raw)
    if v:
        ui.ok(f"Pinned Chrome to v{v}.")
        return str(v)
    ui.info("Using auto-detect.")
    return ""


def login_step(agents: dict):
    ui.rule("Step 5 · Log in to your tools (required)", "teal")
    urls = []
    lines = []
    seen = set()
    for cat, name in agents.items():
        cfg = A.AGENT_REGISTRY[name]
        if name not in seen:
            lines.append(f"  • {name:<16} {cfg['url']}")
            urls.append(cfg["url"])
            seen.add(name)
    ui.panel(
        "Prism drives your REAL, logged-in Chrome — it stores no passwords.\n"
        "Before your first real run you MUST be signed in to each tool below\n"
        "in Google Chrome:\n\n" + "\n".join(lines),
        title="🔐  Sign in first", style="teal",
    )
    if _ask_confirm("Open all of these in Chrome now so you can log in?", default=True):
        from . import automation
        automation.open_login_tabs(urls)
        ui.ok("Opened login tabs in Chrome. Sign in, then come back here.")
        _ask_text("Press Enter once you've signed in to continue…")


def firstrun_choice() -> str:
    ui.rule("Step 6 · First run", "pink")
    ui.panel(
        "Strongly recommended: do a [bold]dry run[/bold] first. Prism will show exactly\n"
        "how Groq splits a sample task across your chosen agents — WITHOUT opening\n"
        "any browser — so you can sanity-check your setup safely.",
        title="🧪  Try it safely", style="pink",
    )
    choice = _ask_select(
        "How do you want to start?",
        [
            "Dry run (recommended) — show the routing plan, no browser",
            "Full run now — actually drive the agents",
            "Skip — I'll run it myself later",
        ],
    )
    if choice.startswith("Dry"):
        return "dry"
    if choice.startswith("Full"):
        return "full"
    return "skip"


def run(existing: dict | None = None) -> dict:
    """Run the whole wizard, persist, and return the config. `existing` lets the
    /config command re-run onboarding pre-filled with current values."""
    cfg = existing or C.load()
    ui.banner()
    ui.panel(
        "Welcome. Prism turns one prompt into a sequenced hand-off across the best\n"
        "specialist AIs — grounded research, deep reasoning, writing, visuals,\n"
        "media, and shipped apps — each feeding the next.\n\n"
        "Let's set you up. This happens once; you can change anything later with\n"
        "the [bold]/config[/bold] command.",
        title="◈  Prism Setup", style="pink",
    )

    cfg["api_key"] = collect_key(cfg.get("api_key", ""))
    cfg["profile"] = collect_profile(cfg.get("profile", ""))
    cfg["agents"] = collect_agents(cfg.get("agents", {}))
    cfg["premium"] = collect_premium(cfg["agents"], cfg.get("premium"))
    cfg["chrome_version"] = collect_chrome(cfg.get("chrome_version", ""))
    cfg["onboarded"] = True
    C.save(cfg)
    ui.ok(f"Saved to {C.CONFIG_PATH}")

    login_step(cfg["agents"])
    cfg["_firstrun"] = firstrun_choice()  # consumed by prism.py, not persisted
    return cfg
