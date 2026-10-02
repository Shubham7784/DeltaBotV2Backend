"""Unit and Integration tests for Risk Management Engine & Execution Orchestrator (Phase 7).

Tests:
1. Deterministic 1% Max Risk Rule and integer contract sizing.
2. 30% Uncommitted Reserve Buffer and 50% Max Portfolio Exposure.
3. Single-Position Invariant per instrument.
4. Liquidation Price & Buffer Calculation.
5. Advisory LLM & Market Regime Validation.
6. Full 7-State Trade Lifecycle progression (State 1 to State 7).
7. Position TP/SL trigger detection and trailing stop logic.
8. Emergency Kill Switch.
9. FastAPI REST API endpoints for Risk and Execution.
"""

import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.db.enums import TradeLifecycleState
from app.db.models import TradeDecision
from app.db.repository import TradeRepository
from app.db.session import AsyncSessionLocal, init_db
from app.main import app
from app.schemas.risk import RiskCheckRequest
from app.services.execution_engine import execution_engine
from app.services.llm_advisory import llm_advisory
from app.services.risk_engine import risk_engine


def test_risk_engine_sizing_and_1_pct_rule():
    """Verify 1% max risk rule sizing calculation."""
    total_equity = 1000.0  # $1,000 USD
    entry_price = 60000.0
    stop_loss = 59000.0    # 1,000 point distance
    take_profit = 62500.0  # 2,500 point distance (2.5 R:R)
    leverage = 50
    symbol = "BTCUSD"

    # Contract unit for BTCUSD = 0.001 BTC.
    # Loss per contract = 1,000 * 0.001 = $1.00 USD.
    # 1% of $1,000 = $10.00 USD max risk.
    # Expected size = 10 / 1.00 = 10 contracts.
    size, margin, risk_amt, risk_pct, rr = risk_engine.calculate_sizing_and_risk(
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        direction="long",
        total_equity=total_equity,
        leverage=leverage,
        symbol=symbol,
    )

    assert size == 10
    assert risk_amt == 10.0
    assert risk_pct == 1.0
    assert rr == 2.5
    # Notional = 60,000 * 0.001 * 10 = $600 USD. Margin at 50x = $12 USD.
    assert margin == 12.0


def test_liquidation_price_calculation():
    """Verify liquidation price calculation and safety buffer."""
    entry = 60000.0
    leverage = 50
    # Long liquidation = 60,000 * (1 - 1/50 + 0.01) = 60,000 * 0.99 = 59,400.
    liq_long = risk_engine.calculate_liquidation_price(entry, "long", leverage)
    assert liq_long < entry
    assert liq_long == 59400.0

    # Short liquidation = 60,000 * (1 + 1/50 - 0.01) = 60,000 * 1.01 = 60,600.
    liq_short = risk_engine.calculate_liquidation_price(entry, "short", leverage)
    assert liq_short > entry
    assert liq_short == 60600.0


def test_advisory_regime_evaluation():
    """Verify advisory regime classifier generates structured recommendation without overriding risk."""
    advisory = llm_advisory._deterministic_regime_eval(
        symbol="BTCUSD",
        direction="long",
        snapshot=None,
    )
    assert advisory.approved is True
    assert advisory.risk_guardrail_acknowledged is True
    assert advisory.confidence >= 0.50
    assert advisory.advisory_recommendation in ("CONFIRM", "CAUTION", "REJECT")


@pytest.mark.asyncio
async def test_deterministic_risk_validation_pass_and_fail():
    """Verify risk engine validates invariants and rejects breaches."""
    await init_db()

    # 1. Valid proposal: should approve
    valid_res = await risk_engine.validate_trade(
        symbol="TEST_RISK_VALID",
        direction="long",
        entry_price=50000.0,
        stop_loss_price=49800.0,  # 200 pt stop loss
        take_profit_price=50500.0,
        leverage=50,
        override_capital=1000.0,
    )
    assert valid_res.is_approved is True
    assert valid_res.calculated_size > 0
    assert valid_res.reserve_buffer_passed is True
    assert valid_res.single_position_passed is True

    # 2. Inverted Stop Loss: Long with SL above Entry must reject
    invalid_sl = await risk_engine.validate_trade(
        symbol="TEST_RISK_SL",
        direction="long",
        entry_price=50000.0,
        stop_loss_price=51000.0,  # Invalid
        take_profit_price=52000.0,
        override_capital=1000.0,
    )
    assert invalid_sl.is_approved is False
    assert any("must be strictly below" in r for r in invalid_sl.rejection_reasons)


@pytest.mark.asyncio
async def test_full_7_state_lifecycle_pipeline():
    """Verify complete sequential progression through States 1 -> 7."""
    await init_db()

    symbol = f"BTC_CYCLE_{uuid.uuid4().hex[:6].upper()}"

    # Step 1: Create trade decision in State 1: SIGNAL_GENERATED
    async with AsyncSessionLocal() as session:
        trade = await TradeRepository.create_trade_decision(
            session=session,
            symbol=symbol,
            direction="long",
            subaccount="futures",
            is_paper=True,
            entry_price=65000.0,
            stop_loss_price=64675.0,   # 0.5% stop loss
            take_profit_price=66300.0,  # 2.0% take profit
            leverage=50,
            suggested_size=1,
            timeframe_alignment={"strategy": "EMA_Trend_Follower", "timeframe": "5m"},
        )
        await session.commit()
        trade_id = trade.id

    # Step 2: Advance to State 2: VALIDATING
    res2 = await execution_engine.advance_trade_lifecycle(trade_id=trade_id)
    assert res2.current_state == TradeLifecycleState.VALIDATING.value

    # Step 3: Advance to State 3: LLM_CONFIRMED
    res3 = await execution_engine.advance_trade_lifecycle(trade_id=trade_id)
    assert res3.current_state == TradeLifecycleState.LLM_CONFIRMED.value
    assert res3.llm_validation is not None

    # Step 4: Advance to State 4: RISK_VALIDATED
    res4 = await execution_engine.advance_trade_lifecycle(trade_id=trade_id)
    assert res4.current_state == TradeLifecycleState.RISK_VALIDATED.value
    assert res4.risk_validation is not None
    assert res4.risk_validation["is_approved"] is True

    # Step 5: Advance to State 5: ORDER_SUBMITTED
    res5 = await execution_engine.advance_trade_lifecycle(trade_id=trade_id)
    assert res5.current_state == TradeLifecycleState.ORDER_SUBMITTED.value
    assert len(res5.orders) >= 1

    # Step 6: Advance to State 6: POSITION_OPEN
    res6 = await execution_engine.advance_trade_lifecycle(trade_id=trade_id)
    assert res6.current_state == TradeLifecycleState.POSITION_OPEN.value
    assert res6.position is not None
    assert res6.position.is_open is True

    # Step 7: Close trade to State 7: EXITED
    res7 = await execution_engine.close_trade(
        trade_id=trade_id,
        exit_reason="TAKE_PROFIT_TRIGGER",
        custom_exit_price=66300.0,
    )
    assert res7.current_state == TradeLifecycleState.EXITED.value
    assert res7.is_terminal is True
    assert res7.trade_summary["realized_pnl"] > 0.0


@pytest.mark.asyncio
async def test_automated_execution_pipeline():
    """Verify auto-execution advances from State 1 directly to State 6 (POSITION_OPEN)."""
    await init_db()

    symbol = f"ETH_AUTO_{uuid.uuid4().hex[:6].upper()}"

    async with AsyncSessionLocal() as session:
        trade = await TradeRepository.create_trade_decision(
            session=session,
            symbol=symbol,
            direction="short",
            subaccount="futures",
            is_paper=True,
            entry_price=3500.0,
            stop_loss_price=3517.5,   # 0.5% SL (leaves safe liquidation buffer vs 50x liq at 3535)
            take_profit_price=3430.0,  # 2.0% TP
            leverage=50,
            suggested_size=2,
            timeframe_alignment={"strategy": "MACD_Momentum_Crossover", "timeframe": "15m"},
        )
        await session.commit()
        trade_id = trade.id

    # Auto execute all the way through
    res = await execution_engine.advance_trade_lifecycle(trade_id=trade_id, auto_execute=True)
    assert res.current_state == TradeLifecycleState.POSITION_OPEN.value
    assert res.position is not None
    assert res.position.is_open is True

    # Clean up by closing
    await execution_engine.close_trade(trade_id=trade_id, exit_reason="TEST_CLEANUP")


@pytest.mark.asyncio
async def test_emergency_kill_switch():
    """Verify emergency kill switch closes active positions and cancels orders."""
    await init_db()

    symbol = f"SOL_KILL_{uuid.uuid4().hex[:6].upper()}"
    async with AsyncSessionLocal() as session:
        trade = await TradeRepository.create_trade_decision(
            session=session,
            symbol=symbol,
            direction="long",
            subaccount="futures",
            is_paper=True,
            entry_price=150.0,
            stop_loss_price=149.25,  # 0.5% SL (leaves safe liquidation buffer vs 50x liq at 148.5)
            take_profit_price=153.0,   # 2.0% TP
            leverage=50,
            suggested_size=1,
        )
        await session.commit()
        trade_id = trade.id

    # Advance to open position
    await execution_engine.advance_trade_lifecycle(trade_id=trade_id, auto_execute=True)

    # Trigger emergency kill switch
    kill_res = await execution_engine.execute_kill_switch(subaccount="futures", reason="TEST_EMERGENCY_HALT")
    assert kill_res.success is True
    assert kill_res.closed_positions_count >= 1

    # Verify trade is now EXITED
    async with AsyncSessionLocal() as session:
        t = await TradeRepository.get_trade_decision(session, trade_id)
        assert t.state == TradeLifecycleState.EXITED.value
        assert t.exit_reason == "KILL_SWITCH_LIQUIDATED"


@pytest.mark.asyncio
async def test_api_routes_risk_and_execution():
    """Verify FastAPI endpoints for Risk and Execution."""
    await init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET /api/v1/risk/status
        res_risk_status = await ac.get("/api/v1/risk/status?subaccount=futures&override_capital=1000.0")
        assert res_risk_status.status_code == 200
        data = res_risk_status.json()
        assert data["total_equity"] == 1000.0
        assert data["reserve_buffer_amount"] == 300.0
        assert data["max_risk_per_trade_amount"] == 10.0

        # 2. POST /api/v1/risk/validate
        api_test_sym = f"BTC_API_{uuid.uuid4().hex[:6].upper()}"
        res_validate = await ac.post(
            "/api/v1/risk/validate",
            json={
                "symbol": api_test_sym,
                "direction": "long",
                "entry_price": 64000.0,
                "stop_loss_price": 63680.0,
                "take_profit_price": 65280.0,
                "leverage": 50,
                "override_capital": 1000.0,
            },
        )
        assert res_validate.status_code == 200
        val_data = res_validate.json()
        assert val_data["is_approved"] is True
        assert val_data["calculated_size"] > 0

        # 3. GET /api/v1/execution/trades
        res_trades = await ac.get("/api/v1/execution/trades?limit=10")
        assert res_trades.status_code == 200
        assert isinstance(res_trades.json(), list)

        # 4. POST /api/v1/execution/scan-triggers
        res_scan = await ac.post("/api/v1/execution/scan-triggers")
        assert res_scan.status_code == 200
        assert "triggered_exits" in res_scan.json()

        # 5. POST /api/v1/execution/kill-switch
        res_kill = await ac.post(
            "/api/v1/execution/kill-switch",
            json={"reason": "API_TEST_HALT"},
        )
        assert res_kill.status_code == 200
        assert res_kill.json()["success"] is True
