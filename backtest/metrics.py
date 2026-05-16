"""
Performance Metrics for XAU/USD Trading System.

Standalone performance analysis for backtest results.
Computes all metrics required by PRD performance gates:
- Win Rate ≥ 58%
- Profit Factor ≥ 1.8
- Sharpe Ratio ≥ 1.5
- Max Drawdown ≤ 8%
- Recovery Factor ≥ 3.0

Plus additional institutional metrics:
- SQN (System Quality Number)
- Sortino Ratio
- Calmar Ratio
- Expectancy
- Regime P&L breakdown
- Cost vs Edge analysis
- Monte Carlo simulation
"""

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
import json

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult, Trade
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class PerformanceAnalyzer:
    """
    Compute comprehensive performance metrics from trade history.
    
    Works with both BacktestResult and raw trade lists.
    """
    
    # PRD Performance Gates
    GATES = {
        "min_win_rate": 0.58,
        "min_profit_factor": 1.8,
        "min_sharpe_ratio": 1.5,
        "max_drawdown_pct": 0.08,
        "min_recovery_factor": 3.0,
    }
    
    def __init__(self, trades: List[Trade] = None,
                equity_curve: List[float] = None,
                initial_capital: float = 10000.0):
        self.trades = trades or []
        self.equity_curve = equity_curve or []
        self.initial_capital = initial_capital
    
    def from_backtest_result(self, result: BacktestResult) -> "PerformanceAnalyzer":
        """Create analyzer from BacktestResult."""
        self.trades = result.trades
        if result.equity_curve:
            self.equity_curve = result.equity_curve.equity
        return self
    
    def from_trade_dataframe(self, df: pd.DataFrame) -> "PerformanceAnalyzer":
        """
        Create analyzer from a trades DataFrame.
        
        Expected columns: entry_time, exit_time, direction, entry_price,
                         exit_price, volume, net_pl
        """
        trades = []
        for _, row in df.iterrows():
            trades.append(Trade(
                id=row.get('id', len(trades)),
                symbol=row.get('symbol', 'XAUUSD'),
                direction=row.get('direction', 'long'),
                entry_time=row.get('entry_time', datetime.now()),
                exit_time=row.get('exit_time', datetime.now()),
                entry_price=row.get('entry_price', 0),
                exit_price=row.get('exit_price', 0),
                volume=row.get('volume', 0.01),
                net_pl=row.get('net_pl', 0),
                is_win=row.get('net_pl', 0) > 0,
            ))
        self.trades = trades
        return self
    
    # ── Core Metrics ───────────────────────────────────────────────────────────
    
    def win_rate(self) -> float:
        """Calculate win rate (0-1)."""
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.is_win) / len(self.trades)
    
    def profit_factor(self) -> float:
        """Calculate profit factor (gross win / gross loss)."""
        wins = sum(t.gross_pl if hasattr(t, 'gross_pl') and t.gross_pl > 0 else max(0, t.net_pl) for t in self.trades)
        losses = abs(sum(t.gross_pl if hasattr(t, 'gross_pl') and t.gross_pl < 0 else min(0, t.net_pl) for t in self.trades))
        return wins / losses if losses > 0 else float('inf')
    
    def sharpe_ratio(self, risk_free_rate: float = 0.02) -> float:
        """
        Calculate annualized Sharpe ratio.
        
        Uses daily returns from equity curve.
        """
        returns = self._daily_returns()
        if len(returns) < 2 or returns.std() == 0:
            return 0.0
        
        excess = returns.mean() - (risk_free_rate / 252)
        return float(np.sqrt(252) * excess / returns.std())
    
    def sortino_ratio(self, risk_free_rate: float = 0.02) -> float:
        """Calculate Sortino ratio (downside deviation only)."""
        returns = self._daily_returns()
        if len(returns) < 2:
            return 0.0
        
        downside = returns[returns < 0]
        if len(downside) < 2 or downside.std() == 0:
            return 0.0
        
        excess = returns.mean() - (risk_free_rate / 252)
        return float(np.sqrt(252) * excess / downside.std())
    
    def max_drawdown(self) -> Tuple[float, float, Optional[datetime], Optional[datetime]]:
        """
        Calculate maximum drawdown.
        
        Returns:
            Tuple of (max_dd_pct, max_dd_amount, peak_time, trough_time)
        """
        if not self.equity_curve:
            return 0.0, 0.0, None, None
        
        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / peak
        
        max_dd_idx = int(np.argmax(dd))
        max_dd = float(dd[max_dd_idx])
        
        return max_dd, float(peak[max_dd_idx] - equity[max_dd_idx]), None, None
    
    def recovery_factor(self) -> float:
        """Calculate recovery factor (net profit / max drawdown)."""
        total_pl = sum(t.net_pl for t in self.trades)
        max_dd, _, _, _ = self.max_drawdown()
        
        if max_dd <= 0 or self.initial_capital <= 0:
            return float('inf')
        
        max_dd_amount = max_dd * self.initial_capital
        return abs(total_pl) / max_dd_amount if max_dd_amount > 0 else float('inf')
    
    def calmar_ratio(self) -> float:
        """Calculate Calmar ratio (annualized return / max drawdown)."""
        total_return = self.total_return()
        max_dd, _, _, _ = self.max_drawdown()
        
        if max_dd <= 0:
            return float('inf')
        
        days = self._trading_days()
        annualized = ((1 + total_return) ** (365 / max(days, 1)) - 1)
        return annualized / max_dd
    
    def sqn(self) -> float:
        """System Quality Number — measures strategy robustness."""
        if not self.trades:
            return 0.0
        
        n = len(self.trades)
        r_values = [t.r_multiple if hasattr(t, 'r_multiple') and t.r_multiple != 0 
                   else (t.net_pl / 100) for t in self.trades]
        
        if np.std(r_values) == 0:
            return 0.0
        
        return float(np.sqrt(n) * np.mean(r_values) / np.std(r_values))
    
    def expectancy(self) -> float:
        """Calculate trade expectancy in account currency."""
        if not self.trades:
            return 0.0
        
        avg_win = np.mean([t.net_pl for t in self.trades if t.is_win]) if any(t.is_win for t in self.trades) else 0
        avg_loss = np.mean([t.net_pl for t in self.trades if not t.is_win]) if any(not t.is_win for t in self.trades) else 0
        
        wr = self.win_rate()
        return wr * avg_win + (1 - wr) * avg_loss
    
    def total_return(self) -> float:
        """Calculate total return (0-1)."""
        total_pl = sum(t.net_pl for t in self.trades)
        return total_pl / self.initial_capital if self.initial_capital > 0 else 0.0
    
    # ── Advanced Metrics ───────────────────────────────────────────────────────
    
    def consecutive_stats(self) -> dict:
        """Calculate max consecutive wins and losses."""
        max_cw, max_cl = 0, 0
        cw, cl = 0, 0
        
        for t in self.trades:
            if t.is_win:
                cw += 1
                cl = 0
                max_cw = max(max_cw, cw)
            else:
                cl += 1
                cw = 0
                max_cl = max(max_cl, cl)
        
        return {
            "max_consecutive_wins": max_cw,
            "max_consecutive_losses": max_cl,
        }
    
    def holding_time_stats(self) -> dict:
        """Calculate holding time statistics."""
        times = []
        for t in self.trades:
            if hasattr(t, 'entry_time') and hasattr(t, 'exit_time'):
                hold = (t.exit_time - t.entry_time).total_seconds() / 60
                times.append(hold)
        
        if not times:
            return {"avg_minutes": 0, "median_minutes": 0, "max_minutes": 0}
        
        return {
            "avg_minutes": round(float(np.mean(times)), 1),
            "median_minutes": round(float(np.median(times)), 1),
            "max_minutes": round(max(times), 1),
            "p90_minutes": round(float(np.percentile(times, 90)), 1),
        }
    
    def regime_breakdown(self) -> dict:
        """
        Break down performance by trading regime.
        
        Requires trades to have 'regime' attribute.
        """
        regimes = {}
        for t in self.trades:
            r = getattr(t, 'regime', 'unknown')
            if r not in regimes:
                regimes[r] = {"trades": 0, "wins": 0, "net_pl": 0.0, "gross_pl": 0.0}
            
            regimes[r]["trades"] += 1
            if t.is_win:
                regimes[r]["wins"] += 1
            regimes[r]["net_pl"] += t.net_pl
            regimes[r]["gross_pl"] += getattr(t, 'gross_pl', t.net_pl)
        
        for r in regimes:
            n = regimes[r]["trades"]
            regimes[r]["win_rate"] = round(regimes[r]["wins"] / n * 100, 1) if n > 0 else 0
            regimes[r]["avg_pl"] = round(regimes[r]["net_pl"] / n, 2) if n > 0 else 0
        
        return regimes
    
    def trade_distribution(self) -> dict:
        """Analyze P&L distribution of trades."""
        if not self.trades:
            return {}
        
        pl_values = [t.net_pl for t in self.trades]
        
        return {
            "mean": round(float(np.mean(pl_values)), 2),
            "median": round(float(np.median(pl_values)), 2),
            "std": round(float(np.std(pl_values)), 2),
            "skew": round(float(pd.Series(pl_values).skew()), 3),
            "kurtosis": round(float(pd.Series(pl_values).kurtosis()), 3),
            "p25": round(float(np.percentile(pl_values, 25)), 2),
            "p75": round(float(np.percentile(pl_values, 75)), 2),
            "p95": round(float(np.percentile(pl_values, 95)), 2),
            "best_trade": round(max(pl_values), 2),
            "worst_trade": round(min(pl_values), 2),
        }
    
    # ── Monte Carlo Simulation ────────────────────────────────────────────────
    
    def monte_carlo(self, n_simulations: int = 1000, 
                   n_trades: int = None,
                   confidence: float = 0.95) -> dict:
        """
        Monte Carlo simulation of trade sequences.
        
        Randomly shuffles trade outcomes to estimate robustness.
        
        Args:
            n_simulations: Number of simulated sequences
            n_trades: Trades per simulation (default: use actual count)
            confidence: Confidence interval
            
        Returns:
            Dict with simulation results.
        """
        if not self.trades:
            return {}
        
        pl_values = [t.net_pl for t in self.trades]
        n = n_trades or len(pl_values)
        
        results = []
        for _ in range(n_simulations):
            sampled = np.random.choice(pl_values, size=n, replace=True)
            results.append(np.sum(sampled))
        
        results = np.array(results)
        
        return {
            "simulations": n_simulations,
            "trades_per_sim": n,
            "mean_pl": round(float(np.mean(results)), 2),
            "median_pl": round(float(np.median(results)), 2),
            "std_pl": round(float(np.std(results)), 2),
            "min_pl": round(float(np.min(results)), 2),
            "max_pl": round(float(np.max(results)), 2),
            f"var_{int(confidence*100)}": round(float(np.percentile(results, (1 - confidence) * 100)), 2),
            "prob_loss": round(float(np.mean(results < 0) * 100), 1),
            "prob_dd_10pct": round(float(np.mean(results < -self.initial_capital * 0.1) * 100), 1),
        }
    
    # ── Performance Gate Check ────────────────────────────────────────────────
    
    def check_gates(self, gates: dict = None) -> dict:
        """
        Check if performance meets all PRD gates.
        
        Returns:
            Dict with each gate's status and actual value.
        """
        gates = gates or self.GATES
        max_dd, _, _, _ = self.max_drawdown()
        
        checks = {
            "win_rate": {
                "target": f"≥{gates['min_win_rate']*100:.0f}%",
                "actual": f"{self.win_rate()*100:.1f}%",
                "passed": self.win_rate() >= gates["min_win_rate"],
            },
            "profit_factor": {
                "target": f"≥{gates['min_profit_factor']}",
                "actual": round(self.profit_factor(), 2),
                "passed": self.profit_factor() >= gates["min_profit_factor"],
            },
            "sharpe_ratio": {
                "target": f"≥{gates['min_sharpe_ratio']}",
                "actual": round(self.sharpe_ratio(), 2),
                "passed": self.sharpe_ratio() >= gates["min_sharpe_ratio"],
            },
            "max_drawdown": {
                "target": f"≤{gates['max_drawdown_pct']*100:.0f}%",
                "actual": f"{max_dd*100:.1f}%",
                "passed": max_dd <= gates["max_drawdown_pct"],
            },
            "recovery_factor": {
                "target": f"≥{gates['min_recovery_factor']}",
                "actual": round(self.recovery_factor(), 2),
                "passed": self.recovery_factor() >= gates["min_recovery_factor"],
            },
        }
        
        checks["all_passed"] = all(c["passed"] for c in checks.values())
        return checks
    
    # ── Full Report ───────────────────────────────────────────────────────────
    
    def full_metrics(self) -> dict:
        """Compute all metrics as a dictionary."""
        max_dd, max_dd_amount, _, _ = self.max_drawdown()
        consec = self.consecutive_stats()
        holding = self.holding_time_stats()
        
        return {
            "summary": {
                "total_trades": len(self.trades),
                "win_rate": round(self.win_rate() * 100, 1),
                "total_return_pct": round(self.total_return() * 100, 2),
                "total_net_pl": round(sum(t.net_pl for t in self.trades), 2),
            },
            "returns": {
                "profit_factor": round(self.profit_factor(), 2),
                "sharpe_ratio": round(self.sharpe_ratio(), 2),
                "sortino_ratio": round(self.sortino_ratio(), 2),
                "calmar_ratio": round(self.calmar_ratio(), 2),
                "sqn": round(self.sqn(), 2),
                "expectancy": round(self.expectancy(), 2),
            },
            "risk": {
                "max_drawdown_pct": round(max_dd * 100, 2),
                "max_drawdown_amount": round(max_dd_amount, 2),
                "recovery_factor": round(self.recovery_factor(), 2),
                "max_consecutive_wins": consec["max_consecutive_wins"],
                "max_consecutive_losses": consec["max_consecutive_losses"],
            },
            "trades": {
                "avg_hold_minutes": holding["avg_minutes"],
                "median_hold_minutes": holding["median_minutes"],
                "max_hold_minutes": holding["max_hold_minutes"],
            },
            "distribution": self.trade_distribution(),
            "regime_breakdown": self.regime_breakdown(),
            "gates": self.check_gates(),
        }
    
    def print_report(self) -> str:
        """Generate a human-readable performance report."""
        m = self.full_metrics()
        gates = m["gates"]
        
        lines = []
        lines.append("=" * 60)
        lines.append("  XAU/USD TRADING SYSTEM — PERFORMANCE REPORT")
        lines.append("=" * 60)
        lines.append("")
        
        s = m["summary"]
        lines.append(f"Total Trades:    {s['total_trades']}")
        lines.append(f"Win Rate:        {s['win_rate']}%")
        lines.append(f"Total Return:    {s['total_return_pct']}%")
        lines.append(f"Net P&L:         ${s['total_net_pl']:,.2f}")
        lines.append("")
        
        r = m["returns"]
        lines.append("--- Returns ---")
        lines.append(f"Profit Factor:   {r['profit_factor']}")
        lines.append(f"Sharpe Ratio:    {r['sharpe_ratio']}")
        lines.append(f"Sortino Ratio:   {r['sortino_ratio']}")
        lines.append(f"SQN:             {r['sqn']}")
        lines.append(f"Expectancy:      ${r['expectancy']:,.2f}")
        lines.append("")
        
        risk = m["risk"]
        lines.append("--- Risk ---")
        lines.append(f"Max DD:          {risk['max_drawdown_pct']}% (${risk['max_drawdown_amount']:,.2f})")
        lines.append(f"Recovery Factor: {risk['recovery_factor']}")
        lines.append(f"Max Consec Wins: {risk['max_consecutive_wins']}")
        lines.append(f"Max Consec Loss: {risk['max_consecutive_losses']}")
        lines.append("")
        
        lines.append("--- Performance Gates ---")
        for gate_name, gate_data in gates.items():
            if gate_name == "all_passed":
                continue
            emoji = "✅" if gate_data["passed"] else "❌"
            lines.append(f"{emoji} {gate_name}: {gate_data['actual']} (target: {gate_data['target']})")
        
        lines.append("")
        lines.append(f"OVERALL: {'✅ ALL GATES PASSED' if gates['all_passed'] else '❌ GATES FAILED'}")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    # ── Utilities ──────────────────────────────────────────────────────────────
    
    def _daily_returns(self) -> pd.Series:
        """Extract daily returns from equity curve."""
        if len(self.equity_curve) < 2:
            # Build from trades
            if not self.trades:
                return pd.Series(dtype=float)
            
            # Sort trades by time and build equity curve
            sorted_trades = sorted(self.trades, key=lambda t: t.entry_time if hasattr(t, 'entry_time') else datetime.now())
            equity = [self.initial_capital]
            for t in sorted_trades:
                equity.append(equity[-1] + t.net_pl)
            self.equity_curve = equity
        
        series = pd.Series(self.equity_curve)
        return series.pct_change().dropna()
    
    def _trading_days(self) -> int:
        """Estimate number of trading days in the period."""
        if not self.trades:
            return 90
        
        times = []
        for t in self.trades:
            if hasattr(t, 'entry_time'):
                times.append(t.entry_time)
            if hasattr(t, 'exit_time'):
                times.append(t.exit_time)
        
        if not times:
            return 90
        
        return max(1, (max(times) - min(times)).days)
