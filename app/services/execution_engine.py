"""Trade Execution & 7-State Lifecycle Orchestrator (Phase 7).

Manages the complete deterministic lifecycle:
State 1: SIGNAL_GENERATED -> State 2: VALIDATING -> State 3: LLM_CONFIRMED ->
State 4: RISK_VALIDATED   -> State 5: ORDER_SUBMITTED -> State 6: POSITION_OPEN ->
State 7: EXITED

Features:
- Sequential lifecycle step advancing and multi-step auto-execution.
- HMAC-SHA256 authenticated order placement with Delta Exchange / Paper engine.
- Bracket Take-Profit & Stop-Loss orders attached to entry.
- Real-time mark price tracking and Trailing Stop / Break-Even adjustment.
- Automated TP/SL trigger detection, position closing, and realized PnL accounting.
- Emergency Kill-Switch for instant position liquidation and order cancellation.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.leverage import get_trade_leverage
from app.db.enums import (
    AuditEventType,
    AuditSeverity,
    OrderState,
    TradeLifecycleState,
)
from app.db.models import (
    Order,
    Position,
    TradeDecision,
)
from app.db.repository import TradeRepository
from app.db.session import get_db_session
from app.schemas.execution import (
    KillSwitchResponse,
    OrderSnapshot,
    PositionSnapshot,
    TradeLifecycleStepResponse,
)
from app.services.client_factory import client_factory
from app.services.llm_advisory import llm_advisory
from app.services.market_data_engine import market_engine
from app.services.risk_engine import risk_engine
import inspect

logger = logging.getLogger("delta_bot.execution_engine")


async def _invoke_client(func, *args, **kwargs):
    """Safely invokes a client method whether it is synchronous or a coroutine."""
    res = func(*args, **kwargs)
    if inspect.isawaitable(res):
        return await res
    return res


class ExecutionOrchestrator:
    """Central orchestrator managing trade transitions and order routing."""

    def __init__(self):
        self._monitoring_task: Optional[asyncio.Task] = None
        self._is_monitoring: bool = False

    async def resume_pending_signal_trades(self) -> int:
        """Retry auto-generated signal trades that stopped before position confirmation.

        The state machine persists each transition before contacting the next external
        dependency. A transient failure can therefore leave an automatically generated
        trade in an active state; this scan resumes only trades linked to a signal.
        """
        resumable_states = (
            TradeLifecycleState.SIGNAL_GENERATED.value,
            TradeLifecycleState.VALIDATING.value,
            TradeLifecycleState.LLM_CONFIRMED.value,
            TradeLifecycleState.RISK_VALIDATED.value,
            TradeLifecycleState.ORDER_SUBMITTED.value,
        )
        pending: List[tuple[int, str]] = []
        async with get_db_session() as session:
            active = await TradeRepository.get_trade_decisions(
                session=session, active_only=True, limit=1000
            )

        terminal_states = {"REJECTED_LLM", "REJECTED_RISK", "CANCELLED", TradeLifecycleState.EXITED.value}
        active_by_symbol: Dict[str, List[TradeDecision]] = {}
        for trade in active:
            if trade.state not in terminal_states:
                active_by_symbol.setdefault(trade.symbol, []).append(trade)

        # Recover only the two newest active decisions per asset. Older queued
        # decisions may predate the per-asset cap and must not flood the exchange.
        for trades in active_by_symbol.values():
            selected = sorted(trades, key=lambda trade: trade.id, reverse=True)[:2]
            pending.extend(
                (trade.id, trade.state)
                for trade in selected
                if trade.state in resumable_states
                and (trade.timeframe_alignment or {}).get("signal_id") is not None
            )

        resumed = 0
        for trade_id, state in pending:
            try:
                await self.advance_trade_lifecycle(
                    trade_id=trade_id,
                    auto_execute=True,
                    notes=f"Resuming auto-generated signal trade from {state}",
                )
                resumed += 1
            except Exception:
                logger.exception("Could not resume auto-generated trade #%s from %s", trade_id, state)
        return resumed

    async def advance_trade_lifecycle(
        self,
        trade_id: int,
        target_state: Optional[str] = None,
        auto_execute: bool = False,
        notes: Optional[str] = None,
    ) -> TradeLifecycleStepResponse:
        """Transitions a trade decision to the next sequential state in the 7-State lifecycle."""
        async with get_db_session() as session:
            trade = await TradeRepository.get_trade_decision(session, trade_id)
            if not trade:
                raise ValueError(f"TradeDecision #{trade_id} not found")

            curr_state = trade.state
            prev_state = curr_state

            # If already terminal, return current state
            if curr_state in (TradeLifecycleState.EXITED.value, "REJECTED_LLM", "REJECTED_RISK", "CANCELLED"):
                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=f"Trade #{trade_id} is in terminal state '{curr_state}'",
                    is_terminal=True,
                )

            if curr_state in {
                TradeLifecycleState.SIGNAL_GENERATED.value,
                TradeLifecycleState.VALIDATING.value,
                TradeLifecycleState.LLM_CONFIRMED.value,
                TradeLifecycleState.RISK_VALIDATED.value,
            }:
                configured_leverage = get_trade_leverage(
                    trade.symbol, trade.subaccount, trade.leverage
                )
                if trade.leverage != configured_leverage:
                    trade.leverage = configured_leverage
                    await session.commit()

        # ---------------------------------------------------------------------
        # STATE 1: SIGNAL_GENERATED -> STATE 2: VALIDATING
        # ---------------------------------------------------------------------
        if curr_state == TradeLifecycleState.SIGNAL_GENERATED.value:
            async with get_db_session() as session:
                trade = await TradeRepository.transition_trade_state(
                    session=session,
                    decision_id=trade_id,
                    target_state=TradeLifecycleState.VALIDATING,
                    notes=notes or "Beginning automated validation pipeline",
                )
                await session.commit()
            curr_state = TradeLifecycleState.VALIDATING.value

            if not auto_execute and target_state != TradeLifecycleState.LLM_CONFIRMED.value:
                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=f"Advanced to State 2: {curr_state}",
                )

        # ---------------------------------------------------------------------
        # STATE 2: VALIDATING -> STATE 3: LLM_CONFIRMED (or REJECTED_LLM)
        # ---------------------------------------------------------------------
        if curr_state == TradeLifecycleState.VALIDATING.value:
            advisory = await llm_advisory.evaluate_trade(
                symbol=trade.symbol,
                direction=trade.direction,
                entry_price=trade.entry_price or 0.0,
                stop_loss_price=trade.stop_loss_price or 0.0,
                take_profit_price=trade.take_profit_price or 0.0,
            )

            if not advisory.approved:
                # LLM advisory rejection
                async with get_db_session() as session:
                    trade = await TradeRepository.transition_trade_state(
                        session=session,
                        decision_id=trade_id,
                        target_state=TradeLifecycleState.EXITED,
                        exit_reason="REJECTED_LLM",
                        llm_approved=False,
                        llm_confidence=advisory.confidence,
                        llm_reasoning=advisory.reasoning,
                        notes=f"Advisory rejection: {advisory.reasoning}",
                    )
                    trade.state = "REJECTED_LLM"
                    await session.commit()

                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=f"Trade rejected by Advisory layer: {advisory.reasoning}",
                    is_terminal=True,
                    llm_val=advisory.model_dump(),
                )

            # Approved by Advisory
            async with get_db_session() as session:
                trade = await TradeRepository.transition_trade_state(
                    session=session,
                    decision_id=trade_id,
                    target_state=TradeLifecycleState.LLM_CONFIRMED,
                    llm_approved=True,
                    llm_confidence=advisory.confidence,
                    llm_reasoning=advisory.reasoning,
                    notes=f"Confirmed by Advisory layer: {advisory.market_regime}",
                )
                await session.commit()
            curr_state = TradeLifecycleState.LLM_CONFIRMED.value

            if not auto_execute and target_state != TradeLifecycleState.RISK_VALIDATED.value:
                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=f"Advanced to State 3: {curr_state}",
                    llm_val=advisory.model_dump(),
                )

        # ---------------------------------------------------------------------
        # STATE 3: LLM_CONFIRMED -> STATE 4: RISK_VALIDATED (or REJECTED_RISK)
        # ---------------------------------------------------------------------
        if curr_state == TradeLifecycleState.LLM_CONFIRMED.value:
            risk_val = await risk_engine.validate_trade(
                symbol=trade.symbol,
                direction=trade.direction,
                entry_price=trade.entry_price or 0.0,
                stop_loss_price=trade.stop_loss_price or 0.0,
                take_profit_price=trade.take_profit_price or 0.0,
                leverage=trade.leverage,
                subaccount=trade.subaccount,
                current_trade_id=trade_id,
                is_paper=trade.is_paper,
            )

            if not risk_val.is_approved:
                # Deterministic risk engine rejection
                reasons_str = "; ".join(risk_val.rejection_reasons)
                async with get_db_session() as session:
                    trade = await TradeRepository.transition_trade_state(
                        session=session,
                        decision_id=trade_id,
                        target_state=TradeLifecycleState.EXITED,
                        exit_reason="REJECTED_RISK",
                        risk_approved=False,
                        risk_notes=reasons_str,
                        notes=f"Deterministic risk rejection: {reasons_str}",
                    )
                    trade.state = "REJECTED_RISK"
                    await session.commit()

                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=f"Trade rejected by Risk Engine: {reasons_str}",
                    is_terminal=True,
                    risk_val=risk_val.model_dump(),
                )

            # Approved by Risk Engine: assign calculated integer size and liquidation buffer
            async with get_db_session() as session:
                t = await TradeRepository.get_trade_decision(session, trade_id)
                t.suggested_size = risk_val.calculated_size
                t.liquidation_buffer_pct = risk_val.liquidation_buffer_pct
                trade = await TradeRepository.transition_trade_state(
                    session=session,
                    decision_id=trade_id,
                    target_state=TradeLifecycleState.RISK_VALIDATED,
                    risk_approved=True,
                    risk_notes=f"Approved: Size={risk_val.calculated_size}, Risk=${risk_val.dollar_risk:.2f} ({risk_val.dollar_risk_pct}%)",
                )
                await session.commit()
            curr_state = TradeLifecycleState.RISK_VALIDATED.value

            if not auto_execute and target_state != TradeLifecycleState.ORDER_SUBMITTED.value:
                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=f"Advanced to State 4: {curr_state} (Calculated Size: {risk_val.calculated_size} contracts)",
                    risk_val=risk_val.model_dump(),
                )

        # ---------------------------------------------------------------------
        # STATE 4: RISK_VALIDATED -> STATE 5: ORDER_SUBMITTED
        # ---------------------------------------------------------------------
        if curr_state == TradeLifecycleState.RISK_VALIDATED.value:
            size_to_order = trade.suggested_size or 1
            entry_side = "buy" if trade.direction.lower() == "long" else "sell"
            client = client_factory.get_client(trade.subaccount, paper_trading=trade.is_paper)
            logger.info(
                "Submitting %s %s order for Trade #%s (%s mode, endpoint=%s)",
                entry_side.upper(),
                trade.symbol,
                trade_id,
                "paper" if trade.is_paper else "live",
                client.base_url,
            )

            # 1. Place Order on Exchange (or paper simulator) with attached brackets
            client_order_id = f"trade_{trade_id}"
            try:
                # Recover an accepted order after a process/database failure instead of duplicating it.
                try:
                    order_res = await _invoke_client(client.get_order_by_client_oid, client_order_id)
                    known_order = order_res.get("result") if isinstance(order_res, dict) else None
                except Exception as lookup_error:
                    is_not_found = (
                        getattr(lookup_error, "status_code", None) == 404
                        or getattr(lookup_error, "code", None) in {"order_not_found", "not_found"}
                    )
                    if not is_not_found:
                        raise RuntimeError(
                            f"Could not verify prior submission for {client_order_id}; refusing duplicate order"
                        ) from lookup_error
                    known_order = None

                if not isinstance(known_order, dict) or not known_order:
                    if not trade.is_paper:
                        product_response = await _invoke_client(
                            client.get_product_by_symbol, trade.symbol
                        )
                        product = product_response.get("result", {}) if isinstance(product_response, dict) else {}
                        product_id = product.get("id") if isinstance(product, dict) else None
                        if product_id is None:
                            raise RuntimeError(
                                f"Could not resolve product ID for {trade.symbol}; leverage was not applied"
                            )
                        leverage_response = await _invoke_client(
                            client.set_order_leverage,
                            product_id=int(product_id),
                            leverage=trade.leverage,
                        )
                        if isinstance(leverage_response, dict) and leverage_response.get("success") is False:
                            raise RuntimeError(
                                f"Exchange rejected {trade.leverage}x leverage for {trade.symbol}: {leverage_response}"
                            )
                    order_res = await _invoke_client(
                        client.place_order,
                        symbol=trade.symbol,
                        size=size_to_order,
                        side=entry_side,
                        order_type="market_order",
                        bracket_stop_loss=trade.stop_loss_price,
                        bracket_take_profit=trade.take_profit_price,
                        client_order_id=client_order_id,
                    )
            except Exception as e:
                logger.error(f"Failed to submit order to exchange for Trade #{trade_id}: {e}")
                raise RuntimeError(f"Order submission failed: {e}")

            exchange_result = order_res.get("result", order_res)
            if order_res.get("success") is False or not isinstance(exchange_result, dict):
                raise RuntimeError(f"Exchange rejected order: {order_res}")
            exchange_order_id = exchange_result.get("id")
            if exchange_order_id is None:
                raise RuntimeError(f"Exchange response did not include an order ID: {order_res}")

            exchange_state = str(exchange_result.get("state", "pending")).lower()
            unfilled_size = int(exchange_result.get("unfilled_size", 0 if exchange_state in {"closed", "filled"} else size_to_order))
            order_state = (
                OrderState.FILLED
                if exchange_state in {"closed", "filled"} and unfilled_size == 0
                else OrderState.CANCELLED
                if exchange_state in {"cancelled", "canceled"}
                else OrderState.REJECTED
                if exchange_state == "rejected"
                else OrderState.OPEN
            )

            # 2. Record order in database
            async with get_db_session() as session:
                db_order = await TradeRepository.record_order(
                    session=session,
                    symbol=trade.symbol,
                    side=entry_side,
                    size=size_to_order,
                    order_type="market_order",
                    price=trade.entry_price,
                    subaccount=trade.subaccount,
                    trade_decision_id=trade_id,
                    exchange_order_id=exchange_order_id,
                    is_paper=trade.is_paper,
                    is_bracket=True,
                    bracket_take_profit_price=trade.take_profit_price,
                    bracket_stop_loss_price=trade.stop_loss_price,
                    response_payload=order_res,
                )
                db_order.state = order_state.value
                db_order.unfilled_size = unfilled_size

                # Transition trade to ORDER_SUBMITTED
                trade = await TradeRepository.transition_trade_state(
                    session=session,
                    decision_id=trade_id,
                    target_state=TradeLifecycleState.ORDER_SUBMITTED,
                    notes=f"Order {exchange_order_id} submitted to {trade.subaccount} exchange",
                )
                await session.commit()
            curr_state = TradeLifecycleState.ORDER_SUBMITTED.value

            if not auto_execute and target_state != TradeLifecycleState.POSITION_OPEN.value:
                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=f"Advanced to State 5: {curr_state} (Order ID: {exchange_order_id})",
                )

        # ---------------------------------------------------------------------
        # STATE 5: ORDER_SUBMITTED -> STATE 6: POSITION_OPEN
        # ---------------------------------------------------------------------
        if curr_state == TradeLifecycleState.ORDER_SUBMITTED.value:
            order = next((o for o in reversed(trade.orders) if o.order_type == "market_order"), None)
            if order is None:
                raise RuntimeError(f"Trade #{trade_id} is ORDER_SUBMITTED but has no recorded entry order")

            exchange_result = (order.response_payload or {}).get("result", {})
            exchange_state = str(exchange_result.get("state", "")).lower()
            unfilled_size = int(exchange_result.get("unfilled_size", order.unfilled_size))
            if exchange_state not in {"closed", "filled"} or unfilled_size != 0:
                client = client_factory.get_client(trade.subaccount, paper_trading=trade.is_paper)
                try:
                    latest = await _invoke_client(client.get_order_by_id, int(order.exchange_order_id))
                except Exception as e:
                    logger.warning("Could not confirm fill for order %s: %s", order.exchange_order_id, e)
                    latest = None
                if isinstance(latest, dict):
                    candidate = latest.get("result", latest)
                    if isinstance(candidate, dict) and candidate:
                        exchange_result = candidate
                        exchange_state = str(candidate.get("state", "")).lower()
                        unfilled_size = int(candidate.get("unfilled_size", order.size))

            is_filled = exchange_state in {"closed", "filled"} and unfilled_size == 0
            if not is_filled:
                return self._build_step_response(
                    trade=trade,
                    prev_state=prev_state,
                    message=(
                        f"Entry order {order.exchange_order_id} is {exchange_state or 'unconfirmed'} "
                        f"({unfilled_size} contracts unfilled); position remains unconfirmed."
                    ),
                )

            size_to_order = order.size
            fill_price = float(
                exchange_result.get("average_fill_price")
                or exchange_result.get("fill_price")
                or trade.entry_price
                or 0.0
            )

            # Calculate isolated margin
            contract_unit = risk_engine.get_contract_unit(trade.symbol)
            notional = fill_price * contract_unit * size_to_order
            margin = notional / trade.leverage if trade.leverage > 0 else notional
            liq_price = risk_engine.calculate_liquidation_price(fill_price, trade.direction, trade.leverage)

            async with get_db_session() as session:
                # Persist the exchange-confirmed fill before opening the local position.
                persisted_order = await session.get(Order, order.id)
                if persisted_order is None:
                    raise RuntimeError(f"Recorded order #{order.id} disappeared before fill confirmation")
                persisted_order.state = OrderState.FILLED.value
                persisted_order.unfilled_size = 0
                persisted_order.price = fill_price
                persisted_order.response_payload = {"success": True, "result": exchange_result}
                # Create or update position
                pos = await TradeRepository.upsert_position(
                    session=session,
                    symbol=trade.symbol,
                    size=size_to_order,
                    entry_price=fill_price,
                    subaccount=trade.subaccount,
                    trade_decision_id=trade_id,
                    leverage=trade.leverage,
                    margin=margin,
                    liquidation_price=liq_price,
                    take_profit_price=trade.take_profit_price,
                    stop_loss_price=trade.stop_loss_price,
                )

                # Transition trade state to POSITION_OPEN
                trade = await TradeRepository.transition_trade_state(
                    session=session,
                    decision_id=trade_id,
                    target_state=TradeLifecycleState.POSITION_OPEN,
                    entry_price=fill_price,
                    notes=f"Position filled at ${fill_price:,.2f} for {size_to_order} contracts",
                )
                await session.commit()
            curr_state = TradeLifecycleState.POSITION_OPEN.value

            return self._build_step_response(
                trade=trade,
                prev_state=prev_state,
                message=f"Advanced to State 6: {curr_state} (Position active at ${fill_price:,.2f})",
            )

        # Return status for already open position
        return self._build_step_response(
            trade=trade,
            prev_state=prev_state,
            message=f"Trade #{trade_id} is in State: {curr_state}",
        )

    async def close_trade(
        self,
        trade_id: int,
        exit_reason: str = "MANUAL_CLOSE",
        custom_exit_price: Optional[float] = None,
    ) -> TradeLifecycleStepResponse:
        """Closes an open position and transitions trade to State 7: EXITED."""
        async with get_db_session() as session:
            trade = await TradeRepository.get_trade_decision(session, trade_id)
            if not trade:
                raise ValueError(f"TradeDecision #{trade_id} not found")

            if trade.state == TradeLifecycleState.EXITED.value:
                return self._build_step_response(
                    trade=trade,
                    prev_state=trade.state,
                    message=f"Trade #{trade_id} is already EXITED",
                    is_terminal=True,
                )

            # Determine exit price
            exit_price = custom_exit_price
            if exit_price is None:
                live_info = market_engine.get_live_price(trade.symbol)
                exit_price = live_info.get("mark_price", trade.entry_price or 0.0) if live_info else (trade.entry_price or 0.0)

            # Calculate realized PnL
            entry_price = trade.entry_price or exit_price
            size = trade.suggested_size or 1
            contract_unit = risk_engine.get_contract_unit(trade.symbol)
            is_long = trade.direction.lower() == "long"

            price_pnl = (exit_price - entry_price) if is_long else (entry_price - exit_price)
            realized_pnl = round(price_pnl * contract_unit * size, 4)

            # 1. Close position in exchange/paper client
            client = client_factory.get_client(trade.subaccount, paper_trading=trade.is_paper)
            try:
                close_result = await _invoke_client(client.close_position, trade.symbol)
            except Exception as e:
                logger.error("Failed to close trade #%s on exchange: %s", trade_id, e)
                raise RuntimeError(f"Position close failed; trade remains open: {e}") from e
            if not isinstance(close_result, dict) or close_result.get("success") is not True:
                raise RuntimeError(f"Position close rejected; trade remains open: {close_result}")

            # 2. Close position in database
            await TradeRepository.close_position(
                session=session,
                symbol=trade.symbol,
                exit_price=exit_price,
                realized_pnl=realized_pnl,
                exit_reason=exit_reason,
                subaccount=trade.subaccount,
            )

            # 3. Transition trade state to EXITED
            trade = await TradeRepository.transition_trade_state(
                session=session,
                decision_id=trade_id,
                target_state=TradeLifecycleState.EXITED,
                exit_price=exit_price,
                exit_reason=exit_reason,
                realized_pnl=realized_pnl,
                notes=f"Exited via {exit_reason} at ${exit_price:,.2f}. Realized PnL: ${realized_pnl:+,.4f}",
            )
            await session.commit()

        return self._build_step_response(
            trade=trade,
            prev_state=TradeLifecycleState.POSITION_OPEN.value,
            message=f"Trade #{trade_id} closed ({exit_reason}) at ${exit_price:,.2f}. Realized PnL: ${realized_pnl:+,.4f}",
            is_terminal=True,
        )

    async def check_open_positions_triggers(self) -> List[Dict[str, Any]]:
        """Scans all open positions against live market prices for TP/SL hits and trailing stops."""
        exited_results: List[Dict[str, Any]] = []

        async with get_db_session() as session:
            open_positions = await TradeRepository.get_open_positions(session)
            if not open_positions:
                return []

            for pos in open_positions:
                symbol = pos.symbol
                live_price_info = market_engine.get_live_price(symbol)
                if not live_price_info:
                    continue

                mark_price = live_price_info.get("mark_price", pos.entry_price)
                pos.current_price = mark_price

                # Calculate unrealized PnL
                contract_unit = risk_engine.get_contract_unit(symbol)
                price_diff = (mark_price - pos.entry_price) if pos.size > 0 else (pos.entry_price - mark_price)
                pos.unrealized_pnl = round(price_diff * contract_unit * abs(pos.size), 4)

                tp = pos.take_profit_price
                sl = pos.stop_loss_price
                trade_id = pos.trade_decision_id

                if not trade_id or not tp or not sl:
                    continue

                # Check triggers for LONG
                if pos.size > 0:
                    if mark_price >= tp:
                        res = await self.close_trade(trade_id=trade_id, exit_reason="TAKE_PROFIT_TRIGGER", custom_exit_price=tp)
                        exited_results.append(res.model_dump())
                        continue
                    elif mark_price <= sl:
                        res = await self.close_trade(trade_id=trade_id, exit_reason="STOP_LOSS_TRIGGER", custom_exit_price=sl)
                        exited_results.append(res.model_dump())
                        continue
                    # Trailing stop to break-even if in +1R profit
                    r1_dist = abs(pos.entry_price - sl)
                    if (mark_price - pos.entry_price) >= r1_dist and sl < pos.entry_price:
                        pos.stop_loss_price = pos.entry_price
                        logger.info(f"Trailing Stop moved to Break-Even for {symbol} at ${pos.entry_price:,.2f}")

                # Check triggers for SHORT
                elif pos.size < 0:
                    if mark_price <= tp:
                        res = await self.close_trade(trade_id=trade_id, exit_reason="TAKE_PROFIT_TRIGGER", custom_exit_price=tp)
                        exited_results.append(res.model_dump())
                        continue
                    elif mark_price >= sl:
                        res = await self.close_trade(trade_id=trade_id, exit_reason="STOP_LOSS_TRIGGER", custom_exit_price=sl)
                        exited_results.append(res.model_dump())
                        continue
                    # Trailing stop to break-even if in +1R profit
                    r1_dist = abs(sl - pos.entry_price)
                    if (pos.entry_price - mark_price) >= r1_dist and sl > pos.entry_price:
                        pos.stop_loss_price = pos.entry_price
                        logger.info(f"Trailing Stop moved to Break-Even for {symbol} at ${pos.entry_price:,.2f}")

            await session.commit()

        return exited_results

    async def execute_kill_switch(self, subaccount: Optional[str] = None, reason: str = "KILL_SWITCH") -> KillSwitchResponse:
        """Emergency circuit breaker: cancels all orders and liquidates open positions."""
        affected_trades: List[int] = []
        cancelled_orders = 0
        closed_positions = 0

        subaccounts = [subaccount] if subaccount else ["futures", "options"]

        for sub in subaccounts:
            client = client_factory.get_client(sub)
            try:
                await _invoke_client(client.cancel_all_orders)
                await _invoke_client(client.close_all_positions)
            except Exception as e:
                logger.error(f"Error executing kill switch on exchange for {sub}: {e}")

        # Reconcile database state
        async with get_db_session() as session:
            open_trades = await TradeRepository.get_trade_decisions(session, active_only=True)
            for t in open_trades:
                if subaccount and t.subaccount != subaccount:
                    continue
                affected_trades.append(t.id)
                await TradeRepository.transition_trade_state(
                    session=session,
                    decision_id=t.id,
                    target_state=TradeLifecycleState.EXITED,
                    exit_reason="KILL_SWITCH_LIQUIDATED",
                    notes=f"Closed by emergency kill-switch: {reason}",
                )

            open_positions = await TradeRepository.get_open_positions(session)
            for pos in open_positions:
                if subaccount and pos.subaccount != subaccount:
                    continue
                pos.is_open = False
                pos.exit_reason = "KILL_SWITCH"
                closed_positions += 1

            # Log audit trail
            await TradeRepository.create_audit_log(
                session=session,
                message=f"EMERGENCY KILL SWITCH ACTIVATED: Liquidated {closed_positions} positions across {len(affected_trades)} trades",
                event_type=AuditEventType.SYSTEM,
                severity=AuditSeverity.CRITICAL,
                payload={
                    "reason": reason,
                    "subaccount": subaccount,
                    "affected_trades": affected_trades,
                    "closed_positions": closed_positions,
                },
            )
            await session.commit()

        return KillSwitchResponse(
            success=True,
            message=f"Emergency kill switch executed: {closed_positions} positions closed, orders cancelled.",
            cancelled_orders_count=cancelled_orders,
            closed_positions_count=closed_positions,
            affected_trades=affected_trades,
        )

    def _build_step_response(
        self,
        trade: TradeDecision,
        prev_state: str,
        message: str,
        is_terminal: bool = False,
        llm_val: Optional[Dict[str, Any]] = None,
        risk_val: Optional[Dict[str, Any]] = None,
    ) -> TradeLifecycleStepResponse:
        orders_snapshot: List[OrderSnapshot] = []
        if trade.orders:
            for o in trade.orders:
                orders_snapshot.append(
                    OrderSnapshot(
                        id=o.id,
                        exchange_order_id=o.exchange_order_id,
                        symbol=o.symbol,
                        side=o.side,
                        order_type=o.order_type,
                        price=o.price,
                        stop_price=o.stop_price,
                        size=o.size,
                        unfilled_size=o.unfilled_size,
                        state=o.state,
                        is_paper=o.is_paper,
                        is_bracket=o.is_bracket,
                        created_at=o.created_at.isoformat() if o.created_at else None,
                    )
                )

        pos_snapshot: Optional[PositionSnapshot] = None
        if trade.positions:
            p = trade.positions[0]
            pos_snapshot = PositionSnapshot(
                id=p.id,
                symbol=p.symbol,
                size=p.size,
                entry_price=p.entry_price,
                current_price=p.current_price,
                liquidation_price=p.liquidation_price,
                margin=p.margin,
                leverage=p.leverage,
                unrealized_pnl=p.unrealized_pnl,
                realized_pnl=p.realized_pnl,
                take_profit_price=p.take_profit_price,
                stop_loss_price=p.stop_loss_price,
                is_open=p.is_open,
                opened_at=p.opened_at.isoformat() if p.opened_at else None,
            )

        return TradeLifecycleStepResponse(
            trade_id=trade.id,
            symbol=trade.symbol,
            direction=trade.direction,
            previous_state=prev_state,
            current_state=trade.state,
            is_terminal=is_terminal or trade.state in (TradeLifecycleState.EXITED.value, "REJECTED_LLM", "REJECTED_RISK", "CANCELLED"),
            message=message,
            llm_validation=llm_val,
            risk_validation=risk_val,
            orders=orders_snapshot,
            position=pos_snapshot,
            trade_summary=trade.to_dict(),
        )


# Global singleton instance
execution_engine = ExecutionOrchestrator()
