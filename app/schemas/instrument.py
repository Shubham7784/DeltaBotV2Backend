"""Pydantic schemas for Trading Instruments."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class InstrumentBase(BaseModel):
    symbol: str = Field(..., json_schema_extra={"example": "BTCUSD"})
    product_id: Optional[int] = Field(None, json_schema_extra={"example": 27})
    contract_type: str = Field(default="perpetual_futures")
    underlying_asset: str = Field(default="BTC")
    tick_size: float = Field(default=0.5)
    contract_value: float = Field(default=0.001)
    is_active: bool = Field(default=True)


class InstrumentCreate(InstrumentBase):
    pass


class InstrumentUpdate(BaseModel):
    is_active: Optional[bool] = None
    tick_size: Optional[float] = None
    contract_value: Optional[float] = None


class InstrumentResponse(InstrumentBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
