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

### Status: 🔧 Code Complete — v3.0 Redesign PRD Done — Pending Implementation

### BTC_Scalper_Pro.mq5 v2.0 (Existing)
- 528 lines, multi-TF (M5/M15/M30)
- ADX>22 + BB squeeze + EMA cascade + RSI(5) + PA
- **Audit found:** BB buffer bug, softBreak never activates, indicator leak, EMA cascade unused
- **Backtest:** 5 trades/month (too strict) — Adhit wants 10+/day

### BTC_Scalper_Pro v3.0 (Redesigned PRD)
- 📄 `docs/BTC_SCALPER_PRD.md` — Updated dengan:
  - **5-layer gate filter:** H4→H1→M30→M15→M5
  - **3 strategy modes:** Conservative / Balanced / Aggressive
  - **6 entry patterns:** Pinbar / Engulfing / BB Bounce / Squeeze Breakout / Retest / Cascade
  - **3-level TP:** 40%/35%/25% split with runner
  - **Compounding engine:** Auto-scale every +10%
  - **21 improvements documented** in `docs/BTC_SCALPER_IMPROVEMENT.md`
  - **Weekend mode, anti-chop, HTF S/R, volatility scaling**

### Target Metrik (Balanced mode)
| Metric | Target |
|--------|--------|
| Win Rate | 55-65% |
| Trades/Day | 3-7 |
| Monthly ROI | 15-30% |
| 200% Return | ~7 bulan (realistis) / ~4 bulan (aggressive) |
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
│   ├── BTC_SCALPER_PRD.md         # BTC scalper PRD (v3.0 updated)
│   └── BTC_SCALPER_IMPROVEMENT.md # 21 improvement items
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

1. [ ] Implement BTC_Scalper_Pro v3.0 MQL5 (~650 lines) based on updated PRD
2. [ ] Compile via MetaEditor (VNC → F7)
3. [ ] Backtest native MT5 — mode Balanced + Aggressive, 6 bulan
4. [ ] Attach ke BTCUSD M5 chart, live test (24/7)
5. [ ] Live test XAU/USD EA when market opens (Monday)
6. [ ] Review entry reason logs from live BTC trading
7. [ ] Optimize per mode berdasarkan hasil backtest & live
8. [ ] Build Python signal generator to feed XAU_Executor EA
