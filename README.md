# XAU/USD Institutional Quantitative Trading System

**Production-grade, fully automated trading system for XAU/USD (Gold).**

Version 1.0 — May 2026

---

## Architecture

```
Python 3.11+ (Signals, ML, Risk)  ←→  MetaTrader5 (Execution)
         ├── Regime Detection (Hybrid Time + ATR + ADX)
         ├── Intermarket Filter (DXY + US10Y)
         ├── ML Anomaly Detection (Statistical + Isolation Forest + Autoencoder)
         ├── ML Execution Optimization (DQN / Smart Executor)
         ├── Macroeconomic News Filter (30-min pre/post pause)
         ├── Risk Management (ATR-based sizing, 6% circuit breaker)
         └── Telegram Monitoring + Audit Trail
```

## Quick Start

### Prerequisites
- Python 3.11+
- MetaTrader 5 terminal installed
- MT5 account with XAUUSD access
- (Optional) PostgreSQL for trade database

### Installation

```bash
# Clone
git clone https://github.com/adhitamaw/Clawbot-trading.git
cd Clawbot-trading

# Virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Dependencies
pip install -r requirements.txt

# TA-Lib (system-level)
# Ubuntu: sudo apt-get install ta-lib
# macOS: brew install ta-lib
# Windows: download from https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib

# Configuration
cp config/.env.example config/.env
# Edit config/.env with your credentials
```

### Running

```bash
python -m src.main
```

Or with Docker:

```bash
docker compose -f docker/docker-compose.yml up -d
```

## Project Structure

```
├── config/
│   ├── config.yaml          # All tunable parameters
│   └── .env.example         # Environment variables template
├── src/
│   ├── main.py              # Async orchestrator
│   ├── config.py            # Pydantic settings loader
│   ├── mt5_bridge/          # Python ↔ MT5 communication
│   ├── data/                # Tick collection & historical data
│   ├── features/            # Technical indicators
│   ├── regime/              # Regime detection engine
│   ├── news/                # Economic calendar filter
│   ├── ml/                  # Anomaly detection + execution optimizer
│   ├── strategy/            # Mean-reversion & trend-following
│   ├── risk/                # Position sizing & circuit breakers
│   ├── execution/           # Smart order execution
│   ├── logging/             # Structured logging & audit trail
│   └── monitoring/          # Telegram alerts
├── mql5_ea/                 # MQL5 Expert Advisor (hybrid fallback)
├── backtest/                # Backtesting engine & walk-forward
├── tests/                   # Test suite
├── docker/                  # Docker deployment
└── deploy/                  # systemd service file
```

## Performance Targets

| Metric | Target |
|--------|--------|
| Win Rate | ≥ 58% |
| Profit Factor | ≥ 1.8 |
| Sharpe Ratio | ≥ 1.5 |
| Max Drawdown | ≤ 8% |
| Recovery Factor | ≥ 3.0 |

## Development Roadmap

1. ✅ MT5 Bridge + Hybrid MQL5 EA
2. ✅ Config + Logger + Main Orchestrator
3. ⏳ Real-time Tick Data + Anomaly Detection
4. ⏳ Regime Detection + Intermarket Filter
5. ⏳ News Filter Integration
6. ⏳ ML Models (Anomaly + Execution)
7. ⏳ Risk Engine + Circuit Breakers
8. ⏳ Backtesting Engine
9. ⏳ Docker Deployment
10. ⏳ Final Robustness & Documentation

## License

Private — All rights reserved.
