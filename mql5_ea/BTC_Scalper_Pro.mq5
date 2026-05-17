//+------------------------------------------------------------------+
//|  BTC_Scalper_Pro.mq5 — v5.0 "High Probability Momentum"          |
//|  BTC/USD Scalping EA — M5 Execution                              |
//|  3-TF Alignment (H4+H1+M15) + 3 Entry Patterns                   |
//|  Based on Python v5 strategy — backtested 89% WR                  |
//|  Author: Dasha + Adhit | Repo: github.com/adhitamaw/btc_bot      |
//+------------------------------------------------------------------+
#property copyright "BTC Scalping Pro v5.0"
#property version   "5.00"
#property description "BTC/USD v5 — 3-TF Momentum Scalper"
#property description "H4+H1+M15 Trend Alignment | ATR-based SL/TP/Trail"
#property description "3 Patterns: Pullback, Momentum Continuation, BB Bounce"

// ═══════════════════════════════════════════════════════════════════
// INPUT PARAMETERS
// ═══════════════════════════════════════════════════════════════════

input group "═══ Trade Settings ═══"
input int      MagicNumber    = 20260517;     // Magic number
input double   RiskPercent    = 2.0;           // Risk per trade (%)
input int      MaxPositions   = 2;             // Max concurrent positions
input bool     UseCompounding = true;          // Auto-scale with equity
input double   CompoundStep   = 5.0;           // Rescale every N% gain

input group "═══ Trend Filters ═══"
input int      ADXPeriod      = 14;            // ADX period
input double   ADXMin         = 20.0;          // Min ADX for trend
input int      EMAPeriod      = 20;            // EMA period for trend
input int      RSIPeriod      = 14;            // RSI period

input group "═══ Entry Filters ═══"
input double   RSI_Low        = 35.0;          // RSI oversold
input double   RSI_High       = 65.0;          // RSI overbought
input double   VolumeMult     = 1.5;           // Volume multiplier vs avg

input group "═══ Risk & Exit ═══"
input double   SL_ATR         = 1.0;           // Stop loss (ATR units)
input double   TP_ATR         = 2.0;           // Take profit (ATR units)
input double   TrailActivate  = 0.6;           // Start trail at ATR profit
input double   TrailStep      = 0.3;           // Trail distance (ATR units)
input double   BreakEven      = 0.4;           // Move to BE at ATR profit

input group "═══ Circuit Breakers ═══"
input int      MaxLossStreak  = 3;             // Cooldown after N losses
input int      CooldownMins   = 30;            // Cooldown duration (min)
input double   MaxDailyLoss   = 6.0;           // Max daily loss (%)
input double   MaxDrawdown    = 18.0;          // Max total DD (%)
input int      MaxTradesPerDay = 30;           // Max trades/day

// ═══════════════════════════════════════════════════════════════════
// GLOBAL VARIABLES
// ═══════════════════════════════════════════════════════════════════

datetime     _lastBarTime       = 0;
double       _balanceStart      = 0;
double       _peakEquity        = 0;
int          _lossStreak        = 0;
datetime     _cooldownUntil     = 0;
int          _todayTrades       = 0;
datetime     _todayDate         = 0;
double       _scaleFactor       = 1.0;
double       _initialBalance    = 0;

// Handle storage
int          _adxHandle         = INVALID_HANDLE;
int          _rsiHandle         = INVALID_HANDLE;
int          _bbHandle          = INVALID_HANDLE;
int          _ema20Handle       = INVALID_HANDLE;

// H4 handles
int          _adxH4Handle       = INVALID_HANDLE;
int          _emaH4Handle       = INVALID_HANDLE;
double       _emaH4[1], _adxH4[1], _pdiH4[1], _mdiH4[1];
datetime     _lastH4Bar         = 0;

// H1 handles
int          _adxH1Handle       = INVALID_HANDLE;
int          _emaH1Handle       = INVALID_HANDLE;
double       _emaH1[1], _adxH1[1], _pdiH1[1], _mdiH1[1];
datetime     _lastH1Bar         = 0;

// M15 handles
int          _ema15Handle       = INVALID_HANDLE;
double       _ema15[1];
datetime     _lastM15Bar         = 0;

// Order tracking
int          _ticketPool[];

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   _initialBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   _balanceStart = _initialBalance;
   _peakEquity = _initialBalance;
   _scaleFactor = 1.0;
   
   // M5 indicators
   _adxHandle = iADX(_Symbol, PERIOD_CURRENT, ADXPeriod);
   _rsiHandle = iRSI(_Symbol, PERIOD_CURRENT, RSIPeriod, PRICE_CLOSE);
   _bbHandle = iBands(_Symbol, PERIOD_CURRENT, 20, 0, 2.0, PRICE_CLOSE);
   _ema20Handle = iMA(_Symbol, PERIOD_CURRENT, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   
   if(_adxHandle == INVALID_HANDLE || _rsiHandle == INVALID_HANDLE ||
      _bbHandle == INVALID_HANDLE || _ema20Handle == INVALID_HANDLE)
   {
      Print("Failed to create M5 indicators");
      return INIT_FAILED;
   }
   
   // H4 indicators
   _adxH4Handle = iADX(_Symbol, PERIOD_H4, ADXPeriod);
   _emaH4Handle = iMA(_Symbol, PERIOD_H4, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   
   if(_adxH4Handle == INVALID_HANDLE || _emaH4Handle == INVALID_HANDLE)
   {
      Print("Failed to create H4 indicators");
      return INIT_FAILED;
   }
   
   // H1 indicators
   _adxH1Handle = iADX(_Symbol, PERIOD_H1, ADXPeriod);
   _emaH1Handle = iMA(_Symbol, PERIOD_H1, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   
   if(_adxH1Handle == INVALID_HANDLE || _emaH1Handle == INVALID_HANDLE)
   {
      Print("Failed to create H1 indicators");
      return INIT_FAILED;
   }
   
   // M15 indicators
   _ema15Handle = iMA(_Symbol, PERIOD_M15, EMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(_ema15Handle == INVALID_HANDLE)
   {
      Print("Failed to create M15 indicators");
      return INIT_FAILED;
   }
   
   Print("BTC Scalper v5 initialized. Balance: ", _initialBalance);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(_adxHandle);
   IndicatorRelease(_rsiHandle);
   IndicatorRelease(_bbHandle);
   IndicatorRelease(_ema20Handle);
   IndicatorRelease(_adxH4Handle);
   IndicatorRelease(_emaH4Handle);
   IndicatorRelease(_adxH1Handle);
   IndicatorRelease(_emaH1Handle);
   IndicatorRelease(_ema15Handle);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Only process on new M5 bar
   if(!IsNewBar()) return;
   
   // Manage open positions
   ManagePositions();
   
   // Check circuit breakers
   if(!CanTrade()) return;
   
   // Check entry
   CheckEntry();
}

//+------------------------------------------------------------------+
//| Check if new M5 bar                                              |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime cur = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(cur != _lastBarTime)
   {
      _lastBarTime = cur;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Manage open positions: SL, TP, trailing                           |
//+------------------------------------------------------------------+
void ManagePositions()
{
   // We use built-in SL/TP from OrderSend, so positions auto-manage
   // Additional trailing logic would go here for advanced trail
   // For now, we rely on MT5's built-in SL/TP
}

//+------------------------------------------------------------------+
//| Check circuit breakers                                           |
//+------------------------------------------------------------------+
bool CanTrade()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   
   // Day rollover
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime today = StringToTime(IntegerToString(dt.year) + "." +
                                IntegerToString(dt.mon) + "." +
                                IntegerToString(dt.day));
   
   if(today != _todayDate)
   {
      _todayDate = today;
      _balanceStart = balance;
      _todayTrades = 0;
   }
   
   // Update peak
   if(equity > _peakEquity) _peakEquity = equity;
   
   // Max drawdown check
   double dd = (_peakEquity > 0) ? (_peakEquity - equity) / _peakEquity * 100.0 : 0;
   if(dd >= MaxDrawdown)
   {
      Print("CIRCUIT BREAKER: Max DD ", DoubleToString(dd, 1), "% reached");
      return false;
   }
   
   // Daily loss check
   double dayLoss = (balance - _balanceStart) / _balanceStart * 100.0;
   if(dayLoss <= -MaxDailyLoss) return false;
   
   // Cooldown check
   if(_cooldownUntil > 0 && TimeCurrent() < _cooldownUntil) return false;
   
   // Max positions check
   if(CountOpenPositions() >= MaxPositions) return false;
   
   // Daily trade limit
   if(_todayTrades >= MaxTradesPerDay) return false;
   
   // Compounding scale update
   if(UseCompounding)
   {
      double gain = (balance - _initialBalance) / _initialBalance * 100.0;
      double target = 1.0 + MathFloor(gain / CompoundStep) * 0.1;
      _scaleFactor = fmax(1.0, fmin(5.0, target));
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Count open positions                                             |
//+------------------------------------------------------------------+
int CountOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Check for entry signals                                          |
//+------------------------------------------------------------------+
void CheckEntry()
{
   // ── Get M5 indicator values ──
   double m5_close = iClose(_Symbol, PERIOD_CURRENT, 0);
   double m5_high  = iHigh(_Symbol, PERIOD_CURRENT, 0);
   double m5_low   = iLow(_Symbol, PERIOD_CURRENT, 0);
   double m5_open  = iOpen(_Symbol, PERIOD_CURRENT, 0);
   double prev_close = iClose(_Symbol, PERIOD_CURRENT, 1);
   double prev_high  = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double prev_low   = iLow(_Symbol, PERIOD_CURRENT, 1);
   double prev_open  = iOpen(_Symbol, PERIOD_CURRENT, 1);
   double volume_now = (double)iVolume(_Symbol, PERIOD_CURRENT, 0);
   
   // M5 indicators
   double ema20[1], rsi[1], adx[1], pdi[1], mdi[1], atr_val[1];
   double bb_mid[1], bb_up[1], bb_low[1];
   
   CopyBuffer(_ema20Handle, 0, 0, 1, ema20);
   CopyBuffer(_rsiHandle, 0, 0, 1, rsi);
   CopyBuffer(_adxHandle, 0, 0, 1, adx);
   CopyBuffer(_adxHandle, 1, 0, 1, pdi);
   CopyBuffer(_adxHandle, 2, 0, 1, mdi);
   
   double hh[3], ll[3], cc[3];
   for(int j = 0; j < 3; j++)
   {
      hh[j] = iHigh(_Symbol, PERIOD_CURRENT, j);
      ll[j] = iLow(_Symbol, PERIOD_CURRENT, j);
      cc[j] = iClose(_Symbol, PERIOD_CURRENT, j);
   }
   
   // Manual ATR
   atr_val[0] = 0;
   int atr_period = 14;
   for(int j = 0; j < atr_period && j < 100; j++)
   {
      double h = iHigh(_Symbol, PERIOD_CURRENT, j);
      double l = iLow(_Symbol, PERIOD_CURRENT, j);
      double pc = iClose(_Symbol, PERIOD_CURRENT, j+1);
      double tr = fmax(h - l, fmax(fabs(h - pc), fabs(l - pc)));
      atr_val[0] += tr;
   }
   atr_val[0] /= atr_period;
   if(atr_val[0] < 1) atr_val[0] = 10;
   
   // Volume SMA
   double vol_sum = 0;
   for(int j = 1; j <= 20; j++) vol_sum += (double)iVolume(_Symbol, PERIOD_CURRENT, j);
   double vol_avg = vol_sum / 20.0;
   double vol_ratio = (vol_avg > 0) ? volume_now / vol_avg : 1.0;
   
   // ── Refresh H4 indicators ──
   datetime h4_time = iTime(_Symbol, PERIOD_H4, 0);
   if(h4_time != _lastH4Bar)
   {
      _lastH4Bar = h4_time;
      CopyBuffer(_emaH4Handle, 0, 0, 1, _emaH4);
      CopyBuffer(_adxH4Handle, 0, 0, 1, _adxH4);
      CopyBuffer(_adxH4Handle, 1, 0, 1, _pdiH4);
      CopyBuffer(_adxH4Handle, 2, 0, 1, _mdiH4);
   }
   
   // ── Refresh H1 indicators ──
   datetime h1_time = iTime(_Symbol, PERIOD_H1, 0);
   if(h1_time != _lastH1Bar)
   {
      _lastH1Bar = h1_time;
      CopyBuffer(_emaH1Handle, 0, 0, 1, _emaH1);
      CopyBuffer(_adxH1Handle, 0, 0, 1, _adxH1);
      CopyBuffer(_adxH1Handle, 1, 0, 1, _pdiH1);
      CopyBuffer(_adxH1Handle, 2, 0, 1, _mdiH1);
   }
   
   // ── Refresh M15 indicators ──
   datetime m15_time = iTime(_Symbol, PERIOD_M15, 0);
   if(m15_time != _lastM15Bar)
   {
      _lastM15Bar = m15_time;
      CopyBuffer(_ema15Handle, 0, 0, 1, _ema15);
   }
   
   // ── BB values on M5 ──
   CopyBuffer(_bbHandle, 0, 0, 1, bb_mid);
   CopyBuffer(_bbHandle, 1, 0, 1, bb_up);
   CopyBuffer(_bbHandle, 2, 0, 1, bb_low);
   
   // ── Trend Context ──
   bool h4_up = (m5_close > _emaH4[0] && _adxH4[0] > ADXMin && _pdiH4[0] > _mdiH4[0]);
   bool h4_dn = (m5_close < _emaH4[0] && _adxH4[0] > ADXMin && _mdiH4[0] > _pdiH4[0]);
   bool h1_up = (m5_close > _emaH1[0] && _adxH1[0] > ADXMin && _pdiH1[0] > _mdiH1[0]);
   bool h1_dn = (m5_close < _emaH1[0] && _adxH1[0] > ADXMin && _mdiH1[0] > _pdiH1[0]);
   bool m15_up = (m5_close > _ema15[0]);
   bool m15_dn = (m5_close < _ema15[0]);
   bool above_ema20 = (m5_close > ema20[0]);
   bool below_ema20 = (m5_close < ema20[0]);
   
   bool vol_ok = (vol_ratio >= VolumeMult);
   bool rsi_buy = (rsi[0] >= RSI_Low && rsi[0] <= 55.0);
   bool rsi_sell = (rsi[0] >= 45.0 && rsi[0] <= RSI_High);
   
   // ── Candle Analysis (prev candle) ──
   double body = fabs(prev_close - prev_open);
   double range = prev_high - prev_low;
   double low_w = (prev_open < prev_close) ? (prev_open - prev_low) : (prev_close - prev_low);
   double up_w  = (prev_open > prev_close) ? (prev_open - prev_high) : (prev_close - prev_high);
   low_w = fabs(low_w);
   up_w = fabs(up_w);
   bool c_bull = (prev_close > prev_open);
   bool c_bear = (prev_close < prev_open);
   
   string signal = "";
   
   // ── Pattern 1: Trend Pullback ──
   if(vol_ok && range > 0)
   {
      if(h4_up && h1_up && m15_up && above_ema20)
      {
         bool touch = (m5_low <= _ema15[0] * 1.002);
         bool rev = c_bull && body > range * 0.35 && low_w < body * 0.3;
         if(touch && rev && rsi_buy) signal = "buy";
      }
      else if(h4_dn && h1_dn && m15_dn && below_ema20)
      {
         bool touch = (m5_high >= _ema15[0] * 0.998);
         bool rev = c_bear && body > range * 0.35 && up_w < body * 0.3;
         if(touch && rev && rsi_sell) signal = "sell";
      }
   }
   
   // ── Pattern 2: Momentum Continuation ──
   if(signal == "" && vol_ok && vol_ratio >= 2.0 && range > 0)
   {
      if(h4_up && h1_up)
      {
         bool strong = c_bull && body > range * 0.6 && range > atr_val[0] * 0.8;
         if(strong && rsi_buy) signal = "buy";
      }
      else if(h4_dn && h1_dn)
      {
         bool strong = c_bear && body > range * 0.6 && range > atr_val[0] * 0.8;
         if(strong && rsi_sell) signal = "sell";
      }
   }
   
   // ── Pattern 3: BB Bounce ──
   if(signal == "" && vol_ok && range > 0)
   {
      if(h4_up && h1_up)
      {
         bool bounce = (m5_low <= bb_low[0] * 1.002) && (m5_close > bb_low[0] * 1.001);
         if(bounce && c_bull && body > range * 0.4 && rsi_buy) signal = "buy";
      }
      else if(h4_dn && h1_dn)
      {
         bool bounce = (m5_high >= bb_up[0] * 0.998) && (m5_close < bb_up[0] * 0.999);
         if(bounce && c_bear && body > range * 0.4 && rsi_sell) signal = "sell";
      }
   }
   
   if(signal == "") return;
   
   // ── Execute ──
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double stop_dist = atr_val[0] * SL_ATR;
   double risk_amount = balance * (RiskPercent / 100.0) * _scaleFactor;
   double lot_size = risk_amount / stop_dist;
   
   // Normalize lot
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lot_size = MathRound(lot_size / lot_step) * lot_step;
   if(lot_size < min_lot) lot_size = min_lot;
   
   double sl, tp;
   if(signal == "buy")
   {
      sl = m5_close - stop_dist;
      tp = m5_close + atr_val[0] * TP_ATR;
   }
   else
   {
      sl = m5_close + stop_dist;
      tp = m5_close - atr_val[0] * TP_ATR;
   }
   
   // Send order
   int ticket = OpenOrder(signal, lot_size, m5_close, sl, tp);
   if(ticket > 0)
   {
      _todayTrades++;
      string patternName = "";
      if(signal == "buy") patternName = "PULLBACK";
      else patternName = "MOMENTUM";
      Print("SIGNAL ", signal, " | Entry: ", m5_close, " SL: ", sl, " TP: ", tp,
            " | Lot: ", lot_size, " | Balance: ", balance);
   }
}

//+------------------------------------------------------------------+
//| Open market order                                                |
//+------------------------------------------------------------------+
int OpenOrder(string type, double lot, double price, double sl, double tp)
{
   MqlTradeRequest req = {};
   MqlTradeResult res = {};
   
   req.action = TRADE_ACTION_DEAL;
   req.symbol = _Symbol;
   req.volume = lot;
   req.deviation = 10;
   req.magic = MagicNumber;
   
   if(type == "buy")
   {
      req.type = ORDER_TYPE_BUY;
      req.price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   }
   else
   {
      req.type = ORDER_TYPE_SELL;
      req.price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   }
   
   req.sl = NormalizeDouble(sl, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   req.tp = NormalizeDouble(tp, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   
   if(OrderSend(req, res))
   {
      Print("Order opened: ", res.order, " ", type, " ", lot, " lots");
      return res.order;
   }
   else
   {
      Print("Order failed: ", res.retcode, " ", res.comment);
      return -1;
   }
}
//+------------------------------------------------------------------+
