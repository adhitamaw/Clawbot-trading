"""
Intermarket Confirmation Filter.

Uses DXY (US Dollar Index) and US10Y (10-Year Treasury Yield)
correlation with XAUUSD to confirm or reject trading signals.

Logic from PRD:
- Calculate 20-period rolling Pearson correlation between XAUUSD and DXY returns
- Long filter: corr < -0.25 AND DXY weakening → confirm
- Short filter: corr > 0.25 AND DXY strengthening → confirm
- US10Y rising sharply → caution on longs (higher real yields pressure gold)
- Filter failure → downgrade signal strength or block entry
"""

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple

import numpy as np
import pandas as pd

from src.mt5_bridge import MT5Bridge
from src.features.technical import FeatureEngine
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class FilterVerdict(str):
    """Enum-like verdict for intermarket filter."""
    PASS = "pass"
    FAIL = "fail"
    CAUTION = "caution"
    NO_DATA = "no_data"


@dataclass
class IntermarketSignal:
    """Output from intermarket filter analysis."""
    verdict: str  # pass | fail | caution | no_data
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Correlation data
    corr_xau_dxy: float = 0.0
    corr_xau_us10y: float = 0.0
    
    # DXY analysis
    dxy_trend: str = "neutral"       # strengthening | weakening | neutral
    dxy_change_pct: float = 0.0       # recent % change
    
    # US10Y analysis
    us10y_trend: str = "neutral"      # rising | falling | neutral
    us10y_change_pct: float = 0.0
    
    # Diagnostic
    data_quality: str = "ok"
    details: str = ""
    
    def is_pass(self) -> bool:
        return self.verdict == FilterVerdict.PASS
    
    def is_blocked(self) -> bool:
        return self.verdict == FilterVerdict.FAIL
    
    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "corr_xau_dxy": round(self.corr_xau_dxy, 4),
            "corr_xau_us10y": round(self.corr_xau_us10y, 4),
            "dxy_trend": self.dxy_trend,
            "dxy_change_pct": round(self.dxy_change_pct, 4),
            "us10y_trend": self.us10y_trend,
            "us10y_change_pct": round(self.us10y_change_pct, 4),
            "data_quality": self.data_quality,
            "details": self.details,
        }


class IntermarketFilter:
    """
    Intermarket correlation and trend analysis.
    
    Validates trade direction against DXY and US10Y behavior.
    Historically, gold has strong negative correlation with USD.
    """
    
    def __init__(self, bridge: MT5Bridge = None, config=None):
        """
        Initialize intermarket filter.
        
        Args:
            bridge: MT5Bridge for fetching intermarket data
            config: TradingSystemConfig.intermarket
        """
        self.bridge = bridge
        self.features = FeatureEngine()
        
        # Default parameters
        self.correlation_period = 20
        self.corr_threshold_long = -0.25
        self.corr_threshold_short = 0.25
        self.dxy_weakening_period = 5
        
        # Symbols
        self.dxy_symbol = "DXY"
        self.us10y_symbol = "US10Y"
        self.primary_symbol = "XAUUSD"
        
        if config is not None:
            self._apply_config(config)
        
        # Cache for correlation data
        self._last_check: Optional[IntermarketSignal] = None
        self._cache_ttl_seconds: int = 60  # reuse for 60 seconds
        self._cache_timestamp: Optional[datetime] = None
    
    def _apply_config(self, config) -> None:
        """Apply configuration settings."""
        if hasattr(config, 'intermarket'):
            im = config.intermarket
            self.correlation_period = im.correlation_period
            self.corr_threshold_long = im.corr_threshold_long
            self.corr_threshold_short = im.corr_threshold_short
            self.dxy_weakening_period = im.dxy_weakening_period
        
        if hasattr(config, 'symbols'):
            self.primary_symbol = config.symbols.primary
            self.dxy_symbol = config.symbols.intermarket_dxy
            self.us10y_symbol = config.symbols.intermarket_us10y
    
    # ── Main Analysis ─────────────────────────────────────────────────────────
    
    def analyze(self, direction: str = None, force_refresh: bool = False) -> IntermarketSignal:
        """
        Full intermarket analysis for a given trade direction.
        
        Args:
            direction: Trade direction to validate ('long' or 'short').
                      If None, returns general correlation data without verdict.
            force_refresh: Force refresh even if cache is valid.
            
        Returns:
            IntermarketSignal with verdict.
        """
        # Check cache
        if not force_refresh and self._is_cache_valid():
            cached = self._last_check
            if direction:
                return self._apply_directional_filter(cached, direction)
            return cached
        
        # Fetch intermarket data
        dxy_data = self._fetch_symbol_data(self.dxy_symbol)
        xau_data = self._fetch_symbol_data(self.primary_symbol)
        us10y_data = self._fetch_symbol_data(self.us10y_symbol)
        
        # Data quality check
        data_quality = self._assess_data_quality(dxy_data, xau_data, us10y_data)
        
        if data_quality != "ok":
            signal = IntermarketSignal(
                verdict=FilterVerdict.NO_DATA,
                data_quality=data_quality,
                details=f"Missing data: {data_quality}"
            )
            self._cache_result(signal)
            return signal
        
        # Compute correlations
        corr_xau_dxy = self._compute_correlation(xau_data, dxy_data)
        corr_xau_us10y = self._compute_correlation(xau_data, us10y_data)
        
        # DXY trend analysis
        dxy_trend, dxy_change = self._analyze_trend(dxy_data, self.dxy_weakening_period)
        
        # US10Y trend analysis
        us10y_trend, us10y_change = self._analyze_trend(us10y_data, self.dxy_weakening_period)
        
        # Build signal
        signal = IntermarketSignal(
            verdict=FilterVerdict.PASS,  # default, directional filter overrides
            corr_xau_dxy=corr_xau_dxy,
            corr_xau_us10y=corr_xau_us10y,
            dxy_trend=dxy_trend,
            dxy_change_pct=dxy_change,
            us10y_trend=us10y_trend,
            us10y_change_pct=us10y_change,
            data_quality=data_quality,
            details="",
        )
        
        # Apply directional filtering if requested
        if direction:
            signal = self._apply_directional_filter(signal, direction)
        
        self._cache_result(signal)
        return signal
    
    def _apply_directional_filter(self, signal: IntermarketSignal,
                                   direction: str) -> IntermarketSignal:
        """
        Apply directional logic to determine PASS/FAIL/CAUTION.
        
        PRD rules:
        - LONG: corr(XAU,DXY) < -0.25 AND DXY weakening → PASS
        - SHORT: corr(XAU,DXY) > 0.25 AND DXY strengthening → PASS
        - US10Y rising sharply → CAUTION on longs
        """
        corr = signal.corr_xau_dxy
        reasons = []
        
        if direction == "long":
            # Long requires negative correlation + DXY weakening
            corr_ok = corr < self.corr_threshold_long
            dxy_ok = signal.dxy_trend == "weakening"
            
            if corr_ok and dxy_ok:
                signal.verdict = FilterVerdict.PASS
            elif corr_ok or dxy_ok:
                signal.verdict = FilterVerdict.CAUTION
                reasons.append("partial_confirmation")
            else:
                signal.verdict = FilterVerdict.FAIL
                if corr >= self.corr_threshold_long:
                    reasons.append(f"corr_fail:{corr:.3f}>={self.corr_threshold_long}")
                if signal.dxy_trend != "weakening":
                    reasons.append(f"dxy_not_weakening:{signal.dxy_trend}")
            
            # Additional: US10Y caution
            if signal.us10y_trend == "rising" and abs(signal.us10y_change_pct) > 0.001:
                if signal.verdict == FilterVerdict.PASS:
                    signal.verdict = FilterVerdict.CAUTION
                    reasons.append("us10y_rising_caution")
        
        elif direction == "short":
            corr_ok = corr > self.corr_threshold_short
            dxy_ok = signal.dxy_trend == "strengthening"
            
            if corr_ok and dxy_ok:
                signal.verdict = FilterVerdict.PASS
            elif corr_ok or dxy_ok:
                signal.verdict = FilterVerdict.CAUTION
                reasons.append("partial_confirmation")
            else:
                signal.verdict = FilterVerdict.FAIL
                if corr <= self.corr_threshold_short:
                    reasons.append(f"corr_fail:{corr:.3f}<={self.corr_threshold_short}")
                if signal.dxy_trend != "strengthening":
                    reasons.append(f"dxy_not_strengthening:{signal.dxy_trend}")
        
        signal.details = " | ".join(reasons) if reasons else "all_checks_passed"
        return signal
    
    # ── Correlation Computation ────────────────────────────────────────────────
    
    def _compute_correlation(self, df_a: pd.DataFrame, df_b: pd.DataFrame) -> float:
        """
        Compute rolling Pearson correlation between two price series.
        
        Args:
            df_a: First symbol's OHLCV data
            df_b: Second symbol's OHLCV data
            
        Returns:
            Latest correlation value (-1 to 1).
        """
        if df_a.empty or df_b.empty:
            return 0.0
        
        # Align timestamps and compute log returns
        returns_a = np.log(df_a['close'] / df_a['close'].shift(1)).dropna()
        returns_b = np.log(df_b['close'] / df_b['close'].shift(1)).dropna()
        
        # Align indexes
        common_idx = returns_a.index.intersection(returns_b.index)
        if len(common_idx) < self.correlation_period:
            return 0.0
        
        returns_a = returns_a.loc[common_idx]
        returns_b = returns_b.loc[common_idx]
        
        # Rolling correlation
        rolling_corr = returns_a.rolling(window=self.correlation_period).corr(returns_b)
        
        latest = rolling_corr.iloc[-1] if not rolling_corr.empty else 0.0
        return float(latest)
    
    def _analyze_trend(self, df: pd.DataFrame, period: int) -> Tuple[str, float]:
        """
        Analyze recent trend direction and magnitude.
        
        Args:
            df: OHLCV DataFrame
            period: Lookback period for trend analysis
            
        Returns:
            Tuple of (trend_direction, change_percentage)
        """
        if df.empty or len(df) < period:
            return "neutral", 0.0
        
        recent = df.tail(period)
        start_price = recent['close'].iloc[0]
        end_price = recent['close'].iloc[-1]
        
        if start_price <= 0:
            return "neutral", 0.0
        
        change_pct = (end_price - start_price) / start_price
        
        # Determine trend
        if change_pct > 0.002:  # > 0.2%
            return "strengthening" if "DXY" in str(df.get('symbol', '') or self.dxy_symbol) else "rising", change_pct
        elif change_pct < -0.002:
            return "weakening" if "DXY" in str(df.get('symbol', '') or self.dxy_symbol) else "falling", change_pct
        else:
            # Check short-term slope
            slope = self._compute_slope(recent['close'].values)
            if slope > 0:
                return "strengthening" if "DXY" in str(df.get('symbol', '') or self.dxy_symbol) else "rising", change_pct
            elif slope < 0:
                return "weakening" if "DXY" in str(df.get('symbol', '') or self.dxy_symbol) else "falling", change_pct
        
        return "neutral", change_pct
    
    @staticmethod
    def _compute_slope(values: np.ndarray) -> float:
        """Compute linear slope of a series."""
        if len(values) < 2:
            return 0.0
        x = np.arange(len(values))
        slope = np.polyfit(x, values, 1)[0]
        return float(slope)
    
    # ── Data Fetching & Quality ────────────────────────────────────────────────
    
    def _fetch_symbol_data(self, symbol: str, bars: int = 200,
                           timeframe: str = "M5") -> pd.DataFrame:
        """
        Fetch OHLCV data for intermarket symbols.
        
        Args:
            symbol: Symbol name
            bars: Number of bars
            timeframe: MT5 timeframe
            
        Returns:
            DataFrame or empty if unavailable.
        """
        if self.bridge is None:
            return pd.DataFrame()
        
        try:
            self.bridge.ensure_symbols([symbol])
            df = self.bridge.get_bars(symbol, timeframe, count=bars)
            return df
        except Exception as e:
            logger.warning("intermarket_fetch_failed", symbol=symbol, error=str(e))
            return pd.DataFrame()
    
    def _assess_data_quality(self, dxy_df: pd.DataFrame,
                             xau_df: pd.DataFrame,
                             us10y_df: pd.DataFrame) -> str:
        """Assess quality of intermarket data."""
        issues = []
        
        if xau_df.empty or len(xau_df) < self.correlation_period:
            issues.append("xau_insufficient")
        
        if dxy_df.empty or len(dxy_df) < self.correlation_period:
            issues.append("dxy_insufficient")
        
        if us10y_df.empty or len(us10y_df) < self.correlation_period:
            issues.append("us10y_insufficient")
        else:
            # Invert — some feeds use inverted yield convention
            pass
        
        if not issues:
            return "ok"
        return "|".join(issues)
    
    # ── Cache ──────────────────────────────────────────────────────────────────
    
    def _is_cache_valid(self) -> bool:
        """Check if cached analysis is still fresh."""
        if self._last_check is None or self._cache_timestamp is None:
            return False
        age = (datetime.now(timezone.utc) - self._cache_timestamp).total_seconds()
        return age < self._cache_ttl_seconds
    
    def _cache_result(self, signal: IntermarketSignal) -> None:
        """Cache the latest intermarket analysis."""
        self._last_check = signal
        self._cache_timestamp = datetime.now(timezone.utc)
    
    def invalidate_cache(self) -> None:
        """Force cache invalidation."""
        self._cache_timestamp = None
        self._last_check = None
    
    # ── Convenience ────────────────────────────────────────────────────────────
    
    def is_long_confirmed(self) -> bool:
        """Quick check: is long direction confirmed by intermarket?"""
        result = self.analyze(direction="long")
        return result.verdict == FilterVerdict.PASS
    
    def is_short_confirmed(self) -> bool:
        """Quick check: is short direction confirmed by intermarket?"""
        result = self.analyze(direction="short")
        return result.verdict == FilterVerdict.PASS
    
    def get_last_analysis(self) -> Optional[IntermarketSignal]:
        """Get the last cached intermarket analysis."""
        return self._last_check
    
    def get_status(self) -> dict:
        """Get current filter status."""
        if self._last_check is None:
            return {"status": "no_data"}
        return self._last_check.to_dict()
