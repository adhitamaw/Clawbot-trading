"""
Advanced Risk & Capital Management Engine.

Handles:
1. Dynamic ATR-based position sizing (max 1% equity risk per trade)
2. Circuit breakers — Hard (6% daily DD) and Soft (4% daily DD)
3. Full transaction cost modeling pre-trade gate
4. Position limits and exposure management
5. Daily equity tracking and recovery monitoring

Formula (PRD spec):
lots = risk_amount / (sl_distance_price * point_value)
where risk_amount = current_equity * risk_pct
"""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Tuple
from enum import Enum

from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class CircuitBreakerLevel(str, Enum):
    NONE = "none"
    SOFT = "soft"      # 4% DD: reduce size 50%, tighten filters
    HARD = "hard"      # 6% DD: close all, halt trading


@dataclass
class PositionSize:
    """Result of position sizing calculation."""
    lots: float
    risk_amount: float            # absolute $ risk
    risk_pct: float               # % of equity at risk
    sl_distance: float            # SL distance in price units
    sl_distance_pips: float       # SL distance in pips
    point_value: float            # $ value per point per lot
    max_loss: float               # worst-case loss in $
    equity_used: float            # margin used
    free_margin_after: float      # free margin after trade
    is_valid: bool
    rejection_reason: str = ""
    
    def to_dict(self) -> dict:
        return {
            "lots": round(self.lots, 3),
            "risk_amount": round(self.risk_amount, 2),
            "risk_pct": round(self.risk_pct * 100, 2),
            "sl_distance_pips": round(self.sl_distance_pips, 1),
            "max_loss": round(self.max_loss, 2),
            "is_valid": self.is_valid,
        }


@dataclass
class CostGate:
    """Pre-trade transaction cost check."""
    total_cost_pips: float
    expected_edge_pips: float
    edge_cost_ratio: float
    is_viable: bool
    details: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "total_cost_pips": round(self.total_cost_pips, 2),
            "expected_edge_pips": round(self.expected_edge_pips, 2),
            "edge_cost_ratio": round(self.edge_cost_ratio, 2),
            "is_viable": self.is_viable,
            "details": self.details,
        }


@dataclass
class RiskVerdict:
    """Complete risk assessment for a potential trade."""
    approved: bool
    position_size: Optional[PositionSize] = None
    cost_gate: Optional[CostGate] = None
    circuit_breaker: CircuitBreakerLevel = CircuitBreakerLevel.NONE
    rejection_reasons: list = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "position_size": self.position_size.to_dict() if self.position_size else None,
            "cost_gate": self.cost_gate.to_dict() if self.cost_gate else None,
            "circuit_breaker": self.circuit_breaker,
            "rejection_reasons": self.rejection_reasons,
        }


class RiskManager:
    """
    Institutional risk management engine.
    
    Controls position sizing, circuit breakers, and pre-trade
    cost validation for every trade signal.
    """
    
    def __init__(self, config=None):
        """
        Initialize risk manager.
        
        Args:
            config: TradingSystemConfig.risk
        """
        # Position sizing defaults
        self.mr_risk_pct = 0.01          # 1% per MR trade
        self.tf_risk_pct = 0.006         # 0.6% per TF trade
        self.reduce_above_daily_dd_pct = 0.03  # halve risk if daily DD > 3%
        self.min_lot = 0.01
        self.max_lot = 5.0
        
        # Circuit breaker defaults
        self.hard_dd_pct = 0.06          # 6%
        self.soft_dd_pct = 0.04          # 4%
        self.daily_reset_utc = "00:00"
        
        # Cost model defaults
        self.min_edge_multiple = 2.5
        self.default_spread_pips = 0.30
        self.estimated_slippage_pips = 0.50
        self.commission_per_lot = 7.0
        self.overnight_swap_long = -3.0
        self.overnight_swap_short = -1.0
        
        if config is not None:
            self._apply_config(config)
        
        # State tracking
        self.daily_start_equity: float = 0.0
        self.peak_equity: float = 0.0
        self.peak_equity_time: Optional[datetime] = None
        self.current_equity: float = 0.0
        self.daily_dd: float = 0.0
        self.todays_date = datetime.now(timezone.utc).date()
        
        # Trade tracking
        self.trades_today: int = 0
        self.max_daily_trades: int = 10
        self._breaker_triggered: CircuitBreakerLevel = CircuitBreakerLevel.NONE
        self._breaker_triggered_at: Optional[datetime] = None
        
        # Point value for XAUUSD (1 lot = 100 oz, 0.01 move ≈ $1 for 0.01 lots)
        self.point_value_per_lot: float = 1.0
        self.point_size: float = 0.01  # typical XAUUSD point
    
    def _apply_config(self, config) -> None:
        """Apply configuration."""
        if hasattr(config, 'risk'):
            r = config.risk
            if hasattr(r, 'position_sizing'):
                ps = r.position_sizing
                self.mr_risk_pct = ps.mean_reversion_risk_pct
                self.tf_risk_pct = ps.trend_risk_pct
                self.reduce_above_daily_dd_pct = ps.reduce_above_daily_dd_pct
                self.min_lot = ps.min_lot
                self.max_lot = ps.max_lot
            
            if hasattr(r, 'circuit_breakers'):
                cb = r.circuit_breakers
                self.hard_dd_pct = cb.hard_dd_pct
                self.soft_dd_pct = cb.soft_dd_pct
                self.daily_reset_utc = cb.daily_reset_utc
            
            if hasattr(r, 'cost_model'):
                cm = r.cost_model
                self.min_edge_multiple = cm.min_edge_multiple
                self.default_spread_pips = cm.default_spread_pips
                self.estimated_slippage_pips = cm.estimated_slippage_pips
                self.commission_per_lot = cm.commission_per_lot
                self.overnight_swap_long = cm.overnight_swap_long
                self.overnight_swap_short = cm.overnight_swap_short
        
        if hasattr(config, 'trading'):
            self.max_daily_trades = config.trading.max_daily_trades
    
    # ── Equity Updates ────────────────────────────────────────────────────────
    
    def update_equity(self, equity: float, balance: float = 0.0,
                     margin: float = 0.0, margin_free: float = 0.0) -> None:
        """
        Update current equity and recalculate daily drawdown.
        
        Called every bar or on significant equity changes.
        """
        # Daily reset
        today = datetime.now(timezone.utc).date()
        if today > self.todays_date:
            self._daily_reset(equity)
            return
        
        self.current_equity = equity
        
        if self.daily_start_equity == 0:
            self.daily_start_equity = equity
        
        # Track peak equity
        if equity > self.peak_equity:
            self.peak_equity = equity
            self.peak_equity_time = datetime.now(timezone.utc)
        
        # Calculate daily DD
        if self.peak_equity > 0:
            self.daily_dd = (self.peak_equity - equity) / self.peak_equity
        
        # Check circuit breakers
        self._check_breakers()
    
    def _daily_reset(self, equity: float) -> None:
        """Reset daily tracking."""
        self.todays_date = datetime.now(timezone.utc).date()
        self.daily_start_equity = equity
        self.peak_equity = equity
        self.peak_equity_time = datetime.now(timezone.utc)
        self.daily_dd = 0.0
        self.trades_today = 0
        self._breaker_triggered = CircuitBreakerLevel.NONE
        self._breaker_triggered_at = None
        
        logger.info("risk_daily_reset", start_equity=round(equity, 2))
    
    # ── Circuit Breakers ──────────────────────────────────────────────────────
    
    def _check_breakers(self) -> CircuitBreakerLevel:
        """Check and enforce circuit breakers."""
        if self.daily_dd >= self.hard_dd_pct:
            if self._breaker_triggered != CircuitBreakerLevel.HARD:
                self._breaker_triggered = CircuitBreakerLevel.HARD
                self._breaker_triggered_at = datetime.now(timezone.utc)
                logger.critical(
                    "hard_breaker_triggered",
                    dd_pct=f"{self.daily_dd*100:.2f}%",
                    equity=round(self.current_equity, 2),
                    peak=round(self.peak_equity, 2),
                )
            return CircuitBreakerLevel.HARD
        
        if self.daily_dd >= self.soft_dd_pct:
            if self._breaker_triggered != CircuitBreakerLevel.SOFT:
                self._breaker_triggered = CircuitBreakerLevel.SOFT
                self._breaker_triggered_at = datetime.now(timezone.utc)
                logger.warning(
                    "soft_breaker_triggered",
                    dd_pct=f"{self.daily_dd*100:.2f}%",
                )
            return CircuitBreakerLevel.SOFT
        
        return CircuitBreakerLevel.NONE
    
    def is_halted(self) -> bool:
        """Check if trading is permanently halted for the day."""
        return self._breaker_triggered == CircuitBreakerLevel.HARD
    
    def is_soft_breaker(self) -> bool:
        """Check if soft breaker is active (reduce size)."""
        return self._breaker_triggered == CircuitBreakerLevel.SOFT
    
    def get_circuit_breaker_status(self) -> dict:
        return {
            "level": self._breaker_triggered,
            "triggered_at": self._breaker_triggered_at.isoformat() if self._breaker_triggered_at else None,
            "daily_dd_pct": round(self.daily_dd * 100, 2),
            "peak_equity": round(self.peak_equity, 2),
            "current_equity": round(self.current_equity, 2),
        }
    
    # ── Dynamic Position Sizing ───────────────────────────────────────────────
    
    def calculate_position_size(self, entry_price: float, stop_loss: float,
                                regime: str, atr: float = 0.0,
                                spread_pips: float = None) -> PositionSize:
        """
        Calculate dynamic position size based on ATR and risk budget.
        
        Formula: lots = risk_amount / (sl_distance * point_value)
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            regime: 'mean_reversion' or 'trend_following'
            atr: Current ATR value
            spread_pips: Current spread in pips (optional)
            
        Returns:
            PositionSize with recommended lot size.
        """
        # Base risk percentage
        risk_pct = self.mr_risk_pct if regime == "mean_reversion" else self.tf_risk_pct
        
        # Reduce risk if daily DD already > threshold
        if self.daily_dd >= self.reduce_above_daily_dd_pct:
            risk_pct = risk_pct * 0.5
            logger.info("risk_halved_due_to_dd", original=risk_pct*2, reduced=risk_pct)
        
        # Apply soft breaker reduction
        if self.is_soft_breaker():
            risk_pct = risk_pct * 0.5
        
        # Calculate SL distance
        if stop_loss <= 0 or entry_price <= 0:
            return PositionSize(
                lots=0, risk_amount=0, risk_pct=risk_pct,
                sl_distance=0, sl_distance_pips=0,
                point_value=self.point_value_per_lot,
                max_loss=0, equity_used=0, free_margin_after=0,
                is_valid=False, rejection_reason="Invalid prices"
            )
        
        sl_distance = abs(entry_price - stop_loss)
        sl_distance_pips = sl_distance / self.point_size
        
        # Minimum SL distance
        if sl_distance_pips < 5:
            return PositionSize(
                lots=0, risk_amount=0, risk_pct=risk_pct,
                sl_distance=sl_distance, sl_distance_pips=sl_distance_pips,
                point_value=self.point_value_per_lot,
                max_loss=0, equity_used=0, free_margin_after=0,
                is_valid=False, rejection_reason="SL too close (< 5 pips)"
            )
        
        # Risk amount in account currency
        equity = self.current_equity if self.current_equity > 0 else 10000.0
        risk_amount = equity * risk_pct
        
        # Lot calculation
        point_value = self.point_value_per_lot
        lots = risk_amount / (sl_distance_pips * point_value)
        
        # Round to broker step (0.01 for standard XAUUSD)
        lot_step = 0.01
        lots = round(lots / lot_step) * lot_step
        
        # Enforce limits
        if lots < self.min_lot:
            if lots < self.min_lot * 0.5:
                return PositionSize(
                    lots=0, risk_amount=0, risk_pct=risk_pct,
                    sl_distance=sl_distance, sl_distance_pips=sl_distance_pips,
                    point_value=point_value,
                    max_loss=0, equity_used=0, free_margin_after=0,
                    is_valid=False, rejection_reason=f"Lot too small ({lots:.4f} < {self.min_lot})"
                )
            lots = self.min_lot  # round up to min
        
        lots = min(lots, self.max_lot)
        
        # Calculate max loss
        max_loss = lots * sl_distance_pips * point_value
        
        # Estimated margin (1% for XAUUSD with 100:1 leverage, adjust per broker)
        estimated_margin = lots * entry_price * 100 * 0.01  # rough estimate
        
        return PositionSize(
            lots=lots,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            sl_distance=sl_distance,
            sl_distance_pips=sl_distance_pips,
            point_value=point_value,
            max_loss=max_loss,
            equity_used=estimated_margin,
            free_margin_after=equity - estimated_margin,
            is_valid=True,
        )
    
    # ── Transaction Cost Model ────────────────────────────────────────────────
    
    def check_cost_gate(self, entry_price: float, stop_loss: float,
                       take_profit: float, direction: str = "long",
                       current_spread_pips: float = None,
                       hold_hours: float = 1.0) -> CostGate:
        """
        Pre-trade transaction cost gate.
        
        total_cost = spread + estimated_slippage + commission + (swap if overnight)
        Must have: expected_edge > total_cost * min_edge_multiple
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            direction: 'long' or 'short'
            current_spread_pips: Live spread (optional)
            hold_hours: Expected hold time in hours
            
        Returns:
            CostGate with viability assessment.
        """
        # Calculate expected edge and risk
        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            return CostGate(0, 0, 0, False, {"error": "Invalid prices"})
        
        expected_sl_pips = abs(entry_price - stop_loss) / self.point_size
        expected_tp_pips = abs(take_profit - entry_price) / self.point_size
        expected_edge_pips = expected_tp_pips - expected_sl_pips
        
        # Spread cost
        spread_cost = current_spread_pips if current_spread_pips else self.default_spread_pips
        
        # Slippage estimate
        slippage = self.estimated_slippage_pips
        
        # Commission (per lot round-turn, converted to pips per 0.01 lot)
        commission_pips = self.commission_per_lot / (self.point_value_per_lot) * 0.01
        
        # Swap (if holding overnight)
        swap_pips = 0
        if hold_hours > 6:  # likely overnight
            swap_rate = self.overnight_swap_long if direction == "long" else self.overnight_swap_short
            swap_pips = abs(swap_rate) * 0.01  # approximate
        
        total_cost_pips = spread_cost + slippage + commission_pips + swap_pips
        
        # Viability check
        edge_cost_ratio = expected_edge_pips / total_cost_pips if total_cost_pips > 0 else float('inf')
        is_viable = edge_cost_ratio >= self.min_edge_multiple
        
        details = {
            "spread": round(spread_cost, 3),
            "slippage": round(slippage, 3),
            "commission": round(commission_pips, 3),
            "swap": round(swap_pips, 3),
            "expected_sl_pips": round(expected_sl_pips, 1),
            "expected_tp_pips": round(expected_tp_pips, 1),
        }
        
        return CostGate(
            total_cost_pips=total_cost_pips,
            expected_edge_pips=expected_edge_pips,
            edge_cost_ratio=edge_cost_ratio,
            is_viable=is_viable,
            details=details,
        )
    
    # ── Complete Pre-Trade Approval ───────────────────────────────────────────
    
    def approve_trade(self, signal: dict, regime: str,
                     direction: str, entry_price: float,
                     stop_loss: float, take_profit: float,
                     atr: float = 0.0,
                     current_spread_pips: float = None) -> RiskVerdict:
        """
        Full pre-trade risk approval pipeline.
        
        1. Check circuit breaker
        2. Check daily trade limit
        3. Calculate position size
        4. Run cost model gate
        5. Return final verdict
        
        Args:
            signal: Signal dict from strategy module
            regime: 'mean_reversion' or 'trend_following'
            direction: 'long' or 'short'
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price (TP1)
            atr: Current ATR
            current_spread_pips: Live spread
            
        Returns:
            RiskVerdict with approval status.
        """
        rejection_reasons = []
        
        # 1. Circuit breaker check
        breaker = self._check_breakers()
        if breaker == CircuitBreakerLevel.HARD:
            rejection_reasons.append("hard_circuit_breaker")
            return RiskVerdict(
                approved=False,
                circuit_breaker=breaker,
                rejection_reasons=rejection_reasons,
            )
        
        # 2. Daily trade limit
        if self.trades_today >= self.max_daily_trades:
            rejection_reasons.append("max_daily_trades")
            return RiskVerdict(
                approved=False,
                rejection_reasons=rejection_reasons,
            )
        
        # 3. Position sizing
        size = self.calculate_position_size(
            entry_price=entry_price,
            stop_loss=stop_loss,
            regime=regime,
            atr=atr,
            spread_pips=current_spread_pips,
        )
        
        if not size.is_valid:
            rejection_reasons.append(f"position_sizing:{size.rejection_reason}")
            return RiskVerdict(
                approved=False,
                position_size=size,
                circuit_breaker=breaker,
                rejection_reasons=rejection_reasons,
            )
        
        # 4. Cost model
        cost = self.check_cost_gate(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            direction=direction,
            current_spread_pips=current_spread_pips,
        )
        
        if not cost.is_viable:
            rejection_reasons.append(f"cost_gate:edge_cost_ratio={cost.edge_cost_ratio:.2f}")
            return RiskVerdict(
                approved=False,
                position_size=size,
                cost_gate=cost,
                circuit_breaker=breaker,
                rejection_reasons=rejection_reasons,
            )
        
        # Approved!
        self.trades_today += 1
        
        logger.info(
            "trade_approved",
            regime=regime,
            direction=direction,
            lots=size.lots,
            risk_pct=f"{size.risk_pct*100:.2f}%",
            risk_amount=f"${size.risk_amount:.2f}",
            max_loss=f"${size.max_loss:.2f}",
            edge_cost_ratio=f"{cost.edge_cost_ratio:.2f}",
        )
        
        return RiskVerdict(
            approved=True,
            position_size=size,
            cost_gate=cost,
            circuit_breaker=breaker,
        )
    
    # ── Position Management ───────────────────────────────────────────────────
    
    def get_max_concurrent(self, regime: str) -> int:
        """Get max concurrent positions for a regime."""
        # Could be config-driven; for now return 1 each
        return 1
    
    def can_open_position(self, regime: str, current_positions: int) -> bool:
        """
        Check if a new position can be opened.
        
        Args:
            regime: Trading regime
            current_positions: Current open positions count
            
        Returns:
            True if position can be opened.
        """
        if self.is_halted():
            return False
        
        if self.trades_today >= self.max_daily_trades:
            return False
        
        max_concurrent = self.get_max_concurrent(regime)
        if current_positions >= max_concurrent:
            return False
        
        return True
    
    # ── Status ────────────────────────────────────────────────────────────────
    
    def get_status(self) -> dict:
        """Get comprehensive risk status."""
        return {
            "daily": {
                "start_equity": round(self.daily_start_equity, 2),
                "peak_equity": round(self.peak_equity, 2),
                "current_equity": round(self.current_equity, 2),
                "dd_pct": round(self.daily_dd * 100, 2),
                "trades_today": self.trades_today,
                "max_trades": self.max_daily_trades,
            },
            "circuit_breaker": self.get_circuit_breaker_status(),
            "position_sizing": {
                "mr_risk_pct": self.mr_risk_pct * 100,
                "tf_risk_pct": self.tf_risk_pct * 100,
                "min_lot": self.min_lot,
                "max_lot": self.max_lot,
            },
        }
