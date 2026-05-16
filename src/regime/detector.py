"""
Hybrid Regime Detection Engine.

Combines time-based session detection with dynamic confirmation
from ATR(20) and ADX(14) to produce one of three regimes:
- mean_reversion: Low volatility Asian session with ATR + ADX confirm
- trend_following: High volatility London/NY with ATR + ADX confirm
- neutral: No clear regime signal, trade with tight filters or skip

Key rules:
- Regime can only switch after confirmation on closed bar (avoid whipsaw)
- Uses rolling median ATR(50) for relative volatility comparison
- ADX(14) + DI+/DI- for trend strength and direction
- Persists regime through noise with confirmation bar requirement
"""

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from enum import Enum

import numpy as np
import pandas as pd

from src.features.technical import FeatureEngine
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class Regime(str, Enum):
    MEAN_REVERSION = "mean_reversion"
    TREND_FOLLOWING = "trend_following"
    NEUTRAL = "neutral"


class TrendDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class RegimeSignal:
    """Output from regime detection with full diagnostic data."""
    regime: Regime
    direction: TrendDirection
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Diagnostic values
    session: str = ""
    atr_value: float = 0.0
    atr_median: float = 0.0
    atr_ratio: float = 0.0
    adx_value: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    adx_rising: bool = False
    
    # Confirmation
    confirmed: bool = False
    confirmation_bars: int = 0
    
    def is_tradeable(self) -> bool:
        """Regime allows trading."""
        return self.regime != Regime.NEUTRAL
    
    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "direction": self.direction,
            "session": self.session,
            "atr": round(self.atr_value, 5),
            "atr_median": round(self.atr_median, 5),
            "atr_ratio": round(self.atr_ratio, 3),
            "adx": round(self.adx_value, 2),
            "plus_di": round(self.plus_di, 2),
            "minus_di": round(self.minus_di, 2),
            "adx_rising": self.adx_rising,
            "confirmed": self.confirmed,
            "confirmation_bars": self.confirmation_bars,
        }


class RegimeDetector:
    """
    Hybrid regime detection engine.
    
    Uses time-based session mapping + ATR/ADX dynamic confirmation
    to determine the current market regime.
    
    The regime only switches after `confirm_bars` consistent signals,
    preventing whipsaw in transitional periods.
    """

    def __init__(self, config=None):
        """
        Initialize regime detector.
        
        Args:
            config: TradingSystemConfig.regime and TradingSystemConfig.sessions
        """
        # Default parameters
        self.atr_period = 20
        self.atr_rolling_median_period = 50
        self.atr_median_low_threshold = 1.15
        self.atr_median_high_threshold = 1.25
        self.adx_period = 14
        self.adx_low_threshold = 22
        self.adx_high_threshold = 25
        self.confirm_bars = 1
        
        # Session defaults (UTC)
        self.session_map = {
            "asian": (0, 8),
            "london": (8, 13),
            "ny_overlap": (13, 17),
            "late_ny": (17, 24),
        }
        
        if config is not None:
            self._apply_config(config)
        
        # State tracking
        self._current_regime: Regime = Regime.NEUTRAL
        self._pending_regime: Optional[Regime] = None
        self._confirmation_count: int = 0
        self._last_regime_change: Optional[datetime] = None
        self._regime_history: List[RegimeSignal] = []
        
        # Feature engine
        self.features = FeatureEngine()
    
    def _apply_config(self, config) -> None:
        """Apply configuration from Pydantic model or dict."""
        if hasattr(config, 'regime'):
            r = config.regime
            self.atr_period = r.atr_period
            self.atr_rolling_median_period = r.atr_rolling_median_period
            self.atr_median_low_threshold = r.atr_median_low_threshold
            self.atr_median_high_threshold = r.atr_median_high_threshold
            self.adx_period = r.adx_period
            self.adx_low_threshold = r.adx_low_threshold
            self.adx_high_threshold = r.adx_high_threshold
            self.confirm_bars = r.confirm_bars
        
        if hasattr(config, 'sessions'):
            s = config.sessions
            self.session_map = {
                "asian": (s.asian.start_utc, s.asian.end_utc),
                "london": (s.london.start_utc, s.london.end_utc),
                "ny_overlap": (s.ny_overlap.start_utc, s.ny_overlap.end_utc),
                "late_ny": (s.late_ny.start_utc, s.late_ny.end_utc),
            }
    
    # ── Session Detection ────────────────────────────────────────────────────
    
    def get_current_session(self, timestamp: datetime = None) -> str:
        """
        Determine current trading session based on UTC hour.
        
        Args:
            timestamp: Current datetime (UTC). Default: now.
            
        Returns:
            Session name: 'asian', 'london', 'ny_overlap', 'late_ny'
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        hour = timestamp.hour
        
        for session, (start, end) in self.session_map.items():
            if start <= hour < end:
                return session
        
        return "late_ny"  # fallback
    
    def is_session_transition(self, timestamp: datetime = None) -> bool:
        """
        Check if we're near a session boundary (within 10 minutes).
        
        Session transitions can be volatile — signals near boundaries
        should be treated with caution.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        hour = timestamp.hour
        minute = timestamp.minute
        
        # Check if within 10 min of any session boundary
        boundaries = [0, 8, 13, 17]
        for boundary in boundaries:
            if hour == boundary - 1 and minute >= 50:
                return True
            if hour == boundary and minute <= 10:
                return True
        
        return False
    
    # ── Volatility Assessment ─────────────────────────────────────────────────
    
    def _assess_volatility(self, df: pd.DataFrame) -> Tuple[float, float, float]:
        """
        Assess current volatility using ATR vs rolling median.
        
        Args:
            df: OHLCV DataFrame with enough history
            
        Returns:
            Tuple of (atr_value, atr_median, atr_ratio)
        """
        if df.empty or len(df) < self.atr_period:
            return 0.0, 0.0, 1.0
        
        self.features.set_data(df)
        atr_series = self.features.atr(self.atr_period)
        
        if atr_series.empty:
            return 0.0, 0.0, 1.0
        
        atr_value = float(atr_series.iloc[-1])
        
        # Rolling median of ATR for relative comparison
        rolling_median = atr_series.rolling(
            window=self.atr_rolling_median_period, min_periods=10
        ).median()
        
        atr_median = float(rolling_median.iloc[-1]) if not rolling_median.empty else atr_value
        
        atr_ratio = atr_value / atr_median if atr_median > 0 else 1.0
        
        return atr_value, atr_median, atr_ratio
    
    def _assess_trend(self, df: pd.DataFrame) -> Tuple[float, float, float, bool]:
        """
        Assess trend strength using ADX and directional indicators.
        
        Args:
            df: OHLCV DataFrame
            
        Returns:
            Tuple of (adx_value, plus_di, minus_di, is_rising)
        """
        if df.empty or len(df) < self.adx_period + 2:
            return 20.0, 25.0, 25.0, False
        
        self.features.set_data(df)
        adx_series, plus_series, minus_series = self.features.adx(self.adx_period)
        
        if adx_series.empty:
            return 20.0, 25.0, 25.0, False
        
        adx_value = float(adx_series.iloc[-1])
        plus_di = float(plus_series.iloc[-1]) if not plus_series.empty else 25.0
        minus_di = float(minus_series.iloc[-1]) if not minus_series.empty else 25.0
        
        # Check if ADX is rising (last 3 bars)
        adx_rising = False
        if len(adx_series) >= 3:
            adx_rising = bool(adx_series.iloc[-1] > adx_series.iloc[-3])
        
        return adx_value, plus_di, minus_di, adx_rising
    
    # ── Regime Determination ──────────────────────────────────────────────────
    
    def detect(self, df: pd.DataFrame, timestamp: datetime = None) -> RegimeSignal:
        """
        Determine current trading regime.
        
        Hybrid logic:
        1. Get current session (time-based)
        2. Compute ATR and ADX for dynamic confirmation
        3. Apply regime rules
        4. Require confirmation bars before switching
        
        Args:
            df: OHLCV DataFrame (M5, at least 50 bars)
            timestamp: Current datetime (UTC)
            
        Returns:
            RegimeSignal with full diagnostic data.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        session = self.get_current_session(timestamp)
        
        # Compute indicators
        atr_value, atr_median, atr_ratio = self._assess_volatility(df)
        adx_value, plus_di, minus_di, adx_rising = self._assess_trend(df)
        
        # Determine trend direction
        direction = self._determine_direction(plus_di, minus_di)
        
        # Hybrid regime logic (matching PRD spec)
        proposed_regime = self._compute_regime(session, atr_ratio, adx_value, adx_rising)
        
        # Confirmation logic — prevent whipsaw
        confirmed, confirmation_bars = self._confirm_regime(proposed_regime)
        
        if confirmed and proposed_regime != self._current_regime:
            old_regime = self._current_regime
            self._current_regime = proposed_regime
            self._last_regime_change = timestamp
            
            logger.info(
                "regime_changed",
                from_regime=old_regime,
                to_regime=proposed_regime,
                session=session,
                atr_ratio=f"{atr_ratio:.3f}",
                adx=f"{adx_value:.1f}",
                direction=direction,
            )
        
        signal = RegimeSignal(
            regime=self._current_regime,
            direction=direction,
            timestamp=timestamp,
            session=session,
            atr_value=atr_value,
            atr_median=atr_median,
            atr_ratio=atr_ratio,
            adx_value=adx_value,
            plus_di=plus_di,
            minus_di=minus_di,
            adx_rising=adx_rising,
            confirmed=self._current_regime != Regime.NEUTRAL,
            confirmation_bars=confirmation_bars,
        )
        
        # Record in history (keep last 1000)
        self._regime_history.append(signal)
        if len(self._regime_history) > 1000:
            self._regime_history = self._regime_history[-500:]
        
        return signal
    
    def _determine_direction(self, plus_di: float, minus_di: float) -> TrendDirection:
        """Determine trend direction from DI+/DI-."""
        if plus_di > minus_di * 1.1:
            return TrendDirection.BULLISH
        elif minus_di > plus_di * 1.1:
            return TrendDirection.BEARISH
        return TrendDirection.NEUTRAL
    
    def _compute_regime(self, session: str, atr_ratio: float,
                       adx_value: float, adx_rising: bool) -> Regime:
        """
        Compute regime using hybrid rules from PRD.
        
        PRD Spec:
        - Asian + ATR < median*1.15 + ADX < 22 → mean_reversion
        - London/NY + (ATR > median*1.25 or ADX > 25) → trend_following
        - Otherwise → neutral
        """
        # Mean-reversion regime
        if session == "asian":
            if (atr_ratio < self.atr_median_low_threshold and 
                adx_value < self.adx_low_threshold):
                return Regime.MEAN_REVERSION
            return Regime.NEUTRAL
        
        # Trend-following regime (London, NY Overlap)
        if session in ("london", "ny_overlap"):
            high_vol = atr_ratio > self.atr_median_high_threshold
            strong_trend = adx_value > self.adx_high_threshold
            
            if high_vol or strong_trend:
                return Regime.TREND_FOLLOWING
            
            # If not strongly trending, still allow trend if ADX is rising
            if adx_rising and adx_value > self.adx_low_threshold:
                return Regime.TREND_FOLLOWING
            
            return Regime.NEUTRAL
        
        # Late NY — neutral or reduced activity
        return Regime.NEUTRAL
    
    def _confirm_regime(self, proposed_regime: Regime) -> Tuple[bool, int]:
        """
        Require consistent regime signals for N bars before switching.
        
        Returns:
            Tuple of (confirmed, confirmation_count)
        """
        if proposed_regime == self._current_regime:
            # Same regime, reset pending
            self._pending_regime = None
            self._confirmation_count = 0
            return True, 0
        
        if proposed_regime == self._pending_regime:
            self._confirmation_count += 1
        else:
            # New proposed regime
            self._pending_regime = proposed_regime
            self._confirmation_count = 1
        
        confirmed = self._confirmation_count >= self.confirm_bars
        return confirmed, self._confirmation_count
    
    # ── Regime Context ─────────────────────────────────────────────────────────
    
    def get_allowed_strategies(self) -> List[str]:
        """
        Get list of strategy types allowed in current regime.
        """
        if self._current_regime == Regime.MEAN_REVERSION:
            return ["mean_reversion"]
        elif self._current_regime == Regime.TREND_FOLLOWING:
            return ["trend_following"]
        return []  # neutral — no strategies
    
    def is_mean_reversion(self) -> bool:
        """Check if currently in mean-reversion regime."""
        return self._current_regime == Regime.MEAN_REVERSION
    
    def is_trend_following(self) -> bool:
        """Check if currently in trend-following regime."""
        return self._current_regime == Regime.TREND_FOLLOWING
    
    def get_current(self) -> Regime:
        """Get current regime."""
        return self._current_regime
    
    def get_history(self, n: int = 10) -> List[dict]:
        """Get recent regime history."""
        return [r.to_dict() for r in self._regime_history[-n:]]
    
    def get_status(self) -> dict:
        """Get current detector status."""
        recent = self._regime_history[-1].to_dict() if self._regime_history else {}
        return {
            "current_regime": self._current_regime,
            "last_change": self._last_regime_change.isoformat() if self._last_regime_change else None,
            "pending_regime": self._pending_regime,
            "confirmation_count": self._confirmation_count,
            "recent_signal": recent,
        }
