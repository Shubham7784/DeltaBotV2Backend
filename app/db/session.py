"""Database session and connection management for Delta Bot V2."""

import logging
from pathlib import Path
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger("delta_bot.db")


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models."""
    pass


# Ensure data directory exists if using local SQLite database
if "sqlite" in settings.DATABASE_URL:
    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    parent_dir = Path(db_path).parent
    if str(parent_dir) not in (".", ""):
        parent_dir.mkdir(parents=True, exist_ok=True)

# Convert libpq-style Neon query parameters to asyncpg connection arguments.
database_url = make_url(settings.DATABASE_URL)
connect_args = {}
if database_url.drivername == "postgresql+asyncpg":
    sslmode = database_url.query.get("sslmode")
    if sslmode:
        connect_args["ssl"] = sslmode
    database_url = database_url.difference_update_query(["sslmode", "channel_binding"])

# Create asynchronous SQLAlchemy engine
engine = create_async_engine(
    database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else connect_args,
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Alias for services and tests
get_db_session = AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an isolated asynchronous database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def ensure_legacy_schema_columns() -> None:
    """Backfills legacy/partial DB schemas with required foreign-key columns used by the current models."""
    if "postgres" not in settings.DATABASE_URL:
        return

    required_tables = {
        "orders": ["trade_decision_id"],
        "positions": ["trade_decision_id"],
    }

    async with engine.begin() as conn:
        for table_name, columns in required_tables.items():
            for column_name in columns:
                result = await conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = :table_name AND column_name = :column_name
                        """
                    ),
                    {"table_name": table_name, "column_name": column_name},
                )
                if result.scalar() is None:
                    logger.warning("Adding missing DB column %s.%s to repair schema compatibility", table_name, column_name)
                    await conn.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER")
                    )
                    await conn.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS ix_{table_name}_{column_name} ON {table_name} ({column_name})"
                        )
                    )


async def init_db():
    """Initializes the database schema and seeds initial instruments."""
    from app.db.models import Instrument  # import to ensure models are registered on Base
    from sqlalchemy import select

    logger.info("Initializing database tables on %s", database_url.render_as_string(hide_password=True))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await ensure_legacy_schema_columns()

    # Seed baseline instruments if table is empty
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Instrument))
        existing = result.scalars().first()
        if not existing:
            logger.info("Seeding baseline instruments (BTCUSD, ETHUSD, GOLDUSD)...")
            default_instruments = [
                Instrument(
                    symbol="BTCUSD",
                    product_id=27,
                    contract_type="perpetual_futures",
                    underlying_asset="BTC",
                    tick_size=0.5,
                    contract_value=0.001,
                    is_active=True,
                ),
                Instrument(
                    symbol="ETHUSD",
                    product_id=1394,
                    contract_type="perpetual_futures",
                    underlying_asset="ETH",
                    tick_size=0.05,
                    contract_value=0.01,
                    is_active=True,
                ),
                Instrument(
                    symbol="GOLDUSD",
                    product_id=3145,
                    contract_type="perpetual_futures",
                    underlying_asset="GOLD",
                    tick_size=0.1,
                    contract_value=0.01,
                    is_active=True,
                ),
            ]
            session.add_all(default_instruments)
            await session.commit()
            logger.info("Seeded %d default instruments", len(default_instruments))
