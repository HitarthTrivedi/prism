"""
Prism — reel assets
───────────────────
Turns what the client actually gave you into things a reel can put on screen.

The important decision here is what NOT to ask an AI for. A model asked to
"make the logo without a background" redraws the logo, and a redrawn logo is
the wrong logo — close enough to look right in a preview and wrong enough to
embarrass whoever posts it. So the client's own marks are EXTRACTED from the
artwork they supplied, by code, pixel for pixel.

Generated imagery is still welcome; it just has a different job. Anything
decorative — a texture, a field at dusk, an abstract backdrop — can come from
an image stage upstream and arrives here as an ordinary file. Anything that
IS the client (their logo, their card, their product) is taken from what they
sent.

Same split as everywhere else in Prism: code establishes the facts, the AI
decides what to do with them.
"""
from __future__ import annotations
import os
import tempfile

# A colour no logo contains, used to mark background during the flood fill.
_SENTINEL = (1, 2, 3)


def _corner_colours(im):
    w, h = im.size
    return [im.getpixel(p) for p in
            ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]


def _close(a, b, tol: int) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a[:3], b[:3]))


def has_flat_background(path: str, tol: int = 26) -> bool:
    """True when the four corners agree — i.e. the artwork sits on a plain
    background that can be removed exactly. A photograph does not, and
    pretending otherwise would eat half the image."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
    except Exception:
        return False
    corners = _corner_colours(im)
    return all(_close(corners[0], c, tol) for c in corners[1:])


def cutout(src: str, out_dir: str | None = None, tol: int = 26) -> str | None:
    """Strip a flat background and trim to the artwork. Returns a PNG path.

    The background colour is cleared EVERYWHERE it appears, not only where it
    touches the edge — because inside a mark it is a knockout. The counters of
    a letter O, the gap in a monogram, the hole through a ring: all of those
    are the card showing through, and they have to show the reel through
    instead. Keeping them opaque leaves cream-filled letters on a dark scene,
    which is exactly how a logo looks wrong.

    The safe fallback is still there: if clearing every matching pixel would
    erase almost the whole image — a mark drawn IN the background colour on a
    tinted panel — it falls back to clearing only what is connected to the
    edge, which can never eat the artwork.

    Returns None when the background is not flat, rather than returning a
    mangled image: a photo has no background to remove, and saying so is more
    useful than half-erasing it.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    if not has_flat_background(src, tol):
        return None
    try:
        im = Image.open(src).convert("RGB")
    except Exception:
        return None

    w, h = im.size
    bg = _corner_colours(im)[0]
    px = im.load()

    alpha = Image.new("L", (w, h), 255)
    apx = alpha.load()
    cleared = 0
    for y in range(h):
        for x in range(w):
            if _close(px[x, y], bg, tol):
                apx[x, y] = 0
                cleared += 1

    # The guard is "did any artwork survive", NOT "how much was cleared": a
    # small mark on a large card legitimately clears 95% of the canvas, and
    # measuring the cleared share sent every ordinary logo down the fallback
    # path with its counters left opaque.
    if (w * h) - cleared < (w * h) * 0.004:
        # Nothing left: the artwork itself is in the background colour. Clear
        # only what the outside can reach.
        work = im.copy()
        for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                     (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)):
            try:
                ImageDraw.floodfill(work, seed, _SENTINEL, thresh=tol)
            except Exception:
                continue
        wpx = work.load()
        alpha = Image.new("L", (w, h), 255)
        apx = alpha.load()
        cleared = 0
        for y in range(h):
            for x in range(w):
                if wpx[x, y] == _SENTINEL:
                    apx[x, y] = 0
                    cleared += 1

    if cleared < (w * h) * 0.04:
        return None          # nothing meaningful was background

    out = im.convert("RGBA")
    out.putalpha(alpha)
    box = out.getbbox()
    if box:
        out = out.crop(box)

    out_dir = out_dir or tempfile.mkdtemp(prefix="prism_assets_")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(
        out_dir, os.path.splitext(os.path.basename(src))[0] + "_cut.png")
    out.save(dest, "PNG")
    return dest


def _ink_ratio(path: str) -> float:
    """Share of the image that is actually drawn on. A logo is mostly empty
    once its background is gone; a photo or a full card is not — which is how
    the logo is picked out of a pile of attachments without asking anyone."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA")
    except Exception:
        return 1.0
    im.thumbnail((160, 160))
    px = im.load()
    w, h = im.size
    on = sum(1 for y in range(h) for x in range(w) if px[x, y][3] > 24)
    return on / max(1, w * h)


def collect(images: list, out_dir: str | None = None,
            generated: set | None = None) -> dict:
    """Build the asset table the design stage is allowed to reference.

    Names are derived from the files in a fixed order, so the same inputs
    always produce the same names — the design stage is told what exists
    before the render, and the renderer resolves the same names afterwards
    without either of them passing paths around.

    Returns {name: {"path", "w", "h", "alpha", "kind"}}.
    """
    try:
        from PIL import Image
    except Exception:
        return {}
    out_dir = out_dir or os.path.join(tempfile.gettempdir(), "prism_reel_assets")
    os.makedirs(out_dir, exist_ok=True)

    paths = []
    for item in images or []:
        p = item.get("path") if isinstance(item, dict) else item
        if p and str(p).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            paths.append(p)
    if not paths:
        return {}

    # Order is meaning here, so dedupe without sorting: the imagery stage is
    # asked for the mark FIRST and the subject art after it, and the page is
    # harvested top to bottom. Sorting by filename threw that away.
    seen, ordered = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            ordered.append(p)

    prepared = []
    for p in ordered:
        cut = cutout(p, out_dir)
        use = cut or p
        try:
            with Image.open(use) as im:
                w, h = im.size
                alpha = im.mode in ("RGBA", "LA")
        except Exception:
            continue
        prepared.append({"path": use, "w": w, "h": h, "alpha": bool(cut),
                         "ink": _ink_ratio(use) if cut else 1.0,
                         "source": p, "made": p in (generated or ())})

    if not prepared:
        return {}

    table, rest = {}, list(prepared)

    # A mark the CLIENT supplied always wins the logo slot — theirs is the
    # real one. Among their files it is the sparsest cut-out: a mark leaves
    # mostly transparency behind, a photograph leaves none.
    own_marks = [a for a in rest if not a["made"] and a["alpha"] and a["ink"] < 0.55]
    logo = min(own_marks, key=lambda a: a["ink"]) if own_marks else None

    # Failing that, the FIRST generated image is the mark — not the sparsest.
    # Density does not identify a logo among generated art: a line drawing of
    # a sprout is sparser than a wordmark, and picking by sparsity put the
    # sprout in the logo slot. The imagery stage is told to produce the mark
    # first, and the page is harvested in order, so order is the answer.
    if logo is None:
        made = [a for a in rest if a["made"]]
        if made:
            logo = made[0]

    if logo is not None:
        rest.remove(logo)
        table["logo"] = {**logo, "kind": "logo"}
    for i, a in enumerate(rest, 1):
        table[f"art{i}"] = {**a, "kind": "art"}
    return table


# What the design stage is told when the imagery never arrived.
#
# Silence is not neutral here, and that was the bug. An empty asset list left
# the prompt simply not mentioning pictures, so a model asked for a premium
# product reel assumed the usual ones existed and wrote src='asset:art1' —
# which renders as an empty box, or nothing at all, in every scene that used
# it. The reel came out looking broken rather than looking spare.
#
# Image generation fails for ordinary reasons — a quota, a content refusal, a
# slow render that never finished — and none of them should cost the customer
# the whole reel. A type-and-colour reel is a legitimate design, and several of
# the best ones are exactly that. It just has to be designed ON PURPOSE.
NO_ARTWORK = """  (none — no imagery was generated for this reel)

THERE ARE NO IMAGES. This is not an oversight and it is not a reason to stop:
design this reel entirely from type, colour, layout and CSS.

  - Do NOT reference asset:anything. There are no files behind those names,
    and every one you write becomes an empty box on screen.
  - Do NOT leave space for pictures that are coming later. Nothing is coming.
  - DO carry the whole design on typography: scale, weight, hierarchy,
    generous negative space, rules and dividers, colour fields, gradients,
    and shapes built in CSS (borders, radii, transforms, clip-path).
  - A spare, confident, type-led reel is a legitimate and often superior
    design. Make it look deliberate, not like something is missing."""


def manifest(table: dict) -> str:
    """The asset list as the design stage is told about it — names, sizes and
    whether the background is really gone.

    An empty table returns the NO_ARTWORK instruction rather than an empty
    string: the design stage has to be told that the pictures are not coming,
    or it designs around ones that never arrive.
    """
    if not table:
        return NO_ARTWORK
    lines = []
    for name, a in table.items():
        if a.get("made"):
            what = ("a mark generated for this reel" if a["kind"] == "logo"
                    else "imagery generated for this reel")
        else:
            what = ("the client's own mark, taken from their artwork"
                    if a["kind"] == "logo" else "artwork the client supplied")
        cut = "transparent PNG" if a["alpha"] else "opaque, has its own background"
        lines.append(f'  asset:{name} — {a["w"]}x{a["h"]}, {cut} — {what}')
    return "\n".join(lines)
