"""
Pydantic settings loader with YAML + .env support.
All tunable parameters are validated at startup.
"""

import os
import yaml
from pathlib import Path
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


# ── MT5 Connection ──────────────────────────────────────────────────────────

class MT5Config(BaseModel):
    terminal_path: Optional[str] = None
    login: int
    password: str
    server: str
    max_reconnect_attempts: int = 10
    reconnect_backoff_base_ms: int = 1000
    reconnect_backoff_max_ms: int = 30000
    heartbeat_interval_seconds: int = 30


# ── Symbols ─────────────────────────────────────────────────────────────────

class SymbolsConfig(BaseModel):
    primary: str = "XAUUSD"
    intermarket_dxy: str = "DXY"
    intermarket_us10y: str = "US10Y"
    watch_list: list[str] = ["XAUUSD", "DXY"]


# ── Time ────────────────────────────────────────────────────────────────────

class TimeConfig(BaseModel):
    timezone: str = "UTC"
    broker_gmt_offset: int = 0
    skip_rollover_minutes: int = 20


# ── Sessions ────────────────────────────────────────────────────────────────

class SessionWindow(BaseModel):
    start_utc: int
    end_utc: int


class SessionsConfig(BaseModel):
    asian: SessionWindow
    london: SessionWindow
    ny_overlap: SessionWindow
    late_ny: SessionWindow


# ── Regime ──────────────────────────────────────────────────────────────────

class RegimeConfig(BaseModel):
    atr_period: int = 20
    atr_rolling_median_period: int = 50
    atr_median_low_threshold: float = 1.15
    atr_median_high_threshold: float = 1.25
    adx_period: int = 14
    adx_low_threshold: float = 22
    adx_high_threshold: float = 25
    confirm_bars: int = 1


# ── Intermarket ─────────────────────────────────────────────────────────────

class IntermarketConfig(BaseModel):
    correlation_period: int = 20
    corr_threshold_long: float = -0.25
    corr_threshold_short: float = 0.25
    dxy_weakening_period: int = 5


# ── Strategies ──────────────────────────────────────────────────────────────

class MeanReversionConfig(BaseModel):
    bollinger_period: int = 20
    bollinger_deviation: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 30
    rsi_overbought: float = 70
    adx_max: float = 20
    atr_multiplier_sl: float = 1.8
    atr_multiplier_tp1: float = 1.5
    atr_multiplier_tp2: float = 2.5
    trail_start_atr: float = 0.5
    trail_atr: float = 0.8
    partial_close_pct: float = 50
    max_hold_minutes: int = 360
    max_concurrent: int = 1
    min_risk_reward: float = 1.8


class TrendFollowingConfig(BaseModel):
    ema_fast: int = 9
    ema_slow: int = 21
    adx_min: float = 25
    adx_must_rise: bool = True
    breakout_period: int = 20
    tick_volume_period: int = 10
    tick_volume_multiplier: float = 1.3
    atr_multiplier_sl: float = 2.0
    atr_multiplier_tp1: float = 2.0
    atr_multiplier_tp2: float = 3.5
    partial_close_tp1_pct: float = 40
    partial_close_tp2_pct: float = 30
    runner_pct: float = 30
    trail_activate_r_multiple: float = 1.5
    trail_atr: float = 1.5
    max_concurrent: int = 1
    min_risk_reward: float = 1.8


# ── Common Trading ──────────────────────────────────────────────────────────

class TradingConfig(BaseModel):
    max_daily_trades: int = 10
    max_concurrent_total: int = 2
    min_risk_reward: float = 1.8
    allow_hedging: bool = False
    cooldown_minutes: int = 15


# ── News ────────────────────────────────────────────────────────────────────

class NewsConfig(BaseModel):
    enabled: bool = True
    pre_pause_minutes: int = 30
    post_pause_minutes: int = 30
    close_before_minutes: int = 15
    calendar_source: Literal["trading_economics", "forex_factory", "finnhub"] = "trading_economics"
    calendar_api_key: Optional[str] = None
    daily_sync_utc: str = "00:30"
    filter_keywords: list[str] = [
        "FOMC", "NFP", "Nonfarm", "CPI", "Core CPI",
        "GDP", "Retail Sales", "PPI", "Fed",
        "Interest Rate", "Unemployment"
    ]


# ── Anomaly Detection ──────────────────────────────────────────────────────

class StatisticalAnomalyConfig(BaseModel):
    zscore_window: int = 200
    zscore_threshold: float = 3.5
    volatility_spike_mult: float = 3.0
    price_gap_atr_mult: float = 2.5
    spread_percentile: float = 99


class IsolationForestConfig(BaseModel):
    enabled: bool = True
    n_estimators: int = 100
    contamination: float = 0.01
    retrain_interval_bars: int = 500


class AutoencoderConfig(BaseModel):
    enabled: bool = True
    sequence_length: int = 50
    hidden_dim: int = 32
    latent_dim: int = 8
    error_threshold_percentile: float = 95
    retrain_interval_hours: int = 24
    model_format: Literal["onnx", "torchscript"] = "onnx"


class AnomalyConfig(BaseModel):
    statistical: StatisticalAnomalyConfig = StatisticalAnomalyConfig()
    isolation_forest: IsolationForestConfig = IsolationForestConfig()
    autoencoder: AutoencoderConfig = AutoencoderConfig()
    cooldown_minutes: int = 30
    max_cooldown_minutes: int = 45


# ── Execution ───────────────────────────────────────────────────────────────

class SmartExecutorConfig(BaseModel):
    enabled: bool = True
    max_slippage_pips: float = 2.0
    fill_timeout_seconds: int = 10
    order_split_min_lots: float = 0.02


class DQNConfig(BaseModel):
    enabled: bool = False
    state_features: list[str] = [
        "spread_vs_avg", "recent_volatility", "time_of_day",
        "regime", "distance_to_news", "recent_slippage"
    ]
    retrain_weekly: bool = True


class ExecutionConfig(BaseModel):
    smart_executor: SmartExecutorConfig = SmartExecutorConfig()
    dqn_model: DQNConfig = DQNConfig()


# ── Risk ────────────────────────────────────────────────────────────────────

class PositionSizingConfig(BaseModel):
    mean_reversion_risk_pct: float = 0.01
    trend_risk_pct: float = 0.006
    reduce_above_daily_dd_pct: float = 0.03
    min_lot: float = 0.01
    max_lot: float = 5.0


class CircuitBreakersConfig(BaseModel):
    hard_dd_pct: float = 0.06
    soft_dd_pct: float = 0.04
    daily_reset_utc: str = "00:00"


class CostModelConfig(BaseModel):
    min_edge_multiple: float = 2.5
    default_spread_pips: float = 0.30
    estimated_slippage_pips: float = 0.50
    commission_per_lot: float = 7.0
    overnight_swap_long: float = -3.0
    overnight_swap_short: float = -1.0


class RiskConfig(BaseModel):
    position_sizing: PositionSizingConfig = PositionSizingConfig()
    circuit_breakers: CircuitBreakersConfig = CircuitBreakersConfig()
    cost_model: CostModelConfig = CostModelConfig()


# ── Logging ─────────────────────────────────────────────────────────────────

class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "text"] = "json"
    log_dir: str = "./logs"
    audit_enabled: bool = True


class TelegramConfig(BaseModel):
    enabled: bool = True
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    alert_cooldown_seconds: int = 300


class WatchdogConfig(BaseModel):
    heartbeat_file: str = "/tmp/xau_heartbeat"
    heartbeat_interval_seconds: int = 25
    max_stale_seconds: int = 90


class MonitoringConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()
    watchdog: WatchdogConfig = WatchdogConfig()


# ── Database ────────────────────────────────────────────────────────────────

class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    database: str = "xau_trading"
    user: Optional[str] = None
    password: Optional[str] = None


# ── Performance Gates ──────────────────────────────────────────────────────

class PerformanceGatesConfig(BaseModel):
    min_win_rate: float = 0.58
    min_profit_factor: float = 1.8
    min_sharpe_ratio: float = 1.5
    max_drawdown: float = 0.08
    min_recovery_factor: float = 3.0


# ── Root Config ─────────────────────────────────────────────────────────────

class TradingSystemConfig(BaseModel):
    mt5: MT5Config
    symbols: SymbolsConfig = SymbolsConfig()
    time: TimeConfig = TimeConfig()
    sessions: SessionsConfig
    regime: RegimeConfig = RegimeConfig()
    intermarket: IntermarketConfig = IntermarketConfig()
    mean_reversion: MeanReversionConfig = MeanReversionConfig()
    trend_following: TrendFollowingConfig = TrendFollowingConfig()
    trading: TradingConfig = TradingConfig()
    news: NewsConfig = NewsConfig()
    anomaly: AnomalyConfig = AnomalyConfig()
    execution: ExecutionConfig = ExecutionConfig()
    risk: RiskConfig = RiskConfig()
    logging: LoggingConfig = LoggingConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    database: DatabaseConfig = DatabaseConfig()
    performance_gates: PerformanceGatesConfig = PerformanceGatesConfig()


# ── Loader ──────────────────────────────────────────────────────────────────

def _substitute_env(value: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variables."""
    import re
    pattern = re.compile(r'\$\{(\w+)(?::-([^}]*))?\}')
    
    def replacer(match):
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")
    
    return pattern.sub(replacer, value)


def _walk_and_substitute(obj):
    """Recursively substitute env vars in string values."""
    if isinstance(obj, dict):
        return {k: _walk_and_substitute(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_walk_and_substitute(v) for v in obj]
    elif isinstance(obj, str):
        return _substitute_env(obj)
    return obj


def load_config(config_path: str = None) -> TradingSystemConfig:
    """
    Load and validate trading system configuration from YAML.
    
    Args:
        config_path: Path to config.yaml. Defaults to config/config.yaml
                     relative to the project root.
    
    Returns:
        Validated TradingSystemConfig instance.
    """
    if config_path is None:
        # Find project root (where config/ dir lives)
        project_root = Path(__file__).parent.parent
        config_path = str(project_root / "config" / "config.yaml")
    
    with open(config_path, 'r') as f:
        raw = yaml.safe_load(f)
    
    # Substitute environment variables
    raw = _walk_and_substitute(raw)
    
    # Convert non-string login to int
    if 'mt5' in raw and 'login' in raw['mt5']:
        try:
            raw['mt5']['login'] = int(raw['mt5']['login'])
        except (ValueError, TypeError):
            pass
    
    return TradingSystemConfig(**raw)


# Module-level convenience
if __name__ == "__main__":
    cfg = load_config()
    print(cfg.model_dump_json(indent=2))
