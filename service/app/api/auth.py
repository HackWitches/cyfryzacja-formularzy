"""JWT bearer authentication (PyJWT). Issue a token for testing: python -m app.api.auth [subject] [minutes]"""
import os
import sys
import time

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

bearer = HTTPBearer(auto_error=False)


async def require_jwt(request: Request,
                      creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict | None:
    """The token's claims, or 401. `Depends(bearer)` also puts the Bearer scheme into the OpenAPI document."""
    s: Settings = request.app.state.settings
    if s.auth_disabled:
        return None
    if creds is None:
        raise HTTPException(401, "Brak tokenu JWT (nagłówek Authorization: Bearer ...).",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return jwt.decode(creds.credentials, s.jwt_secret, algorithms=[s.jwt_algorithm],
                          audience=s.jwt_audience or None, options={"require": ["exp"]})
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Nieprawidłowy token JWT: {e}", headers={"WWW-Authenticate": "Bearer"}) from e


def issue(s: Settings, subject: str, minutes: int) -> str:
    claims = {"sub": subject, "iat": int(time.time()), "exp": int(time.time()) + minutes * 60}
    if s.jwt_audience:
        claims["aud"] = s.jwt_audience
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm)


if __name__ == "__main__":
    settings = Settings.from_env()
    if not settings.jwt_secret:
        sys.exit("JWT_SECRET is not set")
    print(issue(settings, sys.argv[1] if len(sys.argv) > 1 else os.environ.get("USER", "urzednik"),
                int(sys.argv[2]) if len(sys.argv) > 2 else 480))
