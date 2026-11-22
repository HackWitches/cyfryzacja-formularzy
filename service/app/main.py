"""FastAPI application factory: `uvicorn app.main:create_app --factory`."""
import logging

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings
from app.llm import FormReader, make_reader


def create_app(settings: Settings | None = None, reader: FormReader | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.jwt_secret:
        logging.getLogger("uvicorn.error").warning("JWT_SECRET is not set: authentication is OFF")
    app = FastAPI(title="Cyfryzacja formularzy COI", version="1.0.0",
                  description="Plik formularza (PDF, DOC, DOCX, ODS, XLSX, JPG, PNG) -> JSON konfiguracji e-formularza.")
    app.state.settings = settings
    app.state.reader = reader or make_reader(settings)
    app.include_router(router)
    return app

