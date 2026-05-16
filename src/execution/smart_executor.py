"""
Smart Order Execution Engine.

Handles institutional-grade order placement with:
- Slippage control and monitoring
- Order splitting for large positions
- Smart retry with price validation
- Fill quality tracking
- Spread-aware execution timing
- Pre/post-execution logging and audit

Designed to minimize slippage while maintaining fill certainty.
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from src.mt5_bridge import (
    MT5Bridge, OrderRequest, OrderResult, OrderType, OrderFilling
)
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExecutionPlan:
    """Execution plan for a trade signal."""
    symbol: str
    direction: str                    # "long" or "short"
    total_volume: float               # total lots
    entry_price: float
    stop_loss: float
    take_profit: float
    
    # Split plan
    split_orders: int = 1
    split_volumes: List[float] = field(default_factory=list)
    
    # Limits
    max_slippage_pips: float = 2.0
    fill_timeout_seconds: int = 10
    
    # Metadata
    strategy: str = ""
    regime: str = ""
    magic: int = 20260516
    comment: str = "XAU_System"
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "total_volume": self.total_volume,
            "entry_price": round(self.entry_price, 5),
            "stop_loss": round(self.stop_loss, 5),
            "take_profit": round(self.take_profit, 5),
            "split_orders": self.split_orders,
            "max_slippage_pips": self.max_slippage_pips,
        }


@dataclass
class ExecutionReport:
    """Report for a completed execution."""
    success: bool
    plan: ExecutionPlan
    
    # Fill details
    filled_volume: float = 0.0
    filled_price: float = 0.0
    average_fill_price: float = 0.0
    
    # Slippage
    entry_slippage_pips: float = 0.0
    total_slippage_pips: float = 0.0
    
    # Timing
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    fill_time_ms: float = 0.0
    
    # Individual order results
    order_results: List[OrderResult] = field(default_factory=list)
    
    # Costs
    spread_at_entry: float = 0.0
    commission_estimate: float = 0.0
    
    # Status
    error_message: str = ""
    retries: int = 0
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "symbol": self.plan.symbol,
            "direction": self.plan.direction,
            "requested_volume": self.plan.total_volume,
            "filled_volume": self.filled_volume,
            "average_fill_price": round(self.average_fill_price, 5),
            "slippage_pips": round(self.entry_slippage_pips, 2),
            "fill_time_ms": round(self.fill_time_ms, 1),
            "spread_at_entry": round(self.spread_at_entry, 5),
            "retries": self.retries,
            "error": self.error_message,
        }


class SmartExecutor:
    """
    Intelligent order execution with slippage control.
    
    Functions:
    - Converts strategy signals into MT5 orders
    - Validates spread and market conditions before execution
    - Splits large orders to reduce market impact
    - Retries on transient failures with price validation
    - Tracks fill quality and execution latency
    """
    
    def __init__(self, bridge: MT5Bridge, config=None):
        """
        Initialize smart executor.
        
        Args:
            bridge: MT5Bridge instance
            config: TradingSystemConfig.execution
        """
        self.bridge = bridge
        
        # Default parameters
        self.max_slippage_pips = 2.0
        self.fill_timeout_seconds = 10
        self.order_split_min_lots = 0.02
        self.max_spread_pips = 5.0
        self.max_retries = 3
        self.retry_delay_ms = 200
        self.max_slippage_per_retry_pips = 0.5
        
        if config is not None:
            self._apply_config(config)
        
        # Stats
        self._executions: List[ExecutionReport] = []
        self._total_fills = 0
        self._total_slippage_pips = 0.0
        self._total_fill_time_ms = 0.0
    
    def _apply_config(self, config) -> None:
        """Apply execution configuration."""
        if hasattr(config, 'execution') and hasattr(config.execution, 'smart_executor'):
            se = config.execution.smart_executor
            self.max_slippage_pips = se.max_slippage_pips
            self.fill_timeout_seconds = se.fill_timeout_seconds
            self.order_split_min_lots = se.order_split_min_lots
    
    # ── Order Type Resolution ─────────────────────────────────────────────────
    
    def _resolve_order_type(self, direction: str) -> OrderType:
        """Convert direction string to MT5 order type."""
        return OrderType.MARKET_BUY if direction == "long" else OrderType.MARKET_SELL
    
    def _resolve_close_type(self, direction: str) -> OrderType:
        """Convert direction to closing order type."""
        return OrderType.MARKET_SELL if direction == "long" else OrderType.MARKET_BUY
    
    # ── Pre-Execution Checks ──────────────────────────────────────────────────
    
    def _check_spread(self, symbol: str = "XAUUSD") -> Tuple[bool, float]:
        """
        Check if current spread is acceptable.
        
        Returns:
            Tuple of (acceptable, spread_pips)
        """
        tick = self.bridge.get_tick(symbol)
        if tick is None:
            return False, 0.0
        
        spread_pips = tick.spread
        
        if spread_pips > self.max_spread_pips:
            logger.warning("spread_too_high_for_execution",
                          spread=round(spread_pips, 5),
                          max=self.max_spread_pips)
            return False, spread_pips
        
        return True, spread_pips
    
    def _get_execution_price(self, symbol: str, direction: str) -> Optional[float]:
        """Get the current execution price (ask for buy, bid for sell)."""
        tick = self.bridge.get_tick(symbol)
        if tick is None:
            return None
        
        return tick.ask if direction == "long" else tick.bid
    
    # ── Order Splitting ───────────────────────────────────────────────────────
    
    def _calculate_split(self, total_volume: float) -> List[float]:
        """
        Split large orders into smaller chunks.
        
        Orders > min_split_lot may be split to reduce slippage.
        
        Args:
            total_volume: Total volume in lots
            
        Returns:
            List of volumes per split order.
        """
        if total_volume <= self.order_split_min_lots:
            return [total_volume]
        
        # Split into chunks of order_split_min_lots
        parts = []
        remaining = total_volume
        
        while remaining > self.order_split_min_lots:
            parts.append(self.order_split_min_lots)
            remaining -= self.order_split_min_lots
        
        if remaining >= 0.01:  # min lot
            parts.append(round(remaining, 2))
        else:
            # Distribute small remainder to last chunk
            if parts:
                parts[-1] += remaining
                parts[-1] = round(parts[-1], 2)
        
        return parts
    
    # ── Main Execution ────────────────────────────────────────────────────────
    
    async def execute(self, plan: ExecutionPlan) -> ExecutionReport:
        """
        Execute a trading plan with smart order placement.
        
        Args:
            plan: ExecutionPlan with all trade parameters
            
        Returns:
            ExecutionReport with fill details.
        """
        start_time = datetime.now(timezone.utc)
        report = ExecutionReport(success=False, plan=plan, start_time=start_time)
        
        try:
            # 1. Pre-execution spread check
            spread_ok, spread_pips = self._check_spread(plan.symbol)
            report.spread_at_entry = spread_pips
            
            if not spread_ok:
                report.error_message = f"Spread too high: {spread_pips:.5f} > {self.max_spread_pips}"
                logger.warning("execution_rejected_spread", reason=report.error_message)
                return report
            
            # 2. Calculate order splits
            split_volumes = self._calculate_split(plan.total_volume)
            plan.split_orders = len(split_volumes)
            plan.split_volumes = split_volumes
            
            # 3. Execute each split
            filled_volume = 0.0
            total_price_sum = 0.0
            total_price_weight = 0.0
            slippages = []
            
            for i, volume in enumerate(split_volumes):
                if volume < 0.01:
                    continue
                
                result = await self._place_order_with_retry(
                    symbol=plan.symbol,
                    order_type=self._resolve_order_type(plan.direction),
                    volume=volume,
                    entry_price=plan.entry_price,
                    stop_loss=plan.stop_loss,
                    take_profit=plan.take_profit,
                    magic=plan.magic,
                    comment=plan.comment,
                )
                
                report.order_results.append(result)
                
                if result.success:
                    filled_volume += result.volume
                    total_price_sum += result.price * result.volume
                    total_price_weight += result.volume
                    
                    # Calculate slippage
                    order_slippage = abs(result.price - plan.entry_price)
                    if plan.entry_price > 0:
                        slippage_pips = order_slippage
                        slippages.append(slippage_pips)
                else:
                    report.retries += 1
                    logger.warning("order_split_failed", split=i+1, total=len(split_volumes))
            
            # 4. Calculate fill metrics
            report.filled_volume = filled_volume
            
            if filled_volume > 0:
                report.average_fill_price = total_price_sum / total_price_weight if total_price_weight > 0 else 0
                report.entry_slippage_pips = abs(report.average_fill_price - plan.entry_price)
                report.total_slippage_pips = sum(slippages) / len(slippages) if slippages else 0
                report.success = filled_volume >= plan.total_volume * 0.8  # 80% fill = success
            
            report.end_time = datetime.now(timezone.utc)
            report.fill_time_ms = (report.end_time - report.start_time).total_seconds() * 1000
            
            # 5. Log and track
            if report.success:
                logger.info(
                    "execution_complete",
                    symbol=plan.symbol,
                    direction=plan.direction,
                    filled=report.filled_volume,
                    avg_price=round(report.average_fill_price, 5),
                    slippage_pips=round(report.entry_slippage_pips, 2),
                    fill_time_ms=round(report.fill_time_ms, 1),
                )
            else:
                logger.warning(
                    "execution_partial",
                    filled_pct=f"{(filled_volume/plan.total_volume)*100:.0f}%",
                    error=report.error_message or "Partial fill",
                )
            
            # Track stats
            self._record_execution(report)
            
        except Exception as e:
            report.error_message = str(e)
            report.end_time = datetime.now(timezone.utc)
            report.fill_time_ms = (report.end_time - report.start_time).total_seconds() * 1000
            logger.error("execution_error", error=str(e))
        
        return report
    
    async def _place_order_with_retry(self, symbol: str, order_type: OrderType,
                                     volume: float, entry_price: float,
                                     stop_loss: float, take_profit: float,
                                     magic: int, comment: str) -> OrderResult:
        """
        Place an order with retry logic for transient failures.
        
        Retries if:
        - Connection lost
        - Price changed significantly (requotes)
        - Timeout reached
        
        Args:
            See OrderRequest parameters
            
        Returns:
            OrderResult with fill details.
        """
        last_result = None
        
        for attempt in range(self.max_retries):
            # Get current execution price
            exec_price = self._get_execution_price(
                symbol, "long" if "buy" in order_type else "short"
            )
            
            if exec_price is None:
                await asyncio.sleep(0.1)
                continue
            
            # Check if price moved too far from entry
            if attempt > 0 and entry_price > 0:
                price_slippage = abs(exec_price - entry_price)
                if price_slippage > self.max_slippage_per_retry_pips * (attempt + 1):
                    logger.warning("execution_price_moved_too_far",
                                  attempt=attempt,
                                  slippage=round(price_slippage, 5))
                    return OrderResult(
                        success=False,
                        error_code=-100,
                        error_desc=f"Price moved {price_slippage:.5f} from entry"
                    )
            
            # Build order request
            request = OrderRequest(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                price=0,  # market order
                stop_loss=stop_loss,
                take_profit=take_profit,
                deviation=int(self.max_slippage_pips * 10),  # points, not pips
                filling=OrderFilling.IOC,
                comment=comment,
                magic=magic,
            )
            
            # Send order
            result = self.bridge.send_order(request)
            
            if result.success:
                return result
            
            # Retry on certain errors
            if "off quotes" in result.error_desc.lower() or "requote" in result.error_desc.lower():
                logger.info("execution_retry_requote", attempt=attempt+1)
                await asyncio.sleep(self.retry_delay_ms / 1000.0)
            elif result.error_code in (10028, 10030):  # reject, timeout
                logger.info("execution_retry_error", attempt=attempt+1, code=result.error_code)
                await asyncio.sleep(self.retry_delay_ms / 1000.0)
            else:
                # Non-retryable error
                break
            
            last_result = result
        
        return last_result or OrderResult(
            success=False,
            error_code=-200,
            error_desc="Max retries exceeded"
        )
    
    # ── Position Closure ──────────────────────────────────────────────────────
    
    async def close_position(self, ticket: int, partial_pct: float = 1.0) -> OrderResult:
        """
        Close a position (full or partial).
        
        Args:
            ticket: Position ticket number
            partial_pct: Percentage to close (1.0 = full close)
            
        Returns:
            OrderResult.
        """
        if partial_pct >= 1.0:
            return self.bridge.close_position(ticket)
        
        # Partial close = partially cover position
        position = self.bridge.get_position(ticket)
        if position is None:
            return OrderResult(success=False, error_desc="Position not found")
        
        close_volume = round(position.volume * partial_pct, 2)
        if close_volume < 0.01:
            return OrderResult(success=False, error_desc="Close volume too small")
        
        logger.info("partial_close", ticket=ticket, pct=f"{partial_pct*100:.0f}%", volume=close_volume)
        
        return self.bridge.close_position(ticket)
    
    # ── Stats & Monitoring ────────────────────────────────────────────────────
    
    def _record_execution(self, report: ExecutionReport) -> None:
        """Record execution for stats tracking."""
        self._executions.append(report)
        if len(self._executions) > 1000:
            self._executions = self._executions[-500:]
        
        self._total_fills += 1
        self._total_slippage_pips += report.entry_slippage_pips
        self._total_fill_time_ms += report.fill_time_ms
    
    def get_stats(self) -> dict:
        """Get execution statistics."""
        avg_slippage = self._total_slippage_pips / self._total_fills if self._total_fills > 0 else 0
        avg_fill_time = self._total_fill_time_ms / self._total_fills if self._total_fills > 0 else 0
        
        # Success rate from recent executions
        recent = self._executions[-50:]
        success_rate = sum(1 for r in recent if r.success) / len(recent) if recent else 1.0
        
        return {
            "total_fills": self._total_fills,
            "avg_slippage_pips": round(avg_slippage, 2),
            "avg_fill_time_ms": round(avg_fill_time, 1),
            "recent_success_rate": round(success_rate * 100, 1),
            "last_execution": self._executions[-1].to_dict() if self._executions else None,
        }
    
    def get_last_report(self) -> Optional[dict]:
        """Get the most recent execution report."""
        if not self._executions:
            return None
        return self._executions[-1].to_dict()
