"""
Structured audit trail helpers (blueprint sections 4, 26, 34: "every order
must be reconstructable from an audit trail", "use correlation/request IDs
to reconstruct every incident end-to-end").
"""
import uuid
from app.db.models import SystemEvent, AuditLog, utcnow


def new_request_id() -> str:
    return str(uuid.uuid4())


def log_event(session, component: str, level: str, message: str, request_id: str = None, payload: dict = None):
    row = SystemEvent(
        component=component, level=level, message=message,
        request_id=request_id, payload=payload or {},
    )
    session.add(row)
    session.commit()
    return row


def log_audit(session, actor: str, action: str, entity_type: str = None,
              entity_id: str = None, request_id: str = None, details: dict = None):
    row = AuditLog(
        actor=actor, action=action, entity_type=entity_type, entity_id=entity_id,
        request_id=request_id, details=details or {},
    )
    session.add(row)
    session.commit()
    return row
