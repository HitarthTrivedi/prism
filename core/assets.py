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

Three kinds of file arrive, and the first rule is to tell them apart before
touching a pixel — see prepare():

  · a picture that already HAS transparency (an exported logo, and, since
    2026, every image ChatGPT's image tool makes). Kept exactly as it is,
    trimmed to its ink. The 2026-09-07 reel is why this rule exists: three
    generated PNGs with real alpha went through the old cut-out, which
    flattened them to RGB first and then read their (0,0,0,0) transparent
    pixels as a black background — and cleared every black letter of the
    wordmark, leaving one red "d" and some confetti on the finished reel.
  · a mark on a flat card — a JPEG logo on white. The background is
    removed: from the EDGE inward, with a soft edge and the card colour
    taken back out of the boundary pixels, so nothing arrives with a
    sawtooth outline or a white halo on a dark scene.
  · a photograph. Left alone, and SAID to be a photograph, because the one
    thing the art director cannot do well with an opaque picture is guess.
"""
from __future__ import annotations
import os
import tempfile


def _corner_colours(im):
    w, h = im.size
    return [im.getpixel(p) for p in
            ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]


def _close(a, b, tol: int) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a[:3], b[:3]))


# ── what kind of picture is this ─────────────────────────────────────────────

def alpha_coverage(im) -> tuple[float, float]:
    """(share of pixels fully transparent, share partly transparent).

    (0, 0) for anything without an alpha channel. A palette image with a
    transparency index counts — that is how a lot of old-style logo GIFs and
    8-bit PNGs carry their cut-out.
    """
    import numpy as np
    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")
    if im.mode not in ("RGBA", "LA"):
        return 0.0, 0.0
    a = np.asarray(im.getchannel("A"))
    if a.size == 0:
        return 0.0, 0.0
    return float((a == 0).mean()), float(((a > 0) & (a < 255)).mean())


def has_real_alpha(path: str) -> bool:
    """Does the file already carry a usable cut-out of its own?

    Two percent is the floor: a PNG saved with an alpha channel that is 255
    everywhere is an opaque picture that happens to be RGBA, and treating it
    as transparent would tell the art director a photo needs no backing.
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            clear, soft = alpha_coverage(im)
    except Exception:
        return False
    return clear + soft >= 0.02


def background_colour(im, tol: int = 26):
    """The single colour the picture's border is made of, or None.

    Measured on a band around the whole edge, not on four corner pixels. Four
    pixels are what a JPEG artefact, a 1px frame, or a rounded card lie about
    — and a screenshot of a web page has white in all four corners and a
    whole interface in between. The border has to be one colour nearly all
    the way round before anything is called "flat".
    """
    import numpy as np
    arr = np.asarray(im.convert("RGB"))
    h, w = arr.shape[:2]
    if h < 4 or w < 4:
        return None
    band = max(2, int(round(min(w, h) * 0.02)))
    edge = np.concatenate([
        arr[:band].reshape(-1, 3), arr[-band:].reshape(-1, 3),
        arr[:, :band].reshape(-1, 3), arr[:, -band:].reshape(-1, 3)])
    bg = np.median(edge, axis=0).astype(np.int16)
    share = float((np.abs(edge.astype(np.int16) - bg).max(axis=1) <= tol).mean())
    if share < 0.92:
        return None
    return tuple(int(v) for v in bg)


def has_flat_background(path: str, tol: int = 26) -> bool:
    """True when the artwork sits on one plain colour that can be removed
    exactly. A photograph does not, and pretending otherwise would eat half
    the image."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return background_colour(im, tol) is not None
    except Exception:
        return False


# ── removing a flat background ───────────────────────────────────────────────

def _flood_from_border(mask, value: int = 128) -> None:
    """Fill, in place, every 255 pixel of an "L" mask reachable from the
    border. PIL's flood fill is C; seeding it from each border pixel that is
    still 255 handles a mark that touches the edge and splits the
    background into several regions."""
    from PIL import ImageDraw
    w, h = mask.size
    px = mask.load()
    seeds = ([(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
             + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)])
    for x, y in seeds:
        if px[x, y] == 255:
            ImageDraw.floodfill(mask, (x, y), value)


def _label(mask, cap: int):
    """Connected components of a boolean mask.

    Returns (labels, areas): label 0 is "not in the mask", labels 2..k are
    components, and label 1 is everything left unlabelled once `cap`
    components have been found — a halftone or a screenshot's worth of
    fragments, which is not a shape worth reasoning about individually.
    """
    import numpy as np
    from PIL import Image, ImageDraw
    # .copy(): see cutout() — a fromarray() image is read-only and floodfill
    # would silently do nothing.
    lab = Image.fromarray(np.where(mask, 255, 0).astype(np.int32), "I").copy()
    px = lab.load()
    k = 1
    for y, x in np.argwhere(mask):
        if px[int(x), int(y)] == 255:
            k += 1
            ImageDraw.floodfill(lab, (int(x), int(y)), k)
            if k >= cap:
                break
    labels = np.asarray(lab)
    labels = np.where(labels == 255, 1, labels)
    areas = np.bincount(labels.ravel(), minlength=k + 1)
    return labels, areas


def _counters(near, cleared, share: float = 0.05, cap: float = 0.04):
    """Enclosed pockets of card colour that are the card showing THROUGH the
    mark: the hole in an O, the gap in a monogram, the ring's centre. They
    have to show the reel through instead — kept opaque they are cream-
    filled letters on a dark scene.

    Told apart from white lettering on a coloured badge — which is ALSO an
    enclosed pocket of card colour, and which the old rule ("clear it
    wherever it is") wiped out — by size relative to the ink around it. A
    counter is a large share of the letter that encloses it; a letter is a
    tiny share of the badge it sits on. Absolute size cannot separate them:
    a big O's counter and a small badge letter are the same number of
    pixels.
    """
    import numpy as np
    from PIL import Image, ImageFilter
    h, w = near.shape
    punch = np.zeros_like(cleared)
    pockets = near & ~cleared
    if not pockets.any():
        return punch
    plab, _ = _label(pockets, 600)
    ilab, iareas = _label(~near, 2000)

    coords = np.argwhere(plab >= 2)
    if coords.size == 0:
        return punch
    labs = plab[coords[:, 0], coords[:, 1]]
    order = np.argsort(labs, kind="stable")
    labs, coords = labs[order], coords[order]
    uniq, starts = np.unique(labs, return_index=True)
    ends = np.append(starts[1:], len(labs))
    for k, s, e in zip(uniq, starts, ends):
        area = e - s
        if area > cap * w * h:
            continue                      # a field, not a counter
        ys, xs = coords[s:e, 0], coords[s:e, 1]
        y0, y1 = max(0, ys.min() - 1), min(h, ys.max() + 2)
        x0, x1 = max(0, xs.min() - 1), min(w, xs.max() + 2)
        sub = plab[y0:y1, x0:x1] == k
        ring = Image.fromarray((sub * 255).astype(np.uint8), "L") \
            .filter(ImageFilter.MaxFilter(3))
        ring = (np.asarray(ring) > 0) & ~sub
        walls = ilab[y0:y1, x0:x1][ring]
        walls = walls[walls >= 2]         # 1 = unlabelled = something huge
        if walls.size == 0:
            continue
        wall = int(np.bincount(walls).argmax())
        if area >= share * iareas[wall]:
            punch[y0:y1, x0:x1] |= sub
    return punch


def cutout(src: str, out_dir: str | None = None, tol: int = 26) -> str | None:
    """Strip a flat background and trim to the artwork. Returns a PNG path.

    Works from the EDGE inward: only background connected to the border is
    cleared, plus small enclosed pockets of it (counters). The previous
    version cleared every pixel near the background colour wherever it was,
    which is how white lettering on a badge, the white panels of a
    screenshot, and — once flattened to RGB — the black letters of a
    transparent wordmark all disappeared.

    The boundary is soft. A pixel that is a blend of ink and card gets an
    alpha in proportion, and the card colour is taken back out of it
    (un-premultiplied), so the cut-out has the anti-aliased edge the artwork
    had rather than a sawtooth with a halo of the old background.

    Returns None when the background is not flat, or when clearing it would
    change nothing worth the trouble — a photo has no background to remove,
    and saying so is more useful than half-erasing it.
    """
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:
        return None
    try:
        im = Image.open(src).convert("RGB")
    except Exception:
        return None
    bg = background_colour(im, tol)
    if bg is None:
        return None

    arr = np.asarray(im).astype(np.int16)
    h, w = arr.shape[:2]
    dist = np.abs(arr - np.array(bg, dtype=np.int16)).max(axis=2)
    near = dist <= tol

    # .copy() is load-bearing: fromarray() shares the numpy buffer and the
    # image comes back READ-ONLY, and PIL's floodfill catches the ValueError
    # a read-only pixel write raises and returns silently — so nothing was
    # cleared, and a logo on white sailed through as "a photograph".
    mask = Image.fromarray(np.where(near, 255, 0).astype(np.uint8), "L").copy()
    _flood_from_border(mask)
    cleared = np.asarray(mask) == 128
    if cleared.mean() < 0.04:
        return None                       # nothing meaningful was background

    # Counters, only once this is plainly a mark on a card. A photograph with
    # a white sky along its edge clears a strip; its interior is not a card.
    if cleared.mean() >= 0.25:
        cleared |= _counters(near, cleared)

    # The soft edge. A boundary pixel is a blend of ink and card, and its
    # alpha is how much of it is ink: its distance from the card colour over
    # the distance the ink NEXT TO IT has — a 50/50 pixel on a black mark
    # reads 127 against the mark's 255 and gets alpha 0.5. Measured locally
    # (a max-filter of the distance map) rather than against black, so a
    # light-grey shape gets a fair edge too. Then the card is taken back out
    # of the colour — un-premultiplied — so a pale edge pixel becomes an ink
    # pixel at low alpha, not a halo of the old background at full alpha.
    alpha = np.where(cleared, 0.0, 255.0).astype(np.float32)
    dilated = Image.fromarray((cleared * 255).astype(np.uint8), "L") \
        .filter(ImageFilter.MaxFilter(5))
    band = (np.asarray(dilated) > 0) & ~cleared
    ref = np.asarray(
        Image.fromarray(np.clip(dist, 0, 255).astype(np.uint8), "L")
        .filter(ImageFilter.MaxFilter(7))).astype(np.float32)
    soft = np.clip(dist.astype(np.float32) / np.maximum(ref, 1.0), 0.0, 1.0)
    alpha[band] = soft[band] * 255.0

    rgb = arr.astype(np.float32)
    mix = band & (soft > 0.02) & (soft < 0.98)
    if mix.any():
        aa = soft[..., None]
        card = np.array(bg, dtype=np.float32)
        rgb = np.where(mix[..., None],
                       np.clip((rgb - card * (1.0 - aa)) / np.maximum(aa, 1e-3),
                               0, 255), rgb)

    if (alpha > 0).mean() < 0.004:
        return None                       # nothing survived: not a cut-out

    out = Image.fromarray(
        np.dstack([rgb, alpha]).round().astype(np.uint8), "RGBA")
    out = _trim(out)
    out_dir = out_dir or tempfile.mkdtemp(prefix="prism_assets_")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(
        out_dir, os.path.splitext(os.path.basename(src))[0] + "_cut.png")
    out.save(dest, "PNG")
    return dest


def _trim(im, margin: int = 2):
    """Crop to the ink, with a little air so a soft edge is never clipped."""
    box = im.getbbox()
    if not box:
        return im
    w, h = im.size
    return im.crop((max(0, box[0] - margin), max(0, box[1] - margin),
                    min(w, box[2] + margin), min(h, box[3] + margin)))


def prepare(src: str, out_dir: str | None = None, tol: int = 26) -> dict:
    """One file → the version a reel can use, and the truth about it.

    Returns {"path", "alpha", "how"}. `alpha` is what the art director is
    told, so it is measured from the result rather than inferred from which
    branch ran — the old code set it from "did the cut-out run", and reported
    a client's real transparent logo as "opaque, has its own background".
    """
    try:
        from PIL import Image
    except Exception:
        return {"path": src, "alpha": False, "how": "as supplied"}

    if has_real_alpha(src):
        try:
            with Image.open(src) as im:
                out = _trim(im.convert("RGBA"))
            out_dir = out_dir or tempfile.mkdtemp(prefix="prism_assets_")
            os.makedirs(out_dir, exist_ok=True)
            dest = os.path.join(
                out_dir, os.path.splitext(os.path.basename(src))[0] + "_trim.png")
            out.save(dest, "PNG")
            return {"path": dest, "alpha": True, "how": "its own transparency"}
        except Exception:
            return {"path": src, "alpha": True, "how": "its own transparency"}

    cut = cutout(src, out_dir, tol)
    if cut:
        return {"path": cut, "alpha": True, "how": "cut from a flat background"}
    return {"path": src, "alpha": False, "how": "photograph"}


def _ink_ratio(path: str) -> float:
    """Share of the image that is actually drawn on. A logo is mostly empty
    once its background is gone; a photo or a full card is not — which is how
    the logo is picked out of a pile of attachments without asking anyone."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGBA")
            im.thumbnail((160, 160))
            a = np.asarray(im.getchannel("A"))
    except Exception:
        return 1.0
    if a.size == 0:
        return 1.0
    return float((a > 24).mean())


def looks_like_contact_sheet(path: str) -> bool:
    """Detect a generated multi-panel board masquerading as one asset.

    Image models sometimes return a 2xN storyboard/contact sheet when asked
    for separate artwork. Long, high-contrast separator rules are a reliable
    signal and are intentionally conservative: a single decorative rule is
    not enough to reject a photograph.
    """
    try:
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((320, 320))
            arr = np.asarray(im).astype("int16")
        gray = arr.mean(axis=2)
        def separators(axis: int) -> int:
            # Contrast on both sides of a column/row is what a panel rule has;
            # texture alone rarely spans 70% of the perpendicular dimension.
            d = np.abs(np.diff(gray, axis=axis))
            score = (d > 85).mean(axis=1-axis)
            runs = 0; in_run = False
            for value in score > 0.70:
                if value and not in_run: runs += 1
                in_run = bool(value)
            return runs
        return separators(0) >= 2 or separators(1) >= 2
    except Exception:
        return False


def split_contact_sheet(path: str, out_dir: str | None = None) -> list[str]:
    """Turn the common generated 4+3 storyboard board into usable panels.

    This is deliberately conservative and only runs after
    :func:`looks_like_contact_sheet` has identified a board.  The image
    generator's portrait boards use four tiles on the upper row and three on
    the lower row; trimming a small gutter keeps separator rules out of the
    actual scene art.  Returning an empty list leaves unusual composites
    rejected instead of guessing.
    """
    if not looks_like_contact_sheet(path):
        return []
    try:
        from PIL import Image
        out_dir = out_dir or os.path.dirname(path)
        os.makedirs(out_dir, exist_ok=True)
        with Image.open(path) as src:
            im = src.convert("RGBA")
            w, h = im.size
            # The 4+3 layout is recognisable by its near-square board shape.
            if not (0.72 <= w / max(h, 1) <= 1.25):
                return []
            split = h // 2
            panels = []
            for row, (y0, y1, cols) in enumerate(((0, split, 4), (split, h, 3))):
                row_h = y1 - y0
                for col in range(cols):
                    x0, x1 = round(col * w / cols), round((col + 1) * w / cols)
                    gx, gy = max(2, round((x1 - x0) * .012)), max(2, round(row_h * .012))
                    box = (x0 + gx, y0 + gy, x1 - gx, y1 - gy)
                    tile = im.crop(box)
                    target = os.path.join(out_dir, f"{os.path.basename(path)}.panel{len(panels)+1}.png")
                    tile.save(target, "PNG", optimize=True)
                    panels.append(target)
            return panels
    except Exception:
        return []


def collect(images: list, out_dir: str | None = None,
            generated: set | None = None) -> dict:
    """Build the asset table the design stage is allowed to reference.

    Names are derived from the files in a fixed order, so the same inputs
    always produce the same names — the design stage is told what exists
    before the render, and the renderer resolves the same names afterwards
    without either of them passing paths around.

    Returns {name: {"path", "w", "h", "alpha", "how", "kind"}}.
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
        if not p:
            continue
        p_low = str(p).lower()
        if p_low.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            paths.append(p)
        elif p_low.endswith((".pptx", ".docx")):
            try:
                import zipfile
                with zipfile.ZipFile(p, "r") as z:
                    for name in z.namelist():
                        if name.startswith(("ppt/media/", "word/media/")) and name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
                            extracted = os.path.join(out_dir, f"doc_{os.path.basename(name)}")
                            with open(extracted, "wb") as ef:
                                ef.write(z.read(name))
                            paths.append(extracted)
            except Exception:
                pass
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
        prep = prepare(p, out_dir)
        use = prep["path"]
        try:
            with Image.open(use) as im:
                w, h = im.size
        except Exception:
            continue
        composite = looks_like_contact_sheet(use)
        panels = split_contact_sheet(use, out_dir) if composite else []
        prepared.append({"path": use, "w": w, "h": h, "alpha": prep["alpha"],
                         "how": prep["how"],
                         "ink": _ink_ratio(use) if prep["alpha"] else 1.0,
                         "composite": composite, "panels": panels,
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
    #
    # BUT only when the client attached nothing. That is the exact condition
    # under which the imagery stage was asked for a mark at all — handed the
    # client's own artwork it is told "NO logo, make three SUBJECT images
    # instead", and then its first image is a photograph of the trade.
    # Claiming the logo slot for it put a product shot on the endcard where
    # the company's name belongs, because the design stage is told to put the
    # logo somewhere a logo goes. Screenshots of the customer's own software
    # are the case that exposed it: attached artwork, none of it a mark.
    if logo is None and not any(not a["made"] for a in rest):
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
    lines, wide, opaque, clear = [], [], [], []
    for name, a in table.items():
        if a.get("made"):
            what = ("a mark generated for this reel" if a["kind"] == "logo"
                    else "imagery generated for this reel")
        else:
            what = ("the client's own mark, taken from their artwork"
                    if a["kind"] == "logo" else "artwork the client supplied")
        if a["alpha"]:
            cut = "transparent PNG, soft edges"
            clear.append(f"asset:{name}")
        else:
            cut = "OPAQUE — a photograph with its own background"
            opaque.append(f"asset:{name}")
        if a.get("composite"):
            cut += "; REJECTED CONTACT SHEET — not a single usable image"
        lines.append(f'  asset:{name} — {a["w"]}x{a["h"]}, {cut} — {what}')
        # A LANDSCAPE picture in a PORTRAIT frame. Made to be flagged because
        # the customer types "make a reel, here are two screenshots" and
        # nothing else — so the advice that would otherwise have to be in
        # their prompt has to come from here instead.
        if a["kind"] != "logo" and a["w"] > a["h"] * 1.2:
            wide.append(f"asset:{name}")
    if clear:
        lines.append(
            "\nTHE TRANSPARENT ONES — " + ", ".join(clear) + " — need no "
            "backing. Place them straight on the background, on a colour "
            "field, or beside type, and let their edges breathe. Do not draw "
            "a card, box or panel behind one to 'contain' it: the cut-out IS "
            "the shape, and a box around it reads as a placeholder.")
    if opaque:
        # The 2026-09-07 reel: told only that a picture was "opaque", the
        # art director set it down as a rectangle in the middle of the paper
        # with a grey plate behind it. An opaque picture has exactly three
        # good uses, and they are named rather than left to be guessed.
        lines.append(
            "\nTHE OPAQUE ONES — " + ", ".join(opaque) + " — carry their own "
            "background, and a rectangle of somebody else's background sitting "
            "in the middle of the frame is the commonest way a picture ruins a "
            "reel. Treat each as a PHOTOGRAPH, one of three ways: (1) "
            "FULL-BLEED — the picture fills the frame or a whole band of it, "
            "`object-fit: cover` with `object-position` on the part that "
            "matters, and the copy sits on a solid or translucent panel over "
            "it; (2) INSIDE A SHAPE — a `clip-path`, a circle, a tall rounded "
            "frame — so the crop is obviously deliberate; (3) AS A PLATE — "
            "edge-to-edge across the frame's width with a rule above and "
            "below, part of the layout's grid. Never a bare <img> at its own "
            "size floating on empty paper, and never a drop-in with a soft "
            "grey box behind it.")
    if wide:
        lines.append(
            "\nTHESE ARE WIDER THAN THEY ARE TALL — " + ", ".join(wide) +
            " — and the frame is 1080 wide by 1920 tall. Fitted whole into it "
            "they end up a band across the middle a few hundred pixels high, "
            "and any writing inside them becomes unreadable. That is the "
            "commonest way an attached picture ruins a reel.\n"
            "CROP INSTEAD OF SHRINKING. Pick the part of the picture that "
            "carries the point — the one panel, the one row, the one control "
            "— and show that part large enough to read, letting the rest fall "
            "outside the frame. `object-fit: cover` with `object-position`, a "
            "`clip-path`, or a wrapper with `overflow: hidden` and the image "
            "scaled up inside it all do this. Filling the frame edge to edge "
            "with a detail is a composition; a small rectangle floating in the "
            "middle is not.\n"
            "A picture may also be MOVED across the cut — held still and then "
            "drifted, or a crop that opens outward — which is worth more than "
            "showing all of it at once.")
    return "\n".join(lines)
