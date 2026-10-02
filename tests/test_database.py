"""Unit tests for Delta Bot V2 Database & Domain Models (Phase 3)."""

from contextlib import asynccontextmanager
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.enums import (
    AuditEventType,
    AuditSeverity,
    ExitReason,
    TradeLifecycleState,
)
from app.db.repository import TradeRepository
from app.db.session import Base, get_db
from app.main import app

# Isolated in-memory SQLite engine for tests
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@asynccontextmanager
async def get_test_context():
    """Provides an isolated test database and session."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@asynccontextmanager
async def get_test_client():
    """Provides an AsyncClient connected to an in-memory test database."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# =============================================================================
# REPOSITORY UNIT TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_instrument_crud():
    """Verifies instrument upsert and querying."""
    async with get_test_context() as (session, _):
        inst = await TradeRepository.upsert_instrument(
            session=session,
            symbol="SOLUSD",
            product_id=888,
            contract_type="perpetual_futures",
            underlying_asset="SOL",
            tick_size=0.01,
            contract_value=0.1,
        )
        assert inst.symbol == "SOLUSD"
        assert inst.product_id == 888

        fetched = await TradeRepository.get_instrument_by_symbol(session, "SOLUSD")
        assert fetched is not None
        assert fetched.underlying_asset == "SOL"

        all_active = await TradeRepository.get_instruments(session, active_only=True)
        assert len(all_active) == 1
        assert all_active[0].symbol == "SOLUSD"


@pytest.mark.asyncio
async def test_candle_storage_and_ordering():
    """Verifies candlestick deduplication and ascending chronological ordering."""
    async with get_test_context() as (session, _):
        await TradeRepository.save_candle(
            session=session,
            symbol="BTCUSD",
            resolution="5m",
            open_time=1700000000,
            open_p=65000.0,
            high_p=65100.0,
            low_p=64950.0,
            close_p=65050.0,
            volume=12.5,
        )
        await TradeRepository.save_candle(
            session=session,
            symbol="BTCUSD",
            resolution="5m",
            open_time=1700000300,
            open_p=65050.0,
            high_p=65200.0,
            low_p=65000.0,
            close_p=65150.0,
            volume=18.2,
        )
        # Update existing candle (idempotent upsert)
        await TradeRepository.save_candle(
            session=session,
            symbol="BTCUSD",
            resolution="5m",
            open_time=1700000300,
            open_p=65050.0,
            high_p=65250.0,  # updated high
            low_p=65000.0,
            close_p=65220.0,
            volume=20.0,
        )

        candles = await TradeRepository.get_recent_candles(session, symbol="BTCUSD", resolution="5m")
        assert len(candles) == 2
        assert candles[0].open_time < candles[1].open_time
        assert candles[1].high == 65250.0


@pytest.mark.asyncio
async def test_7_state_trade_lifecycle():
    """Verifies trade progression across all 7 lifecycle states and single-position invariant."""
    async with get_test_context() as (session, _):
        # 1. State: SIGNAL_GENERATED
        trade = await TradeRepository.create_trade_decision(
            session=session,
            symbol="BTCUSD",
            direction="long",
            entry_price=64000.0,
            take_profit_price=66560.0,  # 4%
            stop_loss_price=63360.0,    # 1%
            suggested_size=2,
        )
        assert trade.id is not None
        assert trade.state == TradeLifecycleState.SIGNAL_GENERATED.value

        # INVARIANT TEST: Attempting to create another active trade on BTCUSD must fail
        with pytest.raises(ValueError) as exc:
            await TradeRepository.create_trade_decision(
                session=session,
                symbol="BTCUSD",
                direction="short",
            )
        assert "Invariant violation" in str(exc.value)

        # 2. State: VALIDATING
        trade = await TradeRepository.transition_trade_state(
            session=session,
            decision_id=trade.id,
            target_state=TradeLifecycleState.VALIDATING,
            notes="Checking multi-timeframe 1h/15m/5m trend alignment",
        )
        assert trade.state == TradeLifecycleState.VALIDATING.value

        # 3. State: LLM_CONFIRMED
        trade = await TradeRepository.transition_trade_state(
            session=session,
            decision_id=trade.id,
            target_state=TradeLifecycleState.LLM_CONFIRMED,
            llm_approved=True,
            llm_confidence=0.88,
            llm_reasoning="Strong momentum above EMA9 on 15m and 5m; clear support zone.",
        )
        assert trade.state == TradeLifecycleState.LLM_CONFIRMED.value
        assert trade.llm_approved is True
        assert trade.llm_confidence == 0.88

        # 4. State: RISK_VALIDATED
        trade = await TradeRepository.transition_trade_state(
            session=session,
            decision_id=trade.id,
            target_state=TradeLifecycleState.RISK_VALIDATED,
            risk_approved=True,
            risk_notes="Risk parameters valid: sizing 2 contracts <= 50% max exposure limit.",
        )
        assert trade.state == TradeLifecycleState.RISK_VALIDATED.value
        assert trade.risk_approved is True

        # 5. State: ORDER_SUBMITTED
        trade = await TradeRepository.transition_trade_state(
            session=session,
            decision_id=trade.id,
            target_state=TradeLifecycleState.ORDER_SUBMITTED,
            notes="Bracket limit order placed on Delta Exchange",
        )
        assert trade.state == TradeLifecycleState.ORDER_SUBMITTED.value

        # 6. State: POSITION_OPEN
        trade = await TradeRepository.transition_trade_state(
            session=session,
            decision_id=trade.id,
            target_state=TradeLifecycleState.POSITION_OPEN,
            entry_price=64010.0,
        )
        assert trade.state == TradeLifecycleState.POSITION_OPEN.value
        assert trade.opened_at is not None

        # 7. State: EXITED
        trade = await TradeRepository.transition_trade_state(
            session=session,
            decision_id=trade.id,
            target_state=TradeLifecycleState.EXITED,
            exit_price=66560.0,
            exit_reason=ExitReason.TAKE_PROFIT.value,
            realized_pnl=5.10,
        )
        assert trade.state == TradeLifecycleState.EXITED.value
        assert trade.closed_at is not None
        assert trade.exit_reason == "take_profit"
        assert trade.realized_pnl == 5.10

        # Once EXITED, a new trade on BTCUSD CAN now be opened (invariant cleared)
        new_trade = await TradeRepository.create_trade_decision(
            session=session,
            symbol="BTCUSD",
            direction="short",
        )
        assert new_trade.id != trade.id
        assert new_trade.state == TradeLifecycleState.SIGNAL_GENERATED.value


@pytest.mark.asyncio
async def test_audit_trail_logging():
    """Verifies invariant and event audit logging."""
    async with get_test_context() as (session, _):
        await TradeRepository.create_audit_log(
            session=session,
            event_type=AuditEventType.RISK_CHECK,
            severity=AuditSeverity.WARNING,
            message="Margin buffer close to 30% reserve threshold",
            symbol="ETHUSD",
            payload={"available_margin": 320.5, "threshold": 300.0},
        )

        logs = await TradeRepository.get_audit_logs(session, severity="WARNING")
        assert len(logs) == 1
        assert logs[0].event_type == "RISK_CHECK"
        assert logs[0].symbol == "ETHUSD"
        assert logs[0].payload["threshold"] == 300.0


# =============================================================================
# FASTAPI ENDPOINT INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_api_instruments_and_db_stats():
    """Tests instrument management and database stats API endpoints."""
    async with get_test_client() as client:
        # 1. Create instrument
        create_res = await client.post(
            "/api/v1/instruments",
            json={
                "symbol": "ETHUSD",
                "product_id": 1394,
                "contract_type": "perpetual_futures",
                "underlying_asset": "ETH",
                "tick_size": 0.05,
                "contract_value": 0.01,
                "is_active": True,
            },
        )
        assert create_res.status_code == 201
        inst_data = create_res.json()
        assert inst_data["symbol"] == "ETHUSD"

        # 2. List instruments
        list_res = await client.get("/api/v1/instruments")
        assert list_res.status_code == 200
        instruments = list_res.json()
        assert len(instruments) >= 1

        # 3. Check DB stats endpoint
        stats_res = await client.get("/api/v1/database/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()
        assert stats["status"] == "healthy"
        assert stats["counts"]["instruments"] >= 1


@pytest.mark.asyncio
async def test_api_trades_full_lifecycle():
    """Tests the full API workflow for trade lifecycle state transitions."""
    async with get_test_client() as client:
        # 1. Post new Trade
        create_res = await client.post(
            "/api/v1/trades",
            json={
                "symbol": "BTCUSD",
                "direction": "long",
                "entry_price": 62000.0,
                "take_profit_price": 64480.0,
                "stop_loss_price": 61380.0,
                "leverage": 50,
                "suggested_size": 1,
            },
        )
        assert create_res.status_code == 201
        trade = create_res.json()
        trade_id = trade["id"]
        assert trade["state"] == "SIGNAL_GENERATED"

        # 2. Attempt duplicate trade on same symbol -> 409 Conflict
        dup_res = await client.post(
            "/api/v1/trades",
            json={"symbol": "BTCUSD", "direction": "long"},
        )
        assert dup_res.status_code == 409

        # 3. Transition to LLM_CONFIRMED
        patch_res = await client.patch(
            f"/api/v1/trades/{trade_id}/state",
            json={
                "state": "LLM_CONFIRMED",
                "llm_approved": True,
                "llm_confidence": 0.92,
                "llm_reasoning": "High timeframe 1h trend alignment confirmed.",
            },
        )
        assert patch_res.status_code == 200
        updated = patch_res.json()
        assert updated["state"] == "LLM_CONFIRMED"
        assert updated["llm_approved"] is True

        # 4. Transition to EXITED
        exit_res = await client.patch(
            f"/api/v1/trades/{trade_id}/state",
            json={
                "state": "EXITED",
                "exit_price": 64500.0,
                "exit_reason": "take_profit",
                "realized_pnl": 4.02,
            },
        )
        assert exit_res.status_code == 200
        final = exit_res.json()
        assert final["state"] == "EXITED"
        assert final["realized_pnl"] == 4.02

        # 5. Query trades list
        list_trades = await client.get("/api/v1/trades?symbol=BTCUSD")
        assert list_trades.status_code == 200
        assert len(list_trades.json()) == 1

        # 6. Verify audit trail was created
        audit_res = await client.get("/api/v1/audit-logs")
        assert audit_res.status_code == 200
        logs = audit_res.json()
        assert len(logs) >= 2  # State transitions logged automatically
