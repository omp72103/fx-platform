"""
FastAPI application entrypoint. Run with:
    uvicorn app.main:app --reload
"""
import uuid
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.session import init_db, SessionLocal
from app.orchestrator import seed_reference_data
from app.api.routes import router
from app.audit import log_event

app = FastAPI(title="AI Adaptive Forex Trading Platform API", version="0.1.0")
app.include_router(router)


@app.on_event("startup")
def on_startup():
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
    return {"service": "fx-platform", "gateway_mode": settings.gateway_mode,
            "account_mode": settings.account_mode}
