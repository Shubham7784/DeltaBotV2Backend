"""Market Data Engine for Delta Bot V2.

Provides:
1. Multi-timeframe historical candlestick synchronization from Delta Exchange API v2.
2. In-memory high-throughput circular buffers (5m, 15m, 1h) for sub-millisecond indicator calculations.
3. Real-time mark price, index price, and funding rate cache.
4. Background polling worker for continuous live market streaming and candle finalization.
"""

import asyncio
import bisect
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.db.repository import TradeRepository
from app.schemas.candle import MarketBufferStats, MarketEngineStatus, MarketSyncResult
from app.services.client_factory import get_client

logger = logging.getLogger("delta_bot.market_data_engine")


@dataclass
class CandleItem:
    """Lightweight in-memory candle representation."""
    symbol: str
    resolution: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    close_time: Optional[int] = None
    is_closed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CandleRingBuffer:
    """Circular sorted in-memory candle buffer with rapid vector slicing.
    
    Guarantees chronological ordering, deduplication by open_time, and O(1)
    or O(log N) operations up to a fixed maximum capacity.
    """

    def __init__(self, symbol: str, resolution: str, max_capacity: int = 300):
        self.symbol = symbol.upper()
        self.resolution = resolution
        self.max_capacity = max_capacity
        self._candles: List[CandleItem] = []
        self._times: List[int] = []  # Parallel sorted list of open_times for binary search

    def __len__(self) -> int:
        return len(self._candles)

    def clear(self) -> None:
        self._candles.clear()
        self._times.clear()

    def upsert(
        self,
        open_time: int,
        open_p: float,
        high_p: float,
        low_p: float,
        close_p: float,
        volume: float = 0.0,
        close_time: Optional[int] = None,
        is_closed: bool = True,
    ) -> CandleItem:
        """Inserts or updates a candle at open_time, maintaining sorted order."""
        item = CandleItem(
            symbol=self.symbol,
            resolution=self.resolution,
            open_time=open_time,
            open=float(open_p),
            high=float(high_p),
            low=float(low_p),
            close=float(close_p),
            volume=float(volume),
            close_time=close_time,
            is_closed=is_closed,
        )

        idx = bisect.bisect_left(self._times, open_time)
        if idx < len(self._times) and self._times[idx] == open_time:
            # Update existing candle
            self._candles[idx] = item
        else:
            # Insert at sorted position
            self._times.insert(idx, open_time)
            self._candles.insert(idx, item)

            # Evict oldest if exceeding capacity
            if len(self._candles) > self.max_capacity:
                self._candles.pop(0)
                self._times.pop(0)

        return item

    def bulk_load(self, items: List[CandleItem]) -> int:
        """Loads a list of candles in bulk and resets the buffer."""
        # Sort by open_time
        sorted_items = sorted(items, key=lambda x: x.open_time)
        # Deduplicate by open_time keeping latest
        deduped: Dict[int, CandleItem] = {c.open_time: c for c in sorted_items}
        final_list = [deduped[t] for t in sorted(deduped.keys())]

        if len(final_list) > self.max_capacity:
            final_list = final_list[-self.max_capacity :]

        self._candles = final_list
        self._times = [c.open_time for c in final_list]
        return len(self._candles)

    @property
    def latest(self) -> Optional[CandleItem]:
        return self._candles[-1] if self._candles else None

    @property
    def oldest(self) -> Optional[CandleItem]:
        return self._candles[0] if self._candles else None

    def get_all(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self._candles]

    def get_closes(self) -> List[float]:
        return [c.close for c in self._candles]

    def get_highs(self) -> List[float]:
        return [c.high for c in self._candles]

    def get_lows(self) -> List[float]:
        return [c.low for c in self._candles]

    def get_opens(self) -> List[float]:
        return [c.open for c in self._candles]

    def get_volumes(self) -> List[float]:
        return [c.volume for c in self._candles]

    def get_timestamps(self) -> List[int]:
        return list(self._times)

    def stats(self) -> MarketBufferStats:
        return MarketBufferStats(
            symbol=self.symbol,
            timeframe=self.resolution,
            candle_count=len(self._candles),
            oldest_time=self.oldest.open_time if self.oldest else None,
            newest_time=self.latest.open_time if self.latest else None,
            last_close=self.latest.close if self.latest else None,
        )


class MarketDataEngine:
    """Core Market Data Engine managing real-time ingestion, circular buffers, and pricing."""

    DEFAULT_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
    DEFAULT_TIMEFRAMES = ["5m", "15m", "1h"]

    def __init__(self):
        self._buffers: Dict[str, Dict[str, CandleRingBuffer]] = {}
        self._live_prices: Dict[str, Dict[str, Any]] = {}
        self._poller_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._last_sync_time: Optional[datetime] = None
        self._poll_interval_sec: int = 20
        self._lock = asyncio.Lock()

        # Initialize empty buffers for default symbols & timeframes
        for symbol in self.DEFAULT_SYMBOLS:
            self._ensure_buffers_exist(symbol)

    def _ensure_buffers_exist(self, symbol: str) -> None:
        sym = symbol.upper()
        if sym not in self._buffers:
            self._buffers[sym] = {}
        for tf in self.DEFAULT_TIMEFRAMES:
            if tf not in self._buffers[sym]:
                self._buffers[sym][tf] = CandleRingBuffer(sym, tf, max_capacity=300)

    def get_buffer(self, symbol: str, resolution: str) -> Optional[CandleRingBuffer]:
        sym = symbol.upper()
        return self._buffers.get(sym, {}).get(resolution)

    def get_all_buffers_for_symbol(self, symbol: str) -> Dict[str, CandleRingBuffer]:
        sym = symbol.upper()
        return self._buffers.get(sym, {})

    def get_live_price(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._live_prices.get(symbol.upper())

    @property
    def tracked_symbols(self) -> List[str]:
        """Returns symbols with initialized market-data buffers."""
        return list(self._buffers.keys())

    # =========================================================================
    # PRELOAD & DATABASE SYNCHRONIZATION
    # =========================================================================

    async def preload_from_db(self, session: AsyncSession) -> int:
        """Preloads in-memory buffers from the SQLite Candle table upon application startup."""
        total_loaded = 0
        async with self._lock:
            for symbol in list(self._buffers.keys()):
                for tf in self.DEFAULT_TIMEFRAMES:
                    candles = await TradeRepository.get_recent_candles(
                        session=session,
                        symbol=symbol,
                        resolution=tf,
                        limit=300,
                    )
                    if candles:
                        items = [
                            CandleItem(
                                symbol=c.symbol,
                                resolution=c.resolution,
                                open_time=c.open_time,
                                open=c.open,
                                high=c.high,
                                low=c.low,
                                close=c.close,
                                volume=c.volume,
                                close_time=c.close_time,
                                is_closed=c.is_closed,
                            )
                            for c in candles
                        ]
                        count = self._buffers[symbol][tf].bulk_load(items)
                        total_loaded += count
                        logger.info(
                            "Preloaded %d candles into buffer for %s (%s)",
                            count,
                            symbol,
                            tf,
                        )
        return total_loaded

    # =========================================================================
    # DELTA EXCHANGE INGESTION & NORMALIZATION
    # =========================================================================

    async def sync_symbol_timeframe(
        self,
        session: AsyncSession,
        symbol: str,
        resolution: str = "5m",
        lookback_hours: int = 24,
    ) -> MarketSyncResult:
        """Pulls candlestick history from Delta Exchange, normalizes data, and updates DB & buffers."""
        sym = symbol.strip().upper()
        self._ensure_buffers_exist(sym)
        buffer = self._buffers[sym][resolution]

        now = int(time.time())
        start_time = now - (lookback_hours * 3600)

        client = get_client("futures")
        try:
            # Fetch candles from Delta Exchange API (auth_required=False)
            response = await asyncio.to_thread(
                client.get_candles,
                symbol=sym,
                resolution=resolution,
                start=start_time,
                end=now,
            )

            raw_candles = response.get("result", [])
            if not isinstance(raw_candles, list):
                raw_candles = []

            # Normalize candles
            normalized_items: List[CandleItem] = []
            for item in raw_candles:
                if not isinstance(item, dict):
                    continue
                open_t = int(item.get("time") or item.get("open_time") or 0)
                if not open_t:
                    continue

                open_p = float(item.get("open", 0.0))
                high_p = float(item.get("high", 0.0))
                low_p = float(item.get("low", 0.0))
                close_p = float(item.get("close", 0.0))
                volume = float(item.get("volume", 0.0))
                close_t = item.get("close_time")

                # Upsert into database
                await TradeRepository.save_candle(
                    session=session,
                    symbol=sym,
                    resolution=resolution,
                    open_time=open_t,
                    open_p=open_p,
                    high_p=high_p,
                    low_p=low_p,
                    close_p=close_p,
                    volume=volume,
                    close_time=int(close_t) if close_t else None,
                    is_closed=True,
                )

                # Upsert into in-memory ring buffer
                c_item = buffer.upsert(
                    open_time=open_t,
                    open_p=open_p,
                    high_p=high_p,
                    low_p=low_p,
                    close_p=close_p,
                    volume=volume,
                    close_time=int(close_t) if close_t else None,
                    is_closed=True,
                )
                normalized_items.append(c_item)

            await session.commit()
            self._last_sync_time = datetime.now(timezone.utc)

            latest_ts = buffer.latest.open_time if buffer.latest else None
            logger.info(
                "Successfully synchronized %d candles for %s (%s)",
                len(normalized_items),
                sym,
                resolution,
            )
            return MarketSyncResult(
                symbol=sym,
                timeframe=resolution,
                synced_count=len(normalized_items),
                latest_timestamp=latest_ts,
                status="ok",
            )

        except Exception as e:
            await session.rollback()
            logger.error("Failed to sync candles for %s (%s): %s", sym, resolution, e)
            return MarketSyncResult(
                symbol=sym,
                timeframe=resolution,
                synced_count=0,
                status="error",
                error=str(e),
            )

    async def sync_all(
        self,
        session: AsyncSession,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        lookback_hours: int = 24,
    ) -> List[MarketSyncResult]:
        """Synchronizes multi-timeframe candle datasets for all designated symbols."""
        sym_list = symbols or self.DEFAULT_SYMBOLS
        tf_list = timeframes or self.DEFAULT_TIMEFRAMES

        results: List[MarketSyncResult] = []
        for sym in sym_list:
            for tf in tf_list:
                res = await self.sync_symbol_timeframe(
                    session=session,
                    symbol=sym,
                    resolution=tf,
                    lookback_hours=lookback_hours,
                )
                results.append(res)
        return results

    # =========================================================================
    # REAL-TIME PRICE & TICKER CACHE
    # =========================================================================

    async def update_live_prices(self) -> Dict[str, Dict[str, Any]]:
        """Refreshes spot/mark prices and 24h ticker metadata for tracked symbols."""
        client = get_client("futures")
        updated: Dict[str, Dict[str, Any]] = {}

        for sym in list(self._buffers.keys()):
            try:
                ticker_data = await asyncio.to_thread(client.get_ticker_by_symbol, sym)
                res = ticker_data.get("result")
                if res and isinstance(res, dict):
                    mark_p = float(res.get("mark_price") or res.get("close") or 0.0)
                    spot_p = float(res.get("spot_price") or mark_p)
                    self._live_prices[sym] = {
                        "symbol": sym,
                        "mark_price": mark_p,
                        "spot_price": spot_p,
                        "turnover_symbol": res.get("turnover_symbol"),
                        "volume_24h": float(res.get("volume") or 0.0),
                        "high_24h": float(res.get("high") or 0.0),
                        "low_24h": float(res.get("low") or 0.0),
                        "funding_rate": float(res.get("funding_rate") or 0.0),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    updated[sym] = self._live_prices[sym]
            except Exception as err:
                logger.debug("Could not fetch ticker for %s: %s", sym, err)

        return updated

    # =========================================================================
    # BACKGROUND POLLER WORKER
    # =========================================================================

    async def _poller_loop(self) -> None:
        """Background continuous loop updating mark prices and fresh candles."""
        logger.info(
            "Market Data Engine background poller started (interval=%ds)",
            self._poll_interval_sec,
        )
        while self._is_running:
            try:
                # 1. Update live ticker prices
                await self.update_live_prices()

                # 2. Synchronize the most recent candle window (last 2 hours)
                async with AsyncSessionLocal() as session:
                    for sym in list(self._buffers.keys()):
                        for tf in self.DEFAULT_TIMEFRAMES:
                            await self.sync_symbol_timeframe(
                                session=session,
                                symbol=sym,
                                resolution=tf,
                                lookback_hours=2,
                            )

            except asyncio.CancelledError:
                logger.info("Market Data Engine background poller cancelled")
                break
            except Exception as e:
                logger.error("Error in Market Data Engine poller loop: %s", e)

            # Sleep until next poll interval
            try:
                await asyncio.sleep(self._poll_interval_sec)
            except asyncio.CancelledError:
                break

    def start_background_poller(self, interval_sec: int = 20) -> None:
        """Launches the background polling task if not already active."""
        if self._is_running:
            return
        self._poll_interval_sec = interval_sec
        self._is_running = True
        self._poller_task = asyncio.create_task(self._poller_loop())

    async def stop_background_poller(self) -> None:
        """Gracefully cancels and awaits the background poller task."""
        self._is_running = False
        if self._poller_task and not self._poller_task.done():
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
        self._poller_task = None
        logger.info("Market Data Engine background poller stopped")

    # =========================================================================
    # STATUS & DIAGNOSTICS
    # =========================================================================

    def get_status(self) -> MarketEngineStatus:
        """Generates a snapshot of the market engine state and buffer diagnostics."""
        buffer_stats: List[MarketBufferStats] = []
        for sym, tfs in self._buffers.items():
            for tf, buf in tfs.items():
                buffer_stats.append(buf.stats())

        return MarketEngineStatus(
            is_running=self._is_running,
            poller_active=self._poller_task is not None and not self._poller_task.done(),
            poll_interval_sec=self._poll_interval_sec,
            tracked_symbols=list(self._buffers.keys()),
            timeframes=self.DEFAULT_TIMEFRAMES,
            buffer_stats=buffer_stats,
            live_prices=self._live_prices,
            last_sync_time=self._last_sync_time.isoformat() if self._last_sync_time else None,
        )


# Global singleton instance for the application
market_engine = MarketDataEngine()
