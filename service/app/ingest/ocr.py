"""Scans (JPG/PNG, PDF pages without text): Tesseract 5 words with boxes -> the same numbered lines as for PDFs.

Tesseract (Apache-2.0) runs as a separate program and returns TSV (block, paragraph, line, word with box).
ponytail: no box/checkbox detection on images yet (OpenCV step); text, columns and reading order only.
"""
import csv
import io
import subprocess
from pathlib import Path

from app.ingest.lines import clean, reflow, rows_of


class OcrError(RuntimeError):
    pass


def words(image: Path, lang: str) -> list[tuple]:
    try:
        tsv = subprocess.run(["tesseract", str(image), "stdout", "-l", lang, "--psm", "4", "tsv"],
                             check=True, capture_output=True, text=True, timeout=120).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise OcrError(f"OCR nie powiódł się: {e}") from e
    out = []
    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (r.get("text") or "").strip()
        if r.get("level") == "5" and text:
            x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
            out.append((x, y, x + w, y + h, text))
    return out


def lines_from_images(images: list[Path], lang: str) -> list[dict]:
    out = []
    for page_no, image in enumerate(images, 1):
        for r in reflow(rows_of(words(image, lang))):
            text = clean(" ".join(w[4] for w in r))
            if text:
                out.append({"id": f"L{len(out) + 1:03d}", "page": page_no, "text": text,
                            "bbox": [min(w[0] for w in r), min(w[1] for w in r), max(w[2] for w in r),
                                     max(w[3] for w in r)]})
    return out
