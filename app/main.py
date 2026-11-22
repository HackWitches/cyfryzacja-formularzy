"""FastAPI application factory: `uvicorn app.main:create_app --factory`."""

import logging

from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes import router
from app.config import Settings
from app.ingest.upload import KINDS
from app.llm import FormReader, make_reader

MULTIPART_OVERHEAD = 64 * 1024  # boundaries and part headers around the one file


class UploadSizeLimit:
    """Rejects an oversized upload by its Content-Length, before the body is read.

    FastAPI spools the whole multipart body to a temp file before the endpoint and the JWT check run.
    Without this guard, an anonymous client could fill the temp disk.
    A body without a declared length gets 411; the server cuts a body longer than declared.
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST":
            length = Headers(scope=scope).get("content-length", "")
            if not length.isdigit():
                return await JSONResponse({"detail": "Wymagany nagłówek Content-Length."}, 411)(scope, receive, send)
            if int(length) > self.max_bytes + MULTIPART_OVERHEAD:
                return await JSONResponse({"detail": f"Plik większy niż {self.max_bytes // (1024 * 1024)} MB."}, 413)(
                    scope, receive, send
                )
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, reader: FormReader | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.auth_disabled:
        logging.getLogger("uvicorn.error").warning("AUTH_DISABLED is set: the API accepts requests without a token")
    elif not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is not set. Set it, or set AUTH_DISABLED=true for local development.")
    formats = ", ".join(sorted(ext.upper() for ext in KINDS))
    app = FastAPI(
        title="Cyfryzacja formularzy COI",
        version="1.0.0",
        description=f"Plik formularza ({formats}) -> JSON konfiguracji e-formularza.",
    )
    app.state.settings = settings
    app.state.reader = reader or make_reader(settings)
    app.include_router(router)
    app.add_middleware(UploadSizeLimit, max_bytes=settings.max_file_size_bytes)
    return app
