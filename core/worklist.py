"""
Prism — the pending-work file
──────────────────────────────
Three of the working dialog's tabs — what arrived, replies, purchase orders —
used to show only what the LAST check happened to find. Each check's result
was held in memory and nowhere else, so the moment the inbox's own read
bookmark moved past a message, whatever had been shown for it was gone —
switching tabs, or simply reopening the dialog the next morning, was enough
to lose a customer's reply that still needed an answer, or a purchase order
nobody had accepted yet.

This is the fix: one JSON file per inquiry folder, appended to every time a
check finds something, and read back every time a tab needs to draw itself —
so what is on screen is never just "since the last check", it is "everything
that has not been dealt with yet", however many checks ago it arrived.

Three lists, one per tab:

  · "arrived"  — every sorted message, kept as a permanent log (nothing here
    is ever "done"; it exists so a customer's mail can be found again next
    week, not just glimpsed once).
  · "replies"  — a customer's answer to a quotation, cleared once you apply
    it to the register.
  · "orders"   — a purchase order read off the mail, cleared once you accept
    or otherwise deal with it.

Written atomically, same discipline as register.py: a crash mid-write must
never truncate the only record of what still needs a reply.
"""
from __future__ import annotations

import json
import os

FILENAME = "worklist.json"
KINDS = ("arrived", "replies", "orders")

# "arrived" never resolves — it is a log, not a todo list — so without a cap
# it would grow forever. This is generous: a mailbox doing a few hundred
# messages a month takes years to reach it. Only unresolved rows are ever
# eligible for trimming in "replies"/"orders" — an unread purchase order from
# three weeks ago is exactly the row this file exists to keep from vanishing,
# so it is never trimmed for being old, only for being long since resolved.
ARRIVED_KEEP = 5000
RESOLVED_KEEP = 500


def path_in(folder: str) -> str:
    return os.path.join(folder, FILENAME)


def _blank() -> dict:
    return {"arrived": [], "replies": [], "orders": []}


def load(folder: str) -> dict:
    """The whole file, or an empty one if it has never been written — a
    mailbox nobody has checked yet is not an error."""
    path = path_in(folder)
    if not os.path.exists(path):
        return _blank()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _blank()
    out = _blank()
    for kind in KINDS:
        rows = data.get(kind)
        out[kind] = rows if isinstance(rows, list) else []
    return out


def save(folder: str, data: dict) -> None:
    """Atomic, like register.py's — this file is the only record of a
    reply or a purchase order that has not been dealt with yet."""
    os.makedirs(folder, exist_ok=True)
    path = path_in(folder)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _key(entry: dict) -> str:
    """A message-id when there is one — the one identifier that survives a
    re-check — falling back to sender+subject+date for anything unusual
    enough to lack one."""
    message_id = (entry.get("message_id") or "").strip()
    if message_id:
        return message_id
    return "|".join((entry.get("from_addr", ""), entry.get("subject", ""),
                     entry.get("date", "")))


def append(folder: str, kind: str, entries: list[dict]) -> dict:
    """Merge new entries into `kind`, keyed by _key() so the same message
    from two checks (the bookmark did not move, or the walk re-read it)
    never doubles up. An entry already on file is left exactly as it is —
    the resolved flag, or a correction, a person made is theirs to keep,
    not the next check's to quietly undo."""
    if kind not in KINDS:
        raise ValueError(f"unknown worklist kind {kind!r}")
    data = load(folder)
    rows = data[kind]
    seen = {_key(r) for r in rows}
    for entry in entries:
        k = _key(entry)
        if k in seen:
            continue
        seen.add(k)
        entry = dict(entry)
        if kind != "arrived":
            entry.setdefault("resolved", False)
        rows.append(entry)
    data[kind] = _trimmed(kind, rows)
    save(folder, data)
    return data


def _trimmed(kind: str, rows: list[dict]) -> list[dict]:
    if kind == "arrived":
        return rows[-ARRIVED_KEEP:] if len(rows) > ARRIVED_KEEP else rows
    pending = [r for r in rows if not r.get("resolved")]
    resolved = [r for r in rows if r.get("resolved")]
    if len(resolved) > RESOLVED_KEEP:
        resolved = resolved[-RESOLVED_KEEP:]
    # Stable order: interleave back by original position rather than
    # pending-then-resolved, so the screen reads oldest-to-newest as typed.
    kept_keys = {_key(r) for r in pending + resolved}
    return [r for r in rows if _key(r) in kept_keys]


def update(folder: str, kind: str, message_id: str, changes: dict) -> dict:
    """Apply changes to one row, found by message id — a correction to how
    a sender is sorted, or marking a reply resolved once it is applied."""
    data = load(folder)
    for row in data.get(kind, []):
        if row.get("message_id") == message_id:
            row.update(changes)
            break
    save(folder, data)
    return data


def resolve(folder: str, kind: str, message_id: str) -> dict:
    return update(folder, kind, message_id, {"resolved": True})


def pending(data: dict, kind: str) -> list[dict]:
    """Not yet dealt with, oldest first — the actionable list for "replies"
    and "orders"."""
    return [r for r in data.get(kind, []) if not r.get("resolved")]


def history(data: dict, kind: str, days: int | None = None) -> list[dict]:
    """Everything, newest first — the log view for "arrived", and the "show
    what has already been handled too" view for the other two. `days`
    filters on the entry's own "date" (a plain YYYY-MM-DD), inclusive of
    today; None returns all of it."""
    rows = list(data.get(kind, []))
    rows.reverse()
    if days is not None:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        rows = [r for r in rows if (r.get("date") or "") >= cutoff]
    return rows
