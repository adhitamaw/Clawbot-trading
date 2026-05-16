"""
Mean-Reversion Strategy (Asian Session / Low Volatility).

Core rules from PRD:

LONG Entry Conditions (all must be true):
1. Price closes BELOW Lower Bollinger Band
2. RSI(14) ≤ 30 (oversold)
3. ADX(14) < 20 (ranging market)
4. Intermarket filter passed (DXY correlation supportive)
5. No active news pause window
6. Anomaly detection score = Normal
7. Session allows mean-reversion

SHORT Entry: Price above Upper Band + RSI ≥ 70 + symmetric.

Trade Management:
- Stop Loss: Opposite Bollinger Band or Entry ± 1.8×ATR (whichever tighter)
- Take Profit: TP1 (50%) at Middle Band or +1.5×ATR; TP2 (remainder) at +2.5×ATR
- Trailing Stop: After TP1 hit, move SL to breakeven + 0.5×ATR, trail by 0.8×ATR
- Partial Close: Mandatory 50% at TP1
- Max Hold Time: 4-6 hours or end of Asian session
- Max concurrent: 1 position in this regime
"""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Tuple
from enum import Enum

import numpy as np
import pandas as pd

from src.features.technical import FeatureEngine
from src.regime.detector import Regime
from src.regime.intermarket import IntermarketFilter, FilterVerdict
from src.ml.anomaly import AnomalyDetector
from src.news.filter import NewsFilter
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class SignalType(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


@dataclass
class MREntrySignal:
    """Mean-reversion entry signal with all conditions and diagnostics."""
    signal: str  # "long", "short", "none"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Entry parameters
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    position_size_pct: float = 50.0  # TP1 close %
    
    # Indicator values at signal
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    rsi: float = 0.0
    adx: float = 0.0
    atr: float = 0.0
    
    # Condition checks
    conditions: dict = field(default_factory=dict)
    all_conditions_met: bool = False
    
    # Risk metrics
    risk_reward_ratio: float = 0.0
    risk_amount_pips: float = 0.0
    reward_amount_pips: float = 0.0
    
    # Filters
    intermarket_verdict: str = "no_data"
    news_paused: bool = False
    anomaly_detected: bool = False
    
    def to_dict(self) -> dict:
        return {
            "signal": self.signal,
            "entry_price": round(self.entry_price, 5),
            "stop_loss": round(self.stop_loss, 5),
            "tp1": round(self.take_profit_1, 5),
            "tp2": round(self.take_profit_2, 5),
            "bb_upper": round(self.bb_upper, 5),
            "bb_lower": round(self.bb_lower, 5),
            "rsi": round(self.rsi, 2),
            "adx": round(self.adx, 2),
            "atr": round(self.atr, 5),
            "rr_ratio": round(self.risk_reward_ratio, 2),
            "all_met": self.all_conditions_met,
            "conditions": self.conditions,
            "intermarket": self.intermarket_verdict,
        }


class MeanReversionStrategy:
    """
    Mean-reversion trading strategy for Asian / low-volatility sessions.
    
    Generates signals when price deviates from mean (Bollinger Bands)
    with oversold/overbought RSI confirmation in ranging markets.
    """
    
    def __init__(self, config=None):
        """
        Initialize mean-reversion strategy.
        
        Args:
            config: TradingSystemConfig.mean_reversion
        """
        # Default parameters
        self.bollinger_period = 20
        self.bollinger_deviation = 2.0
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.adx_max = 20
        self.atr_multiplier_sl = 1.8
        self.atr_multiplier_tp1 = 1.5
        self.atr_multiplier_tp2 = 2.5
        self.trail_start_atr = 0.5
        self.trail_atr = 0.8
        self.partial_close_pct = 50
        self.max_hold_minutes = 360
        self.max_concurrent = 1
        self.min_risk_reward = 1.8
        
        if config is not None:
            self._apply_config(config)
        
        self.features = FeatureEngine()
        self._last_signal: Optional[MREntrySignal] = None
        self._cooldown_until: Optional[datetime] = None
        self._cooldown_minutes = 15  # minimum time between signals
    
    def _apply_config(self, config) -> None:
        """Apply strategy configuration."""
        if hasattr(config, 'mean_reversion'):
            mr = config.mean_reversion
            self.bollinger_period = mr.bollinger_period
            self.bollinger_deviation = mr.bollinger_deviation
            self.rsi_period = mr.rsi_period
            self.rsi_oversold = mr.rsi_oversold
            self.rsi_overbought = mr.rsi_overbought
            self.adx_max = mr.adx_max
            self.atr_multiplier_sl = mr.atr_multiplier_sl
            self.atr_multiplier_tp1 = mr.atr_multiplier_tp1
            self.atr_multiplier_tp2 = mr.atr_multiplier_tp2
            self.trail_start_atr = mr.trail_start_atr
            self.trail_atr = mr.trail_atr
            self.partial_close_pct = mr.partial_close_pct
            self.max_hold_minutes = mr.max_hold_minutes
            self.max_concurrent = mr.max_concurrent
            self.min_risk_reward = mr.min_risk_reward
    
    # ── Signal Generation ─────────────────────────────────────────────────────
    
    def generate_signal(self, df: pd.DataFrame,
                       regime_info: dict = None,
                       intermarket_filter: IntermarketFilter = None,
                       news_filter: NewsFilter = None,
                       anomaly_detector: AnomalyDetector = None) -> MREntrySignal:
        """
        Generate a mean-reversion entry signal.
        
        Args:
            df: OHLCV DataFrame (M5, 100+ bars)
            regime_info: Dict from RegimeDetector.detect()
            intermarket_filter: IntermarketFilter instance (optional)
            news_filter: NewsFilter instance (optional)
            anomaly_detector: AnomalyDetector instance (optional)
            
        Returns:
            MREntrySignal with signal details.
        """
        # Check cooldown
        if self._cooldown_until and datetime.now(timezone.utc) < self._cooldown_until:
            return MREntrySignal(signal="none", conditions={"cooldown_active": True})
        
        if df.empty or len(df) < self.bollinger_period + 2:
            return MREntrySignal(signal="none", conditions={"insufficient_data": True})
        
        # Compute all indicators
        self.features.set_data(df)
        
        # Bollinger Bands
        _, bb_upper, bb_lower = self.features.bollinger_bands(
            self.bollinger_period, self.bollinger_deviation
        )
        bb_middle = df['close'].rolling(window=self.bollinger_period).mean()
        
        # RSI
        rsi = self.features.rsi(self.rsi_period)
        
        # ADX
        adx, plus_di, minus_di = self.features.adx(14)
        
        # ATR
        atr = self.features.atr(20)
        
        # Get latest values
        close = float(df['close'].iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1]) if not bb_upper.empty else close * 1.02
        bb_middle_val = float(bb_middle.iloc[-1]) if not bb_middle.empty else close
        bb_lower_val = float(bb_lower.iloc[-1]) if not bb_lower.empty else close * 0.98
        rsi_val = float(rsi.iloc[-1]) if not rsi.empty else 50.0
        adx_val = float(adx.iloc[-1]) if not adx.empty else 25.0
        atr_val = float(atr.iloc[-1]) if not atr.empty else close * 0.002
        plus_di_val = float(plus_di.iloc[-1]) if not plus_di.empty else 25.0
        minus_di_val = float(minus_di.iloc[-1]) if not minus_di.empty else 25.0
        
        # ── Condition Checks ──
        conditions = {}
        
        # LONG conditions
        conditions["price_below_bb_lower"] = close < bb_lower_val
        conditions["rsi_oversold"] = rsi_val <= self.rsi_oversold
        conditions["adx_ranging"] = adx_val < self.adx_max
        
        # SHORT conditions
        conditions["price_above_bb_upper"] = close > bb_upper_val
        conditions["rsi_overbought"] = rsi_val >= self.rsi_overbought
        
        # Regime check
        regime_ok = True
        if regime_info:
            regime_ok = regime_info.get("regime") == Regime.MEAN_REVERSION
        conditions["regime_mr"] = regime_ok
        
        # External filters
        intermarket_verdict = "no_data"
        news_paused = False
        anomaly_detected = False
        
        # Intermarket check
        if intermarket_filter:
            analysis = intermarket_filter.analyze()
            if analysis.data_quality == "ok":
                conditions["intermarket_ok"] = analysis.verdict != FilterVerdict.FAIL
                intermarket_verdict = analysis.verdict
            else:
                conditions["intermarket_ok"] = True  # allow if no data
        
        # News check
        if news_filter and news_filter.enabled:
            status = news_filter.is_paused()
            news_paused = status.is_paused
            conditions["news_clear"] = not news_paused
        
        # Determine signal
        signal_type = "none"
        entry_price = 0.0
        stop_loss = 0.0
        tp1 = 0.0
        tp2 = 0.0
        
        # LONG signal
        long_conditions = [
            conditions.get("price_below_bb_lower", False),
            conditions.get("rsi_oversold", False),
            conditions.get("adx_ranging", False),
            conditions.get("regime_mr", True),
            conditions.get("intermarket_ok", True),
            conditions.get("news_clear", True),
        ]
        
        if all(long_conditions):
            signal_type = "long"
            entry_price = close  # or use ask price
            
            # Stop loss: opposite Bollinger Band or 1.8×ATR (whichever tighter)
            sl_atr = entry_price - self.atr_multiplier_sl * atr_val
            sl_band = bb_lower_val - atr_val * 0.5  # slightly below lower band
            stop_loss = max(sl_atr, sl_band)  # tighter stop (higher value for long)
            
            # Take Profit
            tp1 = entry_price + self.atr_multiplier_tp1 * atr_val
            tp1_band = bb_middle_val
            tp1 = min(tp1, tp1_band)  # earlier: whichever is hit first
            
            tp2 = entry_price + self.atr_multiplier_tp2 * atr_val
        
        # SHORT signal
        short_conditions = [
            conditions.get("price_above_bb_upper", False),
            conditions.get("rsi_overbought", False),
            conditions.get("adx_ranging", False),
            conditions.get("regime_mr", True),
            conditions.get("intermarket_ok", True),
            conditions.get("news_clear", True),
        ]
        
        if all(short_conditions):
            signal_type = "short"
            entry_price = close
            
            sl_atr = entry_price + self.atr_multiplier_sl * atr_val
            sl_band = bb_upper_val + atr_val * 0.5
            stop_loss = min(sl_atr, sl_band)
            
            tp1 = entry_price - self.atr_multiplier_tp1 * atr_val
            tp1_band = bb_middle_val
            tp1 = max(tp1, tp1_band)
            
            tp2 = entry_price - self.atr_multiplier_tp2 * atr_val
        
        # Risk-reward calculation
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
        
        all_met = signal_type != "none"
        
        signal = MREntrySignal(
            signal=signal_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            bb_upper=bb_upper_val,
            bb_middle=bb_middle_val,
            bb_lower=bb_lower_val,
            rsi=rsi_val,
            adx=adx_val,
            atr=atr_val,
            conditions=conditions,
            all_conditions_met=all_met,
            risk_reward_ratio=rr_ratio,
            risk_amount_pips=abs(entry_price - stop_loss) if entry_price > 0 else 0,
            reward_amount_pips=abs(tp1 - entry_price) if tp1 > 0 else 0,
            intermarket_verdict=intermarket_verdict,
            news_paused=news_paused,
            anomaly_detected=anomaly_detected,
        )
        
        if all_met:
            self._last_signal = signal
            self._cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=self._cooldown_minutes)
            logger.info(
                "mr_signal_generated",
                signal=signal_type,
                entry=round(entry_price, 5),
                sl=round(stop_loss, 5),
                tp1=round(tp1, 5),
                rr=f"{rr_ratio:.2f}",
            )
        
        return signal
    
    # ── Trade Management ──────────────────────────────────────────────────────
    
    def update_trailing_stop(self, position: dict, current_price: float,
                            atr: float) -> Optional[float]:
        """
        Update trailing stop for an active mean-reversion position.
        
        Args:
            position: Position info dict (from MT5Bridge)
            current_price: Current mid/bid/ask price
            atr: Current ATR value
            
        Returns:
            New stop loss price, or None if no change needed.
        """
        pos_type = position.get("type", "buy")
        entry_price = position.get("open_price", 0)
        current_sl = position.get("stop_loss", 0)
        tp = position.get("take_profit", 0)
        
        # Calculate profit in ATR multiples
        if pos_type == "buy":
            profit_pips = current_price - entry_price
        else:
            profit_pips = entry_price - current_price
        
        # Stage 1: Move to breakeven after TP1 distance
        tp1_distance = self.atr_multiplier_tp1 * atr
        
        if profit_pips >= tp1_distance * 0.7:  # 70% of TP1 distance
            # Move to breakeven + small buffer
            breakeven_sl = entry_price + (self.trail_start_atr * atr if pos_type == "buy" else -self.trail_start_atr * atr)
            
            if pos_type == "buy":
                if current_sl < breakeven_sl or current_sl == 0:
                    return breakeven_sl
            else:
                if current_sl > breakeven_sl or current_sl == 0:
                    return breakeven_sl
        
        # Stage 2: Trail after breakeven
        trail_distance = self.trail_atr * atr
        
        if pos_type == "buy":
            new_sl = current_price - trail_distance
            if new_sl > current_sl and current_sl > 0:
                return new_sl
        else:
            new_sl = current_price + trail_distance
            if new_sl < current_sl and current_sl > 0:
                return new_sl
        
        return None  # No change
    
    def should_close_partial(self, position: dict, current_price: float) -> Tuple[bool, float]:
        """
        Check if partial close should be executed (50% at TP1).
        
        Args:
            position: Position info dict
            current_price: Current price
            
        Returns:
            Tuple of (should_close, close_volume_pct)
        """
        pos_type = position.get("type", "buy")
        tp = position.get("take_profit", 0)
        
        if tp == 0:
            return False, 0.0
        
        # Check if price reached TP1
        if pos_type == "buy":
            if current_price >= tp:
                return True, self.partial_close_pct / 100.0
        else:
            if current_price <= tp:
                return True, self.partial_close_pct / 100.0
        
        return False, 0.0
    
    def get_max_hold_time(self) -> timedelta:
        """Get maximum hold time for mean-reversion trades."""
        return timedelta(minutes=self.max_hold_minutes)
    
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
