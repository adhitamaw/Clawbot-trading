"""
Main orchestrator for the XAU/USD Institutional Trading System.

Async event loop that coordinates:
- MT5 connection and heartbeat
- Real-time data pipeline
- Regime detection
- Intermarket filtering
- News filtering
- Anomaly detection
- Signal generation (mean-reversion / trend-following)
- Risk management & position sizing
- Smart execution
- Logging, monitoring, and Telegram alerts
- Graceful shutdown

Start: python -m src.main
"""

import asyncio
import signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.config import load_config, TradingSystemConfig
from src.logging.structured_logger import setup_logging, get_logger, AuditLogger
from src.mt5_bridge import MT5Bridge
from src.data.tick_collector import TickCollector
from src.data.historical import HistoricalData
from src.features.technical import FeatureEngine
from src.ml.anomaly import AnomalyDetector
from src.ml.model_manager import ModelManager
logger = None


class TradingSystem:
    """
    Main trading system orchestrator.
    
    Coordinates all subsystems in a unified async event loop.
    """
    
    def __init__(self, config: TradingSystemConfig):
        self.config = config
        self.logger = get_logger("trading_system")
        self.audit = AuditLogger(config.logging.log_dir)
        
        # Core components (initialized later)
        self.bridge: MT5Bridge = None
        self.tick_collector: TickCollector = None
        self.historical: HistoricalData = None
        self.features: FeatureEngine = None
        self.anomaly_detector: AnomalyDetector = None
        self.model_manager: ModelManager = None
        self.regime_detector = None
        self.intermarket_filter = None
        self.news_filter = None
        self.risk_manager = None
        self.strategy_mr = None
        self.strategy_tf = None
        self.executor = None
        self.notifier = None
        
        # State
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        
        # Daily stats
        self.daily_start_equity = 0.0
        self.peak_equity = 0.0
        self.peak_equity_time = None
        self.daily_trade_count = 0
        self.todays_date = datetime.now(timezone.utc).date()
        
        # Anomaly cooldown
        self.anomaly_cooldown_until: datetime = None
    
    # ── Lifecycle ─────────────────────────────────────────────────────────────
    
    async def start(self):
        """Initialize all subsystems and start the main loop."""
        self.logger.info("system_starting", version="1.0.0")
        
        # 1. Initialize MT5 Bridge
        self.bridge = MT5Bridge(
            login=self.config.mt5.login,
            password=self.config.mt5.password,
            server=self.config.mt5.server,
            max_reconnect_attempts=self.config.mt5.max_reconnect_attempts,
            reconnect_backoff_base_ms=self.config.mt5.reconnect_backoff_base_ms,
            reconnect_backoff_max_ms=self.config.mt5.reconnect_backoff_max_ms,
            heartbeat_interval_seconds=self.config.mt5.heartbeat_interval_seconds,
        )
        
        if not self.bridge.initialize():
            self.logger.critical("mt5_init_failed_critical")
            return False
        
        # Ensure symbols in Market Watch
        self.bridge.ensure_symbols(self.config.symbols.watch_list)
        
        # 2. Initialize Data Pipeline
        self.tick_collector = TickCollector(
            bridge=self.bridge,
            symbol=self.config.symbols.primary,
        )
        
        # Start async tick collection
        self._tasks.append(await self.tick_collector.start())
        self.logger.info("tick_collector_started")
        
        # 3. Initialize Historical Data
        self.historical = HistoricalData(bridge=self.bridge)
        self.historical.load_cache()
        
        # 4. Initialize Feature Engine
        self.features = FeatureEngine()
        
        # 5. Initialize Anomaly Detection
        self.anomaly_detector = AnomalyDetector(config=self.config.anomaly)
        
        # 6. Initialize Model Manager
        self.model_manager = ModelManager()
        # Load trained models if they exist
        self.anomaly_detector.load_models()
        
        # 7. Start heartbeat
        await self.bridge.start_heartbeat()
        
        # 3. Record daily start
        account = self.bridge.get_account_info()
        if account:
            self.daily_start_equity = account.equity
            self.peak_equity = account.equity
            self.peak_equity_time = datetime.now(timezone.utc)
        
        # 4. Register signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle_signal)
        
        self._running = True
        self.logger.info(
            "system_started",
            daily_start_equity=self.daily_start_equity,
            symbol=self.config.symbols.primary,
        )
        
        # 5. Start main loop
        await self._main_loop()
        
        return True
    
    async def shutdown(self):
        """Graceful shutdown sequence."""
        self.logger.info("system_shutdown_starting")
        self._running = False
        self._shutdown_event.set()
        
        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # Stop tick collector
        if self.tick_collector:
            await self.tick_collector.stop()
        
        # Wait for tasks to finish
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        # Close all positions? (configurable — off by default for safety)
        # self.bridge.close_all_positions()
        
        # Disconnect
        self.bridge.shutdown()
        
        self.logger.info("system_shutdown_complete")
    
    def _handle_signal(self, signum, frame):
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        self.logger.info("signal_received", signal=signum)
        asyncio.create_task(self.shutdown())
    
    # ── Main Loop ─────────────────────────────────────────────────────────────
    
    async def _main_loop(self):
        """Primary event loop — runs every bar (M5) or tick depending on mode."""
        self.logger.info("main_loop_starting")
        
        while self._running:
            try:
                # Run one cycle
                await self._trading_cycle()
                
                # Wait for next bar or tick
                # For M5 primary timeframe, check every 10 seconds
                # (we could also sync to actual bar close time)
                await asyncio.sleep(10)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("main_loop_error", error=str(e), exc_info=True)
                await asyncio.sleep(5)  # Brief pause on error
    
    async def _trading_cycle(self):
        """Single trading cycle — check all conditions and act."""
        
        # ── 1. Daily Reset Check ──
        self._check_daily_reset()
        
        # ── 2. Circuit Breaker Check ──
        if not self._check_circuit_breaker():
            return
        
        # ── 3. News Pause Check ──
        if self._is_news_pause():
            self.logger.debug("news_pause_active")
            return
        
        # ── 4. Anomaly Check ──
        if self.anomaly_detector and self.anomaly_detector.is_cooldown_active():
            return
        
        # Run anomaly detection on current data
        anomaly_result = self._run_anomaly_check()
        if anomaly_result and anomaly_result.is_anomaly:
            self.logger.warning(
                "anomaly_blocking",
                reason=anomaly_result.trigger_reason,
                confidence=f"{anomaly_result.confidence:.2f}"
            )
            return
        
        # ── 5. Get Current Session & Regime ──
        session = self._get_current_session()
        if session == "late_ny":
            # Reduced activity in late NY
            if self.daily_trade_count >= self.config.trading.max_daily_trades:
                return
        
        # ── 6. Check max daily trades ──
        if self.daily_trade_count >= self.config.trading.max_daily_trades:
            return
        
        # ── 7. Check max concurrent positions ──
        open_count = self.bridge.count_positions(magic=20260516)
        if open_count >= self.config.trading.max_concurrent_total:
            return
        
        # ── 8. Get Regime (placeholder — full implementation in Phase 3) ──
        regime = self._get_regime(session)
        
        # ── 9. Generate Signals (placeholder — full implementation Phase 3-4) ──
        signal = await self._generate_signal(session, regime)
        
        # ── 10. Execute if signal valid ──
        if signal:
            await self._execute_signal(signal)
        
        # ── 11. Manage existing positions (trailing stops, partial closes) ──
        await self._manage_positions()
    
    # ── Helpers ───────────────────────────────────────────────────────────────
    
    def _check_daily_reset(self):
        """Reset daily stats at 00:00 UTC."""
        today = datetime.now(timezone.utc).date()
        if today > self.todays_date:
            self.logger.info("daily_reset", previous_date=str(self.todays_date))
            self.todays_date = today
            self.daily_trade_count = 0
            
            account = self.bridge.get_account_info()
            if account:
                self.daily_start_equity = account.equity
                self.peak_equity = account.equity
                self.peak_equity_time = datetime.now(timezone.utc)
    
    def _check_circuit_breaker(self) -> bool:
        """Check hard/soft circuit breakers. Returns False if trading blocked."""
        account = self.bridge.get_account_info()
        if account is None:
            return False
        
        # Update peak equity
        if account.equity > self.peak_equity:
            self.peak_equity = account.equity
            self.peak_equity_time = datetime.now(timezone.utc)
        
        if self.peak_equity <= 0:
            return True
        
        daily_dd = (self.peak_equity - account.equity) / self.peak_equity
        
        # Hard breaker
        if daily_dd >= self.config.risk.circuit_breakers.hard_dd_pct:
            self.logger.critical(
                "hard_circuit_breaker",
                dd_pct=daily_dd * 100,
                equity=account.equity,
                peak=self.peak_equity,
            )
            self.bridge.close_all_positions()
            # Notify Telegram (placeholder)
            return False
        
        # Soft breaker
        if daily_dd >= self.config.risk.circuit_breakers.soft_dd_pct:
            self.logger.warning(
                "soft_circuit_breaker",
                dd_pct=daily_dd * 100,
            )
            # Trading continues but position sizing reduces (handled in risk manager)
        
        return True
    
    def _is_news_pause(self) -> bool:
        """Check if we're in a news pause window."""
        # Placeholder — full implementation in Phase 4
        return False
    
    def _run_anomaly_check(self):
        """Run full multi-layer anomaly detection on current data."""
        if self.anomaly_detector is None or self.bridge is None:
            return None
        
        try:
            # Get required data points
            tick = self.bridge.get_tick(self.config.symbols.primary)
            if tick is None:
                return None
            
            mid_price = (tick.bid + tick.ask) / 2
            spread = tick.spread
            
            # Get latest bars for feature computation
            bars_df = self.bridge.get_bars(self.config.symbols.primary, "M5", count=100)
            
            if not bars_df.empty:
                # Compute features
                self.features.set_data(bars_df)
                
                atr_series = self.features.atr(20)
                atr = float(atr_series.iloc[-1]) if not atr_series.empty else spread
                
                # Build feature dict for ML layers
                features = {
                    'log_return': float(self.features.log_returns().iloc[-1]) if len(bars_df) > 1 else 0.0,
                    'volatility': float(self.features.rolling_volatility(20).iloc[-1]) if len(bars_df) > 20 else 0.0,
                    'spread': spread,
                    'tick_volume': float(bars_df['tick_volume'].iloc[-1]) if 'tick_volume' in bars_df.columns else 0.0,
                    'rsi': float(self.features.rsi(14).iloc[-1]) if len(bars_df) > 14 else 50.0,
                    'adx': float(self.features.adx(14)[0].iloc[-1]) if len(bars_df) > 14 else 25.0,
                    'zscore': float(self.features.zscore(200).iloc[-1]) if len(bars_df) > 200 else 0.0,
                    'volume_ratio': float(self.features.volume_ratio(10).iloc[-1]) if 'tick_volume' in bars_df.columns else 1.0,
                }
            else:
                atr = spread * 3
                features = {}
            
            # Run anomaly check
            return self.anomaly_detector.check(
                price=mid_price,
                spread=spread,
                atr=atr,
                features=features,
            )
            
        except Exception as e:
            self.logger.error("anomaly_check_error", error=str(e))
            return None
    
    def _get_current_session(self) -> str:
        """Determine current trading session based on UTC hour."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        
        if self.config.sessions.asian.start_utc <= hour < self.config.sessions.asian.end_utc:
            return "asian"
        elif self.config.sessions.london.start_utc <= hour < self.config.sessions.london.end_utc:
            return "london"
        elif self.config.sessions.ny_overlap.start_utc <= hour < self.config.sessions.ny_overlap.end_utc:
            return "ny_overlap"
        else:
            return "late_ny"
    
    def _get_regime(self, session: str) -> str:
        """
        Determine trading regime (mean_reversion / trend_following / neutral).
        
        This is a simplified version — full hybrid regime detection 
        with ATR+ADX confirmation is in src/regime/detector.py (Phase 3).
        """
        # For now, session-based only
        if session == "asian":
            return "mean_reversion"
        elif session in ("london", "ny_overlap"):
            return "trend_following"
        return "neutral"
    
    async def _generate_signal(self, session: str, regime: str) -> dict:
        """
        Generate trading signals based on strategy rules.
        Placeholder — full implementation in Phase 3-4.
        """
        # Will call:
        # - regime/detector.py for dynamic confirmation
        # - features/technical.py for indicator values
        # - strategy/mean_reversion.py or strategy/trend_following.py
        # - intermarket filter
        # - anomaly detector
        # - cost model gate
        return None  # No signals in stub
    
    async def _execute_signal(self, signal: dict):
        """
        Execute a validated trading signal.
        Placeholder — full implementation with smart executor in Phase 5-6.
        """
        self.logger.info("signal_execute", signal=signal)
        self.daily_trade_count += 1
    
    async def _manage_positions(self):
        """Manage open positions: trailing stops, partial closes."""
        positions = self.bridge.get_open_positions(magic=20260516)
        
        for pos in positions:
            # Trailing stop logic will be in strategy modules
            # For now, just log position status
            pass
    
    # ── Monitoring ────────────────────────────────────────────────────────────
    
    def get_status(self) -> dict:
        """Get current system status for monitoring."""
        account = self.bridge.get_account_info()
        positions = self.bridge.get_open_positions(magic=20260516)
        
        daily_dd = 0.0
        if account and self.peak_equity > 0:
            daily_dd = (self.peak_equity - account.equity) / self.peak_equity * 100
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connected": self.bridge.is_connected() if self.bridge else False,
            "running": self._running,
            "session": self._get_current_session(),
            "regime": self._get_regime(self._get_current_session()),
            "daily_trades": self.daily_trade_count,
            "daily_dd_pct": round(daily_dd, 2),
            "open_positions": len(positions),
            "account": {
                "balance": account.balance if account else 0,
                "equity": account.equity if account else 0,
                "margin_level": account.margin_level if account else 0,
            } if account else None,
        }


# ── Entry Point ──────────────────────────────────────────────────────────────

async def main():
    """Application entry point."""
    # Setup logging first
    config = None
    try:
        config = load_config()
    except Exception as e:
        print(f"Failed to load config: {e}")
        sys.exit(1)
    
    # Setup structured logging
    global logger
    setup_logging(
        log_level=config.logging.level,
        log_format=config.logging.format,
        log_dir=config.logging.log_dir,
        audit_enabled=config.logging.audit_enabled,
    )
    logger = get_logger("main")
    
    logger.info("application_start", config_version="1.0")
    
    # Create and start the trading system
    system = TradingSystem(config)
    
    try:
        await system.start()
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    except Exception as e:
        logger.critical("fatal_error", error=str(e), exc_info=True)
    finally:
        await system.shutdown()
    
    logger.info("application_exit")


if __name__ == "__main__":
    asyncio.run(main())
