# PRD: BTC Scalping Pro EA v3.0

> **Codename:** "Nightmare" — Aggressive multi-TF scalping for 200% return target
> **Target symbol:** BTCUSD (Exness demo → live)
> **Execution:** MQL5 standalone EA, no Python dependency

---

## Ringkasan

EA scalping profesional untuk BTC/USD dengan arsitektur **5-layer gate filter** (H4→H1→M30→M15→M5) dan **3 mode strategi** yang bisa dipilih. Semua logika embedded di MQL5 — tinggal compile, attach, trading.

Target return: **200% dalam 2-6 bulan** via compounding, tergantung mode dan kondisi market.

---

## Spesifikasi Teknis

| Item | Value |
|------|-------|
| Symbol | BTCUSD |
| Entry TF | M5 |
| Confirmation TF | M15, M30, H1, H4 |
| Akun | Exness demo → live |
| Magic Number | 20260517 |
| Execution | Market order (IOC) |
| Max Spread | 20 pts (dynamic, wider on weekend) |
| Compounding | ✅ Active (auto-scale every +10% balance) |

---

## Arsitektur: 5-Layer Gate Filter

Setiap layer harus **PASS semua** sebelum entry dieksekusi. Ini mengurangi noise dan meningkatkan kualitas sinyal.

| Gate | TF | Filter | Logic |
|------|-----|--------|-------|
| **G1** | H4 | Major Structure | Price > EMA50 → bullish bias; Price < EMA50 → bearish bias. H4 candle direction confirm. |
| **G2** | H1 | Key S/R + ADX | ADX(14) > threshold; Price respect S/R (prev day high/low, weekly pivot). No counter-trend entry within 0.5 ATR of S/R. |
| **G3** | M30 | EMA Cascade | EMA8 > EMA21 > EMA34 (bullish) OR EMA8 < EMA21 < EMA34 (bearish). All 3 must align. |
| **G4** | M15 | Entry Zone | RSI(5) 30-60 for buy, 40-70 for sell. Volume > avg × threshold. Not in chop zone (ADX check). |
| **G5** | M5 | Trigger Pattern | ≥1 of: Pinbar, Bullish/Bearish Engulfing, BB Bounce, BB Squeeze Breakout, EMA Retest. Volume confirm. |

**Entry hanya terjadi kalo G1-G4 hijau, lalu G5 trigger.**

---

## 3 Mode Strategi

User bisa pilih mode via input parameter `StrategyMode`.

### Mode 1: CONSERVATIVE
```
Target: Win rate 65-75%, trades 1-3/hari, monthly return 8-15%
┌─────────────────────────────────────────────┐
│ ADX threshold: 22                    
│ EMA alignment: 5/5 (M5+M15+M30+H1 EMA full cascade)
│ Entry trigger:  Pinbar OR BB Bounce only
│ Volume:         > 1.3x avg
│ Risk/trade:     0.5%
│ Max positions:  2
│ Pyramiding:     Disabled
│ TP levels:      2 level (40% at 1.5x ATR, 60% trail)
│ Compounding:    Weekly
└─────────────────────────────────────────────┘
```

### Mode 2: BALANCED *(default)*
```
Target: Win rate 55-65%, trades 3-7/hari, monthly return 15-30%
┌─────────────────────────────────────────────┐
│ ADX threshold: 18                    
│ EMA alignment: 4/5 (M15 EMA cascade + H1 EMA)
│ Entry trigger:  Pinbar / Engulfing / BB Bounce
│ Volume:         > 1.1x avg
│ Risk/trade:     0.5%
│ Max positions:  3
│ Pyramiding:     up to 2 per direction (profit-triggered)
│ TP levels:      3 level (40% at 1.5x, 30% at 2.5x, 30% trail)
│ Compounding:    Daily (auto-scale every +10%)
└─────────────────────────────────────────────┘
```

### Mode 3: AGGRESSIVE
```
Target: Win rate 45-55%, trades 7-15/hari, monthly return 25-50%
┌─────────────────────────────────────────────┐
│ ADX threshold: 15                    
│ EMA alignment: 3/5 (M15 only)
│ Entry trigger:  Pinbar / Engulfing / BB Bounce / Retest / Breakout
│ Volume:         > 1.0x avg (any volume OK)
│ Risk/trade:     0.7%
│ Max positions:  4
│ Pyramiding:     up to 3 per direction (first win-triggered)
│ TP levels:      3 level (40% at 1.5x, 35% at 2.5x, 25% trail)
│ Compounding:    Daily (auto-scale every +5%)
└─────────────────────────────────────────────┘
```

---

## Entry Triggers (G5 Patterns)

| Pattern | Condition | Direction | Weight |
|---------|-----------|-----------|--------|
| **Pinbar** | Body < 30% range, tail rejection | Bullish: low < prev low & close > open; Bearish: high > prev high & close < open | High |
| **Engulfing** | Current body > prev body, full engulf | Bullish: prev red, current green, engulfs; Bearish: prev green, current red, engulfs | High |
| **BB Bounce** | Touch/reject lower band + close above | Lower bounce (buy), Upper rejection (sell) | Medium |
| **BB Squeeze Breakout** | Band width <1% then expand +50% | Break above upper (buy), break below lower (sell) | Medium |
| **EMA Retest** | Break EMA then pullback to EMA area | Buy: bounce off EMA support; Sell: rejection from EMA resistance | Low-Med |
| **Cascade Confirm** | All 3 EMA aligned + close above/below | EMA8>EMA21>EMA34 with close above all (buy); opposite (sell) | Low |

---

## Exit & Risk Management

### Take Profit (3-Level Split)
```
TP1: 40% of position → 1.5x ATR from entry
TP2: 30-35% of position → 2.5x ATR from entry
TP3: 25-30% of position → Trailing only (no fixed TP)

After TP1 hits: SL moves to breakeven
After TP2 hits: SL moves to TP1 level
```

### Stop Loss
```
SL = entry ± (SL_ATR_Mult × weighted_ATR)
weighted_ATR = 0.7 × ATR(M5) + 0.3 × ATR(M15)

Conservative: SL_ATR_Mult = 2.5
Balanced:     SL_ATR_Mult = 2.0
Aggressive:   SL_ATR_Mult = 1.8 (tighter — more volatile mode)
```

### Trailing
```
Start after TP1 is hit and TP2 distance reached
Trail distance = Trail_ATR × ATR(M5)
Trailing increment: 0.2 ATR (smooth, avoid whipsaw)
```

### Circuit Breaker
```
┌──────────────────────────────────────────┐
│ HARD DD:        12% total DD → close all, ExpertRemove
│ DAILY MAX LOSS:  5% → close all, pause rest of day
│ SOFT BREAKER:    2 consecutive losses → half lot size
│ COOLDOWN:        4 consecutive losses → pause 60 min
│ SPREAD GATE:     >30 pts → no entry
│ WEEKEND MODE:    Saturday/Sunday → half risk, tighter filters
└──────────────────────────────────────────┘
```

---

## HTF Support/Resistance (G2)

Ambil otomatis dari:
1. **Previous Day High/Low** (D1 candle -1)
2. **Current Day Open** (D1 candle 0)
3. **Weekly Pivot Points:** `(H+L+C)/3`, `R1=2P-L`, `S1=2P-H`

Rules:
- No BUY within 0.5 ATR(H1) of resistance
- No SELL within 0.5 ATR(H1) of support
- Full trade allowed in breakout zone (price > R1 or < S1)

---

## Compounding Engine

```
Every time account balance reaches next milestone (+10%), lot size auto-scales.

Milestone:         Lot multiplier:
Start (100%)       1.0x
+10% (110%)        1.1x
+20% (121%)        1.2x
+30% (133%)        1.3x
...                ...

On drawdown, lot scales down proportionally (never below 0.5x base).
```

---

## Special Filters

| Filter | Description | Mode |
|--------|-------------|------|
| **Weekend Mode** | Sat/Sun UTC → half risk, ADX+4, volume ×1.3 | All |
| **First 15 Min** | Skip first 900s of daily candle (rollover chaos) | All |
| **Anti-Chop** | Last 20 candles range < 1 ATR AND ADX < 15 → skip | Balanced, Aggressive |
| **Volatility Scale** | Current ATR > 2x historical avg → half risk. Current ATR < 0.5x → 1.3x risk (capped) | Balanced |
| **News Pause** | 15 min before/after USD high-impact news → pause (read from Common/Files/news.json) | Optional |
| **Session Volume** | UTC 6-22 (London+US) → normal; outside → tighter filters | Optional |

---

## Risk Parameters

```
Parameter              Conservative  Balanced   Aggressive
──────────────────────────────────────────────────────────
Max Daily Loss         5%            5%         5%
Max Total DD           10%           12%        15%
Max Consecutive Loss   3 strikes     4 strikes  4 strikes
Cooldown After Streak  90 min        60 min     30 min
Hard DD (kill switch)  10%           12%        15%
Soft Breaker           2 losses      2 losses   2 losses
Risk per Trade         0.5%          0.5%       0.7%
Max Spread             20 pts        25 pts     30 pts
Weekend Risk           Normal        0.5x       0.5x
```

---

## Performance Targets

| Target | Conservative | Balanced | Aggressive |
|--------|-------------|----------|------------|
| Win Rate | 65-75% | 55-65% | 45-55% |
| Avg RR | 1:2.5+ | 1:2.0+ | 1:1.5+ |
| Trades/Day | 1-3 | 3-7 | 7-15 |
| Monthly ROI | 8-15% | 15-30% | 25-50% |
| 100% Return | ~8 bulan | ~4 bulan | ~2 bulan |
| 200% Return | ~14 bulan | ~7 bulan | ~4 bulan |
| Max Drawdown | <10% | <15% | <20% |
| Sharpe Ratio | >2.0 | >1.5 | >1.0 |

*200% dalam 2 bulan possible di mode Aggressive dengan market trending kuat + zero extended loss streak. Tapi realitistis ekspektasi 4-6 bulan.*

---

## BTC_Scalper_Pro v3.0 — Full Spec vs v2.0

| Feature | v2.0 | v3.0 |
|---------|------|------|
| TF layers | M5 entry + M15/M30 trend check | H4→H1→M30→M15→M5 5-layer gate |
| Strategy modes | 1 fixed | 3 selectable (Conservative/Balanced/Aggressive) |
| Entry patterns | Pinbar + BB Bounce | Pinbar / Engulfing / BB Bounce / Squeeze Breakout / Retest |
| EMA cascade | 3 (M5: 8/21/34) | 4 levels (M15: 8/21/34 + H1: 21 + H4: 50) |
| BB buffers | ❌ Bug — all read middle | ✅ Correct buffer index |
| Indicator handles | Leak (create/destroy per tick) | ✅ Created once in OnInit |
| softBreak | Defined but never active | ✅ Activates after 2 losses |
| Pyramiding | Stub (2 max, both directions) | ✅ 2-3 per direction, profit-triggered |
| TP levels | 2 (50/50) | ✅ 3 (40/35/25) with runner |
| Compounding | None | ✅ Auto every +10% |
| HTF S/R | None | ✅ Prev day H/L/O + weekly pivot |
| Weekend mode | None | ✅ Half risk + tighter filters |
| Anti-chop | None | ✅ Skip during tight range |
| Volatility scaling | None | ✅ Dynamic based on ATR vs historical |
| Rollover filter | None | ✅ Skip first 15min of daily candle |
| News filter | Stub | ✅ Optional via news.json |
| Performance logging | None | ✅ CSV log per trade with entry reason |
| Spread tracking | None | ✅ Rolling average + alert |
| Max positions | 2 total | 2-4 concurrent, 2-3 same direction |

---

## File Output

- `BTC_Scalper_Pro.mq5` — Main EA file (~600-700 lines)
- `docs/BTC_SCALPER_IMPROVEMENT.md` — Detailed improvement prompt
- `trade/mt5_data/MQL5/Profiles/Tester/BTC_Scalper_Pro*.ini` — Tester configs

---

## Testing Checklist

- [ ] Compile (0 errors, max 3 warnings)
- [ ] Backtest M5, 6 bulan, mode Balanced
- [ ] Backtest M5, 6 bulan, mode Aggressive
- [ ] Verify BB buffers correct (compare with chart)
- [ ] Verify softBreak activates after 2 losses
- [ ] Verify pyramiding limit (2-3 same direction)
- [ ] Verify compounding lot-size scale
- [ ] Verify weekend mode applies correctly
- [ ] Demo live test, 1 minggu
- [ ] Review entry reason logs
- [ ] Optimize per mode based on live data

---

## Success Criteria

```
✅ Backtest:   Win rate >55% | PF >1.5 | Sharpe >1.2
✅ Demo live:  3-7 trades/day (Balanced) | 7-15 (Aggressive)
✅ Risk:       Max DD stays within target per mode
✅ Bugs:       BB fix verified, softBreak functional, no handle leak
✅ Quality:    Compile error-free, clean logs, readable
```
