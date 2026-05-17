# BTC_Scalper_Pro v2.1 — Improvement Prompt

> Target: Fix bugs + make strategy more aggressive + professional-grade execution
> Source: BTC_Scalper_Pro.mq5 (528 lines, v2.0)

---

## 🔴 CRITICAL BUGS (Fix First)

### 1. Bollinger Bands Buffer Index Wrong
**Problem:** All 3 BB handles (`hBB_upper`, `hBB_lower`, `hBB_mid`) are created identically AND read using `GetBuf(handle, 0, 0)` → everything reads buffer 0 (BASE_LINE/middle band). BB squeeze and bounce detection are broken.

**Fix:**
```cpp
// In OnInit: create ONE handle, use buffer index when reading
hBB = iBands("BTCUSD", PERIOD_M5, BBPeriod, 0, BBDeviation, PRICE_CLOSE);

// In ScanEntry: read correct buffers
double bbMid   = GetBuf(hBB, 0, 0);  // Buffer 0 = BASE_LINE
double bbUpper = GetBuf(hBB, 1, 0);  // Buffer 1 = UPPER_BAND
double bbLower = GetBuf(hBB, 2, 0);  // Buffer 2 = LOWER_BAND
```
Also update OnDeinit: only release one `hBB` handle.

### 2. softBreak Never Activated
**Problem:** Variable `softBreak` is declared, checked (halves lot size), but NEVER set to true anywhere.

**Fix:** Activate after 2 consecutive losses:
```cpp
// In OpenTrade, on failure:
if(res.retcode != TRADE_RETCODE_DONE) {
    lossStreak++;
    if(lossStreak >= 2) softBreak = true;  // ← ADD THIS
}

// Reset softBreak after a win:
if(res.retcode == TRADE_RETCODE_DONE) {
    lossStreak = 0;
    if(softBreak) softBreak = false;  // ← ADD THIS
    todayTrades++;
}
```

### 3. EMA Cascade Conditions Unused
**Problem:** `buyMcrx` and `sellMcrx` are calculated but never used in the final entry condition.

**Fix:** Integrate into entry logic — entry requires at least ONE of: pinbar OR BB bounce OR EMA cascade:
```cpp
bool buyConfirm = (buyPin || buyBBSqz || buyMcrx) && volOK;
bool sellConfirm = (sellPin || sellBBSqz || sellMcrx) && volOK;
```

---

## 🟡 PERFORMANCE ISSUES

### 4. Indicator Handle Create/Destroy Every Tick
**Problem:** `Refresh()` and `ScanEntry()` create M15/M30 iMA/iATR/iADX handles, use once, then `IndicatorRelease()`. This happens ~288 times/day (every M5 bar) — major CPU waste.

**Fix:** Move ALL handles to OnInit:
```cpp
// Add to globals:
int hATR15, hATR30, hEMA15, hEMA30, hADX15, hADX30;

// Create in OnInit:
hATR15 = iATR("BTCUSD", PERIOD_M15, 14);
hEMA15 = iMA("BTCUSD", PERIOD_M15, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
hADX15 = iADX("BTCUSD", PERIOD_M15, ADXPeriod);
hEMA30 = iMA("BTCUSD", PERIOD_M30, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
hADX30 = iADX("BTCUSD", PERIOD_M30, ADXPeriod);

// Release in OnDeinit (add all 5 new handles)
// Use directly in ScanEntry/Refresh — NO create/release cycle
```

### 5. Refresh() Creates Extra ATR Handle
**Problem:** `Refresh()` already creates its own `hATR15` every call, conflicting with fix #4.

**Fix:** Remove handle creation from Refresh(), use the global `hATR15` from OnInit.

---

## 🟢 STRATEGY ENHANCEMENTS (More Aggressive)

### 6. Add Two Entry Modes
**Problem:** Current settings are "sniper" — super selective. Need configurable aggression.

**Add input:**
```cpp
input group "═══ Strategy Mode ═══"
input ENUM_STRATEGY_MODE StrategyMode = MODE_BALANCED;
// MODE_CONSERVATIVE: ADX>22, all 5 EMA aligned, only pinbar + volume>1.3x
// MODE_BALANCED:     ADX>18, 4/5 EMA aligned, pinbar/pin+BB/rejection
// MODE_AGGRESSIVE:   ADX>15, 3/5 EMA aligned, pinbar/BB/EMA cascade/breakout
```

**Implementation:** Use a switch/case at the start of ScanEntry to override filter thresholds.

### 7. Lower ADX Filter
Current `ADXThreshold = 22` filters out too much (BTC sideways ~40% of time).

**New defaults per mode:**
- Conservative: 22 (unchanged)
- Balanced: 18
- Aggressive: 15

### 8. Add Breakout Entry
After BB squeeze (bands narrow → volatility expansion), enter on breakout direction:
```cpp
// BB squeeze detection
double bbWidth = (bbUpper - bbLower) / bbMid;
static double bbWidthPrev = 0;
bool isSqueezing = (bbWidth < 0.01 && bbWidthPrev < 0.01); // <1% width
bool breakout = (isSqueezing && bbWidth > bbWidthPrev * 1.5); // expansion

// Entry on breakout
bool buyBreakout  = breakout && close > bbUpper && close1 <= bbUpper;
bool sellBreakout = breakout && close < bbLower && close1 >= bbLower;
```

### 9. Add Engulfing Pattern
**Problem:** Only pinbar is used for PA. Add engulfing for more entries.
```cpp
// Bullish engulfing
bool prevRed  = (close2 < open2);
bool currGreen = (close1 > open1);
bool engulfBody = (body > MathAbs(close2 - open2));
bool bullEngulf = prevRed && currGreen && engulfBody && close1 > open2 && open1 < close2;

// Bearish engulfing  
bool prevGreen = (close2 > open2);
bool currRed   = (close1 < open1);
bool bearEngulf = prevGreen && currRed && engulfBody && close1 < open2 && open1 > close2;
```

### 10. Add Retest Entry
When price retests a recently broken level (EMA, BB, recent high/low):
```cpp
// Buy: price breaks above EMA, pulls back, holds above EMA
bool buyRetest = (close1 > ema && close < ema * 1.002 && close > ema * 0.998 && rsi > 35);

// Sell: price breaks below EMA, rallies, holds below EMA
bool sellRetest = (close1 < ema && close > ema * 0.998 && close < ema * 1.002 && rsi < 65);
```

---

## 🔵 FEATURES TO ADD

### 11. Pyramiding (True Multi-Position)
**Current:** `MaxPositions = 2` limits to one buy + one sell.

**New behavior:**
- After first position is in profit (≥1 ATR), allow second entry in same direction
- Max 3 positions same direction
- Each pyramid uses 50% of remaining risk budget
- Positions managed independently (different SL/TP levels)

```cpp
int CountPositionsByType(int type) {
    int count = 0;
    for(int i=0; i<PositionsTotal(); i++) {
        if(PositionGetTicket(i) && PositionGetInteger(POSITION_MAGIC)==Magic) {
            if(PositionGetInteger(POSITION_TYPE)==type) count++;
        }
    }
    return count;
}

// In ScanEntry:
int sameDir = CountPositionsByType(dir);
if(sameDir >= 3) return;  // Max 3 pyramids
if(sameDir >= 1) {
    // Only pyramid if first position is profitable
    if(!IsFirstPositionProfitable(dir)) return;
}
```

### 12. News Filter (Stub → Real)
**Current:** PRD mentions news filter but code has none.

**Implementation:**
```cpp
input bool UseNewsFilter = false;

bool IsNewsTime() {
    if(!UseNewsFilter) return false;
    // Check against hardcoded high-impact USD event schedule
    // Or read from a JSON file in Common/Files (like XAU Executor)
    // Return true if within 15min of high-impact event
    return false; // Stub — implement when data source ready
}
```

### 13. Session-Based Volume Filter
BTC volume peaks during London-US overlap (13:00-17:00 WIB / 06:00-10:00 UTC). Add session awareness:

```cpp
input bool UseSessionFilter = false;
input int SessionStartUTC = 6;   // London open
input int SessionEndUTC   = 22;  // US close

bool IsHighVolumeSession() {
    if(!UseSessionFilter) return true;
    MqlDateTime dt; TimeCurrent(dt);
    return (dt.hour >= SessionStartUTC && dt.hour < SessionEndUTC);
}
```

### 14. Performance Logging
Keep a running stats log for analysis:

```cpp
struct TradeStats {
    int total, wins, losses;
    double totalProfit, maxWin, maxLoss;
    double avgRR, winRate;
};

// Write to CSV in Common/Files/btc_performance.csv after each trade
// Format: ticket,type,entry,exit,profit,reason,timestamp
```

### 15. Spread Tracker
Log spread every 100 ticks, alert if abnormal:

```cpp
static int tickCount = 0;
static double spreadSum = 0, spreadMax = 0;
tickCount++;
spreadSum += spread;
if(spread > spreadMax) spreadMax = spread;

if(tickCount >= 100) {
    double avgSpread = spreadSum / tickCount;
    if(avgSpread > 30) Print("⚠️ High avg spread: ", avgSpread, " pts");
    tickCount = 0; spreadSum = 0;
}
```

---

## 📋 IMPLEMENTATION ORDER

| Priority | # | Task | Effort | Impact |
|----------|---|------|--------|--------|
| 🔴 | 1 | Fix BB buffer index | 5 min | Critical |
| 🔴 | 2 | Activate softBreak | 3 min | Medium |
| 🔴 | 3 | Integrate EMA cascade | 2 min | Medium |
| 🟡 | 4 | Move handles to OnInit | 15 min | High (perf) |
| 🟡 | 5 | Clean up Refresh() | 3 min | Low |
| 🟢 | 6 | Add strategy modes | 20 min | High (trades) |
| 🟢 | 7 | Lower ADX defaults | 1 min | High (trades) |
| 🟢 | 8 | Add breakout entry | 15 min | High (trades) |
| 🟢 | 9 | Add engulfing PA | 10 min | Medium |
| 🟢 | 10 | Add retest entry | 10 min | Medium |
| 🔵 | 11 | Pyramiding (3x) | 25 min | High (profit) |
| 🔵 | 12 | News filter stub | 10 min | Low (future) |
| 🔵 | 13 | Session filter | 5 min | Low |
| 🔵 | 14 | Performance log | 20 min | Med (analysis) |
| 🔵 | 15 | Spread tracker | 5 min | Low |

**Total: ~2 hours** for all fixes and enhancements.

---

## 🎯 Success Criteria

After implementation, the EA should:
- **Minimal 5 trades/day** in Balanced mode (currently 5/month)
- **Win rate ≥ 55%** maintained
- **Profit Factor ≥ 1.8** maintained
- **Max DD < 10%** maintained
- **Handle count** stays constant (no per-tick create/destroy)
- **softBreak** activates after 2 losses, halves risk, resets on win
- **BB squeeze breakout** generates entry signals
- **All 3 entry patterns** (pinbar, BB bounce, EMA cascade) active in entry logic

---

## 🔗 Related Files
- Source: `mql5_ea/BTC_Scalper_Pro.mq5`
- PRD: `docs/BTC_SCALPER_PRD.md`
- Progress tracker: `PROGRESS.md`
- GitHub: `github.com/adhitamaw/Clawbot-trading`
