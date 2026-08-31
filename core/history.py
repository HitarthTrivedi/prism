"""Prism — the one file that reads as the whole story of an inquiry
───────────────────────────────────────────────────────────────────
Every other file in an inquiry's folder is data: `inquiries.csv` has the
row, `QTN-…csv` has the priced lines, a drawing is a drawing. None of them
is what actually got said. The enquiry's own words, the covering letter
that went out with the quotation, the customer's reply, the purchase
order's own text — none of that was ever kept anywhere, which meant "show
me everything about this inquiry" stopped at whatever was still in the
sent-mail folder or somebody's memory.

    <inquiry folder>/history.txt

One entry per event, oldest first, in the order they actually happened:
the enquiry as it arrived, every quotation and reminder Prism sent, every
reply and purchase order that came back, and how it was finally decided —
converted or lost. Appended to, never rewritten, so a crash mid-write
costs at most the entry being written, never an earlier one — the same
promise worklist.py's logs make, kept here with a plain append instead of
an atomic replace, because this file only ever grows.

It is deliberately plain text, not JSON: this is the file a person opens,
in Notepad or on a phone, when a customer calls and asks "what did we
quote them last time" — not one Prism itself reads back.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

FILENAME = "history.txt"
_RULE = "=" * 70
_HEADER_SPLIT = "  —  "


def path_for(folder: str) -> str:
    return os.path.join(folder, FILENAME)


def read(folder: str) -> str:
    """The whole story back, oldest entry first — or "" for a folder
    nothing has happened to yet, which is not an error."""
    if not folder:
        return ""
    try:
        with open(path_for(folder), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def append(folder: str, event: str, *, who: str = "", subject: str = "",
          body: str = "", when: datetime | None = None) -> None:
    """Record one moment in the inquiry's life: what happened, who it was
    with, and the words themselves — not just that a mail went out, but
    what it said. A folder that does not exist yet is created, the same
    way the register's own per-inquiry folder is; a write that fails
    (a full disk, a folder somebody deleted) is swallowed rather than
    raised, because a history entry must never be the reason the actual
    send, or the actual register update, is reported as failed."""
    if not folder:
        return
    when = when or datetime.now()
    lines = [_RULE, f"{when.strftime('%d-%m-%Y %H:%M')}{_HEADER_SPLIT}{event}"]
    if who:
        lines.append(who)
    if subject:
        lines.append(f"Subject: {subject}")
    lines.append("")
    if body:
        lines.append(body.strip())
    lines.append("")
    text = "\n".join(lines) + "\n"
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path_for(folder), "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


_ENTRY_SPLIT = re.compile(rf"^{re.escape(_RULE)}$", re.MULTILINE)


def entries(folder: str) -> list[dict]:
    """The same file, parsed back into one dict per event — {when, event,
    who, subject, body} — so a screen can draw each entry as its own card
    instead of a wall of plain text. This is a view onto append()'s exact,
    self-controlled format, not a second copy of the record: the text file
    stays the one source of truth, readable with nothing but a text editor
    even if this parser is wrong or this function is never called."""
    text = read(folder)
    if not text:
        return []
    out = []
    for chunk in _ENTRY_SPLIT.split(text):
        lines = chunk.strip("\n").split("\n")
        if not lines or not lines[0].strip():
            continue
        when, _, event = lines[0].partition(_HEADER_SPLIT)
        rest = lines[1:]
        who = ""
        subject = ""
        i = 0
        if i < len(rest) and rest[i].strip() and not rest[i].startswith("Subject: "):
            who = rest[i].strip()
            i += 1
        if i < len(rest) and rest[i].startswith("Subject: "):
            subject = rest[i][len("Subject: "):].strip()
            i += 1
        if i < len(rest) and not rest[i].strip():
            i += 1
        body = "\n".join(rest[i:]).strip()
        out.append({"when": when.strip(), "event": event.strip(),
                   "who": who, "subject": subject, "body": body})
    return out
