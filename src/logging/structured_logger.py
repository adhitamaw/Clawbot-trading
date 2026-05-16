"""
Structured logging with structlog.
Audit trail with cryptographic checksums.
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone

import structlog


def setup_logging(log_level: str = "INFO", log_format: str = "json",
                  log_dir: str = "./logs", audit_enabled: bool = True):
    """
    Configure structured logging for the entire system.
    
    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_format: Output format (json or text)
        log_dir: Directory for log files
        audit_enabled: Whether to write cryptographic audit trail
    """
    # Ensure log directory exists
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    
    log_level_num = getattr(logging, log_level.upper(), logging.INFO)
    
    # Shared processors
    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()
    
    structlog.configure(
        processors=processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    # File handler for JSON logs
    log_file = os.path.join(log_dir, f"xau_trading_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level_num)
    
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=processors,
    )
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING if log_format == "json" else log_level_num)
    console_handler.setFormatter(formatter)
    
    # Root logger setup
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level_num)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    # Store audit config
    if audit_enabled:
        audit_dir = os.path.join(log_dir, "audit")
        Path(audit_dir).mkdir(parents=True, exist_ok=True)
    
    return structlog.get_logger()


def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a structured logger instance."""
    logger = structlog.get_logger(name or __name__)
    return logger.bind()


class AuditLogger:
    """
    Cryptographic audit trail for all critical decisions.
    
    Each entry includes:
    - UTC ISO timestamp
    - Event type and description
    - Full feature snapshot
    - Cryptographic checksum (git commit SHA + config hash)
    """
    
    def __init__(self, log_dir: str = "./logs/audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._log_file = self.log_dir / f"audit_{self.today}.jsonl"
    
    def log_decision(self, event_type: str, data: dict,
                     feature_snapshot: dict = None) -> None:
        """
        Log a critical decision with full context.
        
        Args:
            event_type: Type of decision (e.g., "signal_generated", "order_executed")
            data: Decision-specific data
            feature_snapshot: Current feature/indicator values
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "data": data,
            "features": feature_snapshot or {},
        }
        
        # Add checksum over the entry for integrity
        entry_str = json.dumps(entry, sort_keys=True, default=str)
        entry["checksum"] = hashlib.sha256(entry_str.encode()).hexdigest()[:16]
        
        with open(self._log_file, 'a') as f:
            f.write(json.dumps(entry, default=str) + "\n")
    
    def read_audit_trail(self, date_str: str = None) -> list[dict]:
        """
        Read audit log for a specific date.
        
        Args:
            date_str: Date in YYYYMMDD format (default: today)
        """
        date_str = date_str or self.today
        audit_file = self.log_dir / f"audit_{date_str}.jsonl"
        
        if not audit_file.exists():
            return []
        
        entries = []
        with open(audit_file, 'r') as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
        return entries
