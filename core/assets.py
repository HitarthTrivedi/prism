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


def collect(images: list, out_dir: str | None = None) -> dict:
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

    prepared = []
    for p in sorted(set(paths)):
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
                         "source": p})

    if not prepared:
        return {}

    # The sparsest cut-out is the logo: a mark on a card leaves mostly
    # transparency behind, a photograph leaves none.
    table, rest = {}, list(prepared)
    marks = [a for a in rest if a["alpha"] and a["ink"] < 0.55]
    if marks:
        logo = min(marks, key=lambda a: a["ink"])
        rest.remove(logo)
        table["logo"] = {**logo, "kind": "logo"}
    for i, a in enumerate(rest, 1):
        table[f"art{i}"] = {**a, "kind": "art"}
    return table


def manifest(table: dict) -> str:
    """The asset list as the design stage is told about it — names, sizes and
    whether the background is really gone."""
    if not table:
        return ""
    lines = []
    for name, a in table.items():
        what = ("the client's own mark, background removed" if a["kind"] == "logo"
                else "supplied artwork")
        cut = "transparent PNG" if a["alpha"] else "opaque, has its own background"
        lines.append(f'  asset:{name} — {a["w"]}x{a["h"]}, {cut} — {what}')
    return "\n".join(lines)
