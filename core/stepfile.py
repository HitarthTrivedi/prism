"""
Prism — /step: measure a 3D CAD model the way /gerber measures a PCB job
─────────────────────────────────────────────────────────────────────────
A customer sends a .STEP file of the product — a sheet-metal enclosure, a
plastic part — and the fab's estimator opens it in CAD and reads the
numbers off by hand: overall size, each part's size, wall thickness, every
hole. This module does that reading offline, on this machine, with the
same rules as the Gerber add-on:

  · No AI ever sees the STEP file. The customer's design is their product;
    only the measured numbers leave this module.
  · Everything shown is measured from the real geometry (OpenCascade, via
    the cadquery package) — never guessed, never generated.
  · Two decimal places everywhere a figure is shown or written.

What comes out, per part and for the assembly:

  · the formed (as-modelled) size L x W x H — NOTE: for bent sheet metal
    this is the finished part, not the unfolded flat sheet; the report
    says so, because a fab drawing often quotes the flat size;
  · a wall/sheet thickness ESTIMATE (2V/A — exact for a plain sheet, a
    little under for a part full of holes; the report calls it an
    estimate);
  · every hole, grouped by diameter with a count of distinct positions
    (a slot's two rounded ends count as two positions);
  · volume, surface area, and weight at common material densities —
    CRC steel for metal moulding, ABS/PP/PC/Nylon for plastic.

Deliverables written beside the terminal output:

  · dimensions.xlsx     — one row per part in the drawing's own style
                          ("101.00 x 93.00 x 71.00 mm — 1 nos") plus a
                          hole table, readable by the estimator's Excel;
  · drawing.html/.png   — a drawing sheet: an isometric view of every
                          part with its caption and holes, like the
                          hand-made sheet this replaces;
  · <part>.svg          — the individual views.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

try:
    import cadquery as _cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import (TCollection_AsciiString,
                                 TCollection_ExtendedString)
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopoDS import TopoDS
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    HAVE_CAD = True
except Exception:                                   # pragma: no cover
    HAVE_CAD = False

try:
    import openpyxl
    HAVE_XLSX = True
except Exception:                                   # pragma: no cover
    HAVE_XLSX = False


class StepError(Exception):
    """A sentence for the user, not a stack trace."""


MODES = ("metal", "plastic")

# g/cm3. The estimator multiplies volume by one of these anyway; doing it
# here saves the calculator without pretending to know the exact alloy.
_DENSITY = {
    "metal": [("CRC steel", 7.85), ("aluminium", 2.70), ("SS 304", 8.00)],
    "plastic": [("ABS", 1.04), ("PP", 0.91), ("PC", 1.20), ("Nylon 6", 1.14)],
}

_EXTS = (".step", ".stp")


def available() -> tuple[bool, str]:
    if not HAVE_CAD:
        return False, ("The STEP add-on needs the cadquery package "
                       "(pip install cadquery).")
    return True, ""


# ── reading the file, names kept ─────────────────────────────────────────────

def _name_of(label) -> str:
    n = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), n):
        return TCollection_AsciiString(n.Get()).ToCString()
    return ""


def load_parts(path: str) -> list[tuple[str, object]]:
    """[(part name, cadquery Shape)] with assembly placement applied.

    Through the XCAF reader rather than the plain importer, because the
    plain importer throws the part names away — and "top: 101.00 x 93.00"
    is worth a great deal more than "part 2: 101.00 x 93.00".
    """
    doc = TDocStd_Document(TCollection_ExtendedString("prism-step"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise StepError(f"Could not read {os.path.basename(path)} — is it a "
                        "STEP file?")
    reader.Transfer(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = TDF_LabelSequence()
    tool.GetFreeShapes(free)

    parts: list[tuple[str, object]] = []
    for i in range(1, free.Length() + 1):
        root = free.Value(i)
        comps = TDF_LabelSequence()
        if tool.GetComponents_s(root, comps) and comps.Length():
            for j in range(1, comps.Length() + 1):
                comp = comps.Value(j)
                name = _name_of(comp)
                ref = TDF_Label()
                if tool.GetReferredShape_s(comp, ref):
                    name = _name_of(ref) or name
                parts.append((name or f"part {len(parts) + 1}",
                              _cq.Shape.cast(tool.GetShape_s(comp))))
        else:
            parts.append((_name_of(root) or f"part {len(parts) + 1}",
                          _cq.Shape.cast(tool.GetShape_s(root))))
    if not parts:
        raise StepError("The file holds no solid parts.")
    return parts


# ── measuring ────────────────────────────────────────────────────────────────

def _holes_of(shape) -> list[dict]:
    """Cylindrical faces grouped by diameter: [{dia_mm, positions}].

    Positions are distinct cylinder axes, so a hole counted from both its
    halves stays one hole. A slot's two rounded ends are two positions —
    honest, and the report says a slot reads that way.
    """
    groups: dict[float, set] = {}
    for f in shape.Faces():
        if f.geomType() != "CYLINDER":
            continue
        ad = BRepAdaptor_Surface(TopoDS.Face_s(f.wrapped))
        cyl = ad.Cylinder()
        dia = round(2 * cyl.Radius(), 2)
        p = cyl.Axis().Location()
        key = (round(p.X(), 1), round(p.Y(), 1), round(p.Z(), 1))
        groups.setdefault(dia, set()).add(key)
    return [{"dia_mm": dia, "count": len(axes)}
            for dia, axes in sorted(groups.items())]


def _measure(name: str, shape) -> dict:
    bb = shape.BoundingBox()
    dims = sorted((bb.xlen, bb.ylen, bb.zlen), reverse=True)
    volume = shape.Volume()             # mm3
    area = shape.Area()                 # mm2
    return {
        "name": name,
        "size_mm": tuple(round(d, 2) for d in dims),
        # Exact for a plain sheet; slightly under for a part full of holes.
        "thickness_mm": round(2 * volume / area, 2) if area else 0.0,
        "volume_cm3": round(volume / 1000.0, 2),
        "area_cm2": round(area / 100.0, 2),
        "holes": _holes_of(shape),
    }


def analyse(path: str, mode: str = "metal") -> dict:
    """Measure every part of a STEP file. Offline; nothing leaves here."""
    ok, why = available()
    if not ok:
        raise StepError(why)
    if mode not in MODES:
        raise StepError(f"Mode must be one of {MODES}, not {mode!r}.")
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        raise StepError(f"No such file: {path}")
    if not path.lower().endswith(_EXTS):
        raise StepError("That is not a .step/.stp file.")

    named = load_parts(path)
    parts = [_measure(name, shape) for name, shape in named]
    parts.sort(key=lambda p: -p["volume_cm3"])

    compound = _cq.Compound.makeCompound([s for _, s in named])
    bb = compound.BoundingBox()
    overall = tuple(round(d, 2)
                    for d in sorted((bb.xlen, bb.ylen, bb.zlen), reverse=True))

    return {
        "file": os.path.basename(path),
        "path": path,
        "mode": mode,
        "parts": parts,
        "overall_mm": overall,
        "_shapes": named,       # for the renderer; stripped before saving
        "warnings": [
            "Sizes are the FORMED part as modelled — for bent sheet metal "
            "this is the finished part, not the unfolded flat sheet a "
            "cutting list quotes.",
            "Thickness is an estimate (2 x volume / surface); a part full "
            "of holes reads a little under its nominal sheet.",
            "A slot's two rounded ends count as two hole positions.",
        ],
    }


# ── words and numbers out ────────────────────────────────────────────────────

def _weights(volume_cm3: float, mode: str) -> str:
    return " · ".join(f"{name} {volume_cm3 * dens:.2f} g"
                      for name, dens in _DENSITY[mode])


def _caption(part: dict, mode: str) -> str:
    L, W, H = part["size_mm"]
    stock = (f"t≈{part['thickness_mm']:.2f} mm sheet" if mode == "metal"
             else f"wall≈{part['thickness_mm']:.2f} mm")
    return f"{L:.2f} x {W:.2f} x {H:.2f} mm · {stock} — 1 nos"


def report_text(report: dict) -> str:
    lines = [f"STEP MEASUREMENT — {report['file']}   "
             f"({report['mode']} moulding)",
             "",
             f"  Assembly overall    {report['overall_mm'][0]:.2f} x "
             f"{report['overall_mm'][1]:.2f} x {report['overall_mm'][2]:.2f} mm",
             f"  Parts               {len(report['parts'])}",
             ""]
    for i, part in enumerate(report["parts"], 1):
        lines.append(f"  {i}) {part['name']}  —  {_caption(part, report['mode'])}")
        lines.append(f"     volume {part['volume_cm3']:.2f} cm3 · "
                     f"weight {_weights(part['volume_cm3'], report['mode'])}")
        if part["holes"]:
            lines.append("     holes  " + " · ".join(
                f"Ø{h['dia_mm']:g} x {h['count']}" for h in part["holes"]))
        lines.append("")
    for w in report["warnings"]:
        lines.append(f"  ! {w}")
    lines.append("")
    lines.append("  Measured offline from the geometry itself — the STEP "
                 "file never leaves this machine and no AI ever sees it.")
    return "\n".join(lines)


def write_xlsx(report: dict, path: str) -> str:
    """The estimator's sheet: one row per part, then every hole."""
    if not HAVE_XLSX:
        raise StepError("Writing Excel needs the openpyxl package.")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Parts"
    bold = openpyxl.styles.Font(bold=True)

    ws.append([f"{report['file']} — {report['mode']} moulding — "
               f"measured {_dt.date.today().strftime('%d-%m-%Y')}"])
    ws["A1"].font = bold
    ws.append([])
    header = ["Sr", "Part", "L (mm)", "W (mm)", "H (mm)",
              "Thickness est (mm)", "Volume (cm3)",
              "Weight (g)", "Holes", "Line for the drawing"]
    ws.append(header)
    for cell in ws[ws.max_row]:
        cell.font = bold
    first = _DENSITY[report["mode"]][0]
    for i, part in enumerate(report["parts"], 1):
        L, W, H = part["size_mm"]
        holes = " · ".join(f"Ø{h['dia_mm']:g} x {h['count']}"
                           for h in part["holes"]) or "—"
        ws.append([i, part["name"], L, W, H, part["thickness_mm"],
                   part["volume_cm3"],
                   round(part["volume_cm3"] * first[1], 2),
                   holes, f"{i}) {_caption(part, report['mode'])}"])
    ws.append([])
    ws.append(["Assembly overall",
               f"{report['overall_mm'][0]:.2f} x {report['overall_mm'][1]:.2f}"
               f" x {report['overall_mm'][2]:.2f} mm"])
    ws.append([f"Weight column uses {first[0]} at {first[1]} g/cm3."])
    for w in report["warnings"]:
        ws.append([f"! {w}"])
    for col, width in zip("ABCDEFGHIJ", (4, 14, 10, 10, 10, 16, 12, 12, 40, 44)):
        ws.column_dimensions[col].width = width

    holes_ws = wb.create_sheet("Holes")
    holes_ws.append(["Part", "Diameter (mm)", "Positions"])
    for cell in holes_ws[1]:
        cell.font = bold
    for part in report["parts"]:
        for h in part["holes"]:
            holes_ws.append([part["name"], h["dia_mm"], h["count"]])
    for col, width in zip("ABC", (14, 14, 10)):
        holes_ws.column_dimensions[col].width = width

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    wb.save(path)
    return path


# ── the drawing sheet ────────────────────────────────────────────────────────

def _svg_of(shape, path: str) -> bool:
    """One part as a hidden-line isometric SVG. A view that cannot be drawn
    must not sink the measurement — the sheet shows the caption alone."""
    try:
        from cadquery.occ_impl.exporters.svg import getSVG
        svg = getSVG(shape, opts={"width": 420, "height": 340,
                                  "marginLeft": 24, "marginTop": 24,
                                  "showAxes": False, "showHidden": True,
                                  "projectionDir": (1, -1.2, 1)})
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        return True
    except Exception:                               # noqa: BLE001
        return False


def render_sheet(report: dict, out_dir: str) -> dict:
    """drawing.html (+ .png when a browser engine is present) — every part
    as an isometric view with the caption and holes under it, in the shape
    of the hand-made sheet this replaces."""
    os.makedirs(out_dir, exist_ok=True)
    cells = []
    shapes = dict((name, s) for name, s in report.get("_shapes") or [])
    for i, part in enumerate(report["parts"], 1):
        safe = re.sub(r"[^\w.-]", "_", part["name"]) or f"part{i}"
        svg_name = f"{safe}.svg"
        drawn = False
        shape = shapes.get(part["name"])
        if shape is not None:
            drawn = _svg_of(shape, os.path.join(out_dir, svg_name))
        holes = " · ".join(f"Ø{h['dia_mm']:g} × {h['count']}"
                           for h in part["holes"]) or "no holes"
        img = (f'<img src="{svg_name}" alt="">' if drawn
               else '<p class="nodraw">(view could not be drawn)</p>')
        cells.append(
            f'<figure>{img}<figcaption><b>{i}) {part["name"]}</b> — '
            f'{_caption(part, report["mode"])}<br>'
            f'<span class="holes">{holes}</span></figcaption></figure>')

    o = report["overall_mm"]
    html = (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{report['file']}</title>"
        "<style>body{font:14px/1.45 -apple-system,'Segoe UI',sans-serif;"
        "margin:28px;color:#16181a}h1{font-size:21px;margin:0}"
        ".sub{color:#5a6067;margin:2px 0 18px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,"
        "minmax(430px,1fr));gap:20px}"
        "figure{margin:0;border:1px solid #d7dade;border-radius:8px;"
        "padding:12px;background:#fff}"
        "img{width:100%;height:auto}figcaption{margin-top:8px}"
        ".holes{color:#41586e;font-size:13px}"
        ".nodraw{color:#8a9098}.note{color:#8a6d1f;font-size:12.5px;"
        "margin-top:18px}</style>"
        f"<h1>{report['file']} — measured drawing sheet</h1>"
        f"<p class='sub'>{report['mode']} moulding · assembly "
        f"{o[0]:.2f} x {o[1]:.2f} x {o[2]:.2f} mm · "
        f"{len(report['parts'])} part(s) · measured offline by Prism, "
        f"{_dt.date.today().strftime('%d-%m-%Y')}</p>"
        f"<div class='grid'>{''.join(cells)}</div>"
        + "".join(f"<p class='note'>! {w}</p>" for w in report["warnings"]))
    html_path = os.path.join(out_dir, "drawing.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    png_path = ""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1360, "height": 900})
            page.goto("file://" + html_path, wait_until="load")
            png_path = os.path.join(out_dir, "drawing.png")
            page.screenshot(path=png_path, full_page=True)
            browser.close()
    except Exception:                               # noqa: BLE001
        png_path = ""   # the HTML stands on its own
    return {"html": html_path, "png": png_path}


def auto_brief(report: dict) -> str:
    """The prompt /step-auto hands the image agent.

    The security model lives IN the prompt: only these measured figures and
    Prism's own plain render travel to the tool — the customer's STEP file
    stays on this machine, and the brief says so out loud, so the agent
    neither asks for the model nor invents a number to fill a gap.
    """
    o = report["overall_mm"]
    lines = [
        "Draw ONE professional sheet-metal fabrication drawing sheet as a "
        "single portrait image, in the plain style of a manufacturer's "
        "hand-made dimension sheet: black line-work on white, one labelled "
        "view per part, dimension lines with arrowheads for length, width "
        "and height, hole callouts written as diameter x count, and a small "
        "title block at the bottom.",
        "",
        "Every figure below was MEASURED OFFLINE by Prism from the "
        "customer's CAD model. The model itself is confidential and is not "
        "shared — the attached image is Prism's own plain render of the "
        "parts, for view reference only.",
        "",
        f"Job: {report['file']} · {report['mode']} moulding · "
        f"assembly overall {o[0]:.2f} x {o[1]:.2f} x {o[2]:.2f} mm · "
        f"{len(report['parts'])} part(s)",
        "",
    ]
    for i, part in enumerate(report["parts"], 1):
        L, W, H = part["size_mm"]
        lines.append(f"{i}) {part['name']} — {L:.2f} x {W:.2f} x {H:.2f} mm "
                     f"· sheet t≈{part['thickness_mm']:.2f} mm · 1 nos")
        if part["holes"]:
            lines.append("   holes: " + " · ".join(
                f"Ø{h['dia_mm']:g} x {h['count']}" for h in part["holes"]))
    lines += [
        "",
        "Title block: job name, material "
        + ("CRC sheet" if report["mode"] == "metal" else "moulded plastic")
        + ", scale NTS, today's date, and the line "
          "'Measured offline by Prism'.",
        "Rules: use EXACTLY the numbers above — do not round, convert or "
        "invent any dimension, and do not add parts or holes that are not "
        "listed. Label every part with its name.",
        "Never label any view 'flat pattern' or 'developed' — no unfolded "
        "flat has been computed, and a fabricator would cut from it. Views "
        "are of the FORMED part only. Do not state any tolerance, grade or "
        "finish that is not written above; general notes may only say the "
        "dimensions are in millimetres and hole positions are indicative.",
    ]
    return "\n".join(lines)


# ── /step-ask: a question → Groq's advice → an agent's plan → applied ───────
# The customer's model still never leaves this machine. Groq gets measured
# numbers and the question; the browser agent gets those plus Groq's advice;
# and the geometry edits themselves happen HERE, in cadquery, on a copy.

PLAN_OPS = ("enlarge_hole", "scale")


def _part_lines(report: dict) -> str:
    lines = []
    for i, part in enumerate(report["parts"], 1):
        L, W, H = part["size_mm"]
        lines.append(f"{i}) {part['name']} — {L:.2f} x {W:.2f} x {H:.2f} mm "
                     f"· wall/sheet t≈{part['thickness_mm']:.2f} mm · "
                     f"volume {part['volume_cm3']:.2f} cm3")
        if part["holes"]:
            lines.append("   holes: " + " · ".join(
                f"Ø{h['dia_mm']:g} x {h['count']}" for h in part["holes"]))
    return "\n".join(lines)


def ask_prompt(report: dict, question: str) -> str:
    """What Groq is asked. Numbers and the question — never the model."""
    o = report["overall_mm"]
    return (
        f"You are advising a {report['mode']} moulding shop on a customer's "
        "part. The CAD model is confidential and cannot be shown to you — "
        "everything known about it was measured offline and is below.\n\n"
        f"Job: {report['file']} · assembly overall "
        f"{o[0]:.2f} x {o[1]:.2f} x {o[2]:.2f} mm · "
        f"{len(report['parts'])} part(s)\n"
        f"{_part_lines(report)}\n\n"
        f"The customer asks: {question}\n\n"
        "Give short, numbered, practical suggestions grounded ONLY in the "
        "figures above — do not invent features you cannot see. Where a "
        "suggestion is a hole size change or an overall scale change, state "
        "it precisely: which part, current Ø, new Ø (or scale factor), and "
        "why. Mark anything that would need the customer's designer (ribs, "
        "draft, wall changes) as their decision, not ours.")


def plan_prompt(report: dict, question: str, suggestions: str) -> str:
    """What the reviewing agent is asked: turn the advice into a strict
    machine plan of ONLY the operations Prism can execute locally."""
    return (
        "You are the reviewing engineer. Below are offline measurements of "
        "a confidential CAD model (the model itself is not shared) and a "
        "first advisor's suggestions. Decide which changes are right, then "
        "answer with ONE JSON object and nothing else.\n\n"
        f"MEASURED ({report['mode']} moulding, {report['file']}):\n"
        f"{_part_lines(report)}\n\n"
        f"THE CUSTOMER ASKED: {question}\n\n"
        f"FIRST ADVISOR SAID:\n{suggestions}\n\n"
        "Prism can execute exactly two operations on the model, locally:\n"
        '  {"op": "enlarge_hole", "part": "<part name or all>", '
        '"dia_mm": <current>, "new_dia_mm": <bigger>, "why": "..."}\n'
        '  {"op": "scale", "part": "<part name or all>", '
        '"factor": <0.2..5>, "why": "..."}\n\n'
        "Answer format:\n"
        '{"changes": [ ...only the two ops above, only if truly right... ],\n'
        ' "advice":  [ "every other worthwhile suggestion, as a sentence" ]}\n\n'
        "Rules: use only part names and hole diameters that appear in the "
        "measurements. A hole can only be enlarged, never shrunk. When no "
        "executable change is justified, return an empty changes list — an "
        "honest empty list beats an invented edit.")


def _valid_change(ch) -> dict | None:
    if not isinstance(ch, dict):
        return None
    part = str(ch.get("part") or "all").strip() or "all"
    why = str(ch.get("why") or "")[:240]
    try:
        if ch.get("op") == "enlarge_hole":
            dia, new = float(ch["dia_mm"]), float(ch["new_dia_mm"])
            if not 0 < dia < new:
                return None
            return {"op": "enlarge_hole", "part": part, "dia_mm": round(dia, 2),
                    "new_dia_mm": round(new, 2), "why": why}
        if ch.get("op") == "scale":
            factor = float(ch["factor"])
            if not 0.2 <= factor <= 5 or factor == 1:
                return None
            return {"op": "scale", "part": part,
                    "factor": round(factor, 4), "why": why}
    except (KeyError, TypeError, ValueError):
        return None
    return None


def parse_plan(texts: list[str]) -> tuple[dict | None, str]:
    """Newest capture that parses — the tab also holds the prompt Prism
    typed, which carries the example schema inside it."""
    import json
    for t in reversed([t for t in texts if t and t.strip()]):
        s, e = t.find("{"), t.rfind("}") + 1
        if s == -1 or e <= s:
            continue
        try:
            raw = json.loads(t[s:e])
        except ValueError:
            continue
        if not isinstance(raw, dict) or not (
                "changes" in raw or "advice" in raw):
            continue
        changes = [c for c in map(_valid_change, raw.get("changes") or [])
                   if c]
        advice = [str(a).strip() for a in (raw.get("advice") or [])
                  if str(a).strip()][:12]
        return {"changes": changes, "advice": advice}, ""
    return None, "The agent returned no JSON plan Prism could read."


def _enlarged(shape, dia: float, new_dia: float):
    """Cut a bigger cylinder along every existing axis of the Ø`dia` holes.
    Enlarge only — shrinking would mean adding material, which a boolean
    cut cannot do and _valid_change refuses upstream."""
    bb = shape.BoundingBox()
    span = (bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2) ** 0.5 or 1.0
    seen, cutters = set(), []
    for f in shape.Faces():
        if f.geomType() != "CYLINDER":
            continue
        cyl = BRepAdaptor_Surface(TopoDS.Face_s(f.wrapped)).Cylinder()
        if abs(2 * cyl.Radius() - dia) > 0.05:
            continue
        ax = cyl.Axis()
        loc, d = ax.Location(), ax.Direction()
        key = (round(loc.X(), 1), round(loc.Y(), 1), round(loc.Z(), 1))
        if key in seen:
            continue
        seen.add(key)
        start = _cq.Vector(loc.X(), loc.Y(), loc.Z()) - \
            _cq.Vector(d.X(), d.Y(), d.Z()) * span
        cutters.append(_cq.Solid.makeCylinder(
            new_dia / 2, 2 * span, pnt=start,
            dir=_cq.Vector(d.X(), d.Y(), d.Z())))
    for c in cutters:
        shape = shape.cut(c)
    return shape, len(seen)


def _scaled(shape, factor: float):
    """gp_Trsf, not transformGeometry: the general transform rewrites every
    cylinder as a b-spline, and a hole that is no longer a CYLINDER face
    vanishes from _holes_of — the re-measure would deny holes that exist."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Pnt, gp_Trsf
    t = gp_Trsf()
    t.SetScale(gp_Pnt(0, 0, 0), factor)
    return _cq.Shape.cast(
        BRepBuilderAPI_Transform(shape.wrapped, t, True).Shape())


def apply_plan(path: str, plan: dict, out_path: str) -> dict:
    """Execute the validated plan on a COPY; the original file is never
    written. Returns {"out": path, "log": [human lines]}."""
    parts = load_parts(path)
    by_name = dict(parts)
    log = []
    for ch in plan.get("changes") or []:
        names = ([n for n, _s in parts] if ch["part"] == "all"
                 else [n for n, _s in parts if n == ch["part"]])
        if not names:
            log.append(f"! no part called '{ch['part']}' — skipped")
            continue
        for name in names:
            if ch["op"] == "enlarge_hole":
                shape, n = _enlarged(by_name[name],
                                     ch["dia_mm"], ch["new_dia_mm"])
                if n:
                    by_name[name] = shape
                    log.append(f"Ø{ch['dia_mm']:g} → Ø{ch['new_dia_mm']:g} "
                               f"on {name}: {n} hole(s) enlarged")
                elif ch["part"] != "all":
                    log.append(f"! no Ø{ch['dia_mm']:g} hole on {name} "
                               "— skipped")
            elif ch["op"] == "scale":
                by_name[name] = _scaled(by_name[name], ch["factor"])
                log.append(f"{name} scaled x {ch['factor']:g}")
    if not any(not line.startswith("!") for line in log):
        raise StepError("Nothing in the plan could be applied — "
                        "the model was not written.")
    asm = _cq.Assembly()
    for name, _s in parts:
        asm.add(by_name[name], name=name)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    asm.export(out_path)
    return {"out": out_path, "log": log}


def predicted_parts(report: dict, plan: dict) -> list[dict]:
    """What the parts SHOULD measure after the plan — arithmetic on the
    measured figures, for the review page. The real answer still comes from
    re-measuring the built file; this is the promise the user approves."""
    import copy
    parts = copy.deepcopy(report["parts"])
    for ch in plan.get("changes") or []:
        for p in parts:
            if ch["part"] not in ("all", p["name"]):
                continue
            if ch["op"] == "enlarge_hole":
                for h in p["holes"]:
                    if abs(h["dia_mm"] - ch["dia_mm"]) <= 0.05:
                        h["dia_mm"] = ch["new_dia_mm"]
                merged: dict[float, int] = {}
                for h in p["holes"]:
                    merged[h["dia_mm"]] = merged.get(h["dia_mm"], 0) + h["count"]
                p["holes"] = [{"dia_mm": d, "count": c}
                              for d, c in sorted(merged.items())]
            elif ch["op"] == "scale":
                f = ch["factor"]
                p["size_mm"] = tuple(round(v * f, 2) for v in p["size_mm"])
                p["thickness_mm"] = round(p["thickness_mm"] * f, 2)
                p["volume_cm3"] = round(p["volume_cm3"] * f ** 3, 2)
                p["holes"] = [{"dia_mm": round(h["dia_mm"] * f, 2),
                               "count": h["count"]} for h in p["holes"]]
    return parts


def _holes_str(part: dict) -> str:
    return " · ".join(f"Ø{h['dia_mm']:g} x {h['count']}"
                      for h in part["holes"]) or "—"


def review_html(report: dict, plan: dict, out_dir: str,
                question: str = "", after: dict | None = None) -> str:
    """The approval page: every dimension before and after, side by side,
    with the drawing image — written BEFORE anything is built, so the user
    confirms against what they can see, not against terminal text. Called
    again with `after` (the re-measured report) once modified.step exists,
    so the same page becomes the record of what was actually done."""
    import html as _html

    before = report["parts"]
    shown = ([{"name": p["name"], "size_mm": p["size_mm"],
               "thickness_mm": p["thickness_mm"],
               "volume_cm3": p["volume_cm3"], "holes": p["holes"]}
              for p in after["parts"]] if after
             else predicted_parts(report, plan))
    by_after = {p["name"]: p for p in shown}

    if after:
        banner = ("<div class='banner built'>modified.step is BUILT — the "
                  "After column is re-measured from the new file, not "
                  "predicted.</div>")
    else:
        banner = ("<div class='banner'>Nothing is built yet. This page is "
                  "the plan — go back to the terminal and answer Y to "
                  "write modified.step, or N to stop here.</div>")

    def fmt(size):
        return f"{size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f}"

    change_rows = []
    for ch in plan.get("changes") or []:
        what = (f"Hole Ø{ch['dia_mm']:g} → Ø{ch['new_dia_mm']:g}"
                if ch["op"] == "enlarge_hole"
                else f"Scale x {ch['factor']:g}")
        change_rows.append(
            f"<tr><td>{_html.escape(ch['part'])}</td>"
            f"<td>{_html.escape(what)}</td>"
            f"<td>{_html.escape(ch.get('why') or '')}</td></tr>")

    part_rows = []
    for p in before:
        q = by_after.get(p["name"], p)
        changed_size = p["size_mm"] != q["size_mm"]
        changed_holes = _holes_str(p) != _holes_str(q)
        mark = " class='changed'"
        part_rows.append(
            "<tr>"
            f"<td>{_html.escape(p['name'])}</td>"
            f"<td>{fmt(p['size_mm'])}</td>"
            f"<td{mark if changed_size else ''}>{fmt(q['size_mm'])}</td>"
            f"<td>{_holes_str(p)}</td>"
            f"<td{mark if changed_holes else ''}>{_holes_str(q)}</td>"
            f"<td>{p['thickness_mm']:.2f} → {q['thickness_mm']:.2f}</td>"
            "</tr>")

    advice = "".join(f"<li>{_html.escape(a)}</li>"
                     for a in plan.get("advice") or [])
    imgs = []
    if os.path.exists(os.path.join(out_dir, "drawing.png")):
        imgs.append(("The part as received", "drawing.png"))
    if after and os.path.exists(os.path.join(out_dir, "drawing_after.png")):
        imgs.append(("The modified model — drawn from the BUILT file",
                     "drawing_after.png"))
    img = "".join(f"<h2>{t}</h2><img src='{f}'>" for t, f in imgs)

    page = (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{_html.escape(report['file'])} — change review</title>"
        "<style>body{font-family:-apple-system,Segoe UI,sans-serif;margin:32px "
        "auto;max-width:1000px;color:#1c2733;background:#fafbfc;padding:0 16px}"
        "h1{font-size:22px}h2{font-size:15px;margin-top:28px}"
        "table{border-collapse:collapse;width:100%;font-size:13.5px}"
        "th,td{border:1px solid #d7dade;padding:7px 10px;text-align:left}"
        "th{background:#eef1f4}td.changed{background:#e7f6ec;font-weight:600}"
        ".banner{background:#fff4d6;border:1px solid #e3c96e;padding:12px "
        "16px;border-radius:8px;margin:14px 0;font-weight:600}"
        ".banner.built{background:#e7f6ec;border-color:#7cc796}"
        ".q{color:#41586e}img{max-width:100%;border:1px solid #d7dade;"
        "border-radius:8px}.note{color:#8a6d1f;font-size:12.5px}</style>"
        f"<h1>{_html.escape(report['file'])} — change review</h1>"
        f"<p class='q'>Asked: {_html.escape(question)}</p>"
        f"{banner}"
        "<h2>Changes Prism will make"
        + (" (made)" if after else "") + "</h2>"
        "<table><tr><th>Part</th><th>Change</th><th>Why</th></tr>"
        + "".join(change_rows) + "</table>"
        "<h2>Every dimension, before → after</h2>"
        "<table><tr><th>Part</th><th>Size before (mm)</th>"
        f"<th>Size {'after' if after else 'after (predicted)'} (mm)</th>"
        "<th>Holes before</th>"
        f"<th>Holes {'after' if after else 'after (predicted)'}</th>"
        "<th>t (mm)</th></tr>"
        + "".join(part_rows) + "</table>"
        + (f"<h2>For the designer (not applied)</h2><ul>{advice}</ul>"
           if advice else "")
        + img
        + "<p class='note'>Measured offline by Prism — the STEP file never "
          "left this machine; the edits run locally on a copy.</p>")

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "review.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
