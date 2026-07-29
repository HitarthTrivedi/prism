"""
Prism — email blasts (/email)
─────────────────────────────
Mail-merge through the pipeline: the user attaches a CSV of recipients and a
source document (e.g. a brochure PDF). The pipeline analyses the document,
the drafting agent is locked to output ONLY the email (strict SUBJECT/BODY
format — nothing else survives into the send), and then Prism itself sends
the email to every address in the CSV through the user's own account (SMTP,
stdlib smtplib — no new dependencies).

The CSV is never shown to any AI: recipients are parsed locally, so the
address list never leaves this machine.
"""
from __future__ import annotations
import csv
import io
import re
import ssl
import time
import smtplib
from email.message import EmailMessage
from . import ui

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NAME_HEADERS = ("name", "first name", "firstname", "first_name", "full name",
                 "full_name", "fullname", "contact", "person")

# Known providers → (smtp host, port). 465 = SSL, 587 = STARTTLS.
_SMTP_HOSTS = {
    "gmail.com": ("smtp.gmail.com", 465),
    "googlemail.com": ("smtp.gmail.com", 465),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
    "live.com": ("smtp-mail.outlook.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 465),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
    "zoho.com": ("smtp.zoho.com", 465),
}

# Pause between sends — keeps providers from flagging the account for bursts.
SEND_DELAY = 2.0


# ── recipients (parsed locally — the CSV never reaches any AI) ────────────────

def split_attachments(attachments: list[dict]):
    """(csv attachments, everything else). CSVs hold recipients; the rest is
    source material for the pipeline."""
    csvs = [a for a in attachments if a["name"].lower().endswith(".csv")]
    others = [a for a in attachments if a not in csvs]
    return csvs, others


def parse_recipients(path: str) -> list[dict]:
    """Extract [{'email', 'name'}, …] from any reasonable CSV: with or without
    a header row, whatever column the addresses live in. Deduped, in order."""
    with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    if not rows:
        return []

    width = max(len(r) for r in rows)
    cell = lambda r, i: r[i].strip() if i < len(r) else ""

    # The email column is the one with the most address-looking cells.
    email_col, best = None, 0
    for i in range(width):
        hits = sum(1 for r in rows if _EMAIL_RE.search(cell(r, i)))
        if hits > best:
            email_col, best = i, hits
    if email_col is None:
        return []

    # Header row = first row whose email cell isn't an address.
    has_header = not _EMAIL_RE.search(cell(rows[0], email_col))
    header = [c.strip().lower() for c in rows[0]] if has_header else []
    data = rows[1:] if has_header else rows

    # Name column: a name-ish header if there is one, else the first other
    # column that holds mostly letters (not numbers/URLs).
    name_col = None
    for i, h in enumerate(header):
        if h in _NAME_HEADERS:
            name_col = i
            break
    if name_col is None:
        for i in range(width):
            if i == email_col:
                continue
            alpha = sum(1 for r in data
                        if cell(r, i) and re.fullmatch(r"[A-Za-z .'-]+", cell(r, i)))
            if data and alpha >= max(1, len(data) // 2):
                name_col = i
                break

    out, seen = [], set()
    for r in data:
        m = _EMAIL_RE.search(cell(r, email_col))
        if not m:
            continue
        email = m.group(0).lower()
        if email in seen:
            continue
        seen.add(email)
        out.append({"email": email,
                    "name": cell(r, name_col) if name_col is not None else ""})
    return out


def recipients_from_text(text: str):
    """Addresses typed straight into the /email prompt ("… send to a@x.com and
    b@y.com") become recipients. Returns (recipients, text with the addresses
    removed) — like the CSV, addresses are never shown to any AI."""
    recs, seen = [], set()
    for e in _EMAIL_RE.findall(text):
        e = e.lower()
        if e not in seen:
            seen.add(e)
            recs.append({"email": e, "name": ""})
    cleaned = _EMAIL_RE.sub("", text)
    cleaned = re.sub(r"\s*(?:,|;|\band\b)?\s*(?:,|;|\band\b)\s*(?=$|[,;.])", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;&")
    return recs, cleaned


# ── discovered recipients (a research agent found them, not the user) ────────

def discovery_prompts(goal: str) -> tuple[str, str]:
    """(research prompt, structuring prompt) for finding recipients that match
    a description — "the best-suited agencies in Vadodara", not one named
    org. Two stages because a research agent free-writes prose; asking it to
    also format strict CSV in the same breath tends to lose rows. Splitting
    the jobs is exactly the 'let stages cycle back through agents' pattern —
    the structuring stage often reuses whichever agent already ran."""
    research = (
        "Your ONLY task is: based on the brief above, find real, currently "
        f"operating businesses that match this request: {goal}. Search the "
        "web for actual candidates — do not invent any. For each one you are "
        "reasonably confident is real, list: Name, Website, a public contact "
        "email ONLY if you actually found one (write exactly 'unknown' if you "
        "did not — never guess or construct one from a pattern), and a "
        "one-line reason it fits. One candidate per line, in the form: "
        "Name — Website — Email — Reason. List every genuine candidate you "
        "can find; do not pad the list to hit a number."
    )
    structure = (
        "Take the candidate list from the previous stage and convert it to a "
        "strict CSV. Reply with NOTHING except the CSV — no commentary, no "
        "markdown fences. First line exactly: name,website,email,reason. "
        "Then one row per candidate that has a real email address — DROP any "
        "candidate whose email is 'unknown', blank, or that you are not "
        "confident is real. Quote any field that contains a comma. If no "
        "candidate has a confirmed email, reply with just the header row."
    )
    return research, structure


def parse_structured_csv_text(text: str) -> list[dict]:
    """Pull [{'name','email','website','reason'}, …] out of the structuring
    stage's reply. It's told to answer with ONLY a
    'name,website,email,reason' CSV, but a scrape can still carry a stray
    fence or a leaked comment around it — keep only well-formed rows that
    contain an actual email address, so prose that slipped through can't
    masquerade as a recipient."""
    if not text:
        return []
    t = re.sub(r"^```[a-z]*\n|\n```$", "", text.strip())
    out = []
    for row in csv.reader(io.StringIO(t)):
        cells = [c.strip() for c in row]
        email = next((c for c in cells if _EMAIL_RE.fullmatch(c)), None)
        if not email:
            continue
        rest = [c for c in cells if c != email]
        out.append({
            "email": email.lower(),
            "name": rest[0] if rest else "",
            "website": rest[1] if len(rest) > 1 else "",
            "reason": rest[2] if len(rest) > 2 else "",
        })
    seen, dedup = set(), []
    for r in out:
        if r["email"] not in seen:
            seen.add(r["email"])
            dedup.append(r)
    return dedup


def write_recipients_csv(rows: list[dict], path: str) -> None:
    """Persist discovered recipients to a real CSV on disk — a durable record
    the user can open, edit or hand to someone else, and (since it's a plain
    recipients CSV) re-attach to a future /email run via parse_recipients()."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "website", "email", "reason"])
        for r in rows:
            w.writerow([r.get("name", ""), r.get("website", ""),
                       r.get("email", ""), r.get("reason", "")])


# ── the draft (what the AI produced) ──────────────────────────────────────────

# Fingerprints of the instructions Prism itself typed into the tool. A scrape
# that contains any of them is our own prompt read back off the page — several
# tools render the user's message with the same CSS classes as the reply — and
# it parses as a perfectly valid draft whose subject is "<one subject line>".
_PROMPT_MARKERS = (
    "your only task is",
    "reply with nothing except",
    "<one subject line>",
    "<the full email body>",
    "strict pipeline rules",
    "every character you output will be sent",
)


def is_prompt_echo(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _PROMPT_MARKERS)


def parse_draft(text: str):
    """Pull (subject, body) out of the drafting agent's answer. The agent is
    ordered to reply in exactly 'SUBJECT: …' / 'BODY: …' — but scrapes can
    carry stray fences or a leaked handoff, so be forgiving on the way in."""
    if not text or not text.strip():
        return None
    t = text.strip()
    t = re.sub(r"^```[a-z]*\n|\n```$", "", t)                    # markdown fences
    t = re.split(r"\n\s*HANDOFF\b", t, flags=re.IGNORECASE)[0]   # leaked handoff

    # Take the LAST SUBJECT: on the page, not the first. When the capture also
    # holds the prompt (or the tool restated the format before answering), the
    # earlier ones are the template and the real draft is the final block.
    # Tools love to bold the labels: **SUBJECT:** … / **BODY:** …
    for m in reversed(list(re.finditer(r"^[ \t]*\**\s*SUBJECT\s*\**\s*:\s*\**(.+)$",
                                       t, re.IGNORECASE | re.MULTILINE))):
        subject = m.group(1).strip().strip("*").strip()
        # A placeholder straight out of our own instructions is never a draft.
        if not subject or subject.startswith("<") or is_prompt_echo(subject):
            continue
        rest = t[m.end():]
        b = re.search(r"^[ \t]*\**\s*BODY\s*\**\s*:\s*\**[ \t]*\n?", rest,
                      re.IGNORECASE | re.MULTILINE)
        body = rest[b.end():].strip() if b else rest.strip()
        if body and not body.startswith("<") and not is_prompt_echo(body):
            return subject, body
    return None


# ── account setup ─────────────────────────────────────────────────────────────

def smtp_for(address: str):
    domain = address.rsplit("@", 1)[-1].lower()
    return _SMTP_HOSTS.get(domain)


# Google shows app passwords as four groups of four — "abcd efgh ijkl mnop" —
# and people paste them exactly as shown, spaces and all. Gmail's SMTP then
# rejects the login with the same 535 it gives a wrong password, which is the
# single most common reason sending "just doesn't work".
_APP_PASSWORD = re.compile(r"^([A-Za-z0-9]{4}[ \t ]){3}[A-Za-z0-9]{4}$")


def clean_password(password: str) -> str:
    """Trim a pasted password. Outer whitespace always goes; inner spaces go
    only when the string is exactly an app password's shape, because a real
    passphrase is allowed to contain spaces and we must not corrupt it."""
    p = (password or "").strip().replace(" ", " ")
    if _APP_PASSWORD.match(p):
        return re.sub(r"\s+", "", p)
    return p


def explain_error(error: str, address: str = "") -> str:
    """Turn an smtplib failure into the sentence that actually unblocks the
    user. The raw text ('(535, b\\'5.7.8 Username and Password not accepted\\')')
    says nothing about app passwords, which is what it almost always means."""
    e = (error or "").lower()
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    if "535" in e or "auth" in e or "username and password" in e:
        if domain in ("gmail.com", "googlemail.com"):
            return ("Google rejected the sign-in. Gmail needs a 16-character "
                    "APP PASSWORD (not your Google password), created at "
                    "myaccount.google.com/apppasswords with 2-Step "
                    "Verification switched on.")
        if domain in ("outlook.com", "hotmail.com", "live.com"):
            return ("Microsoft rejected the sign-in. Personal Outlook accounts "
                    "no longer allow SMTP passwords — you need an app password "
                    "from account.microsoft.com/security, or a different "
                    "sending account.")
        if domain in ("yahoo.com",):
            return ("Yahoo rejected the sign-in. Generate an app password "
                    "under Account Security → App passwords.")
        return ("The server rejected that address/password. Most providers "
                "require an app password for SMTP rather than your normal one.")
    if "certificate" in e or "ssl" in e:
        return ("TLS handshake failed — check the port: 465 is SSL, 587 is "
                "STARTTLS. Using the wrong one for your host fails like this.")
    if "getaddrinfo" in e or "name or service" in e or "resolve" in e:
        return "Couldn't resolve the SMTP host — check it for typos."
    if "timed out" in e or "timeout" in e:
        return ("The mail server didn't answer. Some networks block SMTP "
                "ports — try another connection, or port 587 instead of 465.")
    return error


def is_configured(cfg: dict) -> bool:
    ec = cfg.get("email") or {}
    return bool(ec.get("address") and ec.get("password") and ec.get("host"))


def verify(cfg: dict) -> str:
    """Open a session and log in, then hang up. Returns "" on success or a
    human error. Credentials are otherwise only ever tested by a real blast,
    which is the worst moment to discover that Gmail wants an app password."""
    ec = (cfg or {}).get("email") or {}
    if not is_configured(cfg or {}):
        return "No sending account is set up yet."
    try:
        server = _connect(ec, timeout=30)
    except Exception as e:
        return explain_error(str(e), ec.get("address", ""))
    try:
        server.quit()
    except Exception:
        pass
    return ""


# ── sending ───────────────────────────────────────────────────────────────────

def _connect(ec: dict, timeout: int = 60):
    if int(ec["port"]) == 465:
        server = smtplib.SMTP_SSL(ec["host"], 465, timeout=timeout,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(ec["host"], int(ec["port"]), timeout=timeout)
        server.starttls(context=ssl.create_default_context())
    # Cleaned here as well as at save time, so an account stored by an older
    # build (spaced app password → permanent 535) starts working by itself.
    server.login(ec["address"].strip(), clean_password(ec["password"]))
    return server


def _send_timeout(files: list[dict]) -> int:
    """Socket timeout scaled to the attachment payload. Uploading a big PDF
    can take minutes on a slow uplink — with a short timeout the socket dies
    mid-transfer and smtplib reports the misleading 'Server not connected'.
    Budget: worst-case ~20 KB/s on the base64-inflated (×1.4) size."""
    total = 0
    for f in files:
        try:
            import os
            total += os.path.getsize(f["path"])
        except Exception:
            pass
    return max(60, min(900, int(total * 1.4 / 20_000)))


def _build_message(ec, recipient, subject, body, files):
    name = (recipient.get("name") or "").strip() or "there"
    msg = EmailMessage()
    msg["From"] = ec["address"]
    msg["To"] = recipient["email"]
    msg["Subject"] = subject.replace("{name}", name)
    msg.set_content(body.replace("{name}", name))
    for f in files:
        with open(f["path"], "rb") as fh:
            data = fh.read()
        maintype, _, subtype = (f.get("mime") or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=f["name"])
    return msg


def send_bulk(cfg: dict, recipients: list[dict], subject: str, body: str,
              files: list[dict], delay: float = SEND_DELAY,
              on_progress=None, should_stop=None):
    """Send the draft to every recipient, one message each (so {name} can be
    personalised and one bad address can't sink the rest).
    Returns (sent emails, [(email, error), …]).

    on_progress(i, total, email, ok, error) is called after every attempt, and
    should_stop() is polled between them — a blast of 200 addresses takes
    minutes at SEND_DELAY, and the GUI needs both a live count and a way out.
    Neither is used by the CLI, which has ui.* and Ctrl-C for the same jobs."""
    ec = cfg["email"]
    timeout = _send_timeout(files)
    if timeout > 60:
        ui.info(f"   📦  large attachment(s) — allowing up to {timeout}s per send")
    server = _connect(ec, timeout)
    sent, failed = [], []

    def report(i, r, ok, error=""):
        if on_progress:
            on_progress(i, len(recipients), r["email"], ok, error)

    try:
        for i, r in enumerate(recipients, 1):
            if should_stop and should_stop():
                ui.warn(f"stopped after {len(sent)} send(s) — "
                        f"{len(recipients) - i + 1} not attempted")
                break
            msg = _build_message(ec, r, subject, body, files)
            try:
                server.send_message(msg)
                sent.append(r["email"])
                ui.info(f"   ✉️   {i}/{len(recipients)}  {r['email']}")
                report(i, r, True)
            except smtplib.SMTPServerDisconnected:
                # Provider dropped the connection mid-run — reconnect once.
                try:
                    server = _connect(ec, timeout)
                    server.send_message(msg)
                    sent.append(r["email"])
                    ui.info(f"   ✉️   {i}/{len(recipients)}  {r['email']}  (reconnected)")
                    report(i, r, True)
                except Exception as e:
                    failed.append((r["email"], str(e)))
                    ui.err(f"   ✗   {r['email']}: {e}")
                    report(i, r, False, str(e))
            except Exception as e:
                failed.append((r["email"], str(e)))
                ui.err(f"   ✗   {r['email']}: {e}")
                report(i, r, False, str(e))
            if i < len(recipients):
                # Split the pause so a cancel lands in ~a quarter second
                # instead of after the full provider-friendly delay.
                waited = 0.0
                while waited < delay:
                    if should_stop and should_stop():
                        break
                    time.sleep(min(0.25, delay - waited))
                    waited += 0.25
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return sent, failed


# ── the pipeline prompts (kept here so the wording lives with the feature) ────

def draft_question(instruction: str) -> str:
    return (
        f"Your ONLY task is: write ONE email. Goal of the email: {instruction}. "
        "Reply with NOTHING except the final email, in EXACTLY this format:\n\n"
        "SUBJECT: <one subject line>\n"
        "BODY:\n"
        "<the full email body>\n\n"
        "Strict rules: no introduction, no explanation, no notes, no options or "
        "alternatives, no markdown code fences, and no placeholders except "
        "{name}. Address the reader as {name} — it will be replaced with each "
        "recipient's real name before sending. Every character you output will "
        "be sent to real recipients exactly as written."
    )
