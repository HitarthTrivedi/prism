"""
Prism — reading the inbox (IMAP)
────────────────────────────────
The other half of mailer.py. That module sends through the user's own account
over SMTP; this one reads the same account over IMAP, with the stdlib and no
new dependencies.

Two rules this module will not break:

  1. **Prism never changes the state of anybody's mailbox.** The folder is
     opened read-only and every fetch uses BODY.PEEK, so nothing is marked as
     read, moved or deleted. The owner still uses Outlook or their phone on the
     same account, and a tool that silently marked mail as read would make them
     miss a real order. Prism tracks where it got to on its own — see State.

  2. **Nothing here talks to an AI.** Fetching is plumbing. Deciding what a
     message *is* happens in triage.py, which sorts most mail locally and only
     sends out what it genuinely cannot place. Keeping the two apart is what
     makes "most of your mail never leaves this computer" a true statement
     rather than a hopeful one.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import ssl
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from datetime import datetime, timedelta, timezone

# Known providers → (imap host, port). Mirrors mailer._SMTP_HOSTS; 993 is the
# SSL port and effectively universal — plaintext 143 is not offered at all.
_IMAP_HOSTS = {
    "gmail.com": ("imap.gmail.com", 993),
    "googlemail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "me.com": ("imap.mail.me.com", 993),
    "zoho.com": ("imap.zoho.com", 993),
    "zohomail.in": ("imap.zoho.in", 993),
    "rediffmail.com": ("imap.rediffmail.com", 993),
}

# How far back the very first fetch reaches. A mailbox with eight years in it
# would otherwise be downloaded in full on day one — minutes of waiting, and
# an inquiry register suddenly full of business that closed in 2019.
FIRST_FETCH_DAYS = 30

# Ceiling on one fetch. Not a limit on the mailbox — the next run picks up from
# where this one stopped — just a guarantee that no single check can hang for
# ten minutes because somebody was on holiday for a fortnight.
MAX_PER_FETCH = 200

# Body text kept per message. Enough for any inquiry; short enough that a
# 4 MB marketing mailshot with the whole catalogue inlined cannot blow up
# memory, the log, or an AI prompt.
MAX_BODY_CHARS = 20_000


# ── account setup ─────────────────────────────────────────────────────────────

def imap_for(address: str):
    """(host, port) for a known consumer provider, else None."""
    domain = address.rsplit("@", 1)[-1].lower()
    return _IMAP_HOSTS.get(domain)


def guess_hosts(address: str) -> list[str]:
    """Host names to try for a company-domain address.

    Most of GIDC is on `something@theircompany.co.in`, hosted on ordinary
    cPanel-style hosting where the server is `mail.` or `imap.` in front of
    their own domain. Trying the three obvious ones turns a support call
    ("what is my IMAP server?" — they do not know, and neither does their web
    designer) into a spinner that resolves itself.
    """
    known = imap_for(address)
    if known:
        return [known[0]]
    domain = address.rsplit("@", 1)[-1].lower().strip()
    if not domain:
        return []
    return [f"imap.{domain}", f"mail.{domain}", domain]


def is_configured(cfg: dict) -> bool:
    ic = (cfg or {}).get("inbox") or {}
    return bool(ic.get("address") and ic.get("password") and ic.get("host"))


def explain_error(error: str, address: str = "") -> str:
    """Turn an imaplib failure into the sentence that unblocks the user.

    Same job as mailer.explain_error, and the same reasoning: the raw text
    ('AUTHENTICATIONFAILED') names no cause, and the cause is nearly always
    that the provider wants an app password rather than the normal one.
    """
    e = (error or "").lower()
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    if "authenticationfailed" in e or "invalid credentials" in e or "login failed" in e:
        if domain in ("gmail.com", "googlemail.com"):
            return ("Google rejected the sign-in. Gmail needs a 16-character "
                    "APP PASSWORD (not your Google password), created at "
                    "myaccount.google.com/apppasswords with 2-Step "
                    "Verification switched on.")
        if domain in ("outlook.com", "hotmail.com", "live.com"):
            return ("Microsoft rejected the sign-in. Outlook accounts need an "
                    "app password from account.microsoft.com/security, and "
                    "some company accounts have IMAP switched off entirely — "
                    "your IT person can turn it back on.")
        return ("The server rejected that address and password. Many providers "
                "need an app password for mail programs rather than the "
                "password you type on their website.")
    if "imap" in e and ("disabled" in e or "not enabled" in e):
        return ("IMAP is switched off for this mailbox. It is a single setting "
                "in the mail provider's control panel — the same one Outlook "
                "and phones need.")
    if "certificate" in e or "ssl" in e or "wrong version number" in e:
        return ("Secure connection failed. Port 993 is the normal one for "
                "reading mail; check the port and the server name.")
    if "getaddrinfo" in e or "name or service" in e or "resolve" in e or "nodename" in e:
        return ("Couldn't find that mail server. Check the server name for "
                "typos — it is usually mail.yourcompany.com.")
    if "timed out" in e or "timeout" in e:
        return ("The mail server didn't answer. Some office networks block "
                "mail ports; try another connection or ask your provider "
                "whether IMAP is open.")
    return error or "Couldn't read the mailbox."


def _connect(ic: dict, timeout: int = 60):
    host = ic["host"]
    port = int(ic.get("port") or 993)
    if port == 993:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout,
                                 ssl_context=ssl.create_default_context())
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        conn.starttls(ssl.create_default_context())
    # Shares mailer.clean_password: app passwords are shown in groups of four
    # and get pasted with the spaces in, which fails like a wrong password.
    from .mailer import clean_password
    conn.login(ic["address"].strip(), clean_password(ic["password"]))
    return conn


def verify(cfg: dict) -> str:
    """Log in, look at the inbox, hang up. "" on success, else a human error.

    Worth its own button: the alternative is discovering at 9 a.m. on the first
    Monday that IMAP was never enabled on the account.
    """
    ic = (cfg or {}).get("inbox") or {}
    if not is_configured(cfg or {}):
        return "No mail account is set up for reading yet."
    try:
        conn = _connect(ic, timeout=30)
    except Exception as e:
        return explain_error(str(e), ic.get("address", ""))
    try:
        typ, _ = conn.select(ic.get("folder") or "INBOX", readonly=True)
        if typ != "OK":
            return (f"Signed in, but couldn't open the "
                    f"'{ic.get('folder') or 'INBOX'}' folder.")
    except Exception as e:
        return explain_error(str(e), ic.get("address", ""))
    finally:
        _hangup(conn)
    return ""


def discover(address: str, password: str, timeout: int = 20) -> tuple[dict, str]:
    """Find the settings for an address by trying the likely servers.

    Returns ({host, port, address, password}, "") or ({}, human error). This is
    the whole of "set up my mail" for a company domain: they type the two
    things they know and Prism works out the rest.
    """
    last = ""
    for host in guess_hosts(address):
        ic = {"address": address, "password": password, "host": host, "port": 993}
        try:
            conn = _connect(ic, timeout=timeout)
        except Exception as e:
            last = str(e)
            # A refused connection means "wrong server, try the next one". A
            # rejected password means the server was right and guessing more
            # hosts is pointless — stop and say so.
            if "authenticationfailed" in last.lower() or "invalid credentials" in last.lower():
                return {}, explain_error(last, address)
            continue
        _hangup(conn)
        return ic, ""
    return {}, explain_error(last, address)


def _hangup(conn) -> None:
    for step in (conn.close, conn.logout):
        try:
            step()
        except Exception:
            pass


# ── where we got to last time ────────────────────────────────────────────────

@dataclass
class State:
    """Prism's own bookmark in the mailbox.

    UIDVALIDITY is the part that is easy to miss. A server is allowed to
    renumber a folder — it happens when a mailbox is rebuilt or migrated — and
    it signals that by changing UIDVALIDITY. If Prism kept using the old
    numbers it would either re-import hundreds of old mails as fresh inquiries
    or skip everything new for ever. Storing both means a renumber is detected
    and handled instead of corrupting the register.
    """
    uidvalidity: int = 0
    last_uid: int = 0

    def to_dict(self) -> dict:
        return {"uidvalidity": self.uidvalidity, "last_uid": self.last_uid}

    @classmethod
    def from_dict(cls, d: dict | None) -> "State":
        d = d or {}
        try:
            return cls(int(d.get("uidvalidity") or 0), int(d.get("last_uid") or 0))
        except (TypeError, ValueError):
            return cls()


# ── one message ───────────────────────────────────────────────────────────────

@dataclass
class Attachment:
    name: str
    mime: str
    data: bytes = b""

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class Message:
    uid: int = 0
    message_id: str = ""
    date: datetime | None = None
    from_name: str = ""
    from_addr: str = ""
    to: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    # Kept because triage reads them and because a reply has to be tied back to
    # the inquiry it answers — that thread link is what stops one conversation
    # becoming four rows in the register.
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    list_unsubscribe: str = ""
    auto_submitted: str = ""
    precedence: str = ""

    @property
    def sender_domain(self) -> str:
        return self.from_addr.rsplit("@", 1)[-1].lower() if "@" in self.from_addr else ""

    @property
    def attachment_names(self) -> list[str]:
        return [a.name for a in self.attachments]

    def snippet(self, limit: int = 1500) -> str:
        """Subject plus the opening of the body — what triage shows an AI.

        Deliberately short. Everything a classifier needs to tell an inquiry
        from a newsletter is in the first few lines, and the less of somebody's
        correspondence that leaves the building, the more honestly we can
        describe what Prism does with it.
        """
        text = re.sub(r"\n{3,}", "\n\n", (self.body or "").strip())
        if len(text) > limit:
            text = text[:limit].rstrip() + " …"
        return f"Subject: {self.subject}\n\n{text}".strip()


# ── decoding what the server sends back ──────────────────────────────────────

def _header(msg, name: str) -> str:
    """One header, decoded. Real mail carries =?UTF-8?B?…?= in Subject and
    From, and a raw one shown to the user looks like corruption."""
    raw = msg.get(name)
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return str(raw).strip()


_TAG = re.compile(r"<[^>]+>")
_STYLE_BLOCK = re.compile(r"(?is)<(script|style)\b.*?</\1>")


def html_to_text(html: str) -> str:
    """Good-enough plain text from an HTML body.

    Not a renderer — a stripper. Marketing mail is HTML-only, and the
    classifier needs the words, not the layout. Block tags become newlines so
    sentences do not run together, which is the difference between readable
    and a wall.
    """
    if not html:
        return ""
    text = _STYLE_BLOCK.sub(" ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)>", "\n", text)
    text = _TAG.sub(" ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def parse_message(raw: bytes, uid: int = 0) -> Message:
    """Turn one raw RFC-822 message into a Message.

    Split out from fetching so it can be tested against saved mail files
    without a server anywhere near it — which is the only sane way to keep a
    parser honest about the shapes real mail arrives in.
    """
    msg = email.message_from_bytes(raw)
    out = Message(uid=uid)
    out.message_id = (msg.get("Message-ID") or "").strip()
    out.subject = _header(msg, "Subject")
    out.in_reply_to = (msg.get("In-Reply-To") or "").strip()
    out.references = (msg.get("References") or "").split()
    out.list_unsubscribe = (msg.get("List-Unsubscribe") or "").strip()
    out.auto_submitted = (msg.get("Auto-Submitted") or "").strip()
    out.precedence = (msg.get("Precedence") or "").strip()

    sender = getaddresses([msg.get("From") or ""])
    if sender:
        name, addr = sender[0]
        try:
            out.from_name = str(make_header(decode_header(name))).strip()
        except Exception:
            out.from_name = name.strip()
        out.from_addr = addr.strip().lower()
    out.to = [a.lower() for _n, a in getaddresses([msg.get("To") or ""]) if a]

    try:
        out.date = parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        out.date = None

    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if filename:
            try:
                filename = str(make_header(decode_header(filename)))
            except Exception:
                pass
        # An inline image with no filename is a signature logo, not an
        # attachment worth saving to an inquiry folder.
        if filename and ("attachment" in disposition or "inline" in disposition
                         or part.get_content_maintype() != "text"):
            data = part.get_payload(decode=True) or b""
            out.attachments.append(
                Attachment(name=os.path.basename(filename),
                           mime=part.get_content_type(), data=data))
            continue
        if part.get_content_type() == "text/plain":
            plain.append(_part_text(part))
        elif part.get_content_type() == "text/html":
            html.append(_part_text(part))

    # Plain text is preferred where the sender provided it: it is what they
    # typed, without the marketing wrapper around it.
    body = "\n".join(t for t in plain if t.strip())
    if not body.strip():
        body = html_to_text("\n".join(html))
    out.body = body[:MAX_BODY_CHARS]
    return out


# ── fetching ──────────────────────────────────────────────────────────────────

def fetch_new(cfg: dict, state: State | None = None, *, limit: int = MAX_PER_FETCH,
              first_days: int = FIRST_FETCH_DAYS,
              timeout: int = 60) -> tuple[list[Message], State, str]:
    """Everything that has arrived since the last check.

    Returns (messages oldest-first, new state to save, error or "").
    Errors come back as a sentence rather than an exception because this runs
    on a timer: a mail server that is down for ten minutes must not put a
    traceback in front of somebody who is trying to run a factory.
    """
    ic = (cfg or {}).get("inbox") or {}
    if not is_configured(cfg or {}):
        return [], state or State(), "No mail account is set up for reading yet."

    state = state or State()
    try:
        conn = _connect(ic, timeout=timeout)
    except Exception as e:
        return [], state, explain_error(str(e), ic.get("address", ""))

    folder = ic.get("folder") or "INBOX"
    try:
        # readonly=True: opening the folder must not clear anybody's unread
        # flags. The owner reads the same mailbox in Outlook.
        typ, data = conn.select(folder, readonly=True)
        if typ != "OK":
            return [], state, f"Couldn't open the '{folder}' folder."

        validity = _uidvalidity(conn)
        fresh_start = (state.uidvalidity != validity) or not state.last_uid
        new_state = State(uidvalidity=validity, last_uid=state.last_uid)
        if state.uidvalidity and state.uidvalidity != validity:
            # The server renumbered the folder. Everything we remember about
            # positions is meaningless now; start again from the recent window
            # rather than re-importing years of mail as new inquiries.
            new_state.last_uid = 0

        uids = _search(conn, new_state.last_uid, first_days if fresh_start else 0)
        if not uids:
            return [], new_state, ""

        # Oldest first, so the register fills in the order things happened, and
        # so a truncated batch leaves the bookmark somewhere sensible.
        uids.sort()
        clipped = uids[:limit]

        messages = []
        for uid in clipped:
            raw = _fetch_one(conn, uid)
            if raw is None:
                continue
            try:
                messages.append(parse_message(raw, uid))
            except Exception:
                # One unparseable message must not stop the other 40. It stays
                # unread in their real mail client, which is the safe failure.
                continue
        if clipped:
            new_state.last_uid = max(new_state.last_uid, max(clipped))
        return messages, new_state, ""
    except Exception as e:
        return [], state, explain_error(str(e), ic.get("address", ""))
    finally:
        _hangup(conn)


def _uidvalidity(conn) -> int:
    try:
        typ, data = conn.response("UIDVALIDITY")
        if typ == "OK" and data and data[0]:
            return int(data[0])
    except (ValueError, TypeError, IndexError):
        pass
    return 0


def _search(conn, last_uid: int, first_days: int) -> list[int]:
    """UIDs worth fetching."""
    if last_uid:
        criteria = f"UID {last_uid + 1}:*"
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=first_days or FIRST_FETCH_DAYS))
        criteria = f'SINCE {since.strftime("%d-%b-%Y")}'
    typ, data = conn.uid("SEARCH", None, criteria)
    if typ != "OK" or not data or not data[0]:
        return []
    found = [int(x) for x in data[0].split()]
    # "UID n:*" is defined to return the last message in the folder even when
    # its UID is below n — so an idle mailbox reports its newest mail over and
    # over. Filtering here is what stops the same inquiry being registered on
    # every ten-minute check.
    return [u for u in found if u > last_uid]


def _fetch_one(conn, uid: int) -> bytes | None:
    # BODY.PEEK[] rather than BODY[]: the plain form sets the \Seen flag, and
    # marking somebody's unread mail as read behind their back is how they
    # miss an order. PEEK is the whole reason this is safe to run on a timer.
    typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
    if typ != "OK" or not data:
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) > 1 and item[1]:
            return item[1]
    return None


# ── attachments to disk ───────────────────────────────────────────────────────

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, fallback: str = "attachment") -> str:
    """A filename that cannot escape its folder or upset Windows.

    A mail attachment's name is attacker-controlled text. '../../…' and a
    Windows reserved name like CON are both live problems, and this is one of
    the few places in Prism where untrusted input becomes a path.
    """
    name = os.path.basename((name or "").strip().replace("\\", "/"))
    name = _UNSAFE.sub("_", name).strip(". ")
    if not name:
        return fallback
    stem = name.rsplit(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name[:120]


def save_attachments(msg: Message, folder: str) -> list[str]:
    """Write a message's attachments into `folder`. Returns the paths written.

    Names collide constantly — every second customer attaches "drawing.pdf" —
    so a numeric suffix is added rather than overwriting, because the file that
    would be lost is the one somebody is about to quote from.
    """
    if not msg.attachments:
        return []
    os.makedirs(folder, exist_ok=True)
    written = []
    for att in msg.attachments:
        name = safe_name(att.name)
        path = os.path.join(folder, name)
        if os.path.exists(path):
            stem, dot, ext = name.rpartition(".")
            stem = stem or name
            for n in range(2, 100):
                candidate = f"{stem}-{n}{dot}{ext}" if dot else f"{name}-{n}"
                path = os.path.join(folder, candidate)
                if not os.path.exists(path):
                    break
        try:
            with open(path, "wb") as f:
                f.write(att.data)
            written.append(path)
        except OSError:
            continue
    return written
