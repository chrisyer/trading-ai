//+------------------------------------------------------------------+
//|                                  Crellastein_MGC_SELL_SCALPER.mq5 |
//|                              Copyright 2026, AiiQ-tAIq Platform  |
//|                     MANUAL SELL SCALPER - MGC Micro Gold Futures |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, AiiQ-tAIq Platform"
#property link      "https://aiiq-taiq.com"
#property version   "1.00"
#property description "MGC MICRO GOLD FUTURES SELL SCALPER"
#property description "Drop on chart = Instant SELL with TP/SL"
#property description "TP: 25 pts | SL: 50 pts | 11 contracts"

#include <Trade\Trade.mqh>

//=== MGC SCALP SETTINGS ===
// MGC = Micro Gold Futures
// Contract value: $10 per point (1 tick = $1)
// 11 contracts = $110 per point move
// TP 25 pts = $275 profit target per contract ($3,025 total for 11)
// SL 50 pts = $550 max loss per contract ($6,050 total for 11)

input group "=== 📉 MGC SELL SCALPER SETTINGS ==="
input double LotSize = 11.0;              // Number of MGC contracts (11 units)
input double TP_Points = 25.0;            // Take Profit (points = $25 move)
input double SL_Points = 50.0;            // Stop Loss (points = $50 move)
input bool   AutoExecute = true;          // Execute immediately on attach
input int    MagicNumber = 8880912;       // Magic number for MGC SELL scalper

//=== ALTERNATIVE SETTINGS ===
input group "=== ⚙️ ALTERNATIVE SETTINGS ==="
input bool   UseConservative = false;     // Use conservative TP/SL (50/25)
input double ConservativeTP = 50.0;       // Conservative TP (50 pts = $5,500)
input double ConservativeSL = 25.0;       // Conservative SL (25 pts = $2,750)

CTrade g_trade;
bool g_executed = false;

//+------------------------------------------------------------------+
int OnInit()
{
   g_trade.SetExpertMagicNumber(MagicNumber);
   
   double actualTP = UseConservative ? ConservativeTP : TP_Points;
   double actualSL = UseConservative ? ConservativeSL : SL_Points;
   double riskPerPoint = LotSize * 10.0;  // $10 per point per contract
   
   Print("╔═══════════════════════════════════════════════════════════════════╗");
   Print("║        📉 MGC MICRO GOLD FUTURES SELL SCALPER                     ║");
   Print("╠═══════════════════════════════════════════════════════════════════╣");
   Print("║  Contracts: ", LotSize, " MGC");
   Print("║  Risk/Point: $", riskPerPoint);
   Print("║  TP: ", actualTP, " points ($", actualTP * riskPerPoint, ")");
   Print("║  SL: ", actualSL, " points ($", actualSL * riskPerPoint, ")");
   Print("║  Mode: ", UseConservative ? "CONSERVATIVE (2:1 R:R)" : "STANDARD");
   Print("╠═══════════════════════════════════════════════════════════════════╣");
   Print("║  Press 'S' to SELL manually anytime                               ║");
   Print("║  Press 'C' to CLOSE all SELL positions                            ║");
   Print("╚═══════════════════════════════════════════════════════════════════╝");
   
   // Auto-execute on attach
   if(AutoExecute && !g_executed)
   {
      ExecuteSell();
      g_executed = true;
   }
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("📉 MGC SELL SCALPER removed from chart");
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Nothing to do on tick - this is manual!
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   if(id == CHARTEVENT_KEYDOWN)
   {
      // Press 'S' to execute another SELL
      if(lparam == 'S')
      {
         ExecuteSell();
      }
      // Press 'C' to close all SELL positions
      if(lparam == 'C')
      {
         CloseAllSells();
      }
   }
}

//+------------------------------------------------------------------+
void ExecuteSell()
{
   string symbol = Symbol();
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   
   // Use appropriate TP/SL based on mode
   double actualTP = UseConservative ? ConservativeTP : TP_Points;
   double actualSL = UseConservative ? ConservativeSL : SL_Points;
   
   // MGC uses points directly (1 point = $1 price move)
   // For SELL: SL is above, TP is below
   double sl = NormalizeDouble(bid + actualSL, digits);
   double tp = NormalizeDouble(bid - actualTP, digits);
   
   double riskPerPoint = LotSize * 10.0;
   
   string comment = "MGC_SELL|" + IntegerToString((int)LotSize) + "x";
   
   Print("╔═══════════════════════════════════════════════════════════════════╗");
   Print("║  📉 EXECUTING MGC SELL SCALP - ", LotSize, " CONTRACTS             ║");
   Print("╠═══════════════════════════════════════════════════════════════════╣");
   Print("║  Symbol: ", symbol);
   Print("║  Entry: ", bid);
   Print("║  SL: ", sl, " (+$", actualSL * riskPerPoint, " risk)");
   Print("║  TP: ", tp, " (+$", actualTP * riskPerPoint, " target)");
   Print("║  Contracts: ", LotSize);
   Print("╚═══════════════════════════════════════════════════════════════════╝");
   
   if(g_trade.Sell(LotSize, symbol, bid, sl, tp, comment))
   {
      Print("✅ MGC SELL SCALP EXECUTED! ", LotSize, " contracts");
   }
   else
   {
      Print("❌ SELL FAILED: ", g_trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
void CloseAllSells()
{
   int closed = 0;
   double totalPL = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL)
         {
            if(PositionGetString(POSITION_SYMBOL) == Symbol())
            {
               totalPL += PositionGetDouble(POSITION_PROFIT);
               g_trade.PositionClose(ticket);
               closed++;
            }
         }
      }
   }
   Print("🔴 Closed ", closed, " MGC SELL positions | P&L: $", totalPL);
}

//+------------------------------------------------------------------+
// MGC Detection (Micro Gold Futures)
bool IsMGC()
{
   string symbol = Symbol();
   return (StringFind(symbol, "MGC") >= 0 || 
           StringFind(symbol, "XAU") >= 0 || 
           StringFind(symbol, "GOLD") >= 0);
}
//+------------------------------------------------------------------+
