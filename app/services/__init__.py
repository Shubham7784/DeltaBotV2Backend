"""Delta Exchange Bot Services."""

from app.core.config import settings
from app.services.delta_client import DeltaClient, DeltaAPIError

# Singleton client instances for Dual-Domain Isolation
futures_client = DeltaClient(
    api_key=settings.DELTA_FUTURES_API_KEY,
    api_secret=settings.DELTA_FUTURES_API_SECRET,
    base_url=settings.get_delta_base_url(),
    subaccount_name="futures",
    paper_trading=settings.PAPER_TRADING,
)

options_client = DeltaClient(
    api_key=settings.DELTA_OPTIONS_API_KEY,
    api_secret=settings.DELTA_OPTIONS_API_SECRET,
    base_url=settings.get_delta_base_url(),
    subaccount_name="options",
    paper_trading=settings.PAPER_TRADING,
)

__all__ = ["DeltaClient", "DeltaAPIError", "futures_client", "options_client"]
