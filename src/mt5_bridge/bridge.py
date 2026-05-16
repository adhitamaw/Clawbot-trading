"""
MetaTrader 5 Bridge — Core communication layer between Python and MT5 terminal.

Provides:
- Connection lifecycle (initialize, login, heartbeat, reconnect, shutdown)
- Real-time tick/bar data retrieval
- Order placement, modification, and closure
- Position and account information
- Symbol properties (spread, margin, contract size, etc.)

Designed for reliability with exponential-backoff reconnection,
structured logging, and graceful shutdown.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


# ── Data Types ──────────────────────────────────────────────────────────────

class OrderType(str, Enum):
    MARKET_BUY = "market_buy"
    MARKET_SELL = "market_sell"
    BUY_LIMIT = "buy_limit"
    SELL_LIMIT = "sell_limit"
    BUY_STOP = "buy_stop"
    SELL_STOP = "sell_stop"


class OrderFilling(str, Enum):
    FOK = "fok"      # Fill or Kill
    IOC = "ioc"      # Immediate or Cancel
    RETURN = "return"  # Return (partial fills allowed)


@dataclass
class TickData:
    """Single tick data point."""
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    time: datetime
    spread: float = 0.0
    
    def __post_init__(self):
        self.spread = self.ask - self.bid


@dataclass
class BarData:
    """OHLCV bar data."""
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    tick_volume: float
    spread: float
    time: datetime


@dataclass
class OrderRequest:
    """Order placement request."""
    symbol: str
    order_type: OrderType
    volume: float
    price: float = 0.0           # 0 = market
    stop_loss: float = 0.0
    take_profit: float = 0.0
    deviation: int = 20           # max slippage in points
    filling: OrderFilling = OrderFilling.IOC
    comment: str = "XAU_System"
    magic: int = 20260516         # expert advisor magic number


@dataclass
class OrderResult:
    """Order placement/modification result."""
    success: bool
    order_id: Optional[int] = None
    volume: float = 0.0
    price: float = 0.0
    comment: str = ""
    error_code: int = 0
    error_desc: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PositionInfo:
    """Open position information."""
    ticket: int
    symbol: str
    type: str                      # "buy" or "sell"
    volume: float
    open_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    profit: float
    swap: float
    commission: float
    open_time: datetime
    comment: str
    magic: int


@dataclass
class AccountInfo:
    """Account summary."""
    login: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    leverage: int
    currency: str
    profit: float = 0.0


@dataclass
class SymbolInfo:
    """Symbol properties."""
    name: str
    bid: float
    ask: float
    spread: float
    point: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    swap_long: float
    swap_short: float
    digits: int
    trade_mode: int                # 0=disabled, 1=long only, 2=short only, 3=close only, 4=full
    session_open: tuple
    session_close: tuple


# ── Bridge Class ────────────────────────────────────────────────────────────

class MT5Bridge:
    """
    Python ↔ MetaTrader 5 communication bridge.
    
    Manages connection lifecycle, provides data and order execution,
    and handles automatic reconnection with exponential backoff.
    """
    
    def __init__(self, login: int, password: str, server: str,
                 max_reconnect_attempts: int = 10,
                 reconnect_backoff_base_ms: int = 1000,
                 reconnect_backoff_max_ms: int = 30000,
                 heartbeat_interval_seconds: int = 30):
        """
        Initialize the bridge with connection parameters.
        
        Args:
            login: MT5 account login number
            password: MT5 account password
            server: MT5 broker server name
            max_reconnect_attempts: Maximum reconnection attempts before giving up
            reconnect_backoff_base_ms: Starting backoff in milliseconds
            reconnect_backoff_max_ms: Maximum backoff cap in milliseconds
            heartbeat_interval_seconds: How often to check connection health
        """
        self.login = login
        self.password = password
        self.server = server
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_backoff_base_ms = reconnect_backoff_base_ms
        self.reconnect_backoff_max_ms = reconnect_backoff_max_ms
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        
        self._connected = False
        self._shutdown_requested = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._on_disconnect_callbacks: list[Callable] = []
        
    # ── Connection Lifecycle ─────────────────────────────────────────────────
    
    def initialize(self) -> bool:
        """
        Initialize and connect to MT5 terminal.
        Must be called before any other operations.
        
        Returns:
            True if successfully connected and logged in.
        """
        logger.info("mt5_bridge_init", login=self.login, server=self.server)
        
        if not mt5.initialize():
            error_code = mt5.last_error()
            logger.error("mt5_init_failed", error=error_code)
            return False
        
        # Attempt login
        authorized = mt5.login(
            login=self.login,
            password=self.password,
            server=self.server
        )
        
        if not authorized:
            error_code = mt5.last_error()
            logger.error("mt5_login_failed", error=error_code)
            mt5.shutdown()
            return False
        
        self._connected = True
        account = mt5.account_info()
        
        if account:
            logger.info(
                "mt5_connected",
                login=account.login,
                balance=account.balance,
                equity=account.equity,
                server=account.server
            )
        else:
            logger.info("mt5_connected", login=self.login, server=self.server)
        
        return True
    
    def shutdown(self) -> None:
        """Gracefully disconnect from MT5 terminal."""
        logger.info("mt5_shutdown_requested")
        self._shutdown_requested = True
        self._connected = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        
        mt5.shutdown()
        logger.info("mt5_shutdown_complete")
    
    async def reconnect(self) -> bool:
        """
        Attempt reconnection with exponential backoff.
        
        Returns:
            True if reconnection successful.
        """
        for attempt in range(1, self.max_reconnect_attempts + 1):
            if self._shutdown_requested:
                return False
            
            backoff_ms = min(
                self.reconnect_backoff_base_ms * (2 ** (attempt - 1)),
                self.reconnect_backoff_max_ms
            )
            
            logger.warning(
                "mt5_reconnect_attempt",
                attempt=attempt,
                max_attempts=self.max_reconnect_attempts,
                backoff_ms=backoff_ms
            )
            
            await asyncio.sleep(backoff_ms / 1000.0)
            
            if self.initialize():
                logger.info("mt5_reconnect_success", attempt=attempt)
                return True
        
        logger.critical("mt5_reconnect_exhausted", attempts=self.max_reconnect_attempts)
        return False
    
    def is_connected(self) -> bool:
        """Check if the bridge is currently connected."""
        return self._connected and mt5.terminal_info() is not None
    
    # ── Heartbeat ────────────────────────────────────────────────────────────
    
    async def start_heartbeat(self) -> None:
        """Start periodic connection health checks."""
        async def _heartbeat_loop():
            while not self._shutdown_requested:
                try:
                    if not self.is_connected():
                        logger.warning("heartbeat_connection_lost")
                        for cb in self._on_disconnect_callbacks:
                            try:
                                cb()
                            except Exception as e:
                                logger.error("heartbeat_callback_error", error=str(e))
                        
                        # Attempt reconnect
                        reconnected = await self.reconnect()
                        if not reconnected:
                            logger.critical("heartbeat_reconnect_failed")
                            break
                    else:
                        # Touch heartbeat file
                        import os
                        try:
                            heartbeat_file = "/tmp/xau_heartbeat"
                            os.utime(heartbeat_file, None)
                        except OSError:
                            pass
                except Exception as e:
                    logger.error("heartbeat_error", error=str(e))
                
                await asyncio.sleep(self.heartbeat_interval_seconds)
        
        self._heartbeat_task = asyncio.create_task(_heartbeat_loop())
        logger.info("heartbeat_started", interval_seconds=self.heartbeat_interval_seconds)
    
    def on_disconnect(self, callback: Callable) -> None:
        """Register a callback to be called on disconnection."""
        self._on_disconnect_callbacks.append(callback)
    
    # ── Market Data ──────────────────────────────────────────────────────────
    
    def get_tick(self, symbol: str = "XAUUSD") -> Optional[TickData]:
        """
        Get the latest tick for a symbol.
        
        Args:
            symbol: Trading instrument name
            
        Returns:
            TickData or None if unavailable.
        """
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        
        return TickData(
            symbol=symbol,
            bid=tick.bid,
            ask=tick.ask,
            last=tick.last,
            volume=tick.volume if hasattr(tick, 'volume') else 0.0,
            time=datetime.fromtimestamp(tick.time, tz=timezone.utc),
        )
    
    def get_bars(self, symbol: str = "XAUUSD", timeframe: str = "M5",
                 count: int = 100, start_pos: int = 0) -> pd.DataFrame:
        """
        Fetch historical OHLCV bars.
        
        Args:
            symbol: Trading instrument
            timeframe: MT5 timeframe string (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
            count: Number of bars to retrieve
            start_pos: Starting position (0 = most recent)
            
        Returns:
            DataFrame with columns: time, open, high, low, close, tick_volume, spread
        """
        tf = self._parse_timeframe(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
        
        if rates is None or len(rates) == 0:
            logger.warning("get_bars_empty", symbol=symbol, timeframe=timeframe)
            return pd.DataFrame()
        
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
    
    def get_ticks_range(self, symbol: str = "XAUUSD",
                        from_time: datetime = None,
                        to_time: datetime = None,
                        count: int = 1000) -> pd.DataFrame:
        """
        Fetch tick data within a time range.
        
        Args:
            symbol: Trading instrument
            from_time: Start datetime (UTC)
            to_time: End datetime (UTC)
            count: Maximum ticks to fetch
            
        Returns:
            DataFrame of ticks.
        """
        if from_time is None:
            from_time = datetime.now(timezone.utc) - pd.Timedelta(hours=1)
        if to_time is None:
            to_time = datetime.now(timezone.utc)
        
        from_ts = int(from_time.timestamp())
        to_ts = int(to_time.timestamp())
        
        ticks = mt5.copy_ticks_range(symbol, from_ts, to_ts, mt5.COPY_TICKS_ALL)
        
        if ticks is None or len(ticks) == 0:
            return pd.DataFrame()
        
        df = pd.DataFrame(ticks[:count])
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
    
    def get_symbol_info(self, symbol: str = "XAUUSD") -> Optional[SymbolInfo]:
        """
        Get detailed symbol properties.
        
        Args:
            symbol: Trading instrument name
            
        Returns:
            SymbolInfo or None if symbol not available.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        
        return SymbolInfo(
            name=info.name,
            bid=info.bid,
            ask=info.ask,
            spread=info.spread,
            point=info.point,
            contract_size=info.trade_contract_size,
            min_volume=info.volume_min,
            max_volume=info.volume_max,
            volume_step=info.volume_step,
            swap_long=info.swap_long,
            swap_short=info.swap_short,
            digits=info.digits,
            trade_mode=info.trade_mode,
            session_open=info.session_open,
            session_close=info.session_close,
        )
    
    def get_account_info(self) -> Optional[AccountInfo]:
        """
        Get current account information.
        
        Returns:
            AccountInfo or None if unavailable.
        """
        acc = mt5.account_info()
        if acc is None:
            return None
        
        return AccountInfo(
            login=acc.login,
            balance=acc.balance,
            equity=acc.equity,
            margin=acc.margin,
            margin_free=acc.margin_free,
            margin_level=acc.margin_level,
            leverage=acc.leverage,
            currency=acc.currency,
            profit=acc.profit,
        )
    
    # ── Order Management ─────────────────────────────────────────────────────
    
    def _to_mt5_order_type(self, order_type: OrderType) -> int:
        """Convert our OrderType enum to MT5 order type constant."""
        mapping = {
            OrderType.MARKET_BUY: mt5.ORDER_TYPE_BUY,
            OrderType.MARKET_SELL: mt5.ORDER_TYPE_SELL,
            OrderType.BUY_LIMIT: mt5.ORDER_TYPE_BUY_LIMIT,
            OrderType.SELL_LIMIT: mt5.ORDER_TYPE_SELL_LIMIT,
            OrderType.BUY_STOP: mt5.ORDER_TYPE_BUY_STOP,
            OrderType.SELL_STOP: mt5.ORDER_TYPE_SELL_STOP,
        }
        return mapping[order_type]
    
    def _to_mt5_filling(self, filling: OrderFilling) -> int:
        """Convert our OrderFilling enum to MT5 filling constant."""
        mapping = {
            OrderFilling.FOK: mt5.ORDER_FILLING_FOK,
            OrderFilling.IOC: mt5.ORDER_FILLING_IOC,
            OrderFilling.RETURN: mt5.ORDER_FILLING_RETURN,
        }
        return mapping[filling]
    
    def send_order(self, request: OrderRequest) -> OrderResult:
        """
        Send a trade order to MT5.
        
        Args:
            request: OrderRequest with all parameters
            
        Returns:
            OrderResult indicating success/failure with details.
        """
        if not self.is_connected():
            return OrderResult(
                success=False,
                error_code=-1,
                error_desc="MT5 bridge not connected"
            )
        
        # Validate symbol availability
        mt5.symbol_select(request.symbol, True)
        
        # Build MT5 request
        mt5_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": request.volume,
            "type": self._to_mt5_order_type(request.order_type),
            "price": request.price if request.price > 0 else mt5.symbol_info_tick(request.symbol).ask if "buy" in request.order_type else mt5.symbol_info_tick(request.symbol).bid,
            "sl": request.stop_loss,
            "tp": request.take_profit,
            "deviation": request.deviation,
            "magic": request.magic,
            "comment": request.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._to_mt5_filling(request.filling),
        }
        
        logger.info(
            "order_sending",
            symbol=request.symbol,
            order_type=request.order_type,
            volume=request.volume,
            price=mt5_request["price"],
            sl=request.stop_loss,
            tp=request.take_profit
        )
        
        # Send order
        result = mt5.order_send(mt5_request)
        
        if result is None:
            error = mt5.last_error()
            logger.error("order_send_null", error=error)
            return OrderResult(
                success=False,
                error_code=error[0] if error else -2,
                error_desc=str(error) if error else "Unknown error sending order"
            )
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                "order_failed",
                retcode=result.retcode,
                comment=result.comment,
                request_id=result.request_id
            )
            return OrderResult(
                success=False,
                order_id=result.order,
                volume=result.volume,
                price=result.price,
                comment=result.comment,
                error_code=result.retcode,
                error_desc=f"MT5 retcode: {result.retcode} - {result.comment}"
            )
        
        logger.info(
            "order_filled",
            order_id=result.order,
            volume=result.volume,
            price=result.price,
            comment=result.comment
        )
        
        return OrderResult(
            success=True,
            order_id=result.order,
            volume=result.volume,
            price=result.price,
            comment=result.comment,
        )
    
    def close_position(self, ticket: int, deviation: int = 20,
                       comment: str = "XAU_System_Close") -> OrderResult:
        """
        Close an open position by ticket number.
        
        Args:
            ticket: Position ticket number
            deviation: Maximum slippage in points
            comment: Order comment
            
        Returns:
            OrderResult indicating success/failure.
        """
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            return OrderResult(
                success=False,
                error_code=-3,
                error_desc=f"Position {ticket} not found"
            )
        
        pos = position[0]
        
        # Determine close order type
        if pos.type == mt5.POSITION_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(pos.symbol).bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(pos.symbol).ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": deviation,
            "magic": 20260516,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        logger.info("close_position", ticket=ticket, symbol=pos.symbol, volume=pos.volume)
        
        result = mt5.order_send(request)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = mt5.last_error()
            logger.error("close_position_failed", ticket=ticket, error=error)
            return OrderResult(
                success=False,
                error_code=result.retcode if result else error[0],
                error_desc=f"Failed to close position {ticket}"
            )
        
        logger.info("position_closed", ticket=ticket, profit=pos.profit)
        return OrderResult(
            success=True,
            order_id=result.order,
            volume=result.volume,
            price=result.price,
        )
    
    def modify_position(self, ticket: int, stop_loss: float = None,
                        take_profit: float = None) -> bool:
        """
        Modify Stop Loss and/or Take Profit for an open position.
        
        Args:
            ticket: Position ticket number
            stop_loss: New Stop Loss price (None = unchanged)
            take_profit: New Take Profit price (None = unchanged)
            
        Returns:
            True if modification successful.
        """
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            logger.error("modify_position_not_found", ticket=ticket)
            return False
        
        pos = position[0]
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": stop_loss if stop_loss is not None else pos.sl,
            "tp": take_profit if take_profit is not None else pos.tp,
        }
        
        logger.info(
            "modify_position",
            ticket=ticket,
            new_sl=stop_loss,
            new_tp=take_profit
        )
        
        result = mt5.order_send(request)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("modify_position_failed", ticket=ticket)
            return False
        
        return True
    
    # ── Position Queries ─────────────────────────────────────────────────────
    
    def get_open_positions(self, symbol: str = None,
                           magic: int = None) -> list[PositionInfo]:
        """
        Get currently open positions, optionally filtered.
        
        Args:
            symbol: Filter by symbol (None = all)
            magic: Filter by magic number (None = all)
            
        Returns:
            List of PositionInfo objects.
        """
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        
        if positions is None:
            return []
        
        result = []
        for pos in positions:
            if magic is not None and pos.magic != magic:
                continue
            
            # Get current price
            tick = mt5.symbol_info_tick(pos.symbol)
            current_price = tick.ask if pos.type == mt5.POSITION_TYPE_BUY else tick.bid if tick else pos.price_open
            
            result.append(PositionInfo(
                ticket=pos.ticket,
                symbol=pos.symbol,
                type="buy" if pos.type == mt5.POSITION_TYPE_BUY else "sell",
                volume=pos.volume,
                open_price=pos.price_open,
                current_price=current_price,
                stop_loss=pos.sl,
                take_profit=pos.tp,
                profit=pos.profit,
                swap=pos.swap,
                commission=pos.commission,
                open_time=datetime.fromtimestamp(pos.time, tz=timezone.utc),
                comment=pos.comment,
                magic=pos.magic,
            ))
        
        return result
    
    def get_position(self, ticket: int) -> Optional[PositionInfo]:
        """Get a single position by ticket number."""
        positions = self.get_open_positions()
        for pos in positions:
            if pos.ticket == ticket:
                return pos
        return None
    
    def count_positions(self, symbol: str = None, magic: int = 20260516) -> int:
        """Count open positions matching filters."""
        return len(self.get_open_positions(symbol=symbol, magic=magic))
    
    def close_all_positions(self, symbol: str = None,
                            magic: int = 20260516) -> list[OrderResult]:
        """
        Close all open positions matching filter criteria.
        
        Args:
            symbol: Filter by symbol (None = all)
            magic: Filter by magic number
            
        Returns:
            List of OrderResult for each closed position.
        """
        positions = self.get_open_positions(symbol=symbol, magic=magic)
        results = []
        
        for pos in positions:
            result = self.close_position(pos.ticket)
            results.append(result)
            
            # Small delay between closures to avoid broker throttling
            if len(positions) > 1:
                time.sleep(0.1)
        
        logger.info("close_all_positions", count=len(positions), results=[r.success for r in results])
        return results
    
    # ── Order History ────────────────────────────────────────────────────────
    
    def get_order_history(self, symbol: str = "XAUUSD",
                          from_time: datetime = None,
                          count: int = 50) -> pd.DataFrame:
        """
        Get historical orders.
        
        Args:
            symbol: Instrument filter
            from_time: Starting datetime (UTC)
            count: Maximum orders to fetch
            
        Returns:
            DataFrame of historical orders.
        """
        if from_time is None:
            from_time = datetime.now(timezone.utc) - pd.Timedelta(days=7)
        
        from_ts = int(from_time.timestamp())
        to_ts = int(datetime.now(timezone.utc).timestamp())
        
        history = mt5.history_deals_get(from_ts, to_ts)
        
        if history is None or len(history) == 0:
            return pd.DataFrame()
        
        df = pd.DataFrame(list(history[:count]))
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
    
    # ── Symbol Management ────────────────────────────────────────────────────
    
    def ensure_symbols(self, symbols: list[str]) -> list[str]:
        """
        Ensure symbols are visible in Market Watch.
        
        Args:
            symbols: List of symbol names to enable
            
        Returns:
            List of symbols that were successfully enabled.
        """
        enabled = []
        for sym in symbols:
            if mt5.symbol_select(sym, True):
                enabled.append(sym)
            else:
                logger.warning("symbol_select_failed", symbol=sym)
        return enabled
    
    def get_current_spread(self, symbol: str = "XAUUSD") -> float:
        """
        Get current bid-ask spread for a symbol.
        
        Args:
            symbol: Instrument name
            
        Returns:
            Spread in points.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            return 0.0
        return info.spread
    
    # ── Utility ──────────────────────────────────────────────────────────────
    
    @staticmethod
    def _parse_timeframe(tf: str) -> int:
        """Convert friendly timeframe string to MT5 constant."""
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
            "W1": mt5.TIMEFRAME_W1,
            "MN1": mt5.TIMEFRAME_MN1,
        }
        return mapping.get(tf.upper(), mt5.TIMEFRAME_M5)
    
    @staticmethod
    def timeframe_minutes(tf: str) -> int:
        """Convert timeframe string to minutes."""
        mapping = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "D1": 1440,
            "W1": 10080, "MN1": 43200,
        }
        return mapping.get(tf.upper(), 5)
    
    async def get_terminal_info(self) -> dict:
        """Get MT5 terminal information."""
        info = mt5.terminal_info()
        if info is None:
            return {}
        return {
            "community_account": info.community_account,
            "community_connection": info.community_connection,
            "connected": info.connected,
            "path": info.path,
            "build": info.build,
            "name": info.name,
        }
