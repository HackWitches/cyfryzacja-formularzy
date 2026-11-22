"""Where things sit on the paper: page sizes, numbered text lines and boxes of the fields the model found.

Origin is the page's top-left corner. Unit: "pt" (PDF points) with a text layer, "px" of the OCR image otherwise.
"""

from pathlib import Path

import pdfplumber
from PIL import Image

from app.domain.contract import fields


def pages(path: Path, from_ocr: bool, scan_dpi: int) -> tuple[str, list[dict]]:
    """Unit and size of each page of the file the lines came from (converted PDF or image)."""
    if path.suffix != ".pdf":
        with Image.open(path) as img:
            return "px", [{"page": 1, "width": img.width, "height": img.height}]
    scale = scan_dpi / 72 if from_ocr else 1  # a scanned PDF is rendered at scan_dpi before OCR
    with pdfplumber.open(path) as pdf:
        return ("px" if from_ocr else "pt"), [
            {"page": n, "width": round(p.width * scale, 1), "height": round(p.height * scale, 1)}
            for n, p in enumerate(pdf.pages, 1)
        ]


def lines(items: list[dict]) -> list[dict]:
    return [
        {"id": i["id"], "page": i["page"], "text": i["text"], "bbox": [round(v, 1) for v in i["bbox"]]} for i in items
    ]


def field_boxes(contract: dict, items: list[dict]) -> list[dict]:
    """One box per field and page: the union of the lines the model cited for the field.

    Fields the model added with no cited lines get no box.
    """
    where = {i["id"]: i for i in items}
    out = []
    for page, section, f in fields(contract):
        by_page = {}
        for line in (where[i] for i in f["linie"] if i in where):
            by_page.setdefault(line["page"], []).append(line["bbox"])
        for n, boxes in sorted(by_page.items()):
            out.append(
                {
                    "techName": f["techName"],
                    "title": f["tytul"],
                    "type": f["wzorzec"] if f["wzorzec"] != "POLE" else f["typ"],
                    "section": section["tytul"],
                    "formPage": page["tytul"],
                    "page": n,
                    "lines": [i for i in f["linie"] if i in where and where[i]["page"] == n],
                    "bbox": [
                        round(min(b[0] for b in boxes), 1),
                        round(min(b[1] for b in boxes), 1),
                        round(max(b[2] for b in boxes), 1),
                        round(max(b[3] for b in boxes), 1),
                    ],
                }
            )
    return out
