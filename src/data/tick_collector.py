"""
Real-time tick data collector with buffering and resampling.

Features:
- Async tick streaming via MT5
- Rolling buffer for recent ticks and bars
- Resampling ticks → M1/M5/M15 bars
- Spread tracking (percentile statistics)
- Volatility estimation from tick data
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable

import numpy as np
import pandas as pd

from src.mt5_bridge import MT5Bridge, TickData
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


@dataclass
class TickBuffer:
    """Rolling tick buffer with statistical properties."""
    symbol: str
    max_ticks: int = 5000
    ticks: deque = field(default_factory=deque)
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    spreads: list = field(default_factory=list)
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def add(self, tick: TickData) -> None:
        """Add tick to rolling buffer."""
        self.ticks.append(tick)
        self.bids.append(tick.bid)
        self.asks.append(tick.ask)
        self.spreads.append(tick.spread)
        self.last_update = datetime.now(timezone.utc)
        
        # Trim to max size
        while len(self.ticks) > self.max_ticks:
            self.ticks.popleft()
            self.bids.pop(0)
            self.asks.pop(0)
            self.spreads.pop(0)
    
    @property
    def mid_prices(self) -> list:
        return [(b + a) / 2 for b, a in zip(self.bids, self.asks)]
    
    def get_recent_ticks(self, n: int = 100) -> list[TickData]:
        """Get the n most recent ticks."""
        return list(self.ticks)[-n:]
    
    def get_spread_percentile(self, percentile: float = 99.0) -> float:
        """Get the Nth percentile of recent spreads."""
        if len(self.spreads) < 20:
            return float('inf')
        return float(np.percentile(self.spreads, percentile))
    
    def get_volatility(self, window: int = 100) -> float:
        """Estimate realized volatility from recent mid-price log returns."""
        mids = self.mid_prices
        if len(mids) < window + 2:
            return 0.0
        recent = np.array(mids[-window:])
        log_returns = np.diff(np.log(recent))
        return float(np.std(log_returns) * np.sqrt(len(log_returns)))
    
    def price_gap(self) -> float:
        """Get the latest price gap (bid-ask spread in absolute terms)."""
        if len(self.ticks) < 2:
            return 0.0
        current_mid = (self.ticks[-1].bid + self.ticks[-1].ask) / 2
        previous_mid = (self.ticks[-2].bid + self.ticks[-2].ask) / 2
        return abs(current_mid - previous_mid)


class TickCollector:
    """
    Async tick data collector.
    
    Streams tick data from MT5, maintains rolling buffer,
    resamples into bars, and provides feature snapshots.
    """
    
    def __init__(self, bridge: MT5Bridge, symbol: str = "XAUUSD",
                 buffer_size: int = 5000, poll_interval_ms: int = 50):
        self.bridge = bridge
        self.symbol = symbol
        self.poll_interval_ms = poll_interval_ms
        
        self.buffer = TickBuffer(symbol=symbol, max_ticks=buffer_size)
        
        # Bar state for resampling
        self._current_bar: Optional[dict] = None
        self._bar_buffers: dict[str, pd.DataFrame] = {}
        self._new_bar_callbacks: list[Callable] = []
        
        # State
        self._running = False
        self._last_tick_time: datetime = None
        self._tick_count = 0
        self._stats = {"ticks_collected": 0, "bars_formed": 0, "gaps": 0}
    
    @property
    def current_spread(self) -> float:
        return self.buffer.spreads[-1] if self.buffer.spreads else 0.0
    
    @property
    def spread_99th(self) -> float:
        return self.buffer.get_spread_percentile(99)
    
    @property
    def recent_volatility(self) -> float:
        return self.buffer.get_volatility(100)
    
    # ── Async Tick Loop ──────────────────────────────────────────────────────
    
    async def start(self) -> asyncio.Task:
        """Start the async tick collection loop."""
        self._running = True
        logger.info("tick_collector_starting", symbol=self.symbol)
        return asyncio.create_task(self._tick_loop())
    
    async def stop(self) -> None:
        """Stop the tick collection loop."""
        self._running = False
        logger.info("tick_collector_stopped", ticks_collected=self._tick_count)
    
    async def _tick_loop(self):
        """Main tick collection loop."""
        while self._running:
            try:
                tick = self.bridge.get_tick(self.symbol)
                
                if tick is None:
                    await asyncio.sleep(self.poll_interval_ms / 1000.0)
                    continue
                
                # Skip duplicate ticks (same timestamp)
                if self._last_tick_time and tick.time == self._last_tick_time:
                    await asyncio.sleep(0.001)  # 1ms sleep for tight loop
                    continue
                
                self._last_tick_time = tick.time
                self.buffer.add(tick)
                self._tick_count += 1
                self._stats["ticks_collected"] += 1
                
                # Check for price gap (anomaly signal)
                gap = self.buffer.price_gap()
                if gap > 0:
                    atr = self._get_current_atr()
                    if atr > 0 and gap > atr * 2.5:
                        self._stats["gaps"] += 1
                
                # Resample into bar (check if we crossed bar boundary)
                self._update_bar(tick)
                
                # Yield control
                await asyncio.sleep(0)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("tick_loop_error", error=str(e))
                await asyncio.sleep(1)
    
    def _get_current_atr(self) -> float:
        """Get current ATR estimate from recent bars."""
        # Simplified: use spread as proxy if no bars yet
        return self.current_spread * 3  # rough proxy
    
    # ── Bar Resampling ────────────────────────────────────────────────────────
    
    def _update_bar(self, tick: TickData) -> None:
        """
        Resample ticks into bars by tracking current bar state.
        Triggers callbacks when a bar completes.
        """
        # Bar boundary detection using tick timestamp
        # For M5, bar closes at :00, :05, :10, etc.
        bar_time = tick.time.replace(second=0, microsecond=0)
        
        if self._current_bar is None:
            mid = (tick.bid + tick.ask) / 2
            self._current_bar = {
                'time': bar_time,
                'open': mid,
                'high': mid,
                'low': mid,
                'close': mid,
                'tick_volume': tick.volume,
                'spread': tick.spread,
            }
            return
        
        # Check if new bar
        current_bar_time = self._current_bar['time']
        if bar_time > current_bar_time:
            # Bar closed — finalize and emit
            self._emit_bar(self._current_bar)
            
            # Start new bar
            mid = (tick.bid + tick.ask) / 2
            self._current_bar = {
                'time': bar_time,
                'open': mid,
                'high': mid,
                'low': mid,
                'close': mid,
                'tick_volume': tick.volume,
                'spread': tick.spread,
            }
            self._stats["bars_formed"] += 1
        else:
            # Same bar — update OHLC
            mid = (tick.bid + tick.ask) / 2
            self._current_bar['high'] = max(self._current_bar['high'], mid)
            self._current_bar['low'] = min(self._current_bar['low'], mid)
            self._current_bar['close'] = mid
            self._current_bar['tick_volume'] += tick.volume
            self._current_bar['spread'] = tick.spread
    
    def _emit_bar(self, bar: dict) -> None:
        """Emit a completed bar to all registered callbacks."""
        for callback in self._new_bar_callbacks:
            try:
                callback(bar)
            except Exception as e:
                logger.error("bar_callback_error", error=str(e))
    
    def on_new_bar(self, callback: Callable) -> None:
        """Register callback for new bar events."""
        self._new_bar_callbacks.append(callback)
    
    # ── Bar History ───────────────────────────────────────────────────────────
    
    def get_bar_buffer(self, count: int = 100) -> pd.DataFrame:
        """
        Fetch recent bars from MT5 and return as DataFrame.
        Combines MT5 historical bars with our live-ticked current bar.
        """
        df = self.bridge.get_bars(self.symbol, "M5", count=count)
        
        # Append current incomplete bar if we have one
        if self._current_bar is not None and (df.empty or 
            pd.Timestamp(self._current_bar['time']) > df['time'].max()):
            current_row = pd.DataFrame([self._current_bar])
            current_row['time'] = pd.to_datetime(current_row['time'])
            df = pd.concat([df, current_row], ignore_index=True).tail(count)
        
        return df
    
    def get_ticks_dataframe(self, n: int = 500) -> pd.DataFrame:
        """Get recent ticks as DataFrame for feature engineering."""
        ticks = self.buffer.get_recent_ticks(n)
        if not ticks:
            return pd.DataFrame()
        
        return pd.DataFrame([{
            'time': t.time,
            'bid': t.bid,
            'ask': t.ask,
            'mid': (t.bid + t.ask) / 2,
            'spread': t.spread,
            'volume': t.volume,
        } for t in ticks])
    
    # ── Feature Snapshot ──────────────────────────────────────────────────────
    
    def get_feature_snapshot(self) -> dict:
        """
        Get current feature snapshot for ML models and logging.
        """
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'symbol': self.symbol,
            'bid': self.buffer.bids[-1] if self.buffer.bids else None,
            'ask': self.buffer.asks[-1] if self.buffer.asks else None,
            'spread': self.current_spread,
            'spread_99th': self.spread_99th if len(self.buffer.spreads) > 20 else None,
            'volatility': self.recent_volatility,
            'price_gap': self.buffer.price_gap(),
            'tick_count': self._tick_count,
            'ticks_in_buffer': len(self.buffer.ticks),
        }
    
    def get_stats(self) -> dict:
        """Get collector statistics."""
        return {
            **self._stats,
            'buffer_ticks': len(self.buffer.ticks),
            'current_spread': round(self.current_spread, 6),
            'is_running': self._running,
        }
