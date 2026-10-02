"""Client factory for managing Futures and Options DeltaClient singletons."""

from typing import Optional

from app.core.config import settings
from app.services.delta_client import DeltaClient

# Futures Subaccount Client Singleton
futures_client = DeltaClient(
    api_key=settings.DELTA_FUTURES_API_KEY,
    api_secret=settings.DELTA_FUTURES_API_SECRET,
    base_url=settings.get_delta_base_url(),
    subaccount_name="futures",
    paper_trading=settings.PAPER_TRADING,
)

# Options Subaccount Client Singleton (Isolated Subaccount)
options_client = DeltaClient(
    api_key=settings.DELTA_OPTIONS_API_KEY,
    api_secret=settings.DELTA_OPTIONS_API_SECRET,
    base_url=settings.get_delta_base_url(),
    subaccount_name="options",
    paper_trading=settings.PAPER_TRADING,
)


def get_client(subaccount: str = "futures") -> DeltaClient:
    """Returns the isolated DeltaClient instance for the requested subaccount."""
    return options_client if subaccount.lower() == "options" else futures_client


_mode_clients = {}


def get_client_for_mode(subaccount: str = "futures", paper_trading: bool = True) -> DeltaClient:
    """Returns a persistent subaccount client using the trade's requested execution mode."""
    name = "options" if subaccount.lower() == "options" else "futures"
    default_client = get_client(name)
    if default_client.paper_trading == paper_trading:
        return default_client

    key = (name, paper_trading)
    if key not in _mode_clients:
        _mode_clients[key] = DeltaClient(
            api_key=settings.DELTA_OPTIONS_API_KEY if name == "options" else settings.DELTA_FUTURES_API_KEY,
            api_secret=settings.DELTA_OPTIONS_API_SECRET if name == "options" else settings.DELTA_FUTURES_API_SECRET,
            base_url=settings.get_delta_base_url(),
            subaccount_name=name,
            paper_trading=paper_trading,
        )
    return _mode_clients[key]


class ClientFactoryManager:
    """Singleton manager providing access to subaccount Delta clients."""
    @staticmethod
    def get_client(subaccount: str = "futures", paper_trading: Optional[bool] = None) -> DeltaClient:
        if paper_trading is None:
            return get_client(subaccount)
        return get_client_for_mode(subaccount, paper_trading)


client_factory = ClientFactoryManager()
