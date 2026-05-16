//+------------------------------------------------------------------+
//|                                           XAU_Executor.mq5        |
//|                  XAU/USD Institutional Trading System             |
//|                    Hybrid EA for execution fallback                |
//+------------------------------------------------------------------+
#property copyright "XAU Institutional Trading System"
#property version   "1.00"
#property description "Hybrid Execution EA for XAUUSD Trading System"
#property description "Receives commands from Python via file/socket"
#property description "and executes with ultra-low latency."

// --- Input Parameters ---
input int      MagicNumber    = 20260516;    // Expert Magic Number
input double   MaxSpread      = 5.0;         // Max allowed spread (pips)
input double   MaxSlippage    = 20;          // Max slippage (points)
input bool     EnableAlerts   = true;        // Enable EA alerts
input string   CommandFile    = "xau_command.json"; // Command file path
input string   ResponseFile   = "xau_response.json"; // Response file path
input int      FilePollMs     = 50;          // File poll interval (ms)

// --- Global Variables ---
datetime lastBarTime = 0;
double atrValue = 0;
double adxValue = 0;
bool newsPause = false;
double dailyStartEquity = 0;
double peakEquity = 0;
double dailyDD = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set magic number for all orders
   Print("XAU_Executor initialized. Magic: ", MagicNumber);
   
   // Record daily start equity
   dailyStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   peakEquity = dailyStartEquity;
   
   // Ensure symbols are visible
   SymbolSelect("XAUUSD", true);
   SymbolSelect("DXY", true);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("XAU_Executor deinitialized. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function - main loop                                  |
//+------------------------------------------------------------------+
void OnTick()
{
   // --- New Bar Detection ---
   if(!IsNewBar())
      return;
   
   // --- Update Indicators ---
   UpdateIndicators();
   
   // --- Circuit Breaker Check ---
   if(!CheckCircuitBreaker())
      return;
   
   // --- News Pause Check ---
   if(newsPause)
      return;
   
   // --- Check for Python Commands ---
   CheckCommandFile();
   
   // --- Manage Existing Positions ---
   ManagePositions();
}

//+------------------------------------------------------------------+
//| Check if a new bar has formed (M5)                                |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime currentBar = iTime("XAUUSD", PERIOD_M5, 0);
   if(currentBar != lastBarTime)
   {
      lastBarTime = currentBar;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Update technical indicators                                       |
//+------------------------------------------------------------------+
void UpdateIndicators()
{
   // ATR(20) on M5
   int atrHandle = iATR("XAUUSD", PERIOD_M5, 20);
   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(atrHandle, 0, 0, 1, atrBuf) > 0)
      atrValue = atrBuf[0];
   IndicatorRelease(atrHandle);
   
   // ADX(14) on M5
   int adxHandle = iADX("XAUUSD", PERIOD_M5, 14);
   double adxBuf[];
   ArraySetAsSeries(adxBuf, true);
   if(CopyBuffer(adxHandle, 0, 0, 1, adxBuf) > 0)
      adxValue = adxBuf[0];
   IndicatorRelease(adxHandle);
   
   // Update equity tracking
   double currentEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(currentEquity > peakEquity)
      peakEquity = currentEquity;
   
   if(peakEquity > 0)
      dailyDD = (peakEquity - currentEquity) / peakEquity;
}

//+------------------------------------------------------------------+
//| Circuit Breaker - Hard DD 6%, Soft DD 4%                         |
//+------------------------------------------------------------------+
bool CheckCircuitBreaker()
{
   if(dailyDD >= 0.06)
   {
      // HARD BREAKER - Close all positions
      Print("HARD CIRCUIT BREAKER TRIGGERED - DD: ", DoubleToString(dailyDD * 100, 2), "%");
      CloseAllPositions();
      return false; // Block new trading
   }
   
   if(dailyDD >= 0.04)
   {
      // SOFT BREAKER - Reduce size by 50%
      Print("SOFT CIRCUIT BREAKER - DD: ", DoubleToString(dailyDD * 100, 2), "%");
      // Trading continues but with reduced size (handled in Python)
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Close all positions with our magic number                         |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         {
            string symbol = PositionGetString(POSITION_SYMBOL);
            double volume = PositionGetDouble(POSITION_VOLUME);
            long type = PositionGetInteger(POSITION_TYPE);
            
            MqlTradeRequest request = {};
            MqlTradeResult result = {};
            
            request.action = TRADE_ACTION_DEAL;
            request.symbol = symbol;
            request.volume = volume;
            request.type = (type == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
            request.position = ticket;
            request.price = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK);
            request.deviation = (int)MaxSlippage;
            request.magic = MagicNumber;
            request.comment = "CircuitBreaker";
            
            OrderSend(request, result);
            
            if(result.retcode == TRADE_RETCODE_DONE)
               Print("Position closed by circuit breaker: ", ticket);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Manage trailing stops and partial closes for open positions       |
//+------------------------------------------------------------------+
void ManagePositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket))
         continue;
      
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;
      
      string symbol = PositionGetString(POSITION_SYMBOL);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      double volume = PositionGetDouble(POSITION_VOLUME);
      long type = PositionGetInteger(POSITION_TYPE);
      
      double currentPrice = (type == POSITION_TYPE_BUY) ? 
         SymbolInfoDouble(symbol, SYMBOL_BID) : 
         SymbolInfoDouble(symbol, SYMBOL_ASK);
      
      double profitPips = (type == POSITION_TYPE_BUY) ?
         (currentPrice - openPrice) / SymbolInfoDouble(symbol, SYMBOL_POINT) :
         (openPrice - currentPrice) / SymbolInfoDouble(symbol, SYMBOL_POINT);
      
      // --- Trailing Stop (simple ATR-based) ---
      double trailDistance = atrValue * 1.5; // Configurable
      
      if(type == POSITION_TYPE_BUY)
      {
         double newSL = currentPrice - trailDistance;
         if(newSL > currentSL && currentSL > 0)
            ModifySLTP(ticket, newSL, currentTP);
         else if(profitPips > atrValue * 1.5 && currentSL == 0)
            ModifySLTP(ticket, openPrice, currentTP); // Breakeven
      }
      else
      {
         double newSL = currentPrice + trailDistance;
         if(newSL < currentSL && currentSL > 0)
            ModifySLTP(ticket, newSL, currentTP);
         else if(profitPips > atrValue * 1.5 && currentSL == 0)
            ModifySLTP(ticket, openPrice, currentTP); // Breakeven
      }
   }
}

//+------------------------------------------------------------------+
//| Modify Stop Loss and Take Profit                                  |
//+------------------------------------------------------------------+
bool ModifySLTP(ulong ticket, double sl, double tp)
{
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   
   PositionSelectByTicket(ticket);
   
   request.action = TRADE_ACTION_SLTP;
   request.position = ticket;
   request.symbol = PositionGetString(POSITION_SYMBOL);
   request.sl = sl;
   request.tp = tp;
   
   OrderSend(request, result);
   
   if(result.retcode == TRADE_RETCODE_DONE)
      return true;
   
   return false;
}

//+------------------------------------------------------------------+
//| Place a new market order                                          |
//+------------------------------------------------------------------+
ulong PlaceOrder(string symbol, int orderType, double volume, 
                 double sl, double tp, string comment = "")
{
   // Spread check
   double spread = (SymbolInfoDouble(symbol, SYMBOL_ASK) - SymbolInfoDouble(symbol, SYMBOL_BID)) 
                   / SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(spread > MaxSpread)
   {
      Print("Spread too high: ", spread, " pips. Order rejected.");
      return 0;
   }
   
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = volume;
   request.type = (ENUM_ORDER_TYPE)orderType;
   request.price = (orderType == ORDER_TYPE_BUY) ? 
      SymbolInfoDouble(symbol, SYMBOL_ASK) : 
      SymbolInfoDouble(symbol, SYMBOL_BID);
   request.sl = sl;
   request.tp = tp;
   request.deviation = (int)MaxSlippage;
   request.magic = MagicNumber;
   request.comment = comment != "" ? comment : "XAU_System";
   request.type_filling = ORDER_FILLING_IOC;
   
   OrderSend(request, result);
   
   if(result.retcode == TRADE_RETCODE_DONE)
   {
      Print("Order placed: ", result.order, " ", symbol, " vol=", volume);
      return result.order;
   }
   
   Print("Order failed. Retcode: ", result.retcode, " Comment: ", result.comment);
   return 0;
}

//+------------------------------------------------------------------+
//| Check for command file from Python                                |
//+------------------------------------------------------------------+
void CheckCommandFile()
{
   if(!FileIsExist(CommandFile))
      return;
   
   int handle = FileOpen(CommandFile, FILE_READ|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return;
   
   string content = FileReadString(handle, (int)FileSize(handle));
   FileClose(handle);
   
   // Delete command file after reading
   FileDelete(CommandFile);
   
   // Process command (simplified - production would parse JSON)
   // For now, this is a stub that Python can extend
   ProcessCommand(content);
}

//+------------------------------------------------------------------+
//| Process a command received from Python                            |
//+------------------------------------------------------------------+
void ProcessCommand(string jsonCmd)
{
   // Stub for Python-to-EA command processing
   // Full implementation would parse JSON commands:
   // {"action": "open", "type": "buy", "volume": 0.01, "sl": 2400.0, "tp": 2410.0}
   // {"action": "close", "ticket": 123456}
   // {"action": "modify", "ticket": 123456, "sl": 2410.0, "tp": 2420.0}
   // {"action": "news_pause", "enabled": true}
   
   Print("Command received from Python: ", jsonCmd);
   
   // Write response
   int resHandle = FileOpen(ResponseFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(resHandle != INVALID_HANDLE)
   {
      FileWrite(resHandle, "{\"status\":\"received\",\"timestamp\":", TimeCurrent(), "}");
      FileClose(resHandle);
   }
}

//+------------------------------------------------------------------+
//| Get current session based on UTC hour                             |
//+------------------------------------------------------------------+
string GetCurrentSession()
{
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   
   if(dt.hour >= 0 && dt.hour < 8)
      return "asian";
   else if(dt.hour >= 8 && dt.hour < 13)
      return "london";
   else if(dt.hour >= 13 && dt.hour < 17)
      return "ny_overlap";
   else
      return "late_ny";
}
//+------------------------------------------------------------------+
