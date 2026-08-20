"""
Prism — PCB fabrication data from Gerber + drill files (/gerber)
────────────────────────────────────────────────────────────────
No AI ever measures the board. Same principle as core/boq.py, and for the
same reason: a Gerber file is exact vector geometry — apertures, draws,
flashes, filled regions — and an LLM asked to eyeball a picture of it can
only produce plausible-looking numbers. A wrong minimum track width is a
scrapped panel or a mis-quoted job, so this module measures the geometry
itself, deterministically, and an AI stage only ever sees the numbers
afterward (to write the quote or the reply, never to produce the figures).

It answers the five questions a fab actually asks of an incoming job:

    1. PCB size            — from the board OUTLINE, not the ink bounding box
    2. minimum track width — smallest aperture actually used to DRAW copper
    3. minimum track spacing — real copper-to-copper clearance, by geometry
    4. minimum drill size  — smallest tool with at least one hit
    5. number of drills    — total hits, and the per-tool breakdown

WHY THE OBVIOUS IMPLEMENTATION IS WRONG
───────────────────────────────────────
Every one of these has a naive answer that looks right and isn't. The three
that bite, all three observed on real customer jobs:

  • SIZE IS NOT THE BOUNDING BOX. Fiducials, tooling holes, a legend block
    and stray title text sit outside the board edge and inflate the extents.
    One real job measured 3.600 x 3.750 in by bounding box and 3.550 x 3.550
    in by its actual outline — a 10% error in area, straight into the price.
    So the outline layer is polygonised and the largest closed face wins;
    the bounding box is a last resort and says so loudly.

  • THE SMALLEST GAP IS USUALLY NOISE. Two shapes meant to touch, rounded
    apart by the file's own coordinate resolution, leave a hairline gap of a
    few hundredths of a mil. Reported raw, that becomes "minimum spacing:
    0.31 mil" and the job looks unbuildable. Gaps below a snap tolerance are
    therefore treated as touching (and counted separately, never hidden),
    and the full gap distribution is reported alongside the minimum so a
    single outlier at one connector is visibly different from the design
    rule the whole board was routed to.

  • THE SMALLEST APERTURE MAY BARELY EXIST. One job's minimum track was 8
    mil — across 2.3 inches of trace, against 304 inches at 50 mil. That is
    a true minimum and a misleading headline, so the width table carries
    segment count and total length per width. The fab decides.

WHAT IS SUPPORTED
─────────────────
RS-274X: FS format spec (leading/trailing zero suppression, absolute),
MO units, AD aperture definitions (C circle, R rectangle, O obround,
P regular polygon), D01/D02/D03, G01 linear, G02/G03 circular in both
G74 single-quadrant and G75 multi-quadrant, G36/G37 filled regions, and
LPD/LPC polarity (clear polarity SUBTRACTS — a plane's clearances are
drawn that way, and ignoring it merges every net on the board into one).

Excellon drill: M48 header, INCH/METRIC and M72/M71, tool table with
diameters, leading/trailing zero suppression and decimal coordinates,
and repeat counts.

DISCLOSED LIMITATIONS (not hidden — a wrong number is worse than a gap)
  • Aperture macros (%AM) define arbitrary custom shapes. Flashes using one
    are counted but not given geometry, so they take no part in the spacing
    measurement. Every affected layer is named in the warnings.
  • Step-and-repeat (%SR) panelisation is detected and reported, but the
    repeats are not expanded; the measurement describes one image.
  • Spacing is measured WITHIN each copper layer. Layer-to-layer spacing is
    a stackup question, not a Gerber one.
  • Arcs are flattened to chords at ~0.005 mm sagitta. That is finer than
    any fabrication tolerance, but it is an approximation.
  • Nets are inferred from touching copper on one layer. Two shapes on the
    same net that only join through a via on another layer read as two
    islands, so the gap between them is reported as clearance. It is real
    copper-to-copper distance either way; it just may not be a violation.
"""
from __future__ import annotations

import math
import os
import re

# Shapely does the geometry for spacing and for the outline. Everything
# else — size from a rectangular outline, track widths, drills, counts —
# works without it, so a missing install degrades rather than dies.
try:
    from shapely.geometry import LineString, Point, Polygon, box
    from shapely.ops import polygonize, unary_union
    from shapely.strtree import STRtree
    HAVE_SHAPELY = True
except Exception:                                       # pragma: no cover
    HAVE_SHAPELY = False


class GerberError(Exception):
    """Raised when there is nothing measurable to work with."""


MM_PER_INCH = 25.4

# Two shapes closer than this are taken to be touching, not separated. It is
# ~0.4 mil — an order below any real design rule and an order above the
# rounding noise of a 4-decimal-place coordinate.
SNAP_MM = 0.01

# Chord tolerance when flattening an arc.
ARC_SAGITTA_MM = 0.005


# ── file identification ───────────────────────────────────────────────────────
#
# Extension first, because CAM tools are consistent about them, then CONTENT,
# because they are not consistent enough. A `.txt` in a job folder has been
# seen as all three of: an Excellon drill file, a plain-English drill report,
# and (on a 2013 job) a Gerber file holding the drill as flashed pads. Only
# looking inside tells them apart.

_EXT_ROLE = {
    ".gtl": "copper_top", ".cmp": "copper_top", ".top": "copper_top",
    ".gbl": "copper_bottom", ".sol": "copper_bottom", ".bot": "copper_bottom",
    ".gp1": "plane", ".gp2": "plane", ".gp3": "plane", ".gp4": "plane",
    ".g1": "copper_inner", ".g2": "copper_inner", ".g3": "copper_inner",
    ".g4": "copper_inner", ".g5": "copper_inner", ".g6": "copper_inner",
    ".gko": "outline", ".gm1": "outline", ".gml": "outline", ".oln": "outline",
    ".dim": "outline", ".gbr": "unknown",
    ".gts": "mask_top", ".gbs": "mask_bottom",
    ".gto": "silk_top", ".gbo": "silk_bottom",
    ".gtp": "paste_top", ".gbp": "paste_bottom",
    ".gpt": "pad_master", ".gpb": "pad_master",
    ".gd1": "drill_drawing", ".gg1": "drill_guide",
    ".drl": "drill", ".xln": "drill", ".nc": "drill", ".tap": "drill",
    ".txt": "drill",   # overloaded — the content sniff decides (see classify)
    ".exc": "drill", ".drd": "drill",
    ".drr": "report", ".rep": "report", ".rpt": "report",
    ".apr": "aperture_list",
}

_ROLE_LABEL = {
    "copper_top": "top copper (the tracks and pads on the component side)",
    "copper_bottom": "bottom copper (tracks and pads on the solder side)",
    "copper_inner": "inner copper layer (routed, like top and bottom)",
    "plane": "internal ground or power plane — a solid copper sheet, no tracks",
    "outline": "board outline — the shape the board is cut to",
    "mask_top": "top solder mask (the green coating; holes where solder goes)",
    "mask_bottom": "bottom solder mask",
    "silk_top": "top silkscreen (the white printed component labels)",
    "silk_bottom": "bottom silkscreen",
    "paste_top": "top solder paste stencil",
    "paste_bottom": "bottom solder paste stencil",
    "pad_master": "pad master (every pad, no tracks — a reference layer)",
    "drill": "DRILL FILE — every hole, its size and position",
    "drill_gerber": "drill supplied as a Gerber (holes drawn as flashed pads)",
    "drill_drawing": "drill drawing (a human-readable picture of the holes)",
    "drill_guide": "drill guide (hole positions, often with the board outline)",
    "report": "text report written by the CAM tool (human-readable summary)",
    "unknown": "unrecognised — content was checked, see notes",
    "aperture_list": "aperture list (the CAM tool's own D-code table)",
    "drill_binary": "drill file in binary EIA form — the ASCII copy is read instead",
    "other": "not a fabrication file",
}

# Layers that carry ROUTED copper, and therefore have a track width and a
# track spacing at all. An internal plane is a solid sheet: the only thing in
# its Gerber is the clearance punched around each hole, so "minimum track
# width" on one is a category error, not a small number. On a real 2018 job
# the two plane layers reported 3 mil on a board routed to 10, and dragged
# the whole job's answer down with them.
_COPPER_ROLES = ("copper_top", "copper_bottom", "copper_inner")
_PLANE_ROLES = ("plane",)
_ALL_COPPER = _COPPER_ROLES + _PLANE_ROLES


def _sniff(path: str) -> str:
    """What the file actually IS, read from its first few hundred bytes."""
    try:
        with open(path, "r", errors="replace") as fh:
            head = fh.read(4000)
    except Exception:
        return "other"
    if "%FS" in head and "%MO" in head:
        return "gerber"
    if re.search(r"^M48\b", head, re.M) or re.search(r"^M7[12]\b", head, re.M):
        return "excellon"
    if re.search(r"^T\d+\b.*?[CF]\d", head, re.M) and re.search(r"^X[\d.+-]", head, re.M):
        return "excellon"
    if re.search(r"Tool\s+Hole Size|NCDrill File Report|Aperture|Report For:"
                 r"|Generation Report|Layer Extension", head, re.I):
        return "report"
    return "other"


def classify(paths: list[str]) -> list[dict]:
    """Name every file in the job: what it is, and what it is for.

    The `role` drives the measurement; the `label` is what a person who has
    never opened a Gerber gets to read.
    """
    out = []
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        role = _EXT_ROLE.get(ext, "unknown")
        kind = _sniff(p)

        # Content overrules the extension wherever they disagree.
        if kind == "excellon":
            role = "drill"
        elif kind == "gerber":
            if role in ("drill", "unknown", "report"):
                # A Gerber sitting where a drill file belongs IS the drill,
                # expressed as flashed pads. Common on older Indian jobs.
                role = "drill_gerber" if role == "drill" else role
            if role == "unknown":
                role = _role_from_gerber_hint(p)
        elif kind == "report":
            role = "report"
        elif kind == "other" and role not in ("report", "aperture_list"):
            role = "drill_binary" if role == "drill" else "other"

        out.append({
            "path": p,
            "name": os.path.basename(p),
            "ext": ext,
            "kind": kind,
            "role": role,
            "label": _ROLE_LABEL.get(role, role),
            "size": os.path.getsize(p) if os.path.exists(p) else 0,
        })
    return out


def _role_from_gerber_hint(path: str) -> str:
    """Last resort for a `.gbr`-style name: read the layer-name extension."""
    try:
        head = open(path, "r", errors="replace").read(4000)
    except Exception:
        return "unknown"
    m = re.search(r"%LN([^*]*)\*%", head) or re.search(r"%TF\.FileFunction,([^*]*)\*%", head)
    name = (m.group(1) if m else "").lower()
    for key, role in (("outline", "outline"), ("profile", "outline"),
                      ("keepout", "outline"), ("top", "copper_top"),
                      ("bottom", "copper_bottom"), ("silk", "silk_top"),
                      ("mask", "mask_top"), ("drill", "drill_gerber")):
        if key in name:
            return role
    return "unknown"


# ── Gerber (RS-274X) ──────────────────────────────────────────────────────────

_APERTURE = re.compile(r"%ADD(\d+)([A-Za-z_$][\w.$-]*),?([^*]*)\*%")
_FS = re.compile(r"%FS([LTD]?)([AI])X(\d)(\d)Y(\d)(\d)\*%")
_OP = re.compile(
    r"^(?:G(\d{1,2}))?"
    r"(?:X([+-]?\d+))?(?:Y([+-]?\d+))?"
    r"(?:I([+-]?\d+))?(?:J([+-]?\d+))?"
    r"(?:D(\d{1,3}))?\*$"
)


class GerberLayer:
    """One parsed Gerber file, in millimetres, whatever it was written in."""

    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self.unit = "mm"
        self.source_unit = "mm"
        self.apertures: dict[int, tuple[str, list[float]]] = {}
        self.macros: set[str] = set()
        self.draws: list[tuple[int, tuple, tuple]] = []      # (dcode, a, b)
        self.arcs: list[tuple[int, tuple, tuple, tuple, int]] = []
        self.flashes: list[tuple[int, tuple]] = []
        self.regions: list[tuple[list[tuple], bool]] = []    # (points, dark)
        self.dark_flags: list[bool] = []
        # Gerber is a sequence of paint operations, not two sets to be
        # unioned and subtracted. Order is kept here because a file that
        # goes dark → clear → dark (and real ones do) means the last dark
        # object PUTS BACK copper the clear one removed.
        self.ops: list[tuple[str, object, bool]] = []
        self.has_step_repeat = False
        self.macro_flashes = 0
        self.warnings: list[str] = []


def parse_gerber(path: str) -> GerberLayer:
    """Parse one RS-274X file into millimetre geometry."""
    text = open(path, "r", errors="replace").read()
    layer = GerberLayer(path)

    m = _FS.search(text)
    if not m:
        raise GerberError(f"{layer.name}: no %FS format spec — not a Gerber file.")
    zero_mode, coord_mode, int_digits, dec_digits = (
        m.group(1) or "L", m.group(2), int(m.group(3)), int(m.group(4)))
    if coord_mode == "I":
        layer.warnings.append(
            f"{layer.name}: incremental coordinates (%FS…I…) — rare and "
            "deprecated. Measurements from this layer are not trustworthy.")

    layer.source_unit = "mm" if "%MOMM*%" in text else "in"
    to_mm = 1.0 if layer.source_unit == "mm" else MM_PER_INCH
    scale = (10.0 ** -dec_digits) * to_mm
    width = int_digits + dec_digits

    def num(raw: str) -> float:
        neg = raw.startswith("-")
        digits = raw.lstrip("+-")
        if "." in digits:                       # some tools write it plainly
            return (-1 if neg else 1) * float(digits) * to_mm
        # Zero suppression decides which end to pad. Getting this backwards
        # scales the whole board by a power of ten, so it is worth the care.
        digits = digits.rjust(width, "0") if zero_mode in ("L", "D") \
            else digits.ljust(width, "0")
        value = int(digits) * scale
        return -value if neg else value

    for am in re.finditer(r"%AM([^*]+)\*", text):
        layer.macros.add(am.group(1).strip())
    for ad in _APERTURE.finditer(text):
        dcode, shape, params = int(ad.group(1)), ad.group(2), ad.group(3)
        nums: list[float] = []
        for piece in params.split("X"):
            piece = piece.strip()
            if not piece:
                continue
            try:
                nums.append(float(piece) * (to_mm if shape in "CROP" else 1.0))
            except ValueError:
                break
        layer.apertures[dcode] = (shape, nums)
    if "%SR" in text and not re.search(r"%SRX1Y1I0J0\*%", text):
        layer.has_step_repeat = True

    x = y = 0.0
    dcode: int | None = None
    # D01/D02/D03 are MODAL: a coordinate line carrying no D-code repeats
    # the last operation. Older CAM tools lean on this heavily — one real
    # 2013 job writes 4332 of its 4336 region points that way, so a parser
    # that only acts on an explicit D01 sees 158 objects out of 470 and
    # measures the clearance of a board it has mostly not read.
    dop: str | None = None
    interp = 1                  # 1 linear, 2 CW arc, 3 CCW arc
    quadrant = 75               # G74 single / G75 multi
    dark = True                 # LPD; LPC subtracts
    in_region = False
    region_pts: list[tuple] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("%"):
            if "%LPC" in line:
                dark = False
            elif "%LPD" in line:
                dark = True
            continue
        if line == "G36*":
            in_region, region_pts = True, []
            continue
        if line == "G37*":
            if len(region_pts) >= 3:
                layer.regions.append((region_pts, dark))
                layer.ops.append(("region", region_pts, dark))
            in_region, region_pts = False, []
            continue
        if line == "G74*":
            quadrant = 74
            continue
        if line == "G75*":
            quadrant = 75
            continue

        # A bare aperture select, with or without the deprecated G54 in front.
        sel = re.fullmatch(r"(?:G54)?D(\d{2,4})\*", line)
        if sel and int(sel.group(1)) >= 10:
            dcode = int(sel.group(1))
            continue

        op = _OP.match(line)
        if not op:
            continue
        g, xs, ys, is_, js, d = op.groups()
        if d is None and (xs or ys):
            d = dop                      # modal: repeat the last operation
        elif d in ("01", "02", "03"):
            dop = d
        if g is not None:
            gi = int(g)
            if gi in (1,):
                interp = 1
            elif gi in (2, 3):
                interp = gi
            elif gi == 54 and d and int(d) >= 10:
                dcode = int(d)
                continue
        nx = num(xs) if xs else x
        ny = num(ys) if ys else y

        if d == "01":
            if interp == 1:
                pts = [(nx, ny)]
            else:
                pts = _arc_points((x, y), (nx, ny),
                                  num(is_) if is_ else 0.0,
                                  num(js) if js else 0.0,
                                  clockwise=(interp == 2),
                                  multiquadrant=(quadrant == 75))
            if in_region:
                if not region_pts:
                    region_pts = [(x, y)]
                region_pts.extend(pts)
            else:
                prev = (x, y)
                for pt in pts:
                    if dcode is not None:
                        layer.draws.append((dcode, prev, pt))
                        layer.dark_flags.append(dark)
                        layer.ops.append(("draw", (dcode, prev, pt), dark))
                    prev = pt
        elif d == "02":
            if in_region:
                if len(region_pts) >= 3:
                    layer.regions.append((region_pts, dark))
                    layer.ops.append(("region", region_pts, dark))
                region_pts = [(nx, ny)]
        elif d == "03":
            if dcode is not None:
                shape = layer.apertures.get(dcode, ("", []))[0]
                if shape not in ("C", "R", "O", "P"):
                    layer.macro_flashes += 1
                layer.flashes.append((dcode, (nx, ny)))
                layer.ops.append(("flash", (dcode, (nx, ny)), dark))
        x, y = nx, ny

    if in_region and len(region_pts) >= 3:
        layer.regions.append((region_pts, dark))
        layer.ops.append(("region", region_pts, dark))
    if layer.macro_flashes:
        layer.warnings.append(
            f"{layer.name}: {layer.macro_flashes} flash(es) use an aperture "
            "macro (%AM). Counted, but given no geometry — they take no part "
            "in the spacing measurement.")
    if layer.has_step_repeat:
        layer.warnings.append(
            f"{layer.name}: step-and-repeat (%SR) — this file is a panel. "
            "Measured as ONE image; the repeats are not expanded.")
    return layer


def _arc_points(start, end, i, j, clockwise: bool, multiquadrant: bool):
    """Flatten one circular interpolation to chords."""
    sx, sy = start
    ex, ey = end
    centres = [(sx + i, sy + j)]
    if not multiquadrant:
        # G74: I/J are unsigned magnitudes; the right centre is whichever
        # of the four is equidistant from both endpoints.
        centres = [(sx + si * abs(i), sy + sj * abs(j))
                   for si in (1, -1) for sj in (1, -1)]
    best, best_err = centres[0], float("inf")
    for c in centres:
        r1 = math.hypot(sx - c[0], sy - c[1])
        r2 = math.hypot(ex - c[0], ey - c[1])
        err = abs(r1 - r2)
        if err < best_err:
            best, best_err = c, err
    cx, cy = best
    r = math.hypot(sx - cx, sy - cy)
    if r <= 0:
        return [end]
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    sweep = a1 - a0
    if clockwise:
        while sweep > 0:
            sweep -= 2 * math.pi
        if abs(sweep) < 1e-12:
            sweep = -2 * math.pi if multiquadrant else 0.0
    else:
        while sweep < 0:
            sweep += 2 * math.pi
        if abs(sweep) < 1e-12:
            sweep = 2 * math.pi if multiquadrant else 0.0
    # Chord count from the sagitta tolerance, clamped so a huge arc cannot
    # generate a pathological number of points.
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - ARC_SAGITTA_MM / r))) if r > ARC_SAGITTA_MM else math.pi / 8
    n = max(2, min(720, int(abs(sweep) / max(step, 1e-6)) + 1))
    return [(cx + r * math.cos(a0 + sweep * k / n),
             cy + r * math.sin(a0 + sweep * k / n)) for k in range(1, n + 1)]


# ── geometry from a parsed layer ──────────────────────────────────────────────

def aperture_shape(shape: str, params: list[float], at=(0.0, 0.0)):
    """One flash as a polygon. None for anything macro-defined."""
    if not HAVE_SHAPELY or not params:
        return None
    x, y = at
    if shape == "C":
        return Point(x, y).buffer(params[0] / 2, 32)
    if shape == "R":
        w = params[0]
        h = params[1] if len(params) > 1 else w
        return box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    if shape == "O":
        w = params[0]
        h = params[1] if len(params) > 1 else w
        r = min(w, h) / 2
        if w >= h:
            return LineString([(x - (w / 2 - r), y), (x + (w / 2 - r), y)]).buffer(r, 16)
        return LineString([(x, y - (h / 2 - r)), (x, y + (h / 2 - r))]).buffer(r, 16)
    if shape == "P":
        d = params[0]
        sides = int(params[1]) if len(params) > 1 else 6
        rot = math.radians(params[2]) if len(params) > 2 else 0.0
        r = d / 2
        return Polygon([(x + r * math.cos(rot + 2 * math.pi * k / sides),
                         y + r * math.sin(rot + 2 * math.pi * k / sides))
                        for k in range(max(3, sides))])
    return None


def layer_copper(layer: GerberLayer):
    """The layer's real copper, as one shapely geometry.

    Replayed as a SEQUENCE, in the order the file paints it. Clear-polarity
    (%LPC) objects subtract; dark ones add. That order is not a refinement —
    a real 2013 job goes dark → clear → dark, so unioning every dark object
    and then subtracting every clear one erases copper the file explicitly
    puts back. It showed up as an independent raster measurement finding
    gaps on that board that the polygon measurement could not see.

    A plane is one dark region with its clearances knocked out in clear
    polarity, so ignoring polarity altogether merges every net on the board
    into a single island and spacing comes back as "nothing to measure".
    """
    if not HAVE_SHAPELY:
        raise GerberError(
            "Measuring track spacing needs shapely — `pip install shapely`. "
            "Size, track width and drill counts work without it.")

    def geom_for(kind, payload):
        if kind == "draw":
            dcode, a, b = payload
            shape, params = layer.apertures.get(dcode, (None, []))
            if not params:
                return None
            if shape == "C":
                return LineString([a, b]).buffer(params[0] / 2, 8)
            if shape in ("R", "O"):
                return LineString([a, b]).buffer(min(params) / 2, 8, cap_style=3)
            return None
        if kind == "flash":
            dcode, at = payload
            shape, params = layer.apertures.get(dcode, (None, []))
            return aperture_shape(shape, params, at) if params else None
        try:
            poly = Polygon(payload)
            if not poly.is_valid:
                poly = poly.buffer(0)
            return None if poly.is_empty else poly
        except Exception:
            return None

    copper = None
    run: list = []
    run_dark = True
    seen_any = False

    def flush(target):
        if not run:
            return target
        merged = unary_union(run)
        if target is None:
            return merged if run_dark else None
        return target.union(merged) if run_dark else target.difference(merged)

    for kind, payload, is_dark in layer.ops:
        g = geom_for(kind, payload)
        if g is None:
            continue
        seen_any = seen_any or is_dark
        if is_dark != run_dark and run:
            copper = flush(copper)
            run = []
        run_dark = is_dark
        run.append(g)
    copper = flush(copper)

    if copper is None or copper.is_empty or not seen_any:
        raise GerberError(f"{layer.name}: no drawable copper found.")
    return copper


def islands(copper) -> list:
    if copper.is_empty:
        return []
    return list(copper.geoms) if copper.geom_type == "MultiPolygon" else [copper]


# ── the five measurements ─────────────────────────────────────────────────────

def classify_copper(layer: GerberLayer, copper=None):
    """Split a layer's copper into CONDUCTORS and MARKINGS.

    Boards carry text: part numbers, revision marks, a company logo, etched
    into the copper alongside the tracks. It is copper, and it is not a
    conductor, and telling them apart is the difference between a number a
    fabricator recognises and one they do not.

    On a real 2013 board, 94 of the layer's 114 copper islands are lettering
    drawn with a thin 10-mil pen, while every actual conductor is a 3 mm
    strip. Measuring all of it reported a minimum track width of 10 mil for
    a board whose narrowest conductor is 118 mil — and a clearance of 5 mil
    for a board whose tightest real gap is 81. The customer's own check
    sheet said 118 and 81.9. A 2018 four-layer board had the same fault in
    miniature: 184 tiny segments of vertical text at the board edge,
    reported as an 8-mil minimum on a board routed to 10.

    THE TEST: a conductor connects to something. Lettering connects to
    nothing. So an island counts as a conductor when it carries at least one
    flashed pad. Pads are where holes and components land, and they are on
    the same layer in the same coordinates, so no cross-file assumption is
    needed.

    Returns (conductors, markings). If the layer has no flashes at all there
    is nothing to test against, so everything is treated as conductor and
    the caller is told why.
    """
    if copper is None:
        copper = layer_copper(layer)
    isl = islands(copper)
    if not layer.flashes or len(isl) < 2:
        return isl, []
    pads = [Point(pt) for _, pt in layer.flashes]
    tree = STRtree(pads)
    conductors, markings = [], []
    for g in isl:
        hit = [int(i) for i in tree.query(g)]
        if any(g.intersects(pads[i]) for i in hit):
            conductors.append(g)
        else:
            markings.append(g)
    # A layer that is ALL markings by this test is a layer the test does not
    # understand — a plane with no flashed pads, say. Better to measure
    # everything and say so than to report nothing.
    return (conductors, markings) if conductors else (isl, [])


def track_widths(layer: GerberLayer, conductors=None) -> list[dict]:
    """Every width copper was DRAWN with, and how much of it there is.

    Flashes are excluded — a 3 mm pad is not a 3 mm track — and so is
    anything non-circular, because a rectangle dragged along a path is a pad
    shape, not a routed trace.

    With `conductors` given, a segment only counts if its midpoint lands on
    one. That is what keeps the silkscreen-in-copper out of the answer; see
    classify_copper().
    """
    stats: dict[int, dict] = {}
    tree = STRtree(conductors) if (conductors and HAVE_SHAPELY) else None
    for (dcode, a, b), is_dark in zip(layer.draws, layer.dark_flags):
        if not is_dark:
            continue
        shape, params = layer.apertures.get(dcode, (None, []))
        if shape != "C" or not params:
            continue
        if tree is not None:
            mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if not any(conductors[int(i)].intersects(mid) for i in tree.query(mid)):
                continue
        row = stats.setdefault(dcode, {"width_mm": params[0], "dcode": dcode,
                                       "segments": 0, "length_mm": 0.0})
        row["segments"] += 1
        row["length_mm"] += math.hypot(b[0] - a[0], b[1] - a[1])
    return sorted(stats.values(), key=lambda r: r["width_mm"])


def _nearest_pairs(isl: list, snap_mm: float) -> dict:
    """Every neighbouring pair and the gap between them."""
    tree = STRtree(isl)
    span = 1.0
    for g in isl:
        x0, y0, x1, y1 = g.bounds
        span = max(span, x1 - x0, y1 - y0)
    pairs: dict[tuple[int, int], float] = {}
    for i, geom in enumerate(isl):
        reach = 2.0
        while True:
            found = [int(j) for j in tree.query(geom.buffer(reach)) if int(j) != i]
            if found or reach > span:
                break
            reach *= 4
        for j in found:
            key = (i, j) if i < j else (j, i)
            if key not in pairs:
                pairs[key] = geom.distance(isl[j])
    return pairs


def spacing(layer: GerberLayer, snap_mm: float = SNAP_MM, conductors=None,
            copper=None) -> dict:
    """Minimum copper-to-copper clearance on one layer, plus the distribution.

    Measured between CONDUCTORS. Lettering etched in copper is real copper
    and a real etching constraint, but it is not track spacing, and a fab
    quoting off "5 mil" when the board is routed to 81 is quoting a
    different board. The markings figure is still computed and returned
    beside it, so nothing is hidden — just not confused with the answer.

    The distribution is the other half. A lone 1-mil gap at one connector
    and a board routed throughout to 10 mil are the same headline number and
    very different jobs, and only the histogram tells them apart.
    """
    if conductors is None:
        conductors, _ = classify_copper(layer, copper)
    isl = conductors
    if len(isl) < 2:
        return {"min_mm": None, "islands": len(isl), "pairs": 0,
                "histogram": {}, "tightest": [], "snapped": 0,
                "with_markings_mm": None,
                "note": "one connected copper island — nothing to measure a "
                        "gap against on this layer."}
    pairs = _nearest_pairs(isl, snap_mm)
    real = {k: v for k, v in pairs.items() if v >= snap_mm}
    snapped = len(pairs) - len(real)
    if not real:
        return {"min_mm": None, "islands": len(isl), "pairs": len(pairs),
                "histogram": {}, "tightest": [], "snapped": snapped,
                "with_markings_mm": None,
                "note": f"every gap was below the {snap_mm} mm snap tolerance "
                        "— the shapes touch."}
    ordered = sorted(real.items(), key=lambda kv: kv[1])
    hist: dict[float, int] = {}
    for _, gap in ordered:
        bucket = round(round(gap / 0.025) * 0.025, 3)
        hist[bucket] = hist.get(bucket, 0) + 1
    tightest = []
    for (i, j), gap in ordered[:8]:
        try:
            from shapely.ops import nearest_points
            pa, _ = nearest_points(isl[i], isl[j])
            where = (round(pa.x, 4), round(pa.y, 4))
        except Exception:
            where = None
        tightest.append({"gap_mm": gap, "at": where})
    return {"min_mm": ordered[0][1], "islands": len(isl), "pairs": len(real),
            "histogram": dict(sorted(hist.items())), "tightest": tightest,
            "snapped": snapped, "with_markings_mm": None, "note": ""}


def board_outline(layers: list[GerberLayer]) -> dict:
    """The board's real size.

    Tries hardest first: polygonise the outline layer's strokes and take the
    largest closed face. That is the only method that survives a fiducial,
    a legend block, or a title outside the board edge — and it is the only
    one that gets a chamfered or routed profile right.

    EVERY candidate layer is considered, not just the first that yields a
    closed shape. A drill drawing carries the board rectangle and a title
    block, and on a real job the title block came first in the folder — so
    stopping at the first hit reported a 60 x 73 mm board as 98 x 13 mm.
    """
    result = {"width_mm": None, "height_mm": None, "area_mm2": None,
              "method": "", "source": "", "confident": False, "shape": "",
              "origin": (0.0, 0.0)}
    best_face, best_layer = None, None
    fallback = None
    for layer in layers:
        segs = [(a, b) for _, a, b in layer.draws]
        if len(segs) < 3:
            continue
        if HAVE_SHAPELY:
            try:
                q = 1e-3        # 1 µm — under any tolerance, over any noise
                def snap(pt):
                    return (round(pt[0] / q) * q, round(pt[1] / q) * q)
                faces = [f for f in polygonize(
                    [LineString([snap(a), snap(b)]) for a, b in segs
                     if snap(a) != snap(b)]) if f.area > 1.0]
                if faces:
                    top = max(faces, key=lambda f: f.area)
                    if best_face is None or top.area > best_face.area:
                        best_face, best_layer = top, layer
            except Exception:
                pass
        if fallback is None:
            xs = [p[0] for a, b in segs for p in (a, b)]
            ys = [p[1] for a, b in segs for p in (a, b)]
            fallback = (max(xs) - min(xs), max(ys) - min(ys), layer.name,
                        (min(xs), min(ys)))
    if best_face is not None:
        x0, y0, x1, y1 = best_face.bounds
        corners = len(best_face.exterior.coords) - 1
        result.update(
            width_mm=x1 - x0, height_mm=y1 - y0, area_mm2=best_face.area,
            method="closed outline path", source=best_layer.name,
            confident=True, origin=(x0, y0),
            shape=("rectangular" if corners <= 4 else
                   f"{corners}-sided (chamfered or routed profile)"))
        return result
    if fallback:
        w, h, name, origin = fallback
        result.update(width_mm=w, height_mm=h, origin=origin,
                      method="outline layer extents (no closed path found)",
                      source=name, confident=False, shape="")
    return result


def excellon(path: str) -> dict:
    """Parse a drill file. Returns tools in mm and a hit count per tool."""
    text = open(path, "r", errors="replace").read()
    metric = bool(re.search(r"^(METRIC|M71)", text, re.M))
    to_mm = 1.0 if metric else MM_PER_INCH

    # Coordinate format. Excellon is chronically under-specified; the header
    # sometimes says, and when it doesn't the convention is 2.4 inch / 3.3
    # metric with trailing zeros suppressed.
    lead = bool(re.search(r"LZ\b", text))
    dec = 4 if not metric else 3
    fm = re.search(r"FMAT,(\d)", text)
    if fm and fm.group(1) == "1":
        dec = 3 if not metric else 3

    tools: dict[int, float] = {}
    for m in re.finditer(r"^T(\d+)(?:[^\nCX]*)C\s*([\d.]+)", text, re.M):
        try:
            tools[int(m.group(1))] = float(m.group(2)) * to_mm
        except ValueError:
            continue
    if not tools:
        raise GerberError(f"{os.path.basename(path)}: no tool table (T..C..) "
                          "found — this does not look like an Excellon drill file.")

    def coord(raw: str) -> float:
        if "." in raw:
            return float(raw) * to_mm
        neg = raw.startswith("-")
        digits = raw.lstrip("+-")
        width = 2 + dec if not metric else 3 + dec
        digits = digits.rjust(width, "0") if lead else digits.ljust(width, "0")
        v = int(digits) * (10.0 ** -dec) * to_mm
        return -v if neg else v

    hits: dict[int, int] = {}
    positions: dict[int, list] = {}
    current: int | None = None
    body = text
    if "%" in text:
        body = text.split("%", 1)[1]
    elif re.search(r"^M95\b", text, re.M):
        body = re.split(r"^M95\b", text, maxsplit=1, flags=re.M)[1]
    for line in body.splitlines():
        line = line.strip()
        sel = re.fullmatch(r"T(\d+)", line)
        if sel:
            current = int(sel.group(1))
            continue
        pos = re.match(r"^(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?", line)
        if line[:1] in ("X", "Y") and pos and (pos.group(1) or pos.group(2)):
            hits[current] = hits.get(current, 0) + 1
            if pos.group(1) and pos.group(2):
                positions.setdefault(current, []).append(
                    (coord(pos.group(1)), coord(pos.group(2))))
            rep = re.search(r"R(\d+)", line)
            if rep:
                hits[current] += int(rep.group(1))
    used = [{"tool": t, "dia_mm": tools[t], "hits": hits.get(t, 0)}
            for t in sorted(tools)]
    return {"tools": used, "total": sum(h for h in hits.values()),
            "positions": positions, "source": os.path.basename(path),
            "as_gerber": False}


def drill_from_gerber(layer: GerberLayer) -> dict:
    """A drill file supplied as a Gerber — holes drawn as flashed circles.

    Older jobs (and some Indian CAM houses) still ship the drill this way.
    A parser that only reads Excellon returns nothing at all on those, which
    reads as "no holes" rather than "wrong format".
    """
    counts: dict[int, int] = {}
    for dcode, _ in layer.flashes:
        counts[dcode] = counts.get(dcode, 0) + 1
    used = []
    for dcode, n in counts.items():
        shape, params = layer.apertures.get(dcode, (None, []))
        if shape == "C" and params:
            used.append({"tool": dcode, "dia_mm": params[0], "hits": n})
    used.sort(key=lambda r: r["dia_mm"])
    return {"tools": used, "total": sum(r["hits"] for r in used),
            "positions": {}, "source": layer.name, "as_gerber": True}


# ── the whole job ─────────────────────────────────────────────────────────────

def analyse(paths: list[str], snap_mm: float = SNAP_MM) -> dict:
    """Measure a job. Every number here came out of the geometry."""
    if not paths:
        raise GerberError("No files given.")
    files = classify(paths)
    warnings: list[str] = []
    parsed: dict[str, GerberLayer] = {}

    for entry in files:
        if entry["kind"] != "gerber":
            continue
        try:
            layer = parse_gerber(entry["path"])
            parsed[entry["path"]] = layer
            warnings.extend(layer.warnings)
            entry["unit"] = layer.source_unit
            entry["draws"] = len(layer.draws)
            entry["flashes"] = len(layer.flashes)
            entry["regions"] = len(layer.regions)
        except GerberError as e:
            warnings.append(str(e))
        except Exception as e:                              # pragma: no cover
            warnings.append(f"{entry['name']}: could not be parsed ({e}).")

    # ── size ──
    outline_layers = [parsed[e["path"]] for e in files
                      if e["role"] == "outline" and e["path"] in parsed]
    if not outline_layers:
        # A drill guide very often carries the board rectangle, and on the
        # 2013-vintage jobs it is the ONLY place the true edge appears.
        outline_layers = [parsed[e["path"]] for e in files
                          if e["role"] in ("drill_guide", "drill_drawing")
                          and e["path"] in parsed]
        if outline_layers:
            warnings.append(
                "No outline layer (.GKO/.GM1) in this job — the board size "
                "below was taken from the drill guide/drawing, which usually "
                "carries the board rectangle. Worth confirming with the "
                "customer.")
    board = board_outline(outline_layers)
    if board["width_mm"] is None:
        copper_layers = [parsed[e["path"]] for e in files
                         if e["role"] in _COPPER_ROLES and e["path"] in parsed]
        pts = [p for l in copper_layers for _, a, b in l.draws for p in (a, b)]
        pts += [p for l in copper_layers for _, p in l.flashes]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            board.update(width_mm=max(xs) - min(xs), height_mm=max(ys) - min(ys),
                         method="COPPER BOUNDING BOX — no outline found",
                         source="copper layers", confident=False)
            warnings.append(
                "NO BOARD OUTLINE FOUND. The size below is the bounding box "
                "of the copper, which is an OVER-estimate whenever a fiducial, "
                "tooling hole or legend sits outside the board edge — on one "
                "real job that was a 10% error in area. Ask for the outline "
                "layer before quoting.")

    # ── copper ──
    copper_results = []
    for entry in files:
        if entry["role"] not in _COPPER_ROLES or entry["path"] not in parsed:
            continue
        layer = parsed[entry["path"]]
        row = {"name": entry["name"], "role": entry["role"], "widths": [],
               "min_width_mm": None, "spacing": None, "markings": 0,
               "conductors": 0, "widths_all": track_widths(layer)}
        conductors = None
        if HAVE_SHAPELY:
            try:
                copper = layer_copper(layer)
                conductors, markings = classify_copper(layer, copper)
                row["conductors"], row["markings"] = len(conductors), len(markings)
                row["spacing"] = spacing(layer, snap_mm, conductors, copper)
                if markings:
                    # Keep the everything-included figure for disclosure. A
                    # customer who asks "what about the printing?" gets an
                    # answer instead of a shrug.
                    allsp = spacing(layer, snap_mm, islands(copper), copper)
                    row["spacing"]["with_markings_mm"] = allsp.get("min_mm")
            except GerberError as e:
                warnings.append(str(e))
            except Exception as e:                          # pragma: no cover
                warnings.append(f"{entry['name']}: spacing not measured ({e}).")
        row["widths"] = track_widths(layer, conductors)
        if not row["widths"]:
            row["widths"] = row["widths_all"]
        row["min_width_mm"] = row["widths"][0]["width_mm"] if row["widths"] else None
        if row["markings"]:
            warnings.append(
                f"{entry['name']}: {row['markings']} piece(s) of copper carry no "
                "pad — lettering or a logo etched into the layer. They are "
                "excluded from track width and spacing, which measure "
                "CONDUCTORS. Including them reported 10 mil on a board routed "
                "to 118 on one real job.")
        copper_results.append(row)
    planes = [e["name"] for e in files if e["role"] in _PLANE_ROLES]
    if planes:
        warnings.append(
            f"{len(planes)} internal plane layer(s) ({', '.join(planes)}) — solid "
            "copper sheets, so they have no tracks and are excluded from track "
            "width and spacing. They DO count toward the layer total: this board "
            f"has {len(copper_results) + len(planes)} copper layers, of which "
            f"{len(copper_results)} are routed.")
    if not copper_results:
        warnings.append("No copper layer was identified in this job — track "
                        "width and spacing could not be measured.")
    if not HAVE_SHAPELY:
        warnings.append("shapely is not installed, so track SPACING was not "
                        "measured. `pip install shapely` enables it; every "
                        "other number here is unaffected.")

    # ── drills ──
    drills = None
    for entry in files:
        if entry["role"] == "drill":
            try:
                drills = excellon(entry["path"])
                break
            except GerberError as e:
                warnings.append(str(e))
    if drills is None:
        for entry in files:
            if entry["role"] == "drill_gerber" and entry["path"] in parsed:
                drills = drill_from_gerber(parsed[entry["path"]])
                warnings.append(
                    f"{entry['name']}: the drill came as a GERBER, not an "
                    "Excellon file — holes are flashed pads. Sizes and counts "
                    "below are read from the flash apertures. Plated/non-plated "
                    "is not stated in this format.")
                break
    if drills is None:
        warnings.append("No drill file found — hole size and count could not "
                        "be measured. Ask for the .DRL/.TXT drill output.")

    used_tools = [t for t in (drills["tools"] if drills else []) if t["hits"]]
    unused = [t for t in (drills["tools"] if drills else []) if not t["hits"]]
    if unused:
        warnings.append(
            f"{len(unused)} tool(s) are declared in the drill file but never "
            "used. They are excluded from the minimum drill size — a declared "
            "0.2 mm tool that drills nothing must not set the price.")

    min_width = min((r["min_width_mm"] for r in copper_results
                     if r["min_width_mm"]), default=None)
    gaps = [r["spacing"]["min_mm"] for r in copper_results
            if r["spacing"] and r["spacing"]["min_mm"]]
    min_gap = min(gaps) if gaps else None

    return {
        "files": files,
        "board": board,
        "copper": copper_results,
        "drills": drills,
        "answers": {
            "pcb_size": (f"{board['width_mm']:.2f} x {board['height_mm']:.2f} mm"
                         if board["width_mm"] else None),
            "pcb_size_mm": (board["width_mm"], board["height_mm"]),
            "min_track_width_mm": min_width,
            "min_track_spacing_mm": min_gap,
            "spacing_pairs_at_min": _pairs_at(copper_results, min_gap),
            "min_drill_mm": min((t["dia_mm"] for t in used_tools), default=None),
            "drill_count": drills["total"] if drills else None,
            "layers": len(copper_results) + len(planes),
            "routed_layers": len(copper_results),
            "plane_layers": len(planes),
        },
        "warnings": warnings,
        "snap_mm": snap_mm,
    }


def crosscheck(job: dict) -> list[dict]:
    """Compare our measurements against the report the CAM tool wrote itself.

    Most jobs ship one — Altium's .DRR, a `Read Me`, a fab's drill table —
    stating the hole count and tool sizes in plain English. It is independent
    of anything we computed, which makes it the only free accuracy test we
    get. Agreement is worth showing; disagreement is worth stopping for.
    """
    checks: list[dict] = []
    drills = job.get("drills")
    for entry in job["files"]:
        if entry["role"] != "report":
            continue
        try:
            text = open(entry["path"], "r", errors="replace").read()
        except Exception:
            continue

        m = re.search(r"^\s*Totals?\s+(\d+)\b", text, re.M | re.I)
        if m and drills:
            stated = int(m.group(1))
            checks.append({
                "what": "total holes", "source": entry["name"],
                "stated": stated, "measured": drills["total"],
                "agrees": stated == drills["total"]})

        rows = re.findall(
            r"^\s*T(\d+)\s+[\d.]+\s*mil\s*\(([\d.]+)\s*mm\)\s+(\d+)",
            text, re.M | re.I)
        if rows and drills:
            ours = {t["tool"]: t for t in drills["tools"]}
            bad = []
            for tool, dia, hits in rows:
                mine = ours.get(int(tool))
                if (mine is None or abs(mine["dia_mm"] - float(dia)) > 0.002
                        or mine["hits"] != int(hits)):
                    bad.append(f"T{tool}")
            checks.append({
                "what": f"{len(rows)} drill tools (size and hit count)",
                "source": entry["name"], "stated": f"{len(rows)} tools",
                "measured": ("all match" if not bad
                             else "differs on " + ", ".join(bad)),
                "agrees": not bad})

        m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*[xX*]\s*(\d+(?:\.\d+)?)\s*mm", text)
        if m and job["board"].get("width_mm"):
            want = sorted((float(m.group(1)), float(m.group(2))))
            got = sorted((job["board"]["width_mm"], job["board"]["height_mm"]))
            checks.append({
                "what": "board size", "source": entry["name"],
                "stated": f"{want[0]:g} x {want[1]:g} mm",
                "measured": f"{got[0]:.2f} x {got[1]:.2f} mm",
                "agrees": all(abs(a - b) < 0.5 for a, b in zip(want, got))})
    return checks


def crosscheck_text(checks: list[dict]) -> str:
    if not checks:
        return ("No report file in this job to check against — the numbers "
                "above stand on the geometry alone.")
    out = []
    for c in checks:
        mark = "✓" if c["agrees"] else "✗"
        out.append(f" {mark} {c['what']}: {c['source']} says {c['stated']}, "
                   f"Prism measured {c['measured']}")
    if all(c["agrees"] for c in checks):
        out.append("")
        out.append("Every independent figure in this job's own CAM report is "
                   "reproduced exactly.")
    else:
        out.append("")
        out.append("⚠ A figure disagrees with the job's own report. Do not quote "
                   "from this until it is understood.")
    return "\n".join(out)


# ── presentation ──────────────────────────────────────────────────────────────

def _pairs_at(copper_results: list, min_gap) -> int:
    """How many places on the board are at (or within a mil of) the minimum.

    One pair at 9 mil on a board otherwise routed to 10 is a single
    footprint. Twenty-nine pairs at 9 mil is how the board was routed, and
    the fab has to be able to etch it. Same headline number, different job —
    and the customer's own sheet said 10 where we measure 9, precisely
    because theirs is a design rule and ours is the worst case actually
    present. Neither is wrong; only one of them limits manufacture.
    """
    if min_gap is None:
        return 0
    n = 0
    for row in copper_results:
        sp = row.get("spacing") or {}
        for gap, count in (sp.get("histogram") or {}).items():
            if gap <= min_gap + 0.0254:          # within one mil
                n += count
    return n


def mm_to_mil(v: float) -> float:
    return v / MM_PER_INCH * 1000.0


def _fmt(v, mil=True) -> str:
    if v is None:
        return "not measured"
    return f"{v:.3f} mm ({mm_to_mil(v):.1f} mil)" if mil else f"{v:.3f} mm"


def answers_text(job: dict) -> str:
    """The five numbers, and nothing else. This is what gets quoted from."""
    a = job["answers"]
    b = job["board"]
    size = a["pcb_size"] or "not measured"
    if b.get("width_mm"):
        size += (f"   [{b['width_mm'] / MM_PER_INCH:.3f} x "
                 f"{b['height_mm'] / MM_PER_INCH:.3f} in]")
    layers = a.get("layers")
    layer_txt = str(layers) if layers else "not measured"
    if a.get("plane_layers"):
        layer_txt += (f"   ({a['routed_layers']} routed + "
                      f"{a['plane_layers']} solid plane)")
    lines = [
        f"0. Copper layers        {layer_txt}",
        f"1. PCB size             {size}",
        f"2. Min track width      {_fmt(a['min_track_width_mm'])}",
        f"3. Min track spacing    {_fmt(a['min_track_spacing_mm'])}"
        + (f"   — {a['spacing_pairs_at_min']} place(s) on the board are this "
           f"tight" if a.get("spacing_pairs_at_min") else ""),
        f"4. Min drill size       {_fmt(a['min_drill_mm'])}",
        f"5. Number of drills     "
        f"{a['drill_count'] if a['drill_count'] is not None else 'not measured'}",
    ]
    return "\n".join(lines)


def summary_text(job: dict) -> str:
    """The workings behind the five numbers — so they can be argued with."""
    out: list[str] = []
    b = job["board"]
    if b.get("width_mm"):
        out.append(f"SIZE      {b['width_mm']:.3f} x {b['height_mm']:.3f} mm"
                   + (f", area {b['area_mm2']:.0f} mm²" if b.get("area_mm2") else ""))
        out.append(f"          via {b['method']} in {b['source']}"
                   + (f" — {b['shape']}" if b.get("shape") else ""))
        if not b["confident"]:
            out.append("          ⚠ not from a closed outline path — treat as "
                       "approximate")
        out.append("")

    for row in job["copper"]:
        out.append(f"{row['name']}  ({_ROLE_LABEL.get(row['role'], row['role'])})")
        if row["widths"]:
            out.append("   track widths actually drawn:")
            for w in row["widths"]:
                out.append(f"      {w['width_mm']:.3f} mm "
                           f"({mm_to_mil(w['width_mm']):5.1f} mil)   "
                           f"{w['segments']:6d} segments, "
                           f"{w['length_mm'] / 1000:7.2f} m of trace")
        if row.get("markings"):
            out.append(f"   {row['conductors']} conductor(s) measured; "
                       f"{row['markings']} piece(s) of lettering/logo in copper "
                       "excluded")
        sp = row["spacing"]
        if sp and sp.get("min_mm"):
            out.append(f"   minimum clearance {_fmt(sp['min_mm'])} "
                       f"across {sp['islands']} conductors")
            if sp.get("with_markings_mm"):
                out.append(f"   (counting the lettering too it would be "
                           f"{_fmt(sp['with_markings_mm'])} — real copper, "
                           "not track spacing)")
            if sp["histogram"]:
                top = sorted(sp["histogram"].items(), key=lambda kv: kv[0])[:8]
                out.append("   gap distribution: " + ",  ".join(
                    f"{mm_to_mil(g):.0f} mil ×{n}" for g, n in top))
                out.append("   ↑ the busiest bucket is the design rule the board "
                           "was routed to; a lone tighter gap is one footprint, "
                           "not the whole board.")
            if sp["tightest"] and sp["tightest"][0].get("at"):
                x, y = sp["tightest"][0]["at"]
                out.append(f"   tightest gap is at X{x:.3f} Y{y:.3f} mm")
            if sp["snapped"]:
                out.append(f"   ({sp['snapped']} pair(s) closer than "
                           f"{job['snap_mm']} mm treated as touching, not as a gap)")
        elif sp and sp.get("note"):
            out.append(f"   {sp['note']}")
        out.append("")

    d = job["drills"]
    if d:
        how = "drill supplied as a Gerber" if d["as_gerber"] else "Excellon drill file"
        out.append(f"DRILLS    from {d['source']} ({how})")
        for t in d["tools"]:
            flag = "" if t["hits"] else "   ← declared but never used"
            out.append(f"      T{t['tool']:<4} {t['dia_mm']:6.3f} mm "
                       f"({mm_to_mil(t['dia_mm']):6.1f} mil)   "
                       f"{t['hits']:5d} holes{flag}")
        out.append(f"      {'TOTAL':<5} {'':6} {'':8}   {d['total']:5d} holes")
    return "\n".join(out).rstrip()


def files_text(job: dict) -> str:
    """What every file in the folder is, in plain words."""
    order = {"copper_top": 0, "copper_bottom": 1, "copper_inner": 2, "outline": 3,
             "drill": 4, "drill_gerber": 4, "drill_drawing": 5, "drill_guide": 6}
    rows = sorted(job["files"], key=lambda f: (order.get(f["role"], 9), f["name"]))
    used = {"copper_top", "copper_bottom", "copper_inner", "outline", "drill",
            "drill_gerber", "drill_guide", "drill_drawing"}
    out = []
    for f in rows:
        mark = "▸" if f["role"] in used else " "
        out.append(f" {mark} {f['name']:<38} {f['label']}")
    out.append("")
    out.append(" ▸ = this file was used to produce a number above.")
    return "\n".join(out)


def write_report_csv(job: dict, path: str) -> None:
    """An auditable row-per-fact file. Cross-check anything AI writes later."""
    import csv
    a = job["answers"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["measurement", "value", "unit", "source", "note"])
        b = job["board"]
        w.writerow(["pcb_width", f"{b['width_mm']:.4f}" if b["width_mm"] else "",
                    "mm", b.get("source", ""), b.get("method", "")])
        w.writerow(["pcb_height", f"{b['height_mm']:.4f}" if b["height_mm"] else "",
                    "mm", b.get("source", ""), b.get("shape", "")])
        w.writerow(["min_track_width",
                    f"{a['min_track_width_mm']:.4f}" if a["min_track_width_mm"] else "",
                    "mm", "copper layers",
                    "smallest circular aperture used to draw a CONDUCTOR "
                    "(lettering etched in copper excluded)"])
        w.writerow(["min_track_spacing",
                    f"{a['min_track_spacing_mm']:.4f}" if a["min_track_spacing_mm"] else "",
                    "mm", "copper layers",
                    f"conductors only; gaps below {job['snap_mm']} mm treated "
                    "as touching"])
        w.writerow(["min_drill", f"{a['min_drill_mm']:.4f}" if a["min_drill_mm"] else "",
                    "mm", job["drills"]["source"] if job["drills"] else "",
                    "smallest tool with at least one hit"])
        w.writerow(["drill_count", a["drill_count"] if a["drill_count"] is not None else "",
                    "holes", job["drills"]["source"] if job["drills"] else "", ""])
        w.writerow([])
        w.writerow(["drill_tool", "diameter_mm", "diameter_mil", "hits", ""])
        for t in (job["drills"]["tools"] if job["drills"] else []):
            w.writerow([f"T{t['tool']}", f"{t['dia_mm']:.4f}",
                        f"{mm_to_mil(t['dia_mm']):.1f}", t["hits"], ""])
        w.writerow([])
        w.writerow(["layer", "track_width_mm", "track_width_mil", "segments",
                    "trace_length_m"])
        for row in job["copper"]:
            for wd in row["widths"]:
                w.writerow([row["name"], f"{wd['width_mm']:.4f}",
                            f"{mm_to_mil(wd['width_mm']):.1f}", wd["segments"],
                            f"{wd['length_mm'] / 1000:.3f}"])


def gather(paths: list[str]) -> list[str]:
    """Expand folders and archives into the flat list of files to measure.

    A job arrives as a folder, a .zip or a .rar — never as fifteen selected
    files — so accepting only the flat list would fail on every real enquiry.
    """
    import tempfile
    import zipfile
    out: list[str] = []
    for p in paths:
        p = os.path.expanduser(p.strip().strip('"').strip("'"))
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                out.extend(os.path.join(root, n) for n in sorted(names)
                           if not n.startswith("."))
        elif zipfile.is_zipfile(p):
            dest = tempfile.mkdtemp(prefix="prism_gerber_")
            with zipfile.ZipFile(p) as z:
                z.extractall(dest)
            out.extend(gather([dest]))
        elif p.lower().endswith(".rar"):
            dest = tempfile.mkdtemp(prefix="prism_gerber_")
            # bsdtar ships with macOS and most Linux distributions and reads
            # RAR through libarchive; unar/unrar are the usual alternatives.
            import subprocess
            ok = False
            for cmd in (["tar", "-xf", p, "-C", dest],
                        ["unar", "-o", dest, p],
                        ["unrar", "x", "-o+", p, dest]):
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=120)
                    if r.returncode == 0 and os.listdir(dest):
                        ok = True
                        break
                except Exception:
                    continue
            if not ok:
                raise GerberError(
                    f"Could not open {os.path.basename(p)}. Install one of "
                    "`unar`, `unrar`, or a bsdtar built with RAR support — or "
                    "unzip it by hand and point Prism at the folder.")
            out.extend(gather([dest]))
        elif os.path.exists(p):
            out.append(p)
    return out
