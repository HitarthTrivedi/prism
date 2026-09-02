"""Prism — fill the client's own quotation form from a measured Gerber job.

The client already has a form. Fine Circuits (Manjusar GIDC) hands their
estimator F-SAL-01 — an Excel quotation sheet with a cell for every figure:
Board X/Y, Array X/Y, Pcs/Array, Min Line, Min Space, Smallest Hole, Pitch,
SMT pad, a drill table summed by the form's own =SUM() — and what they want
from Prism is not a new report format, it is THEIR form with the measured
numbers already in it.

How filling works — labels, not coordinates. The template is scanned for
cells whose text matches a known label ("Board X", "Min Line", "Smallest
Hole", "Pcs / Array"…) and the measured value is written into the cell
immediately to the label's right — which is how these forms are invariably
laid out. Coordinates would pin Prism to one revision of one client's file;
labels survive the client inserting a row, and they make the same code fill
the NEXT client's differently-drawn form too.

Two rules keep the client's form theirs:

  · A cell that holds a formula is never overwritten. The form's own
    arithmetic (=SUM(B30:B51), =B30*$B$13) keeps working on the values
    Prism puts in — their totals stay THEIR totals.
  · A label Prism has no measurement for is left exactly as drawn. An
    empty cell on their form is a question for their estimator, not a
    place for a guess.

The drill table is the one structural fill: the row below "HOLE SIZE" gets
one row per measured tool — diameter in mm, hits per array — and any
leftover pre-printed placeholder rows are zeroed so the form's SUM counts
only real drills.

Figures are written to 2 decimal places, same as every Gerber surface.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

try:
    import openpyxl
    HAVE_XLSX = True
except Exception:                                   # pragma: no cover
    HAVE_XLSX = False


class FormError(Exception):
    """A sentence for the user, not a stack trace."""


def _round2(v):
    return round(float(v), 2)


def _smt_lw(answers: dict) -> tuple:
    """(length, width) of the smallest SMT pad, longest side first."""
    text = answers.get("min_smt_pad") or ""
    m = re.match(r"([\d.]+)\s*x\s*([\d.]+)", text)
    if not m:
        return None, None
    a, b = float(m.group(1)), float(m.group(2))
    return max(a, b), min(a, b)


def _size(answers: dict, key: str, idx: int):
    pair = answers.get(key)
    return _round2(pair[idx]) if pair and pair[idx] else None


# (label regex, getter(answers, meta) -> value or None). Matched against the
# cell text lowercased with trailing ':'/'-' stripped, so "CUSTOMER:-" and
# "Board X" both read naturally. First match wins; order the specific
# before the generic.
_LABELS = [
    (r"^no\.?\s*layers?$|^layers$|^no\s+layer$",
     lambda a, m: a.get("layers") or None),
    (r"^board\s*x$", lambda a, m: _size(a, "pcb_size_mm", 0)),
    (r"^board\s*y$", lambda a, m: _size(a, "pcb_size_mm", 1)),
    (r"^array\s*x$", lambda a, m: _size(a, "array_size_mm", 0)),
    (r"^array\s*y$", lambda a, m: _size(a, "array_size_mm", 1)),
    (r"^pcs\s*/\s*array$|^pcbs?\s*(in|per)\s*(the\s*)?array$",
     lambda a, m: a.get("pcbs_per_array") or None),
    (r"^min\.?\s*line$|^min\.?\s*track(\s*width)?$",
     lambda a, m: _round2(a["min_track_width_mm"])
     if a.get("min_track_width_mm") else None),
    (r"^min\.?\s*space$|^min\.?\s*(track\s*)?spacing$",
     lambda a, m: _round2(a["min_track_spacing_mm"])
     if a.get("min_track_spacing_mm") else None),
    (r"^smallest\s*hole$|^min\.?\s*drill(\s*size)?$",
     lambda a, m: _round2(a["min_drill_mm"])
     if a.get("min_drill_mm") else None),
    (r"^(min\.?\s*)?pitch$",
     lambda a, m: _round2(a["min_pitch_mm"])
     if a.get("min_pitch_mm") else None),
    (r"^min\.?\s*smt\s*length$", lambda a, m: _smt_lw(a)[0]),
    (r"^min\.?\s*smt\s*width$", lambda a, m: _smt_lw(a)[1]),
    (r"^customer$", lambda a, m: m.get("customer") or None),
    (r"^part\s*no\.?$", lambda a, m: m.get("part") or None),
    (r"^date$", lambda a, m: m.get("date")
     or _dt.date.today().strftime("%d-%m-%Y")),
]
_COMPILED = [(re.compile(rx, re.IGNORECASE), get) for rx, get in _LABELS]

_DRILL_HEADER = re.compile(r"hole\s*size", re.IGNORECASE)


def _label_text(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip(":-").strip().lower()


def _is_formula(value) -> bool:
    return isinstance(value, str) and value.startswith("=")


def _fill_drill_table(ws, job: dict, filled: list) -> int:
    """One row per measured tool under 'HOLE SIZE'; leftover pre-printed
    placeholder rows zeroed so the form's own SUM counts only real drills.
    Stops at the first row that mentions TOTAL — that row is the form's."""
    header = next((c for row in ws.iter_rows() for c in row
                   if isinstance(c.value, str)
                   and _DRILL_HEADER.search(c.value)), None)
    if header is None:
        return 0
    tools = sorted((t for t in (job.get("drills") or {}).get("tools", [])
                    if t.get("hits")), key=lambda t: t["dia_mm"])
    col, count_col = header.column, header.column + 1
    row = header.row + 1
    written = 0
    while row <= ws.max_row:
        size_cell = ws.cell(row=row, column=col)
        count_cell = ws.cell(row=row, column=count_col)
        if isinstance(size_cell.value, str) and "total" in size_cell.value.lower():
            break
        if written < len(tools):
            t = tools[written]
            if not _is_formula(size_cell.value):
                size_cell.value = _round2(t["dia_mm"])
            if not _is_formula(count_cell.value):
                count_cell.value = t["hits"]
            filled.append((size_cell.coordinate, "drill",
                           f"Ø{_round2(t['dia_mm']):g} x {t['hits']}"))
            written += 1
        elif size_cell.value is not None or count_cell.value:
            # A pre-printed placeholder past the measured list — zero it so
            # the form's SUM row adds up to the real drill count.
            if not _is_formula(size_cell.value):
                size_cell.value = None
            if not _is_formula(count_cell.value):
                count_cell.value = 0
        row += 1
    return written


def fill_form(job: dict, template_path: str, out_path: str,
              meta: dict | None = None) -> dict:
    """Write a filled COPY of the client's form; the template is never
    touched. Returns {"out", "filled": [(cell, label, value)…],
    "drill_rows": n}."""
    if not HAVE_XLSX:
        raise FormError("Filling an Excel form needs the openpyxl package.")
    template_path = os.path.abspath(os.path.expanduser(template_path))
    if not os.path.exists(template_path):
        raise FormError(f"No such template: {template_path}")

    wb = openpyxl.load_workbook(template_path)
    answers = job.get("answers") or {}
    meta = meta or {}
    filled: list = []
    drills = 0

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                text = _label_text(cell.value)
                if not text:
                    continue
                for rx, get in _COMPILED:
                    if not rx.match(text):
                        continue
                    try:
                        value = get(answers, meta)
                    except Exception:               # noqa: BLE001
                        value = None
                    if value is None:
                        break   # measured nothing — leave their form as drawn
                    target = ws.cell(row=cell.row, column=cell.column + 1)
                    if _is_formula(target.value):
                        break   # their arithmetic, never ours
                    target.value = value
                    filled.append((target.coordinate, cell.value.strip(),
                                   value))
                    break
        drills += _fill_drill_table(ws, job, filled)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    wb.save(out_path)
    return {"out": out_path, "filled": filled, "drill_rows": drills}
