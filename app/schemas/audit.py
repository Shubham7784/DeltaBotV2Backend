"""Pydantic schemas for System and Risk Audit Logs."""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.db.enums import AuditEventType, AuditSeverity


class AuditLogCreate(BaseModel):
    event_type: AuditEventType = AuditEventType.SYSTEM
    severity: AuditSeverity = AuditSeverity.INFO
    message: str
    subaccount: Optional[str] = None
    symbol: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


class AuditLogResponse(BaseModel):
    id: int
    event_type: str
    severity: str
    message: str
    subaccount: Optional[str] = None
    symbol: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
