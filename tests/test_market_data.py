"""Unit and integration tests for Market Data Engine (Phase 4).

Tests:
1. CandleRingBuffer circular eviction, deduplication, chronological sorting, and vector slices.
2. MarketDataEngine synchronization, database persistence, and buffer caching.
3. Market Data REST API endpoints (/status, /sync, /buffers, /live-feed, /poller).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.session import AsyncSessionLocal, init_db
from app.services.market_data_engine import CandleRingBuffer, market_engine


def test_ring_buffer_basics_and_eviction():
    """Verify CandleRingBuffer maintains capacity, chronological ordering, and vector slices."""
    buf = CandleRingBuffer(symbol="BTCUSD", resolution="5m", max_capacity=5)
    assert len(buf) == 0
    assert buf.latest is None

    # Insert 5 candles in order
    base_time = 1700000000
    for i in range(5):
        buf.upsert(
            open_time=base_time + (i * 300),
            open_p=100.0 + i,
            high_p=110.0 + i,
            low_p=90.0 + i,
            close_p=105.0 + i,
            volume=50.0 + i,
        )

    assert len(buf) == 5
    assert buf.oldest.open_time == base_time
    assert buf.latest.open_time == base_time + (4 * 300)
    assert buf.get_closes() == [105.0, 106.0, 107.0, 108.0, 109.0]
    assert buf.get_highs() == [110.0, 111.0, 112.0, 113.0, 114.0]

    # Update existing candle without changing count
    buf.upsert(
        open_time=base_time + (4 * 300),
        open_p=104.0,
        high_p=114.0,
        low_p=94.0,
        close_p=115.0,  # updated close
        volume=80.0,
    )
    assert len(buf) == 5
    assert buf.latest.close == 115.0

    # Insert 6th candle -> should evict oldest (base_time)
    buf.upsert(
        open_time=base_time + (5 * 300),
        open_p=120.0,
        high_p=130.0,
        low_p=115.0,
        close_p=125.0,
        volume=99.0,
    )
    assert len(buf) == 5
    assert buf.oldest.open_time == base_time + 300
    assert buf.latest.open_time == base_time + (5 * 300)
    assert buf.get_timestamps() == [base_time + (i * 300) for i in range(1, 6)]


def test_ring_buffer_out_of_order_insertion():
    """Verify out-of-order candles are sorted correctly in the ring buffer."""
    buf = CandleRingBuffer(symbol="ETHUSD", resolution="15m", max_capacity=10)

    # Insert t=300, then t=100, then t=200
    buf.upsert(open_time=300, open_p=30, high_p=35, low_p=25, close_p=32)
    buf.upsert(open_time=100, open_p=10, high_p=15, low_p=5, close_p=12)
    buf.upsert(open_time=200, open_p=20, high_p=25, low_p=15, close_p=22)

    assert len(buf) == 3
    assert buf.get_timestamps() == [100, 200, 300]
    assert buf.get_closes() == [12.0, 22.0, 32.0]


@pytest.mark.asyncio
async def test_market_engine_sync_and_persistence():
    """Verify market_engine synchronizes candles, stores in DB, and fills ring buffer."""
    await init_db()
    async with AsyncSessionLocal() as session:
        result = await market_engine.sync_symbol_timeframe(
            session=session,
            symbol="BTCUSD",
            resolution="5m",
            lookback_hours=3,
        )
        assert result.symbol == "BTCUSD"
        assert result.timeframe == "5m"
        assert result.status == "ok"
        assert result.synced_count > 0

        buf = market_engine.get_buffer("BTCUSD", "5m")
        assert buf is not None
        assert len(buf) > 0
        assert buf.latest is not None
        assert buf.latest.close > 0


@pytest.mark.asyncio
async def test_market_api_status_and_live_feed():
    """Verify GET /api/v1/market/status and /live-feed return structured data."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Status
        res = await ac.get("/api/v1/market/status")
        assert res.status_code == 200
        data = res.json()
        assert "tracked_symbols" in data
        assert "BTCUSD" in data["tracked_symbols"]
        assert "buffer_stats" in data
        assert isinstance(data["buffer_stats"], list)

        # 2. Live feed
        feed_res = await ac.get("/api/v1/market/live-feed")
        assert feed_res.status_code == 200
        feed_data = feed_res.json()
        assert "symbols" in feed_data
        assert "BTCUSD" in feed_data["symbols"]


@pytest.mark.asyncio
async def test_market_api_sync_and_buffer_query():
    """Verify POST /api/v1/market/sync and GET /api/v1/market/buffers/{symbol}."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        sync_res = await ac.post(
            "/api/v1/market/sync",
            json={
                "symbols": ["BTCUSD"],
                "timeframes": ["5m"],
                "lookback_hours": 2,
            },
        )
        assert sync_res.status_code == 200
        sync_data = sync_res.json()
        assert len(sync_data) == 1
        assert sync_data[0]["symbol"] == "BTCUSD"
        assert sync_data[0]["status"] == "ok"

        # Query single timeframe buffer
        buf_res = await ac.get("/api/v1/market/buffers/BTCUSD?resolution=5m")
        assert buf_res.status_code == 200
        buf_data = buf_res.json()
        assert buf_data["symbol"] == "BTCUSD"
        assert buf_data["resolution"] == "5m"
        assert buf_data["count"] > 0
        assert len(buf_data["candles"]) > 0

        # Query all timeframes for symbol
        all_buf_res = await ac.get("/api/v1/market/buffers/BTCUSD")
        assert all_buf_res.status_code == 200
        all_data = all_buf_res.json()
        assert "timeframes" in all_data
        assert "5m" in all_data["timeframes"]


@pytest.mark.asyncio
async def test_market_api_poller_toggle():
    """Verify background poller start and stop endpoints."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_res = await ac.post("/api/v1/market/poller/start?interval_sec=25")
        assert start_res.status_code == 200
        assert start_res.json()["status"] == "started"

        stop_res = await ac.post("/api/v1/market/poller/stop")
        assert stop_res.status_code == 200
        assert stop_res.json()["status"] == "stopped"
