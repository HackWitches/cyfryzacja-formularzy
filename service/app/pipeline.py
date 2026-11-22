"""File -> numbered lines -> language model -> contract v2 -> builder -> COI FormDefinition.

Everything happens in a temporary directory that is removed when the request ends (no persistent storage).
"""
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.domain.builder import build
from app.domain.contract import normalize
from app.ingest import convert, ocr
from app.ingest.lines import as_prompt, lines
from app.ingest.render import page_image
from app.llm import FormReader, Page
from app.rules import Context, apply as apply_rules

MIN_TEXT_CHARS = 20  # fewer characters in the PDF text layer = a scan, read it with OCR
MAX_IMAGE_SIDE = 2000  # px; model APIs limit image size, forms stay readable at this resolution


@dataclass
class Result:
    form: dict
    flags: list[str]
    errors: list[str]
    contract: dict
    lines_text: str
    rules: list[str]


def page_for_model(path: Path, kind: str) -> Page:
    if kind != "image":
        return Page("application/pdf", path.read_bytes())
    from PIL import Image  # installed with pdfplumber

    img = Image.open(path).convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return Page("image/jpeg", buf.getvalue())


def read_lines(path: Path, kind: str, s: Settings) -> tuple[list[dict], Path, bool]:
    """Numbered lines, the file the model may look at (the PDF after conversion, or the image), from OCR?"""
    if kind == "image":
        return ocr.lines_from_images([path], s.ocr_lang), path, True
    if kind == "office":
        path = convert.to_pdf(path, s.soffice_bin, s.convert_timeout_s)
    items = lines(path)
    if sum(len(i["text"]) for i in items) >= MIN_TEXT_CHARS:
        return items, path, False
    import pdfplumber  # scanned PDF: render pages and OCR them

    images = []
    with pdfplumber.open(path) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            out = path.parent / f"page{n}.png"
            page_image(page, 300).save(out)
            images.append(out)
    return ocr.lines_from_images(images, s.ocr_lang), path, True


def run(name: str, data: bytes, kind: str, reader: FormReader, s: Settings) -> Result:
    with tempfile.TemporaryDirectory(prefix="form-") as tmp:
        path = Path(tmp) / ("upload." + name.rsplit(".", 1)[-1].lower())
        path.write_bytes(data)
        items, seen, from_ocr = read_lines(path, kind, s)
        text = as_prompt(items)
        vision = s.llm_vision == "on" or (s.llm_vision == "auto" and from_ocr)
        pages = [page_for_model(seen, "image" if seen.suffix != ".pdf" else "pdf")] if vision else []
    raw = reader.understand(text, pages)
    contract = normalize(raw)
    notes = apply_rules(contract, Context.of(text))  # Polish paper rules fix what the model missed (app/rules)
    form, flags, errors = build(contract)
    if raw.get("_ucieta_odpowiedz"):
        flags.insert(0, "Odpowiedź modelu była ucięta: część pól z końca formularza może brakować.")
    return Result(form, flags, errors, contract, text, notes)
