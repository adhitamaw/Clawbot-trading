# PRD: BTC/USD Trading Bot v1.0

## 1. Ringkasan
Bot trading otomatis untuk BTC/USD di MetaTrader 5 (Exness), memanfaatkan infrastruktur yang sudah ada (MT5 container + EA + JSON command pipeline).

## 2. Latar Belakang
- XAU/USD system sudah siap tapi market tutup weekend
- BTC/USD bisa trading 24/7 (termasuk weekend)
- Infrastruktur MT5 + EA yang udah ada bisa dipake ulang

## 3. Tujuan
- Bot bisa trading BTC/USD otomatis 24/7
- Integrasi dengan Exness demo account (siap lanjut ke real)
- Minimal 80% uptime

## 4. Arsitektur

```
┌─────────────────────┐     Command JSON     ┌──────────────────┐
│  Python Backend     │──────────────────────→│  MT5 Container   │
│  (Signal Generator) │←──────────────────────│  (BTC_Executor)  │
│                     │     Response JSON     │                  │
│  - BTC price feed   │                      │  - BTCUSD chart  │
│  - Strategy logic   │                      │  - Execute trades│
│  - Risk management  │                      │  - Trail SL/TP   │
│  - Telegram alerts  │                      │                  │
└─────────────────────┘                      └──────────────────┘
```

## 5. Komponen

### 5.1. BTC Executor EA (MQL5)
- Fork dari XAU_Executor dengan modifikasi:
  - Symbol: BTCUSD
  - Timeframe: M15 (lebih cocok buat crypto)
  - Max spread: 10 pips (crypto lebih volatil)
  - Circuit breaker: DD 10% (hard), 6% (soft)
  - Command file: `btc_command.json`
  - Response file: `btc_response.json`
  - Magic number: 20260517

### 5.2. Python Signal Generator
- **Price feed:** WebSocket dari Binance/Bybit API (real-time)
- **Strategi:**
  1. Trend Following — EMA crossover (20/50) di M15
  2. Mean Reversion — RSI (14) oversold/overbought di M5
  3. Momentum — Volume + price action breakout
- **Risk management:**
  - Risk per trade: 0.5%-1%
  - Max open positions: 2
  - Max daily loss: 5%
- **Telegram alerts:**
  - Signal masuk
  - Order terisi
  - Daily summary

### 5.3. Infrastructure (Existing)
- ✅ Docker container MT5 (port 5901/6901)
- ✅ Exness demo account
- ✅ Cloudflare tunnel
- ✅ Shared volume MQL5/Files/

## 6. File Structure

```
xauusd_trading_system_v1/
├── btc_ea/
│   ├── BTC_Executor.mq5
│   └── BTC_Executor.ex5 (compiled)
├── btc_bot/
│   ├── main.py          (entry point)
│   ├── strategy/
│   │   ├── trend.py
│   │   ├── mean_reversion.py
│   │   └── momentum.py
│   ├── risk.py
│   ├── notifier.py
│   └── config.py
├── docker/
│   └── docker-compose-btc.yml
└── tests/
    └── test_btc_bot.py
```

## 7. Implementation Phases

### Phase 1 — EA Setup (Estimasi: 1 hari)
- [ ] Buat BTC_Executor.mq5 (fork dari XAU_Executor)
- [ ] Compile EA
- [ ] Test attach ke chart BTCUSD di MT5
- [ ] Kirim test command → execute

### Phase 2 — Python Backend (Estimasi: 2 hari)
- [ ] Install Python di container (pake pendekatan rpyc)
- [ ] Price feed dari Binance WebSocket
- [ ] Strategy trend following (EMA crossover)
- [ ] Risk management module
- [ ] JSON command sender ke EA

### Phase 3 — Telegram & Monitoring (Estimasi: 1 hari)
- [ ] Integrasi Telegram bot (dari XAU system)
- [ ] Alert signal masuk
- [ ] Daily summary report
- [ ] Error notification

### Phase 4 — Testing & Optimization (Estimasi: 2-3 hari)
- [ ] Paper trading 3-7 hari
- [ ] Backtest performance
- [ ] Parameter optimization
- [ ] Gradual scaling

## 8. Tech Stack
- **Language:** Python 3.11+, MQL5
- **Data:** Binance WebSocket API
- **Execution:** MT5 via EA (JSON file IPC)
- **Monitoring:** Telegram Bot
- **Infra:** Docker, Azure VM

## 9. Performance Targets
| Metric | Target |
|--------|--------|
| Win Rate | ≥ 55% |
| Profit Factor | ≥ 1.5 |
| Sharpe Ratio | ≥ 1.2 |
| Max Drawdown | ≤ 12% |
| Daily Trades | 3-10 |
| Uptime | ≥ 99% |

## 10. Risk Notes
- Crypto 24/7 — butuh monitoring non-stop
- Volatilitas tinggi — spread wider saat news
- Gap risk antar candle
- Exchange outage possibility
- Gunakan demo account minimal 1 bulan sebelum live
