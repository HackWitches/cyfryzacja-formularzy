"""OCR for photos and scanned PDF pages: Tesseract 5 words -> the same numbered lines as for PDFs.

Tesseract (Apache-2.0) runs as a separate program and returns words with boxes as TSV.
ponytail: text, columns and reading order only; box and checkbox detection would need OpenCV.
"""

import csv
import io
import logging
import subprocess
from pathlib import Path

from app.ingest.lines import clean, numbered, reflow, rows_of

log = logging.getLogger(__name__)


class OcrError(RuntimeError):
    pass


def words(image: Path, lang: str, timeout_s: int) -> list[tuple]:
    """Words as (x0, top, x1, bottom, text), with top and bottom taken from the word's text line.

    Tesseract word boxes hug the glyphs, so "oraz" starts lower than "Imię".
    Per-word heights would make rows_of() split one line in two.
    """
    try:
        tsv = subprocess.run(
            ["tesseract", str(image), "stdout", "-l", lang, "--psm", "4", "tsv"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("tesseract failed: %r", e)  # command line and temp paths go to the log, not the client
        raise OcrError("OCR nie powiódł się.") from e
    out, line_box = [], {}
    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        line = (r.get("block_num"), r.get("par_num"), r.get("line_num"))
        text = (r.get("text") or "").strip()
        if r.get("level") == "4":
            line_box[line] = (int(r["top"]), int(r["top"]) + int(r["height"]))
        elif r.get("level") == "5" and text:
            x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
            top, bottom = line_box.get(line, (y, y + h))
            out.append((x, top, x + w, bottom, text))
    return out


def lines_from_images(images: list[Path], lang: str, timeout_s: int = 120) -> list[dict]:
    out = []
    for page_no, image in enumerate(images, 1):
        for r in reflow(rows_of(words(image, lang, timeout_s))):
            text = clean(" ".join(w[4] for w in r))
            if text:
                out.append(
                    numbered(
                        len(out) + 1,
                        page_no,
                        text,
                        [min(w[0] for w in r), min(w[1] for w in r), max(w[2] for w in r), max(w[3] for w in r)],
                    )
                )
    return out
