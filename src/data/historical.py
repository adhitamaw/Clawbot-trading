"""
Historical data management — fetch, cache, and manage bar/tick history.

Features:
- Pull historical bars from MT5
- Local caching (parquet) for fast reload
- Multi-symbol historical data
- Time range queries
- Data validation
"""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from src.mt5_bridge import MT5Bridge
from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class HistoricalData:
    """
    Historical data manager with local caching.
    
    Fetches from MT5 on first request, caches locally as parquet,
    and merges new data on subsequent requests.
    """
    
    def __init__(self, bridge: MT5Bridge, cache_dir: str = "./data/historical",
                 symbol: str = "XAUUSD"):
        self.bridge = bridge
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.symbol = symbol
        
        # In-memory cache
        self._cache: dict[str, pd.DataFrame] = {}
    
    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        """Get cache file path for a symbol/timeframe combination."""
        return self.cache_dir / f"{symbol}_{timeframe}.parquet"
    
    def get_bars(self, symbol: str = None, timeframe: str = "M5",
                 count: int = 500, use_cache: bool = True) -> pd.DataFrame:
        """
        Fetch historical bars for a symbol and timeframe.
        
        Uses local cache if available and merges with fresh data from MT5.
        
        Args:
            symbol: Instrument name (default: XAUUSD)
            timeframe: MT5 timeframe (M1, M5, M15, etc.)
            count: Number of bars to return
            use_cache: Whether to use and update local cache
            
        Returns:
            DataFrame with columns: time, open, high, low, close, tick_volume, spread
        """
        symbol = symbol or self.symbol
        cache_key = f"{symbol}_{timeframe}"
        
        # Try cache first
        if use_cache and cache_key in self._cache:
            cached = self._cache[cache_key]
            if len(cached) >= count:
                return cached.tail(count)
        
        # Fetch from MT5
        df = self.bridge.get_bars(symbol, timeframe, count=count)
        
        if df.empty:
            logger.warning("historical_bars_empty", symbol=symbol, timeframe=timeframe)
            return df
        
        # Clean up
        df = df.rename(columns={
            'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close',
            'tick_volume': 'tick_volume', 'spread': 'spread'
        })
        
        # Ensure required columns exist
        for col in ['open', 'high', 'low', 'close']:
            if col not in df.columns:
                logger.error("historical_missing_column", column=col)
                return pd.DataFrame()
        
        df = df.sort_values('time').reset_index(drop=True)
        
        # Cache
        if use_cache:
            self._cache[cache_key] = df
        
        return df
    
    def get_bars_range(self, symbol: str = None, timeframe: str = "M5",
                       start: datetime = None, end: datetime = None) -> pd.DataFrame:
        """
        Fetch bars within a specific date range.
        
        Args:
            symbol: Instrument name
            timeframe: MT5 timeframe
            start: Start datetime (UTC)
            end: End datetime (UTC)
            
        Returns:
            DataFrame of bars in the specified range.
        """
        symbol = symbol or self.symbol
        
        if start is None:
            start = datetime.now(timezone.utc) - timedelta(days=30)
        if end is None:
            end = datetime.now(timezone.utc)
        
        # Calculate how many bars we need
        from src.mt5_bridge.bridge import MT5Bridge as Bridge
        tf_minutes = Bridge.timeframe_minutes(timeframe)
        total_minutes = (end - start).total_seconds() / 60
        total_bars = int(total_minutes / tf_minutes) + 100  # buffer
        
        # Cap to reasonable limit
        total_bars = min(total_bars, 100000)
        
        df = self.get_bars(symbol, timeframe, count=total_bars, use_cache=False)
        
        if df.empty:
            return df
        
        # Filter to date range
        df['time'] = pd.to_datetime(df['time'])
        mask = (df['time'] >= start) & (df['time'] <= end)
        return df[mask].reset_index(drop=True)
    
    def get_ticks(self, symbol: str = None, from_time: datetime = None,
                  to_time: datetime = None, count: int = 5000) -> pd.DataFrame:
        """
        Fetch historical tick data.
        
        Args:
            symbol: Instrument name
            from_time: Start time (UTC)
            to_time: End time (UTC)
            count: Maximum ticks to fetch
            
        Returns:
            DataFrame of ticks.
        """
        symbol = symbol or self.symbol
        return self.bridge.get_ticks_range(symbol, from_time, to_time, count)
    
    def get_multi_timeframe(self, symbol: str = None,
                            timeframes: list[str] = None) -> dict[str, pd.DataFrame]:
        """
        Fetch bars for multiple timeframes simultaneously.
        
        Args:
            symbol: Instrument name
            timeframes: List of timeframes (e.g., ["M1", "M5", "M15"])
            
        Returns:
            Dict mapping timeframe → DataFrame
        """
        symbol = symbol or self.symbol
        timeframes = timeframes or ["M1", "M5", "M15"]
        
        result = {}
        for tf in timeframes:
            df = self.get_bars(symbol, tf)
            if not df.empty:
                result[tf] = df
        
        return result
    
    def get_intermarket_data(self, symbols: list[str] = None,
                             timeframe: str = "M5",
                             count: int = 200) -> dict[str, pd.DataFrame]:
        """
        Fetch data for intermarket symbols.
        
        Args:
            symbols: List of symbols (e.g., ["DXY", "US10Y"])
            timeframe: MT5 timeframe
            count: Number of bars
            
        Returns:
            Dict mapping symbol → DataFrame
        """
        symbols = symbols or ["DXY", "US10Y"]
        
        result = {}
        for sym in symbols:
            self.bridge.ensure_symbols([sym])
            df = self.get_bars(sym, timeframe, count=count, use_cache=False)
            if not df.empty:
                result[sym] = df
        
        return result
    
    def save_cache(self) -> None:
        """Persist in-memory cache to disk as parquet files."""
        for cache_key, df in self._cache.items():
            symbol, timeframe = cache_key.rsplit('_', 1)
            path = self._cache_path(symbol, timeframe)
            try:
                df.to_parquet(path, index=False)
                logger.debug("cache_saved", path=str(path), rows=len(df))
            except Exception as e:
                logger.warning("cache_save_failed", path=str(path), error=str(e))
    
    def load_cache(self) -> int:
        """
        Load cached data from disk.
        
        Returns:
            Number of cached files loaded.
        """
        count = 0
        for path in self.cache_dir.glob("*.parquet"):
            try:
                df = pd.read_parquet(path)
                if 'time' in df.columns:
                    df['time'] = pd.to_datetime(df['time'])
                
                # Extract symbol and timeframe from filename
                stem = path.stem  # e.g., "XAUUSD_M5"
                self._cache[stem] = df
                count += 1
            except Exception as e:
                logger.warning("cache_load_failed", path=str(path), error=str(e))
        
        logger.info("cache_loaded", files=count)
        return count
    
    def clear_cache(self, symbol: str = None, timeframe: str = None) -> int:
        """
        Clear cache entries. If no filter, clears all.
        
        Returns:
            Number of entries cleared.
        """
        if symbol and timeframe:
            cache_key = f"{symbol}_{timeframe}"
            if cache_key in self._cache:
                del self._cache[cache_key]
                return 1
            return 0
        
        count = len(self._cache)
        self._cache.clear()
        return count
