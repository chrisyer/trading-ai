//+------------------------------------------------------------------+
//|                            Crellastein_MGC_BUY_SCALPER_SYNCED.mq5 |
//|                              Copyright 2026, AiiQ-tAIq Platform  |
//|                     SYNCED SCALPER - Mirrors to IBKR!            |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, AiiQ-tAIq Platform"
#property link      "https://aiiq-taiq.com"
#property version   "1.00"
#property description "MGC SYNCED SCALPER - Mirrors trades to IBKR"
#property description "Drop on chart = BUY in MT5 + IBKR simultaneously"
#property description "TP: 25 pts | SL: 50 pts | 11 contracts"

#include <Trade\Trade.mqh>

//=== SYNC SETTINGS ===
input group "=== 🔄 IBKR SYNC SETTINGS ==="
input string WebhookURL = "http://143.198.44.252:8460/api/trade/mirror";  // IBKR Mirror API
input bool   SyncToIBKR = true;           // Mirror trades to IBKR
input int    IBKRContracts = 11;          // Number of MGC contracts on IBKR

//=== MGC SCALP SETTINGS ===
input group "=== 📈 MGC SCALPER SETTINGS ==="
input double LotSize = 1.0;               // MT5 Lot size (1.0 for gold CFD)
input double TP_Points = 25.0;            // Take Profit (points)
input double SL_Points = 50.0;            // Stop Loss (points)
input bool   AutoExecute = true;          // Execute immediately on attach
input int    MagicNumber = 8880911;       // Magic number

CTrade g_trade;
bool g_executed = false;

//+------------------------------------------------------------------+
int OnInit()
{
   g_trade.SetExpertMagicNumber(MagicNumber);
   
   Print("╔═══════════════════════════════════════════════════════════════════╗");
   Print("║        📈 MGC SYNCED SCALPER - MT5 + IBKR MIRROR                  ║");
   Print("╠═══════════════════════════════════════════════════════════════════╣");
   Print("║  MT5 Lots: ", LotSize);
   Print("║  IBKR Contracts: ", IBKRContracts);
   Print("║  TP: ", TP_Points, " points");
   Print("║  SL: ", SL_Points, " points");
   Print("║  Sync to IBKR: ", SyncToIBKR ? "YES ✅" : "NO ❌");
   Print("╠═══════════════════════════════════════════════════════════════════╣");
   Print("║  Press 'B' to BUY (MT5 + IBKR)                                    ║");
   Print("║  Press 'S' to SELL (MT5 + IBKR)                                   ║");
   Print("║  Press 'C' to CLOSE all positions                                 ║");
   Print("╚═══════════════════════════════════════════════════════════════════╝");
   
   if(AutoExecute && !g_executed)
   {
      ExecuteBuy();
      g_executed = true;
   }
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("📈 MGC SYNCED SCALPER removed");
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Manual scalper - nothing on tick
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   if(id == CHARTEVENT_KEYDOWN)
   {
      if(lparam == 'B') ExecuteBuy();
      if(lparam == 'S') ExecuteSell();
      if(lparam == 'C') CloseAllPositions();
   }
}

//+------------------------------------------------------------------+
void ExecuteBuy()
{
   string symbol = Symbol();
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   
   double sl = NormalizeDouble(ask - SL_Points, digits);
   double tp = NormalizeDouble(ask + TP_Points, digits);
   
   string comment = "SYNC_BUY|" + IntegerToString(IBKRContracts) + "x";
   
   Print("╔═══════════════════════════════════════════════════════════════════╗");
   Print("║  📈 EXECUTING SYNCED BUY                                          ║");
   Print("╠═══════════════════════════════════════════════════════════════════╣");
   Print("║  MT5: ", LotSize, " lots @ ", ask);
   Print("║  IBKR: ", IBKRContracts, " MGC contracts");
   Print("║  SL: ", sl, " | TP: ", tp);
   Print("╚═══════════════════════════════════════════════════════════════════╝");
   
   // Execute on MT5
   if(g_trade.Buy(LotSize, symbol, ask, sl, tp, comment))
   {
      Print("✅ MT5 BUY EXECUTED!");
      
      // Mirror to IBKR
      if(SyncToIBKR)
      {
         SendToIBKR("BUY", ask, sl, tp);
      }
   }
   else
   {
      Print("❌ MT5 BUY FAILED: ", g_trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
void ExecuteSell()
{
   string symbol = Symbol();
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   
   double sl = NormalizeDouble(bid + SL_Points, digits);
   double tp = NormalizeDouble(bid - TP_Points, digits);
   
   string comment = "SYNC_SELL|" + IntegerToString(IBKRContracts) + "x";
   
   Print("╔═══════════════════════════════════════════════════════════════════╗");
   Print("║  📉 EXECUTING SYNCED SELL                                         ║");
   Print("╠═══════════════════════════════════════════════════════════════════╣");
   Print("║  MT5: ", LotSize, " lots @ ", bid);
   Print("║  IBKR: ", IBKRContracts, " MGC contracts");
   Print("║  SL: ", sl, " | TP: ", tp);
   Print("╚═══════════════════════════════════════════════════════════════════╝");
   
   if(g_trade.Sell(LotSize, symbol, bid, sl, tp, comment))
   {
      Print("✅ MT5 SELL EXECUTED!");
      
      if(SyncToIBKR)
      {
         SendToIBKR("SELL", bid, sl, tp);
      }
   }
   else
   {
      Print("❌ MT5 SELL FAILED: ", g_trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
void CloseAllPositions()
{
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket))
      {
         if(PositionGetString(POSITION_SYMBOL) == Symbol())
         {
            g_trade.PositionClose(ticket);
            closed++;
         }
      }
   }
   Print("🔴 Closed ", closed, " MT5 positions");
   
   // Also close on IBKR
   if(SyncToIBKR && closed > 0)
   {
      SendToIBKR("CLOSE", 0, 0, 0);
   }
}

//+------------------------------------------------------------------+
void SendToIBKR(string action, double price, double sl, double tp)
{
   // Build JSON payload
   string json = "{";
   json += "\"action\":\"" + action + "\",";
   json += "\"symbol\":\"MGC\",";
   json += "\"contracts\":" + IntegerToString(IBKRContracts) + ",";
   json += "\"price\":" + DoubleToString(price, 2) + ",";
   json += "\"sl\":" + DoubleToString(sl, 2) + ",";
   json += "\"tp\":" + DoubleToString(tp, 2) + ",";
   json += "\"source\":\"MT5_SCALPER\",";
   json += "\"magic\":" + IntegerToString(MagicNumber);
   json += "}";
   
   Print("🔄 Sending to IBKR: ", json);
   
   // Send HTTP POST request
   char post[];
   char result[];
   string headers = "Content-Type: application/json\r\n";
   
   StringToCharArray(json, post, 0, StringLen(json));
   ArrayResize(post, StringLen(json));
   
   int timeout = 5000;
   string resultHeaders;
   
   int res = WebRequest("POST", WebhookURL, headers, timeout, post, result, resultHeaders);
   
   if(res == -1)
   {
      int error = GetLastError();
      if(error == 4014)
      {
         Print("⚠️ Add this URL to MT5 allowed URLs:");
         Print("   Tools -> Options -> Expert Advisors -> Allow WebRequest for:");
         Print("   ", WebhookURL);
      }
      else
      {
         Print("❌ IBKR sync failed, error: ", error);
      }
   }
   else
   {
      string response = CharArrayToString(result);
      Print("✅ IBKR Response: ", response);
   }
}
//+------------------------------------------------------------------+
