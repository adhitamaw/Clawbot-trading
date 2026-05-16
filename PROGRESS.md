# XAU/USD + BTC Trading System - Progress Report

> Last updated: 2026-05-16

---

## System Overview

Trading automation system running on Azure VM with MT5 (Docker/Wine), designed for:

- **XAU/USD** - Full-featured trading system with Python orchestrator + MQL5 EA (JSON IPC)
- **BTC/USD** - Standalone MQL5 Scalping EA (M5 timeframe, self-contained)

---

## Phase 1: XAU/USD Trading System

### Status: ✅ Development Complete, ⏸️ Pending Demo Testing

| Component | Status | Notes |
|-----------|--------|-------|
| MT5 Docker Container | ✅ Running | `hudsonventura/mt5:latest`, build 5836 |
| Exness Demo Account | ✅ Configured | Server: Exness-MT5Trial17, Login: 463427941 |
| VNC Access | ✅ Working | Cloudflare Tunnel (trycloudflare.com) |
| EA Compilation | ✅ Done | XAU_Executor.mq5 → XAU_Executor.ex5 |
| EA Command Pipeline | ✅ Tested | Reads JSON via FILE_COMMON, market was closed |
| Python-MT5 Bridge | ❌ Blocked | Linux/Wine incompatibility (`crealf` DLL issue) |
| Live Demo Trading | ⏸️ Pending | Market opens Monday |

### Key Architecture Decision

Python `MetaTrader5` package doesn't work under Wine (DLL `crealf` missing). Solution: **MQL5 EA + JSON file IPC** via FILE_COMMON path:

1. Python writes `xau_command.json` to `C:\Users\root\AppData\Roaming\MetaQuotes\Terminal\Common\Files\`
2. XAU_Executor EA reads command, executes trade, writes response
3. Python reads response for confirmation

---

## Phase 2: BTC Scalping EA

### Status: 🔧 Code Complete, ⏸️ Backtest Done, Pending Compilation & Live Test

### BTC_Scalper.mq5 (Basic)
- **Strategy:** RSI(14) + EMA(20/50) crossover + Volume confirmation
- **Timeframe:** M5
- **Lines:** 484

### BTC_Scalper_Pro.mq5 (Professional)
- **Strategy:** Multi-timeframe (M5/M15/M30) + ADX filter (>22) + BB squeeze + EMA cascade (8/21/34) + RSI(5) + Volume + Price Action patterns
- **Features:** Partial TP (50%/50%), pyramiding (max 3), dynamic SL (ATR-based), circuit breaker (max 3 losses), news filter
- **Lines:** 528

### Backtest Results (Apr 16 - May 16, 2026)
| Metric | Value |
|--------|-------|
| Total Trades | 5 |
| Win Rate | 60% (3 wins, 2 losses) |
| Profit Factor | 3.28 |
| Max DD | 0.0% |
| Return | +0.0% (flat - tight SL) |
| PASS Criteria | ✅ Win Rate >55%, ✅ PF >1.5, ✅ DD <15% |

**Issue:** Only 5 trades generated in 1 month. User wants minimum ~10 trades/day (aggressive).

### Next Steps
1. Compile BTC_Scalper_Pro.mq5 via VNC MetaEditor (F7)
2. Attach to BTCUSD M5 chart in MT5
3. Live test on BTCUSD (24/7 market)
4. Optimize for more trade frequency

---

## Infrastructure

| Resource | Detail |
|----------|--------|
| VM | Azure VM (Whales), Ubuntu, IP 40.80.95.141 |
| MT5 Container | Port 5901/6901, VNC: xautrading2025 |
| Cloudflare Tunnel | TryCloudflare (no auth needed) |
| GitHub | github.com/adhitamaw/Clawbot-trading |
| Speed Test | Ping 15ms, Down 254 Mbps, Up 534 Mbps |

---

## File Manifest

```
xauusd_trading_system_v1/
├── PROGRESS.md                    # This file
├── PRD.md                         # Original PRD
├── GO_LIVE.md                     # Go-live checklist
├── run_backtest.py                # Backtest script
├── mql5_ea/
│   ├── XAU_Executor.mq5           # XAU EA (448 lines)
│   ├── BTC_Scalper.mq5            # Basic BTC scalper (484 lines)
│   └── BTC_Scalper_Pro.mq5        # Pro BTC scalper (528 lines)
├── docs/
│   ├── BTC_TRADING_PRD.md         # BTC trading PRD
│   └── BTC_SCALPER_PRD.md         # BTC scalper PRD
└── trade/
    ├── docker-compose-mt5.yml     # MT5 container config
    ├── custom_start.sh            # Container startup script
    ├── setup_mt5_rpc.sh           # RPC setup script
    └── mt5_data/                  # MT5 shared volume
```

---

## Known Issues

1. **Python-MT5 Bridge:** `mt5.initialize()` hangs in Wine due to missing `ucrtbase.dll.crealf`
2. **Azure NSG:** Ports 5901/6901 blocked, Cloudflare Tunnel is current workaround
3. **Auto-login:** Exness account needs manual login via VNC (mt5.ini doesn't auto-trigger)

---

## What's Next

1. [ ] Compile BTC_Scalper_Pro.mq5 via MetaEditor (VNC)
2. [ ] Run BTC backtest in MT5 native tester
3. [ ] Live test XAU/USD EA when market opens (Monday)
4. [ ] Live test BTC scalper on BTCUSD (24/7)
5. [ ] Build Python signal generator to feed XAU_Executor EA
6. [ ] Optimize BTC strategy for higher trade frequency
