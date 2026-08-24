"""
Prism — a plain-text trail for one "Check my mail" run
────────────────────────────────────────────────────────
Nothing in inbox.py, triage.py or mailflow.py wrote anything down. A slow
first run and a hung one look identical from the outside — a spinner, no
number, no way to tell "reading message 40 of 180" from "the server stopped
answering" — and there was no file anybody could open afterward to find out
which.

One append-only text file per day, plain sentences, flushed after every line
so a run that never finishes still leaves a trail up to wherever it stopped.
Not a `logging` handler on purpose: this needs to work the same whether or
not anything else in the process has configured logging, and a customer
reading it should not need to know what a logger is.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

from . import config

LOG_DIR = os.path.join(config.CONFIG_DIR, "logs")


def _path() -> str:
    return os.path.join(LOG_DIR, f"inbox-check-{datetime.now():%Y-%m-%d}.log")


def line(msg: str) -> None:
    """Append one timestamped line. Never raises — a full disk must not turn
    a slow mail check into a crashed one."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S}  {msg}\n")
    except OSError:
        pass


class stopwatch:
    """`with stopwatch("connecting"): ...` — logs start, then done + elapsed.

    A bare try/finally would do the timing; this exists so every instrumented
    step reads the same way in the file instead of however each caller felt
    like phrasing it that day.
    """

    def __init__(self, what: str):
        self.what = what

    def __enter__(self):
        self.t0 = time.monotonic()
        line(f"{self.what} …")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.monotonic() - self.t0
        if exc_type is None:
            line(f"{self.what} — done in {elapsed:.1f}s")
        else:
            line(f"{self.what} — FAILED after {elapsed:.1f}s: {exc}")
        return False
