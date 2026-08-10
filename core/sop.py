"""
Prism — sending SOPs to customers
─────────────────────────────────
The easiest thing in this whole workflow to automate, because there is no money
in the message. Sending the wrong price is expensive; sending the wrong process
document is embarrassing and fixable. So this one runs on its own.

Four things start a send:

  1. an order is converted   → the pack for that product goes to that customer
  2. a customer asks for it  → triage tags the mail, it goes back the same hour
  3. **a document is revised** → everyone holding the old revision gets the new
     one, without the owner remembering to do anything
  4. an annual re-issue falls due for customers whose contract says so

Number three is the one worth building for. The record of who received which
revision on which date — sop_sent.csv — is the answer to "prove your customers
were notified", which is exactly what an ISO auditor asks and which today means
somebody searching Sent Items for an afternoon.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

LOG_FILENAME = "sop_sent.csv"
INDEX_FILENAME = "sops.csv"
CLIENTS_FILENAME = "client_sops.csv"

LOG_COLUMNS = ["Date sent", "Customer", "Email", "SOP code", "Title",
               "Revision", "Reason", "Inquiry no"]

# Revisions in filenames: SOP-07_Heat-Treatment_rev3.pdf, "QAP 02 R4.docx",
# "Packing-SOP-v2.1.pdf". People name files however they like and then never
# rename them, so recognising the common shapes is worth more than insisting
# on one.
_REVISION = re.compile(r"(?:_|-|\s|\()(?:rev|r|v|version)\.?\s*([\d]+(?:\.\d+)?)",
                       re.I)
_CODE = re.compile(r"^([A-Za-z]{2,6}[\s_-]?\d{1,3})")


@dataclass
class SopDoc:
    code: str
    title: str = ""
    revision: str = "1"
    revision_date: date | None = None
    path: str = ""
    # Which products or job types this belongs to, for rule 1 above.
    applies_to: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.code} {self.title}".strip() + f" (rev {self.revision})"

    @property
    def filename(self) -> str:
        return os.path.basename(self.path) if self.path else ""

    def newer_than(self, revision: str) -> bool:
        """True when this document has moved on from the revision given.

        Compared numerically where both sides look like numbers, so rev 10
        correctly beats rev 9 — a string comparison would say otherwise, and
        would stop chasing exactly when a document has been revised ten times.
        """
        return _revision_value(self.revision) > _revision_value(revision)


def _revision_value(revision: str) -> tuple:
    text = str(revision or "").strip().lower().lstrip("rv")
    parts = re.findall(r"\d+", text)
    return tuple(int(p) for p in parts) if parts else (0,)


# ── the library ───────────────────────────────────────────────────────────────

def load_library(folder: str) -> list[SopDoc]:
    """Every SOP in the folder, at its current revision.

    An index file (sops.csv: code, title, revision, file, applies to) wins when
    one exists, because then the owner controls the titles. Without one the
    filenames are read, so a company that has done nothing but drop PDFs in a
    folder still gets a working library on day one.
    """
    if not os.path.isdir(folder):
        return []
    index = os.path.join(folder, INDEX_FILENAME)
    docs = _from_index(index, folder) if os.path.exists(index) else _from_filenames(folder)

    # Keep only the highest revision of each code — a folder accumulates old
    # copies, and sending rev 2 when rev 5 exists is the failure this whole
    # module is meant to prevent.
    best: dict[str, SopDoc] = {}
    for doc in docs:
        current = best.get(doc.code.upper())
        if current is None or doc.newer_than(current.revision):
            best[doc.code.upper()] = doc
    return sorted(best.values(), key=lambda d: d.code.upper())


def _from_index(path: str, folder: str) -> list[SopDoc]:
    docs = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            keys = {(k or "").strip().lower(): (v or "").strip()
                    for k, v in row.items()}
            code = keys.get("code") or keys.get("sop") or ""
            if not code:
                continue
            filename = keys.get("file") or keys.get("filename") or ""
            full = os.path.join(folder, filename) if filename else ""
            applies = keys.get("applies to") or keys.get("applies_to") or ""
            docs.append(SopDoc(
                code=code,
                title=keys.get("title") or keys.get("name") or code,
                revision=keys.get("revision") or keys.get("rev") or "1",
                revision_date=_parse_date(keys.get("revision date")
                                          or keys.get("date") or ""),
                path=full if full and os.path.exists(full) else "",
                applies_to=[a.strip().lower() for a in re.split(r"[;,]", applies)
                            if a.strip()],
            ))
    return docs


def _from_filenames(folder: str) -> list[SopDoc]:
    docs = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or name.startswith(("~", ".")):
            continue
        if name.lower() in (INDEX_FILENAME, CLIENTS_FILENAME, LOG_FILENAME):
            continue
        if not name.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx",
                                      ".ppt", ".pptx", ".png", ".jpg", ".txt")):
            continue
        stem = os.path.splitext(name)[0]
        revision_match = _REVISION.search(stem)
        revision = revision_match.group(1) if revision_match else "1"
        cleaned = _REVISION.sub("", stem).strip(" _-()")
        code_match = _CODE.match(cleaned)
        code = code_match.group(1).strip() if code_match else cleaned[:20]
        title = cleaned[len(code):].strip(" _-") if code_match else cleaned
        docs.append(SopDoc(code=code, title=title.replace("_", " ").replace("-", " ").strip(),
                           revision=revision, path=path,
                           revision_date=_file_date(path)))
    return docs


def _file_date(path: str) -> date | None:
    try:
        return date.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None


_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y")


def _parse_date(text: str) -> date | None:
    from datetime import datetime
    text = (text or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ── who gets what ─────────────────────────────────────────────────────────────

@dataclass
class ClientRule:
    who: str                       # an email address or a whole domain
    codes: list[str] = field(default_factory=list)
    annual: bool = False
    name: str = ""

    def matches(self, address: str) -> bool:
        address = (address or "").strip().lower()
        who = self.who.strip().lower().lstrip("@")
        if not address or not who:
            return False
        return address == who or address.endswith("@" + who)


def load_client_map(path: str) -> list[ClientRule]:
    """Which customers receive which documents.

    One row per customer: who, which codes, whether they want a yearly
    re-issue. A domain in the "who" column covers everyone at that company,
    which is what keeps this file short enough to be maintained by hand.
    """
    if not os.path.exists(path):
        return []
    rules = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            keys = {(k or "").strip().lower(): (v or "").strip()
                    for k, v in row.items()}
            who = (keys.get("email") or keys.get("who") or keys.get("domain")
                   or keys.get("customer email") or "")
            if not who:
                continue
            codes = keys.get("sops") or keys.get("codes") or keys.get("sop codes") or ""
            annual = (keys.get("annual") or keys.get("yearly") or "").lower()
            rules.append(ClientRule(
                who=who,
                codes=[c.strip().upper() for c in re.split(r"[;,]", codes) if c.strip()],
                annual=annual in ("yes", "y", "true", "1"),
                name=keys.get("customer") or keys.get("name") or "",
            ))
    return rules


def for_client(address: str, rules: list[ClientRule],
               library: list[SopDoc]) -> list[SopDoc]:
    """The documents this customer should hold, at their current revision."""
    wanted: list[str] = []
    for rule in rules:
        if rule.matches(address):
            wanted += rule.codes
    by_code = {d.code.upper(): d for d in library}
    out, seen = [], set()
    for code in wanted:
        doc = by_code.get(code.upper())
        if doc and doc.code.upper() not in seen:
            seen.add(doc.code.upper())
            out.append(doc)
    return out


def for_product(product: str, library: list[SopDoc]) -> list[SopDoc]:
    """Documents whose "applies to" mentions what was ordered — rule 1."""
    text = (product or "").lower()
    if not text:
        return []
    return [d for d in library
            if any(tag and tag in text for tag in d.applies_to)]


# ── the record ────────────────────────────────────────────────────────────────

def load_log(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)
                if any((v or "").strip() for v in r.values())]


def save_log(rows: list[dict], path: str) -> None:
    """Written atomically, like the register — this is an audit record, and a
    half-written audit record is worse than none."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    columns = list(LOG_COLUMNS)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
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
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise PermissionError(
            f"The SOP record is open in another program. Close "
            f"{os.path.basename(path)} in Excel and try again.") from e


def record_sent(rows: list[dict], *, doc: SopDoc, address: str, customer: str = "",
                reason: str = "", inquiry_no: str = "",
                when: date | None = None) -> dict:
    when = when or date.today()
    row = {"Date sent": when.strftime("%d-%m-%Y"), "Customer": customer,
           "Email": address, "SOP code": doc.code, "Title": doc.title,
           "Revision": doc.revision, "Reason": reason, "Inquiry no": inquiry_no}
    rows.append(row)
    return row


def last_sent(log: list[dict], address: str, code: str) -> dict | None:
    """The most recent send of one document to one address."""
    address = (address or "").strip().lower()
    code = (code or "").strip().upper()
    hits = [r for r in log
            if (r.get("Email") or "").strip().lower() == address
            and (r.get("SOP code") or "").strip().upper() == code]
    if not hits:
        return None
    hits.sort(key=lambda r: _parse_date(r.get("Date sent", "")) or date.min)
    return hits[-1]


# ── what is due to go out ─────────────────────────────────────────────────────

@dataclass
class Pending:
    address: str
    customer: str
    doc: SopDoc
    reason: str

    @property
    def line(self) -> str:
        return f"{self.doc.label} → {self.customer or self.address} ({self.reason})"


def pending(rules: list[ClientRule], library: list[SopDoc], log: list[dict],
            *, annual_days: int = 365, today: date | None = None) -> list[Pending]:
    """Everything that should go out now, with the reason for each.

    Three of the four triggers live here — never sent, revised since last sent,
    and the annual re-issue. The fourth (a customer asking by mail) is answered
    on the spot by whoever handles the reply, using for_client().
    """
    today = today or date.today()
    out = []
    by_code = {d.code.upper(): d for d in library}
    for rule in rules:
        for code in rule.codes:
            doc = by_code.get(code.upper())
            if not doc:
                continue
            previous = last_sent(log, rule.who, code)
            if previous is None:
                out.append(Pending(rule.who, rule.name, doc, "never sent"))
                continue
            held = (previous.get("Revision") or "").strip()
            if doc.newer_than(held):
                out.append(Pending(rule.who, rule.name, doc,
                                   f"they have rev {held}, current is rev {doc.revision}"))
                continue
            if rule.annual:
                when = _parse_date(previous.get("Date sent", ""))
                if when and (today - when) >= timedelta(days=annual_days):
                    out.append(Pending(rule.who, rule.name, doc,
                                       "yearly re-issue due"))
    return out


def covering_prompt(docs: list[SopDoc], customer: str = "",
                    reason: str = "", signature: str = "") -> str:
    """The mail that carries the documents. Short, and never invents policy.

    An SOP mail is one of the few pieces of writing where saying less is
    strictly better, so the model is fenced in hard: it may name the documents
    and nothing else. Describing what is *inside* a process document it has
    never read is exactly the failure to design out.
    """
    listing = "\n".join(f"  - {d.code} {d.title} (revision {d.revision})"
                        for d in docs) or "  - (none)"
    why = {"never sent": "they are being sent for the first time",
           "yearly re-issue due": "this is the yearly re-issue"}.get(
        reason, reason or "they have been requested")
    return (
        "Write a very short covering email for some standard operating "
        "procedure documents being sent to a customer. Reply with NOTHING "
        "except the email, in exactly this format:\n\n"
        "SUBJECT: <one subject line>\n"
        "BODY:\n"
        "<the email body>\n\n"
        "Rules:\n"
        "- Two short paragraphs at most.\n"
        "- Name the documents exactly as listed below, including the revision "
        "number. Do NOT describe what is inside them — you have not read "
        "them, and guessing at the contents of a process document is worse "
        "than saying nothing.\n"
        "- Do not invent any policy, standard, certification or commitment.\n"
        "- Ask them to replace any earlier revision they hold.\n"
        "- Plain business English as used in Indian manufacturing. No "
        "marketing language.\n\n"
        f"Customer: {customer or 'the customer'}\n"
        f"Why now: {why}\n"
        f"Documents attached:\n{listing}\n"
        f"Sign off as: {signature or 'the quality team'}\n"
    )
