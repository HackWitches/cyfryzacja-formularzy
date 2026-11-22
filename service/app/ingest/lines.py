"""Numbered text lines with boxes: the cheap deterministic "regions" the model cites by number.

Each line carries the paper's fill-in places as tokens: "……" a dotted line, "[___]" a box, "[#N]" a row of N
character cells, "☐" a checkbox, " | " between the cells of a table row.
Usage: python -m app.ingest.lines plik.pdf   -> prints the text that goes to the model
"""
import re
import sys

DOTS = re.compile(r"[.…_]{4,}")
# Checkboxes come as many glyphs: ❑ □ ▢ ◻ (forms 7, 9) and private-use Wingdings/Symbol codes (U+F0F0 in form 3,
# U+F092 in form 9) that mean nothing to a model. One symbol for an empty box, one for a ticked box.
# ponytail: every private-use glyph counts as an empty box; add a per-font table if one turns out to be something else
EMPTY_BOX = re.compile("[\u2751\u25a1\u25a2\u25fb\u2610\u274f\u2750\ue000-\uf8ff]")
TICKED_BOX = re.compile("[\u2612\u2611\u25a0\u2713\u2714\u2716]")


def line_id(n: int) -> str:
    """L001, L002, ... L999, L1000: the ids the model cites in `linie`."""
    return f"L{n:03d}"


def numbered(n: int, page_no: int, text: str, bbox: list) -> dict:
    return {"id": line_id(n), "page": page_no, "text": text, "bbox": bbox}


def clean(text: str) -> str:
    """Fill-in marks of a line in one spelling: dots -> "……", any box glyph -> ☐ / ☒."""
    text = DOTS.sub("……", text)
    text = TICKED_BOX.sub("☒", EMPTY_BOX.sub("☐", text))
    return re.sub(r"☐(?=\S)", "☐ ", text).strip()


def white(color):
    """pdfplumber colours: gray (g,), RGB (r, g, b) or CMYK (c, m, y, k); None means default black."""
    if color is None:
        return False
    c = color if isinstance(color, (list, tuple)) else (color,)
    if not all(isinstance(v, (int, float)) for v in c):
        return True  # pattern fill (a name, not numbers): unknown, do not treat as shading
    if len(c) == 4:
        return all(v <= 0.05 for v in c)
    return all(v >= 0.95 for v in c)


def marks(page, cells):
    """Empty boxes on the page as (x0, top, x1, bottom, token).

    Boxes come from vector rectangles and from table cells (Word tables are drawn as thin bars, form 2).
    Tokens: ☐ checkbox, [#N] row of N character cells (PESEL 11, NIP 10, account 26), [___] one-line box,
    [___↵] tall box for longer text. Boxes with text inside (labels, boxed letters) and outer frames are skipped.
    """
    cands = list(cells) + [(r["x0"], r["top"], r["x1"], r["bottom"]) for r in page.rects if min(r["width"], r["height"]) >= 4]
    centers = [((c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2) for c in page.chars
               if c["text"].isalnum()]  # stray punctuation inside a box (form 2: "`") does not make it a label
    area = lambda b: (b[2] - b[0]) * (b[3] - b[1])  # noqa: E731
    shaded = [(r["x0"], r["top"], r["x1"], r["bottom"]) for r in page.rects
              if r.get("fill") and not white(r.get("non_stroking_color")) and min(r["width"], r["height"]) >= 4]

    def is_shaded(b):  # grey stripes, "=" columns, label backgrounds (form 8): decoration, not a place to write
        return any(max(0, min(b[2], s[2]) - max(b[0], s[0])) * max(0, min(b[3], s[3]) - max(b[1], s[1]))
                   >= 0.8 * area(b) for s in shaded)

    boxes = []
    for b in sorted(cands, key=area):
        w, h = b[2] - b[0], b[3] - b[1]
        if not (4 <= w <= page.width * 0.9 and 4 <= h <= page.height * 0.5) or \
                any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for x, y in centers) or is_shaded(b):
            continue
        if any(all(abs(u - v) <= 1.5 for u, v in zip(b, o)) for o in boxes):
            continue  # the same box drawn twice (rect + table cell, 0.1 pt apart)
        if any(b[0] <= o[0] + 1 and o[2] <= b[2] + 1 and b[1] <= o[1] + 1 and o[3] <= b[3] + 1 for o in boxes):
            continue  # outer frame around smaller boxes: keep the innermost
        boxes.append(b)
    small = [b for b in boxes if b[2] - b[0] <= 30 and b[3] - b[1] <= 30]
    out, rows = [], []
    for b in sorted(small, key=lambda b: b[1]):  # rows by top, then cells left to right
        if rows and abs(rows[-1][0][1] - b[1]) <= 2:
            rows[-1].append(b)
        else:
            rows.append([b])
    for row in rows:
        run = []
        for b in sorted(row, key=lambda b: b[0]) + [None]:
            if run and (b is None or not -1 <= b[0] - run[-1][2] <= 4 or abs((b[3] - b[1]) - (run[-1][3] - run[-1][1])) > 2):
                r0, r1 = run[0], run[-1]
                w, h = r0[2] - r0[0], r0[3] - r0[1]
                if len(run) > 1:
                    out.append((r0[0], min(x[1] for x in run), r1[2], max(x[3] for x in run), f"[#{len(run)}]"))
                elif 0.6 <= w / h <= 1.6:  # a lone square-ish box is a checkbox (or a one-word box if wide)
                    out.append((*r0, "☐" if w <= 18 else "[___]"))
                run = []  # ponytail: a lone thin cell is taken for a table spacer and dropped, so a lone narrow
                # write-in cell is lost too; tell them apart by the label next to the cell if a form needs it
            if b is not None:
                run.append(b)
    out += [(*b, "[___]" if b[3] - b[1] <= 30 else "[___↵]") for b in boxes
            if b not in small and b[2] - b[0] >= 30]  # ponytail: boxes under 30 pt wide and over 30 pt tall count
    # as layout stripes, so a narrow vertical write-in box is not marked; lower the width limit if forms use them
    return out


TIGHT = {"snap_tolerance": 1, "join_tolerance": 1, "intersection_tolerance": 1}  # thick separators sit 3 pt away


def table_cells(page):
    return [tuple(c) for c in page.debug_tablefinder(TIGHT).cells]  # all cells, also single-cell "tables"


def center_in(w, box):
    return box[0] <= (w[0] + w[2]) / 2 <= box[2] and box[1] <= (w[1] + w[3]) / 2 <= box[3]


def table_lines(page, items):
    """Tables with text read row by row: one line per row, cells joined by " | " (form 8 positions 36-50:
    "36. Liczba gospodarstw ... [#4] | X | 37. Stawka ... | = | 38. Wysokość ... [#4] zł").
    Words and marks go to the cell their centre is in. Returns (lines, items left for normal reading)."""
    text = [w for w in items if any(ch.isalnum() for ch in w[4])]
    out, used = [], set()
    for t in page.find_tables(TIGHT):
        cells_with_text = [sum(1 for c in r.cells if c and any(center_in(w, c) for w in text)) for r in t.rows]
        if sum(1 for n in cells_with_text if n) < 2 or max(cells_with_text) < 2:
            continue  # rows of character boxes (NIP, postcode) or a lone frame: not a table to read
        for r in t.rows:
            parts = []
            for c in r.cells:
                ws = [w for w in items if c and id(w) not in used and center_in(w, c)]
                used.update(id(w) for w in ws)
                if ws:
                    parts.append(" ".join(w[4] for row in reflow(rows_of(ws)) for w in row))
            if parts:
                out.append((clean(" | ".join(parts)), list(r.bbox)))
    return out, [w for w in items if id(w) not in used]


def rows_of(words):
    rows = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if rows and abs(rows[-1][0][1] - w[1]) <= 3:  # same baseline band
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w[0]) for r in rows]


def reflow(rows):
    """Two prose columns side by side (form 8 "Okoliczności": options 1 and 2 in two columns) are glued into one
    line by y. Find the x where rows split cleanly and, for a run of such rows with real text (4+ words) on both
    sides in at least two rows, print the left column first, then the right one.
    Label | box rows (form 2) and option rows with checkboxes have no prose on one side, so they stay as they are.
    ponytail: one split per run; three prose columns would need a recursive split."""
    def prose(ws):
        return sum(1 for w in ws if any(ch.isalpha() for ch in w[4])) >= 4

    gaps = [round((a[2] + b[0]) / 20) * 10 for r in rows for a, b in zip(r, r[1:]) if b[0] - a[2] > 12]
    out, i = [], 0
    while i < len(rows):
        best = None
        for split in set(gaps):
            j = i
            while j < len(rows) and not any(w[0] < split < w[2] for w in rows[j]):
                j += 1
            run = rows[i:j]
            both = sum(1 for r in run if prose([w for w in r if w[2] <= split]) and prose([w for w in r if w[0] >= split]))
            if both >= 2 and (best is None or j - i > best[1] - i):
                best = (split, j)
        if not best:
            out.append(rows[i])
            i += 1
            continue
        split, j = best
        run = rows[i:j]
        out += [l for l in ([w for w in r if w[2] <= split] for r in run) if l]
        out += [r_ for r_ in ([w for w in r if w[0] >= split] for r in run) if r_]
        i = j
    return out


def page_lines(page):
    """Text rows of the page with empty boxes inserted as words, tables read row by row, prose columns reflowed."""
    words = [(w["x0"], w["top"], w["x1"], w["bottom"], w["text"]) for w in page.extract_words()]
    mk = marks(page, table_cells(page))
    tables, rest = table_lines(page, words + mk)
    rows = rows_of([w for w in rest if w not in mk])
    for m in (m for m in mk if m in rest):
        mid = (m[1] + m[3]) / 2
        row = next((r for r in rows if min(w[1] for w in r) - 3 <= mid <= max(w[3] for w in r) + 3), None)
        if row is None:
            rows.append([m])
        else:
            row.append(m)
    rows = [sorted(r, key=lambda w: w[0]) for r in sorted(rows, key=lambda r: min(w[1] for w in r))]
    free = [(clean(" ".join(w[4] for w in r)), [min(w[0] for w in r), min(w[1] for w in r),
                                                 max(w[2] for w in r), max(w[3] for w in r)]) for r in reflow(rows)]
    return sorted(free + tables, key=lambda ln: ln[1][1])  # stable: reflowed order kept within equal tops


def lines(pdf_path) -> list[dict]:
    """Every text line of the PDF as {id, page, text, bbox}, numbered through the whole document."""
    import pdfplumber

    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            for text, box in page_lines(page):
                if text:
                    out.append(numbered(len(out) + 1, page_no, text, [round(v, 1) for v in box]))
    return out


def as_prompt(items: list[dict]) -> str:
    """The numbered lines as the text the model reads, with a separator before each page."""
    text, page = [], 0
    for ln in items:
        if ln["page"] != page:
            page = ln["page"]
            text.append(f"--- strona {page} ---")
        text.append(f"{ln['id']} {ln['text']}")
    return "\n".join(text)


if __name__ == "__main__":
    print(as_prompt(lines(sys.argv[1])))
