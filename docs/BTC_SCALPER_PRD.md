# PRD: BTC Scalping EA v1.0

## Ringkasan
EA scalping mandiri untuk BTC/USD di Exness. Tidak butuh Python backend — semua logika ada di dalem EA, bisa langsung di-backtest dan live trading.

## Spesifikasi

| Item | Value |
|------|-------|
| Symbol | BTCUSD |
| Timeframe | M5 |
| Akun | Exness demo (Exchange: 463427941) |
| Magic Number | 20260517 |
| Mode | Scalping (fast in/out) |

## Strategi — 3 Konfirmasi Entry

### Entry BUY:
1. **RSI(7) < 35** — oversold jangka pendek
2. **Close > EMA(21)** — konfirmasi tren naik
3. **Volume tick naik 1.5x dari average** — momentum confirm
→ BUY, SL: -2x ATR, TP: +3x ATR

### Entry SELL:
1. **RSI(7) > 65** — overbought jangka pendek
2. **Close < EMA(21)** — konfirmasi tren turun
3. **Volume tick naik 1.5x dari average** — momentum confirm
→ SELL, SL: +2x ATR, TP: -3x ATR

### Money Management:
- Risk per trade: 0.5% dari balance
- Auto lot berdasarkan SL distance
- Trailing stop aktif setelah +1.5x ATR profit
- Max 1 posisi dalam 1 arah

### Circuit Breaker:
- HARD DD 8%: close all positions, pause 24 jam
- SOFT DD 5%: reduce lot size 50%
- Max 3 consecutive losses → pause 1 jam
- Max spread > 15 pips → no entry

### Session Filter:
- BTC trading 24/7 (no session filter)

### Backtest Mode:
- Visual backtest enabled
- Default: initial deposit $10,000
- Parameter: lot=0.01, risk=0.5%

## Risk Parameters

```
Parameter              Value
─────────────────────────────
Max Daily Loss          5%
Max Consecutive Loss    3 trades
Circuit Break (Hard)    8% DD
Circuit Break (Soft)    5% DD  
ATR Period              14
RSI Period              7
RSI Oversold            35
RSI Overbought          65
EMA Period              21
Volume Threshold        1.5x avg
SL Multiplier           2.0x ATR
TP Multiplier           3.0x ATR
Trail Start             1.5x ATR
Max Spread              15 pips
```

## File Output
- `BTC_Scalper.mq5` — single file, no dependencies

## Testing Checklist
- [ ] Compile (0 errors, max 2 warnings)
- [ ] Backtest M5, 6 bulan data
- [ ] Demo live test dengan command test
- [ ] Check metrics: sharpe > 1.0, win rate > 50%
