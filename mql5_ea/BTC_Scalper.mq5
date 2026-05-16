//+------------------------------------------------------------------+
//|                                        BTC_Scalper.mq5            |
//|                              BTC/USD Scalping EA - M5             |
//|                    3-Confirmation Entry + ATR Risk Management      |
//+------------------------------------------------------------------+
#property copyright "BTC Scalping System v1.0"
#property version   "1.00"
#property description "BTC/USD Scalping EA — M5 Timeframe"
#property description "Entry: RSI + EMA + Volume (3 confirmations)"
#property description "Risk: 0.5% per trade, auto-lot, trailing stop"

// --- Input Parameters ---
input int      MagicNumber    = 20260517;   // Magic Number
input double   RiskPercent    = 0.5;        // Risk per trade (%)
input double   LotSize        = 0.01;       // Lot size (0 = auto)
input int      RSIPeriod      = 7;          // RSI period
input int      RSIOverbought  = 65;         // RSI overbought
input int      RSIOverSold    = 35;         // RSI oversold
input int      EMAPeriod      = 21;         // EMA period
input int      ATRPeriod      = 14;         // ATR period
input double   SLMultiplier   = 2.0;        // SL = ATR * multiplier
input double   TPMultiplier   = 3.0;        // TP = ATR * multiplier
input double   TrailStart     = 1.5;        // Trail after profit > ATR * this
input double   VolumeThresh   = 1.5;        // Volume must be > avg * this
input double   MaxSpread      = 15.0;       // Max spread in pips
input double   HardDDPercent  = 8.0;        // Hard circuit breaker DD%
input double   SoftDDPercent  = 5.0;        // Soft circuit breaker DD%
input int      MaxLossStreak  = 3;          // Max consecutive losses
input int      PauseMinutes   = 60;         // Pause after max loss streak

// --- Globals ---
datetime lastBarTime = 0;
double   atrValue    = 0;
int      lossStreak  = 0;
datetime pauseUntil  = 0;
double   startEquity = 0;
double   peakEquity  = 0;
double   dailyDD     = 0;
double   lotScale    = 1.0;  // Soft breaker reduces this
bool     didCheck    = false;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   SymbolSelect("BTCUSD", true);
   SymbolSelect("BTCUSDm", true);
   
   startEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   peakEquity  = startEquity;
   Print("BTC_Scalper initialized | Magic: ", MagicNumber,
         " | Risk: ", RiskPercent, "% | ATR mul: ", SLMultiplier, "x");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinit                                                     |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("BTC_Scalper stopped. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| New bar detection (M5)                                            |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime bar = iTime("BTCUSD", PERIOD_M5, 0);
   if(bar != lastBarTime)
   {
      lastBarTime = bar;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Main tick handler                                                 |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check circuit breaker on every tick
   if(!CircuitBreaker())
      return;
   
   // New bar events
   if(!IsNewBar())
      return;
   
   // Update indicators
   RefreshData();
   
   // Check pause (consecutive loss streak)
   if(TimeCurrent() < pauseUntil)
      return;
   
   // Spread check
   if(SpreadExceeds())
      return;
   
   // Manage existing positions (trailing stop)
   TrailPositions();
   
   // Check entries
   CheckEntry();
}

//+------------------------------------------------------------------+
//| Refresh ATR, RSI, EMA, Volume data                               |
//+------------------------------------------------------------------+
void RefreshData()
{
   // ATR handle
   int atrH = iATR("BTCUSD", PERIOD_M5, ATRPeriod);
   double atrBuf[1];
   if(CopyBuffer(atrH, 0, 0, 1, atrBuf) > 0)
      atrValue = atrBuf[0];
   IndicatorRelease(atrH);
   
   // Update equity tracking
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq > peakEquity) peakEquity = eq;
   if(peakEquity > 0) dailyDD = (peakEquity - eq) / peakEquity * 100;
}

//+------------------------------------------------------------------+
//| Circuit Breaker                                                   |
//+------------------------------------------------------------------+
bool CircuitBreaker()
{
   // Hard breaker
   if(dailyDD >= HardDDPercent)
   {
      Print("HARD BREAKER: DD ", dailyDD, "% >= ", HardDDPercent, "%");
      CloseAll();
      return false;
   }
   
   // Soft breaker
   if(dailyDD >= SoftDDPercent && lotScale > 0.5)
   {
      lotScale = 0.5;
      Print("SOFT BREAKER: DD ", dailyDD, "%. Lot reduced 50%");
   }
   else if(dailyDD < SoftDDPercent && lotScale < 1.0)
   {
      lotScale = 1.0;
   }
   
   // Max loss streak
   if(lossStreak >= MaxLossStreak)
   {
      pauseUntil = TimeCurrent() + PauseMinutes * 60;
      Print("PAUSE: ", MaxLossStreak, " consecutive losses. Pause ",
            PauseMinutes, " min");
      lossStreak = 0;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Check spread                                                      |
//+------------------------------------------------------------------+
bool SpreadExceeds()
{
   double sp = (SymbolInfoDouble("BTCUSD", SYMBOL_ASK) -
                SymbolInfoDouble("BTCUSD", SYMBOL_BID)) /
               SymbolInfoDouble("BTCUSD", SYMBOL_POINT);
   if(sp > MaxSpread)
   {
      // Silent reject — avoid log spam
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| RSI value at current bar                                          |
//+------------------------------------------------------------------+
double GetRSI()
{
   int h = iRSI("BTCUSD", PERIOD_M5, RSIPeriod, PRICE_CLOSE);
   double buf[1];
   if(CopyBuffer(h, 0, 0, 1, buf) > 0)
   {
      IndicatorRelease(h);
      return buf[0];
   }
   IndicatorRelease(h);
   return 50;
}

//+------------------------------------------------------------------+
//| EMA value at current bar                                          |
//+------------------------------------------------------------------+
double GetEMA()
{
   int h = iMA("BTCUSD", PERIOD_M5, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   double buf[1];
   if(CopyBuffer(h, 0, 0, 1, buf) > 0)
   {
      IndicatorRelease(h);
      return buf[0];
   }
   IndicatorRelease(h);
   return 0;
}

//+------------------------------------------------------------------+
//| Volume ratio vs average (10 bars)                                 |
//+------------------------------------------------------------------+
double GetVolumeRatio()
{
   long tickVol[11];
   for(int i = 0; i < 11; i++)
      tickVol[i] = iVolume("BTCUSD", PERIOD_M5, i);
   
   double avg10 = 0;
   double curVol = tickVol[0];
   for(int i = 1; i < 11; i++)
      avg10 += tickVol[i];
   avg10 /= 10.0;
   
   if(avg10 <= 0) return 1.0;
   return curVol / avg10;
}

//+------------------------------------------------------------------+
//| Check for entry signals                                           |
//+------------------------------------------------------------------+
void CheckEntry()
{
   int posCount = CountPositions();
   if(posCount >= 2) return;  // Max 2 concurrent
   
   double rsi   = GetRSI();
   double ema   = GetEMA();
   double close = iClose("BTCUSD", PERIOD_M5, 0);
   double volR  = GetVolumeRatio();
   
   bool volOK  = (volR >= VolumeThresh);
   bool trendUp= (close > ema);
   bool trendDn= (close < ema);
   
   // BUY signal
   if(rsi < RSIOverSold && trendUp && volOK && !HasBuyPosition())
   {
      ExecuteTrade(ORDER_TYPE_BUY, "BTC_RSI_BUY");
      didCheck = false;
   }
   // SELL signal
   else if(rsi > RSIOverbought && trendDn && volOK && !HasSellPosition())
   {
      ExecuteTrade(ORDER_TYPE_SELL, "BTC_RSI_SELL");
      didCheck = false;
   }
}

//+------------------------------------------------------------------+
//| Execute trade with auto-lot and ATR-based SL/TP                    |
//+------------------------------------------------------------------+
void ExecuteTrade(int type, string comment)
{
   string sym = "BTCUSD";
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   
   // Calculate lot
   double lot = LotSize;
   if(LotSize == 0)
   {
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double riskAmt = balance * RiskPercent / 100.0;
      double slDist  = atrValue * SLMultiplier;
      double pipVal  = 0; // approximate for BTC
      if(type == ORDER_TYPE_BUY)
         pipVal = ask * point;
      else
         pipVal = bid * point;
      
      if(slDist > 0 && pipVal > 0)
         lot = riskAmt / (slDist * pipVal);
      else
         lot = 0.01;
      
      // Normalize lot
      double minLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
      double stepLot= SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      lot = MathMax(minLot, MathRound(lot / stepLot) * stepLot);
      lot *= lotScale;
   }
   
   // SL/TP based on ATR
   double sl, tp;
   if(type == ORDER_TYPE_BUY)
   {
      sl = bid - atrValue * SLMultiplier;
      tp = ask + atrValue * TPMultiplier;
   }
   else
   {
      sl = ask + atrValue * SLMultiplier;
      tp = bid - atrValue * TPMultiplier;
   }
   
   // Normalize prices
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   sl  = NormalizeDouble(sl, digits);
   tp  = NormalizeDouble(tp, digits);
   
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = sym;
   req.volume    = lot;
   req.type      = (ENUM_ORDER_TYPE)type;
   req.price     = (type == ORDER_TYPE_BUY) ? ask : bid;
   req.sl        = sl;
   req.tp        = tp;
   req.deviation = 50;
   req.magic     = MagicNumber;
   req.comment   = comment;
   req.type_filling = ORDER_FILLING_IOC;
   
   OrderSend(req, res);
   
   if(res.retcode == TRADE_RETCODE_DONE)
   {
      Print("✓ ", (type == ORDER_TYPE_BUY ? "BUY" : "SELL"),
            " | Lot: ", lot, " | SL: ", sl, " | TP: ", tp,
            " | RSI: ", GetRSI(), " | Vol: ", GetVolumeRatio(), "x");
   }
   else
   {
      Print("✗ Order failed: ", res.retcode, " — ", res.comment);
      lossStreak++;
   }
}

//+------------------------------------------------------------------+
//| Trailing stop                                                     |
//+------------------------------------------------------------------+
void TrailPositions()
{
   string sym = "BTCUSD";
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      
      double openP  = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL  = PositionGetDouble(POSITION_SL);
      double curTP  = PositionGetDouble(POSITION_TP);
      long   posType = PositionGetInteger(POSITION_TYPE);
      double curPrice = (posType == POSITION_TYPE_BUY) ? bid : ask;
      
      // Profit in terms of ATR
      double profitPts = (posType == POSITION_TYPE_BUY) ?
         (curPrice - openP) / point :
         (openP - curPrice) / point;
      double profitATR = profitPts * point / atrValue;
      
      // Start trailing after TrailStart * ATR profit
      if(profitATR < TrailStart) continue;
      if(profitATR <= 0) continue;
      
      double trailDist = atrValue * 0.8;  // Trail distance
      double newSL;
      
      if(posType == POSITION_TYPE_BUY)
      {
         newSL = curPrice - trailDist;
         if(newSL <= curSL || curSL == 0) continue;
      }
      else
      {
         newSL = curPrice + trailDist;
         if(newSL >= curSL || curSL == 0) continue;
      }
      
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      newSL = NormalizeDouble(newSL, digits);
      
      // Modify SL
      MqlTradeRequest req = {};
      MqlTradeResult  res = {};
      req.action   = TRADE_ACTION_SLTP;
      req.position = ticket;
      req.symbol   = sym;
      req.sl       = newSL;
      req.tp       = curTP;
      
      OrderSend(req, res);
      if(res.retcode == TRADE_RETCODE_DONE)
         Print("→ Trail: ticket ", ticket, " SL → ", newSL);
   }
}

//+------------------------------------------------------------------+
//| Position counting                                                 |
//+------------------------------------------------------------------+
int CountPositions()
{
   int cnt = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(PositionSelectByTicket(t))
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber)
            cnt++;
   }
   return cnt;
}

bool HasBuyPosition()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(PositionSelectByTicket(t))
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
            return true;
   }
   return false;
}

bool HasSellPosition()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong t = PositionGetTicket(i);
      if(PositionSelectByTicket(t))
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL)
            return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Close all positions                                               |
//+------------------------------------------------------------------+
void CloseAll()
{
   string sym = "BTCUSD";
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      
      MqlTradeRequest req = {};
      MqlTradeResult  res = {};
      req.action   = TRADE_ACTION_DEAL;
      req.symbol   = sym;
      req.volume   = PositionGetDouble(POSITION_VOLUME);
      req.type     = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ?
                     ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      req.position = t;
      req.price    = (req.type == ORDER_TYPE_SELL) ?
                     SymbolInfoDouble(sym, SYMBOL_BID) :
                     SymbolInfoDouble(sym, SYMBOL_ASK);
      req.deviation= 50;
      req.magic    = MagicNumber;
      req.comment  = "CircuitBreak";
      
      OrderSend(req, res);
      if(res.retcode == TRADE_RETCODE_DONE)
         Print("Closed: ticket ", t);
   }
}
//+------------------------------------------------------------------+
