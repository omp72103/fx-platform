"""
FastAPI application entrypoint. Run with:
    uvicorn app.main:app --reload
"""
import uuid
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import os
from app.config import settings
from app.live_guard import describe_mode, is_live, require_valid_live_config
from app.db.session import init_db, SessionLocal
from app.orchestrator import seed_reference_data
from app.api.routes import router
from app.audit import log_event

app = FastAPI(title="AI Adaptive Forex Trading Platform API", version="0.1.0")

# CORS: in demo mode this API is meant to be polled directly from a
# browser-based dashboard on another origin, so it is wide open ("*").
# In live mode it is closed by default: only origins listed in CORS_ORIGINS
# (comma-separated) are allowed.
_cors_origins = (
    [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    if is_live(settings) else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def on_startup():
    if is_live(settings):
        require_valid_live_config(settings)   # refuse to serve a half-configured live API
    init_db()
    session = SessionLocal()
    try:
        seed_reference_data(session)
    finally:
        session.close()


@app.middleware("http")
async def request_id_and_logging(request: Request, call_next):
    rid = request.headers.get("x-request-id", str(uuid.uuid4()))
    t0 = time.time()
    try:
        response = await call_next(request)
    except Exception as e:  # noqa: BLE001 - structured 500, never a bare stack trace to the client
        session = SessionLocal()
        try:
            log_event(session, component="api", level="ERROR",
                       message=f"unhandled exception on {request.url.path}: {e}", request_id=rid)
        finally:
            session.close()
        return JSONResponse(status_code=500, content={"error": "internal_error", "request_id": rid})
    response.headers["x-request-id"] = rid
    response.headers["x-elapsed-ms"] = str(round((time.time() - t0) * 1000, 1))
    return response


@app.get("/")
def root():
    return {"service": "fx-platform", **describe_mode(settings)}
