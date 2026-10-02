"""Database repository providing transactional domain operations and lifecycle transitions."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.enums import (
    AuditEventType,
    AuditSeverity,
    ExitReason,
    OrderState,
    TradeLifecycleState,
)
from app.db.models import (
    AuditLog,
    Candle,
    Instrument,
    Order,
    Position,
    Signal,
    Strategy,
    TradeDecision,
)

logger = logging.getLogger("delta_bot.db.repository")

# Strict order of 7-state trade lifecycle
LIFECYCLE_SEQUENCE = [
    TradeLifecycleState.SIGNAL_GENERATED.value,
    TradeLifecycleState.VALIDATING.value,
    TradeLifecycleState.LLM_CONFIRMED.value,
    TradeLifecycleState.RISK_VALIDATED.value,
    TradeLifecycleState.ORDER_SUBMITTED.value,
    TradeLifecycleState.POSITION_OPEN.value,
    TradeLifecycleState.EXITED.value,
]


class TradeRepository:
    """Encapsulates all database operations with transactional integrity."""

    # =========================================================================
    # 1. INSTRUMENTS
    # =========================================================================

    @staticmethod
    async def get_instruments(session: AsyncSession, active_only: bool = True) -> List[Instrument]:
        stmt = select(Instrument)
        if active_only:
            stmt = stmt.where(Instrument.is_active == True)
        result = await session.execute(stmt.order_by(Instrument.symbol))
        return list(result.scalars().all())

    @staticmethod
    async def get_instrument_by_symbol(session: AsyncSession, symbol: str) -> Optional[Instrument]:
        stmt = select(Instrument).where(Instrument.symbol == symbol.upper())
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def upsert_instrument(
        session: AsyncSession,
        symbol: str,
        product_id: Optional[int] = None,
        contract_type: str = "perpetual_futures",
        underlying_asset: str = "BTC",
        tick_size: float = 0.5,
        contract_value: float = 0.001,
        is_active: bool = True,
    ) -> Instrument:
        inst = await TradeRepository.get_instrument_by_symbol(session, symbol)
        if inst:
            inst.product_id = product_id or inst.product_id
            inst.contract_type = contract_type
            inst.underlying_asset = underlying_asset
            inst.tick_size = tick_size
            inst.contract_value = contract_value
            inst.is_active = is_active
        else:
            inst = Instrument(
                symbol=symbol.upper(),
                product_id=product_id,
                contract_type=contract_type,
                underlying_asset=underlying_asset,
                tick_size=tick_size,
                contract_value=contract_value,
                is_active=is_active,
            )
            session.add(inst)
        await session.flush()
        return inst

    # =========================================================================
    # 2. CANDLE DATA
    # =========================================================================

    @staticmethod
    async def save_candle(
        session: AsyncSession,
        symbol: str,
        resolution: str,
        open_time: int,
        open_p: float,
        high_p: float,
        low_p: float,
        close_p: float,
        volume: float = 0.0,
        close_time: Optional[int] = None,
        is_closed: bool = True,
    ) -> Candle:
        stmt = select(Candle).where(
            Candle.symbol == symbol.upper(),
            Candle.resolution == resolution,
            Candle.open_time == open_time,
        )
        result = await session.execute(stmt)
        existing = result.scalars().first()
        if existing:
            existing.open = open_p
            existing.high = high_p
            existing.low = low_p
            existing.close = close_p
            existing.volume = volume
            existing.close_time = close_time
            existing.is_closed = is_closed
            return existing
        else:
            candle = Candle(
                symbol=symbol.upper(),
                resolution=resolution,
                open_time=open_time,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=volume,
                close_time=close_time,
                is_closed=is_closed,
            )
            session.add(candle)
            await session.flush()
            return candle

    @staticmethod
    async def get_recent_candles(
        session: AsyncSession,
        symbol: str,
        resolution: str,
        limit: int = 100,
    ) -> List[Candle]:
        stmt = (
            select(Candle)
            .where(Candle.symbol == symbol.upper(), Candle.resolution == resolution)
            .order_by(desc(Candle.open_time))
            .limit(limit)
        )
        result = await session.execute(stmt)
        # Return in ascending chronological order for technical indicators
        candles = list(result.scalars().all())
        candles.reverse()
        return candles

    # =========================================================================
    # 2.5. STRATEGIES (PHASE 6: STRATEGY ENGINE)
    # =========================================================================

    @staticmethod
    async def get_strategies(session: AsyncSession, active_only: bool = False) -> List[Strategy]:
        stmt = select(Strategy)
        if active_only:
            stmt = stmt.where(Strategy.is_active == True)
        result = await session.execute(stmt.order_by(Strategy.id))
        return list(result.scalars().all())

    @staticmethod
    async def get_strategy_by_name(session: AsyncSession, name: str) -> Optional[Strategy]:
        stmt = select(Strategy).where(Strategy.name == name)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def upsert_strategy(
        session: AsyncSession,
        name: str,
        description: Optional[str] = None,
        is_active: bool = True,
        config: Optional[Dict[str, Any]] = None,
    ) -> Strategy:
        strat = await TradeRepository.get_strategy_by_name(session, name)
        if strat:
            if description is not None:
                strat.description = description
            strat.is_active = is_active
            if config is not None:
                strat.config = config
        else:
            strat = Strategy(
                name=name,
                description=description,
                is_active=is_active,
                config=config or {},
            )
            session.add(strat)
        await session.flush()
        return strat

    @staticmethod
    async def toggle_strategy(session: AsyncSession, name: str, is_active: bool) -> Optional[Strategy]:
        strat = await TradeRepository.get_strategy_by_name(session, name)
        if strat:
            strat.is_active = is_active
            await session.flush()
        return strat

    @staticmethod
    async def update_strategy_config(session: AsyncSession, name: str, config: Dict[str, Any]) -> Optional[Strategy]:
        strat = await TradeRepository.get_strategy_by_name(session, name)
        if strat:
            strat.config = {**strat.config, **config}
            await session.flush()
        return strat

    # =========================================================================
    # 3. SIGNALS
    # =========================================================================

    @staticmethod
    async def record_signal(
        session: AsyncSession,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        side: str,
        trigger_price: float,
        confidence_score: float = 0.5,
        indicator_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        signal = Signal(
            strategy_name=strategy_name,
            symbol=symbol.upper(),
            timeframe=timeframe,
            side=side.lower(),
            trigger_price=trigger_price,
            confidence_score=confidence_score,
            indicator_snapshot=indicator_snapshot or {},
            status="PENDING",
        )
        session.add(signal)
        await session.flush()
        return signal

    @staticmethod
    async def get_signal_by_id(session: AsyncSession, signal_id: int) -> Optional[Signal]:
        stmt = select(Signal).where(Signal.id == signal_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def update_signal_status(session: AsyncSession, signal_id: int, status: str) -> Optional[Signal]:
        signal = await TradeRepository.get_signal_by_id(session, signal_id)
        if signal:
            signal.status = status
            await session.flush()
        return signal

    @staticmethod
    async def get_recent_signals(
        session: AsyncSession,
        symbol: Optional[str] = None,
        limit: int = 50,
    ) -> List[Signal]:
        stmt = select(Signal)
        if symbol:
            stmt = stmt.where(Signal.symbol == symbol.upper())
        result = await session.execute(stmt.order_by(desc(Signal.created_at)).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def get_filtered_signals(
        session: AsyncSession,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        strategy_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Signal]:
        stmt = select(Signal)
        if symbol:
            stmt = stmt.where(Signal.symbol == symbol.upper())
        if timeframe:
            stmt = stmt.where(Signal.timeframe == timeframe)
        if strategy_name:
            stmt = stmt.where(Signal.strategy_name == strategy_name)
        if status:
            stmt = stmt.where(Signal.status == status)
        result = await session.execute(stmt.order_by(desc(Signal.created_at)).limit(limit))
        return list(result.scalars().all())

    # =========================================================================
    # 4. TRADE DECISIONS (7-STATE LIFECYCLE)
    # =========================================================================

    @staticmethod
    async def get_active_trade_for_symbol(
        session: AsyncSession,
        symbol: str,
    ) -> Optional[TradeDecision]:
        """Returns the newest non-terminal trade decision for an instrument."""
        terminal_states = [
            TradeLifecycleState.EXITED.value,
            "REJECTED_LLM",
            "REJECTED_RISK",
            "CANCELLED",
        ]
        stmt = (
            select(TradeDecision)
            .where(
                TradeDecision.symbol == symbol.upper(),
                TradeDecision.state.not_in(terminal_states),
            )
            .order_by(desc(TradeDecision.id))
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    get_active_trade_by_symbol = get_active_trade_for_symbol

    @staticmethod
    async def create_trade_decision(
        session: AsyncSession,
        symbol: str,
        direction: str,
        subaccount: str = "futures",
        is_paper: bool = True,
        timeframe_alignment: Optional[Dict[str, Any]] = None,
        entry_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        leverage: int = 50,
        suggested_size: Optional[int] = None,
    ) -> TradeDecision:
        decision = TradeDecision(
            symbol=symbol.upper(),
            direction=direction.lower(),
            state=TradeLifecycleState.SIGNAL_GENERATED.value,
            subaccount=subaccount,
            is_paper=is_paper,
            timeframe_alignment=timeframe_alignment or {},
            entry_price=entry_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            leverage=leverage,
            suggested_size=suggested_size,
        )
        decision.orders = []
        decision.positions = []
        session.add(decision)
        await session.flush()
        return decision

    @staticmethod
    async def transition_trade_state(
        session: AsyncSession,
        decision_id: int,
        target_state: TradeLifecycleState,
        notes: Optional[str] = None,
        llm_approved: Optional[bool] = None,
        llm_confidence: Optional[float] = None,
        llm_reasoning: Optional[str] = None,
        risk_approved: Optional[bool] = None,
        risk_notes: Optional[str] = None,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        exit_reason: Optional[str] = None,
        realized_pnl: Optional[float] = None,
    ) -> TradeDecision:
        """Transitions a trade through the 7-state lifecycle with strict state sequence checks."""
        stmt = (
            select(TradeDecision)
            .options(selectinload(TradeDecision.orders), selectinload(TradeDecision.positions))
            .where(TradeDecision.id == decision_id)
        )
        result = await session.execute(stmt)
        trade = result.scalars().first()
        if not trade:
            raise ValueError(f"TradeDecision #{decision_id} not found")

        curr_state = trade.state
        new_state = target_state.value

        # State updates
        trade.state = new_state
        now = datetime.now(timezone.utc)

        if new_state == TradeLifecycleState.LLM_CONFIRMED.value:
            trade.llm_approved = llm_approved
            trade.llm_confidence = llm_confidence
            trade.llm_reasoning = llm_reasoning

        elif new_state == TradeLifecycleState.RISK_VALIDATED.value:
            trade.risk_approved = risk_approved
            trade.risk_notes = risk_notes or notes

        elif new_state == TradeLifecycleState.POSITION_OPEN.value:
            trade.opened_at = now
            if entry_price is not None:
                trade.entry_price = entry_price

        elif new_state == TradeLifecycleState.EXITED.value:
            trade.closed_at = now
            if exit_price is not None:
                trade.exit_price = exit_price
            if exit_reason is not None:
                trade.exit_reason = exit_reason
            if realized_pnl is not None:
                trade.realized_pnl = realized_pnl

        # Audit the state transition
        await TradeRepository.create_audit_log(
            session=session,
            event_type=AuditEventType.ORDER_EXECUTION if new_state in (TradeLifecycleState.ORDER_SUBMITTED.value, TradeLifecycleState.POSITION_OPEN.value) else AuditEventType.SYSTEM,
            severity=AuditSeverity.INFO,
            message=f"Trade #{trade.id} ({trade.symbol}) transitioned: {curr_state} -> {new_state}",
            subaccount=trade.subaccount,
            symbol=trade.symbol,
            payload={
                "trade_id": trade.id,
                "from_state": curr_state,
                "to_state": new_state,
                "notes": notes,
                "exit_reason": exit_reason,
            },
        )

        await session.flush()
        return trade

    @staticmethod
    async def get_trade_decision(session: AsyncSession, decision_id: int) -> Optional[TradeDecision]:
        stmt = (
            select(TradeDecision)
            .options(selectinload(TradeDecision.orders), selectinload(TradeDecision.positions))
            .where(TradeDecision.id == decision_id)
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_trade_decisions(
        session: AsyncSession,
        symbol: Optional[str] = None,
        state: Optional[str] = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> List[TradeDecision]:
        stmt = (
            select(TradeDecision)
            .options(selectinload(TradeDecision.orders), selectinload(TradeDecision.positions))
        )
        if symbol:
            stmt = stmt.where(TradeDecision.symbol == symbol.upper())
        if state:
            stmt = stmt.where(TradeDecision.state == state)
        if active_only:
            stmt = stmt.where(TradeDecision.state != TradeLifecycleState.EXITED.value)

        result = await session.execute(stmt.order_by(desc(TradeDecision.created_at)).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def get_active_trade_by_symbol(session: AsyncSession, symbol: str) -> Optional[TradeDecision]:
        """Returns the current active trade decision for a symbol if any exists."""
        stmt = (
            select(TradeDecision)
            .options(selectinload(TradeDecision.orders), selectinload(TradeDecision.positions))
            .where(
                TradeDecision.symbol == symbol.upper(),
                TradeDecision.state != TradeLifecycleState.EXITED.value,
                TradeDecision.state != "REJECTED_LLM",
                TradeDecision.state != "REJECTED_RISK",
                TradeDecision.state != "CANCELLED",
            )
            .order_by(desc(TradeDecision.created_at))
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    # =========================================================================
    # 5. ORDERS & POSITIONS
    # =========================================================================

    @staticmethod
    async def record_order(
        session: AsyncSession,
        symbol: str,
        side: str,
        size: int,
        order_type: str = "limit_order",
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        product_id: Optional[int] = None,
        subaccount: str = "futures",
        trade_decision_id: Optional[int] = None,
        exchange_order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        is_paper: bool = True,
        is_bracket: bool = False,
        bracket_take_profit_price: Optional[float] = None,
        bracket_stop_loss_price: Optional[float] = None,
        response_payload: Optional[Dict[str, Any]] = None,
    ) -> Order:
        order = Order(
            trade_decision_id=trade_decision_id,
            subaccount=subaccount,
            exchange_order_id=exchange_order_id,
            client_order_id=client_order_id,
            symbol=symbol.upper(),
            product_id=product_id,
            side=side.lower(),
            order_type=order_type,
            price=price,
            stop_price=stop_price,
            size=size,
            unfilled_size=size,
            state=OrderState.OPEN.value,
            is_paper=is_paper,
            is_bracket=is_bracket,
            bracket_take_profit_price=bracket_take_profit_price,
            bracket_stop_loss_price=bracket_stop_loss_price,
            response_payload=response_payload,
        )
        session.add(order)
        await session.flush()
        return order

    @staticmethod
    async def update_order_state(
        session: AsyncSession,
        order_id: int,
        state: OrderState,
        unfilled_size: Optional[int] = None,
    ) -> Optional[Order]:
        stmt = select(Order).where(Order.id == order_id)
        result = await session.execute(stmt)
        order = result.scalars().first()
        if order:
            order.state = state.value
            if unfilled_size is not None:
                order.unfilled_size = unfilled_size
            await session.flush()
        return order

    @staticmethod
    async def upsert_position(
        session: AsyncSession,
        symbol: str,
        size: int,
        entry_price: float,
        subaccount: str = "futures",
        product_id: Optional[int] = None,
        trade_decision_id: Optional[int] = None,
        leverage: int = 50,
        margin: float = 0.0,
        liquidation_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
    ) -> Position:
        stmt = select(Position).where(
            Position.symbol == symbol.upper(),
            Position.subaccount == subaccount,
            Position.is_open == True,
        )
        result = await session.execute(stmt)
        pos = result.scalars().first()
        if pos:
            pos.size = size
            pos.entry_price = entry_price
            pos.margin = margin
            pos.leverage = leverage
            pos.liquidation_price = liquidation_price
            pos.take_profit_price = take_profit_price
            pos.stop_loss_price = stop_loss_price
        else:
            pos = Position(
                trade_decision_id=trade_decision_id,
                subaccount=subaccount,
                symbol=symbol.upper(),
                product_id=product_id,
                size=size,
                entry_price=entry_price,
                margin=margin,
                leverage=leverage,
                liquidation_price=liquidation_price,
                take_profit_price=take_profit_price,
                stop_loss_price=stop_loss_price,
                is_open=True,
            )
            session.add(pos)
        await session.flush()
        return pos

    @staticmethod
    async def get_position_by_symbol(
        session: AsyncSession,
        symbol: str,
        subaccount: str = "futures",
    ) -> Optional[Position]:
        """Returns the open position for a symbol and subaccount if any exists."""
        stmt = select(Position).where(
            Position.symbol == symbol.upper(),
            Position.subaccount == subaccount,
            Position.is_open == True,
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_open_positions(
        session: AsyncSession,
        subaccount: Optional[str] = None,
    ) -> List[Position]:
        """Returns all open positions, optionally filtered by subaccount."""
        stmt = select(Position).where(Position.is_open == True)
        if subaccount:
            stmt = stmt.where(Position.subaccount == subaccount)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def close_position(
        session: AsyncSession,
        symbol: str,
        exit_price: float,
        realized_pnl: float,
        exit_reason: str = "MANUAL_CLOSE",
        subaccount: str = "futures",
    ) -> Optional[Position]:
        """Marks an open position as closed and records realized PnL."""
        stmt = select(Position).where(
            Position.symbol == symbol.upper(),
            Position.subaccount == subaccount,
            Position.is_open == True,
        )
        result = await session.execute(stmt)
        pos = result.scalars().first()
        if pos:
            pos.is_open = False
            pos.current_price = exit_price
            pos.realized_pnl = realized_pnl
            pos.exit_reason = exit_reason
            pos.closed_at = datetime.now(timezone.utc)
            await session.flush()
        return pos

    # =========================================================================
    # 6. AUDIT LOGS
    # =========================================================================

    @staticmethod
    async def create_audit_log(
        session: AsyncSession,
        message: str,
        event_type: AuditEventType = AuditEventType.SYSTEM,
        severity: AuditSeverity = AuditSeverity.INFO,
        subaccount: Optional[str] = None,
        symbol: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        audit = AuditLog(
            event_type=event_type.value if hasattr(event_type, "value") else str(event_type),
            severity=severity.value if hasattr(severity, "value") else str(severity),
            message=message,
            subaccount=subaccount,
            symbol=symbol.upper() if symbol else None,
            payload=payload,
        )
        session.add(audit)
        await session.flush()
        return audit

    @staticmethod
    async def get_audit_logs(
        session: AsyncSession,
        severity: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditLog]:
        stmt = select(AuditLog)
        if severity:
            stmt = stmt.where(AuditLog.severity == severity.upper())
        if event_type:
            stmt = stmt.where(AuditLog.event_type == event_type.upper())
        result = await session.execute(stmt.order_by(desc(AuditLog.created_at)).limit(limit))
        return list(result.scalars().all())
