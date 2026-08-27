"""
Prism — cleaning a Gerber job: drop what lies outside the board
─────────────────────────────────────────────────────────────────
The first of the CAM operator's jobs, done by the file rather than by hand:
open every layer, keep what is on the board, remove what is not — the
title block, the stray text, the copper a designer left past the edge —
and write the layers back out so a person can put them beside the ones
they cleaned themselves in CAM and see how close Prism got.

What "inside" means here is exactly what board_outline() measures: the
largest closed shape on the outline layer (`gerber.outline_face`). One
definition, shared, so the size Prism quotes and the copper Prism keeps
cannot disagree.

The rules, deliberately conservative — a wrongly cut production file is a
scrapped panel, and nobody can undo that:

  · An object whose whole extent is OUTSIDE the board is removed.
  · An object whose whole extent is INSIDE is kept, untouched.
  · An object that CROSSES the edge is kept, untouched, and listed — cutting
    a stroked track at the edge means rewriting it as a region, which changes
    the file's meaning, and whether to cut or delete is the operator's call.
  · A small margin (default 0.05 mm) around the edge counts as inside, so a
    pad that kisses the board edge is not thrown away for a rounding error.
  · An ARRAY is cleaned against the whole panel, not one board of it: the
    rails, fiducials and tooling holes between the boards are wanted, and
    the other boards are certainly wanted. The first run on the real panel
    took one 184 x 39 board as "the outline" and removed the other four.
  · If NOTHING on a layer lands on the board, or the layer's whole extent
    sits almost entirely off it, or more than a third of the layer's copper
    BY AREA would go — nothing on that layer is removed and the layer is
    flagged. Those patterns mean the outline and the layer disagree about
    where the board is, not that the board is junk. (Counting objects would
    not do: a legend beside the board is hundreds of tiny strokes, the board
    itself may be eighty pads — so the guard is on area, and it caught an
    outline taken from a drill drawing that would have deleted 60% of a
    twelve-layer board.)
  · Only image layers are cleaned — copper, mask, silkscreen, paste, pad
    master. The outline itself, mechanical layers, drill files and reports
    are copied through as they are.

Reading and writing the Gerber goes through gerbonara, an independent
open-source reader/writer, so the file that comes out is a faithful copy
of the one that went in minus the removed objects — apertures, formats
and attributes preserved — rather than a re-creation from our own
geometry. Our own parser is used for the one thing it is verified for:
finding the board edge.
"""
from __future__ import annotations

import csv
import os
import shutil
import warnings

from . import gerber as G

try:
    from shapely.geometry import box as _box
    HAVE_SHAPELY = True
except Exception:                                   # pragma: no cover
    HAVE_SHAPELY = False

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from gerbonara import GerberFile
    HAVE_GERBONARA = True
except Exception:                                   # pragma: no cover
    GerberFile = None
    HAVE_GERBONARA = False

# The layers that carry an image of the board and can hold stray objects.
# Everything else — the outline, mechanical drawings, drills, reports — is
# copied through untouched.
CLEAN_ROLES = frozenset({
    "copper_top", "copper_bottom", "copper_inner",
    "mask_top", "mask_bottom", "silk_top", "silk_bottom",
    "paste_top", "paste_bottom", "pad_master",
})
DEFAULT_MARGIN_MM = 0.05
# Below this share of a layer's own extent lying over the board, the outline
# and the layer disagree about the origin; removing would destroy the layer.
MIN_ON_BOARD_SHARE = 0.10
# Above this share of a layer's drawn AREA lying outside, what is outside is
# not junk — the outline is wrong — and nothing is removed.
MAX_REMOVED_AREA_SHARE = 0.35
REPORT_TXT = "cleaning_report.txt"
REPORT_CSV = "cleaning_report.csv"
PREVIEW_DIR = "previews"
COMPARE_HTML = "compare.html"


class CleanError(G.GerberError):
    pass


def outline_for(files: list[dict], on_progress=None):
    """The board edge for this job, parsed with Prism's own reader — the
    same layers, in the same order of preference, as analyse() uses."""
    say = on_progress or (lambda _line: None)

    def parse(role_set):
        layers = []
        for entry in files:
            if entry["kind"] == "gerber" and entry["role"] in role_set:
                say(f"reading the outline: {entry['name']}")
                try:
                    layers.append(G.parse_gerber(entry["path"]))
                except Exception as e:              # noqa: BLE001
                    say(f"  could not read {entry['name']}: {e}")
        return layers

    layers = parse({"outline"})
    source = "outline layer"
    face, layer = G.outline_face(layers)
    if face is None:
        layers = parse({"drill_guide", "drill_drawing"})
        source = "drill guide/drawing (no outline layer in the job)"
        face, layer = G.outline_face(layers)
    if face is None:
        return None, source
    array = G.panel(layers)
    if array["is_array"]:
        # The whole panel, rails included — see the module docstring.
        x0, y0 = array["origin"]
        frame = None
        for l in layers:
            for f in (G.closed_faces(l, *G._CLOSE_LADDER[0])
                      or G.closed_faces(l, *G._CLOSE_LADDER[1])):
                b = f.bounds
                if (b[2] - b[0] >= array["array_w"] - 1e-6
                        and b[3] - b[1] >= array["array_h"] - 1e-6):
                    frame = f if frame is None or f.area > frame.area else frame
        if frame is not None:
            face = frame
        else:
            face = _box(x0, y0, x0 + array["array_w"], y0 + array["array_h"])
        source = (f"{source} — a panel of {array['count']} boards of "
                  f"{array['pcb_w']:.2f} x {array['pcb_h']:.2f} mm; cleaned "
                  "against the whole panel")
    return face, source


def clean_job(paths: list[str], out_dir: str, *,
              margin_mm: float = DEFAULT_MARGIN_MM, on_progress=None) -> dict:
    """Clean one job into `out_dir`. Returns the report as a dict and
    writes it beside the layers as text and CSV."""
    if not HAVE_GERBONARA:
        raise CleanError(
            "Cleaning needs the gerbonara package (pip install gerbonara).")
    if not HAVE_SHAPELY:
        raise CleanError("Cleaning needs the shapely package.")
    say = on_progress or (lambda _line: None)

    gathered = G.gather(list(paths))
    if not gathered:
        raise CleanError("Nothing readable in that — check the path.")
    files = G.classify(gathered)

    face, source = outline_for(files, say)
    if face is None:
        raise CleanError(
            "No board outline found in this job, so there is nothing to "
            "clean against. Ask the customer for the outline layer "
            "(.GKO / .GM1 / profile).")
    inside = face.buffer(margin_mm)
    x0, y0, x1, y1 = face.bounds

    os.makedirs(out_dir, exist_ok=True)
    report = {
        "out_dir": out_dir,
        "outline": {"width_mm": round(x1 - x0, 2),
                    "height_mm": round(y1 - y0, 2), "source": source},
        "margin_mm": margin_mm,
        "layers": [],
        "copied": [],
        "warnings": [],
        "previews": [],
    }
    used_names: set[str] = set()
    os.makedirs(os.path.join(out_dir, PREVIEW_DIR), exist_ok=True)

    for entry in files:
        name = _unique_name(entry["name"], used_names)
        out_path = os.path.join(out_dir, name)
        if entry["kind"] != "gerber" or entry["role"] not in CLEAN_ROLES:
            shutil.copy2(entry["path"], out_path)
            report["copied"].append({"name": name, "role": entry["role"]})
            continue

        say(f"cleaning {entry['name']}")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                layer = GerberFile.open(entry["path"])
        except Exception as e:                          # noqa: BLE001
            shutil.copy2(entry["path"], out_path)
            report["copied"].append({"name": name, "role": entry["role"]})
            report["warnings"].append(
                f"{entry['name']}: could not be read for cleaning ({e}); "
                f"copied through unchanged.")
            continue

        kept, removed, crossing = [], [], []
        extents = []
        for obj in layer.objects:
            verdict, where = _place(obj, face, inside)
            extents.append(where)
            if verdict == "outside":
                removed.append((obj, where))
            elif verdict == "crossing":
                crossing.append((obj, where))
                kept.append(obj)
            else:
                kept.append(obj)

        total = len(layer.objects)
        on_board = _on_board_share(extents, face)
        removed_area = _area_share([w for _, w in removed], extents)
        result = {
            "name": name, "role": entry["role"], "label": entry.get("label", ""),
            "objects": total, "kept": len(kept), "removed": len(removed),
            "crossing": len(crossing),
            "removed_list": [_describe(o, w) for o, w in removed[:200]],
            "crossing_list": [_describe(o, w) for o, w in crossing[:200]],
            "suspicious": False,
        }
        if total and (not kept or on_board < MIN_ON_BOARD_SHARE):
            reason = (f"only {on_board:.0%} of this layer sits over the "
                      "outline. That is an origin mismatch between the layer "
                      "and the outline, not junk")
        elif total and removed_area > MAX_REMOVED_AREA_SHARE:
            reason = (f"{removed_area:.0%} of this layer's copper BY AREA "
                      f"lies outside the outline ({len(removed)} objects, "
                      f"{len(crossing)} crossing the edge). That much is not "
                      "junk — the outline is wrong for this layer")
        else:
            reason = ""
        if reason:
            # Not a cleaning job. Refuse rather than destroy the layer, and
            # say so loudly.
            result.update(kept=total, removed=0, crossing=0, suspicious=True,
                          removed_list=[], crossing_list=[])
            shutil.copy2(entry["path"], out_path)
            report["warnings"].append(
                f"{entry['name']}: {reason} — NOTHING was removed from it. "
                "Check the outline layer with the customer.")
        else:
            before = _svg(layer)
            layer.objects = kept
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                layer.save(out_path)
            preview = _write_preview(out_dir, name, before, _svg(layer))
            if preview:
                result["preview"] = preview
                report["previews"].append(preview)
        report["layers"].append(result)

    _write_reports(report)
    _write_compare(report)
    say("done")
    return report


# ── where an object sits ──────────────────────────────────────────────────────

def _place(obj, face, inside) -> tuple[str, tuple]:
    """"inside" / "outside" / "crossing", plus the object's extent in mm.

    The test is on the object's bounding box — exact for pads and axis-
    aligned tracks, and conservative for everything else: a diagonal track
    whose box crosses the edge while the track itself does not is reported
    as crossing, which keeps it. Nothing is ever removed that might touch
    the board."""
    # gerbonara answers ((min x, min y), (max x, max y)).
    (bx0, by0), (bx1, by1) = obj.bounding_box(unit="mm")
    bb = _box(bx0, by0, bx1, by1)
    extent = (round(bx0, 2), round(by0, 2), round(bx1, 2), round(by1, 2))
    if not inside.intersects(bb):
        return "outside", extent
    if inside.contains(bb):
        return "inside", extent
    return "crossing", extent


def _on_board_share(extents: list[tuple], face) -> float:
    """How much of the layer's own extent lies over the board, 0..1.

    A layer drawn to the board's origin covers most of it even with a
    legend alongside; a layer drawn to some other origin covers none of
    it. That, not an object count, is what tells the two apart."""
    if not extents:
        return 1.0
    x0 = min(e[0] for e in extents)
    y0 = min(e[1] for e in extents)
    x1 = max(e[2] for e in extents)
    y1 = max(e[3] for e in extents)
    if x1 <= x0 or y1 <= y0:
        return 1.0
    extent = _box(x0, y0, x1, y1)
    return extent.intersection(face.envelope).area / extent.area


def _area_share(removed: list[tuple], all_extents: list[tuple]) -> float:
    """The removed objects' share of the layer's drawn area, 0..1, by
    bounding box — coarse, and exactly the right coarseness: a legend of
    hairline strokes is nothing by area, a board's worth of tracks and
    pads is most of it."""
    def area(e):
        return max(e[2] - e[0], 1e-3) * max(e[3] - e[1], 1e-3)
    total = sum(area(e) for e in all_extents)
    if total <= 0:
        return 0.0
    return sum(area(e) for e in removed) / total


def _describe(obj, where) -> dict:
    x0, y0, x1, y1 = where
    return {"type": type(obj).__name__, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _unique_name(name: str, used: set[str]) -> str:
    """Jobs are flat folders; on the rare one that is not, two files with
    the same name from two sub-folders must not overwrite each other."""
    if name not in used:
        used.add(name)
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while f"{stem} ({n}){ext}" in used:
        n += 1
    out = f"{stem} ({n}){ext}"
    used.add(out)
    return out


# ── pictures to compare by eye ────────────────────────────────────────────────

def _svg(layer) -> str:
    """The layer as gerbonara draws it, or "" — a preview is a courtesy,
    never a reason for the cleaning to fail."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return str(layer.to_svg())
    except Exception:                                   # noqa: BLE001
        return ""


def _write_preview(out_dir: str, name: str, before: str, after: str):
    if not before or not after:
        return None
    paths = {}
    for tag, svg in (("before", before), ("after", after)):
        # The full name, extension included: board.gtl and board.gbs are
        # two layers, and "board before.svg" would be one picture.
        rel = os.path.join(PREVIEW_DIR, f"{name} {tag}.svg")
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as f:
            f.write(svg)
        paths[tag] = rel
    return {"name": name, **paths}


def _write_compare(report: dict) -> None:
    """One page, every cleaned layer before and after, side by side — the
    thing to open with the graphics team."""
    if not report["previews"]:
        return
    o = report["outline"]
    rows = []
    for layer in report["layers"]:
        p = layer.get("preview")
        if not p:
            continue
        rows.append(
            f"<h2>{_esc(layer['name'])}</h2>"
            f"<p>{layer['objects']} objects &middot; {layer['kept']} kept "
            f"&middot; {layer['removed']} removed &middot; "
            f"{layer['crossing']} crossing the edge (kept)</p>"
            f"<div class=pair><figure><figcaption>Before</figcaption>"
            f"<img src=\"{_esc(p['before'])}\"></figure>"
            f"<figure><figcaption>After</figcaption>"
            f"<img src=\"{_esc(p['after'])}\"></figure></div>")
    html = (
        "<!doctype html><meta charset=utf-8>"
        "<title>Cleaned outside the border</title>"
        "<style>body{font:15px/1.4 -apple-system,Segoe UI,sans-serif;"
        "margin:24px;color:#222}h2{margin:32px 0 4px}p{margin:0 0 8px;"
        "color:#555}.pair{display:flex;gap:16px}figure{margin:0;flex:1;"
        "min-width:0}figcaption{font-weight:600;margin-bottom:4px}"
        "img{width:100%;height:auto;border:1px solid #ccc;background:#fff}"
        "</style>"
        f"<h1>Cleaned outside the border</h1>"
        f"<p>Board outline {o['width_mm']} &times; {o['height_mm']} mm "
        f"(from the {_esc(o['source'])}). Everything wholly outside it was "
        f"removed; anything crossing the edge was kept and is listed in "
        f"{REPORT_TXT}.</p>" + "".join(rows))
    with open(os.path.join(report["out_dir"], COMPARE_HTML), "w",
              encoding="utf-8") as f:
        f.write(html)
    report["compare_html"] = os.path.join(report["out_dir"], COMPARE_HTML)


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── the report ────────────────────────────────────────────────────────────────

def report_text(report: dict) -> str:
    o = report["outline"]
    lines = [
        "CLEANED OUTSIDE THE BOARD OUTLINE",
        "",
        f"  Board outline      {o['width_mm']} x {o['height_mm']} mm  "
        f"(from the {o['source']})",
        f"  Edge margin        {report['margin_mm']} mm counts as inside",
        "",
        "  Removed = lay wholly outside the outline.  Crossing = touches the "
        "edge; KEPT unchanged and listed for you to decide.",
        "",
        f"  {'LAYER':<34} {'OBJECTS':>8} {'KEPT':>8} {'REMOVED':>8} {'CROSSING':>9}",
    ]
    for layer in report["layers"]:
        flag = "   <- CHECK: nothing removed, see warning" if layer["suspicious"] else ""
        lines.append(f"  {layer['name'][:34]:<34} {layer['objects']:>8} "
                     f"{layer['kept']:>8} {layer['removed']:>8} "
                     f"{layer['crossing']:>9}{flag}")
    if report["copied"]:
        lines += ["", "  Copied through unchanged (not image layers):"]
        for item in report["copied"]:
            lines.append(f"    {item['name']}   ({item['role']})")
    for layer in report["layers"]:
        if layer["removed_list"]:
            lines += ["", f"  {layer['name']} — removed:"]
            for d in layer["removed_list"]:
                lines.append(f"    {d['type']:<8} x {d['x0']}..{d['x1']}  "
                             f"y {d['y0']}..{d['y1']}")
        if layer["crossing_list"]:
            lines += ["", f"  {layer['name']} — crossing the edge (kept):"]
            for d in layer["crossing_list"]:
                lines.append(f"    {d['type']:<8} x {d['x0']}..{d['x1']}  "
                             f"y {d['y0']}..{d['y1']}")
    if report["warnings"]:
        lines += ["", "  WARNINGS"]
        for w in report["warnings"]:
            lines.append(f"    ! {w}")
    return "\n".join(lines)


def _write_reports(report: dict) -> None:
    out_dir = report["out_dir"]
    with open(os.path.join(out_dir, REPORT_TXT), "w", encoding="utf-8") as f:
        f.write(report_text(report) + "\n")
    with open(os.path.join(out_dir, REPORT_CSV), "w", encoding="utf-8-sig",
              newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Layer", "Role", "Objects", "Kept", "Removed",
                         "Crossing (kept)", "Check"])
        for layer in report["layers"]:
            writer.writerow([layer["name"], layer["role"], layer["objects"],
                             layer["kept"], layer["removed"], layer["crossing"],
                             "origin mismatch — nothing removed"
                             if layer["suspicious"] else ""])
    report["report_txt"] = os.path.join(out_dir, REPORT_TXT)
    report["report_csv"] = os.path.join(out_dir, REPORT_CSV)
