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
    # Mechanical 2-30. Altium puts the board profile on Mechanical 1 (.GM1,
    # above) and everything else — dimensions, notes, a fab drawing, an
    # assembly view — on the rest. They are documentation, not fabrication,
    # but naming them beats calling them unrecognised: a CAM operator opening
    # a strange folder wants to know which files he can ignore, and that is
    # half of what layer identification is for.
    **{f".gm{n}": "mechanical" for n in range(2, 31)},
    ".gts": "mask_top", ".gbs": "mask_bottom",
    ".gto": "silk_top", ".gbo": "silk_bottom",
    ".gtp": "paste_top", ".gbp": "paste_bottom",
    ".gpt": "pad_master", ".gpb": "pad_master",
    ".gd1": "drill_drawing", ".gg1": "drill_guide",
    ".drl": "drill", ".xln": "drill", ".nc": "drill", ".tap": "drill",
    ".txt": "drill",   # overloaded — the content sniff decides (see classify)
    ".exc": "drill", ".drd": "drill",
    ".drr": "report", ".rep": "report", ".rpt": "report",
    ".rul": "rules", ".extrep": "report", ".ldp": "report",
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
    "mechanical": "mechanical/documentation layer (dimensions, notes, fab drawing)",
    "rules": "the designer's own DRC rules — what the board was ALLOWED to use",
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


# PADS and the older photoplotter houses name files by ROLE and LAYER
# NUMBER, not by extension: art001.pho is copper layer 1, sm010128.pho is
# the solder mask on layer 10. Two real jobs arrived this way and measured
# nothing at all, because `.pho` means only "a photoplot" and every file
# had it. The number matters as much as the prefix — 001 is the top side,
# the highest is the bottom.
_NAME_HINTS = (
    (re.compile(r"^art0*(\d+)", re.I), "copper"),
    (re.compile(r"^(?:l|lyr|layer)0*(\d+)$", re.I), "copper"),
    (re.compile(r"^smd0*(\d+)", re.I), "paste"),
    (re.compile(r"^(?:sm|mask)0*(\d+)", re.I), "mask"),
    (re.compile(r"^sst0*(\d+)", re.I), "silk"),
    (re.compile(r"^ssb0*(\d+)", re.I), "silk_bottom_forced"),
    (re.compile(r"^ss0*(\d+)", re.I), "silk"),
    (re.compile(r"^ad[bt]0*(\d+)", re.I), "mechanical"),
    (re.compile(r"^dd0*(\d+)", re.I), "drill_drawing"),
    (re.compile(r"^(?:drl|drill|nc)", re.I), "drill"),
    (re.compile(r"^(?:pf|profile|outline|border|route)", re.I), "outline"),
    (re.compile(r"paste", re.I), "paste"),
    (re.compile(r"^gko|keepout", re.I), "outline"),
)


def _name_hint(name: str) -> tuple[str, int] | None:
    """(family, layer number) from a role-and-number filename, or None."""
    stem = os.path.splitext(name)[0]
    for pattern, family in _NAME_HINTS:
        m = pattern.match(stem)
        if m:
            try:
                n = int(m.group(1))
            except (IndexError, ValueError):
                n = 0
            return family, n
    return None


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
    # Some tools ship a drill with no M48 header at all — just `%` and then
    # `T1C.008F0S0`. Reading only the header reported such a job as having
    # no drill file, on a board with 3,104 holes.
    if re.search(r"^T\d+\s*C\s*\.?\d", head, re.M):
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
        elif kind == "other" and role not in ("report", "aperture_list", "rules"):
            # A .RUL is plain prose to the sniffer, but the extension is
            # unambiguous and the file is the only place the designer's own
            # limits are stated. Do not let "I do not recognise this" throw
            # away a role the extension already settled.
            role = "drill_binary" if role == "drill" else "other"

        if role in ("unknown", "other") and kind == "gerber":
            hint = _name_hint(os.path.basename(p))
            if hint:
                family, _ = hint
                role = {"copper": "copper_inner", "mask": "mask_top",
                        "silk": "silk_top", "paste": "paste_top",
                        "silk_bottom_forced": "silk_bottom",
                        "mechanical": "mechanical",
                        "drill_drawing": "drill_drawing",
                        "outline": "outline"}.get(family, role)
        out.append({
            "path": p,
            "name": os.path.basename(p),
            "ext": ext,
            "kind": kind,
            "role": role,
            "label": _ROLE_LABEL.get(role, role),
            "size": os.path.getsize(p) if os.path.exists(p) else 0,
            "hint": _name_hint(os.path.basename(p)),
        })
    _resolve_numbered_layers(out)
    return out


def _resolve_numbered_layers(entries: list[dict]) -> None:
    """Turn `art001 … art012` into top, inner, inner … bottom.

    Numbered-family exports say which SIDE a layer is on only by its
    position in the run: the lowest number is the component side, the
    highest the solder side, everything between is inner. Guessing per-file
    cannot know that; it needs the whole job, which is why it happens here.
    """
    numbered: dict[str, list[dict]] = {}
    for e in entries:
        hint = e.get("hint")
        if hint and hint[0] in ("copper", "mask", "silk", "paste"):
            numbered.setdefault(hint[0], []).append(e)
    for family, group in numbered.items():
        group.sort(key=lambda e: e["hint"][1])
        if len(group) < 2:
            continue
        first, last = group[0], group[-1]
        roles = {"copper": ("copper_top", "copper_bottom"),
                 "mask": ("mask_top", "mask_bottom"),
                 "silk": ("silk_top", "silk_bottom"),
                 "paste": ("paste_top", "paste_bottom")}[family]
        if first["role"] in ("unknown", "other") or first["hint"]:
            first["role"] = roles[0]
        if last["role"] in ("unknown", "other") or last["hint"]:
            last["role"] = roles[1]
        for e in group:
            e["label"] = _ROLE_LABEL.get(e["role"], e["role"])


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
        self.macro_defs: dict[str, list[str]] = {}
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
        self.to_mm = 1.0
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
    layer.to_mm = to_mm
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

    for am in re.finditer(r"%AM([^*]+)\*(.*?)%", text, re.S):
        name = am.group(1).strip()
        layer.macros.add(name)
        layer.macro_defs[name] = [ln.strip().rstrip("*")
                                  for ln in am.group(2).strip().splitlines()
                                  if ln.strip().rstrip("*")]
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
                if shape not in ("C", "R", "O", "P") and shape not in layer.macro_defs:
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

def macro_shape(body: list[str], args: list[float], to_mm: float, at=(0.0, 0.0)):
    """Build a polygon from an aperture-macro definition.

    A macro is a little program: a list of primitives, each `code,exposure,
    …parameters`. Only the shapes that actually appear on copper are handled
    — circle, outline, polygon, and the three line forms — because those are
    what a rotated rectangular pad or an oval compiles to, and they account
    for every macro on the real jobs seen so far. `$n` substitutes an
    argument from the AD statement.

    Returning None is honest and safe: the caller counts the flash and warns
    that it took no part in the geometry. Guessing a shape would be neither.
    """
    if not HAVE_SHAPELY:
        return None
    x0, y0 = at
    add, cut = [], []

    def val(tok: str):
        tok = tok.strip()
        if tok.startswith("$"):
            try:
                return args[int(tok[1:]) - 1]
            except (ValueError, IndexError):
                raise ValueError(tok)
        return float(tok)

    for line in body:
        if line.startswith("0") or line.startswith("$"):
            continue                                # a comment, or an assignment
        try:
            nums = [val(t) for t in line.split(",")]
        except ValueError:
            return None                             # arithmetic — not handled
        if len(nums) < 2:
            continue
        code, expose = int(nums[0]), nums[1]
        try:
            if code == 1:                           # circle
                d, cx, cy = nums[2] * to_mm, nums[3] * to_mm, nums[4] * to_mm
                g = Point(x0 + cx, y0 + cy).buffer(d / 2, 24)
            elif code == 4:                         # outline
                n = int(nums[2])
                pts = [(x0 + nums[3 + 2 * i] * to_mm, y0 + nums[4 + 2 * i] * to_mm)
                       for i in range(n + 1)]
                g = Polygon(pts)
                if not g.is_valid:
                    g = g.buffer(0)
            elif code == 5:                         # regular polygon
                n = max(3, int(nums[2]))
                cx, cy, d = nums[3] * to_mm, nums[4] * to_mm, nums[5] * to_mm
                rot = math.radians(nums[6]) if len(nums) > 6 else 0.0
                r = d / 2
                g = Polygon([(x0 + cx + r * math.cos(rot + 2 * math.pi * k / n),
                              y0 + cy + r * math.sin(rot + 2 * math.pi * k / n))
                             for k in range(n)])
            elif code in (2, 20):                   # vector line
                w = nums[2] * to_mm
                ax, ay = nums[3] * to_mm, nums[4] * to_mm
                bx, by = nums[5] * to_mm, nums[6] * to_mm
                g = LineString([(x0 + ax, y0 + ay), (x0 + bx, y0 + by)]) \
                    .buffer(w / 2, 8, cap_style=2)
            elif code in (21, 22):                  # centre / lower-left line
                w, h = nums[2] * to_mm, nums[3] * to_mm
                cx, cy = nums[4] * to_mm, nums[5] * to_mm
                if code == 22:
                    cx, cy = cx + w / 2, cy + h / 2
                g = box(x0 + cx - w / 2, y0 + cy - h / 2,
                        x0 + cx + w / 2, y0 + cy + h / 2)
            elif code == 7:                         # thermal — outer ring
                cx, cy = nums[2] * to_mm, nums[3] * to_mm
                g = Point(x0 + cx, y0 + cy).buffer(nums[4] * to_mm / 2, 24)
            else:
                continue
        except (IndexError, ValueError):
            return None
        rot = 0.0
        if code == 4 and len(nums) > 3 + 2 * (int(nums[2]) + 1):
            rot = nums[3 + 2 * (int(nums[2]) + 1)]
        if rot:
            from shapely.affinity import rotate
            g = rotate(g, rot, origin=(x0, y0))
        (add if expose else cut).append(g)
    if not add:
        return None
    shape = unary_union(add)
    if cut:
        shape = shape.difference(unary_union(cut))
    return None if shape.is_empty else shape


def aperture_shape(shape: str, params: list[float], at=(0.0, 0.0), layer=None):
    """One flash as a polygon. None only when the shape cannot be built."""
    if not HAVE_SHAPELY:
        return None
    if not params and not (layer is not None and shape in layer.macro_defs):
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
    if layer is not None and shape in layer.macro_defs:
        return macro_shape(layer.macro_defs[shape], params, layer.to_mm, at)
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
        if kind == "path":
            dcode, pts = payload
            shape, params = layer.apertures.get(dcode, (None, []))
            if not params:
                return None
            if shape == "C":
                return LineString(pts).buffer(params[0] / 2, 8)
            if shape in ("R", "O"):
                return LineString(pts).buffer(min(params) / 2, 8, cap_style=3)
            return None
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
            return aperture_shape(shape, params, at, layer)
        try:
            poly = Polygon(payload)
            if not poly.is_valid:
                poly = poly.buffer(0)
            return None if poly.is_empty else poly
        except Exception:
            return None

    # Consecutive segments of one trace are chained into a single polyline
    # before buffering. A dense 12-layer board writes 187,674 segments where
    # there are 69,885 actual traces, and unioning them one at a time took
    # 22 seconds a layer against 9. It is also more correct: separately
    # buffered segments can fail to overlap at a joint by a rounding error
    # and split one trace into two islands.
    ops: list = []
    for kind, payload, is_dark in layer.ops:
        if kind == "draw":
            dcode, a, b = payload
            if (ops and ops[-1][0] == "path" and ops[-1][2] == is_dark
                    and ops[-1][1][0] == dcode and ops[-1][1][1][-1] == a):
                ops[-1][1][1].append(b)
                continue
            ops.append(["path", (dcode, [a, b]), is_dark])
        else:
            ops.append([kind, payload, is_dark])

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

    for kind, payload, is_dark in ops:
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
    # One vectorised `intersects` query for the whole layer. Per-island
    # Python looping meant a big pour's bounding box pulled in thousands of
    # pads and tested each against a complex polygon, one at a time.
    try:
        island_idx, _ = tree.query(isl, predicate="intersects")
        with_pad = set(int(i) for i in island_idx.tolist())
    except Exception:                           # pragma: no cover
        with_pad = set()
        for n, g in enumerate(isl):
            if any(g.intersects(pads[int(i)]) for i in tree.query(g)):
                with_pad.add(n)
    conductors = [g for n, g in enumerate(isl) if n in with_pad]
    markings = [g for n, g in enumerate(isl) if n not in with_pad]
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


# Two shapes only need comparing if they are within this of each other. It
# reaches past any sane design rule while keeping the search cheap.
_REACH_MM = 2.0

# Islands are simplified before the distance search. On a dense 12-layer
# board one copper layer carries 380,000 vertices and the exact search took
# 29 seconds; at 2 microns of tolerance it carries 134,000, takes 4, and
# returns the identical minimum. Two microns is two orders below any
# fabrication tolerance.
_SIMPLIFY_MM = 0.002

# Above this many copper islands, the second (lettering-included) spacing
# pass is skipped: it is disclosure rather than an answer, and it doubles
# the cost of the only genuinely expensive step.
_MARKINGS_LIMIT = 400


def _nearest_pairs(isl: list, snap_mm: float) -> dict:
    """For each copper island, its nearest neighbour and the gap between.

    One vectorised `query_nearest` over simplified geometry. Everything
    slower was tried on the way here: a Python loop buffering each island
    (minutes on a 2,400-island layer), then a bulk `dwithin` followed by
    exact distances on every candidate pair (27 seconds). This is 4, and
    returns the same minimum.

    Per-island nearest rather than every pair is also the better histogram:
    it asks "how close is this piece of copper to anything else", once per
    piece, instead of counting a crowded neighbourhood many times over.
    """
    simple = [g.simplify(_SIMPLIFY_MM, preserve_topology=True) for g in isl]
    tree = STRtree(simple)
    pairs: dict[tuple[int, int], float] = {}
    try:
        # Returns ((input_idx, tree_idx), distances) — a 2xN index array and
        # a 1-D distance array, NOT three arrays. Unpacking it wrong raised
        # ValueError, which the except below swallowed straight into the slow
        # fallback: correct answers, six times the wall clock, and no sign
        # anything was amiss.
        idx, dist = tree.query_nearest(
            simple, exclusive=True, all_matches=False, return_distance=True)
        left, right = idx[0], idx[1]
        for i, j in zip(left.tolist(), right.tolist()):
            if i == j:
                continue
            key = (i, j) if i < j else (j, i)
            if key not in pairs:
                # Simplification found the pair; the ORIGINAL geometry gives
                # the gap. Two microns of tolerance moved the reported
                # minimum by one and a half, which is nothing to a fab and
                # everything to a number pinned in a test.
                pairs[key] = isl[i].distance(isl[j])
        return pairs
    except Exception:                           # pragma: no cover
        pass

    # Fallback for a shapely without query_nearest.
    span = 1.0
    for g in isl:
        x0, y0, x1, y1 = g.bounds
        span = max(span, x1 - x0, y1 - y0)
    reach = _REACH_MM
    while True:
        for i, geom in enumerate(simple):
            for j in tree.query(geom.buffer(reach)):
                j = int(j)
                if j == i:
                    continue
                key = (i, j) if i < j else (j, i)
                if key not in pairs:
                    pairs[key] = isl[i].distance(isl[j])
        if pairs or reach > span:
            break
        reach *= 4
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


_ROUT = re.compile(r"^(M15|M16|G0[123])\b", re.M)


def excellon(path: str) -> dict:
    """Parse a drill file. Returns tools in mm and a hit count per tool.

    Excellon is chronically under-specified and every CAD tool leans on a
    different convention, so three things are read rather than assumed:

    · `;FILE_FORMAT=4:4` — a comment, and the only place some tools state
      the coordinate format at all.

    · LZ / TZ. `LZ` means leading zeros are PRESENT, so it is the trailing
      ones that were dropped and the digits pad to the RIGHT. `TZ` is the
      mirror. Reading it backwards scales every coordinate by a power of
      ten, which looks like a different board rather than like a bug.

    · `;TYPE=PLATED` / `;TYPE=NON_PLATED`. A job often ships plated and
      non-plated holes as separate files, and both are holes the fab has to
      drill. Reading only the first file found reported one real job as
      having no holes at all.

    A ROUT file is not a drill file. Board-edge routing is a milling path —
    a tool tracing the outline with the spindle down — and counting its
    coordinates as holes would have added hundreds of holes that nobody
    drills. Detected and reported separately.
    """
    text = open(path, "r", errors="replace").read()
    metric = bool(re.search(r"^(METRIC|M71)", text, re.M))
    to_mm = 1.0 if metric else MM_PER_INCH

    fmt = re.search(r";?\s*FILE_FORMAT\s*=\s*(\d)\s*:\s*(\d)", text, re.I)
    if fmt:
        int_digits, dec = int(fmt.group(1)), int(fmt.group(2))
    else:
        int_digits, dec = (3, 3) if metric else (2, 4)

    # LZ: leading zeros kept, trailing dropped -> pad right.
    # TZ: trailing zeros kept, leading dropped -> pad left.
    pad_right = True
    if re.search(r"\bTZ\b", text):
        pad_right = False
    elif re.search(r"\bLZ\b", text):
        pad_right = True

    plated = None
    if re.search(r";\s*TYPE\s*=\s*NON[_ ]?PLATED", text, re.I):
        plated = False
    elif re.search(r";\s*TYPE\s*=\s*PLATED", text, re.I):
        plated = True

    tools: dict[int, float] = {}
    for m in re.finditer(r"^T(\d+)(?:[^\nCX;]*)C\s*([\d.]+)", text, re.M):
        try:
            tools[int(m.group(1))] = float(m.group(2)) * to_mm
        except ValueError:
            continue
    if not tools:
        raise GerberError(f"{os.path.basename(path)}: no tool table (T..C..) "
                          "found — this does not look like an Excellon drill file.")

    body = text
    if "%" in text:
        body = text.split("%", 1)[1]
    elif re.search(r"^M95\b", text, re.M):
        body = re.split(r"^M95\b", text, maxsplit=1, flags=re.M)[1]

    is_rout = bool(_ROUT.search(body)) or bool(
        re.search(r"rout|mill|slot|profile", os.path.basename(path), re.I))

    def coord(raw: str) -> float:
        if "." in raw:
            return float(raw) * to_mm
        neg = raw.startswith("-")
        digits = raw.lstrip("+-")
        width = int_digits + dec
        digits = digits.ljust(width, "0") if pad_right else digits.rjust(width, "0")
        v = int(digits) * (10.0 ** -dec) * to_mm
        return -v if neg else v

    hits: dict[int, int] = {}
    positions: dict[int, list] = {}
    current: int | None = None
    routing = False
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("M15"):          # spindle down: a cut, not a hole
            routing = True
            continue
        if line.startswith("M16") or line.startswith("M17"):
            routing = False
            continue
        # A tool select is not always a bare `T1`. Real files write
        # `T1C.01969F095S3` — the diameter and the feed/speed repeated on
        # every select. Matching only the bare form left every hole
        # attributed to no tool at all, and a 1,000-hole job reported zero.
        sel = re.fullmatch(r"T(\d+)(?:[CFSHZB][\d.]+)*", line)
        if sel:
            current = int(sel.group(1))
            continue
        if line[:1] in ("G", "M"):
            # G00 positions the head; G01/02/03 with the spindle down mill.
            if re.match(r"^G0[123]\b", line):
                routing = True
            continue
        pos = re.match(r"^(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?", line)
        if line[:1] in ("X", "Y") and pos and (pos.group(1) or pos.group(2)):
            if routing:
                continue                    # a point on a milling path
            hits[current] = hits.get(current, 0) + 1
            if pos.group(1) and pos.group(2):
                positions.setdefault(current, []).append(
                    (coord(pos.group(1)), coord(pos.group(2))))
            rep = re.search(r"R(\d+)", line)
            if rep:
                hits[current] += int(rep.group(1))
    used = [{"tool": t, "dia_mm": tools[t], "hits": hits.get(t, 0),
             "plated": plated} for t in sorted(tools)]
    return {"tools": used, "total": sum(h for h in hits.values()),
            "positions": positions, "source": os.path.basename(path),
            "as_gerber": False, "plated": plated, "is_rout": is_rout}


def merge_drills(files: list[dict]) -> dict:
    """One job, several drill files: plated, non-plated, and a rout path.

    All of them are holes the fab drills, so all of them count. Tool numbers
    collide between files (both start at T1), so the merged table is keyed by
    DIAMETER, which is what a fab orders bits by anyway.
    """
    real = [f for f in files if not f["is_rout"]]
    routs = [f for f in files if f["is_rout"]]
    if not real:
        real, routs = files, []
    by_dia: dict[float, dict] = {}
    for f in real:
        for t in f["tools"]:
            if not t["hits"]:
                continue
            row = by_dia.setdefault(round(t["dia_mm"], 4), {
                "tool": t["tool"], "dia_mm": t["dia_mm"], "hits": 0,
                "plated": t.get("plated")})
            row["hits"] += t["hits"]
            if row["plated"] is not None and t.get("plated") != row["plated"]:
                row["plated"] = None            # both kinds at this size
    tools = sorted(by_dia.values(), key=lambda r: r["dia_mm"])
    return {"tools": tools, "total": sum(t["hits"] for t in tools),
            "positions": {}, "as_gerber": False,
            "source": ", ".join(f["source"] for f in real),
            "rout_files": [f["source"] for f in routs]}


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

def analyse(paths: list[str], snap_mm: float = SNAP_MM, on_progress=None) -> dict:
    """Measure a job. Every number here came out of the geometry.

    `on_progress(message)` is called as each layer starts. A dense 12-layer
    board takes minutes, and a terminal that prints nothing for that long
    reads as a hang — the user kills it and reports the tool as broken.
    """
    def say(msg):
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    if not paths:
        raise GerberError("No files given.")
    files = classify(paths)
    warnings: list[str] = []
    parsed: dict[str, GerberLayer] = {}

    gerbers = [e for e in files if e["kind"] == "gerber"]
    for n, entry in enumerate(gerbers, 1):
        try:
            say(f"reading {entry['name']}  ({n}/{len(gerbers)})")
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
    to_measure = [e for e in files
                  if e["role"] in _COPPER_ROLES and e["path"] in parsed]
    for n, entry in enumerate(to_measure, 1):
        layer = parsed[entry["path"]]
        say(f"measuring {entry['name']}  ({n}/{len(to_measure)}, "
            f"{len(layer.draws)} traces)")
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
                # The everything-included figure is disclosure, not an
                # answer — a customer who asks "what about the printing?"
                # gets a number instead of a shrug. It costs a second full
                # neighbour search, which is nothing on a 250-island board
                # and doubles a twelve-layer job. So it is skipped where it
                # would be felt, and says so rather than going quiet.
                if markings and len(islands(copper)) <= _MARKINGS_LIMIT:
                    allsp = spacing(layer, snap_mm, islands(copper), copper)
                    row["spacing"]["with_markings_mm"] = allsp.get("min_mm")
                elif markings:
                    row["spacing"]["with_markings_skipped"] = True
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
    found = []
    for entry in files:
        if entry["role"] == "drill":
            try:
                found.append(excellon(entry["path"]))
            except GerberError as e:
                warnings.append(str(e))
    if found:
        drills = merge_drills(found) if len(found) > 1 else found[0]
        if len(found) > 1:
            kinds = []
            for f in found:
                kind = ("rout/milling path — not counted as holes" if f["is_rout"]
                        else "plated" if f["plated"] else
                        "non-plated" if f["plated"] is False else "holes")
                kinds.append(f"{f['source']} ({kind}, {f['total']})")
            warnings.append(
                f"{len(found)} drill files in this job — all the drilled ones "
                "are counted together, keyed by diameter because tool numbers "
                "restart in each file: " + "; ".join(kinds))
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

    rules = design_rules(files)
    if rules:
        warnings.append(
            f"{rules['source']} states the DESIGNER'S rules — what the board "
            "was allowed to use, which is not what it actually uses. Both are "
            "shown; the measured figure is the one that limits manufacture.")

    return {
        "files": files,
        "rules": rules,
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
    per_file: list[tuple[str, int, int]] = []
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

        # "Drill Sizes Report": Tool / Size(mil) / Pltd / Feed / Speed / Qty
        sizes = re.findall(r"^\s*(\d+)\s+([\d.]+)\s+[x-]\s+\d+\s+\d+\s+(\d+)\s*$",
                           text, re.M)
        if sizes:
            # One report per DRILL FILE, and a job ships several — plated,
            # non-plated. Each states only its own holes, so they are summed
            # and compared once. Checking each against the job total reported
            # a perfect 8 + 1169 = 1177 as two failures.
            per_file.append((entry["name"], len(sizes),
                             sum(int(q) for _, _, q in sizes)))

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

    if per_file and drills:
        stated = sum(n for _, _, n in per_file)
        tools = sum(t for _, t, _ in per_file)
        checks.append({
            "what": f"{tools} drill tools across "
                    f"{len(per_file)} drill report(s)",
            "source": " + ".join(n for n, _, _ in per_file),
            "stated": stated, "measured": drills["total"],
            "agrees": stated == drills["total"]})
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


_RULE_LINE = re.compile(
    r"RuleKind\s*=\s*(?P<kind>\w+)\s*\|"
    r"\s*RuleName\s*=\s*(?P<name>[^|]*)\|"
    r"[^\n]*?Minimum\s*=\s*(?P<min>[\d.]+)", re.I)

# The board-wide rules, by name. A .RUL carries dozens of local exceptions
# beside them — clearance to one ASIC, via to one plane — and the tightest of
# those is not what the board was routed to.
_RULE_WANTED = {
    "width": "min_track_width_mm",
    "clearance": "min_track_spacing_mm",
    "holesize": "min_drill_mm",
    "minimumannularring": "annular_ring_mm",
}


def design_rules(files: list[dict]) -> dict:
    """What the DESIGNER said the board was allowed to use.

    A `.RUL` file is the rulebook, not the board. It says "tracks may go as
    thin as 3.94 mil"; the copper says "the thinnest actually drawn is 11.8".
    Both are true and they answer different questions — a speed limit and a
    radar reading.

    This matters commercially, not academically. A fabricator who opens the
    .RUL and quotes 3.94 against Prism's measured 11.8 sees a number three
    times out and concludes the software is broken. Reporting both, side by
    side, removes the argument before it starts.

    Units are not declared in the file. Altium writes mil, and the values
    give it away: 3.94, 7.87 and 4.92 are 0.1, 0.2 and 0.125 mm converted.
    A genuinely metric export would be under 1, so that is the test, and the
    assumption is reported rather than hidden.
    """
    out: dict = {}
    for entry in files:
        if entry["role"] != "rules":
            continue
        try:
            text = open(entry["path"], "r", errors="replace").read()
        except Exception:
            continue
        rows = list(_RULE_LINE.finditer(text))
        if not rows:
            continue
        biggest = max(float(m.group("min")) for m in rows)
        unit_is_mil = biggest > 2.0
        scale = MM_PER_INCH / 1000.0 if unit_is_mil else 1.0
        picked: dict = {}
        for m in rows:
            kind = m.group("kind").strip().lower()
            name = m.group("name").strip().lower()
            key = _RULE_WANTED.get(kind)
            # Only the rule NAMED after its kind is the board-wide one.
            if not key or name != kind:
                continue
            picked[key] = float(m.group("min")) * scale
        if picked:
            out = {"source": entry["name"],
                   "unit": "mil" if unit_is_mil else "mm", **picked}
    return out


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


def _rule_note(job: dict, key: str) -> str:
    """`(design rule allows 3.94 mil)` — the limit beside the reading."""
    rules = job.get("rules") or {}
    allowed = rules.get(key)
    if not allowed:
        return ""
    return f"   (design rule allows {mm_to_mil(allowed):.2f} mil)"


def agent_brief(job: dict, context: str = "") -> str:
    """The instruction handed to a writing agent — numbers, never a file.

    This is the one place this text is built. Both the terminal's /gerber and
    the GUI's dialog call it, so a security-critical sentence like "the
    Gerber files themselves are confidential and are NOT attached" cannot
    drift into being worded — or omitted — differently in one of the two.
    """
    a = job["answers"]
    text = (
        f"{context}\n\n" if context else
        "Reply with the measured figures below.\n\n"
    ) + (
        "These figures were MEASURED from the customer's Gerber files by "
        "Prism, on our own machine. Use them exactly as given — do not "
        "recalculate, round differently, or invent any number that is not "
        "here. The Gerber files themselves are confidential and are NOT "
        "attached.\n\n"
        f"  PCB size            {a['pcb_size']}\n"
        f"  Min track width     {_fmt(a['min_track_width_mm'])}\n"
        f"  Min track spacing   {_fmt(a['min_track_spacing_mm'])}\n"
        f"  Min drill size      {_fmt(a['min_drill_mm'])}\n"
        f"  Number of drills    {a['drill_count']}\n"
    )
    if job["warnings"]:
        text += ("\nCaveats that must be repeated to the customer if they "
                 "affect the answer:\n  - "
                 + "\n  - ".join(job["warnings"]) + "\n")
    return text


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
        f"2. Min track width      {_fmt(a['min_track_width_mm'])}"
        + _rule_note(job, "min_track_width_mm"),
        f"3. Min track spacing    {_fmt(a['min_track_spacing_mm'])}"
        + (f"   — {a['spacing_pairs_at_min']} place(s) on the board are this "
           f"tight" if a.get("spacing_pairs_at_min") else "")
        + _rule_note(job, "min_track_spacing_mm"),
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
            if sp.get("with_markings_skipped"):
                out.append("   (the lettering-included figure was skipped on "
                           "this layer — too many pieces of copper for a "
                           "second full pass; ask if you need it)")
            elif sp.get("with_markings_mm"):
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
        # The layer count belongs here too. It is one of the answers, it is
        # the one a customer is most likely to read differently from us (their
        # sheet said 2 for a board whose Gerbers hold four copper layers), and
        # a figure that only ever appears on screen cannot be checked later.
        w.writerow(["copper_layers", a.get("layers", ""), "layers",
                    "copper + plane files",
                    f"{a.get('routed_layers', 0)} routed, "
                    f"{a.get('plane_layers', 0)} solid plane"])
        w.writerow(["routed_layers", a.get("routed_layers", ""), "layers",
                    "copper files",
                    "layers carrying tracks — what a fabricator's check sheet "
                    "may mean by 'layers'"])
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
                    f"as touching; {a.get('spacing_pairs_at_min', 0)} place(s) "
                    "on the board are this tight"])
        w.writerow(["min_drill", f"{a['min_drill_mm']:.4f}" if a["min_drill_mm"] else "",
                    "mm", job["drills"]["source"] if job["drills"] else "",
                    "smallest tool with at least one hit"])
        w.writerow(["drill_count", a["drill_count"] if a["drill_count"] is not None else "",
                    "holes", job["drills"]["source"] if job["drills"] else "", ""])
        # The designer's stated limits, where the job ships them. Not the
        # same question as the measurement above, and the note says so.
        rules = job.get("rules") or {}
        for key, label in (("min_track_width_mm", "rule_allows_track_width"),
                           ("min_track_spacing_mm", "rule_allows_track_spacing"),
                           ("min_drill_mm", "rule_allows_drill"),
                           ("annular_ring_mm", "rule_min_annular_ring")):
            if rules.get(key):
                w.writerow([label, f"{rules[key]:.4f}", "mm", rules["source"],
                            "what the DESIGN was allowed to use — not what it "
                            "actually uses; the measured figure above is what "
                            "limits manufacture"])
        w.writerow([])
        w.writerow(["drill_tool", "diameter_mm", "diameter_mil", "hits", ""])
        for t in (job["drills"]["tools"] if job["drills"] else []):
            w.writerow([f"T{t['tool']}", f"{t['dia_mm']:.4f}",
                        f"{mm_to_mil(t['dia_mm']):.1f}", t["hits"], ""])
        w.writerow([])
        # Layer identification. It was printed on screen and left out of the
        # file, which is the wrong way round: the screen scrolls away and the
        # file is what gets emailed to the customer. He asked for this by
        # name, and "used" tells him which files the numbers above came from.
        w.writerow(["file", "identified_as", "used_for_measurement", "", ""])
        order = {"copper_top": 0, "copper_bottom": 1, "copper_inner": 2,
                 "plane": 3, "outline": 4, "drill": 5, "drill_gerber": 5,
                 "drill_drawing": 6, "drill_guide": 7}
        used = {"copper_top", "copper_bottom", "copper_inner", "outline",
                "drill", "drill_gerber", "drill_guide", "drill_drawing"}
        for f in sorted(job["files"], key=lambda f: (order.get(f["role"], 9),
                                                     f["name"])):
            w.writerow([f["name"], f["label"],
                        "yes" if f["role"] in used else "no", "", ""])
        w.writerow([])
        w.writerow(["layer", "track_width_mm", "track_width_mil", "segments",
                    "trace_length_m"])
        for row in job["copper"]:
            for wd in row["widths"]:
                w.writerow([row["name"], f"{wd['width_mm']:.4f}",
                            f"{mm_to_mil(wd['width_mm']):.1f}", wd["segments"],
                            f"{wd['length_mm'] / 1000:.3f}"])


_JOB_SUFFIX = re.compile(
    r"[-_ ]*(plated|non[-_ ]?plated|npth|pth|drill|drl|rout|boardedgerout|"
    r"mill|slot|top|bot|bottom|copper|preview)$", re.I)


def _job_stem(path: str) -> str:
    """The board a file belongs to, from its name.

    One CAM export shares a stem across every layer — `2-547-161A.GTL`,
    `2-547-161A.GBL`, `2-547-161A-Plated.TXT` — so stripping the role suffix
    leaves the board.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    prev = None
    while prev != stem:                       # `-BoardEdgeRout` then `-Rout`
        prev = stem
        stem = _JOB_SUFFIX.sub("", stem).strip(" -_")
    return stem.lower()


def split_jobs(paths: list[str]) -> list[tuple[str, list[str]]]:
    """Group a flat file list into separate JOBS.

    A folder a customer collects into holds several boards, and measuring
    them as one produces a single confident answer describing none of them —
    one job's outline, everyone's drill count.

    But splitting too eagerly is the same failure wearing a different hat. A
    single export routinely arrives as `Gerber/`, `NC Drill/`, `__Previews/`
    and `Report Board Stack/`, and separating those reported a real 592-hole
    job as having no drill file at all: the copper went in one job and the
    drills in another, and neither complained.

    So the FILENAME STEM decides, not the folder. Files of one export share
    it once the role suffix is stripped. A split needs real evidence — two
    or more stems with three or more fabrication files each — and anything
    that matches no group joins the largest, because a stray readme is not
    a board.
    """
    fab = {p for p in paths
           if os.path.splitext(p)[1].lower() in _EXT_ROLE
           or _sniff(p) in ("gerber", "excellon")}

    stems: dict[str, list[str]] = {}
    for p in paths:
        stems.setdefault(_job_stem(p), []).append(p)

    real = {k: v for k, v in stems.items()
            if sum(1 for p in v if p in fab) >= 3}
    if len(real) < 2:
        name = max(stems, key=lambda k: len(stems[k])) if stems else "job"
        return [(name or "job", list(paths))]

    jobs = [(k, list(v)) for k, v in sorted(real.items())]
    loose = [p for p in paths if _job_stem(p) not in real]
    if loose:
        biggest = max(range(len(jobs)), key=lambda i: len(jobs[i][1]))
        jobs[biggest] = (jobs[biggest][0], jobs[biggest][1] + loose)
    return jobs


def write_summary_csv(jobs: list[tuple[str, dict]], path: str) -> None:
    """One row per job, across every job measured — the customer's own sheet.

    He keeps a spreadsheet with a row per board: LAYER, PCB SIZE, TRACK
    WIDTH, TRACK SPACING, MIN DRILL SIZE, TOTAL DRILL. That is the artefact
    being replaced, so the output is written in his column order and in MIL,
    which is what he works in — a file he can paste into the sheet he
    already has beats a better file he has to re-key.

    Both layer readings go in. His column says 2 for a board whose Gerbers
    hold four copper layers, and both numbers are true: two are routed and
    two are solid planes. Printing one of them would be picking a side of a
    disagreement that is really a difference in wording.

    The last column names what each figure was checked against, because a
    row that reproduces the job's own CAM report is worth more than a row
    that only reproduces itself.
    """
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["JOB", "LAYERS", "ROUTED", "PCB SIZE X (in)",
                    "PCB SIZE Y (in)", "PCB SIZE (mm)", "TRACK WIDTH (mil)",
                    "TRACK SPACING (mil)", "MIN DRILL SIZE (mil)",
                    "TOTAL DRILL", "RULE ALLOWS WIDTH (mil)",
                    "RULE ALLOWS SPACING (mil)", "FILES", "CHECKED AGAINST"])
        for name, job in jobs:
            a = job["answers"]
            def m(v, dp=1):
                return "" if v is None else f"{mm_to_mil(v):.{dp}f}"
            x, y = a.get("pcb_size_mm", (None, None))
            checks = crosscheck(job)
            if checks:
                agreed = sum(1 for c in checks if c["agrees"])
                verdict = (f"{agreed}/{len(checks)} figures match the job's own "
                           "CAM report")
            else:
                verdict = "no report in the job — geometry only"
            w.writerow([
                name,
                a.get("layers", ""),
                a.get("routed_layers", ""),
                f"{x / MM_PER_INCH:.4f}" if x else "",
                f"{y / MM_PER_INCH:.4f}" if y else "",
                f"{x:.2f} x {y:.2f}" if x else "",
                m(a.get("min_track_width_mm")),
                m(a.get("min_track_spacing_mm")),
                m(a.get("min_drill_mm"), 2),
                a.get("drill_count", ""),
                m((job.get("rules") or {}).get("min_track_width_mm"), 2),
                m((job.get("rules") or {}).get("min_track_spacing_mm"), 2),
                len(job["files"]),
                verdict,
            ])
        # Layer identification, per job, underneath — he asked for it by name
        # and it does not fit one row per board.
        w.writerow([])
        w.writerow(["JOB", "FILE", "IDENTIFIED AS", "USED FOR MEASUREMENT"])
        used = {"copper_top", "copper_bottom", "copper_inner", "outline",
                "drill", "drill_gerber", "drill_guide", "drill_drawing"}
        order = {"copper_top": 0, "copper_bottom": 1, "copper_inner": 2,
                 "plane": 3, "outline": 4, "drill": 5, "drill_gerber": 5}
        for name, job in jobs:
            for f in sorted(job["files"],
                            key=lambda f: (order.get(f["role"], 9), f["name"])):
                w.writerow([name, f["name"], f["label"],
                            "yes" if f["role"] in used else "no"])


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
            nested = []
            for root, _, names in os.walk(p):
                for n in sorted(names):
                    if n.startswith("."):
                        continue
                    full = os.path.join(root, n)
                    # An archive found while walking is still an archive. A
                    # customer folder holds `job1.zip` beside `job2.rar`, and
                    # walking past them found nothing to measure.
                    if n.lower().endswith((".zip", ".rar")):
                        nested.append(full)
                    else:
                        out.append(full)
            for arc in nested:
                try:
                    out.extend(gather([arc]))
                except GerberError:
                    continue
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

    # The same job can arrive twice — an archive and the folder someone
    # already extracted it into. Counting both doubles every hole.
    seen: dict[tuple, str] = {}
    unique = []
    for f in out:
        try:
            key = (os.path.basename(f).lower(), os.path.getsize(f))
        except OSError:
            continue
        if key in seen:
            continue
        seen[key] = f
        unique.append(f)
    return unique
