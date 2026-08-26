"""
Shared API dependencies — auth, DB session, request-ID — per blueprint
section 24: "All APIs require authentication/authorization, schema
validation, request IDs, structured errors and audit logging."
"""
import uuid
from fastapi import Depends, Header, HTTPException, Request

from app.config import settings
from app.db.session import SessionLocal


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_auth(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    if not token or token != settings.api_auth_token:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "code": "AUTH_REQUIRED"})
    return token


def request_id(x_request_id: str = Header(default=None)) -> str:
    return x_request_id or str(uuid.uuid4())
