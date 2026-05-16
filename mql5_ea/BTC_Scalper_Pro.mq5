//+------------------------------------------------------------------+
//|                                   BTC_Scalper_Pro.mq5             |
//|                      BTC/USD Professional Scalping EA - M5       |
//|         Multi-TF Confluence + Price Action + Smart Risk          |
//+------------------------------------------------------------------+
#property copyright "BTC Scalping Pro v2.0"
#property version   "2.00"
#property description "BTC/USD Pro Scalper — M5 Timeframe"
#property description "Multi-TF: M5 entry, M15 trend, M30 structure"
#property description "ADX filter + BB squeeze + Price Action"
#property description "Partial TP + Pyramiding + Dynamic SL"

// ── Input Parameters ──────────────────────────────────────────────
input group "═══ Core Settings ═══"
input int      Magic          = 20260517;
input double   RiskPercent    = 0.5;         // 0.5% per trade
input double   FixedLot       = 0.0;         // 0 = auto-risk
input int      MaxPositions   = 2;           // Max concurrent

input group "═══ Entry Filters ═══"
input int      ADXPeriod      = 14;
input double   ADXThreshold   = 22.0;        // Min ADX to trade
input int      RSIPeriod      = 5;           // Fast RSI
input int      RSI_Low        = 30;          // Oversold
input int      RSI_High       = 70;          // Overbought
input int      BBPeriod       = 20;
input double   BBDeviation    = 2.0;

input group "═══ Confirmation ═══"
input int      EMAPeriod      = 21;          // Trend EMA
input int      MAFast         = 8;           // Fast MA
input int      MASlow         = 34;          // Slow MA
input double   MinVolumeMult  = 1.3;         // Min volume vs avg

input group "═══ Risk & Exit ═══"
input double   SL_ATR_Mult    = 2.5;         // SL in ATR units
input double   TP_ATR_Mult    = 3.5;         // First TP in ATR units
input double   PartialClose   = 50.0;        // Close % at TP1
input double   Trail_ATR      = 1.2;         // Trail distance ATR
input double   Trail_Start    = 2.0;         // Start trail after profit ATR
input double   Breakeven_ATR  = 1.0;         // Move to BE after profit ATR

input group "═══ Circuit Breaker ═══"
input double   MaxDailyLoss   = 5.0;         // 5% daily max loss
input double   MaxTotalDD     = 12.0;        // 12% max total DD
input int      MaxLossStreak  = 4;
input int      CoolDownMin    = 60;          // Pause after streak

input group "═══ Time Filters ═══"
input bool     UseTimeFilter  = false;       // BTC 24/7, optional
input int      StartHour      = 0;
input int      EndHour        = 24;

// ── Globals ───────────────────────────────────────────────────────
datetime lastM5   = 0;
datetime lastM15  = 0;
double   atrM5    = 0;
double   atrM15   = 0;
int      lossStreak= 0;
datetime pauseUntil= 0;
double   dailyPL  = 0;
double   startBal = 0;
double   peakBal  = 0;
double   totalDD  = 0;
int      todayTrades = 0;
bool     softBreak= false;

// ── Indicator handles (created once) ───────────────────────────────
int hADX, hRSI, hBB_upper, hBB_lower, hBB_mid, hEMA, hMA8, hMA34, hATR;

//+------------------------------------------------------------------+
//| OnInit                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   SymbolSelect("BTCUSD", true);
   SymbolSelect("BTCUSDm", true);
   
   startBal = AccountInfoDouble(ACCOUNT_BALANCE);
   peakBal  = startBal;
   
   // Create indicator handles
   hADX      = iADX("BTCUSD", PERIOD_M5, ADXPeriod);
   hRSI      = iRSI("BTCUSD", PERIOD_M5, RSIPeriod, PRICE_CLOSE);
   hBB_upper = iBands("BTCUSD", PERIOD_M5, BBPeriod, 0, BBDeviation, PRICE_CLOSE);
   hBB_lower = iBands("BTCUSD", PERIOD_M5, BBPeriod, 0, BBDeviation, PRICE_CLOSE);
   hBB_mid   = iBands("BTCUSD", PERIOD_M5, BBPeriod, 0, BBDeviation, PRICE_CLOSE);
   hEMA      = iMA("BTCUSD\", PERIOD_M5, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   hMA8      = iMA("BTCUSD", PERIOD_M5, MAFast, 0, MODE_EMA, PRICE_CLOSE);
   hMA34     = iMA("BTCUSD", PERIOD_M5, MASlow, 0, MODE_EMA, PRICE_CLOSE);
   hATR      = iATR("BTCUSD", PERIOD_M5, 14);
   
   Print("BTC_Scalper_Pro v2.0 | Risk: ", RiskPercent, "% | MaxDD: ", MaxTotalDD, "%");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(hADX);
   IndicatorRelease(hRSI);
   IndicatorRelease(hBB_upper);
   IndicatorRelease(hBB_lower);
   IndicatorRelease(hBB_mid);
   IndicatorRelease(hEMA);
   IndicatorRelease(hMA8);
   IndicatorRelease(hMA34);
   IndicatorRelease(hATR);
}

//+------------------------------------------------------------------+
//| Buffers                                                           |
//+------------------------------------------------------------------+
double GetBuf(int handle, int buffer=0, int shift=0)
{
   double buf[1];
   if(CopyBuffer(handle, buffer, shift, 1, buf) > 0)
      return buf[0];
   return 0;
}

//+------------------------------------------------------------------+
//| OnTick                                                            |
//+------------------------------------------------------------------+
void OnTick()
{
   // ─ Rule 1: Risk Check (every tick) ─
   CheckRisk();
   
   if(TimeCurrent() < pauseUntil) return;
   if(!IsMarketOpen()) return;
   
   // ─ Rule 2: Only on new M5 ─
   if(!IsNewBar()) return;
   
   // ─ Rule 3: Spread filter ─
   double spread = (SymbolInfoDouble("BTCUSD", SYMBOL_ASK) -
                    SymbolInfoDouble("BTCUSD", SYMBOL_BID)) /
                   SymbolInfoDouble("BTCUSD", SYMBOL_POINT);
   if(spread > 20) return;
   
   // ─ Update indicators, trailing, entries ─
   Refresh();
   ManagePositions();
   ScanEntry();
}

//+------------------------------------------------------------------+
bool IsNewBar() { datetime b=iTime("BTCUSD",PERIOD_M5,0); if(b!=lastM5){lastM5=b;return true;} return false; }

//+------------------------------------------------------------------+
bool IsMarketOpen()
{
   if(!UseTimeFilter) return true;
   MqlDateTime dt; TimeCurrent(dt);
   return (dt.hour >= StartHour && dt.hour < EndHour);
}

//+------------------------------------------------------------------+
//| Risk & Circuit Breaker                                            |
//+------------------------------------------------------------------+
void CheckRisk()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq > peakBal) peakBal = eq;
   
   // Daily loss tracking
   static double dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE);
   static int lastDay = 0;
   MqlDateTime dt; TimeCurrent(dt);
   if(dt.day != lastDay) { lastDay = dt.day; dayStartBal = eq; todayTrades = 0; softBreak = false; }
   
   dailyPL = (eq - dayStartBal) / dayStartBal * 100;
   totalDD = peakBal > 0 ? (peakBal - eq) / peakBal * 100 : 0;
   
   // Hard breaker
   if(totalDD >= MaxTotalDD)
   {
      CloseAll("Hard Breaker");
      Print("🛑 HARD STOP: DD ", totalDD, "%");
      ExpertRemove();
      return;
   }
   
   // Daily loss breach
   if(dailyPL <= -MaxDailyLoss)
   {
      CloseAll("Daily Max Loss");
      Print("⛔ Daily max loss: ", dailyPL, "%");
      pauseUntil = TimeCurrent() + 86400; // Rest of day
   }
   
   // Consecutive loss streak
   if(lossStreak >= MaxLossStreak)
   {
      pauseUntil = TimeCurrent() + CoolDownMin * 60;
      Print("⏸ Pause ", CoolDownMin, "m after ", MaxLossStreak, " losses");
      lossStreak = 0;
   }
}

//+------------------------------------------------------------------+
//| Refresh market data                                               |
//+------------------------------------------------------------------+
void Refresh()
{
   atrM5 = GetBuf(hATR, 0, 0);
   
   // Get M15 ATR for multi-TF
   int hATR15 = iATR("BTCUSD", PERIOD_M15, 14);
   double buf[1];
   if(CopyBuffer(hATR15, 0, 0, 1, buf) > 0) atrM15 = buf[0];
   IndicatorRelease(hATR15);
}

//+------------------------------------------------------------------+
//| ENTRY SCAN — Multi-layer confirmation                             |
//+------------------------------------------------------------------+
void ScanEntry()
{
   if(CountPositions() >= MaxPositions) return;
   if(softBreak && CountPositions() >= 1) return;
   
   double close  = iClose("BTCUSD", PERIOD_M5, 0);
   double open   = iOpen("BTCUSD", PERIOD_M5, 0);
   double high   = iHigh("BTCUSD", PERIOD_M5, 0);
   double low    = iLow("BTCUSD", PERIOD_M5, 0);
   double high1  = iHigh("BTCUSD", PERIOD_M5, 1);
   double low1   = iLow("BTCUSD", PERIOD_M5, 1);
   double close1 = iClose("BTCUSD", PERIOD_M5, 1);
   double open1  = iOpen("BTCUSD", PERIOD_M5, 1);
   
   // ─ Indicators ─
   double rsi    = GetBuf(hRSI, 0, 0);
   double ema    = GetBuf(hEMA, 0, 0);
   double ma8    = GetBuf(hMA8, 0, 0);
   double ma34   = GetBuf(hMA34, 0, 0);
   double adx    = GetBuf(hADX, 0, 0);
   double bbU    = GetBuf(hBB_upper, 0, 0);
   double bbL    = GetBuf(hBB_lower, 0, 0);
   double bbM    = GetBuf(hBB_mid, 0, 0);
   
   // ─ Trend structure (M15) ─
   double emaM15 = 0;
   {
      int hEMA15 = iMA("BTCUSD", PERIOD_M15, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
      double b[1]; if(CopyBuffer(hEMA15, 0, 0, 1, b) > 0) emaM15 = b[0];
      IndicatorRelease(hEMA15);
   }
   
   // ─ M30 for major trend ─
   double emaM30 = 0, adxM30 = 0;
   {
      int hEMA30 = iMA("BTCUSD", PERIOD_M30, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
      int hADX30 = iADX("BTCUSD", PERIOD_M30, ADXPeriod);
      double b1[1], b2[1];
      if(CopyBuffer(hEMA30, 0, 0, 1, b1) > 0) emaM30 = b1[0];
      if(CopyBuffer(hADX30, 0, 0, 1, b2) > 0) adxM30 = b2[0];
      IndicatorRelease(hEMA30); IndicatorRelease(hADX30);
   }
   
   // ─ Volume ─
   long vol0  = iVolume("BTCUSD", PERIOD_M5, 0);
   long volSum = 0;
   for(int i=1; i<=20; i++) volSum += iVolume("BTCUSD", PERIOD_M5, i);
   double volAvg = volSum / 20.0;
   double volRatio = (volAvg > 0) ? vol0 / volAvg : 1.0;
   
   // ─ Price Action ─
   double body   = MathAbs(close1 - open1);
   double rangeH = high1 - low1;
   bool isPinBar  = (rangeH > 0 && body < rangeH * 0.3);
   bool isBullish = (close1 > open1);
   bool isBearish = (close1 < open1);
   
   // ═══════════════════════════════════════════════════════════════
   // LAYER 1: Market structure & ADX (market mode)
   // ═══════════════════════════════════════════════════════════════
   bool isTrending = (adx > ADXThreshold || adxM30 > ADXThreshold * 0.8);
   
   // ═══════════════════════════════════════════════════════════════
   // LAYER 2: Multi-TF trend alignment
   // ═══════════════════════════════════════════════════════════════
   bool trendUp   = (close > ema && close > ma8 && ma8 > ma34 && close > emaM15 && close > emaM30);
   bool trendDown = (close < ema && close < ma8 && ma8 < ma34 && close < emaM15 && close < emaM30);
   
   // ═══════════════════════════════════════════════════════════════
   // LAYER 3: Entry trigger (RSI + Volume + Price Action)
   // ═══════════════════════════════════════════════════════════════
   bool volOK = (volRatio >= MinVolumeMult);
   
   // ── BUY SETUP ──
   bool buyPin   = isPinBar && close1 < low;  // bullish pin bar rejection
   bool buyBBSqz = (low <= bbL * 0.998 && close > bbL); // BB bounce
   bool buyMcrx  = (ma8 > ma34 && ema > ma8 && close > ema); // EMA cascade up
   bool rsiBuyOK = (rsi < 60 && rsi > RSI_Low); // RSI recovering, not overbought
   bool buyConfirm = (buyPin || buyBBSqz) && volOK;
   
   if(isTrending && trendUp && rsiBuyOK && buyConfirm && !HasType(POSITION_TYPE_BUY))
   {
      double riskATR = atrM5 * 0.7 + atrM15 * 0.3;  // Weighted ATR
      OpenTrade(ORDER_TYPE_BUY, riskATR, "PRO_BUY");
   }
   
   // ── SELL SETUP ──
   bool sellPin   = isPinBar && close1 > high;  // bearish pin bar rejection
   bool sellBBSqz = (high >= bbU * 1.002 && close < bbU); // BB rejection
   bool sellMcrx  = (ma8 < ma34 && ema < ma8 && close < ema); // EMA cascade down
   bool rsiSellOK  = (rsi > 40 && rsi < RSI_High); // RSI weakening, not oversold
   bool sellConfirm = (sellPin || sellBBSqz) && volOK;
   
   if(isTrending && trendDown && rsiSellOK && sellConfirm && !HasType(POSITION_TYPE_SELL))
   {
      double riskATR = atrM5 * 0.7 + atrM15 * 0.3;
      OpenTrade(ORDER_TYPE_SELL, riskATR, "PRO_SELL");
   }
}

//+------------------------------------------------------------------+
//| Open trade — Dynamic SL/TP, Partial close                        |
//+------------------------------------------------------------------+
void OpenTrade(int type, double riskATR, string comment)
{
   string sym = "BTCUSD";
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   
   // ─ Lot calculation ─
   double lot = FixedLot;
   if(lot <= 0)
   {
      double bal  = AccountInfoDouble(ACCOUNT_BALANCE);
      double risk = bal * RiskPercent / 100.0;
      double slDist = riskATR * SL_ATR_Mult;
      double pipVal = (type == ORDER_TYPE_BUY) ? ask * point : bid * point;
      lot = (slDist > 0 && pipVal > 0) ? risk / (slDist * pipVal) : 0.01;
      
      double minL = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
      double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      lot = MathMax(minL, MathRound(lot / step) * step);
      if(softBreak) lot *= 0.5;
   }
   lot = NormalizeDouble(lot, 2);
   
   // ─ Dynamic SL/TP ─
   double sl, tp;
   if(type == ORDER_TYPE_BUY)
   {
      sl = bid - riskATR * SL_ATR_Mult;
      tp = ask + riskATR * TP_ATR_Mult;
   }
   else
   {
      sl = ask + riskATR * SL_ATR_Mult;
      tp = bid - riskATR * TP_ATR_Mult;
   }
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
   
   // ─ Execute ─
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = sym;
   req.volume    = lot;
   req.type      = (ENUM_ORDER_TYPE)type;
   req.price     = (type == ORDER_TYPE_BUY) ? ask : bid;
   req.sl        = sl;
   req.tp        = tp;
   req.deviation = 80;
   req.magic     = Magic;
   req.comment   = comment;
   req.type_filling = ORDER_FILLING_IOC;
   
   OrderSend(req, res);
   
   if(res.retcode == TRADE_RETCODE_DONE)
   {
      Print("✅ PRO_", (type==ORDER_TYPE_BUY?"BUY":"SELL"),
            " | Lot:", lot, " | SL:", sl, " | TP:", tp,
            " | ATR5:", atrM5, " | ADX:", GetBuf(hADX,0,0));
      lossStreak = 0;
      todayTrades++;
   }
   else
   {
      Print("❌ Order failed: ", res.retcode, " — ", res.comment);
      lossStreak++;
   }
}

//+------------------------------------------------------------------+
//| Position management — Trail + Breakeven + Partial TP             |
//+------------------------------------------------------------------+
void ManagePositions()
{
   string sym = "BTCUSD";
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != Magic) continue;
      
      double  openP   = PositionGetDouble(POSITION_PRICE_OPEN);
      double  curSL   = PositionGetDouble(POSITION_SL);
      double  curTP   = PositionGetDouble(POSITION_TP);
      double  vol     = PositionGetDouble(POSITION_VOLUME);
      long    posType = PositionGetInteger(POSITION_TYPE);
      double  curPrice= (posType == POSITION_TYPE_BUY) ? bid : ask;
      
      double profitPts = (posType == POSITION_TYPE_BUY) ?
         (curPrice - openP) / point :
         (openP - curPrice) / point;
      double profitATR = atrM5 > 0 ? (profitPts * point) / atrM5 : 0;
      
      // ─ Breakeven ─
      if(profitATR >= Breakeven_ATR && curSL != openP)
      {
         double newSL = NormalizeDouble(openP, digits);
         if((posType == POSITION_TYPE_BUY && newSL > curSL) ||
            (posType == POSITION_TYPE_SELL && newSL < curSL))
            ModifySL(ticket, sym, newSL, curTP);
      }
      
      // ─ Partial TP ─
      if(profitATR >= 1.5 && vol > SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN) * 1.5)
      {
         // Close half
         double closeVol = NormalizeDouble(vol * PartialClose / 100.0, 2);
         if(closeVol >= SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN))
         {
            MqlTradeRequest r = {};
            MqlTradeResult  s = {};
            r.action   = TRADE_ACTION_DEAL;
            r.symbol   = sym;
            r.volume   = closeVol;
            r.type     = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
            r.position = ticket;
            r.price    = (posType == POSITION_TYPE_BUY) ? bid : ask;
            r.deviation= 80;
            r.magic    = Magic;
            r.comment  = "PartialTP";
            OrderSend(r, s);
            if(s.retcode == TRADE_RETCODE_DONE)
               Print("📊 Partial TP: ", closeVol, " lots | Profit: ", profitATR, "x ATR");
         }
      }
      
      // ─ Trailing Stop ─
      if(profitATR >= Trail_Start)
      {
         double trailDist = atrM5 * Trail_ATR;
         double newSL;
         if(posType == POSITION_TYPE_BUY)
            newSL = curPrice - trailDist;
         else
            newSL = curPrice + trailDist;
         
         newSL = NormalizeDouble(newSL, digits);
         
         if((posType == POSITION_TYPE_BUY && newSL > curSL) ||
            (posType == POSITION_TYPE_SELL && newSL < curSL))
            ModifySL(ticket, sym, newSL, curTP);
      }
   }
}

//+------------------------------------------------------------------+
void ModifySL(ulong ticket, string sym, double sl, double tp)
{
   MqlTradeRequest r = {};
   MqlTradeResult  s = {};
   r.action   = TRADE_ACTION_SLTP;
   r.position = ticket;
   r.symbol   = sym;
   r.sl       = sl;
   r.tp       = tp;
   OrderSend(r, s);
   if(s.retcode == TRADE_RETCODE_DONE)
      Print("→ Trail: tkt ", ticket, " SL → ", sl);
}

//+------------------------------------------------------------------+
//| Helpers                                                           |
//+------------------------------------------------------------------+
int CountPositions()
{
   int c=0;
   for(int i=0; i<PositionsTotal(); i++)
   { ulong t=PositionGetTicket(i);
     if(PositionSelectByTicket(t) && PositionGetInteger(POSITION_MAGIC)==Magic) c++; }
   return c;
}

bool HasType(long type)
{
   for(int i=0; i<PositionsTotal(); i++)
   { ulong t=PositionGetTicket(i);
     if(PositionSelectByTicket(t) && PositionGetInteger(POSITION_MAGIC)==Magic &&
        PositionGetInteger(POSITION_TYPE)==type) return true; }
   return false;
}

void CloseAll(string reason)
{
   string sym="BTCUSD";
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong t=PositionGetTicket(i);
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=Magic) continue;
      MqlTradeRequest r={}; MqlTradeResult s={};
      r.action=TRADE_ACTION_DEAL; r.symbol=sym;
      r.volume=PositionGetDouble(POSITION_VOLUME);
      r.type=(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY)?ORDER_TYPE_SELL:ORDER_TYPE_BUY;
      r.position=t; r.price=(r.type==ORDER_TYPE_SELL)?SymbolInfoDouble(sym,SYMBOL_BID):SymbolInfoDouble(sym,SYMBOL_ASK);
      r.deviation=80; r.magic=Magic; r.comment=reason;
      OrderSend(r,s);
   }
}
//+------------------------------------------------------------------+
