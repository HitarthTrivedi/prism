"""What language the AI tools should answer in.

Separate from the language the *app* is drawn in — that is the GUI's business
(prism_gui/i18n.py) and the CLI does not have it at all. This is the other
half: the user has said "reply in Gujarati", and every prompt that goes out to
a tool needs to say so.

It lives in the engine rather than in the GUI because the engine is what
assembles prompts, and because the CLI shares it. The GUI's i18n module keeps
the presentation side (endonyms, right-to-left, which packs are installed) and
takes the names here as the source of truth for the codes themselves.
"""
from __future__ import annotations

# Language codes Prism can ask a tool to write in, and the English name to ask
# with. English is what the models resolve most reliably — asking for a reply
# "in ગુજરાતી" is a worse prompt than asking for one "in Gujarati".
NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "gu": "Gujarati",
    "mr": "Marathi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ar": "Arabic",
}


def directive(code: str) -> str:
    """The line appended to a prompt so the answer comes back in `code`.

    Empty for an unset or unknown code, which is the "leave it alone" default:
    the tool then answers in whatever language the task was written in, which
    is what most people actually want.

    The carve-out matters as much as the instruction. A run whose output feeds
    an email step will have addresses and links in it, and a model told to
    write everything in Hindi will cheerfully transliterate a domain name into
    Devanagari and leave the user with an address that bounces.
    """
    name = NAMES.get((code or "").strip().lower())
    if not name or code == "en":
        # English needs no directive: it is what the tools do unprompted, and
        # a redundant instruction is prompt budget spent for nothing.
        return "" if not name else (
            "LANGUAGE — write your entire answer in English."
        )
    return (
        f"LANGUAGE — write your entire answer in {name}. This applies to the "
        f"whole reply: headings, body text, lists, and any summary or handoff "
        f"section at the end. Do NOT translate or transliterate email "
        f"addresses, URLs, domain names, file names, brand names or code — "
        f"reproduce those exactly as they are, in the original script. If you "
        f"are asked for a block with fixed field names, keep the field names "
        f"in English and write only the values in {name}."
    )
