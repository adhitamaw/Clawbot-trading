//+------------------------------------------------------------------+
//| attach_xau.mq5 - Attach XAU_Executor to XAUUSD M5               |
//+------------------------------------------------------------------+
#property script_show_inputs
void OnStart()
{
   long chartID = ChartOpen("XAUUSD", PERIOD_M5);
   if(chartID > 0)
   {
      // Wait for chart to open
      Sleep(2000);
      ChartSetSymbolPeriod(chartID, "XAUUSD", PERIOD_M5);
      
      // Attach the EA
      string eaName = "XAU_Executor.ex5";
      if(ExpertRemove(chartID, eaName)) Print("Old EA removed");
      Sleep(500);
      
      long chartId = ChartOpen("XAUUSD", PERIOD_M5);
      ExpertAdd(chartId, eaName, 0, 0, false, 0, 0);
      ChartRedraw(chartId);
      
      Print("XAU_Executor attached to chart: ", chartID);
   }
   else
      Print("Failed to open chart");
}
