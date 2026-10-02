"""Configuration inspection endpoint (safe)."""

from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(tags=["Configuration"])


@router.get("/config")
async def get_safe_config() -> dict:
    """Returns safe runtime configuration."""
    return settings.safe_summary()
