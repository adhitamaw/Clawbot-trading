"""
Technical indicators for the XAU/USD trading system.

Computes all indicators needed by the strategy, regime detection,
intermarket filter, and anomaly detection modules.

Uses pandas_ta (primary) with numpy fallback where needed.
"""

from typing import Optional
import numpy as np
import pandas as pd

# Try TA-Lib first, fall back to pandas_ta
try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False

try:
    import pandas_ta as ta
    PANDAS_TA_AVAILABLE = True
except ImportError:
    PANDAS_TA_AVAILABLE = False

from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class FeatureEngine:
    """
    Technical indicator computation engine.
    
    Works with pandas DataFrames (OHLCV format) and produces
    indicator columns for strategy and ML consumption.
    """
    
    def __init__(self, df: pd.DataFrame = None):
        """
        Initialize with optional DataFrame.
        
        Args:
            df: OHLCV DataFrame with columns: time, open, high, low, close, tick_volume, spread
        """
        self.df = df.copy() if df is not None else pd.DataFrame()
    
    def set_data(self, df: pd.DataFrame) -> None:
        """Update the working DataFrame."""
        self.df = df.copy()
    
    # ── Bollinger Bands ──────────────────────────────────────────────────────
    
    def bollinger_bands(self, period: int = 20, deviation: float = 2.0,
                        column: str = 'close') -> tuple:
        """
        Compute Bollinger Bands.
        
        Args:
            period: Moving average period
            deviation: Standard deviation multiplier
            column: Price column to use
            
        Returns:
            Tuple of (middle, upper, lower) as Series
        """
        if self.df.empty or column not in self.df.columns:
            return (pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float))
        
        middle = self.df[column].rolling(window=period).mean()
        std = self.df[column].rolling(window=period).std()
        upper = middle + deviation * std
        lower = middle - deviation * std
        
        return middle, upper, lower
    
    def bb_squeeze(self, period: int = 20, deviation: float = 2.0) -> pd.Series:
        """Bollinger Band width (upper - lower) / middle for squeeze detection."""
        middle, upper, lower = self.bollinger_bands(period, deviation)
        bandwidth = (upper - lower) / middle.replace(0, np.nan)
        return bandwidth
    
    # ── RSI ──────────────────────────────────────────────────────────────────
    
    def rsi(self, period: int = 14, column: str = 'close') -> pd.Series:
        """
        Compute RSI (Relative Strength Index).
        
        Args:
            period: RSI period
            column: Price column
            
        Returns:
            RSI values as Series (0-100).
        """
        if self.df.empty:
            return pd.Series(dtype=float)
        
        if TALIB_AVAILABLE:
            result = talib.RSI(self.df[column].values, timeperiod=period)
            return pd.Series(result, index=self.df.index)
        
        prices = self.df[column]
        delta = prices.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100.0 - (100.0 / (1.0 + rs))
    
    # ── ATR ──────────────────────────────────────────────────────────────────
    
    def atr(self, period: int = 20) -> pd.Series:
        """
        Compute Average True Range.
        
        Args:
            period: ATR period
            
        Returns:
            ATR values as Series.
        """
        if self.df.empty:
            return pd.Series(dtype=float)
        
        if TALIB_AVAILABLE:
            result = talib.ATR(
                self.df['high'].values,
                self.df['low'].values,
                self.df['close'].values,
                timeperiod=period
            )
            return pd.Series(result, index=self.df.index)
        
        if PANDAS_TA_AVAILABLE:
            return ta.atr(self.df['high'], self.df['low'], self.df['close'], length=period)
        
        # Manual calculation
        high, low, close = self.df['high'], self.df['low'], self.df['close']
        prev_close = close.shift(1)
        
        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        return tr.ewm(alpha=1/period, adjust=False).mean()
    
    # ── ADX ──────────────────────────────────────────────────────────────────
    
    def adx(self, period: int = 14) -> tuple:
        """
        Compute ADX, +DI, -DI.
        
        Args:
            period: ADX period
            
        Returns:
            Tuple of (adx, plus_di, minus_di) as Series.
        """
        if self.df.empty:
            empty = pd.Series(dtype=float)
            return empty, empty, empty
        
        if TALIB_AVAILABLE:
            adx_val = talib.ADX(
                self.df['high'].values,
                self.df['low'].values,
                self.df['close'].values,
                timeperiod=period
            )
            plus_di = talib.PLUS_DI(
                self.df['high'].values,
                self.df['low'].values,
                self.df['close'].values,
                timeperiod=period
            )
            minus_di = talib.MINUS_DI(
                self.df['high'].values,
                self.df['low'].values,
                self.df['close'].values,
                timeperiod=period
            )
            return (
                pd.Series(adx_val, index=self.df.index),
                pd.Series(plus_di, index=self.df.index),
                pd.Series(minus_di, index=self.df.index)
            )
        
        if PANDAS_TA_AVAILABLE:
            adx_df = ta.adx(self.df['high'], self.df['low'], self.df['close'], length=period)
            if adx_df is not None:
                return (
                    adx_df[f'ADX_{period}'],
                    adx_df[f'DMP_{period}'],
                    adx_df[f'DMN_{period}']
                )
        
        # Simplified fallback — just return zeros
        logger.warning("adx_no_library_fallback")
        empty = pd.Series(0.0, index=self.df.index)
        return empty, empty, empty
    
    def adx_rising(self, period: int = 14, lookback: int = 3) -> pd.Series:
        """Check if ADX is rising over lookback bars."""
        adx_val, _, _ = self.adx(period)
        return adx_val.diff(lookback) > 0
    
    # ── EMA ──────────────────────────────────────────────────────────────────
    
    def ema(self, period: int, column: str = 'close') -> pd.Series:
        """
        Compute Exponential Moving Average.
        
        Args:
            period: EMA period
            column: Price column
            
        Returns:
            EMA values as Series.
        """
        if self.df.empty:
            return pd.Series(dtype=float)
        
        if TALIB_AVAILABLE:
            result = talib.EMA(self.df[column].values, timeperiod=period)
            return pd.Series(result, index=self.df.index)
        
        return self.df[column].ewm(span=period, adjust=False).mean()
    
    # ── Donchian Channel ─────────────────────────────────────────────────────
    
    def donchian(self, period: int = 20) -> tuple:
        """
        Compute Donchian Channel (highest high / lowest low).
        
        Args:
            period: Lookback period
            
        Returns:
            Tuple of (upper, lower) as Series.
        """
        if self.df.empty:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        
        upper = self.df['high'].rolling(window=period).max()
        lower = self.df['low'].rolling(window=period).min()
        
        return upper, lower
    
    # ── Swing High / Low ─────────────────────────────────────────────────────
    
    def swing_points(self, period: int = 5) -> tuple:
        """
        Find recent swing highs and lows.
        
        Args:
            period: Lookback window for swing detection
            
        Returns:
            Tuple of (swing_high, swing_low) as Series.
        """
        if self.df.empty:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        
        swing_high = self.df['high'].rolling(window=period * 2 + 1, center=True).apply(
            lambda x: x[period] if x[period] == max(x) else np.nan, raw=True
        )
        swing_low = self.df['low'].rolling(window=period * 2 + 1, center=True).apply(
            lambda x: x[period] if x[period] == min(x) else np.nan, raw=True
        )
        
        return swing_high, swing_low
    
    # ── Tick Volume ──────────────────────────────────────────────────────────
    
    def volume_avg(self, period: int = 10) -> pd.Series:
        """
        Compute average tick volume over period.
        
        Args:
            period: Averaging period
            
        Returns:
            Average volume as Series.
        """
        if self.df.empty or 'tick_volume' not in self.df.columns:
            return pd.Series(dtype=float)
        
        return self.df['tick_volume'].rolling(window=period).mean()
    
    def volume_ratio(self, period: int = 10) -> pd.Series:
        """
        Ratio of current volume to average volume.
        
        Args:
            period: Averaging period
            
        Returns:
            Volume ratio as Series (>1 = above average).
        """
        if self.df.empty or 'tick_volume' not in self.df.columns:
            return pd.Series(dtype=float)
        
        avg = self.volume_avg(period)
        return self.df['tick_volume'] / avg.replace(0, np.nan)
    
    # ── Returns & Volatility ─────────────────────────────────────────────────
    
    def log_returns(self, column: str = 'close') -> pd.Series:
        """Compute log returns."""
        if self.df.empty:
            return pd.Series(dtype=float)
        return np.log(self.df[column] / self.df[column].shift(1))
    
    def rolling_volatility(self, period: int = 20, column: str = 'close') -> pd.Series:
        """Rolling standard deviation of log returns."""
        returns = self.log_returns(column)
        return returns.rolling(window=period).std() * np.sqrt(period)
    
    # ── Z-Score ──────────────────────────────────────────────────────────────
    
    def zscore(self, period: int = 200, column: str = 'close') -> pd.Series:
        """
        Rolling Z-score of log returns.
        
        Args:
            period: Rolling window size
            column: Price column
            
        Returns:
            Z-score values as Series.
        """
        returns = self.log_returns(column)
        rolling_mean = returns.rolling(window=period).mean()
        rolling_std = returns.rolling(window=period).std()
        return (returns - rolling_mean) / rolling_std.replace(0, np.nan)
    
    def zscore_price(self, period: int = 20, column: str = 'close') -> pd.Series:
        """
        Z-score of price relative to its moving average.
        Used for mean-reversion signals.
        
        Args:
            period: MA period
            column: Price column
            
        Returns:
            Price Z-score as Series.
        """
        if self.df.empty:
            return pd.Series(dtype=float)
        
        mean = self.df[column].rolling(window=period).mean()
        std = self.df[column].rolling(window=period).std()
        return (self.df[column] - mean) / std.replace(0, np.nan)
    
    # ── Correlation ──────────────────────────────────────────────────────────
    
    @staticmethod
    def rolling_correlation(series_a: pd.Series, series_b: pd.Series,
                           period: int = 20) -> pd.Series:
        """
        Compute rolling Pearson correlation between two series.
        Used for intermarket correlation (XAU vs DXY).
        
        Args:
            series_a: First series (e.g., XAUUSD returns)
            series_b: Second series (e.g., DXY returns)
            period: Rolling window size
            
        Returns:
            Correlation values as Series (-1 to 1).
        """
        return series_a.rolling(window=period).corr(series_b)
    
    # ── Spread Statistics ────────────────────────────────────────────────────
    
    def spread_percentile(self, period: int = 100) -> pd.Series:
        """
        Rolling percentile of spread if available.
        
        Args:
            period: Rolling window
            
        Returns:
            Spread percentile as Series.
        """
        if 'spread' not in self.df.columns:
            return pd.Series(dtype=float)
        
        return self.df['spread'].rolling(window=period).apply(
            lambda x: (x.iloc[-1] > x).mean() * 100, raw=False
        )
    
    # ── Market Structure ─────────────────────────────────────────────────────
    
    def break_above(self, period: int = 20, column: str = 'close') -> pd.Series:
        """Price breaks above N-period high."""
        if self.df.empty:
            return pd.Series(dtype=bool)
        high_roll = self.df['high'].rolling(window=period).max().shift(1)
        return self.df[column] > high_roll
    
    def break_below(self, period: int = 20, column: str = 'close') -> pd.Series:
        """Price breaks below N-period low."""
        if self.df.empty:
            return pd.Series(dtype=bool)
        low_roll = self.df['low'].rolling(window=period).min().shift(1)
        return self.df[column] < low_roll
    
    # ── Composite Feature Vector ─────────────────────────────────────────────
    
    def compute_all(self, config: dict = None) -> pd.DataFrame:
        """
        Compute all indicators and return them as columns in the DataFrame.
        
        Args:
            config: Optional config dict with parameter overrides
            
        Returns:
            DataFrame with all indicators as additional columns.
        """
        if self.df.empty:
            return self.df
        
        df = self.df.copy()
        
        # Bollinger Bands
        _, df['bb_upper'], df['bb_lower'] = self.bollinger_bands(20, 2.0)
        
        # RSI
        df['rsi'] = self.rsi(14)
        
        # ATR
        df['atr'] = self.atr(20)
        
        # ADX
        df['adx'], df['plus_di'], df['minus_di'] = self.adx(14)
        
        # EMAs
        df['ema9'] = self.ema(9)
        df['ema21'] = self.ema(21)
        
        # Donchian
        df['donchian_upper'], df['donchian_lower'] = self.donchian(20)
        
        # Volume
        if 'tick_volume' in df.columns:
            df['volume_avg_10'] = self.volume_avg(10)
            df['volume_ratio'] = self.volume_ratio(10)
        
        # Returns & Vol
        df['log_return'] = self.log_returns()
        df['volatility_20'] = self.rolling_volatility(20)
        
        # Z-scores
        df['zscore_200'] = self.zscore(200)
        
        # Breakouts
        df['break_above_20'] = self.break_above(20)
        df['break_below_20'] = self.break_below(20)
        
        return df
    
    def get_latest(self, indicator: str, period: Optional[int] = None,
                   column: Optional[str] = None) -> float:
        """
        Get the most recent value of an indicator.
        
        Args:
            indicator: Indicator name
            period: Period parameter (if applicable)
            column: Column to use (if applicable)
            
        Returns:
            Latest indicator value or NaN.
        """
        if self.df.empty:
            return float('nan')
        
        p = period or 20
        
        methods = {
            'atr': lambda: self.atr(p).iloc[-1],
            'rsi': lambda: self.rsi(p, column or 'close').iloc[-1],
            'adx': lambda: self.adx(p)[0].iloc[-1],
            'ema': lambda: self.ema(p, column or 'close').iloc[-1],
            'zscore': lambda: self.zscore(p, column or 'close').iloc[-1],
            'volatility': lambda: self.rolling_volatility(p, column or 'close').iloc[-1],
        }
        
        if indicator in methods:
            try:
                return float(methods[indicator]())
            except (IndexError, ValueError):
                return float('nan')
        
        return float('nan')
