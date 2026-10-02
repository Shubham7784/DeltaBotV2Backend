"""Pydantic schemas for Candlestick data."""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CandleBase(BaseModel):
    symbol: str = Field(..., json_schema_extra={"example": "BTCUSD"})
    resolution: str = Field(default="5m", json_schema_extra={"example": "5m"})
    open_time: int = Field(..., description="Unix timestamp in seconds or milliseconds")
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(default=0.0)
    close_time: Optional[int] = None
    is_closed: bool = Field(default=True)


class CandleCreate(CandleBase):
    pass


class CandleBulkCreate(BaseModel):
    symbol: str
    resolution: str
    candles: List[CandleBase]


class CandleResponse(CandleBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class MarketSyncRequest(BaseModel):
    symbols: List[str] = Field(default=["BTCUSD"], description="List of symbol codes to sync")
    timeframes: List[str] = Field(default=["5m", "15m", "1h"], description="Resolutions to sync")
    lookback_hours: int = Field(default=24, ge=1, le=168, description="Lookback window in hours (up to 7 days)")


class MarketSyncResult(BaseModel):
    symbol: str
    timeframe: str
    synced_count: int
    latest_timestamp: Optional[int] = None
    status: str = "ok"
    error: Optional[str] = None


class MarketBufferStats(BaseModel):
    symbol: str
    timeframe: str
    candle_count: int
    oldest_time: Optional[int] = None
    newest_time: Optional[int] = None
    last_close: Optional[float] = None


class MarketEngineStatus(BaseModel):
    is_running: bool
    poller_active: bool
    poll_interval_sec: int
    tracked_symbols: List[str]
    timeframes: List[str]
    buffer_stats: List[MarketBufferStats]
    live_prices: dict
    last_sync_time: Optional[str] = None


class CandleBufferResponse(BaseModel):
    symbol: str
    resolution: str
    count: int
    mark_price: Optional[float] = None
    candles: List[CandleBase]
