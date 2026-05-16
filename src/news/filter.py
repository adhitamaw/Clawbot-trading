"""
Macroeconomic News Filter.

Automatically pauses ALL new entries:
- 30 minutes before High-Impact events
- 30 minutes after High-Impact events
- Optional: close existing positions 15 min before major events

Calendar Sources (priority order):
1. Trading Economics API
2. Forex Factory Calendar (daily export)
3. Finnhub

Focus on USD-related + Gold-sensitive events:
FOMC, NFP, CPI, Core CPI, GDP, Retail Sales, PPI, Fed speakers, etc.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


@dataclass
class NewsEvent:
    """Single economic calendar event."""
    id: str
    title: str
    country: str
    impact: str                  # "high", "medium", "low"
    scheduled_time: datetime
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
    currency: str = "USD"
    
    @property
    def is_usd(self) -> bool:
        return self.currency == "USD"
    
    @property
    def is_high_impact(self) -> bool:
        return self.impact.lower() in ("high", "red", "3")
    
    @property
    def is_gold_sensitive(self) -> bool:
        """Check if event is relevant for gold trading."""
        gold_keywords = [
            "fomc", "nfp", "nonfarm", "cpi", "core cpi", "gdp",
            "retail sales", "ppi", "fed", "interest rate",
            "unemployment", "inflation", "consumer sentiment",
            "ism", "durable goods", "trade balance", "initial jobless",
            "current account", "housing", "new home", "existing home",
        ]
        title_lower = self.title.lower()
        return any(kw in title_lower for kw in gold_keywords)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "country": self.country,
            "impact": self.impact,
            "scheduled_time": self.scheduled_time.isoformat(),
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "currency": self.currency,
        }


@dataclass
class NewsStatus:
    """Current news filter status."""
    is_paused: bool
    reason: str = ""
    next_event: Optional[NewsEvent] = None
    time_to_next_event: float = 0.0  # minutes
    active_windows: int = 0
    events_skipped_today: int = 0


class NewsFilter:
    """
    Macroeconomic news calendar filter.
    
    Fetches economic calendar daily and enforces trade pauses
    around high-impact USD events that affect gold.
    """
    
    def __init__(self, config=None):
        """
        Initialize news filter.
        
        Args:
            config: TradingSystemConfig.news
        """
        self.enabled = True
        self.pre_pause_minutes = 30
        self.post_pause_minutes = 30
        self.close_before_minutes = 15
        self.calendar_source = "trading_economics"
        self.calendar_api_key: Optional[str] = None
        self.daily_sync_time = "00:30"
        
        # Event keywords to filter
        self.filter_keywords = [
            "FOMC", "NFP", "Nonfarm", "CPI", "Core CPI",
            "GDP", "Retail Sales", "PPI", "Fed",
            "Interest Rate", "Unemployment"
        ]
        
        if config is not None:
            self._apply_config(config)
        
        # State
        self._events: List[NewsEvent] = []
        self._last_sync: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()
        self._skipped_trade_count = 0
        self._todays_date = datetime.now(timezone.utc).date()
        
        # Calendar file for persistence
        self._calendar_file = Path("./data/calendar_cache.json")
        self._calendar_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load cached events
        self._load_cache()
    
    def _apply_config(self, config) -> None:
        """Apply configuration settings."""
        if hasattr(config, 'news'):
            n = config.news
            self.enabled = n.enabled
            self.pre_pause_minutes = n.pre_pause_minutes
            self.post_pause_minutes = n.post_pause_minutes
            self.close_before_minutes = n.close_before_minutes
            self.calendar_source = n.calendar_source
            self.calendar_api_key = n.calendar_api_key
            self.daily_sync_time = n.daily_sync_utc
            if n.filter_keywords:
                self.filter_keywords = n.filter_keywords
    
    # ── Calendar Sync ─────────────────────────────────────────────────────────
    
    async def sync_calendar(self, force: bool = False) -> List[NewsEvent]:
        """
        Synchronize economic calendar from configured source.
        
        Fetches next 48 hours of high-impact events.
        Runs daily at configured time or on demand.
        
        Args:
            force: Force sync even if already done today
            
        Returns:
            List of fetched NewsEvent objects.
        """
        if not self.enabled:
            return []
        
        # Check if sync already done today
        today = datetime.now(timezone.utc).date()
        if not force and self._last_sync and self._last_sync.date() == today:
            return self._events
        
        async with self._sync_lock:
            try:
                if self.calendar_source == "trading_economics":
                    events = await self._sync_trading_economics()
                elif self.calendar_source == "forex_factory":
                    events = await self._sync_forex_factory()
                elif self.calendar_source == "finnhub":
                    events = await self._sync_finnhub()
                else:
                    logger.warning("unknown_calendar_source", source=self.calendar_source)
                    events = []
                
                if events:
                    self._events = events
                    self._last_sync = datetime.now(timezone.utc)
                    self._save_cache()
                    logger.info(
                        "calendar_synced",
                        source=self.calendar_source,
                        events_count=len(events),
                    )
                
                return events
                
            except Exception as e:
                logger.error("calendar_sync_error", error=str(e), source=self.calendar_source)
                return self._events  # Return cached events on error
    
    async def _sync_trading_economics(self) -> List[NewsEvent]:
        """Sync from Trading Economics API."""
        if not self.calendar_api_key:
            logger.warning("trading_economics_no_api_key")
            return self._load_static_events()
        
        try:
            import aiohttp
            
            url = "https://api.tradingeconomics.com/calendar"
            params = {
                "c": "c133a6c1d4934e1:ql7dltbipraqkqd",  # default guest key
                "importance": "3",  # high impact only
                "format": "json",
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=15) as resp:
                    if resp.status != 200:
                        logger.warning("trading_economics_api_error", status=resp.status)
                        return self._load_static_events()
                    
                    data = await resp.json()
            
            events = []
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(hours=48)
            
            for item in data[:100]:
                event_time = datetime.fromisoformat(item.get("DateTime", "").replace("Z", "+00:00"))
                
                if event_time < now - timedelta(hours=2):  # skip old
                    continue
                if event_time > cutoff:  # skip too far
                    continue
                
                # Only USD events relevant to gold
                if item.get("Country", "") != "United States":
                    continue
                
                title = item.get("Event", "")
                if not self._is_relevant(title):
                    continue
                
                events.append(NewsEvent(
                    id=f"te_{item.get('CalendarId', '0')}",
                    title=title,
                    country="US",
                    impact="high",
                    scheduled_time=event_time,
                    actual=item.get("Actual"),
                    forecast=item.get("Forecast"),
                    previous=item.get("Previous"),
                    currency="USD",
                ))
            
            return events
            
        except ImportError:
            logger.warning("aiohttp_not_installed")
            return self._load_static_events()
        except Exception as e:
            logger.error("trading_economics_sync_error", error=str(e))
            return self._load_static_events()
    
    async def _sync_forex_factory(self) -> List[NewsEvent]:
        """Sync from Forex Factory calendar."""
        # Forex Factory doesn't have a public API
        # This is a placeholder for manual calendar import
        logger.info("forex_factory_sync_using_cache")
        return self._load_static_events()
    
    async def _sync_finnhub(self) -> List[NewsEvent]:
        """Sync from Finnhub API."""
        if not self.calendar_api_key:
            logger.warning("finnhub_no_api_key")
            return self._load_static_events()
        
        try:
            import aiohttp
            
            now = datetime.now(timezone.utc)
            from_date = now.strftime("%Y-%m-%d")
            to_date = (now + timedelta(days=2)).strftime("%Y-%m-%d")
            
            url = "https://finnhub.io/api/v1/calendar/economic"
            params = {
                "token": self.calendar_api_key,
                "from": from_date,
                "to": to_date,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=15) as resp:
                    if resp.status != 200:
                        return self._load_static_events()
                    data = await resp.json()
            
            events = []
            now_ts = now.timestamp()
            
            for item in data.get("economicCalendar", [])[:100]:
                if item.get("country") != "US":
                    continue
                
                event_ts = item.get("time", 0)
                if event_ts < now_ts - 7200:
                    continue
                
                title = item.get("event", "")
                if not self._is_relevant(title):
                    continue
                
                impact = "high" if item.get("impact", 0) >= 2 else "medium"
                
                events.append(NewsEvent(
                    id=f"fh_{event_ts}",
                    title=title,
                    country="US",
                    impact=impact,
                    scheduled_time=datetime.fromtimestamp(event_ts, tz=timezone.utc),
                    actual=item.get("actual"),
                    forecast=item.get("forecast"),
                    previous=item.get("prev"),
                    currency="USD",
                ))
            
            return events
            
        except ImportError:
            return self._load_static_events()
    
    def _load_static_events(self) -> List[NewsEvent]:
        """
        Load manually configured events from a static calendar file.
        This is the fallback when API is unavailable.
        Format: JSON array of events in data/calendar.json
        """
        static_file = Path("./data/calendar.json")
        
        if not static_file.exists():
            # Create template if missing
            static_file.parent.mkdir(parents=True, exist_ok=True)
            static_file.write_text(json.dumps([], indent=2))
            logger.info("static_calendar_created", path=str(static_file))
            return []
        
        try:
            with open(static_file, 'r') as f:
                raw = json.load(f)
            
            events = []
            for item in raw:
                events.append(NewsEvent(
                    id=item.get("id", f"static_{len(events)}"),
                    title=item.get("title", ""),
                    country=item.get("country", "US"),
                    impact=item.get("impact", "high"),
                    scheduled_time=datetime.fromisoformat(item["scheduled_time"]),
                    currency=item.get("currency", "USD"),
                ))
            
            return events
            
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("static_calendar_parse_error", error=str(e))
            return []
    
    def _is_relevant(self, title: str) -> bool:
        """Check if event title matches gold-relevant keywords."""
        title_lower = title.lower()
        for kw in self.filter_keywords:
            if kw.lower() in title_lower:
                return True
        return False
    
    # ── News Pause Check ──────────────────────────────────────────────────────
    
    def is_paused(self, timestamp: datetime = None) -> NewsStatus:
        """
        Check if trading should be paused due to an upcoming or recent news event.
        
        Args:
            timestamp: Current time (UTC). Default: now.
            
        Returns:
            NewsStatus with pause information.
        """
        if not self.enabled:
            return NewsStatus(is_paused=False)
        
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        # Daily counter reset
        today = timestamp.date()
        if today != self._todays_date:
            self._todays_date = today
            self._skipped_trade_count = 0
        
        active_windows = 0
        next_event: Optional[NewsEvent] = None
        min_time_to_next = float('inf')
        
        for event in self._events:
            if not event.is_usd:
                continue
            if not event.is_high_impact:
                continue
            
            time_to_event = (event.scheduled_time - timestamp).total_seconds() / 60
            time_since_event = (timestamp - event.scheduled_time).total_seconds() / 60
            
            # Pre-event pause window
            if 0 <= time_to_event <= self.pre_pause_minutes:
                active_windows += 1
                if time_to_event < min_time_to_next:
                    min_time_to_next = time_to_event
                    next_event = event
                continue
            
            # Post-event pause window
            if 0 <= time_since_event <= self.post_pause_minutes:
                active_windows += 1
                continue
            
            # Track closest upcoming event
            if time_to_event > 0 and time_to_event < min_time_to_next:
                min_time_to_next = time_to_event
                next_event = event
        
        is_paused = active_windows > 0
        
        reason = ""
        if is_paused:
            if next_event:
                reason = f"News: {next_event.title} in {min_time_to_next:.0f}min"
            else:
                reason = f"Post-event pause ({active_windows} windows)"
        
        return NewsStatus(
            is_paused=is_paused,
            reason=reason,
            next_event=next_event,
            time_to_next_event=min_time_to_next if min_time_to_next != float('inf') else 0,
            active_windows=active_windows,
            events_skipped_today=self._skipped_trade_count,
        )
    
    def should_close_positions(self, timestamp: datetime = None) -> bool:
        """
        Check if existing positions should be closed before a major event.
        
        Returns True if a high-impact event is within close_before_minutes.
        """
        if not self.enabled:
            return False
        
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        for event in self._events:
            if not event.is_usd or not event.is_high_impact:
                continue
            
            time_to_event = (event.scheduled_time - timestamp).total_seconds() / 60
            
            if 0 <= time_to_event <= self.close_before_minutes:
                logger.info("closing_before_event", event=event.title, minutes=time_to_event)
                return True
        
        return False
    
    def record_skipped_trade(self, event: NewsEvent = None) -> None:
        """Record a trade that was skipped due to news filter."""
        self._skipped_trade_count += 1
        event_info = event.title if event else "unknown"
        logger.info(
            "trade_skipped_news",
            event=event_info,
            total_skipped_today=self._skipped_trade_count,
        )
    
    # ── Upcoming Events ───────────────────────────────────────────────────────
    
    def get_upcoming_events(self, hours: int = 24) -> List[NewsEvent]:
        """
        Get upcoming high-impact events within the next N hours.
        
        Args:
            hours: Look-ahead window in hours
            
        Returns:
            List of upcoming NewsEvent objects.
        """
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)
        
        upcoming = []
        for event in self._events:
            if now <= event.scheduled_time <= cutoff:
                if event.is_usd and event.is_high_impact:
                    upcoming.append(event)
        
        return sorted(upcoming, key=lambda e: e.scheduled_time)
    
    # ── Persistence ───────────────────────────────────────────────────────────
    
    def _save_cache(self) -> None:
        """Save events to local cache file."""
        try:
            with open(self._calendar_file, 'w') as f:
                json.dump(
                    [e.to_dict() for e in self._events],
                    f, indent=2, default=str
                )
        except Exception as e:
            logger.warning("calendar_cache_save_error", error=str(e))
    
    def _load_cache(self) -> None:
        """Load events from local cache file."""
        if not self._calendar_file.exists():
            return
        
        try:
            with open(self._calendar_file, 'r') as f:
                data = json.load(f)
            
            self._events = []
            for item in data:
                self._events.append(NewsEvent(
                    id=item.get("id", ""),
                    title=item.get("title", ""),
                    country=item.get("country", "US"),
                    impact=item.get("impact", "high"),
                    scheduled_time=datetime.fromisoformat(item["scheduled_time"]),
                    actual=item.get("actual"),
                    forecast=item.get("forecast"),
                    previous=item.get("previous"),
                    currency=item.get("currency", "USD"),
                ))
            
            if self._events:
                logger.info("calendar_cache_loaded", events=len(self._events))
                
        except Exception as e:
            logger.warning("calendar_cache_load_error", error=str(e))
            self._events = []
    
    # ── Status ────────────────────────────────────────────────────────────────
    
    def get_status(self) -> dict:
        """Get current news filter status."""
        news_status = self.is_paused()
        upcoming = self.get_upcoming_events(12)
        
        return {
            "enabled": self.enabled,
            "is_paused": news_status.is_paused,
            "reason": news_status.reason,
            "active_windows": news_status.active_windows,
            "skipped_today": news_status.events_skipped_today,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "cached_events": len(self._events),
            "upcoming_12h": [
                {"title": e.title, "time": e.scheduled_time.isoformat(), "impact": e.impact}
                for e in upcoming[:8]
            ],
        }
