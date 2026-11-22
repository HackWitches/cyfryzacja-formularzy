"""API tests with a fake language model (no network): input checks, JWT, pipeline, error mapping.

Run: pytest -q   (from the service directory)
"""
import io
import json
import time
import zipfile
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

from app.api.auth import issue
from app.config import Settings
from app.ingest.upload import content_matches
from app.llm import LLMError
from app.main import create_app

DATA = Path(__file__).parent / "data"
PDF = (DATA / "01.pdf").read_bytes()
CONTRACT = json.loads((DATA / "01_kontrakt.json").read_text(encoding="utf-8"))
URL = "/api/v1/forms/convert"
SECRET = "test-secret-" * 4  # HS256 wants at least 32 bytes


class FakeReader:
    def __init__(self, fail: bool = False):
        self.fail, self.seen, self.pages = fail, None, None

    def understand(self, lines_text: str, pages=()) -> dict:
        self.seen, self.pages = lines_text, list(pages)
        if self.fail:
            raise LLMError("model niedostępny")
        return CONTRACT


def make(secret: str = "", mb: int = 10, fail: bool = False, vision: str = "off"):
    s = Settings(jwt_secret=secret, max_file_size_mb=mb, llm_vision=vision)
    reader = FakeReader(fail)
    return TestClient(create_app(s, reader)), reader, s


def upload(c, name="wniosek.pdf", data=PDF, **kw):
    return c.post(URL, files={"file": (name, data, "application/octet-stream")}, **kw)


def test_health():
    c, _, _ = make()
    assert c.get("/health").json() == {"status": "ok"}


def test_pdf_to_coi_form():
    c, reader, _ = make()
    r = upload(c)
    assert r.status_code == 200, r.text
    form = r.json()
    assert form["title"].startswith("Wniosek o ustalenie numeru porządkowego")
    assert [t["type"] for t in form["tabs"]][-1] == "FINISH"
    assert "Informacje o budynku" in [t["title"] for t in form["tabs"]]
    assert len(form["flow"]["links"]) == len(form["tabs"]) - 1
    assert r.headers["X-Form-Errors"] == "0"
    assert "L001" in reader.seen and "Wnioskodawca" in reader.seen  # the model got numbered lines


def test_debug_returns_contract_and_lines():
    c, _, _ = make()
    body = upload(c, params={"debug": "true"}).json()
    assert set(body) == {"form", "do_weryfikacji", "bledy", "kontrakt", "linie", "reguly"}
    assert body["bledy"] == []


def test_rejects_content_not_matching_extension():
    c, _, _ = make()
    assert upload(c, data=b"\x89PNG\r\n\x1a\n" + b"0" * 64).status_code == 415


def test_rejects_unsupported_extension():
    c, _, _ = make()
    assert upload(c, name="wniosek.exe").status_code == 415


def test_rejects_forbidden_characters_and_long_names():
    c, _, _ = make()
    assert upload(c, name="wniosek;1.pdf").status_code == 400
    assert upload(c, name="a" * 247 + ".pdf").status_code == 400


def test_rejects_too_large_file():
    c, _, _ = make(mb=0)
    assert upload(c).status_code == 413


def test_jwt_required_when_secret_set():
    c, _, s = make(secret=SECRET)
    assert upload(c).status_code == 401
    good = issue(s, "urzednik", 5)
    assert upload(c, headers={"Authorization": f"Bearer {good}"}).status_code == 200
    expired = jwt.encode({"sub": "x", "exp": int(time.time()) - 10}, SECRET, algorithm="HS256")
    assert upload(c, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert upload(c, headers={"Authorization": "Bearer nie-token"}).status_code == 401


def test_llm_failure_is_bad_gateway():
    c, _, _ = make(fail=True)
    assert upload(c).status_code == 502


def test_office_formats_recognised_by_content():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    docx = buf.getvalue()
    assert content_matches("docx", docx)
    assert not content_matches("xlsx", docx)
    assert not content_matches("ods", docx)
    assert content_matches("doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)


def test_vision_sends_the_pdf_next_to_the_lines():
    c, reader, _ = make(vision="on")
    assert upload(c).status_code == 200
    assert [p.media_type for p in reader.pages] == ["application/pdf"]
    assert reader.pages[0].data[:4] == b"%PDF"
    for mode in ("off", "auto"):  # auto: a PDF with a text layer goes as text only
        c, reader, _ = make(vision=mode)
        upload(c)
        assert reader.pages == []


def test_entity_variant_from_paper_even_if_the_model_says_no():
    """Form 1 says "imię i nazwisko/nazwa wnioskodawcy": the company sections must stay whatever the model said."""
    import copy

    class NoEntity(FakeReader):
        def understand(self, lines_text, pages=()):
            c = copy.deepcopy(CONTRACT)
            c["warianty"]["podmiot"] = False
            return c

    s = Settings()
    c = TestClient(create_app(s, NoEntity()))
    form = upload(c).json()
    sections = [q["field"]["techName"] for t in form["tabs"] for q in t["questions"]]
    assert "dane_podmiotu" in sections and "adres_siedziby" in sections


def test_concurrent_page_rendering_does_not_crash():
    """pdfium is not thread-safe; without the lock in app/ingest/render.py this kills the process (exit 133)."""
    from concurrent.futures import ThreadPoolExecutor

    from app.llm import Page
    from app.llm.openai_compat import _images

    with ThreadPoolExecutor(8) as ex:
        assert sum(ex.map(lambda _: len(_images([Page("application/pdf", PDF)], 4)), range(64))) > 0

