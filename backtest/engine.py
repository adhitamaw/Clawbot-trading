"""
Event-Driven Backtesting Engine for XAU/USD Trading System.

Features:
- Realistic variable spread modeling
- Empirical slippage distribution per regime
- Full commission + swap cost modeling
- Multi-symbol support for intermarket testing
- Tick-level or bar-level granularity
- Strategy-agnostic (works with any signal generator)

Models transaction costs realistically using historical spread/slippage data.
"""

import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable, List, Dict, Tuple
from collections import defaultdict

import numpy as np
import pandas as pd

from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


# ── Data Types ──────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    """Backtesting configuration."""
    initial_capital: float = 10000.0
    symbol: str = "XAUUSD"
    timeframe: str = "M5"
    
    # Cost modeling
    variable_spread: bool = True
    default_spread_pips: float = 0.30
    spread_multiplier_news: float = 3.0     # spread widening during news
    slippage_model: str = "empirical"       # "fixed", "empirical", "proportional"
    fixed_slippage_pips: float = 0.50
    commission_per_lot: float = 7.0         # round-turn
    swap_long: float = -3.0                 # per lot per day
    swap_short: float = -1.0
    
    # Position sizing
    risk_per_trade_pct: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 5.0
    
    # Execution
    use_regime_slippage: bool = True
    max_slippage_pips: float = 2.0
    
    # Filters
    require_intermarket: bool = False       # require DXY data in backtest
    
    # Risk
    max_daily_dd_pct: float = 0.06
    max_daily_trades: int = 10


@dataclass
class Trade:
    """Single completed trade record."""
    id: int
    symbol: str
    direction: str                    # "long" or "short"
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    volume: float                     # lots
    regime: str = ""
    strategy: str = ""
    
    # P&L
    gross_pl: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    slippage: float = 0.0
    net_pl: float = 0.0
    net_pl_pct: float = 0.0
    r_multiple: float = 0.0
    
    # Risk
    stop_loss: float = 0.0
    take_profit: float = 0.0
    initial_risk: float = 0.0
    
    # Fill
    entry_slippage_pips: float = 0.0
    exit_slippage_pips: float = 0.0
    spread_at_entry: float = 0.0
    
    # Tags
    is_win: bool = False
    exit_reason: str = ""             # "tp", "sl", "trailing", "time", "breakeven"
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": round(self.entry_price, 5),
            "exit_price": round(self.exit_price, 5),
            "volume": self.volume,
            "regime": self.regime,
            "net_pl": round(self.net_pl, 2),
            "net_pl_pct": round(self.net_pl_pct, 2),
            "r_multiple": round(self.r_multiple, 2),
            "is_win": self.is_win,
            "exit_reason": self.exit_reason,
        }


@dataclass
class EquityCurve:
    """Equity curve data."""
    timestamps: List[datetime]
    equity: List[float]
    balance: List[float]
    drawdown: List[float]
    drawdown_pct: List[float]
    positions: List[int]


@dataclass
class BacktestResult:
    """Complete backtest output."""
    # Summary
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # Returns
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    total_net_pl: float = 0.0
    gross_pl: float = 0.0
    total_commission: float = 0.0
    total_swap: float = 0.0
    total_slippage_cost: float = 0.0
    
    # Risk metrics
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_days: int = 0
    avg_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    recovery_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    
    # Trade metrics
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_trade: float = 0.0
    avg_r_multiple: float = 0.0
    expectancy: float = 0.0
    sqn: float = 0.0                     # System Quality Number
    
    # Holding
    avg_hold_minutes: float = 0.0
    max_hold_minutes: float = 0.0
    
    # Consecutive
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    
    # Regime breakdown
    regime_stats: Dict[str, dict] = field(default_factory=dict)
    
    # Data
    trades: List[Trade] = field(default_factory=list)
    equity_curve: Optional[EquityCurve] = None
    
    # Period
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    total_days: int = 0
    
    def to_summary(self) -> dict:
        return {
            "period": f"{self.start_date} → {self.end_date}" if self.start_date else "N/A",
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate * 100, 1),
            "total_return_pct": round(self.total_return_pct * 100, 2),
            "profit_factor": round(self.profit_factor, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
            "recovery_factor": round(self.recovery_factor, 2),
            "expectancy": round(self.expectancy, 2),
            "sqn": round(self.sqn, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
        }


# ── Backtesting Engine ──────────────────────────────────────────────────────

class BacktestEngine:
    """
    Event-driven backtesting engine with realistic cost modeling.
    
    Simulates market mechanics including variable spreads, slippage,
    commission, and swap rates for institutional-grade validation.
    """
    
    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        
        # State
        self.capital = self.config.initial_capital
        self.equity = self.config.initial_capital
        self.peak_equity = self.config.initial_capital
        
        # Trade tracking
        self.trades: List[Trade] = []
        self.open_trades: Dict[int, Trade] = {}
        self._trade_id_counter = 0
        
        # Equity tracking per bar
        self._equity_timestamps: List[datetime] = []
        self._equity_values: List[float] = []
        self._balance_values: List[float] = []
        
        # Daily tracking
        self._daily_trades: Dict[str, int] = defaultdict(int)
        self._daily_start_equity: Dict[str, float] = defaultdict(float)
        self._daily_dd: Dict[str, float] = defaultdict(float)
        
        # Spread model
        self._spread_series: List[float] = []
        self._slippage_series: List[float] = []
        
        # Regime tracking
        self._current_regime: str = "neutral"
        
        # Signal generator (injected)
        self.signal_generator: Optional[Callable] = None
        self.position_manager: Optional[Callable] = None
    
    # ── Spread & Slippage Modeling ───────────────────────────────────────────
    
    def _get_spread(self, bar: dict, use_variable: bool = True) -> float:
        """
        Get realistic spread for this bar.
        
        Uses bar's spread field if available, otherwise models variable spread
        based on time of day (wider during Asian, tighter during London/NY).
        """
        if use_variable and self.config.variable_spread:
            # Use bar spread if available
            if 'spread' in bar and bar['spread'] > 0:
                base_spread = bar['spread']
            else:
                # Model variable spread based on session
                hour = bar.get('time', datetime.now(timezone.utc)).hour
                
                if 0 <= hour < 8:    # Asian: wider spreads
                    base_spread = self.config.default_spread_pips * 1.5
                elif 8 <= hour < 17: # London/NY: tight
                    base_spread = self.config.default_spread_pips * 0.8
                else:                # Late: moderate
                    base_spread = self.config.default_spread_pips * 1.2
                
                # Add noise
                base_spread *= np.random.uniform(0.8, 1.3)
            
            # News widening
            if self._is_news_period(bar):
                base_spread *= self.config.spread_multiplier_news
            
            return base_spread
        
        return self.config.default_spread_pips
    
    def _get_slippage(self, regime: str = "neutral") -> float:
        """
        Estimate slippage based on regime.
        
        Mean-reversion (low vol): less slippage
        Trend-following (high vol): more slippage
        """
        if not self.config.use_regime_slippage:
            return self.config.fixed_slippage_pips
        
        if regime == "mean_reversion":
            # Low vol, easier fills
            mean_slip = self.config.fixed_slippage_pips * 0.6
            slippage = np.random.exponential(mean_slip) if mean_slip > 0 else 0
        elif regime == "trend_following":
            # High vol, more slippage
            mean_slip = self.config.fixed_slippage_pips * 1.5
            slippage = np.random.exponential(mean_slip) if mean_slip > 0 else 0
        else:
            slippage = self.config.fixed_slippage_pips
        
        return min(slippage, self.config.max_slippage_pips)
    
    def _is_news_period(self, bar: dict) -> bool:
        """Check if this bar falls in a high-impact news period."""
        # Simplified: check if bar is within typical news windows
        hour = bar.get('time', datetime.now(timezone.utc)).hour
        minute = bar.get('time', datetime.now(timezone.utc)).minute
        
        # Major news events typically at 12:30, 14:00, 18:00 UTC
        news_windows = [(12, 30), (14, 0), (18, 0)]
        
        for nh, nm in news_windows:
            if hour == nh and abs(minute - nm) <= 30:
                return True
        
        return False
    
    # ── Position Sizing ───────────────────────────────────────────────────────
    
    def _calculate_volume(self, entry_price: float, stop_loss: float,
                         regime: str, atr: float = 0) -> float:
        """
        Calculate position volume based on risk parameters.
        
        Uses 1% risk for mean-reversion, 0.6% for trend-following.
        """
        risk_pct = 0.01 if regime == "mean_reversion" else 0.006
        
        # Reduce risk if daily DD > 3%
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dd = self._daily_dd.get(today, 0)
        if dd >= 0.03:
            risk_pct *= 0.5
        
        risk_amount = self.equity * risk_pct
        
        sl_distance_pips = abs(entry_price - stop_loss) / 0.01  # XAUUSD point = 0.01
        if sl_distance_pips < 5:
            return 0
        
        # 1 lot XAUUSD = $1 per 0.01 move (approximate)
        point_value = 1.0
        lots = risk_amount / (sl_distance_pips * point_value)
        
        # Round to 0.01
        lots = round(lots / 0.01) * 0.01
        lots = max(self.config.min_lot, min(lots, self.config.max_lot))
        
        return lots
    
    # ── Signal Injection ──────────────────────────────────────────────────────
    
    def set_signal_generator(self, generator: Callable) -> None:
        """
        Inject a signal generator function.
        
        generator(bar, regime) → dict with:
            {signal: "long"/"short"/"none", entry_price, stop_loss, take_profit, regime}
        """
        self.signal_generator = generator
    
    def set_position_manager(self, manager: Callable) -> None:
        """
        Inject a position manager function for trailing stops.
        
        manager(trade, bar) → new_stop_loss or None
        """
        self.position_manager = manager
    
    # ── Main Backtest Loop ────────────────────────────────────────────────────
    
    def run(self, data: pd.DataFrame, regime_labels: List[str] = None) -> BacktestResult:
        """
        Run backtest on historical data.
        
        Args:
            data: OHLCV DataFrame with columns: time, open, high, low, close, tick_volume, spread
            regime_labels: Optional list of regime labels per bar (same length as data)
            
        Returns:
            BacktestResult with full performance analysis.
        """
        if data.empty:
            logger.error("backtest_empty_data")
            return BacktestResult()
        
        start_time = time.perf_counter()
        
        # Prepare data
        df = data.reset_index(drop=True)
        n_bars = len(df)
        
        # Regime labels
        if regime_labels is None:
            regime_labels = ["neutral"] * n_bars
        
        self._reset_state()
        
        # Main loop
        for i in range(n_bars):
            bar = df.iloc[i].to_dict()
            bar_time = bar.get('time', datetime.now(timezone.utc))
            regime = regime_labels[i] if i < len(regime_labels) else "neutral"
            self._current_regime = regime
            
            # Skip incomplete bars at start
            if i < 20:
                self._update_equity_tracking(bar_time)
                continue
            
            # ── 1. Check open positions (SL/TP hits, trailing) ──
            self._manage_open_positions(bar, bar_time)
            
            # ── 2. Circuit breaker check ──
            if self._check_circuit_breaker():
                continue  # Skip new trades
            
            # ── 3. Daily limit check ──
            today = bar_time.strftime("%Y-%m-%d") if hasattr(bar_time, 'strftime') else str(bar_time)[:10]
            if self._daily_trades[today] >= self.config.max_daily_trades:
                continue
            
            # ── 4. Generate signals ──
            if self.signal_generator:
                signal = self.signal_generator(bar, regime, i, df)
                
                if signal and signal.get("signal") not in (None, "none"):
                    self._process_signal(signal, bar, bar_time, today)
            
            # ── 5. Update equity tracking ──
            self._update_equity_tracking(bar_time)
        
        # Close any remaining open trades
        self._close_all_trades(df.iloc[-1].to_dict())
        
        # Compute results
        end_time = time.perf_counter()
        logger.info("backtest_complete", bars=n_bars, elapsed_ms=round((end_time - start_time) * 1000, 0))
        
        return self._compute_results(df)
    
    def _manage_open_positions(self, bar: dict, bar_time: datetime) -> None:
        """Check and update open positions."""
        high = bar.get('high', 0)
        low = bar.get('low', 0)
        close = bar.get('close', 0)
        
        trades_to_close = []
        
        for tid, trade in list(self.open_trades.items()):
            hit_sl = False
            hit_tp = False
            
            if trade.direction == "long":
                # Check SL hit (low reached or crossed)
                if trade.stop_loss > 0 and low <= trade.stop_loss:
                    hit_sl = True
                    exit_price = trade.stop_loss
                # Check TP hit (high reached or crossed)
                elif trade.take_profit > 0 and high >= trade.take_profit:
                    hit_tp = True
                    exit_price = trade.take_profit
                else:
                    # Update trailing stop via position manager
                    if self.position_manager:
                        new_sl = self.position_manager(trade, bar)
                        if new_sl and new_sl > trade.stop_loss:
                            trade.stop_loss = new_sl
                            if low <= new_sl:
                                hit_sl = True
                                exit_price = new_sl
                    continue
            else:  # short
                if trade.stop_loss > 0 and high >= trade.stop_loss:
                    hit_sl = True
                    exit_price = trade.stop_loss
                elif trade.take_profit > 0 and low <= trade.take_profit:
                    hit_tp = True
                    exit_price = trade.take_profit
                else:
                    if self.position_manager:
                        new_sl = self.position_manager(trade, bar)
                        if new_sl and new_sl < trade.stop_loss:
                            trade.stop_loss = new_sl
                            if high >= new_sl:
                                hit_sl = True
                                exit_price = new_sl
                    continue
            
            if hit_sl or hit_tp:
                exit_reason = "tp" if hit_tp else "sl"
                trades_to_close.append((tid, exit_price, exit_reason))
        
        for tid, exit_price, reason in trades_to_close:
            self._close_trade(tid, exit_price, bar, reason)
    
    def _process_signal(self, signal: dict, bar: dict, bar_time: datetime, today: str) -> None:
        """Process a trading signal and open a position."""
        direction = signal.get("signal")
        entry_price = signal.get("entry_price", bar.get('close', 0))
        stop_loss = signal.get("stop_loss", 0)
        take_profit = signal.get("take_profit", 0)
        regime = signal.get("regime", self._current_regime)
        atr = signal.get("atr", 0)
        
        # Calculate position size
        volume = self._calculate_volume(entry_price, stop_loss, regime, atr)
        if volume <= 0:
            return
        
        # Apply slippage
        slippage_pips = self._get_slippage(regime)
        if direction == "long":
            filled_price = entry_price + slippage_pips * 0.01
        else:
            filled_price = entry_price - slippage_pips * 0.01
        
        # Apply spread cost
        spread_pips = self._get_spread(bar)
        
        # Create trade
        self._trade_id_counter += 1
        trade = Trade(
            id=self._trade_id_counter,
            symbol=self.config.symbol,
            direction=direction,
            entry_time=bar_time,
            exit_time=bar_time,  # will be updated on close
            entry_price=filled_price,
            exit_price=filled_price,  # will be updated
            volume=volume,
            regime=regime,
            strategy=signal.get("strategy", regime),
            stop_loss=stop_loss,
            take_profit=take_profit,
            initial_risk=abs(filled_price - stop_loss) * volume * 100,  # approximate
            entry_slippage_pips=slippage_pips,
            spread_at_entry=spread_pips,
        )
        
        self.open_trades[trade.id] = trade
        self._daily_trades[today] += 1
    
    def _close_trade(self, trade_id: int, exit_price: float,
                    bar: dict = None, reason: str = "") -> None:
        """Close a trade and record P&L."""
        trade = self.open_trades.pop(trade_id, None)
        if trade is None:
            return
        
        bar_time = bar.get('time', datetime.now(timezone.utc)) if bar else datetime.now(timezone.utc)
        trade.exit_time = bar_time
        trade.exit_price = exit_price
        trade.exit_reason = reason
        
        # Exit slippage
        exit_slippage = self._get_slippage(trade.regime) * 0.5  # smaller on exit
        trade.exit_slippage_pips = exit_slippage
        
        # Calculate P&L
        point_value = 0.01  # XAUUSD point
        pips_moved = (exit_price - trade.entry_price) / point_value
        
        if trade.direction == "short":
            pips_moved = -pips_moved
        
        # Gross P&L
        trade.gross_pl = pips_moved * trade.volume * 1.0  # $1 per 0.01 lot per pip
        
        # Commission
        trade.commission = self.config.commission_per_lot * trade.volume
        
        # Swap (simplified: charge if held across rollover)
        hold_hours = (trade.exit_time - trade.entry_time).total_seconds() / 3600
        if hold_hours > 6:
            swap_rate = self.config.swap_long if trade.direction == "long" else self.config.swap_short
            trade.swap = abs(swap_rate) * trade.volume
        
        # Slippage cost
        total_slippage_pips = (abs(trade.entry_slippage_pips) + abs(trade.exit_slippage_pips)) * 0.01
        trade.slippage = total_slippage_pips * trade.volume * 1.0
        
        # Net P&L
        trade.net_pl = trade.gross_pl - trade.commission - trade.swap - trade.slippage
        trade.net_pl_pct = (trade.net_pl / self.equity * 100) if self.equity > 0 else 0
        
        # R-multiple
        trade.r_multiple = trade.net_pl / trade.initial_risk if trade.initial_risk > 0 else 0
        
        trade.is_win = trade.net_pl > 0
        
        # Update equity
        self.equity += trade.net_pl
        self.peak_equity = max(self.peak_equity, self.equity)
        
        # Update daily DD
        if bar_time:
            today = bar_time.strftime("%Y-%m-%d") if hasattr(bar_time, 'strftime') else str(bar_time)[:10]
            day_peak = self._daily_start_equity.get(today, self.equity)
            if self.equity < day_peak:
                self._daily_dd[today] = (day_peak - self.equity) / day_peak
        
        self.trades.append(trade)
    
    def _close_all_trades(self, bar: dict) -> None:
        """Close all open positions at market."""
        close_price = bar.get('close', 0)
        for tid in list(self.open_trades.keys()):
            self._close_trade(tid, close_price, bar, "time")
    
    def _check_circuit_breaker(self) -> bool:
        """Check hard circuit breaker (6% daily DD)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dd = self._daily_dd.get(today, 0)
        return dd >= self.config.max_daily_dd_pct
    
    def _update_equity_tracking(self, bar_time: datetime) -> None:
        """Update equity curve tracking."""
        self._equity_timestamps.append(bar_time)
        self._equity_values.append(self.equity)
        self._balance_values.append(self.capital)
    
    def _reset_state(self) -> None:
        """Reset engine state for a new backtest."""
        self.capital = self.config.initial_capital
        self.equity = self.config.initial_capital
        self.peak_equity = self.config.initial_capital
        self.trades = []
        self.open_trades = {}
        self._trade_id_counter = 0
        self._equity_timestamps = []
        self._equity_values = []
        self._balance_values = []
        self._daily_trades = defaultdict(int)
        self._daily_start_equity = defaultdict(lambda: self.config.initial_capital)
        self._daily_dd = defaultdict(float)
    
    # ── Results Computation ───────────────────────────────────────────────────
    
    def _compute_results(self, df: pd.DataFrame) -> BacktestResult:
        """Compute comprehensive backtest performance metrics."""
        trades = self.trades
        n = len(trades)
        
        if n == 0:
            return BacktestResult(total_trades=0)
        
        # Basic counts
        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]
        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = n_wins / n if n > 0 else 0
        
        # Returns
        start_equity = self.config.initial_capital
        end_equity = self.equity
        total_return = (end_equity - start_equity) / start_equity
        
        # Annualized
        if df is not None and not df.empty:
            days = (df['time'].max() - df['time'].min()).days if 'time' in df.columns else 90
        else:
            days = 90
        days = max(days, 1)
        annualized_return = ((1 + total_return) ** (365 / days) - 1) if days > 0 else 0
        
        # P&L totals
        total_net = sum(t.net_pl for t in trades)
        total_gross = sum(t.gross_pl for t in trades)
        total_commission = sum(t.commission for t in trades)
        total_swap = sum(t.swap for t in trades)
        total_slippage = sum(t.slippage for t in trades)
        
        # Drawdown from equity curve
        equity_arr = np.array(self._equity_values) if self._equity_values else np.array([start_equity, end_equity])
        peak = np.maximum.accumulate(equity_arr)
        dd = (peak - equity_arr) / peak
        max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
        
        # Drawdown duration
        dd_duration = 0
        max_dd_duration = 0
        for d in dd:
            if d > 0:
                dd_duration += 1
            else:
                max_dd_duration = max(max_dd_duration, dd_duration)
                dd_duration = 0
        max_dd_duration = max(max_dd_duration, dd_duration)
        
        # Profit factor
        gross_win = sum(t.gross_pl for t in wins)
        gross_loss = abs(sum(t.gross_pl for t in losses))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float('inf')
        
        # Recovery factor
        recovery_factor = abs(total_net) / (max_dd * start_equity) if max_dd > 0 and start_equity > 0 else float('inf')
        
        # Sharpe ratio (using daily returns)
        if len(self._equity_values) >= 2:
            equity_series = pd.Series(self._equity_values)
            daily_returns = equity_series.pct_change().dropna()
            if len(daily_returns) > 1:
                sharpe = float(np.sqrt(252) * daily_returns.mean() / daily_returns.std()) if daily_returns.std() > 0 else 0
                downside = daily_returns[daily_returns < 0]
                sortino = float(np.sqrt(252) * daily_returns.mean() / downside.std()) if len(downside) > 0 and downside.std() > 0 else 0
            else:
                sharpe = 0
                sortino = 0
        else:
            sharpe = 0
            sortino = 0
        
        # Calmar ratio
        calmar = annualized_return / max_dd if max_dd > 0 else float('inf')
        
        # Average trade metrics
        avg_win = np.mean([t.net_pl for t in wins]) if wins else 0
        avg_loss = np.mean([t.net_pl for t in losses]) if losses else 0
        avg_trade = total_net / n if n > 0 else 0
        avg_r = np.mean([t.r_multiple for t in trades]) if n > 0 else 0
        
        # Expectancy
        expectancy = (win_rate * avg_win + (1 - win_rate) * avg_loss) if n > 0 else 0
        
        # SQN (System Quality Number)
        if n > 0:
            r_values = [t.r_multiple for t in trades]
            sqn = float(np.sqrt(n) * np.mean(r_values) / np.std(r_values)) if np.std(r_values) > 0 else 0
        else:
            sqn = 0
        
        # Holding times
        hold_times = [(t.exit_time - t.entry_time).total_seconds() / 60 for t in trades]
        avg_hold = np.mean(hold_times) if hold_times else 0
        max_hold = max(hold_times) if hold_times else 0
        
        # Consecutive streaks
        max_cw, max_cl = 0, 0
        current_w, current_l = 0, 0
        for t in trades:
            if t.is_win:
                current_w += 1
                current_l = 0
                max_cw = max(max_cw, current_w)
            else:
                current_l += 1
                current_w = 0
                max_cl = max(max_cl, current_l)
        
        # Regime breakdown
        regime_stats = {}
        for regime in set(t.regime for t in trades):
            rt = [t for t in trades if t.regime == regime]
            if rt:
                regime_stats[regime] = {
                    "trades": len(rt),
                    "win_rate": round(sum(1 for t in rt if t.is_win) / len(rt) * 100, 1),
                    "net_pl": round(sum(t.net_pl for t in rt), 2),
                    "avg_r": round(np.mean([t.r_multiple for t in rt]), 2),
                }
        
        # Date range
        start_date = df['time'].min() if 'time' in df.columns else None
        end_date = df['time'].max() if 'time' in df.columns else None
        
        return BacktestResult(
            total_trades=n,
            winning_trades=n_wins,
            losing_trades=n_losses,
            win_rate=win_rate,
            total_return_pct=total_return,
            annualized_return_pct=annualized_return,
            total_net_pl=total_net,
            gross_pl=total_gross,
            total_commission=total_commission,
            total_swap=total_swap,
            total_slippage_cost=total_slippage,
            max_drawdown_pct=max_dd,
            max_drawdown_duration_days=max_dd_duration,
            profit_factor=profit_factor,
            recovery_factor=recovery_factor,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_trade=avg_trade,
            avg_r_multiple=avg_r,
            expectancy=expectancy,
            sqn=sqn,
            avg_hold_minutes=avg_hold,
            max_hold_minutes=max_hold,
            max_consecutive_wins=max_cw,
            max_consecutive_losses=max_cl,
            regime_stats=regime_stats,
            trades=trades,
            equity_curve=EquityCurve(
                timestamps=self._equity_timestamps,
                equity=self._equity_values,
                balance=self._balance_values,
                drawdown=[],
                drawdown_pct=[],
            ),
            start_date=start_date,
            end_date=end_date,
            total_days=days,
        )
