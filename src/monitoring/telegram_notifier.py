"""
Telegram Alert Notifier for XAU/USD Trading System.

Sends formatted alerts for:
- Critical: Circuit breaker triggered, anomaly detected, connection lost
- Warning: Soft breaker, news pause, high spread
- Info: Trade opened/closed, daily summary, regime change
- Debug: Heartbeat, signal generation

Features:
- Rate limiting per alert type
- Formatted messages with emoji indicators
- Daily summary with key metrics
- Error resilience (non-blocking, fails gracefully)
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict
from collections import defaultdict

from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class AlertLevel:
    CRITICAL = "🔴"
    WARNING = "🟡"
    INFO = "🔵"
    SUCCESS = "🟢"
    DEBUG = "⚪"


class TelegramNotifier:
    """
    Async Telegram notification system with rate limiting.
    
    Sends formatted trading alerts to configured chat/channel.
    """
    
    def __init__(self, bot_token: str = None, chat_id: str = None,
                 alert_cooldown_seconds: int = 300):
        """
        Initialize Telegram notifier.
        
        Args:
            bot_token: Telegram Bot API token
            chat_id: Target chat/channel ID
            alert_cooldown_seconds: Minimum seconds between same-type alerts
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.cooldown_seconds = alert_cooldown_seconds
        self.enabled = bool(bot_token and chat_id)
        
        # Rate limiting
        self._last_alert: Dict[str, datetime] = {}
        
        # Daily summary tracking
        self._daily_stats = {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pl": 0.0,
            "anomalies": 0,
            "news_pauses": 0,
            "regime_changes": 0,
        }
        self._last_summary_date = None
    
    def _can_send(self, alert_type: str) -> bool:
        """Check rate limit for alert type."""
        last = self._last_alert.get(alert_type)
        if last and (datetime.now(timezone.utc) - last).total_seconds() < self.cooldown_seconds:
            return False
        self._last_alert[alert_type] = datetime.now(timezone.utc)
        return True
    
    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send message to Telegram.
        
        Args:
            text: Message text (HTML formatted)
            parse_mode: Telegram parse mode
            
        Returns:
            True if sent successfully.
        """
        if not self.enabled:
            return False
        
        try:
            import aiohttp
            
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        return True
                    
                    error_data = await resp.text()
                    logger.warning("telegram_send_failed",
                                  status=resp.status,
                                  error=error_data[:100])
                    return False
                    
        except ImportError:
            logger.warning("aiohttp_not_installed_for_telegram")
            return False
        except Exception as e:
            logger.error("telegram_send_error", error=str(e))
            return False
    
    # ── Alert Methods ──────────────────────────────────────────────────────────
    
    async def critical(self, title: str, details: str = "",
                      data: dict = None) -> bool:
        """
        Send CRITICAL alert.
        
        Used for: circuit breaker, anomaly detected, connection lost.
        """
        if not self._can_send(f"critical_{title}"):
            return False
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        msg = f"{AlertLevel.CRITICAL} <b>CRITICAL: {title}</b>\n"
        msg += f"<i>{now}</i>\n"
        
        if details:
            msg += f"\n{details}\n"
        
        if data:
            for key, val in data.items():
                msg += f"• <b>{key}:</b> {val}\n"
        
        logger.critical("telegram_alert_critical", title=title)
        return await self._send(msg)
    
    async def warning(self, title: str, details: str = "",
                     data: dict = None) -> bool:
        """Send WARNING alert."""
        if not self._can_send(f"warning_{title}"):
            return False
        
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        
        msg = f"{AlertLevel.WARNING} <b>WARNING: {title}</b>\n"
        msg += f"<i>{now}</i>\n"
        
        if details:
            msg += f"\n{details}\n"
        
        if data:
            for key, val in data.items():
                msg += f"• <b>{key}:</b> {val}\n"
        
        logger.warning("telegram_alert_warning", title=title)
        return await self._send(msg)
    
    async def info(self, title: str, details: str = "",
                  data: dict = None) -> bool:
        """Send INFO alert (no rate limiting)."""
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        
        msg = f"{AlertLevel.INFO} <b>{title}</b>\n"
        msg += f"<i>{now}</i>\n"
        
        if details:
            msg += f"\n{details}\n"
        
        if data:
            for key, val in data.items():
                msg += f"• <b>{key}:</b> {val}\n"
        
        return await self._send(msg)
    
    async def trade_opened(self, direction: str, entry_price: float,
                          stop_loss: float, take_profit: float,
                          volume: float, regime: str,
                          risk_pct: float, rr_ratio: float) -> bool:
        """Notify on trade open."""
        emoji = "📈" if direction == "long" else "📉"
        dir_label = "LONG" if direction == "long" else "SHORT"
        
        msg = f"{emoji} <b>TRADE OPENED — {dir_label}</b>\n"
        msg += f"<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>\n\n"
        msg += f"Entry: <b>{entry_price}</b>\n"
        msg += f"SL: {stop_loss} | TP: {take_profit}\n"
        msg += f"Volume: {volume} lots | Regime: {regime}\n"
        msg += f"Risk: {risk_pct}% | R:R = {rr_ratio:.1f}\n"
        
        self._daily_stats["trades"] += 1
        return await self._send(msg)
    
    async def trade_closed(self, direction: str, exit_price: float,
                          net_pl: float, r_multiple: float,
                          exit_reason: str, hold_minutes: float) -> bool:
        """Notify on trade close."""
        is_win = net_pl > 0
        emoji = "✅" if is_win else "❌"
        pl_emoji = "💰" if net_pl > 5 else "📊"
        
        msg = f"{emoji} <b>TRADE CLOSED</b> {pl_emoji}\n"
        msg += f"<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>\n\n"
        msg += f"P&L: <b>${net_pl:+.2f}</b> ({r_multiple:+.1f}R)\n"
        msg += f"Exit: {exit_price} | Reason: {exit_reason}\n"
        msg += f"Hold: {hold_minutes:.0f}min\n"
        
        if is_win:
            self._daily_stats["wins"] += 1
        else:
            self._daily_stats["losses"] += 1
        self._daily_stats["pl"] += net_pl
        
        return await self._send(msg)
    
    async def anomaly_detected(self, reason: str, confidence: float,
                              layers: dict = None) -> bool:
        """Notify on anomaly detection."""
        self._daily_stats["anomalies"] += 1
        
        layer_info = ""
        if layers:
            layer_info = "\n".join(f"• {k}: {v}" for k, v in layers.items())
        
        return await self.critical(
            title="ANOMALY DETECTED",
            details=f"System entered anomaly cooldown.\n\nTrigger: {reason}\nConfidence: {confidence:.1%}",
            data=layers,
        )
    
    async def circuit_breaker(self, level: str, dd_pct: float,
                             equity: float, peak: float) -> bool:
        """Notify on circuit breaker trigger."""
        if level == "hard":
            return await self.critical(
                title="HARD CIRCUIT BREAKER",
                details=f"Trading HALTED for the day. All positions closed.",
                data={
                    "Drawdown": f"{dd_pct*100:.1f}%",
                    "Current Equity": f"${equity:,.2f}",
                    "Peak Equity": f"${peak:,.2f}",
                    "Action": "CLOSE ALL + HALT",
                },
            )
        else:
            return await self.warning(
                title="SOFT CIRCUIT BREAKER",
                details=f"Position size reduced by 50%. Filters tightened.",
                data={
                    "Drawdown": f"{dd_pct*100:.1f}%",
                    "Action": "REDUCE SIZE 50%",
                },
            )
    
    async def regime_changed(self, from_regime: str, to_regime: str,
                            details: dict = None) -> bool:
        """Notify on regime change."""
        self._daily_stats["regime_changes"] += 1
        
        return await self.info(
            title=f"Regime: {from_regime} → {to_regime}",
            data=details,
        )
    
    async def news_pause(self, event: str, minutes: float) -> bool:
        """Notify on news-related trading pause."""
        self._daily_stats["news_pauses"] += 1
        
        return await self.info(
            title=f"News Pause: {event}",
            details=f"Trading paused. Event in {minutes:.0f} min."
        )
    
    async def system_startup(self, balance: float, equity: float,
                            symbol: str, version: str = "1.0.0") -> bool:
        """Notify on system startup."""
        return await self.info(
            title="🚀 System Started",
            data={
                "Version": version,
                "Symbol": symbol,
                "Balance": f"${balance:,.2f}",
                "Equity": f"${equity:,.2f}",
            },
        )
    
    async def system_shutdown(self, reason: str = "Normal shutdown") -> bool:
        """Notify on system shutdown."""
        return await self.info(
            title="⚠️ System Shutting Down",
            details=reason,
        )
    
    # ── Daily Summary ──────────────────────────────────────────────────────────
    
    async def daily_summary(self) -> bool:
        """
        Send end-of-day summary with key metrics.
        
        Should be called at 23:55 UTC or end of NY session.
        """
        today = datetime.now(timezone.utc).date()
        
        if self._last_summary_date == today:
            return False
        
        self._last_summary_date = today
        
        s = self._daily_stats
        win_rate = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
        
        msg = "📊 <b>DAILY SUMMARY</b>\n"
        msg += f"<i>{today.strftime('%Y-%m-%d')}</i>\n\n"
        
        msg += f"{'🟢' if s['pl'] >= 0 else '🔴'} <b>P&L: ${s['pl']:+,.2f}</b>\n\n"
        
        msg += f"Trades: {s['trades']} | Wins: {s['wins']} | Losses: {s['losses']}\n"
        msg += f"Win Rate: {win_rate:.0f}%\n"
        msg += f"Anomalies: {s['anomalies']} | News Pauses: {s['news_pauses']}\n"
        msg += f"Regime Changes: {s['regime_changes']}\n"
        
        # Reset daily stats
        self._daily_stats = {
            "trades": 0, "wins": 0, "losses": 0, "pl": 0.0,
            "anomalies": 0, "news_pauses": 0, "regime_changes": 0,
        }
        
        return await self._send(msg)
    
    async def status_update(self, status: dict) -> bool:
        """Send periodic status update (heartbeat)."""
        account = status.get("account", {})
        regime = status.get("regime", {})
        
        msg = "💓 <b>System Status</b>\n"
        msg += f"<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>\n\n"
        
        if account:
            msg += f"Balance: ${account.get('balance', 0):,.2f}\n"
            msg += f"Equity: ${account.get('equity', 0):,.2f}\n"
            msg += f"Margin Level: {account.get('margin_level', 0):.0f}%\n"
        
        msg += f"\nRegime: <b>{regime.get('current_regime', 'N/A')}</b>\n"
        msg += f"Session: {status.get('session', 'N/A')}\n"
        msg += f"Positions: {status.get('open_positions', 0)}\n"
        msg += f"DD: {status.get('daily_dd_pct', 0)}%\n"
        
        return await self._send(msg)
