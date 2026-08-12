"""
Prism — the inquiry register
────────────────────────────
One CSV that keeps growing, one row per inquiry, opening in Excel like any
other file. If Prism were uninstalled tomorrow the register would still be
theirs and still work — which is the point. This is their book, not our
database.

Three things this module is careful about, all learned from how the file is
actually used:

  · **It is written atomically.** A crash mid-write must never truncate the
    only copy of somebody's order book. Same reasoning as config.save().

  · **It is often open in Excel.** On Windows that makes the file unwritable,
    and the honest response is to say "close the inquiry register in Excel",
    not to fail silently or lose the row.

  · **The owner edits it by hand.** They will re-sort it, add a column, and
    type in the Notes field. Reading is therefore forgiving: unknown columns
    are preserved untouched, and a row nobody recognises is left alone.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# ── the statuses an inquiry moves through ────────────────────────────────────
NEW = "New"
QUOTED = "Quoted"
FOLLOWING_UP = "Following up"
NEGOTIATING = "Negotiating"
ACCEPTED = "Accepted"
CONVERTED = "Converted"
NOT_CONVERTED = "Not converted"

OPEN_STATUSES = (NEW, QUOTED, FOLLOWING_UP, NEGOTIATING, ACCEPTED)
CLOSED_STATUSES = (CONVERTED, NOT_CONVERTED)
STATUSES = OPEN_STATUSES + CLOSED_STATUSES

# Column order is the order they read it in. Identity first, then what was
# asked, then what we did, then how it ended.
COLUMNS = [
    # Time as well as date. Two inquiries from one customer on the same
    # morning are indistinguishable in a date-only register, and "which of
    # these came first" is exactly the question asked when somebody revises
    # their requirement an hour after sending it.
    "Inquiry no", "Date received", "Time received", "Customer",
    "Contact person", "Email",
    "Phone", "Product asked", "Quantity", "Drawing", "Status",
    "Quotation no", "Quotation date", "Quotation value", "Reminders sent",
    "Last contact", "Result", "Reason if lost", "PO number", "PO date",
    "Order value", "Folder", "Notes",
    # Not for reading — how a reply finds its way back to the right row.
    "Thread",
]

FILENAME = "inquiries.csv"


class RegisterLocked(Exception):
    """The file could not be written because something else holds it open."""


def _friendly_lock(path: str) -> RegisterLocked:
    return RegisterLocked(
        f"The inquiry register is open in another program, so Prism can't add "
        f"to it. Close {os.path.basename(path)} in Excel and try again — "
        f"nothing has been lost.")


# ── the financial year, the way India counts it ──────────────────────────────

def fy_label(when: date | datetime | None = None) -> str:
    """"25-26" for any date from 1 April 2025 to 31 March 2026.

    Every quotation book and PO in the country is numbered this way, so a
    register that numbered by calendar year would be the one odd document in
    the office.
    """
    when = when or date.today()
    if isinstance(when, datetime):
        when = when.date()
    start = when.year if when.month >= 4 else when.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


_NUMBER = re.compile(r"^([A-Z]+)/(\d{2}-\d{2})/(\d+)$", re.I)


def next_number(rows: list[dict], prefix: str = "INQ",
                when: date | datetime | None = None) -> str:
    """The next number in this financial year, e.g. INQ/25-26/0088.

    Counts from what is in the file rather than from a stored counter, so
    deleting the last row genuinely undoes it and two people numbering on
    different machines cannot both be told "0088" by a counter that only one
    of them updated.
    """
    year = fy_label(when)
    highest = 0
    for row in rows:
        match = _NUMBER.match((row.get("Inquiry no") or "").strip())
        if match and match.group(1).upper() == prefix.upper() and match.group(2) == year:
            highest = max(highest, int(match.group(3)))
    return f"{prefix.upper()}/{year}/{highest + 1:04d}"


# ── reading and writing ───────────────────────────────────────────────────────

def path_in(folder: str, filename: str = FILENAME) -> str:
    return os.path.join(folder, filename)


def load(path: str) -> list[dict]:
    """Every row, as dicts. A missing file is an empty register, not an error.

    Columns the owner added by hand survive, because the row is kept as read
    and only the fields Prism knows about are ever replaced.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)
                    if any((v or "").strip() for v in r.values())]
    except OSError as e:
        raise RegisterLocked(
            f"Couldn't read the inquiry register: {e}") from e


def save(rows: list[dict], path: str) -> None:
    """Write the whole register, atomically.

    Any column present in any row is written, so hand-added columns are not
    quietly deleted the next time Prism touches the file — which would be a
    very fast way to lose somebody's trust in it.
    """
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)

    extra = [k for row in rows for k in row if k not in COLUMNS]
    seen, columns = set(), list(COLUMNS)
    for key in extra:
        if key not in seen:
            seen.add(key)
            columns.append(key)

    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in columns})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except PermissionError as e:
        _cleanup(tmp)
        raise _friendly_lock(path) from e
    except OSError as e:
        _cleanup(tmp)
        raise RegisterLocked(f"Couldn't write the inquiry register: {e}") from e


def _cleanup(tmp: str) -> None:
    try:
        os.unlink(tmp)
    except OSError:
        pass


# ── rows ──────────────────────────────────────────────────────────────────────

def blank_row() -> dict:
    return {c: "" for c in COLUMNS}


def thread_key(message) -> str:
    """The identifiers that tie a conversation together.

    Message-Id plus References, space separated. Mail clients quote these in
    every reply, so matching on any one of them is what keeps a four-message
    negotiation as one row instead of four inquiries.
    """
    ids = [getattr(message, "message_id", "") or ""]
    ids += list(getattr(message, "references", []) or [])
    reply_to = getattr(message, "in_reply_to", "") or ""
    if reply_to:
        ids.append(reply_to)
    return " ".join(sorted({i.strip() for i in ids if i.strip()}))


def find_by_thread(rows: list[dict], message) -> dict | None:
    """The row this message belongs to, by conversation then by sender.

    Thread identifiers are exact and are tried first. Falling back to "an open
    inquiry from the same address" catches the very common case of a customer
    replying from their phone with a client that starts a fresh thread — and
    it is limited to OPEN inquiries so a new job from an old customer is not
    swallowed into last year's closed row.
    """
    ids = {i for i in thread_key(message).split() if i}
    if ids:
        for row in rows:
            known = {i for i in (row.get("Thread") or "").split() if i}
            if known & ids:
                return row
    sender = (getattr(message, "from_addr", "") or "").strip().lower()
    if not sender:
        return None
    open_rows = [r for r in rows
                 if (r.get("Email") or "").strip().lower() == sender
                 and (r.get("Status") or "").strip() in OPEN_STATUSES]
    return open_rows[-1] if open_rows else None


def find(rows: list[dict], inquiry_no: str) -> dict | None:
    wanted = (inquiry_no or "").strip().upper()
    for row in rows:
        if (row.get("Inquiry no") or "").strip().upper() == wanted:
            return row
    return None


def add_thread(row: dict, message) -> None:
    """Fold a message's identifiers into a row, without losing the old ones."""
    ids = {i for i in (row.get("Thread") or "").split() if i}
    ids |= {i for i in thread_key(message).split() if i}
    row["Thread"] = " ".join(sorted(ids))


# Subjects that say nothing about what was wanted. Half of all inquiries arrive
# under one of these, and on their own they are useless twice over: a human
# reading the register learns nothing, and matching them against a rate list
# finds nothing.
_GENERIC_SUBJECT = {
    "enquiry", "enquiry.", "inquiry", "quotation", "quote", "quotation required",
    "quotation request", "requirement", "requirements", "rfq", "query",
    "request", "price", "prices", "rate", "rates", "price list", "urgent",
    "hello", "hi", "regarding", "reg", "no subject", "your quotation",
}

_GREETING = ("dear", "hi ", "hii", "hello", "respected", "sir", "madam",
             "greetings", "good morning", "good afternoon", "good evening",
             "thanks", "thank you", "regards")


def _first_real_line(body: str, limit: int = 140) -> str:
    """The first line of an email that is not a greeting — what they want."""
    for line in (body or "").splitlines():
        line = line.strip(" >\t")
        if len(line) < 8:
            continue
        if line.lower().startswith(_GREETING):
            continue
        return line[:limit]
    return ""


def product_summary(message) -> str:
    """What was asked for, for the register and for matching a rate list.

    Uses the subject when the subject carries information. When it does not —
    a bare "Enquiry", which is how half of them arrive — the opening line of
    the message is added, because that is the line a person would read to find
    out what the mail is about.
    """
    subject = re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "",
                     getattr(message, "subject", "") or "",
                     flags=re.IGNORECASE).strip()
    opening = _first_real_line(getattr(message, "body", "") or "")

    words = [w for w in re.findall(r"[A-Za-z0-9]+", subject) if len(w) > 1]
    named = bool(subject) and subject.lower() not in _GENERIC_SUBJECT and len(words) >= 4

    # The digits are the specification. "Enquiry for compression springs" names
    # the product and reads fine, but every spring on the rate list is a
    # compression spring — what picks the row is "2mm wire, 25 OD", and that
    # lives in the body. So a subject without numbers is never the whole story
    # when the first line of the message has them.
    subject_has_spec = bool(re.search(r"\d", subject))
    opening_has_spec = bool(re.search(r"\d", opening))

    if named and (subject_has_spec or not opening_has_spec):
        return subject[:200]
    if subject and opening:
        return f"{subject} — {opening}"[:200]
    return (opening or subject)[:200]


def from_message(message, details: dict | None = None, *, prefix: str = "INQ",
                 rows: list[dict] | None = None, folder: str = "") -> dict:
    """A fresh register row for a message that triage called an inquiry.

    `details` is whatever the extraction step worked out — product, quantity,
    contact name. It is allowed to be empty: a row with the sender, the date
    and the subject is still a row, and an inquiry that is in the register
    imperfectly beats one that is only in the inbox.
    """
    details = details or {}
    row = blank_row()
    row["Inquiry no"] = next_number(rows or [], prefix,
                                    getattr(message, "date", None) or date.today())
    when = getattr(message, "date", None)
    row["Date received"] = when.strftime("%d-%m-%Y") if when else date.today().strftime("%d-%m-%Y")
    # Only from a real timestamp. A row that fell back to today's date must
    # not carry the clock time of the moment Prism happened to run — that
    # would read as the time the customer wrote, and it isn't.
    row["Time received"] = _clock(when)
    row["Customer"] = details.get("customer") or getattr(message, "from_name", "") or ""
    row["Contact person"] = details.get("contact") or getattr(message, "from_name", "") or ""
    row["Email"] = getattr(message, "from_addr", "") or ""
    row["Phone"] = details.get("phone", "")
    row["Product asked"] = details.get("product") or product_summary(message)
    row["Quantity"] = details.get("quantity", "")
    names = list(getattr(message, "attachment_names", []) or [])
    row["Drawing"] = ", ".join(names) if names else "No"
    row["Status"] = NEW
    row["Last contact"] = row["Date received"]
    row["Folder"] = folder
    row["Notes"] = details.get("notes", "")
    add_thread(row, message)
    return row


def _clock(when) -> str:
    """"14:35" in the reader's own timezone, or "" when there is no timestamp.

    A mail Date header carries the SENDER's offset. Left as-is, a 9 a.m.
    enquiry from Germany files itself at 09:00 in a Gujarat register and the
    column stops meaning "when it reached us" — which is the only thing the
    owner reads it as. Naive timestamps are left alone: there is nothing to
    convert from, and guessing would be worse than the plain number.
    """
    if not isinstance(when, datetime):
        return ""
    if when.tzinfo is not None:
        try:
            when = when.astimezone()
        except (OSError, OverflowError, ValueError):
            pass       # a broken local timezone must not lose us the row
    return when.strftime("%H:%M")


def update(rows: list[dict], inquiry_no: str, changes: dict) -> dict | None:
    """Apply changes to one row in place. Returns the row, or None if unknown."""
    row = find(rows, inquiry_no)
    if row is None:
        return None
    row.update({k: ("" if v is None else v) for k, v in changes.items()})
    return row


def mark_quoted(row: dict, quote_no: str, value, when: date | None = None) -> dict:
    when = when or date.today()
    row["Status"] = QUOTED
    row["Quotation no"] = quote_no
    row["Quotation date"] = when.strftime("%d-%m-%Y")
    row["Quotation value"] = money_str(value)
    row["Last contact"] = when.strftime("%d-%m-%Y")
    return row


def mark_lost(row: dict, reason: str = "", when: date | None = None) -> dict:
    when = when or date.today()
    row["Status"] = NOT_CONVERTED
    row["Result"] = NOT_CONVERTED
    row["Reason if lost"] = reason
    row["Last contact"] = when.strftime("%d-%m-%Y")
    return row


def mark_converted(row: dict, po_number: str, value, when: date | None = None) -> dict:
    when = when or date.today()
    row["Status"] = CONVERTED
    row["Result"] = CONVERTED
    row["PO number"] = po_number
    row["PO date"] = when.strftime("%d-%m-%Y")
    row["Order value"] = money_str(value)
    row["Last contact"] = when.strftime("%d-%m-%Y")
    return row


# What a reply means, in register terms. The keys are mailflow's intents; they
# are strings rather than an import because mailflow imports this module and a
# cycle between the two would be a silly thing to introduce for four words.
#
# Note what is NOT here: "accepted" does not become Converted. A customer
# saying "go ahead" is a promise; Converted is a fact backed by a PO number and
# an order value. Collapsing the two would make the conversion figure in the
# month-end summary optimistic by exactly the orders that never arrived, and
# that figure is the one number the owner would repeat to a bank.
REPLY_STATUS = {
    "accepted": ACCEPTED,
    "rejected": NOT_CONVERTED,
    "negotiating": NEGOTIATING,
    "needs_info": NEGOTIATING,
}


def mark_reply(row: dict, intent: str, when: date | None = None,
               note: str = "") -> dict:
    """Move a row on because the customer answered.

    An intent Prism could not read leaves the status exactly as it was and
    only touches Last contact. That is deliberate: a wrong status is worse
    than a stale one, because the owner acts on the register without
    re-reading the mail, and a quotation wrongly marked Not converted is one
    they will never chase again.
    """
    when = when or date.today()
    row["Last contact"] = when.strftime("%d-%m-%Y")
    if note:
        row["Notes"] = f"{(row.get('Notes') or '').strip()} {note}".strip()
    status = REPLY_STATUS.get((intent or "").strip().lower())
    if not status:
        return row
    if status == NOT_CONVERTED:
        # Reuse mark_lost so Result and Reason are filled the same way they are
        # when the owner closes a row by hand — one shape of "lost" in the file.
        return mark_lost(row, "Declined by customer", when)
    row["Status"] = status
    return row


def note_reminder(row: dict, when: date | None = None) -> dict:
    when = when or date.today()
    try:
        sent = int(str(row.get("Reminders sent") or "0").strip() or 0)
    except ValueError:
        sent = 0
    row["Reminders sent"] = str(sent + 1)
    row["Status"] = FOLLOWING_UP
    row["Last contact"] = when.strftime("%d-%m-%Y")
    return row


# ── money and dates, read back from a file a human has been editing ──────────

def money(value) -> Decimal:
    """A Decimal from whatever is in the cell — "₹ 1,42,500.00", "Rs.1,000/-".

    One parser, shared with quoting, so the register's idea of what a rupee
    looks like can never drift from the quotation's. Anything unreadable is
    zero rather than an exception: one mistyped cell must not stop the
    month-end summary.
    """
    from .quoting import to_decimal
    return to_decimal(value)


def money_str(value) -> str:
    """Plain digits with two decimals. No symbol, no grouping — the register is
    read by Excel as often as by a person, and a currency symbol in the cell
    turns the whole column into text."""
    return f"{money(value):.2f}"


_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y")


def parse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ── what the owner is shown ───────────────────────────────────────────────────

@dataclass
class Summary:
    received: int = 0
    quoted: int = 0
    quoted_value: Decimal = Decimal(0)
    converted: int = 0
    converted_value: Decimal = Decimal(0)
    waiting: int = 0
    lost: int = 0
    reasons: dict | None = None

    @property
    def conversion(self) -> float:
        """Orders won as a percentage of quotations sent.

        Against quotations, not inquiries — an inquiry nobody quoted was never
        a chance, and counting it would make the number flattering and useless.
        """
        return round(100.0 * self.converted / self.quoted, 1) if self.quoted else 0.0


def summarise(rows: list[dict], since: date | None = None,
              until: date | None = None) -> Summary:
    """The month-end numbers. Rows with an unreadable date are counted in the
    totals but never filtered out by a date window they cannot be tested
    against — silently dropping somebody's order is worse than a loose count."""
    out = Summary(reasons={})
    for row in rows:
        received = parse_date(row.get("Date received", ""))
        if since and received and received < since:
            continue
        if until and received and received > until:
            continue
        out.received += 1
        status = (row.get("Status") or "").strip()
        if row.get("Quotation no"):
            out.quoted += 1
            out.quoted_value += money(row.get("Quotation value"))
        if status == CONVERTED:
            out.converted += 1
            out.converted_value += money(row.get("Order value"))
        elif status == NOT_CONVERTED:
            out.lost += 1
            reason = (row.get("Reason if lost") or "not given").strip() or "not given"
            out.reasons[reason] = out.reasons.get(reason, 0) + 1
        elif status in (QUOTED, FOLLOWING_UP, NEGOTIATING, ACCEPTED):
            out.waiting += 1
    return out


def awaiting_followup(rows: list[dict], after_days: int = 3,
                      max_reminders: int = 3,
                      today: date | None = None) -> list[dict]:
    """Quotations that have gone quiet and are due a nudge.

    The single most valuable list in the file: it is the money already earned
    and not yet collected on. Stops after `max_reminders` because a fourth
    chase stops being a follow-up and starts being a nuisance.
    """
    today = today or date.today()
    due = []
    for row in rows:
        if (row.get("Status") or "").strip() not in (QUOTED, FOLLOWING_UP, NEGOTIATING):
            continue
        try:
            sent = int(str(row.get("Reminders sent") or "0").strip() or 0)
        except ValueError:
            sent = 0
        if sent >= max_reminders:
            continue
        last = parse_date(row.get("Last contact", "")) or parse_date(
            row.get("Quotation date", ""))
        if last and (today - last).days >= after_days:
            due.append(row)
    return due
