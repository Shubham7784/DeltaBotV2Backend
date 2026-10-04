"""Leverage policy shared by signal, manual, and execution flows."""

from app.core.config import settings


def get_trade_leverage(symbol: str, subaccount: str = "futures", requested: int | None = None) -> int:
    """Return the configured leverage for an instrument and trading account."""
    if subaccount.strip().lower() == "options":
        return settings.OPTIONS_LEVERAGE

    normalized_symbol = symbol.strip().upper()
    if normalized_symbol in {"BTCUSD", "ETHUSD"}:
        return settings.BTC_ETH_FUTURES_LEVERAGE
    return requested or settings.DEFAULT_LEVERAGE
