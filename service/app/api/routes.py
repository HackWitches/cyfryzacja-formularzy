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


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/api/v1/forms/convert", dependencies=[Depends(require_jwt)])
def convert_form(request: Request, file: UploadFile = File(...),
                 debug: bool = Query(False, description="Zwróć też kontrakt, linie i flagi do weryfikacji")):
    s = request.app.state.settings
    data = file.file.read(s.max_file_size_bytes + 1)
    try:
        kind = validate(file.filename or "", data, s.max_file_size_bytes)
        result = pipeline.run(file.filename, data, kind, request.app.state.reader, s)
    except InputError as e:
        raise HTTPException(e.status, str(e)) from e
    except (ConversionError, OcrError) as e:
        raise HTTPException(422, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    headers = {"X-Form-Flags": str(len(result.flags)), "X-Form-Errors": str(len(result.errors))}
    if debug:
        return JSONResponse({"form": result.form, "do_weryfikacji": result.flags, "bledy": result.errors,
                             "kontrakt": result.contract, "linie": result.lines_text,
                             "reguly": result.rules}, headers=headers)
    return JSONResponse(result.form, headers=headers)
