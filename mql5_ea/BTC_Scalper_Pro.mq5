//+------------------------------------------------------------------+
//|  BTC_Scalper_Pro.mq5 — v3.0 "Nightmare"                          |
//|  BTC/USD Professional Scalping EA — M5 Execution                  |
//|  5-Layer Gate Filter (H4→H1→M30→M15→M5)                          |
//|  3 Strategy Modes + Compounding + 6 Entry Patterns                |
//|  Author: Dasha + Adhit | Repo: github.com/adhitamaw/Clawbot-trading |
//+------------------------------------------------------------------+
#property copyright "BTC Scalping Pro v3.0"
#property version   "3.00"
#property description "BTC/USD Pro Scalper — M5 Entry, 5-Layer Gate"
#property description "Modes: Conservative | Balanced | Aggressive"
#property description "6 Patterns: Pinbar, Engulfing, BB, Squeeze, Retest, Cascade"

// ═══════════════════════════════════════════════════════════════════
// INPUT GROUPS
// ═══════════════════════════════════════════════════════════════════

input group "═══ Strategy Mode ═══"
enum ENUM_STRATEGY_MODE { MODE_CONSERVATIVE=0, MODE_BALANCED=1, MODE_AGGRESSIVE=2 };
input ENUM_STRATEGY_MODE StrategyMode = MODE_BALANCED;

input group "═══ Core Settings ═══"
input int      Magic          = 20260517;
input double   RiskPercent    = 0.5;
input double   FixedLot       = 0.0;
input int      MaxPositions   = 3;
input string   TradeSymbol    = "BTCUSD";

input group "═══ Entry Filters (M5) ═══"
input int      ADXPeriod      = 14;
input double   ADXThreshold   = 18.0;
input int      RSIPeriod      = 5;
input int      RSI_Low        = 30;
input int      RSI_High       = 70;
input int      BBPeriod       = 20;
input double   BBDeviation    = 2.0;

input group "═══ EMA Cascade ═══"
input int      EMA8_Period    = 8;
input int      EMA21_Period   = 21;
input int      EMA34_Period   = 34;
input int      EMA50_Period   = 50;
input double   MinVolumeMult  = 1.1;

input group "═══ Risk & Exit ═══"
input double   SL_ATR_Mult    = 2.0;
input double   TP1_ATR_Mult   = 1.5;
input double   TP2_ATR_Mult   = 2.5;
input double   PartialPct1    = 40.0;
input double   PartialPct2    = 35.0;
input double   TrailStart     = 2.0;
input double   TrailStep      = 0.2;

input group "═══ Circuit Breaker ═══"
input double   MaxDailyLoss   = 5.0;
input double   MaxTotalDD     = 12.0;
input int      MaxLossStreak  = 4;
input int      CoolDownMin    = 60;
input int      SoftBreakAfter = 2;

input group "═══ Pyramiding ═══"
input int      MaxPyramid     = 3;
input double   PyramidProfitATR = 1.2;

input group "═══ Time & Session ═══"
input bool     UseTimeFilter  = false;
input int      SessionStartUTC= 6;
input int      SessionEndUTC  = 22;
input bool     UseWeekendDefense = true;

input group "═══ Compounding ═══"
input bool     UseCompounding = true;
input double   CompoundPct    = 10.0;

// ═══════════════════════════════════════════════════════════════════
// GLOBALS
// ═══════════════════════════════════════════════════════════════════
datetime lastM5   = 0;
double   atrM5    = 0;
double   atrM15   = 0;
double   atrH1    = 0;
int      lossStreak= 0;
datetime pauseUntil= 0;
double   dailyPL  = 0;
double   startBal = 0;
double   peakBal  = 0;
double   totalDD  = 0;
int      todayTrades = 0;
bool     softBreak= false;
double   compoundMilestone = 0;

// ── Indicator Handles (all created in OnInit, released in OnDeinit) ─
int hADX5, hRSI5, hBB, hATR5, hATR15, hATRH1;
int hEMA8, hEMA21, hEMA34, hEMA50;
int hEMA50_H4, hADX_H1, hEMA21_H1;
int hEMA8_M30, hEMA21_M30, hEMA34_M30, hADX_M30;

// ── Prev bar data for PA ─
double prevO=0, prevH=0, prevL=0, prevC=0;
double prev2O=0, prev2H=0, prev2L=0, prev2C=0;

// ═══════════════════════════════════════════════════════════════════
// ONINIT — Create all indicator handles ONCE
// ═══════════════════════════════════════════════════════════════════
int OnInit()
{
   string sym = TradeSymbol;
   SymbolSelect(sym, true);

   startBal = AccountInfoDouble(ACCOUNT_BALANCE);
   peakBal  = startBal;
   compoundMilestone = startBal;

   // M5 handles
   hADX5   = iADX(sym, PERIOD_M5, ADXPeriod);
   hRSI5   = iRSI(sym, PERIOD_M5, RSIPeriod, PRICE_CLOSE);
   hBB     = iBands(sym, PERIOD_M5, BBPeriod, 0, BBDeviation, PRICE_CLOSE);
   hATR5   = iATR(sym, PERIOD_M5, 14);
   hEMA8   = iMA(sym, PERIOD_M5, EMA8_Period, 0, MODE_EMA, PRICE_CLOSE);
   hEMA21  = iMA(sym, PERIOD_M5, EMA21_Period, 0, MODE_EMA, PRICE_CLOSE);
   hEMA34  = iMA(sym, PERIOD_M5, EMA34_Period, 0, MODE_EMA, PRICE_CLOSE);
   hEMA50  = iMA(sym, PERIOD_M5, EMA50_Period, 0, MODE_EMA, PRICE_CLOSE);

   // M15 handles
   hATR15  = iATR(sym, PERIOD_M15, 14);
   hEMA8_M30  = iMA(sym, PERIOD_M30, EMA8_Period, 0, MODE_EMA, PRICE_CLOSE);
   hEMA21_M30 = iMA(sym, PERIOD_M30, EMA21_Period, 0, MODE_EMA, PRICE_CLOSE);
   hEMA34_M30 = iMA(sym, PERIOD_M30, EMA34_Period, 0, MODE_EMA, PRICE_CLOSE);
   hADX_M30   = iADX(sym, PERIOD_M30, ADXPeriod);

   // H1 handles
   hATRH1  = iATR(sym, PERIOD_H1, 14);
   hADX_H1 = iADX(sym, PERIOD_H1, ADXPeriod);
   hEMA21_H1 = iMA(sym, PERIOD_H1, EMA21_Period, 0, MODE_EMA, PRICE_CLOSE);

   // H4 handle
   hEMA50_H4 = iMA(sym, PERIOD_H4, EMA50_Period, 0, MODE_EMA, PRICE_CLOSE);

   Print("🚀 BTC_Scalper_Pro v3.0 | Mode: ", StrategyMode==0?"CONSERVATIVE":(StrategyMode==1?"BALANCED":"AGGRESSIVE"),
         " | Risk: ", RiskPercent, "% | MaxDD: ", MaxTotalDD, "%");
   return(INIT_SUCCEEDED);
}

// ═══════════════════════════════════════════════════════════════════
void OnDeinit(const int reason)
{
   IndicatorRelease(hADX5); IndicatorRelease(hRSI5); IndicatorRelease(hBB);
   IndicatorRelease(hATR5); IndicatorRelease(hATR15); IndicatorRelease(hATRH1);
   IndicatorRelease(hEMA8); IndicatorRelease(hEMA21); IndicatorRelease(hEMA34);
   IndicatorRelease(hEMA50); IndicatorRelease(hEMA50_H4);
   IndicatorRelease(hADX_H1); IndicatorRelease(hEMA21_H1);
   IndicatorRelease(hEMA8_M30); IndicatorRelease(hEMA21_M30); IndicatorRelease(hEMA34_M30);
   IndicatorRelease(hADX_M30);
}

// ═══════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════
double GetBuf(int handle, int buffer=0, int shift=0)
{
   double buf[1];
   if(CopyBuffer(handle, buffer, shift, 1, buf) > 0) return buf[0];
   return 0;
}

int CountPosByType(int type)
{
   int c=0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=Magic) continue;
      if(PositionGetInteger(POSITION_TYPE)==type) c++;
   }
   return c;
}

int CountAllPos()
{
   int c=0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if(PositionGetInteger(POSITION_MAGIC)==Magic) c++;
   }
   return c;
}

bool IsNewBar() { datetime b=iTime(TradeSymbol,PERIOD_M5,0); if(b!=lastM5){lastM5=b;return true;} return false; }

bool IsWeekend()
{
   MqlDateTime dt; TimeCurrent(dt);
   return (dt.day_of_week==0 || dt.day_of_week==6);
}

bool IsRolloverWindow()
{
   datetime dayOpen = iTime(TradeSymbol, PERIOD_D1, 0);
   return (TimeCurrent() - dayOpen < 900); // First 15 min
}

bool IsHighVolumeSession()
{
   MqlDateTime dt; TimeCurrent(dt);
   return (dt.hour >= SessionStartUTC && dt.hour < SessionEndUTC);
}

// ═══════════════════════════════════════════════════════════════════
// ADAPTIVE THRESHOLDS (per mode)
// ═══════════════════════════════════════════════════════════════════
double GetADXThresh() {
   if(IsWeekend()) return ADXThreshold + 3;
   switch(StrategyMode) {
      case MODE_CONSERVATIVE: return 22;
      case MODE_BALANCED:     return 18;
      case MODE_AGGRESSIVE:   return 15;
   }
   return ADXThreshold;
}

double GetVolThresh() {
   if(IsWeekend()) return 1.3;
   switch(StrategyMode) {
      case MODE_CONSERVATIVE: return 1.3;
      case MODE_BALANCED:     return 1.1;
      case MODE_AGGRESSIVE:   return 1.0;
   }
   return MinVolumeMult;
}

int GetMaxPos() {
   switch(StrategyMode) {
      case MODE_CONSERVATIVE: return 2;
      case MODE_BALANCED:     return MaxPositions;   // 3
      case MODE_AGGRESSIVE:   return 4;
   }
   return MaxPositions;
}

int GetMaxPyramid() {
   switch(StrategyMode) {
      case MODE_CONSERVATIVE: return 1;
      case MODE_BALANCED:     return 2;
      case MODE_AGGRESSIVE:   return MaxPyramid;     // 3
   }
   return MaxPyramid;
}

double GetRisk() {
   if(softBreak) return RiskPercent * 0.5;
   if(IsWeekend() && UseWeekendDefense) return RiskPercent * 0.5;
   // Volatility scaling
   double atr14h = GetBuf(hATRH1, 0, 0);
   if(atrH1 > 0 && atr14h > 0 && atrH1 > atr14h * 2.0) return RiskPercent * 0.5;
   return RiskPercent;
}

// ═══════════════════════════════════════════════════════════════════
// ONTICK
// ═══════════════════════════════════════════════════════════════════
void OnTick()
{
   CheckRisk();
   CompoundingCheck();
   if(TimeCurrent() < pauseUntil) return;
   if(!IsNewBar()) return;

   // Weekend defense mode
   if(UseWeekendDefense && IsWeekend())
   {
      // Allow trading but tighter filters (handled in GetADXThresh etc.)
   }

   // Rollover window skip
   if(StrategyMode != MODE_AGGRESSIVE && IsRolloverWindow()) return;

   // Spread check
   string sym = TradeSymbol;
   double spread = (SymbolInfoDouble(sym, SYMBOL_ASK) - SymbolInfoDouble(sym, SYMBOL_BID))
                   / SymbolInfoDouble(sym, SYMBOL_POINT);
   if(spread > 30) return;

   Refresh();
   ManagePositions();
   ScanEntry();
}

// ═══════════════════════════════════════════════════════════════════
// REFRESH — Update all ATR values + capture prev bar PA data
// ═══════════════════════════════════════════════════════════════════
void Refresh()
{
   atrM5  = GetBuf(hATR5, 0, 0);
   atrM15 = GetBuf(hATR15, 0, 0);
   atrH1  = GetBuf(hATRH1, 0, 0);

   // Prev bar PA data
   prevO = iOpen(TradeSymbol, PERIOD_M5, 1);
   prevH = iHigh(TradeSymbol, PERIOD_M5, 1);
   prevL = iLow(TradeSymbol, PERIOD_M5, 1);
   prevC = iClose(TradeSymbol, PERIOD_M5, 1);

   prev2O = iOpen(TradeSymbol, PERIOD_M5, 2);
   prev2H = iHigh(TradeSymbol, PERIOD_M5, 2);
   prev2L = iLow(TradeSymbol, PERIOD_M5, 2);
   prev2C = iClose(TradeSymbol, PERIOD_M5, 2);
}

// ═══════════════════════════════════════════════════════════════════
// RISK & CIRCUIT BREAKER
// ═══════════════════════════════════════════════════════════════════
void CheckRisk()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq > peakBal) peakBal = eq;

   static double dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE);
   static int lastDay = 0;
   MqlDateTime dt; TimeCurrent(dt);
   if(dt.day != lastDay) { lastDay = dt.day; dayStartBal = eq; todayTrades = 0; softBreak = false; }

   dailyPL = dayStartBal > 0 ? (eq - dayStartBal) / dayStartBal * 100 : 0;
   totalDD = peakBal > 0 ? (peakBal - eq) / peakBal * 100 : 0;

   // HARD DD — kill switch
   if(totalDD >= MaxTotalDD)
   {
      CloseAll("HARD BREAKER");
      Print("🛑 HARD STOP: DD ", DoubleToString(totalDD,1), "%");
      ExpertRemove();
      return;
   }

   // Daily max loss
   if(dailyPL <= -MaxDailyLoss)
   {
      CloseAll("Daily Max Loss");
      Print("⛔ Daily max loss: ", DoubleToString(dailyPL,1), "%");
      pauseUntil = TimeCurrent() + 86400;
   }

   // Loss streak cooldown
   if(lossStreak >= MaxLossStreak)
   {
      pauseUntil = TimeCurrent() + CoolDownMin * 60;
      Print("⏸ Pause ", CoolDownMin, "m after ", MaxLossStreak, " losses");
      lossStreak = 0;
   }
}

// ═══════════════════════════════════════════════════════════════════
// COMPOUNDING
// ═══════════════════════════════════════════════════════════════════
void CompoundingCheck()
{
   if(!UseCompounding) return;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal >= compoundMilestone * (1 + CompoundPct/100.0))
   {
      compoundMilestone = bal;
      Print("📈 Compounding milestone: $", DoubleToString(bal,2));
   }
}

double GetCompoundingFactor()
{
   if(!UseCompounding) return 1.0;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(startBal <= 0) return 1.0;
   double factor = bal / startBal;
   if(factor < 0.5) factor = 0.5;  // Floor
   return factor;
}

// ═══════════════════════════════════════════════════════════════════
// ENTRY SCAN — 5 LAYER GATE + 6 PATTERNS
// ═══════════════════════════════════════════════════════════════════
void ScanEntry()
{
   if(CountAllPos() >= GetMaxPos()) return;
   if(softBreak && CountAllPos() >= 1) return;

   string sym = TradeSymbol;
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);

   // ── Current bar ──
   double close  = iClose(sym, PERIOD_M5, 0);
   double open   = iOpen(sym, PERIOD_M5, 0);
   double high   = iHigh(sym, PERIOD_M5, 0);
   double low    = iLow(sym, PERIOD_M5, 0);

   // ── M5 Indicators ──
   double rsi5   = GetBuf(hRSI5, 0, 0);
   double ema21  = GetBuf(hEMA21, 0, 0);
   double ema8   = GetBuf(hEMA8, 0, 0);
   double ema34  = GetBuf(hEMA34, 0, 0);
   double adx5   = GetBuf(hADX5, 0, 0);
   double bbMid  = GetBuf(hBB, 0, 0);  // Buffer 0 = BASE LINE
   double bbUp   = GetBuf(hBB, 1, 0);  // Buffer 1 = UPPER BAND  ✅ FIXED
   double bbLo   = GetBuf(hBB, 2, 0);  // Buffer 2 = LOWER BAND  ✅ FIXED

   // ── M30 Indicators ──
   double ema8_m30  = GetBuf(hEMA8_M30, 0, 0);
   double ema21_m30 = GetBuf(hEMA21_M30, 0, 0);
   double ema34_m30 = GetBuf(hEMA34_M30, 0, 0);
   double adxM30    = GetBuf(hADX_M30, 0, 0);

   // ── H1 Indicators ──
   double ema21_h1  = GetBuf(hEMA21_H1, 0, 0);
   double adxH1     = GetBuf(hADX_H1, 0, 0);

   // ── H4 Indicator ──
   double ema50_h4  = GetBuf(hEMA50_H4, 0, 0);

   // ── Volume ──
   long vol0  = iVolume(sym, PERIOD_M5, 0);
   long volSum = 0;
   for(int i=1; i<=20; i++) volSum += iVolume(sym, PERIOD_M5, i);
   double volAvg = volSum / 20.0;
   double volRatio = (volAvg > 0) ? vol0 / volAvg : 1.0;
   double volThresh = GetVolThresh();

   // ── Price Action patterns ──
   double body1   = MathAbs(prevC - prevO);
   double range1  = prevH - prevL;
   double body2   = MathAbs(prev2C - prev2O);
   double range2  = prev2H - prev2L;

   bool isPinBull  = (range1 > 0 && body1 < range1 * 0.3 && prevC > prevO
                      && (prevL < prev2L || prevL < prev2C));
   bool isPinBear  = (range1 > 0 && body1 < range1 * 0.3 && prevC < prevO
                      && (prevH > prev2H || prevH > prev2C));

   bool isEngulfBull = (prev2C < prev2O && prevC > prevO     // prev red, curr green
                        && prevO > prev2C && prevC > prev2O && body1 > body2);
   bool isEngulfBear = (prev2C > prev2O && prevC < prevO     // prev green, curr red
                        && prevO < prev2C && prevC < prev2O && body1 > body2);

   bool isBBbounceBull = (low <= bbLo && close > bbLo);
   bool isBBbounceBear = (high >= bbUp && close < bbUp);

   // BB squeeze breakout
   double bbWidth = (bbUp > 0) ? (bbUp - bbLo) / bbMid : 1;
   static double bbWidthPrev = 0;
   bool isSqueezing = (bbWidth < 0.008 && bbWidthPrev < 0.008);
   bool breakoutBull  = (isSqueezing && close > bbUp && prevC <= bbUp);
   bool breakoutBear  = (isSqueezing && close < bbLo && prevC >= bbLo);
   bbWidthPrev = bbWidth;

   // EMA retest
   bool retestBull = (prevC > ema21 && MathAbs(close - ema21) < atrM5 * 0.3 && close > ema21);
   bool retestBear = (prevC < ema21 && MathAbs(close - ema21) < atrM5 * 0.3 && close < ema21);

   // Volume OK
   bool volOK = (volRatio >= volThresh);

   // ═══════════ GATE 1: H4 MAJOR BIAS ═══════════
   bool g1_bull = (close > ema50_h4);
   bool g1_bear = (close < ema50_h4);

   // ═══════════ GATE 2: H1 TREND + S/R ═══════════
   bool g2_bull = (close > ema21_h1 && adxH1 > 15);
   bool g2_bear = (close < ema21_h1 && adxH1 > 15);

   // HTF S/R check
   double prevDayHigh  = iHigh(sym, PERIOD_D1, 1);
   double prevDayLow   = iLow(sym, PERIOD_D1, 1);
   double curDayOpen   = iOpen(sym, PERIOD_D1, 0);
   bool nearResistance = (high >= prevDayHigh - 0.5 * atrH1);
   bool nearSupport    = (low <= prevDayLow + 0.5 * atrH1);

   // ═══════════ GATE 3: M30 EMA CASCADE ═══════════
   bool g3_bull = (ema8_m30 > ema21_m30 && ema21_m30 > ema34_m30 && close > ema8_m30);
   bool g3_bear = (ema8_m30 < ema21_m30 && ema21_m30 < ema34_m30 && close < ema8_m30);

   // ═══════════ GATE 4: M15 ENTRY ZONE ═══════════
   // Adjusted per mode
   bool g4_bull = (rsi5 > RSI_Low && rsi5 < 65 && volOK);
   bool g4_bear = (rsi5 < RSI_High && rsi5 > 35 && volOK);

   // Anti-chop: tight range + low ADX
   bool isChoppy = false;
   if(StrategyMode != MODE_CONSERVATIVE)
   {
      double range20 = 0;
      for(int j=0; j<20; j++) range20 += iHigh(sym,PERIOD_M5,j) - iLow(sym,PERIOD_M5,j);
      range20 /= 20;
      isChoppy = (range20 < atrM5 && adx5 < 15);
   }

   // ═══════════ GATE 5: M5 PATTERN TRIGGER ═══════════
   // Build composite trigger (any pattern + volume)
   bool buyPattern  = (isPinBull || isEngulfBull || isBBbounceBull || breakoutBull || retestBull);
   bool sellPattern = (isPinBear || isEngulfBear || isBBbounceBear || breakoutBear || retestBear);

   // Cascade-only entry (weaker, active in Balanced+)
   if(StrategyMode != MODE_CONSERVATIVE)
   {
      bool cascadeBull = (ema8 > ema21 && ema21 > ema34 && close > ema8 && volOK);
      bool cascadeBear = (ema8 < ema21 && ema21 < ema34 && close < ema8 && volOK);
      buyPattern  = buyPattern  || cascadeBull;
      sellPattern = sellPattern || cascadeBear;
   }

   // ── Assemble final signal ──
   double thresh = GetADXThresh();
   bool trending = (adx5 > thresh || adxM30 > thresh * 0.8);

   // Per-mode gate strictness
   bool g3_pass_bull = g3_bull, g3_pass_bear = g3_bear;
   if(StrategyMode == MODE_AGGRESSIVE)
   {
      // Relaxed: M15 alignment only
      g3_pass_bull = (close > ema21_m30);
      g3_pass_bear = (close < ema21_m30);
   }
   else if(StrategyMode == MODE_CONSERVATIVE)
   {
      // Strict: also require no near resistance
      g3_pass_bull = g3_bull && !nearResistance;
      g3_pass_bear = g3_bear && !nearSupport;
   }

   // ── BUY SIGNAL ──
   if(g1_bull && g2_bull && g3_pass_bull && g4_bull && trending && buyPattern && !isChoppy
      && !nearResistance && CountPosByType(POSITION_TYPE_BUY) < GetMaxPyramid())
   {
      // Pyramid check: if already long, only add if first is profitable
      if(CountPosByType(POSITION_TYPE_BUY) > 0)
      {
         if(!AnyPositionProfitable(POSITION_TYPE_BUY, PyramidProfitATR)) return;
      }
      OpenTrade(ORDER_TYPE_BUY, "BUY");
   }

   // ── SELL SIGNAL ──
   if(g1_bear && g2_bear && g3_pass_bear && g4_bear && trending && sellPattern && !isChoppy
      && !nearSupport && CountPosByType(POSITION_TYPE_SELL) < GetMaxPyramid())
   {
      if(CountPosByType(POSITION_TYPE_SELL) > 0)
      {
         if(!AnyPositionProfitable(POSITION_TYPE_SELL, PyramidProfitATR)) return;
      }
      OpenTrade(ORDER_TYPE_SELL, "SELL");
   }
}

// ═══════════════════════════════════════════════════════════════════
bool AnyPositionProfitable(int type, double profitATR)
{
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=Magic) continue;
      if(PositionGetInteger(POSITION_TYPE)!=type) continue;
      double profit = PositionGetDouble(POSITION_PROFIT);
      if(profit > 0 && profit >= atrM5 * profitATR * SymbolInfoDouble(TradeSymbol, SYMBOL_POINT))
         return true;
   }
   return false;
}

// ═══════════════════════════════════════════════════════════════════
// OPEN TRADE
// ═══════════════════════════════════════════════════════════════════
void OpenTrade(int type, string reason)
{
   string sym = TradeSymbol;
   double ask   = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(sym, SYMBOL_BID);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

   double riskATR  = atrM5 * 0.7 + atrM15 * 0.3;

   // ── Lot Calculation ──
   double riskPct = GetRisk();
   double lot = FixedLot;
   if(lot <= 0)
   {
      double bal  = AccountInfoDouble(ACCOUNT_BALANCE);
      double risk = bal * riskPct / 100.0 * GetCompoundingFactor();
      double slDist = riskATR * SL_ATR_Mult;
      double price  = (type == ORDER_TYPE_BUY) ? ask : bid;
      lot = (slDist > 0 && price > 0) ? risk / (slDist * point) : 0.01;

      double minL = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
      double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      lot = MathMax(minL, MathRound(lot / step) * step);
      if(softBreak) lot *= 0.5;
   }
   lot = NormalizeDouble(lot, 2);

   // ── Dynamic SL ──
   double sl;
   if(type == ORDER_TYPE_BUY)
      sl = bid - riskATR * SL_ATR_Mult;
   else
      sl = ask + riskATR * SL_ATR_Mult;
   sl = NormalizeDouble(sl, digits);

   // ── TP1 only (partial close at first target) ──
   double tp1;
   if(type == ORDER_TYPE_BUY)
      tp1 = ask + riskATR * TP1_ATR_Mult;
   else
      tp1 = bid - riskATR * TP1_ATR_Mult;
   tp1 = NormalizeDouble(tp1, digits);

   // ── Execute ──
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = sym;
   req.volume    = lot;
   req.type      = (ENUM_ORDER_TYPE)type;
   req.price     = (type == ORDER_TYPE_BUY) ? ask : bid;
   req.sl        = sl;
   req.tp        = tp1;    // Initial TP = TP1; remainder managed by ManagePositions
   req.deviation = 50;
   req.magic     = Magic;
   req.comment   = reason;
   req.type_filling = ORDER_FILLING_IOC;

   OrderSend(req, res);

   if(res.retcode == TRADE_RETCODE_DONE)
   {
      Print("✅ ", reason, " | Lot:", lot, " | SL:", sl, " | TP1:", tp1,
            " | RSI:", GetBuf(hRSI5,0,0), " | ADX:", GetBuf(hADX5,0,0),
            " | Vol:", DoubleToString(vol0>0 ? (double)vol0/volAvg : 0.0, 2));
      lossStreak = 0;
      if(softBreak) softBreak = false;  // ✅ FIXED: reset on win
      todayTrades++;
   }
   else
   {
      Print("❌ Order failed: ", res.retcode, " — ", res.comment);
      lossStreak++;
      if(lossStreak >= SoftBreakAfter) softBreak = true;  // ✅ FIXED: activate
   }
}

// ═══════════════════════════════════════════════════════════════════
// MANAGE POSITIONS — 3-Level TP + Trailing + Breakeven
// ═══════════════════════════════════════════════════════════════════
void ManagePositions()
{
   string sym  = TradeSymbol;
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double bid   = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask   = SymbolInfoDouble(sym, SYMBOL_ASK);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != Magic) continue;

      double  openP    = PositionGetDouble(POSITION_PRICE_OPEN);
      double  curSL    = PositionGetDouble(POSITION_SL);
      double  curTP    = PositionGetDouble(POSITION_TP);
      double  vol      = PositionGetDouble(POSITION_VOLUME);
      long    posType  = PositionGetInteger(POSITION_TYPE);
      double  curPrice = (posType == POSITION_TYPE_BUY) ? bid : ask;
      string  comment  = PositionGetString(POSITION_COMMENT);

      double profitPts = (posType == POSITION_TYPE_BUY) ?
         (curPrice - openP) / point :
         (openP - curPrice) / point;

      // ── Stage: Check if TP1 hit → Manage partial close ──
      bool tp1Hit = (profitPts * point > atrM5 * TP1_ATR_Mult * 0.9);
      bool tp2Hit = (profitPts * point > atrM5 * TP2_ATR_Mult * 0.9);

      // --- Breakeven after TP1 zone ---
      if(tp1Hit && !StringFind(comment, "BE"))
      {
         double newSL = openP;
         if(!ModifySL(ticket, newSL)) continue;
         // Partial close
         if(PartialPct1 > 0)
         {
            double closeVol = NormalizeDouble(vol * PartialPct1 / 100.0, 2);
            if(closeVol >= SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN))
               ClosePartial(ticket, closeVol);
         }
         // Extend TP to TP2 level
         double newTP = (posType==POSITION_TYPE_BUY) ? openP + atrM5*TP2_ATR_Mult : openP - atrM5*TP2_ATR_Mult;
         ModifyTP(ticket, NormalizeDouble(newTP, digits));
      }

      // --- TP2 zone: second partial ---
      if(tp2Hit && !StringFind(comment, "TP2"))
      {
         // Close second portion
         double remainingVol = PositionGetDouble(POSITION_VOLUME);
         double closeVol2 = NormalizeDouble(remainingVol * 0.55, 2);  // ~35% of original
         if(closeVol2 >= SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN))
            ClosePartial(ticket, closeVol2);
         // Remove TP → runner
         ModifyTP(ticket, 0);
         // Move SL to TP1 level
         double runnerSL = (posType==POSITION_TYPE_BUY) ? openP + atrM5*TP1_ATR_Mult*0.8 : openP - atrM5*TP1_ATR_Mult*0.8;
         ModifySL(ticket, NormalizeDouble(runnerSL, digits));
      }

      // --- Trailing for runner portion ---
      if(tp2Hit)
      {
         double trailDist = atrM5 * 1.2;
         double trailStep = atrM5 * TrailStep;
         if(posType == POSITION_TYPE_BUY)
         {
            double newSL = curPrice - trailDist;
            newSL = NormalizeDouble(newSL, digits);
            if(newSL > curSL + trailStep) ModifySL(ticket, newSL);
         }
         else
         {
            double newSL = curPrice + trailDist;
            newSL = NormalizeDouble(newSL, digits);
            if(newSL < curSL - trailStep) ModifySL(ticket, newSL);
         }
      }
   }
}

// ═══════════════════════════════════════════════════════════════════
// ORDER MODIFICATION HELPERS
// ═══════════════════════════════════════════════════════════════════
bool ModifySL(ulong ticket, double newSL)
{
   if(!PositionSelectByTicket(ticket)) return false;
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_SLTP;
   req.position  = ticket;
   req.symbol    = TradeSymbol;
   req.sl        = newSL;
   req.tp        = PositionGetDouble(POSITION_TP);
   OrderSend(req, res);
   return (res.retcode == TRADE_RETCODE_DONE);
}

bool ModifyTP(ulong ticket, double newTP)
{
   if(!PositionSelectByTicket(ticket)) return false;
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_SLTP;
   req.position  = ticket;
   req.symbol    = TradeSymbol;
   req.sl        = PositionGetDouble(POSITION_SL);
   req.tp        = newTP;
   OrderSend(req, res);
   return (res.retcode == TRADE_RETCODE_DONE);
}

void ClosePartial(ulong ticket, double vol)
{
   if(!PositionSelectByTicket(ticket)) return;
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_DEAL;
   req.position  = ticket;
   req.symbol    = TradeSymbol;
   req.volume    = vol;
   req.type      = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   req.price     = (req.type == ORDER_TYPE_SELL) ? SymbolInfoDouble(TradeSymbol, SYMBOL_BID)
                                                  : SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
   req.deviation = 50;
   req.magic     = Magic;
   req.type_filling = ORDER_FILLING_IOC;
   OrderSend(req, res);
}

// ═══════════════════════════════════════════════════════════════════
// CLOSE ALL POSITIONS (circuit breaker)
// ═══════════════════════════════════════════════════════════════════
void CloseAll(string reason)
{
   Print("⛔ CloseAll: ", reason);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != Magic) continue;

      MqlTradeRequest req = {};
      MqlTradeResult  res = {};
      req.action    = TRADE_ACTION_DEAL;
      req.position  = ticket;
      req.symbol    = TradeSymbol;
      req.volume    = PositionGetDouble(POSITION_VOLUME);
      req.type      = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      req.price     = (req.type == ORDER_TYPE_SELL) ? SymbolInfoDouble(TradeSymbol, SYMBOL_BID)
                                                     : SymbolInfoDouble(TradeSymbol, SYMBOL_ASK);
      req.deviation = 80;
      req.magic     = Magic;
      req.type_filling = ORDER_FILLING_IOC;
      OrderSend(req, res);
   }
}
