"""
Prism — terminal UI helpers (rich-powered, Claude-Code-flavoured)
────────────────────────────────────────────────────────────────
All colour, banners, panels and tables live here so the rest of the app stays
free of formatting noise. Degrades to plain print() if `rich` is missing.
"""
from __future__ import annotations

try:
    from rich.console import Console
    from rich.theme import Theme
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich.rule import Rule
    _RICH = True
except Exception:  # pragma: no cover
    _RICH = False

# ── Prism brand palette (mirrors the desktop dashboard) ───────────────────────
_THEME = {
    "pink": "#EF4B77",
    "orange": "#FF8A4B",
    "teal": "#4CD9B4",
    "blue": "#4C9AFF",
    "dim": "#777777",
    "brand": "bold #EF4B77",
    "brains": "#EF4B77",
    "research": "#4C9AFF",
    "content": "#FF8A4B",
    "visual": "#4CD9B4",
    "media": "#B47BFF",
    "audio": "#7BE0FF",
    "development": "#FFD24B",
    "presentation": "#8C9EFF",
    "ok": "bold #4CD9B4",
    "warn": "bold #FF8A4B",
    "err": "bold #EF4B77",
}

console = Console(theme=Theme(_THEME)) if _RICH else None


def _plain(msg: str):
    print(msg)


def rule(label: str = "", style: str = "pink"):
    if _RICH:
        console.print(Rule(label, style=style))
    else:
        _plain(f"── {label} " + "─" * max(0, 40 - len(label)))


def say(msg: str, style: str = ""):
    if _RICH:
        console.print(msg, style=style)
    else:
        _plain(msg)


def ok(msg: str):
    say(f"✅  {msg}", "ok")


def warn(msg: str):
    say(f"⚠️  {msg}", "warn")


def err(msg: str):
    say(f"❌  {msg}", "err")


def info(msg: str):
    say(f"[dim]{msg}[/dim]" if _RICH else msg)


def banner():
    """The big Prism splash."""
    art = r"""
    ██████╗ ██████╗ ██╗███████╗███╗   ███╗
    ██╔══██╗██╔══██╗██║██╔════╝████╗ ████║
    ██████╔╝██████╔╝██║███████╗██╔████╔██║
    ██╔═══╝ ██╔══██╗██║╚════██║██║╚██╔╝██║
    ██║     ██║  ██║██║███████║██║ ╚═╝ ██║
    ╚═╝     ╚═╝  ╚═╝╚═╝╚══════╝╚═╝     ╚═╝"""
    if _RICH:
        text = Text(art, style="bold #EF4B77")
        console.print(text)
        console.print(
            Align.center(
                Text("◈  one prompt → many specialist AIs, in sequence  ◈",
                     style="italic #4CD9B4")
            )
        )
        console.print()
    else:
        _plain(art)
        _plain("  one prompt -> many specialist AIs, in sequence")


def panel(body: str, title: str = "", style: str = "pink"):
    if _RICH:
        console.print(Panel(body, title=title, border_style=style, padding=(1, 2)))
    else:
        if title:
            _plain(f"=== {title} ===")
        _plain(body)


def status_header(cfg: dict, active: dict):
    """Compact one-glance status shown at the top of the REPL."""
    from . import agents as A
    if _RICH:
        t = Table.grid(padding=(0, 2))
        t.add_column(style="dim", justify="right")
        t.add_column()
        who = cfg.get("profile") or "—"
        t.add_row("you", who if len(who) < 70 else who[:67] + "…")
        key = cfg.get("api_key", "")
        t.add_row("groq key", (key[:7] + "…" + key[-4:]) if len(key) > 12 else "not set")
        cv = cfg.get("chrome_version", "")
        t.add_row("chrome", f"pinned v{cv}" if cv else "auto-detect")
        if active:
            for cat, name in active.items():
                meta = A.CATEGORIES.get(cat, {})
                emoji = meta.get("emoji", "•")
                color = meta.get("color", "pink")
                t.add_row(f"{emoji} {cat}", f"[{color}]{name}[/{color}]")
        else:
            t.add_row("agents", "[warn]none selected[/warn]")
        console.print(Panel(t, title="Prism", border_style="pink", padding=(1, 2)))
    else:
        _plain(f"Prism — {cfg.get('profile','')}")
        for cat, name in active.items():
            _plain(f"  {cat}: {name}")


def catalog_table():
    """The full tool catalogue, grouped by category (like the source CSV)."""
    from . import agents as A
    if not _RICH:
        for cat, meta in A.CATEGORIES.items():
            _plain(f"\n{meta['emoji']} {meta['label']}")
            for a in meta["agents"]:
                cfg = A.AGENT_REGISTRY[a]
                _plain(f"   {a:<16} {cfg['cost']:<12} {cfg['avg']}")
        return
    for cat, meta in A.CATEGORIES.items():
        t = Table(
            title=f"{meta['emoji']}  {meta['label']}  —  [dim]{meta['desc']}[/dim]",
            title_justify="left", border_style=meta["color"], expand=True,
            title_style=f"bold {meta['color']}",
        )
        t.add_column("Tool", style="bold", no_wrap=True)
        t.add_column("What it's picked for")
        t.add_column("Cost", no_wrap=True)
        t.add_column("Speed", no_wrap=True)
        for a in meta["agents"]:
            c = A.AGENT_REGISTRY[a]
            t.add_row(a, c["specialty"], c["cost"], c["avg"])
        console.print(t)
        console.print()


def agent_pick_table(stage: str, current: str, suggested: str | None = None):
    """One category's full tool list as a numbered picker — current pick
    marked green (●), the router's suggested alternative starred (★). Used
    right before letting the user type a number to switch, or skip."""
    from . import agents as A
    meta = A.CATEGORIES.get(stage, {})
    names = meta.get("agents", [])
    if not _RICH:
        for i, n in enumerate(names, 1):
            tag = " (current)" if n == current else (" (suggested)" if n == suggested else "")
            _plain(f"  {i}. {n}{tag}")
        return
    color = meta.get("color", "teal")
    t = Table(
        title=f"{meta.get('emoji','')}  {stage.upper()}  —  [dim]{meta.get('desc','')}[/dim]",
        title_justify="left", border_style=color, expand=True,
    )
    t.add_column("#", justify="right", no_wrap=True, width=3)
    t.add_column("Tool", no_wrap=True)
    t.add_column("What it's picked for")
    t.add_column("Cost", no_wrap=True)
    for i, n in enumerate(names, 1):
        c = A.AGENT_REGISTRY.get(n, {})
        if n == current:
            label = f"[ok]●[/ok] {n}"
        elif n == suggested:
            label = f"[bold {color}]★[/bold {color}] {n}"
        else:
            label = n
        t.add_row(str(i), label, c.get("specialty", ""), c.get("cost", ""))
    console.print(t)
    console.print("   [ok]●[/ok] your current default    [bold]★[/bold] router's suggestion",
                  style="dim")


def pipeline_plan(stages: list[tuple[str, str, str]], title: str = "pipeline"):
    """Render an EXPLICIT stage list — (stage, agent, what it does) — the way
    routing_plan renders the router's own choices.

    routing_plan can only draw the fixed PIPELINE_ORDER categories, so a
    purpose-built pipeline (/boq's measure → interpret → write, and the
    task-shaped pipelines in FUTURE_PLAN Phase 6) had no way to show the user
    which tools were about to run. Same table, arbitrary stages."""
    if _RICH:
        t = Table(title=f"🧠  {title}", border_style="teal",
                  title_style="bold teal", expand=True)
        t.add_column("#", justify="right", style="dim")
        t.add_column("Stage", style="bold")
        t.add_column("Agent")
        t.add_column("What it does")
        for i, (stage, agent, purpose) in enumerate(stages, 1):
            t.add_row(str(i), stage, agent or "[dim]—[/dim]", purpose)
        console.print(t)
    else:
        _plain(f"-- {title} --")
        for i, (stage, agent, purpose) in enumerate(stages, 1):
            _plain(f"{i}. {stage}: {agent} — {purpose}")


def routing_plan(routing: dict, agents: dict):
    """Render the router's plan as a table before execution."""
    from . import agents as A
    if _RICH:
        t = Table(title="🧠  Groq routing plan", border_style="teal",
                  title_style="bold teal", expand=True)
        t.add_column("Stage", style="bold")
        t.add_column("Agent")
        t.add_column("Run?", justify="center")
        t.add_column("Prompts")
        for stage in A.PIPELINE_ORDER:
            data = routing.get(stage)
            if not data:
                continue
            name = agents.get(stage) or (A.summary_agent_name(agents) if stage == "summary" else None)
            needed = data.get("needed", False) and bool(data.get("questions"))
            mark = "[ok]● yes[/ok]" if needed else "[dim]○ skip[/dim]"
            qs = data.get("questions", [])
            preview = "\n".join(f"• {q[:80]}" for q in qs) if qs else "[dim]—[/dim]"
            t.add_row(stage, name or "[dim]—[/dim]", mark, preview)
        console.print(t)
    else:
        for stage in A.PIPELINE_ORDER:
            data = routing.get(stage)
            if data:
                _plain(f"{stage}: needed={data.get('needed')} qs={len(data.get('questions',[]))}")
