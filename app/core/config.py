"""Configuration management for Delta Bot V2 using Pydantic Settings."""

from pathlib import Path
from typing import List, Optional
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Application Settings
    APP_NAME: str = "Delta Bot V2"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = Field(default="development", description="development, testnet, or production")
    DEBUG: bool = Field(default=True)
    BACKEND_PORT: int = Field(default=8000)
    HOST: str = Field(default="127.0.0.1")
    FRONTEND_ORIGIN: Optional[str] = Field(
        default=None,
        description="Allowed frontend origin for cross-origin browser requests",
    )

    # Database Configuration
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/delta_bot.db",
        description="SQLAlchemy database connection URL. Set DATABASE_URL in .env for Neon PostgreSQL.",
    )

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Ensure PostgreSQL URLs use SQLAlchemy's asyncpg driver."""
        if isinstance(value, str):
            if value.startswith("postgres://"):
                return "postgresql+asyncpg://" + value[len("postgres://"):]
            if value.startswith("postgresql://"):
                return "postgresql+asyncpg://" + value[len("postgresql://"):]
        return value

    @field_validator("DEBUG", mode="before")
    @classmethod
    def normalize_debug_mode(cls, value):
        """Accept common deployment labels used by process managers for DEBUG."""
        if isinstance(value, str) and value.strip().lower() in {"release", "production", "prod"}:
            return False
        return value

    # Trading Domains Configuration
    # Delta Exchange API Gateway Endpoints
    DELTA_PROD_REST_URL: str = "https://api.india.delta.exchange"
    DELTA_TESTNET_REST_URL: str = "https://cdn-ind.testnet.deltaex.org"
    DELTA_PROD_WS_PUBLIC: str = "wss://public-socket.india.delta.exchange"
    DELTA_PROD_WS_PRIVATE: str = "wss://socket.india.delta.exchange"
    DELTA_TESTNET_WS_PUBLIC: str = "wss://socket-ind-pub.testnet.deltaex.org"
    DELTA_TESTNET_WS_PRIVATE: str = "wss://socket-ind.testnet.deltaex.org"

    # Custom override if needed
    DELTA_CUSTOM_BASE_URL: Optional[str] = Field(
        default=None,
        description="Custom Delta API base URL",
        validation_alias=AliasChoices("DELTA_CUSTOM_BASE_URL", "DELTA_API_BASE_URL"),
    )

    # Futures Subaccount
    DELTA_FUTURES_API_KEY: Optional[str] = Field(default=None, description="Delta Exchange Futures Subaccount Key")
    DELTA_FUTURES_API_SECRET: Optional[str] = Field(default=None, description="Delta Exchange Futures Subaccount Secret")

    # Options Subaccount
    DELTA_OPTIONS_API_KEY: Optional[str] = Field(default=None, description="Delta Exchange Options Subaccount Key")
    DELTA_OPTIONS_API_SECRET: Optional[str] = Field(default=None, description="Delta Exchange Options Subaccount Secret")

    # Capital & Risk Parameters (designed for initial ₹1,000 / ~$12 wallet)
    INITIAL_WALLET_INR: float = Field(default=1000.0, description="Approximate initial capital in INR")
    INITIAL_WALLET_USD: float = Field(default=12.0, description="Approximate initial capital in USD")
    MAX_EXPOSURE_PCT: float = Field(default=50.0, description="Maximum total wallet capital exposure %")
    SAFETY_RESERVE_PCT: float = Field(default=30.0, description="Reserved untouched capital buffer %")

    # Order Defaults
    DEFAULT_LEVERAGE: int = Field(default=50, description="Default leverage for futures contracts")
    BTC_ETH_FUTURES_LEVERAGE: int = Field(default=100, ge=1, description="Leverage for BTCUSD and ETHUSD futures")
    OPTIONS_LEVERAGE: int = Field(default=50, ge=1, description="Leverage for options account trades")
    DEFAULT_TP_PCT: float = Field(default=4.0, description="Default Take Profit percentage")
    DEFAULT_SL_PCT: float = Field(default=1.0, description="Default Stop Loss percentage")

    # Instruments & Timeframes
    INITIAL_INSTRUMENTS: List[str] = ["BTC", "ETH", "GOLD"]
    SUPPORTED_TIMEFRAMES: List[str] = ["5m", "15m", "1h"]

    # Operational Modes
    PAPER_TRADING: bool = Field(default=True, description="True for simulated execution without real exchange risk")
    BOT_ACTIVE: bool = Field(default=True, description="Global master bot toggle: BOT ON / BOT OFF")

    def get_delta_base_url(self) -> str:
        """Resolves active Delta Exchange REST API base URL."""
        if self.DELTA_CUSTOM_BASE_URL:
            return self.DELTA_CUSTOM_BASE_URL.rstrip("/")
        if self.ENVIRONMENT.lower() == "production":
            return self.DELTA_PROD_REST_URL
        return self.DELTA_TESTNET_REST_URL

    def safe_summary(self) -> dict:
        """Returns safe configuration info without exposing sensitive secrets."""
        return {
            "app_name": self.APP_NAME,
            "version": self.APP_VERSION,
            "environment": self.ENVIRONMENT,
            "paper_trading": self.PAPER_TRADING,
            "bot_active": self.BOT_ACTIVE,
            "delta_base_url": self.get_delta_base_url(),
            "initial_wallet_inr": self.INITIAL_WALLET_INR,
            "initial_wallet_usd": self.INITIAL_WALLET_USD,
            "default_leverage": self.DEFAULT_LEVERAGE,
            "btc_eth_futures_leverage": self.BTC_ETH_FUTURES_LEVERAGE,
            "options_leverage": self.OPTIONS_LEVERAGE,
            "default_tp_pct": self.DEFAULT_TP_PCT,
            "default_sl_pct": self.DEFAULT_SL_PCT,
            "initial_instruments": self.INITIAL_INSTRUMENTS,
            "supported_timeframes": self.SUPPORTED_TIMEFRAMES,
            "credentials": {
                "futures_configured": bool(self.DELTA_FUTURES_API_KEY and self.DELTA_FUTURES_API_SECRET),
                "options_configured": bool(self.DELTA_OPTIONS_API_KEY and self.DELTA_OPTIONS_API_SECRET),
            },
        }


settings = Settings()
