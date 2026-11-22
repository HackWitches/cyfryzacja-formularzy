"""Dev-only: draw what the REST API made of a paper form, on top of the paper.

Usage: .venv/bin/python dev/visualize_output.py [01 02 ...] [--in temp] [--out temp/viz]
Input per form NN (in --in): NN.json (contract v2 from the LLM), optional NN.debug.json ({do_weryfikacji, bledy, linie}).
Source PDF: annotations/forms/NN_*.json key "source". Line ids (L001...) come from service/app/ingest/lines.py.
Output (in --out): NN_wynik.pdf (all pages + summary), NN_pageK.png, NN_podsumowanie.png.

Encoding: field box = union of its cited lines (one box per run of consecutive ids), colour = e-service page (strona);
tag "#n TYP*" in the right gutter (* = wymagane, amber "?" = has a warunek); light filled box = section, labelled in
the left gutter; grey dashed box = pominiete with its reason. Side panel lists the fields of the paper page.
TODO: forms that are images/scans (the API OCRs them); only PDF sources are drawn here.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pdfplumber
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True  # do not leave __pycache__ in service/
sys.path.insert(0, str(ROOT / "service"))
from app.ingest.lines import lines  # noqa: E402

DPI = 140
S = DPI / 72  # PDF points -> pixels
LEFT_W, RIGHT_W, PANEL_W = 190, 360, 940
SEC_GAP = 40  # px: field boxes of one section further apart than this get separate section boxes  # gutters around the page image, side panel
STRONA_COLORS = [(31, 119, 180), (214, 39, 40), (44, 160, 44), (148, 60, 190), (230, 120, 20), (23, 150, 160),
                 (140, 86, 75)]
SKIP_COLOR, COND_COLOR, ERR_COLOR, GREY = (125, 125, 125), (240, 170, 0), (200, 0, 0), (95, 95, 95)
CHOICE = {"RADIO", "CHECKBOX", "SELECT"}


def font(size, bold=False):
    for name in (("Arial Bold.ttf",) if bold else ()) + ("Arial.ttf", "Arial Unicode.ttf"):
        try:
            return ImageFont.truetype(f"/System/Library/Fonts/Supplemental/{name}", size)
        except OSError:
            pass
    return ImageFont.load_default(size)  # ponytail: macOS fonts; Polish letters need a real TTF elsewhere


F_TAG, F_SEC, F_TXT, F_SMALL, F_BOLD, F_H, F_BIG = (font(14, True), font(13, True), font(16), font(14),
                                                     font(16, True), font(20, True), font(28, True))


def s(v):
    """Contract text for drawing: anything to str, no em dash."""
    if isinstance(v, dict):
        v = v.get("nazwa") or v.get("tytul") or v.get("label") or v.get("wartosc") or json.dumps(v, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(v if v is not None else "")).replace("\u2014", "-").strip()


def short(v, n=42):
    v = s(v)
    return v if len(v) <= n else v[:n - 1].rstrip() + "…"


def pola(n):
    """Polish plural: 1 pole, 2 pola, 5 pól, 22 pola."""
    return f"{n} " + ("pole" if n == 1 else "pola" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "pól")


def lnum(i):
    m = re.fullmatch(r"L(\d+)", str(i))
    return int(m.group(1)) if m else None


def id_ranges(ids):
    """['L001', 'L002', 'L005'] -> 'L001-L002, L005'"""
    nums = sorted({n for n in map(lnum, ids) if n is not None})
    out, start = [], None
    for a, b in zip(nums, nums[1:] + [None]):
        start = a if start is None else start
        if b != a + 1:
            out.append(f"L{start:03d}" + (f"-L{a:03d}" if a != start else ""))
            start = None
    return ", ".join(out + [str(i) for i in ids if lnum(i) is None])


# ---------- contract ----------

def fields_of(c):
    """Flat list of fields with their running number, e-service page and section."""
    out = []
    for si, st in enumerate(c.get("strony") or [], 1):
        for sec in (st or {}).get("sekcje") or []:
            for f in (sec or {}).get("pola") or []:
                if isinstance(f, dict):
                    out.append({"n": len(out) + 1, "si": si, "strona": st, "sekcja": sec, "f": f})
    return out


def kind(f):
    """Label: the macro (OSOBA, ADRES) or the type; same defaults as service/app/domain/contract.py."""
    w = s(f.get("wzorzec") or "POLE").upper()
    return w if w != "POLE" else s(f.get("typ") or "TEXT_FIELD").upper()


def req(f):
    return f.get("wymagane") is not False  # contract.py: unknown = required


def cond_text(c, by_tech):
    """Readable condition + list of problems. by_tech: techName -> field entry."""
    probs = []

    def go(c, top):
        if not isinstance(c, dict):
            return s(c)
        if "pole" in c:
            t, val = s(c["pole"]), s(c.get("wartosc", c.get("rowna", "")))
            e = by_tech.get(t)
            if not e:
                probs.append(f"warunek wskazuje nieistniejące pole {t}")
                return f"{t} = „{val}”"
            typ, opts = kind(e["f"]), [s(o) for o in e["f"].get("opcje") or []]
            if typ not in CHOICE:
                probs.append(f"warunek na polu #{e['n']} {t} typu {typ} (nie pole wyboru)")
            elif opts and val not in opts:
                probs.append(f"pole #{e['n']} {t} nie ma opcji „{val}”")
            return f"#{e['n']} {t} = „{val}”"
        if "nie" in c:
            return f"nie ({go(c['nie'], True)})"
        for op in ("i", "lub"):
            if isinstance(c.get(op), list):
                txt = f" {op} ".join(go(x, False) for x in c[op])
                return txt if top else f"({txt})"
        return json.dumps(c, ensure_ascii=False)

    return go(c, True), probs


def selftest():
    by = {"rodzaj": {"n": 1, "f": {"typ": "RADIO", "opcje": ["A", "Inne"]}},
          "opis": {"n": 2, "f": {"typ": "TEXT_FIELD"}}}
    assert cond_text({"pole": "rodzaj", "wartosc": "Inne"}, by) == ("#1 rodzaj = „Inne”", [])
    t, p = cond_text({"i": [{"pole": "rodzaj", "wartosc": "A"}, {"nie": {"lub": [
        {"pole": "opis", "wartosc": "x"}, {"pole": "brak", "wartosc": "y"}]}}]}, by)
    assert t == "#1 rodzaj = „A” i nie (#2 opis = „x” lub brak = „y”)", t
    assert len(p) == 2 and "TEXT_FIELD" in p[0] and "nieistniejące" in p[1], p
    assert cond_text({"pole": "rodzaj", "wartosc": "Z"}, by)[1] == ["pole #1 rodzaj nie ma opcji „Z”"]
    assert [pola(n) for n in (1, 3, 5, 12, 22)] == ["1 pole", "3 pola", "5 pól", "12 pól", "22 pola"]
    assert id_ranges(["L003", "L001", "L002", "L007"]) == "L001-L003, L007"


# ---------- drawing helpers ----------

def runs(ids, by_id, page):
    """Boxes (px, in page-image coords) for runs of consecutive line ids on one paper page."""
    nums = sorted({lnum(i) for i in ids if lnum(i) is not None and by_id.get(f"L{lnum(i):03d}", {}).get("page") == page})
    groups = []
    for n in nums:
        if groups and n == groups[-1][-1] + 1:
            groups[-1].append(n)
        else:
            groups.append([n])
    out = []
    for g in groups:
        bs = [by_id[f"L{n:03d}"]["bbox"] for n in g]
        out.append(([f"L{n:03d}" for n in g], [min(b[0] for b in bs) * S, min(b[1] for b in bs) * S,
                                                max(b[2] for b in bs) * S, max(b[3] for b in bs) * S]))
    return out


def grow(b, p):
    return [b[0] - p, b[1] - p, b[2] + p, b[3] + p]


def shift(b, dx):
    return [b[0] + dx, b[1], b[2] + dx, b[3]]


def overlap(a, b):
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


def light(c, k=0.45):
    return tuple(int(v + (255 - v) * k) for v in c)


def dashed(d, b, color, width=2, dash=9):
    x0, y0, x1, y1 = b
    for a, e, horiz, fixed in ((x0, x1, True, y0), (x0, x1, True, y1), (y0, y1, False, x0), (y0, y1, False, x1)):
        for p in range(int(a), int(e), dash * 2):
            q = min(p + dash, e)
            d.line([(p, fixed), (q, fixed)] if horiz else [(fixed, p), (fixed, q)], fill=color, width=width)


class Placer:
    """Puts labels where they cover no paper text and no other label; tries candidates in order, cheapest wins."""

    def __init__(self, size, text_boxes):
        self.size, self.text, self.tags = size, text_boxes, []

    def put(self, cands):
        def cost(r):
            if r[0] < 0 or r[1] < 0 or r[2] > self.size[0] or r[3] > self.size[1]:
                return float("inf")
            return (sum(overlap(r, t) for t in self.text) + 1e6 * sum(overlap(r, t) for t in self.tags))
        best = min(enumerate(cands), key=lambda ic: (cost(ic[1]), ic[0]))[1]
        self.tags.append(best)
        return best


def tag_size(d, parts):
    return sum(d.textlength(t, font=F_TAG) + 10 for t, _, _ in parts), F_TAG.size + 8


def draw_tag(d, r, parts):
    x = r[0]
    for text, bg, fg in parts:
        w = d.textlength(text, font=F_TAG) + 10
        d.rectangle([x, r[1], x + w, r[3]], fill=bg)
        d.text((x + 5, r[1] + 3), text, fill=fg, font=F_TAG)
        x += w


def wrap(d, text, f, width):
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            cand = f"{line} {word}".strip()
            if line and d.textlength(cand, font=f) > width:
                out.append(line)
                line = word
            else:
                line = cand
            while d.textlength(line, font=f) > width and len(line) > 1:  # one very long word: hard split
                cut = len(line)
                while cut > 1 and d.textlength(line[:cut], font=f) > width:
                    cut -= 1
                out.append(line[:cut])
                line = line[cut:]
        out.append(line)
    return out


def text_block(rows, width, min_h=0, pad=28):
    """rows: (text, font, color, indent[, swatch]) -> image sized to fit (never cut)."""
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    laid, y = [], pad
    for row in rows:
        text, f, color, indent = row[:4]
        sw = row[4] if len(row) > 4 else None
        x = pad + indent + (52 if sw else 0)
        if text == "":
            y += f.size // 2
            continue
        for i, part in enumerate(wrap(probe, text, f, width - x - pad - 14)):
            laid.append((x + (14 if i else 0), y, part, f, color, sw if not i else None))
            y += int(f.size * 1.3)
        y += 2
    img = Image.new("RGB", (width, max(min_h, y + pad)), "white")
    d = ImageDraw.Draw(img)
    for x, y, part, f, color, sw in laid:
        if sw:
            c, style = sw
            b = [x - 52, y + 2, x - 12, y + f.size]
            if style == "fill":
                d.rectangle(b, fill=light(c, 0.85), outline=light(c), width=2)
            elif style == "dash":
                d.rectangle(b, fill=(235, 235, 235))
                dashed(d, b, c, 2, 5)
            elif style == "cond":
                d.rectangle([b[0], b[1], b[0] + 20, b[3]], fill=c)
                d.text((b[0] + 5, b[1] - 1), "?", fill="black", font=F_TAG)
            else:
                d.rectangle(b, outline=c, width=3)
        d.text((x, y), part, fill=color, font=f)
    return img


# ---------- one form ----------

class Form:
    def __init__(self, nn, indir):
        self.nn = nn
        self.c = json.loads((indir / f"{nn}.json").read_text())
        dbg = indir / f"{nn}.debug.json"
        self.debug = json.loads(dbg.read_text()) if dbg.exists() else {}
        ann = sorted((ROOT / "annotations" / "forms").glob(f"{nn}_*.json"))
        if not ann:
            raise SystemExit(f"{nn}: brak annotations/forms/{nn}_*.json ze ścieżką do PDF")
        self.source = ROOT / json.loads(ann[0].read_text())["source"]
        self.fields = fields_of(self.c)
        self.by_tech = {s(e["f"].get("techName")): e for e in self.fields}
        self.skips = [p for p in self.c.get("pominiete") or [] if isinstance(p, dict)]
        self.lines = lines(self.source)
        self.by_id = {ln["id"]: ln for ln in self.lines}
        self.warnings = self.check()

    def cond(self, c):
        return cond_text(c, self.by_tech) if c else (None, [])

    def check(self):
        """Contract problems that do not depend on a paper page."""
        w = []
        dbg = dict(re.findall(r"^(L\d+) (.*)$", self.debug.get("linie") or "", re.M))
        bad = [i for i, t in dbg.items() if i not in self.by_id or self.by_id[i]["text"] != t]
        if dbg and (bad or len(dbg) != len(self.lines)):
            w.append(f"(!) linie z lines.py różnią się od {self.nn}.debug.json ({len(bad)} różnych, {len(self.lines)} vs "
                     f"{len(dbg)}): ramki mogą być przesunięte, np. {', '.join(bad[:5])}")
        for e in self.fields:
            for p in self.cond(e["f"].get("warunek"))[1]:
                w.append(f"(!) #{e['n']} {s(e['f'].get('techName'))}: {p}")
        for st in self.c.get("strony") or []:
            for p in self.cond(st.get("warunek"))[1]:
                w.append(f"(!) strona „{s(st.get('tytul'))}”: {p}")
            for sec in st.get("sekcje") or []:
                for p in self.cond(sec.get("warunek"))[1]:
                    w.append(f"(!) sekcja {s(sec.get('techName'))}: {p}")
        cited = [i for e in self.fields for i in e["f"].get("linie") or []] + \
                [i for p in self.skips for i in p.get("linie") or []]
        unknown = sorted({str(i) for i in cited if str(i) not in self.by_id})
        if unknown:
            w.append(f"(!) nieznane numery linii: {', '.join(unknown)}")
        dup = {}
        for e in self.fields:
            dup.setdefault(s(e["f"].get("techName")), []).append(e["n"])
        for t, ns in dup.items():
            if len(ns) > 1:
                w.append(f"(!) powtórzone techName {t}: " + ", ".join(f"#{n}" for n in ns))
        return w

    # ----- page -----

    def page_image(self, page, no, n_pages):
        base = page.to_image(resolution=DPI).original.convert("RGB")
        W, H = LEFT_W + base.width + RIGHT_W, base.height
        img = Image.new("RGB", (W, H), "white")
        img.paste(base, (LEFT_W, 0))
        off = lambda b: shift(b, LEFT_W)  # noqa: E731
        text_boxes = [off([w["x0"] * S, w["top"] * S, w["x1"] * S, w["bottom"] * S]) for w in page.extract_words()]
        page_lines = [ln for ln in self.lines if ln["page"] == no]
        col_x = LEFT_W + max([ln["bbox"][2] * S for ln in page_lines] + [base.width * 0.6]) + 14

        line_px = [(ln["id"], off([v * S for v in ln["bbox"]])) for ln in page_lines]

        def pad(b, p, own):
            """Grow b by p, but vertically only up to half the gap to the next paper line above / below,
            so a border never strikes through a neighbouring line."""
            near = [l for i, l in line_px if i not in own and min(b[2], l[2]) > max(b[0], l[0])]
            up = min([b[1] - l[3] for l in near if l[3] <= b[1] + 1] + [2 * p + 2])
            dn = min([l[1] - b[3] for l in near if l[1] >= b[3] - 1] + [2 * p + 2])
            return [b[0] - p, b[1] - max(1, min(p, up / 2 - 1)), b[2] + p, b[3] + max(1, min(p, dn / 2 - 1))]

        # field boxes; fields sharing a paper line get nested (bigger) boxes so each stays visible
        used, fboxes = {}, []
        for e in self.fields:
            for ids, b in runs(e["f"].get("linie") or [], self.by_id, no):
                k = max(used.get(i, 0) for i in ids)
                for i in ids:
                    used[i] = used.get(i, 0) + 1
                fboxes.append((e, ids, pad(off(b), 3 + 4 * k, set(ids))))
        sboxes = []  # section = union of its field boxes on this page, split where they lie far apart
        for sec in {id(e["sekcja"]): e["sekcja"] for e, _, _ in fboxes}.values():
            si = next(e["si"] for e, _, _ in fboxes if e["sekcja"] is sec)
            groups = []  # [box, ids]
            for ids, b in sorted(((ids, b) for e, ids, b in fboxes if e["sekcja"] is sec), key=lambda t: t[1][1]):
                g = groups[-1] if groups else None
                if g and b[1] - g[0][3] < SEC_GAP:
                    g[0] = [min(g[0][0], b[0]), g[0][1], max(g[0][2], b[2]), max(g[0][3], b[3])]
                    g[1] |= set(ids)
                else:
                    groups.append([list(b), set(ids)])
            sboxes += [(sec, si, pad(g, 5, own), i == 0) for i, (g, own) in enumerate(groups)]
        skboxes = [(p, ids, grow(off(b), 2)) for p in self.skips
                   for ids, b in runs(p.get("linie") or [], self.by_id, no)]

        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        o = ImageDraw.Draw(ov)
        for _, _, b in skboxes:
            o.rectangle(b, fill=SKIP_COLOR + (38,))
        for _, si, b, _ in sboxes:
            o.rectangle(b, fill=self.color(si) + (22,))
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        d = ImageDraw.Draw(img)
        for _, _, b in skboxes:
            dashed(d, b, SKIP_COLOR, 2)
        for _, si, b, _ in sboxes:
            d.rectangle(b, outline=light(self.color(si)), width=2)
        for e, _, b in fboxes:
            d.rectangle(b, outline=self.color(e["si"]), width=3)

        d.line([(LEFT_W, 0), (LEFT_W, H)], fill=(225, 225, 225))  # paper edges, under the labels
        d.line([(LEFT_W + base.width, 0), (LEFT_W + base.width, H)], fill=(225, 225, 225))
        pl = Placer(img.size, text_boxes)
        leaders = []

        def right_tag(box, parts, color):
            w, h = tag_size(d, parts)
            x = max(col_x, box[2] + 8)
            ys = [box[1] + 3 * k for k in range(0, 200)] + [box[1] - 3 * k for k in range(1, 200)]
            r = pl.put([[x, y, x + w, y + h] for y in ys])
            draw_tag(d, r, parts)
            leaders.append((box, r, color))

        for p, ids, b in skboxes:  # one reason tag per entry (on its first run on this page)
            if ids == next(i for q, i, _ in skboxes if q is p):
                right_tag(b, [(f"pominięte: {short(p.get('powod'), 28)}", SKIP_COLOR, "white")], SKIP_COLOR)
        for e, _, b in fboxes:
            f = e["f"]
            parts = [(f"#{e['n']} {kind(f)}{'*' if req(f) else ''}", self.color(e["si"]), "white")]
            if f.get("warunek"):
                parts.append(("?", COND_COLOR, "black"))
            right_tag(b, parts, self.color(e["si"]))
        for sec, si, b, first in sboxes:  # section labels in the left gutter / margin, right-aligned to the box
            txt = ("" if first else "cd. ") + f"S{si}: {short(sec.get('tytul') or sec.get('techName'), 60)}" + \
                  (" (powt.)" if sec.get("powtarzalna") else "") + (" ?" if sec.get("warunek") else "")
            width = max(80, b[0] - 14)
            ls = wrap(d, txt, F_SEC, width)
            w, h = max(d.textlength(t, font=F_SEC) for t in ls) + 8, len(ls) * (F_SEC.size + 3) + 6
            x = b[0] - 6 - w
            r = pl.put([[x, b[1] + k * 6, x + w, b[1] + k * 6 + h] for k in range(0, 60)] +
                       [[x, b[1] - k * 6, x + w, b[1] - k * 6 + h] for k in range(1, 60)])
            d.rectangle(r, fill=light(self.color(si), 0.82), outline=light(self.color(si)))
            for i, t in enumerate(ls):
                d.text((r[2] - 4 - d.textlength(t, font=F_SEC), r[1] + 3 + i * (F_SEC.size + 3)), t,
                       fill=self.color(si), font=F_SEC)
        for box, r, color in leaders:  # elbow: along the box's own row, down the gutter, into the tag
            yb = (r[1] + r[3]) / 2
            ya = min(max(yb, box[1] + 3), box[3] - 3)
            xm = r[0] - 5
            if r[0] - box[2] > 3 or ya != yb:
                d.line([(box[2], ya), (xm, ya), (xm, yb), (r[0], yb)], fill=light(color, 0.3), width=1)

        img = img.crop((0, 0, int(max([LEFT_W + base.width] + [r[2] for r in pl.tags])) + 16, H))
        W = img.width
        panel = text_block(self.panel_rows(no, n_pages, fboxes, skboxes), PANEL_W, H)
        out = Image.new("RGB", (W + PANEL_W, max(H, panel.height)), "white")
        out.paste(img, (0, 0))
        out.paste(panel, (W, 0))
        ImageDraw.Draw(out).line([(W, 0), (W, out.height)], fill=(170, 170, 170), width=2)
        return out

    def color(self, si):
        return STRONA_COLORS[(si - 1) % len(STRONA_COLORS)]

    def legend(self):
        rows = []
        for si, st in enumerate(self.c.get("strony") or [], 1):
            rows.append((f"Strona e-usługi {si}: {s(st.get('tytul'))}", F_BOLD, self.color(si), 0,
                         (self.color(si), "box")))
            if st.get("warunek"):
                rows.append((f"strona widoczna gdy {self.cond(st['warunek'])[0]}", F_SMALL, GREY, 52))
        rows += [("sekcja (podpis po lewej, S = numer strony e-usługi)", F_SMALL, "black", 0, (STRONA_COLORS[0], "fill")),
                 ("pominięte (nie trafia do e-usługi), z powodem", F_SMALL, "black", 0, (SKIP_COLOR, "dash")),
                 ("pole z warunkiem widoczności; * = wymagane", F_SMALL, "black", 0, (COND_COLOR, "cond"))]
        return rows

    def field_rows(self, e, page_skip_ids):
        f = e["f"]
        rows = [(f"#{e['n']} {kind(f)}{' *' if req(f) else ''}  {s(f.get('techName'))} - "
                 f"{s(f.get('tytul'))}", F_BOLD, self.color(e["si"]), 14)]
        sub = lambda t, c=(25, 25, 25): rows.append((t, F_SMALL, c, 34))  # noqa: E731
        if kind(f) != s(f.get("typ")).upper() and f.get("typ"):
            sub(f"wzorzec {kind(f)}, typ {s(f.get('typ'))}")
        if f.get("walidatory"):
            sub("walidatory: " + ", ".join(s(v) for v in f["walidatory"]))
        if f.get("opcje"):
            sub("opcje: " + " / ".join(short(o) for o in f["opcje"]))
        if f.get("slownik"):
            sub(f"słownik: {s(f['slownik'])}")
        if f.get("opis"):
            sub(f"opis: {s(f['opis'])}")
        if f.get("warunek"):
            txt, probs = self.cond(f["warunek"])
            sub(f"widoczne gdy {txt}", (150, 95, 0))
            for p in probs:
                sub(f"(!) {p}", ERR_COLOR)
        ids = [str(i) for i in f.get("linie") or []]
        sub(f"linie: {id_ranges(ids) or 'brak'}", GREY)
        both = [i for i in ids if i in page_skip_ids]
        if both:
            sub(f"(!) {id_ranges(both)} jest też w pominiętych ({', '.join(page_skip_ids[i] for i in both)})",
                ERR_COLOR)
        return rows

    def panel_rows(self, no, n_pages, fboxes, skboxes):
        rows = [(f"{self.nn}. {s(self.c.get('tytul'))}", F_H, "black", 0),
                (f"strona papierowa {no} z {n_pages}  |  {self.source.name}", F_SMALL, GREY, 0), ("", F_TXT, None, 0)]
        rows += self.legend() + [("", F_TXT, None, 0)]
        skip_ids = {}
        for p in self.skips:
            for i in p.get("linie") or []:
                skip_ids.setdefault(str(i), s(p.get("powod")))
        shown = list({e["n"]: e for e, _, _ in fboxes}.values())
        rows.append((f"Pola na tej stronie ({len(shown)})" if shown else "Brak pól e-usługi na tej stronie.", F_H,
                     "black", 0))
        sec = None
        for e in shown:
            if e["sekcja"] is not sec:
                sec = e["sekcja"]
                rows.append(("", F_TXT, None, 0))
                rows.append((f"S{e['si']} / sekcja: {s(sec.get('tytul'))} ({s(sec.get('techName'))})" +
                             (", powtarzalna" if sec.get("powtarzalna") else ""), F_TXT, light(self.color(e["si"]), 0.15), 0))
                if sec.get("warunek"):
                    rows.append((f"sekcja widoczna gdy {self.cond(sec['warunek'])[0]}", F_SMALL, (150, 95, 0), 14))
            rows += self.field_rows(e, skip_ids)
        page_skips = list({id(p): p for p, _, _ in skboxes}.values())
        if page_skips:
            rows += [("", F_TXT, None, 0), ("Pominięte na tej stronie", F_H, "black", 0)]
            rows += [(f"{s(p.get('powod'))}: {id_ranges([str(i) for i in p.get('linie') or []])}", F_SMALL, GREY, 14)
                     for p in page_skips]
        return rows

    # ----- summary -----

    def summary(self, width, min_h):
        c, rows = self.c, []
        ad = c.get("adresat") if isinstance(c.get("adresat"), dict) else {}
        war = c.get("warianty") if isinstance(c.get("warianty"), dict) else {}
        yn = lambda v: "tak" if v else "nie"  # noqa: E731
        rows += [(f"{self.nn}. Podsumowanie: {s(c.get('tytul')) or '(brak tytułu)'}", F_BIG, "black", 0),
                 (f"źródło: {self.source.relative_to(ROOT)}  |  linii: {len(self.lines)}  |  pól: {len(self.fields)}",
                  F_SMALL, GREY, 0), ("", F_TXT, None, 0),
                 (f"Adresat: {s(ad.get('nazwa')) or '(brak)'} ({s(ad.get('rodzaj')) or '?'})", F_TXT, "black", 0),
                 (f"Warianty: podmiot (firma/organizacja) {yn(war.get('podmiot'))}, pełnomocnik "
                  f"{yn(war.get('pelnomocnik'))}", F_TXT, "black", 0),
                 ("Dane wnioskodawcy (dodatkowe): " + (", ".join(s(x) for x in c.get("dane_wnioskodawcy") or [])
                                                       or "brak"), F_TXT, "black", 0), ("", F_TXT, None, 0),
                 ("Struktura e-usługi", F_H, "black", 0)]
        for si, st in enumerate(c.get("strony") or [], 1):
            n = sum(1 for e in self.fields if e["si"] == si)
            rows.append((f"Strona {si}: {s(st.get('tytul'))}  ({pola(n)})", F_BOLD, self.color(si), 0,
                         (self.color(si), "box")))
            if st.get("warunek"):
                rows.append((f"strona widoczna gdy {self.cond(st['warunek'])[0]}", F_SMALL, (150, 95, 0), 52))
            for sec in st.get("sekcje") or []:
                fs = [e for e in self.fields if e["sekcja"] is sec]
                rows.append((f"sekcja: {s(sec.get('tytul'))} ({s(sec.get('techName'))}), {pola(len(fs))}" +
                             (", powtarzalna" if sec.get("powtarzalna") else ""), F_TXT, light(self.color(si), 0.15), 52))
                if sec.get("warunek"):
                    rows.append((f"sekcja widoczna gdy {self.cond(sec['warunek'])[0]}", F_SMALL, (150, 95, 0), 72))
                for e in fs:
                    f = e["f"]
                    cond = f"   [gdy {self.cond(f['warunek'])[0]}]" if f.get("warunek") else ""
                    rows.append((f"#{e['n']} {kind(f)}{' *' if req(f) else ''}  {s(f.get('techName'))} - "
                                 f"{s(f.get('tytul'))}{cond}", F_SMALL, (25, 25, 25), 72))
        for sl in c.get("nowe_slowniki") or []:
            if isinstance(sl, dict):
                vals = ", ".join(short(v, 30) for v in sl.get("wartosci") or []) or "brak wartości"
                rows.append((f"nowy słownik {s(sl.get('nazwa'))}: {vals}. {s(sl.get('zastosowanie'))}", F_SMALL, GREY, 0))

        def listing(title, items, color):
            rows.extend([("", F_TXT, None, 0), (title, F_H, "black", 0)])
            rows.extend([(f"- {s(x)}", F_SMALL, color, 14) for x in items] or [("brak", F_SMALL, GREY, 14)])

        listing(f"Do weryfikacji ({self.nn}.debug.json)" if self.debug else f"Do weryfikacji: brak {self.nn}.debug.json",
                self.debug.get("do_weryfikacji") or [], (150, 95, 0))
        listing(f"Błędy ({self.nn}.debug.json)", self.debug.get("bledy") or [], ERR_COLOR)
        listing("Pola bez linii (nie wiadomo, gdzie są na papierze)",
                [f"#{e['n']} {kind(e['f'])} {s(e['f'].get('techName'))} - {s(e['f'].get('tytul'))}"
                 for e in self.fields if not [i for i in e["f"].get("linie") or [] if str(i) in self.by_id]], ERR_COLOR)
        listing("Problemy wykryte w kontrakcie", self.warnings, ERR_COLOR)
        cited = {str(i) for e in self.fields for i in e["f"].get("linie") or []}
        skipped = {str(i) for p in self.skips for i in p.get("linie") or []}
        listing("Linie bez przypisania (ani pole, ani pominięte)",
                [f"{ln['id']} (str. {ln['page']}): {short(ln['text'], 110)}" for ln in self.lines
                 if ln["id"] not in cited | skipped], (150, 95, 0))
        both = sorted(cited & skipped)
        listing("Linie jednocześnie w polu i w pominiętych", [id_ranges(both)] if both else [], GREY)
        return text_block(rows, width, min_h, pad=50)

    def render(self, out):
        if self.source.suffix.lower() != ".pdf":
            print(f"{self.nn}: {self.source.name} to nie PDF (TODO: obrazy/skany przez OCR), pomijam")
            return
        pages = []
        with pdfplumber.open(self.source) as pdf:
            for no, page in enumerate(pdf.pages, 1):
                img = self.page_image(page, no, len(pdf.pages))
                img.save(out / f"{self.nn}_page{no}.png")
                pages.append(img)
        pages.append(self.summary(pages[0].width, pages[0].height))
        pages[-1].save(out / f"{self.nn}_podsumowanie.png")
        pdf_out = out / f"{self.nn}_wynik.pdf"
        pages[0].save(pdf_out, save_all=True, append_images=pages[1:], resolution=DPI)
        print(f"{self.nn}: {pdf_out.relative_to(ROOT) if pdf_out.is_relative_to(ROOT) else pdf_out} "
              f"({len(pages) - 1} str. + podsumowanie, {len(self.warnings)} uwag)")
        for w in self.warnings:
            print("   ", w)


def main():
    selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("forms", nargs="*", help="numery formularzy, np. 01 02 (domyślnie wszystkie NN.json w --in)")
    ap.add_argument("--in", dest="indir", default="temp")
    ap.add_argument("--out", default="temp/viz")
    a = ap.parse_args()
    indir, out = (Path(p) if Path(p).is_absolute() else ROOT / p for p in (a.indir, a.out))
    forms = [f"{int(x):02d}" for x in a.forms] or sorted(p.stem for p in indir.glob("*.json")
                                                         if re.fullmatch(r"\d+\.json", p.name))
    out.mkdir(parents=True, exist_ok=True)
    for nn in forms:
        Form(nn, indir).render(out)


if __name__ == "__main__":
    main()
