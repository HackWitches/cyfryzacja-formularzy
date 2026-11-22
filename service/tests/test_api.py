"""API tests with a fake language model (no network): input checks, JWT, pipeline, error mapping.

Run: pytest -q   (from the service directory)
"""
import io
import json
import time
import zipfile
from pathlib import Path

import jwt
import pytest
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


def make(secret: str = "", mb: int = 10, fail: bool = False, vision: str = "off", **settings):
    s = Settings(jwt_secret=secret, auth_disabled=not secret, max_file_size_mb=mb, llm_vision=vision, **settings)
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
    assert upload(c, name="a" * 247 + ".pdf").status_code == 400  # 251 characters
    assert upload(c, name="a" * 246 + ".pdf").status_code == 200  # exactly 250
    assert upload(c, data=b"").status_code == 400


def test_rejects_too_large_file():
    c, _, _ = make(mb=0)
    assert upload(c).status_code == 413


def test_too_large_upload_is_refused_before_the_body_is_stored(monkeypatch):
    """The framework spools the multipart body to a temporary file before the endpoint and the JWT check run."""
    from starlette.datastructures import UploadFile

    stored = []
    write = UploadFile.write

    async def spy(self, data):
        stored.append(len(data))
        return await write(self, data)

    monkeypatch.setattr(UploadFile, "write", spy)
    c, _, _ = make(secret=SECRET, mb=1)
    assert upload(c, data=b"%PDF" + b"0" * 3_000_000).status_code == 413  # no token either: size comes first
    assert stored == []
    assert c.post(URL, content=iter([b"x"])).status_code == 411  # chunked body: no declared length


def test_broken_pdf_and_too_many_pages_are_client_errors():
    c, _, _ = make()
    r = upload(c, data=b"%PDF-1.4 to nie jest PDF")
    assert r.status_code == 422 and "PDF" in r.json()["detail"]
    c, _, _ = make(max_pages=0)
    assert upload(c).status_code == 413


def test_unusual_model_output_does_not_crash():
    """Without schema enforcement a model may return a number or a string where the contract has a list."""
    class Sloppy(FakeReader):
        def understand(self, lines_text, pages=()):
            fields = [{"tytul": "A", "opcje": 5, "walidatory": 7, "linie": 13, "opis": ["x"]},
                      {"tytul": "B", "typ": "SELECT", "slownik": ["x"], "opcje": "Tak"},
                      {"tytul": "C", "typ": "RADIO", "opcje": ["a", "b"], "linie": ["L", "13", 4]}]
            return {"tytul": "Wniosek o test", "nowe_slowniki": "brak", "dane_wnioskodawcy": 1, "pominiete": 3,
                    "strony": [{"tytul": "S", "sekcje": [{"tytul": "Dane kandydata", "pola": fields}]}]}

    c = TestClient(create_app(Settings(auth_disabled=True), Sloppy()))
    r = upload(c)
    assert r.status_code == 200, r.text
    assert r.headers["X-Form-Errors"] == "0"


def test_jwt_required_when_secret_set():
    c, _, s = make(secret=SECRET)
    assert upload(c).status_code == 401
    good = issue(s, "urzednik", 5)
    assert upload(c, headers={"Authorization": f"Bearer {good}"}).status_code == 200
    expired = jwt.encode({"sub": "x", "exp": int(time.time()) - 10}, SECRET, algorithm="HS256")
    assert upload(c, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert upload(c, headers={"Authorization": "Bearer nie-token"}).status_code == 401
    other_algorithm = jwt.encode({"sub": "x", "exp": int(time.time()) + 60}, SECRET * 2, algorithm="HS512")
    assert upload(c, headers={"Authorization": f"Bearer {other_algorithm}"}).status_code == 401


def test_jwt_audience_is_checked_when_configured():
    c, _, s = make(secret=SECRET, jwt_audience="coi")
    assert upload(c, headers={"Authorization": f"Bearer {issue(s, 'urzednik', 5)}"}).status_code == 200
    no_aud = jwt.encode({"sub": "x", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")
    assert upload(c, headers={"Authorization": f"Bearer {no_aud}"}).status_code == 401


def test_authentication_is_on_unless_explicitly_disabled():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(Settings(), FakeReader())
    c, _, _ = make(secret=SECRET)
    schemes = c.get("/openapi.json").json()["components"]["securitySchemes"]
    assert [s["scheme"] for s in schemes.values()] == ["bearer"]  # the "Authorize" button in /docs


def test_settings_from_env(monkeypatch):
    for name, value in {"MAX_FILE_SIZE_MB": "", "MAX_PAGES": "7", "AUTH_DISABLED": "true", "LLM_VISION": "false",
                        "LLM_JSON_SCHEMA": "0"}.items():
        monkeypatch.setenv(name, value)
    s = Settings.from_env()
    assert (s.max_file_size_mb, s.max_pages, s.auth_disabled, s.llm_vision, s.llm_json_schema) == \
        (10, 7, True, "off", False)
    monkeypatch.setenv("LLM_VISION", "czasem")
    with pytest.raises(ValueError, match="LLM_VISION"):
        Settings.from_env()
    monkeypatch.setenv("LLM_VISION", "auto")
    monkeypatch.setenv("MAX_PAGES", "dużo")
    with pytest.raises(ValueError, match="MAX_PAGES"):
        Settings.from_env()


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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:  # the mimetype entry is read only up to its first bytes
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet" + " " * 5_000_000)
    assert content_matches("ods", buf.getvalue())


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

    c = TestClient(create_app(Settings(auth_disabled=True), NoEntity()))
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

