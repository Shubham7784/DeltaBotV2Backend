"""Pydantic schemas for health, system status, and monitoring endpoints."""

from typing import Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="Overall system health status")
    app_name: str = Field(..., description="Application name")
    version: str = Field(..., description="Application version")
    timestamp: datetime = Field(..., description="Current UTC timestamp from server")
    phase: str = Field(default="Phase 1 - Project Foundation")


class SystemStatusResponse(BaseModel):
    status: str = Field(default="operational")
    environment: str
    paper_trading: bool
    bot_active: bool
    domains: Dict[str, Dict[str, Any]]
    instruments: List[str]
    timeframes: List[str]
    capital_profile: Dict[str, Any]
    timestamp: datetime


class LogEntryResponse(BaseModel):
    timestamp: str
    level: str
    logger: str
    message: str
    module: str
    line: int
