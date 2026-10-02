"""Unit and Integration tests for Strategy Engine (Phase 6).

Tests:
1. Strategy registration and metadata (EMATrend, MACDMomentum, VWAPBounce, PivotBreakout, CompositeEnsemble).
2. Individual strategy evaluation logic with mock indicator snapshots.
3. Bracket calculation (Stop Loss, Take Profit, and Risk:Reward ratios).
4. Composite Ensemble multi-strategy synthesis and consensus scoring.
5. SQLite signal persistence and retrieval via TradeRepository.
6. Signal promotion to TradeDecision (7-State Lifecycle State 1: SIGNAL_GENERATED) and single-position invariant.
7. REST API endpoints for strategies and signals.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Signal, TradeDecision
from app.db.repository import TradeRepository
from app.db.session import AsyncSessionLocal, init_db
from app.main import app
from app.schemas.indicator import (
    EMAResult,
    IndicatorSnapshot,
    MACDResult,
    PivotLevels,
    PivotPointsResult,
    SupportResistanceResult,
    VWAPResult,
)
from app.services.strategy_engine import (
    CompositeEnsembleStrategy,
    EMATrendStrategy,
    MACDMomentumStrategy,
    PivotBreakoutStrategy,
    VWAPBounceStrategy,
    strategy_engine,
)


def test_strategy_engine_registration():
    """Verify all 5 core strategies are properly registered in StrategyEngine."""
    strategies = ["EMA_Trend_Follower", "MACD_Momentum_Crossover", "VWAP_Band_Bounce", "Pivot_SR_Breakout", "Composite_Ensemble"]
    for name in strategies:
        strat = strategy_engine.get_strategy(name)
        assert strat is not None
        assert strat.name == name
        assert strat.is_active is True


def test_ema_trend_strategy_bullish_signal():
    """Verify EMATrendStrategy generates a LONG signal on bullish EMA 9 > 20 alignment."""
    strat = EMATrendStrategy()
    snapshot = IndicatorSnapshot(
        symbol="BTCUSD",
        timeframe="5m",
        current_price=65000.0,
        candle_count=50,
        technical_score=45.0,
        technical_bias="MODERATELY_BULLISH",
        ema=EMAResult(
            ema_fast=65200.0,
            ema_slow=64800.0,
            trend="BULLISH_TREND",
            price_position="ABOVE_BOTH",
            spread_pct=0.62,
            fast_slope=0.08,
            slow_slope=0.03,
        ),
    )

    signal = strat.evaluate("BTCUSD", "5m", [], snapshot)
    assert signal is not None
    assert signal.side == "buy"
    assert signal.direction == "long"
    assert signal.trigger_price == 65000.0
    assert signal.stop_loss < signal.trigger_price
    assert signal.take_profit > signal.trigger_price
    assert signal.risk_reward_ratio >= 2.0
    assert signal.confidence_score >= 0.65
    assert len(signal.reasons) > 0


def test_macd_momentum_strategy_bearish_signal():
    """Verify MACDMomentumStrategy generates a SHORT signal on bearish crossover."""
    strat = MACDMomentumStrategy()
    snapshot = IndicatorSnapshot(
        symbol="ETHUSD",
        timeframe="15m",
        current_price=3500.0,
        candle_count=50,
        technical_score=-40.0,
        technical_bias="MODERATELY_BEARISH",
        macd=MACDResult(
            macd_line=-12.5,
            signal_line=-5.0,
            histogram=-7.5,
            crossover="BEARISH_CROSSOVER",
            zero_line_position="BELOW_ZERO",
            momentum_state="BEARISH_ACCELERATING",
        ),
    )

    signal = strat.evaluate("ETHUSD", "15m", [], snapshot)
    assert signal is not None
    assert signal.side == "sell"
    assert signal.direction == "short"
    assert signal.trigger_price == 3500.0
    assert signal.stop_loss > signal.trigger_price
    assert signal.take_profit < signal.trigger_price
    assert signal.confidence_score >= 0.70


def test_vwap_mean_reversion_long():
    """Verify VWAPBounceStrategy generates a LONG mean-reversion signal at -2σ band."""
    strat = VWAPBounceStrategy()
    snapshot = IndicatorSnapshot(
        symbol="SOLUSD",
        timeframe="5m",
        current_price=140.0,
        candle_count=50,
        technical_score=-15.0,
        technical_bias="NEUTRAL",
        vwap=VWAPResult(
            vwap=145.0,
            upper_band_1=147.5,
            upper_band_2=150.0,
            lower_band_1=142.5,
            lower_band_2=140.0,
            std_dev=2.5,
            distance_pct=-3.45,
            bias="EXTREME_OVERSOLD",
        ),
    )

    signal = strat.evaluate("SOLUSD", "5m", [], snapshot)
    assert signal is not None
    assert signal.side == "buy"
    assert signal.direction == "long"
    assert signal.trigger_price == 140.0
    # Mean reversion target should target VWAP (145.0)
    assert signal.take_profit == 145.0
    assert signal.stop_loss < 140.0


def test_composite_ensemble_synthesis():
    """Verify CompositeEnsembleStrategy synthesizes multiple agreeing signals."""
    strat = CompositeEnsembleStrategy()
    snapshot = IndicatorSnapshot(
        symbol="BTCUSD",
        timeframe="5m",
        current_price=66000.0,
        candle_count=50,
        technical_score=60.0,
        technical_bias="BULLISH",
    )

    # Two agreeing Long signals
    ema_strat = EMATrendStrategy()
    macd_strat = MACDMomentumStrategy()

    sig1 = ema_strat.calculate_brackets(66000.0, "long", 65500.0, 2.5)
    sig2 = macd_strat.calculate_brackets(66000.0, "long", 65400.0, 2.5)

    from app.schemas.strategy import StrategySignal
    sub_signals = [
        StrategySignal(
            strategy_name="EMA_Trend_Follower",
            symbol="BTCUSD",
            timeframe="5m",
            side="buy",
            direction="long",
            trigger_price=66000.0,
            stop_loss=sig1[0],
            take_profit=sig1[1],
            risk_reward_ratio=sig1[2],
            confidence_score=0.75,
            reasons=["EMA bullish trend"],
        ),
        StrategySignal(
            strategy_name="MACD_Momentum_Crossover",
            symbol="BTCUSD",
            timeframe="5m",
            side="buy",
            direction="long",
            trigger_price=66000.0,
            stop_loss=sig2[0],
            take_profit=sig2[1],
            risk_reward_ratio=sig2[2],
            confidence_score=0.78,
            reasons=["MACD bullish crossover"],
        ),
    ]

    ensemble_signal = strat.synthesize_ensemble("BTCUSD", "5m", sub_signals, snapshot)
    assert ensemble_signal is not None
    assert ensemble_signal.strategy_name == "Composite_Ensemble"
    assert ensemble_signal.direction == "long"
    # Ensemble should boost confidence
    assert ensemble_signal.confidence_score >= 0.80
    assert "EMA_Trend_Follower" in ensemble_signal.reasons[0]
    assert "MACD_Momentum_Crossover" in ensemble_signal.reasons[0]


@pytest.mark.asyncio
async def test_signal_promotion_to_trade_lifecycle():
    """Verify signal promotion transitions into State 1: SIGNAL_GENERATED and enforces single-position invariant."""
    await init_db()
    await strategy_engine.initialize_db_strategies()

    test_symbol = "BTC_PROMO_USD"

    # 1. Record a candidate signal in DB
    async with AsyncSessionLocal() as session:
        signal = await TradeRepository.record_signal(
            session=session,
            strategy_name="EMA_Trend_Follower",
            symbol=test_symbol,
            timeframe="5m",
            side="buy",
            trigger_price=67000.0,
            confidence_score=0.82,
            indicator_snapshot={
                "direction": "long",
                "stop_loss": 66330.0,
                "take_profit": 68675.0,
                "reasons": ["EMA 9 > 20 alignment", "Strong volume expansion"],
            },
        )
        await session.commit()
        signal_id = signal.id

    # 2. Promote to TradeDecision
    response = await strategy_engine.promote_to_trade(signal_id=signal_id)
    assert response.trade_id > 0
    assert response.state == "SIGNAL_GENERATED"
    assert response.symbol == test_symbol
    assert response.direction == "long"
    assert response.entry_price == 67000.0
    assert response.stop_loss == 66330.0
    assert response.take_profit == 68675.0

    # 3. Verify Signal status updated in DB
    async with AsyncSessionLocal() as session:
        updated_sig = await TradeRepository.get_signal_by_id(session, signal_id)
        assert updated_sig.status == "PROMOTED"

    # 4. Invariant Test: Attempting to promote another signal for same symbol must fail because an active trade exists!
    async with AsyncSessionLocal() as session:
        second_signal = await TradeRepository.record_signal(
            session=session,
            strategy_name="MACD_Momentum_Crossover",
            symbol=test_symbol,
            timeframe="15m",
            side="buy",
            trigger_price=67100.0,
        )
        await session.commit()
        second_id = second_signal.id

    with pytest.raises(ValueError) as exc_info:
        await strategy_engine.promote_to_trade(signal_id=second_id)
    assert "Invariant violation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_generated_signal_auto_promotes_and_executes():
    """Verify a generated signal can promote and execute immediately into the trade lifecycle."""
    await init_db()
    await strategy_engine.initialize_db_strategies()

    async with AsyncSessionLocal() as session:
        signal = await TradeRepository.record_signal(
            session=session,
            strategy_name="EMA_Trend_Follower",
            symbol="BTC_AUTO_EXEC",
            timeframe="5m",
            side="buy",
            trigger_price=68000.0,
            confidence_score=0.91,
            indicator_snapshot={
                "direction": "long",
                "stop_loss": 67000.0,
                "take_profit": 71000.0,
                "reasons": ["EMA bullish momentum"],
            },
        )
        await session.commit()

    result = await strategy_engine.auto_execute_generated_signal(signal.id)

    assert result is not None
    assert result.current_state in {"ORDER_SUBMITTED", "POSITION_OPEN"}
    assert result.trade_summary["symbol"] == "BTC_AUTO_EXEC"


@pytest.mark.asyncio
async def test_api_routes_strategies_and_signals():
    """Verify FastAPI routes for strategies and signals."""
    await init_db()
    await strategy_engine.initialize_db_strategies()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET /api/v1/strategies
        res = await ac.get("/api/v1/strategies")
        assert res.status_code == 200
        strats = res.json()
        assert len(strats) >= 5
        names = [s["name"] for s in strats]
        assert "EMA_Trend_Follower" in names
        assert "Composite_Ensemble" in names

        # 2. POST /api/v1/strategies/{name}/toggle
        res_toggle = await ac.post(
            "/api/v1/strategies/EMA_Trend_Follower/toggle",
            json={"is_active": False},
        )
        assert res_toggle.status_code == 200
        assert res_toggle.json()["is_active"] is False

        # Toggle back
        await ac.post(
            "/api/v1/strategies/EMA_Trend_Follower/toggle",
            json={"is_active": True},
        )

        # 3. POST /api/v1/strategies/{name}/config
        res_cfg = await ac.post(
            "/api/v1/strategies/EMA_Trend_Follower/config",
            json={"config": {"risk_reward": 3.0}},
        )
        assert res_cfg.status_code == 200
        assert res_cfg.json()["config"]["risk_reward"] == 3.0

        # 4. POST /api/v1/strategies/evaluate
        res_eval = await ac.post(
            "/api/v1/strategies/evaluate",
            json={"symbol": "BTCUSD", "timeframe": "5m", "persist": True},
        )
        assert res_eval.status_code == 200
        eval_data = res_eval.json()
        assert "evaluated_strategies" in eval_data
        assert "signals" in eval_data

        # 5. GET /api/v1/signals
        res_sig = await ac.get("/api/v1/signals?limit=10")
        assert res_sig.status_code == 200
        assert isinstance(res_sig.json(), list)
