"""
Trend-Following / Breakout Strategy (London / NY High-Volatility Sessions).

Core rules from PRD:

LONG Entry Conditions (all must be true):
1. EMA(9) > EMA(21) AND price closed above both
2. ADX(14) > 25 AND ADX rising (or DI+ > DI-)
3. Price breaks above previous 15-20 period high (breakout confirmation)
4. Tick volume on breakout bar ≥ 1.3x average of previous 10 bars
5. Intermarket filter passed (DXY weakening)
6. No news pause + anomaly clear
7. Regime is trend_following

SHORT Entry: Symmetric (EMA(9) < EMA(21), break below swing low, etc.)

Trade Management:
- Stop Loss: Entry − 2.0×ATR(20) (below recent swing or EMA(21))
- TP1 (40%): +2.0×ATR
- TP2 (30%): +3.5×ATR
- Runner (30%): Trail with 1.5×ATR or previous structure
- Trailing Stop: Activate after +1.5R, trail by structure or ATR
- Partial Close: Staged scaling out
- Max Hold: Until regime change or end of NY session + trail
"""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Tuple
from enum import Enum

import numpy as np
import pandas as pd

from src.features.technical import FeatureEngine
from src.regime.detector import Regime, TrendDirection
from src.regime.intermarket import IntermarketFilter, FilterVerdict
from src.ml.anomaly import AnomalyDetector
from src.news.filter import NewsFilter
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


@dataclass
class TFEntrySignal:
    """Trend-following entry signal with all conditions and diagnostics."""
    signal: str  # "long", "short", "none"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Entry parameters
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    
    # Partial close percentages
    tp1_close_pct: float = 40.0
    tp2_close_pct: float = 30.0
    runner_pct: float = 30.0
    
    # Indicator values
    ema9: float = 0.0
    ema21: float = 0.0
    adx: float = 0.0
    atr: float = 0.0
    volume_ratio: float = 0.0
    breakout_level: float = 0.0
    
    # Condition checks
    conditions: dict = field(default_factory=dict)
    all_conditions_met: bool = False
    
    # Risk metrics
    risk_reward_ratio: float = 0.0
    risk_amount_pips: float = 0.0
    
    # Filters
    intermarket_verdict: str = "no_data"
    trend_direction: str = "neutral"
    
    def to_dict(self) -> dict:
        return {
            "signal": self.signal,
            "entry": round(self.entry_price, 5),
            "sl": round(self.stop_loss, 5),
            "tp1": round(self.take_profit_1, 5),
            "tp2": round(self.take_profit_2, 5),
            "ema9": round(self.ema9, 5),
            "ema21": round(self.ema21, 5),
            "adx": round(self.adx, 2),
            "atr": round(self.atr, 5),
            "vol_ratio": round(self.volume_ratio, 2),
            "breakout_level": round(self.breakout_level, 5),
            "rr_ratio": round(self.risk_reward_ratio, 2),
            "conditions": self.conditions,
            "intermarket": self.intermarket_verdict,
            "direction": self.trend_direction,
        }


class TrendFollowingStrategy:
    """
    Trend-following / breakout trading strategy for London/NY sessions.
    
    Uses EMA crossover, ADX trend strength, breakout confirmation,
    and tick volume confirmation for high-probability entries.
    """
    
    def __init__(self, config=None):
        """
        Initialize trend-following strategy.
        
        Args:
            config: TradingSystemConfig.trend_following
        """
        # Default parameters
        self.ema_fast = 9
        self.ema_slow = 21
        self.adx_min = 25
        self.adx_must_rise = True
        self.breakout_period = 20
        self.tick_volume_period = 10
        self.tick_volume_multiplier = 1.3
        self.atr_multiplier_sl = 2.0
        self.atr_multiplier_tp1 = 2.0
        self.atr_multiplier_tp2 = 3.5
        self.partial_close_tp1_pct = 40
        self.partial_close_tp2_pct = 30
        self.runner_pct = 30
        self.trail_activate_r_multiple = 1.5
        self.trail_atr = 1.5
        self.max_concurrent = 1
        self.min_risk_reward = 1.8
        
        if config is not None:
            self._apply_config(config)
        
        self.features = FeatureEngine()
        self._last_signal: Optional[TFEntrySignal] = None
        self._cooldown_until: Optional[datetime] = None
        self._cooldown_minutes = 15
    
    def _apply_config(self, config) -> None:
        """Apply strategy configuration."""
        if hasattr(config, 'trend_following'):
            tf = config.trend_following
            self.ema_fast = tf.ema_fast
            self.ema_slow = tf.ema_slow
            self.adx_min = tf.adx_min
            self.adx_must_rise = tf.adx_must_rise
            self.breakout_period = tf.breakout_period
            self.tick_volume_period = tf.tick_volume_period
            self.tick_volume_multiplier = tf.tick_volume_multiplier
            self.atr_multiplier_sl = tf.atr_multiplier_sl
            self.atr_multiplier_tp1 = tf.atr_multiplier_tp1
            self.atr_multiplier_tp2 = tf.atr_multiplier_tp2
            self.partial_close_tp1_pct = tf.partial_close_tp1_pct
            self.partial_close_tp2_pct = tf.partial_close_tp2_pct
            self.runner_pct = tf.runner_pct
            self.trail_activate_r_multiple = tf.trail_activate_r_multiple
            self.trail_atr = tf.trail_atr
            self.max_concurrent = tf.max_concurrent
            self.min_risk_reward = tf.min_risk_reward
    
    # ── Signal Generation ─────────────────────────────────────────────────────
    
    def generate_signal(self, df: pd.DataFrame,
                       regime_info: dict = None,
                       intermarket_filter: IntermarketFilter = None,
                       news_filter: NewsFilter = None,
                       anomaly_detector: AnomalyDetector = None) -> TFEntrySignal:
        """
        Generate a trend-following entry signal.
        
        Args:
            df: OHLCV DataFrame (M5, 100+ bars)
            regime_info: Dict from RegimeDetector.detect()
            intermarket_filter: IntermarketFilter instance
            news_filter: NewsFilter instance
            anomaly_detector: AnomalyDetector instance
            
        Returns:
            TFEntrySignal with entry details.
        """
        # Check cooldown
        if self._cooldown_until and datetime.now(timezone.utc) < self._cooldown_until:
            return TFEntrySignal(signal="none", conditions={"cooldown_active": True})
        
        if df.empty or len(df) < max(self.ema_slow, self.breakout_period) + 5:
            return TFEntrySignal(signal="none", conditions={"insufficient_data": True})
        
        self.features.set_data(df)
        
        # ── Compute Indicators ──
        
        # EMAs
        ema9 = self.features.ema(self.ema_fast)
        ema21 = self.features.ema(self.ema_slow)
        
        # ADX
        adx, plus_di, minus_di = self.features.adx(14)
        
        # ATR
        atr = self.features.atr(20)
        
        # Donchian / Breakout levels
        donchian_upper, donchian_lower = self.features.donchian(self.breakout_period)
        
        # Volume
        volume_avg = self.features.volume_avg(self.tick_volume_period)
        
        # Get latest values
        close = float(df['close'].iloc[-1])
        high = float(df['high'].iloc[-1])
        low = float(df['low'].iloc[-1])
        
        ema9_val = float(ema9.iloc[-1]) if not ema9.empty else close
        ema21_val = float(ema21.iloc[-1]) if not ema21.empty else close
        adx_val = float(adx.iloc[-1]) if not adx.empty else 20.0
        plus_di_val = float(plus_di.iloc[-1]) if not plus_di.empty else 25.0
        minus_di_val = float(minus_di.iloc[-1]) if not minus_di.empty else 25.0
        atr_val = float(atr.iloc[-1]) if not atr.empty else close * 0.003
        
        breakout_high = float(donchian_upper.iloc[-1]) if not donchian_upper.empty else high
        breakout_low = float(donchian_lower.iloc[-1]) if not donchian_lower.empty else low
        
        current_volume = float(df['tick_volume'].iloc[-1]) if 'tick_volume' in df.columns else 0
        avg_volume = float(volume_avg.iloc[-1]) if not volume_avg.empty else current_volume
        volume_ratio_val = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # Check ADX rising
        adx_rising = False
        if not adx.empty and len(adx) >= 3:
            adx_rising = bool(adx.iloc[-1] > adx.iloc[-3])
        
        # ── Condition Checks ──
        conditions = {}
        
        # EMA alignment
        conditions["ema_bullish"] = ema9_val > ema21_val and close > ema9_val
        conditions["ema_bearish"] = ema9_val < ema21_val and close < ema9_val
        
        # Trend strength
        conditions["adx_trending"] = adx_val > self.adx_min
        conditions["adx_rising"] = adx_rising if self.adx_must_rise else True
        conditions["di_bullish"] = plus_di_val > minus_di_val
        conditions["di_bearish"] = minus_di_val > plus_di_val
        
        # Breakout
        conditions["breakout_up"] = close > breakout_high
        conditions["breakout_down"] = close < breakout_low
        
        # Volume
        conditions["volume_surge"] = volume_ratio_val >= self.tick_volume_multiplier
        
        # Regime
        regime_ok = True
        trend_dir = "neutral"
        if regime_info:
            regime_ok = regime_info.get("regime") == Regime.TREND_FOLLOWING
            trend_dir = regime_info.get("direction", "neutral")
        conditions["regime_tf"] = regime_ok
        
        # External filters
        intermarket_verdict = "no_data"
        
        if intermarket_filter:
            analysis = intermarket_filter.analyze()
            if analysis.data_quality == "ok":
                conditions["intermarket_ok"] = analysis.verdict != FilterVerdict.FAIL
                intermarket_verdict = analysis.verdict
            else:
                conditions["intermarket_ok"] = True
        
        if news_filter and news_filter.enabled:
            conditions["news_clear"] = not news_filter.is_paused().is_paused
        
        # ── Signal Decision ──
        signal_type = "none"
        entry_price = 0.0
        stop_loss = 0.0
        tp1 = 0.0
        tp2 = 0.0
        
        # LONG signal
        long_conditions = [
            conditions.get("ema_bullish", False),
            conditions.get("adx_trending", False) and conditions.get("di_bullish", False),
            conditions.get("breakout_up", False),
            conditions.get("volume_surge", False),
            conditions.get("regime_tf", True),
            conditions.get("intermarket_ok", True),
            conditions.get("news_clear", True),
        ]
        
        # Also accept if ADX rising (relaxed condition)
        long_relaxed = [
            conditions.get("ema_bullish", False),
            (conditions.get("adx_trending", False) or conditions.get("adx_rising", False)) and conditions.get("di_bullish", False),
            conditions.get("breakout_up", False),
            conditions.get("volume_surge", False),
            conditions.get("regime_tf", True),
            conditions.get("intermarket_ok", True),
            conditions.get("news_clear", True),
        ]
        
        if all(long_conditions) or all(long_relaxed):
            signal_type = "long"
            entry_price = close
            
            # Stop loss: Entry - 2.0×ATR (below recent swing or EMA21)
            sl_atr = entry_price - self.atr_multiplier_sl * atr_val
            sl_swing = breakout_low - atr_val * 0.3
            sl_ema = ema21_val - atr_val * 0.3
            stop_loss = max(sl_atr, sl_swing, sl_ema)  # tightest (highest for long)
            
            tp1 = entry_price + self.atr_multiplier_tp1 * atr_val
            tp2 = entry_price + self.atr_multiplier_tp2 * atr_val
        
        # SHORT signal
        short_conditions = [
            conditions.get("ema_bearish", False),
            conditions.get("adx_trending", False) and conditions.get("di_bearish", False),
            conditions.get("breakout_down", False),
            conditions.get("volume_surge", False),
            conditions.get("regime_tf", True),
            conditions.get("intermarket_ok", True),
            conditions.get("news_clear", True),
        ]
        
        short_relaxed = [
            conditions.get("ema_bearish", False),
            (conditions.get("adx_trending", False) or conditions.get("adx_rising", False)) and conditions.get("di_bearish", False),
            conditions.get("breakout_down", False),
            conditions.get("volume_surge", False),
            conditions.get("regime_tf", True),
            conditions.get("intermarket_ok", True),
            conditions.get("news_clear", True),
        ]
        
        if all(short_conditions) or all(short_relaxed):
            signal_type = "short"
            entry_price = close
            
            sl_atr = entry_price + self.atr_multiplier_sl * atr_val
            sl_swing = breakout_high + atr_val * 0.3
            sl_ema = ema21_val + atr_val * 0.3
            stop_loss = min(sl_atr, sl_swing, sl_ema)
            
            tp1 = entry_price - self.atr_multiplier_tp1 * atr_val
            tp2 = entry_price - self.atr_multiplier_tp2 * atr_val
        
        # Risk-reward
        rr_ratio = 0.0
        if signal_type != "none" and stop_loss > 0:
            if signal_type == "long":
                risk = entry_price - stop_loss
                reward = tp1 - entry_price
            else:
                risk = stop_loss - entry_price
                reward = entry_price - tp1
            
            if risk > 0:
                rr_ratio = reward / risk
        
        # Filter by minimum R:R
        all_met = signal_type != "none" and rr_ratio >= self.min_risk_reward
        
        signal = TFEntrySignal(
            signal=signal_type if all_met else "none",
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            tp1_close_pct=self.partial_close_tp1_pct,
            tp2_close_pct=self.partial_close_tp2_pct,
            runner_pct=self.runner_pct,
            ema9=ema9_val,
            ema21=ema21_val,
            adx=adx_val,
            atr=atr_val,
            volume_ratio=volume_ratio_val,
            breakout_level=breakout_high if signal_type == "long" else breakout_low,
            conditions=conditions,
            all_conditions_met=all_met,
            risk_reward_ratio=rr_ratio,
            risk_amount_pips=abs(entry_price - stop_loss) if entry_price > 0 else 0,
            intermarket_verdict=intermarket_verdict,
            trend_direction=trend_dir,
        )
        
        if all_met:
            self._last_signal = signal
            self._cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=self._cooldown_minutes)
            logger.info(
                "tf_signal_generated",
                signal=signal_type,
                entry=round(entry_price, 5),
                sl=round(stop_loss, 5),
                tp1=round(tp1, 5),
                tp2=round(tp2, 5),
                rr=f"{rr_ratio:.2f}",
                volume_ratio=f"{volume_ratio_val:.2f}",
            )
        
        return signal
    
    # ── Trade Management ──────────────────────────────────────────────────────
    
    def update_trailing_stop(self, position: dict, current_price: float,
                            atr: float, ema21: float = 0.0) -> Optional[float]:
        """
        Update trailing stop for trend-following position.
        
        Uses structure-based trailing after +1.5R with 1.5×ATR distance.
        
        Args:
            position: Position info dict
            current_price: Current price
            atr: Current ATR
            ema21: Current EMA(21) for structure reference
            
        Returns:
            New stop loss price, or None if no change needed.
        """
        pos_type = position.get("type", "buy")
        entry_price = position.get("open_price", 0)
        current_sl = position.get("stop_loss", 0)
        
        if entry_price <= 0 or atr <= 0:
            return None
        
        # Calculate R-multiple
        initial_risk = abs(entry_price - current_sl) if current_sl > 0 else atr * self.atr_multiplier_sl
        
        if pos_type == "buy":
            profit = current_price - entry_price
            r_multiple = profit / initial_risk if initial_risk > 0 else 0
            
            # Stage 1: Wait for +trail_activate_r_multiple (1.5R)
            if r_multiple < self.trail_activate_r_multiple:
                return None
            
            # Stage 2: Trail by 1.5×ATR or structure
            trail_atr = current_price - self.trail_atr * atr
            trail_ema = ema21 if ema21 > 0 else trail_atr
            
            # Use higher of ATR-based or EMA-based (protect profits)
            new_sl = max(trail_atr, trail_ema)
            
            if new_sl > current_sl:
                return new_sl
        else:
            profit = entry_price - current_price
            r_multiple = profit / initial_risk if initial_risk > 0 else 0
            
            if r_multiple < self.trail_activate_r_multiple:
                return None
            
            trail_atr = current_price + self.trail_atr * atr
            trail_ema = ema21 if ema21 > 0 else trail_atr
            
            new_sl = min(trail_atr, trail_ema)
            
            if new_sl < current_sl:
                return new_sl
        
        return None
    
    def should_close_partial(self, position: dict, current_price: float) -> Tuple[bool, float]:
        """
        Check staged partial close.
        
        Stage 1: Close tp1_close_pct% at TP1
        Stage 2: Close tp2_close_pct% at TP2 (if TP1 already triggered)
        
        Args:
            position: Position info dict
            current_price: Current price
            
        Returns:
            Tuple of (should_close, close_volume_pct)
        """
        pos_type = position.get("type", "buy")
        tp1 = position.get("take_profit_1", 0)  # stored in comment/metadata
        tp2 = position.get("take_profit_2", 0)
        
        # TP1 check (40%)
        if tp1 > 0:
            if pos_type == "buy" and current_price >= tp1:
                return True, self.partial_close_tp1_pct / 100.0
            if pos_type == "sell" and current_price <= tp1:
                return True, self.partial_close_tp1_pct / 100.0
        
        # TP2 check (30%) — executed after TP1
        if tp2 > 0:
            if pos_type == "buy" and current_price >= tp2:
                return True, self.partial_close_tp2_pct / 100.0
            if pos_type == "sell" and current_price <= tp2:
                return True, self.partial_close_tp2_pct / 100.0
        
        return False, 0.0
    
    # ── Status ────────────────────────────────────────────────────────────────
    
    def get_last_signal(self) -> Optional[dict]:
        """Get the last generated signal."""
        if self._last_signal is None:
            return None
        return self._last_signal.to_dict()
    
    def is_cooldown_active(self) -> bool:
        """Check if signal cooldown is active."""
        if self._cooldown_until is None:
            return False
        return datetime.now(timezone.utc) < self._cooldown_until
