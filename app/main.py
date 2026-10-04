"""FastAPI main application entry point for Delta Bot V2.

FastAPI backend for Delta Exchange API v2.
"""

from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.logging import setup_logging
from app.api.api_router import api_router
from app.db.session import AsyncSessionLocal, init_db
from app.services.market_data_engine import market_engine
from app.services.strategy_engine import strategy_engine

logger = setup_logging(debug=False)

async def strategy_evaluation_loop() -> None:
    """Continuously evaluate strategies after the market-data poller refreshes buffers."""
    while True:
        try:
            await asyncio.sleep(25)
            results = await strategy_engine.evaluate_all(persist=True)
            signal_count = sum(len(signals) for signals in results.values())
            if signal_count:
                logger.info("Strategy cycle generated %d candidate signal(s)", signal_count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Strategy evaluation cycle failed: %s", exc, exc_info=True)
            await asyncio.sleep(20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown routines."""
    logger.info("Initializing %s v%s on FastAPI", settings.APP_NAME, settings.APP_VERSION)
    logger.info("Environment: %s | Paper Mode: %s", settings.ENVIRONMENT, settings.PAPER_TRADING)
    logger.info("Delta REST endpoint: %s", settings.get_delta_base_url())
    logger.info(
        "Dual Subaccounts Configured: Futures=%s, Options=%s",
        bool(settings.DELTA_FUTURES_API_KEY),
        bool(settings.DELTA_OPTIONS_API_KEY),
    )
    # Initialize SQLite/SQLAlchemy schema and seed instruments
    try:
        await init_db()
        logger.info("Database schemas initialized and baseline instruments seeded successfully")
    except Exception as e:
        logger.error("Failed to initialize database schema: %s", e)

    # Synchronize Strategy Engine with DB
    try:
        await strategy_engine.initialize_db_strategies()
        logger.info("Strategy Engine synchronized with database")
    except Exception as e:
        logger.warning("Could not sync strategy engine with DB: %s", e)

    # Preload market data into in-memory ring buffers
    try:
        async with AsyncSessionLocal() as session:
            count = await market_engine.preload_from_db(session)
            logger.info("Preloaded %d cached candles into MarketDataEngine ring buffers", count)
    except Exception as e:
        logger.warning("Could not preload candles from DB: %s", e)

    # Start background market poller (every 20s)
    market_engine.start_background_poller(interval_sec=20)
    strategy_task = asyncio.create_task(strategy_evaluation_loop())

    try:
        yield
    finally:
        strategy_task.cancel()
        try:
            await strategy_task
        except asyncio.CancelledError:
            pass

        # Clean shutdown of market poller
        try:
            await market_engine.stop_background_poller()
        except Exception as e:
            logger.warning("Error stopping market engine poller: %s", e)
        logger.info("Shutting down %s cleanly", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Automated trading platform for Delta Exchange (Futures & Options) powered by FastAPI",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN.rstrip("/")] if settings.FRONTEND_ORIGIN else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router, prefix="/api/v1")

@app.get("/", include_in_schema=False)
async def serve_root():
    """Returns backend service metadata."""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "server": "FastAPI",
        "status": "online",
        "api_docs": "/docs",
        "health": "/api/v1/health",
        "system_status": "/api/v1/status",
    }
