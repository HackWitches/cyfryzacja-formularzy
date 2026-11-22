"""REST API: one file in, COI form configuration JSON out."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from app import pipeline
from app.api.auth import require_jwt
from app.ingest.convert import ConversionError
from app.ingest.ocr import OcrError
from app.ingest.upload import InputError, validate
from app.llm import LLMError

router = APIRouter()
ERRORS = {  # documented in OpenAPI; the same table is in README.md
    400: "Pusta lub błędna nazwa pliku, pusty plik",
    401: "Brak lub nieważny token JWT",
    411: "Brak nagłówka Content-Length",
    413: "Plik większy niż MAX_FILE_SIZE_MB albo dokument dłuższy niż MAX_PAGES stron",
    415: "Nieobsługiwany format albo zawartość niezgodna z rozszerzeniem",
    422: "Nie udało się odczytać dokumentu (konwersja, uszkodzony plik, OCR)",
    502: "Błąd lub niedostępność modelu językowego",
}


@router.get("/health")
async def health() -> dict:  # async: answers from the event loop even when every worker thread waits for the model
    return {"status": "ok"}


def _read_upload(request: Request, file: UploadFile) -> tuple[bytes, str]:
    s = request.app.state.settings
    data = file.file.read(s.max_file_size_bytes + 1)
    try:
        return data, validate(file.filename or "", data, s.max_file_size_bytes)
    except InputError as e:
        raise HTTPException(e.status, str(e)) from e


@router.post(
    "/api/v1/forms/convert",
    dependencies=[Depends(require_jwt)],
    responses={code: {"description": text} for code, text in ERRORS.items()},
)
def convert_form(
    request: Request,
    file: UploadFile = File(...),
    debug: bool = Query(
        False, description="Zwróć też kontrakt, linie, flagi do weryfikacji i układ (ramki pól na papierze)"
    ),
):
    """Formularz papierowy (jeden plik) -> JSON FormDefinition do importu w Generatorze Formularzy COI."""
    data, kind = _read_upload(request, file)
    try:
        result = pipeline.run(file.filename, data, kind, request.app.state.reader, request.app.state.settings)
    except InputError as e:
        raise HTTPException(e.status, str(e)) from e
    except (ConversionError, OcrError) as e:
        raise HTTPException(422, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    headers = {"X-Form-Flags": str(len(result.flags)), "X-Form-Errors": str(len(result.errors))}
    if debug:
        return JSONResponse(
            {
                "form": result.form,
                "do_weryfikacji": result.flags,
                "bledy": result.errors,
                "kontrakt": result.contract,
                "linie": result.lines_text,
                "reguly": result.rules,
                "layout": result.layout,
            },
            headers=headers,
        )
    return JSONResponse(result.form, headers=headers)


@router.post(
    "/api/v1/forms/layout",
    dependencies=[Depends(require_jwt)],
    responses={code: {"description": text} for code, text in ERRORS.items() if code != 502},
)
def form_layout(request: Request, file: UploadFile = File(...)):
    """Układ papierowego formularza bez modelu: rozmiary stron i ponumerowane linie tekstu z ramkami (bbox).
    Ramki pól znalezionych przez model zwraca `convert?debug=true` w kluczu `layout.fields`."""
    data, kind = _read_upload(request, file)
    try:
        return pipeline.scan_layout(file.filename, data, kind, request.app.state.settings)
    except InputError as e:
        raise HTTPException(e.status, str(e)) from e
    except (ConversionError, OcrError) as e:
        raise HTTPException(422, str(e)) from e
