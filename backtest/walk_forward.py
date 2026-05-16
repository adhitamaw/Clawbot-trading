"""
Walk-Forward Analysis Pipeline.

Implements walk-forward optimization and validation:
1. Split data into rolling train/test windows
2. Optimize parameters on training window
3. Test on out-of-sample window
4. Chain windows for full walk-forward analysis
5. Aggregate out-of-sample metrics

Minimum 4 rolling windows (e.g., 9 months train / 3 months test).
"""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable
import itertools

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


@dataclass
class WalkForwardWindow:
    """Single walk-forward window result."""
    window_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    
    train_trades: int = 0
    test_trades: int = 0
    
    train_metrics: Optional[dict] = None
    test_metrics: Optional[dict] = None
    
    # Optimized parameters for this window
    best_params: dict = field(default_factory=dict)
    
    is_valid: bool = False  # window passes performance gates
    
    def to_dict(self) -> dict:
        return {
            "window": self.window_id,
            "train_period": f"{self.train_start.strftime('%Y-%m-%d')} → {self.train_end.strftime('%Y-%m-%d')}",
            "test_period": f"{self.test_start.strftime('%Y-%m-%d')} → {self.test_end.strftime('%Y-%m-%d')}",
            "train_trades": self.train_trades,
            "test_trades": self.test_trades,
            "test_metrics": self.test_metrics,
            "best_params": self.best_params,
            "is_valid": self.is_valid,
        }


@dataclass
class WalkForwardResult:
    """Complete walk-forward analysis result."""
    total_windows: int = 0
    valid_windows: int = 0
    
    # Aggregate out-of-sample metrics
    oos_total_trades: int = 0
    oos_win_rate: float = 0.0
    oos_profit_factor: float = 0.0
    oos_sharpe_ratio: float = 0.0
    oos_max_drawdown: float = 0.0
    oos_total_return: float = 0.0
    oos_avg_r: float = 0.0
    oos_expectancy: float = 0.0
    
    # Stability metrics
    win_rate_stability: float = 0.0     # std of win rates across windows
    pf_stability: float = 0.0           # std of profit factors
    parameter_stability: dict = field(default_factory=dict)
    
    # Individual windows
    windows: List[WalkForwardWindow] = field(default_factory=list)
    
    # Performance gates
    gates_passed: bool = False
    gate_results: dict = field(default_factory=dict)
    
    def to_summary(self) -> dict:
        return {
            "windows": f"{self.valid_windows}/{self.total_windows} valid",
            "oos_trades": self.oos_total_trades,
            "oos_win_rate": round(self.oos_win_rate, 3),
            "oos_profit_factor": round(self.oos_profit_factor, 2),
            "oos_sharpe": round(self.oos_sharpe_ratio, 2),
            "oos_max_dd": f"{round(self.oos_max_drawdown * 100, 1)}%",
            "oos_return": f"{round(self.oos_total_return * 100, 1)}%",
            "oos_expectancy": round(self.oos_expectancy, 2),
            "gates_passed": self.gates_passed,
        }


class WalkForwardAnalyzer:
    """
    Walk-forward analysis pipeline.
    
    Splits data into rolling windows, runs optimization on training
    portion, validates on out-of-sample test portion, and aggregates
    results to assess strategy robustness.
    """
    
    # Performance gates from PRD
    GATES = {
        "min_win_rate": 0.58,
        "min_profit_factor": 1.8,
        "min_sharpe_ratio": 1.5,
        "max_drawdown": 0.08,
        "min_recovery_factor": 3.0,
    }
    
    def __init__(self, engine: BacktestEngine = None,
                gates: dict = None):
        """
        Initialize walk-forward analyzer.
        
        Args:
            engine: BacktestEngine instance
            gates: Performance gate thresholds (overrides PRD defaults)
        """
        self.engine = engine or BacktestEngine()
        self.gates = gates or self.GATES
        
        # Parameter grid for optimization
        self.param_grid: dict = {}
        self.optimization_metric: str = "sharpe_ratio"  # metric to maximize
    
    # ── Walk-Forward Execution ────────────────────────────────────────────────
    
    def run(self, data: pd.DataFrame,
            train_months: int = 9,
            test_months: int = 3,
            min_windows: int = 4,
            optimize_params: bool = False,
            signal_generator: Callable = None,
            regime_provider: Callable = None) -> WalkForwardResult:
        """
        Execute full walk-forward analysis.
        
        Args:
            data: Full historical OHLCV DataFrame
            train_months: Training window length in months
            test_months: Test window length in months
            min_windows: Minimum number of windows
            optimize_params: Whether to run parameter optimization
            signal_generator: Function to generate signals (can accept params)
            regime_provider: Function to provide regime labels
            
        Returns:
            WalkForwardResult with aggregate metrics.
        """
        if data.empty:
            logger.error("walk_forward_empty_data")
            return WalkForwardResult()
        
        # Ensure time column is datetime
        if 'time' in data.columns:
            data['time'] = pd.to_datetime(data['time'])
        
        # Build windows
        windows = self._build_windows(data, train_months, test_months, min_windows)
        
        if not windows:
            logger.error("walk_forward_no_windows")
            return WalkForwardResult()
        
        result = WalkForwardResult(total_windows=len(windows))
        
        for win in windows:
            logger.info(
                "walk_forward_window",
                window=win.window_id,
                train_start=win.train_start.strftime("%Y-%m-%d"),
                test_start=win.test_start.strftime("%Y-%m-%d"),
            )
            
            # Split data
            train_df = data[(data['time'] >= win.train_start) & (data['time'] <= win.train_end)]
            test_df = data[(data['time'] >= win.test_start) & (data['time'] <= win.test_end)]
            
            if train_df.empty or test_df.empty:
                logger.warning("walk_forward_window_insufficient_data", window=win.window_id)
                continue
            
            # Optional parameter optimization on training data
            if optimize_params and self.param_grid:
                best_params, train_metrics = self._optimize_params(
                    train_df, signal_generator, regime_provider
                )
                win.best_params = best_params
            else:
                train_metrics = {}
            
            # Generate regime labels if provider available
            test_regimes = None
            if regime_provider:
                test_regimes = [regime_provider(bar_time) for bar_time in test_df['time']]
            
            # Run backtest on test data with best params
            if signal_generator:
                self.engine.set_signal_generator(
                    lambda bar, reg, idx, df: signal_generator(bar, reg, idx, df, win.best_params)
                )
            
            oos_result = self.engine.run(test_df, test_regimes)
            
            win.train_metrics = train_metrics
            win.test_metrics = oos_result.to_summary()
            win.train_trades = train_metrics.get("trades", 0)
            win.test_trades = oos_result.total_trades
            
            # Check performance gates for this window
            win.is_valid = self._check_gates(oos_result)
            
            if win.is_valid:
                result.valid_windows += 1
            
            result.windows.append(win)
        
        # Aggregate OOS metrics
        self._aggregate_oos(result)
        
        # Check overall gates
        result.gates_passed = self._check_overall_gates(result)
        result.gate_results = {
            "win_rate": result.oos_win_rate >= self.gates.get("min_win_rate", 0.58),
            "profit_factor": result.oos_profit_factor >= self.gates.get("min_profit_factor", 1.8),
            "sharpe": result.oos_sharpe_ratio >= self.gates.get("min_sharpe_ratio", 1.5),
            "max_dd": result.oos_max_drawdown <= self.gates.get("max_drawdown", 0.08),
        }
        
        logger.info(
            "walk_forward_complete",
            windows=result.total_windows,
            valid=result.valid_windows,
            gates_passed=result.gates_passed,
            oos_sharpe=round(result.oos_sharpe_ratio, 2),
        )
        
        return result
    
    def _build_windows(self, data: pd.DataFrame,
                      train_months: int, test_months: int,
                      min_windows: int) -> List[WalkForwardWindow]:
        """
        Build rolling walk-forward windows from data time range.
        
        Example: 9 months train, 3 months test, 4 windows total.
        Window 1: Train [2024-01 to 2024-09], Test [2024-10 to 2024-12]
        Window 2: Train [2024-04 to 2024-12], Test [2025-01 to 2025-03]
        etc.
        """
        if data.empty or 'time' not in data.columns:
            return []
        
        start = data['time'].min()
        end = data['time'].max()
        
        total_months = (end.year - start.year) * 12 + (end.month - start.month)
        window_months = train_months + test_months
        
        if total_months < window_months:
            logger.warning("walk_forward_data_insufficient",
                          total_months=total_months,
                          needed=window_months)
            return []
        
        # Calculate number of possible windows
        possible_windows = total_months - window_months + 1
        
        # We want at least min_windows, so adjust step size
        if possible_windows < min_windows:
            min_windows = max(1, possible_windows)
        
        step_months = max(1, test_months)  # step by test period size
        
        windows = []
        window_id = 0
        
        current_start = start
        while True:
            train_start = current_start
            train_end = train_start + timedelta(days=train_months * 30)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=test_months * 30)
            
            if test_end > end:
                break
            
            window_id += 1
            windows.append(WalkForwardWindow(
                window_id=window_id,
                train_start=train_start,
                train_end=min(train_end, end),
                test_start=test_start,
                test_end=min(test_end, end),
            ))
            
            # Slide forward by step
            current_start = current_start + timedelta(days=step_months * 30)
            
            if len(windows) >= min_windows * 2:  # safety cap
                break
        
        return windows
    
    # ── Parameter Optimization ────────────────────────────────────────────────
    
    def set_param_grid(self, grid: dict) -> None:
        """
        Set parameter grid for optimization.
        
        Example:
        {
            "atr_multiplier_sl": [1.5, 1.8, 2.0, 2.5],
            "atr_multiplier_tp1": [1.0, 1.5, 2.0],
            "rsi_oversold": [25, 30, 35],
        }
        """
        self.param_grid = grid
    
    def _optimize_params(self, df: pd.DataFrame,
                        signal_generator: Callable,
                        regime_provider: Callable) -> Tuple[dict, dict]:
        """
        Grid-search parameter optimization on training data.
        
        Returns:
            Tuple of (best_params, best_metrics).
        """
        if not self.param_grid:
            return {}, {}
        
        # Generate all parameter combinations
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        combinations = list(itertools.product(*values))
        
        best_score = -float('inf')
        best_params = {}
        best_metrics = {}
        
        logger.info("param_optimization_starting", combinations=len(combinations))
        
        for combo in combinations:
            params = dict(zip(keys, combo))
            
            # Generate regimes
            regimes = None
            if regime_provider:
                regimes = [regime_provider(t) for t in df['time']]
            
            # Run backtest with these params
            if signal_generator:
                self.engine.set_signal_generator(
                    lambda bar, reg, idx, df, p=params: signal_generator(bar, reg, idx, df, p)
                )
            
            result = self.engine.run(df, regimes)
            
            # Score based on optimization metric
            score = getattr(result, self.optimization_metric, 0)
            
            if score > best_score:
                best_score = score
                best_params = params
                best_metrics = {
                    "score": score,
                    "trades": result.total_trades,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "sharpe": result.sharpe_ratio,
                    "max_dd": result.max_drawdown_pct,
                }
        
        logger.info("param_optimization_complete",
                   best_score=round(best_score, 4),
                   best_params=best_params)
        
        return best_params, best_metrics
    
    # ── Aggregation & Validation ──────────────────────────────────────────────
    
    def _aggregate_oos(self, result: WalkForwardResult) -> None:
        """Aggregate out-of-sample metrics across all windows."""
        valid_windows = [w for w in result.windows if w.test_metrics]
        
        if not valid_windows:
            return
        
        # Summative metrics
        result.oos_total_trades = sum(w.test_trades for w in valid_windows)
        
        # Weighted averages (by trades)
        total_trades = sum(w.test_trades for w in valid_windows)
        
        if total_trades > 0:
            win_rates = [w.test_metrics.get('win_rate', 0) / 100 for w in valid_windows]
            result.oos_win_rate = sum(
                wr * w.test_trades / total_trades
                for wr, w in zip(win_rates, valid_windows)
            )
            
            # Profit factors
            pfs = [w.test_metrics.get('profit_factor', 0) for w in valid_windows]
            result.oos_profit_factor = np.mean(pfs) if pfs else 0
            
            # Sharpe
            sharpes = [w.test_metrics.get('sharpe_ratio', 0) for w in valid_windows]
            result.oos_sharpe_ratio = np.mean(sharpes) if sharpes else 0
            
            # Max drawdown (worst case across windows)
            dds = [w.test_metrics.get('max_drawdown_pct', 0) / 100 for w in valid_windows]
            result.oos_max_drawdown = max(dds) if dds else 0
            
            # Total return
            returns = [w.test_metrics.get('total_return_pct', 0) / 100 for w in valid_windows]
            result.oos_total_return = np.prod([1 + r for r in returns]) - 1 if returns else 0
            
            # Avg R
            avg_rs = [w.test_metrics.get('avg_r_multiple', 0) for w in valid_windows]
            result.oos_avg_r = np.mean(avg_rs) if avg_rs else 0
            
            # Expectancy
            expectancies = [w.test_metrics.get('expectancy', 0) for w in valid_windows]
            result.oos_expectancy = np.mean(expectancies) if expectancies else 0
        
        # Stability metrics
        if len(valid_windows) >= 2:
            result.win_rate_stability = float(np.std(win_rates)) if win_rates else 0
            result.pf_stability = float(np.std(pfs) / np.mean(pfs)) if pfs and np.mean(pfs) > 0 else 0
        
        # Parameter stability
        if valid_windows and valid_windows[0].best_params:
            for key in valid_windows[0].best_params:
                values = [w.best_params.get(key) for w in valid_windows if w.best_params]
                if len(values) >= 2:
                    result.parameter_stability[key] = {
                        "values": values,
                        "std": float(np.std(values)),
                    }
    
    def _check_gates(self, result: BacktestResult) -> bool:
        """Check if a single window passes performance gates."""
        checks = [
            result.win_rate >= self.gates.get("min_win_rate", 0.58),
            result.profit_factor >= self.gates.get("min_profit_factor", 1.8),
            result.sharpe_ratio >= self.gates.get("min_sharpe_ratio", 1.5),
            result.max_drawdown_pct <= self.gates.get("max_drawdown", 0.08),
            result.recovery_factor >= self.gates.get("min_recovery_factor", 3.0),
        ]
        return all(checks)
    
    def _check_overall_gates(self, result: WalkForwardResult) -> bool:
        """Check if aggregate OOS results pass performance gates."""
        return all([
            result.oos_win_rate >= self.GATES.get("min_win_rate", 0.58),
            result.oos_profit_factor >= self.GATES.get("min_profit_factor", 1.8),
            result.oos_sharpe_ratio >= self.GATES.get("min_sharpe_ratio", 1.5),
            result.oos_max_drawdown <= self.GATES.get("max_drawdown", 0.08),
        ])
    
    # ── Report Generation ─────────────────────────────────────────────────────
    
    def generate_report(self, result: WalkForwardResult) -> str:
        """
        Generate a human-readable walk-forward report.
        
        Args:
            result: WalkForwardResult from run()
            
        Returns:
            Markdown-formatted report string.
        """
        lines = []
        lines.append("# Walk-Forward Analysis Report")
        lines.append("")
        
        lines.append("## Performance Gates")
        lines.append("")
        for gate, target in self.GATES.items():
            actual = {
                "min_win_rate": result.oos_win_rate,
                "min_profit_factor": result.oos_profit_factor,
                "min_sharpe_ratio": result.oos_sharpe_ratio,
                "max_drawdown": result.oos_max_drawdown,
            }.get(gate, 0)
            
            if "max_drawdown" in gate:
                passed = actual <= target
            else:
                passed = actual >= target
            
            emoji = "✅" if passed else "❌"
            
            if "win_rate" in gate:
                lines.append(f"- {emoji} Win Rate: {actual*100:.1f}% (target: ≥{target*100:.0f}%)")
            elif "drawdown" in gate:
                lines.append(f"- {emoji} Max DD: {actual*100:.1f}% (target: ≤{target*100:.0f}%)")
            else:
                lines.append(f"- {emoji} {gate}: {actual:.2f} (target: ≥{target})")
        
        lines.append("")
        lines.append(f"**Overall: {'PASSED ✅' if result.gates_passed else 'FAILED ❌'}**")
        lines.append("")
        
        lines.append("## Window Results")
        lines.append("")
        for win in result.windows:
            m = win.test_metrics or {}
            lines.append(f"### Window {win.window_id} ({'✅' if win.is_valid else '❌'})")
            lines.append(f"- Train: {win.train_start.strftime('%Y-%m-%d')} → {win.train_end.strftime('%Y-%m-%d')}")
            lines.append(f"- Test: {win.test_start.strftime('%Y-%m-%d')} → {win.test_end.strftime('%Y-%m-%d')}")
            lines.append(f"- Trades: {win.test_trades} | Win Rate: {m.get('win_rate', 0)}%")
            lines.append(f"- PF: {m.get('profit_factor', 0)} | Sharpe: {m.get('sharpe_ratio', 0)} | Max DD: {m.get('max_drawdown_pct', 0)}%")
            if win.best_params:
                lines.append(f"- Best Params: {win.best_params}")
            lines.append("")
        
        lines.append("## Aggregate OOS Metrics")
        lines.append("")
        lines.append(f"- Win Rate: {result.oos_win_rate*100:.1f}%")
        lines.append(f"- Profit Factor: {result.oos_profit_factor:.2f}")
        lines.append(f"- Sharpe Ratio: {result.oos_sharpe_ratio:.2f}")
        lines.append(f"- Max Drawdown: {result.oos_max_drawdown*100:.1f}%")
        lines.append(f"- Expectancy: {result.oos_expectancy:.2f}")
        
        return "\n".join(lines)
