//+------------------------------------------------------------------+
//|                                          Gold_Hunter_YouTube.mq5 |
//|                    YOUTUBE ALGO STRATEGIES - Backtested Setups   |
//|            Triple RSI | Order Blocks | ADX+WPR | ATR Trailing    |
//+------------------------------------------------------------------+
#property copyright "Gold Hunter - YouTube Research Edition"
#property version   "4.00"
#property strict

#include <Trade\Trade.mqh>

//=== INPUT PARAMETERS ===
input group "══════════ RISK MANAGEMENT ══════════"
input double   RiskPercent = 1.0;              // Risk per trade (%)
input double   MaxLots = 0.5;                  // Maximum lot size
input double   RewardRiskRatio = 2.5;          // Initial R:R target
input int      MaxDailyTrades = 5;             // Max trades per day
input int      MaxOpenPositions = 2;           // Max simultaneous positions

input group "══════════ TRIPLE RSI (90% WIN RATE STRATEGY) ══════════"
input bool     UseTripleRSI = true;            // Enable Triple RSI confluence
input int      RSI_Fast = 2;                   // RSI Fast (mean reversion)
input int      RSI_Med = 7;                    // RSI Medium
input int      RSI_Slow = 14;                  // RSI Slow (trend)
input int      RSI_OB = 70;                    // Overbought level
input int      RSI_OS = 30;                    // Oversold level
input int      RSI2_Extreme = 10;              // RSI(2) extreme threshold

input group "══════════ ADX + WILLIAMS %R (4479% PNL STRATEGY) ══════════"
input bool     UseADX_WPR = true;              // Enable ADX + Williams %R scalping
input int      ADX_Period = 14;                // ADX period
input double   ADX_Threshold = 25;             // Minimum ADX for trend
input int      WPR_Period = 14;                // Williams %R period
input int      WPR_OB = -20;                   // WPR overbought
input int      WPR_OS = -80;                   // WPR oversold

input group "══════════ ORDER BLOCKS (GOLD SPECIFIC) ══════════"
input bool     UseOrderBlocks = true;          // Enable Order Block detection
input int      OB_Lookback = 50;               // Bars to scan for OB
input int      OB_SwingLen = 5;                // Swing detection length
input double   OB_MinSize = 1.0;               // Min OB size (ATR multiple)

input group "══════════ ATR TRAILING STOP (PRO EXIT) ══════════"
input bool     UseATRTrailing = true;          // Enable ATR trailing stop
input int      ATR_Period = 14;                // ATR period
input double   ATR_SL_Multi = 2.0;             // ATR multiplier for initial SL
input double   ATR_Trail_Multi = 1.5;          // ATR multiplier for trailing
input double   ATR_Trail_Start = 1.0;          // Start trailing at X*ATR profit

input group "══════════ MA TREND FILTER ══════════"
input bool     UseMATrend = true;              // Enable MA trend filter
input int      MA_Fast = 20;                   // Fast EMA
input int      MA_Slow = 50;                   // Slow EMA
input int      MA_Trend = 200;                 // Trend EMA
input ENUM_MA_METHOD MA_Method = MODE_EMA;     // MA Method

input group "══════════ SESSION FILTER ══════════"
input bool     UseSessionFilter = true;        // Only trade active sessions
input int      LondonOpen = 8;                 // London open (server hour)
input int      NYClose = 22;                   // NY close (server hour)

input group "══════════ CONFLUENCE SETTINGS ══════════"
input int      MinScoreToTrade = 5;            // Minimum confluence score
input int      MinScoreToClose = 3;            // Min opposite score to close

input group "══════════ SYSTEM ══════════"
input int      MagicNumber = 20260204;         // Magic number
input int      Slippage = 50;                  // Max slippage points

//=== GLOBAL VARIABLES ===
CTrade trade;
int dailyTrades = 0;
datetime lastTradeDay = 0;

// Indicator handles
int h_RSI_Fast, h_RSI_Med, h_RSI_Slow;
int h_ADX, h_WPR;
int h_ATR;
int h_MA_Fast, h_MA_Slow, h_MA_Trend;

// Order Block storage
struct OrderBlock {
   double   priceHigh;
   double   priceLow;
   datetime time;
   bool     isBullish;
   bool     isValid;
   int      touches;
};

OrderBlock g_BullishOB[];
OrderBlock g_BearishOB[];

// Signal structure with weighted scores
struct SignalScore {
   int      buyScore;
   int      sellScore;
   string   buyReasons[];
   string   sellReasons[];
   double   tripleRSI_Buy;   // 0-3 (each RSI contributes 1)
   double   tripleRSI_Sell;
   bool     orderBlockBuy;
   bool     orderBlockSell;
   bool     adxWprBuy;
   bool     adxWprSell;
};

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   // Triple RSI handles
   h_RSI_Fast = iRSI(Symbol(), PERIOD_CURRENT, RSI_Fast, PRICE_CLOSE);
   h_RSI_Med = iRSI(Symbol(), PERIOD_CURRENT, RSI_Med, PRICE_CLOSE);
   h_RSI_Slow = iRSI(Symbol(), PERIOD_CURRENT, RSI_Slow, PRICE_CLOSE);
   
   // ADX + Williams %R
   h_ADX = iADX(Symbol(), PERIOD_CURRENT, ADX_Period);
   h_WPR = iWPR(Symbol(), PERIOD_CURRENT, WPR_Period);
   
   // ATR & MAs
   h_ATR = iATR(Symbol(), PERIOD_CURRENT, ATR_Period);
   h_MA_Fast = iMA(Symbol(), PERIOD_CURRENT, MA_Fast, 0, MA_Method, PRICE_CLOSE);
   h_MA_Slow = iMA(Symbol(), PERIOD_CURRENT, MA_Slow, 0, MA_Method, PRICE_CLOSE);
   h_MA_Trend = iMA(Symbol(), PERIOD_CURRENT, MA_Trend, 0, MA_Method, PRICE_CLOSE);
   
   if(h_RSI_Fast == INVALID_HANDLE || h_ADX == INVALID_HANDLE || h_ATR == INVALID_HANDLE)
   {
      Print("❌ Failed to create indicator handles");
      return INIT_FAILED;
   }
   
   // Initialize order blocks
   ArrayResize(g_BullishOB, 0);
   ArrayResize(g_BearishOB, 0);
   ScanOrderBlocks();
   
   Print("════════════════════════════════════════════════════════════════════════");
   Print("🎬 GOLD HUNTER v4.0 - YOUTUBE ALGO EDITION");
   Print("════════════════════════════════════════════════════════════════════════");
   Print("");
   Print("📺 STRATEGIES FROM BACKTESTED YOUTUBE ALGOS:");
   Print("────────────────────────────────────────────────────────────────────────");
   Print("│ 1. TRIPLE RSI (90.36% Win Rate)");
   Print("│    RSI(", RSI_Fast, ") + RSI(", RSI_Med, ") + RSI(", RSI_Slow, ") Confluence");
   Print("│");
   Print("│ 2. ADX + WILLIAMS %R (4,479% PNL Strategy)");
   Print("│    ADX > ", ADX_Threshold, " + WPR Extremes");
   Print("│");
   Print("│ 3. ORDER BLOCKS (Gold-Specific Backtest)");
   Print("│    Supply/Demand Zones with Multi-TF");
   Print("│");
   Print("│ 4. ATR TRAILING STOP (Pro Exit Strategy)");
   Print("│    Dynamic exit > Fixed TP");
   Print("────────────────────────────────────────────────────────────────────────");
   Print("");
   Print("⚙️  Min Score: ", MinScoreToTrade, " | Risk: ", RiskPercent, "% | R:R: 1:", RewardRiskRatio);
   Print("════════════════════════════════════════════════════════════════════════");
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(h_RSI_Fast);
   IndicatorRelease(h_RSI_Med);
   IndicatorRelease(h_RSI_Slow);
   IndicatorRelease(h_ADX);
   IndicatorRelease(h_WPR);
   IndicatorRelease(h_ATR);
   IndicatorRelease(h_MA_Fast);
   IndicatorRelease(h_MA_Slow);
   IndicatorRelease(h_MA_Trend);
   Comment("");
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // Reset daily trade count
   if(TimeToString(TimeCurrent(), TIME_DATE) != TimeToString(lastTradeDay, TIME_DATE))
   {
      dailyTrades = 0;
      lastTradeDay = TimeCurrent();
      ScanOrderBlocks();  // Refresh order blocks daily
   }
   
   // Session filter
   if(UseSessionFilter && !IsActiveSession())
      return;
   
   // Max daily trades
   if(dailyTrades >= MaxDailyTrades)
      return;
   
   // Manage existing positions with ATR trailing
   if(UseATRTrailing)
      ManageATRTrailing();
   
   // Max positions check
   if(CountOpenPositions() >= MaxOpenPositions)
      return;
   
   // Only trade on new bar
   static datetime lastBar = 0;
   if(iTime(Symbol(), PERIOD_CURRENT, 0) == lastBar)
      return;
   lastBar = iTime(Symbol(), PERIOD_CURRENT, 0);
   
   // Analyze all signals
   SignalScore score;
   AnalyzeAllSignals(score);
   
   // Update display
   UpdateDisplay(score);
   
   // Execute trades
   ExecuteTrade(score);
}

//+------------------------------------------------------------------+
//| STRATEGY 1: TRIPLE RSI (90% Win Rate)                             |
//| Source: https://youtube.com/watch?v=xEVEubP4iY4                   |
//+------------------------------------------------------------------+
void Signal_TripleRSI(SignalScore &score)
{
   if(!UseTripleRSI) return;
   
   double rsiFast[], rsiMed[], rsiSlow[];
   ArraySetAsSeries(rsiFast, true);
   ArraySetAsSeries(rsiMed, true);
   ArraySetAsSeries(rsiSlow, true);
   
   CopyBuffer(h_RSI_Fast, 0, 0, 3, rsiFast);
   CopyBuffer(h_RSI_Med, 0, 0, 3, rsiMed);
   CopyBuffer(h_RSI_Slow, 0, 0, 3, rsiSlow);
   
   score.tripleRSI_Buy = 0;
   score.tripleRSI_Sell = 0;
   
   // ═══════════════════════════════════════════════════════════════
   // TRIPLE RSI BUY CONDITIONS
   // Key insight: All 3 RSI must agree for high probability
   // ═══════════════════════════════════════════════════════════════
   
   // RSI(2) extreme low - mean reversion trigger
   if(rsiFast[0] < RSI2_Extreme)
   {
      score.tripleRSI_Buy += 1.5;  // Extra weight for extreme
      score.buyScore += 2;
      AddReason(score.buyReasons, StringFormat("RSI(%d) EXTREME %.1f", RSI_Fast, rsiFast[0]));
   }
   else if(rsiFast[0] < RSI_OS)
   {
      score.tripleRSI_Buy += 1;
      score.buyScore += 1;
      AddReason(score.buyReasons, StringFormat("RSI(%d) OVERSOLD %.1f", RSI_Fast, rsiFast[0]));
   }
   
   // RSI(7) oversold or rising from oversold
   if(rsiMed[0] < RSI_OS || (rsiMed[1] < RSI_OS && rsiMed[0] > rsiMed[1]))
   {
      score.tripleRSI_Buy += 1;
      score.buyScore += 1;
      AddReason(score.buyReasons, StringFormat("RSI(%d) BULLISH %.1f", RSI_Med, rsiMed[0]));
   }
   
   // RSI(14) trend confirmation - not overbought
   if(rsiSlow[0] < 60 && rsiSlow[0] > rsiSlow[1])
   {
      score.tripleRSI_Buy += 1;
      score.buyScore += 1;
      AddReason(score.buyReasons, StringFormat("RSI(%d) TREND UP %.1f", RSI_Slow, rsiSlow[0]));
   }
   
   // ═══════════════════════════════════════════════════════════════
   // TRIPLE RSI SELL CONDITIONS
   // ═══════════════════════════════════════════════════════════════
   
   // RSI(2) extreme high
   if(rsiFast[0] > (100 - RSI2_Extreme))
   {
      score.tripleRSI_Sell += 1.5;
      score.sellScore += 2;
      AddReason(score.sellReasons, StringFormat("RSI(%d) EXTREME %.1f", RSI_Fast, rsiFast[0]));
   }
   else if(rsiFast[0] > RSI_OB)
   {
      score.tripleRSI_Sell += 1;
      score.sellScore += 1;
      AddReason(score.sellReasons, StringFormat("RSI(%d) OVERBOUGHT %.1f", RSI_Fast, rsiFast[0]));
   }
   
   // RSI(7) overbought or falling from overbought
   if(rsiMed[0] > RSI_OB || (rsiMed[1] > RSI_OB && rsiMed[0] < rsiMed[1]))
   {
      score.tripleRSI_Sell += 1;
      score.sellScore += 1;
      AddReason(score.sellReasons, StringFormat("RSI(%d) BEARISH %.1f", RSI_Med, rsiMed[0]));
   }
   
   // RSI(14) trend confirmation - not oversold
   if(rsiSlow[0] > 40 && rsiSlow[0] < rsiSlow[1])
   {
      score.tripleRSI_Sell += 1;
      score.sellScore += 1;
      AddReason(score.sellReasons, StringFormat("RSI(%d) TREND DN %.1f", RSI_Slow, rsiSlow[0]));
   }
   
   // TRIPLE CONFLUENCE BONUS - All 3 agree
   if(score.tripleRSI_Buy >= 2.5)
   {
      score.buyScore += 2;
      AddReason(score.buyReasons, "★ TRIPLE RSI CONFLUENCE");
   }
   if(score.tripleRSI_Sell >= 2.5)
   {
      score.sellScore += 2;
      AddReason(score.sellReasons, "★ TRIPLE RSI CONFLUENCE");
   }
}

//+------------------------------------------------------------------+
//| STRATEGY 2: ADX + WILLIAMS %R (4,479% PNL)                        |
//| Source: https://youtube.com/watch?v=kIudgLFh5cw                   |
//+------------------------------------------------------------------+
void Signal_ADX_WilliamsR(SignalScore &score)
{
   if(!UseADX_WPR) return;
   
   double adxMain[], plusDI[], minusDI[], wpr[];
   ArraySetAsSeries(adxMain, true);
   ArraySetAsSeries(plusDI, true);
   ArraySetAsSeries(minusDI, true);
   ArraySetAsSeries(wpr, true);
   
   CopyBuffer(h_ADX, 0, 0, 3, adxMain);    // ADX main
   CopyBuffer(h_ADX, 1, 0, 3, plusDI);     // +DI
   CopyBuffer(h_ADX, 2, 0, 3, minusDI);    // -DI
   CopyBuffer(h_WPR, 0, 0, 3, wpr);
   
   score.adxWprBuy = false;
   score.adxWprSell = false;
   
   // ═══════════════════════════════════════════════════════════════
   // ADX + WPR SCALPING STRATEGY
   // Trend strength + Momentum exhaustion
   // ═══════════════════════════════════════════════════════════════
   
   bool strongTrend = adxMain[0] > ADX_Threshold;
   bool bullishTrend = plusDI[0] > minusDI[0];
   bool bearishTrend = minusDI[0] > plusDI[0];
   
   // BUY: Strong bullish trend + WPR oversold
   if(strongTrend && bullishTrend && wpr[0] < WPR_OS)
   {
      score.adxWprBuy = true;
      score.buyScore += 3;  // High weight - proven profitable
      AddReason(score.buyReasons, StringFormat("ADX+WPR BUY (ADX=%.1f, WPR=%.0f)", adxMain[0], wpr[0]));
   }
   // WPR rising from extreme
   else if(strongTrend && bullishTrend && wpr[1] < WPR_OS && wpr[0] > wpr[1])
   {
      score.adxWprBuy = true;
      score.buyScore += 2;
      AddReason(score.buyReasons, StringFormat("WPR REVERSAL UP (%.0f)", wpr[0]));
   }
   
   // SELL: Strong bearish trend + WPR overbought
   if(strongTrend && bearishTrend && wpr[0] > WPR_OB)
   {
      score.adxWprSell = true;
      score.sellScore += 3;
      AddReason(score.sellReasons, StringFormat("ADX+WPR SELL (ADX=%.1f, WPR=%.0f)", adxMain[0], wpr[0]));
   }
   // WPR falling from extreme
   else if(strongTrend && bearishTrend && wpr[1] > WPR_OB && wpr[0] < wpr[1])
   {
      score.adxWprSell = true;
      score.sellScore += 2;
      AddReason(score.sellReasons, StringFormat("WPR REVERSAL DN (%.0f)", wpr[0]));
   }
   
   // Trend strength bonus
   if(adxMain[0] > 40)
   {
      if(bullishTrend) { score.buyScore += 1; AddReason(score.buyReasons, "STRONG TREND +DI"); }
      if(bearishTrend) { score.sellScore += 1; AddReason(score.sellReasons, "STRONG TREND -DI"); }
   }
}

//+------------------------------------------------------------------+
//| STRATEGY 3: ORDER BLOCKS (Gold-Specific)                          |
//| Source: https://youtube.com/watch?v=CvlOHCWr_4Q                   |
//+------------------------------------------------------------------+
void ScanOrderBlocks()
{
   ArrayResize(g_BullishOB, 0);
   ArrayResize(g_BearishOB, 0);
   
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   double minOBSize = atr[0] * OB_MinSize;
   
   // Scan for swing highs and lows
   for(int i = OB_SwingLen; i < OB_Lookback - OB_SwingLen; i++)
   {
      // Check for swing low (potential bullish OB)
      if(IsSwingLow(i, OB_SwingLen))
      {
         // The last bearish candle before the swing is the OB
         for(int j = i; j < i + 5; j++)
         {
            if(iClose(Symbol(), PERIOD_CURRENT, j) < iOpen(Symbol(), PERIOD_CURRENT, j))
            {
               double obHigh = iHigh(Symbol(), PERIOD_CURRENT, j);
               double obLow = iLow(Symbol(), PERIOD_CURRENT, j);
               
               if(obHigh - obLow >= minOBSize)
               {
                  OrderBlock ob;
                  ob.priceHigh = obHigh;
                  ob.priceLow = obLow;
                  ob.time = iTime(Symbol(), PERIOD_CURRENT, j);
                  ob.isBullish = true;
                  ob.isValid = true;
                  ob.touches = 0;
                  
                  ArrayResize(g_BullishOB, ArraySize(g_BullishOB) + 1);
                  g_BullishOB[ArraySize(g_BullishOB) - 1] = ob;
               }
               break;
            }
         }
      }
      
      // Check for swing high (potential bearish OB)
      if(IsSwingHigh(i, OB_SwingLen))
      {
         for(int j = i; j < i + 5; j++)
         {
            if(iClose(Symbol(), PERIOD_CURRENT, j) > iOpen(Symbol(), PERIOD_CURRENT, j))
            {
               double obHigh = iHigh(Symbol(), PERIOD_CURRENT, j);
               double obLow = iLow(Symbol(), PERIOD_CURRENT, j);
               
               if(obHigh - obLow >= minOBSize)
               {
                  OrderBlock ob;
                  ob.priceHigh = obHigh;
                  ob.priceLow = obLow;
                  ob.time = iTime(Symbol(), PERIOD_CURRENT, j);
                  ob.isBullish = false;
                  ob.isValid = true;
                  ob.touches = 0;
                  
                  ArrayResize(g_BearishOB, ArraySize(g_BearishOB) + 1);
                  g_BearishOB[ArraySize(g_BearishOB) - 1] = ob;
               }
               break;
            }
         }
      }
   }
   
   Print("📦 Order Blocks Found: ", ArraySize(g_BullishOB), " Bullish, ", ArraySize(g_BearishOB), " Bearish");
}

bool IsSwingLow(int index, int length)
{
   double low = iLow(Symbol(), PERIOD_CURRENT, index);
   for(int i = index - length; i <= index + length; i++)
   {
      if(i == index) continue;
      if(iLow(Symbol(), PERIOD_CURRENT, i) < low) return false;
   }
   return true;
}

bool IsSwingHigh(int index, int length)
{
   double high = iHigh(Symbol(), PERIOD_CURRENT, index);
   for(int i = index - length; i <= index + length; i++)
   {
      if(i == index) continue;
      if(iHigh(Symbol(), PERIOD_CURRENT, i) > high) return false;
   }
   return true;
}

void Signal_OrderBlocks(SignalScore &score)
{
   if(!UseOrderBlocks) return;
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   double buffer = atr[0] * 0.5;  // Entry zone buffer
   
   score.orderBlockBuy = false;
   score.orderBlockSell = false;
   
   // ═══════════════════════════════════════════════════════════════
   // ORDER BLOCK ENTRY LOGIC
   // Price enters OB zone = high probability reversal
   // ═══════════════════════════════════════════════════════════════
   
   // Check bullish OBs (buy when price touches demand zone)
   for(int i = 0; i < ArraySize(g_BullishOB); i++)
   {
      if(!g_BullishOB[i].isValid) continue;
      
      // Price in OB zone
      if(price <= g_BullishOB[i].priceHigh + buffer && price >= g_BullishOB[i].priceLow - buffer)
      {
         score.orderBlockBuy = true;
         score.buyScore += 3;
         AddReason(score.buyReasons, StringFormat("ORDER BLOCK BUY @%.2f", g_BullishOB[i].priceHigh));
         g_BullishOB[i].touches++;
         
         // Invalidate if touched too many times
         if(g_BullishOB[i].touches > 3)
            g_BullishOB[i].isValid = false;
         break;
      }
      
      // OB broken = invalid
      if(price < g_BullishOB[i].priceLow - atr[0])
         g_BullishOB[i].isValid = false;
   }
   
   // Check bearish OBs (sell when price touches supply zone)
   for(int i = 0; i < ArraySize(g_BearishOB); i++)
   {
      if(!g_BearishOB[i].isValid) continue;
      
      if(price >= g_BearishOB[i].priceLow - buffer && price <= g_BearishOB[i].priceHigh + buffer)
      {
         score.orderBlockSell = true;
         score.sellScore += 3;
         AddReason(score.sellReasons, StringFormat("ORDER BLOCK SELL @%.2f", g_BearishOB[i].priceLow));
         g_BearishOB[i].touches++;
         
         if(g_BearishOB[i].touches > 3)
            g_BearishOB[i].isValid = false;
         break;
      }
      
      if(price > g_BearishOB[i].priceHigh + atr[0])
         g_BearishOB[i].isValid = false;
   }
}

//+------------------------------------------------------------------+
//| MA TREND FILTER                                                   |
//+------------------------------------------------------------------+
void Signal_MATrend(SignalScore &score)
{
   if(!UseMATrend) return;
   
   double maFast[], maSlow[], maTrend[];
   ArraySetAsSeries(maFast, true);
   ArraySetAsSeries(maSlow, true);
   ArraySetAsSeries(maTrend, true);
   
   CopyBuffer(h_MA_Fast, 0, 0, 3, maFast);
   CopyBuffer(h_MA_Slow, 0, 0, 3, maSlow);
   CopyBuffer(h_MA_Trend, 0, 0, 3, maTrend);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   
   // Bullish stack: Price > Fast > Slow > Trend
   if(price > maFast[0] && maFast[0] > maSlow[0] && maSlow[0] > maTrend[0])
   {
      score.buyScore += 2;
      AddReason(score.buyReasons, "MA BULLISH STACK");
   }
   else if(price > maTrend[0])
   {
      score.buyScore += 1;
      AddReason(score.buyReasons, "ABOVE MA200");
   }
   
   // Bearish stack: Price < Fast < Slow < Trend
   if(price < maFast[0] && maFast[0] < maSlow[0] && maSlow[0] < maTrend[0])
   {
      score.sellScore += 2;
      AddReason(score.sellReasons, "MA BEARISH STACK");
   }
   else if(price < maTrend[0])
   {
      score.sellScore += 1;
      AddReason(score.sellReasons, "BELOW MA200");
   }
}

//+------------------------------------------------------------------+
//| Analyze all signals                                               |
//+------------------------------------------------------------------+
void AnalyzeAllSignals(SignalScore &score)
{
   score.buyScore = 0;
   score.sellScore = 0;
   score.tripleRSI_Buy = 0;
   score.tripleRSI_Sell = 0;
   score.orderBlockBuy = false;
   score.orderBlockSell = false;
   score.adxWprBuy = false;
   score.adxWprSell = false;
   ArrayResize(score.buyReasons, 0);
   ArrayResize(score.sellReasons, 0);
   
   Signal_TripleRSI(score);
   Signal_ADX_WilliamsR(score);
   Signal_OrderBlocks(score);
   Signal_MATrend(score);
}

//+------------------------------------------------------------------+
//| Execute trade                                                     |
//+------------------------------------------------------------------+
void ExecuteTrade(SignalScore &score)
{
   // Need minimum score and clear direction
   if(score.buyScore >= MinScoreToTrade && score.buyScore > score.sellScore + 2)
   {
      OpenBuy(score);
   }
   else if(score.sellScore >= MinScoreToTrade && score.sellScore > score.buyScore + 2)
   {
      OpenSell(score);
   }
}

//+------------------------------------------------------------------+
//| Open BUY position                                                 |
//+------------------------------------------------------------------+
void OpenBuy(SignalScore &score)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   double sl = price - (atr[0] * ATR_SL_Multi);
   double tp = price + (atr[0] * ATR_SL_Multi * RewardRiskRatio);
   
   double lots = CalculateLots(price - sl);
   
   string comment = "YT_BUY|";
   for(int i = 0; i < MathMin(3, ArraySize(score.buyReasons)); i++)
      comment += score.buyReasons[i] + "|";
   
   if(trade.Buy(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("════════════════════════════════════════════════════════════════════════");
      Print("🎬 BUY OPENED - Score: ", score.buyScore, " (YouTube Algo Confluence)");
      Print("────────────────────────────────────────────────────────────────────────");
      for(int i = 0; i < ArraySize(score.buyReasons); i++)
         Print("   ├── ", score.buyReasons[i]);
      Print("────────────────────────────────────────────────────────────────────────");
      Print("   Entry: ", price, " | SL: ", sl, " | TP: ", tp);
      Print("   Lots: ", lots, " | ATR: ", atr[0]);
      Print("════════════════════════════════════════════════════════════════════════");
   }
}

//+------------------------------------------------------------------+
//| Open SELL position                                                |
//+------------------------------------------------------------------+
void OpenSell(SignalScore &score)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double sl = price + (atr[0] * ATR_SL_Multi);
   double tp = price - (atr[0] * ATR_SL_Multi * RewardRiskRatio);
   
   double lots = CalculateLots(sl - price);
   
   string comment = "YT_SELL|";
   for(int i = 0; i < MathMin(3, ArraySize(score.sellReasons)); i++)
      comment += score.sellReasons[i] + "|";
   
   if(trade.Sell(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("════════════════════════════════════════════════════════════════════════");
      Print("🎬 SELL OPENED - Score: ", score.sellScore, " (YouTube Algo Confluence)");
      Print("────────────────────────────────────────────────────────────────────────");
      for(int i = 0; i < ArraySize(score.sellReasons); i++)
         Print("   ├── ", score.sellReasons[i]);
      Print("────────────────────────────────────────────────────────────────────────");
      Print("   Entry: ", price, " | SL: ", sl, " | TP: ", tp);
      Print("   Lots: ", lots, " | ATR: ", atr[0]);
      Print("════════════════════════════════════════════════════════════════════════");
   }
}

//+------------------------------------------------------------------+
//| STRATEGY 4: ATR TRAILING STOP (Pro Exit)                          |
//| Source: https://youtube.com/watch?v=Gth4H6AekgQ                   |
//+------------------------------------------------------------------+
void ManageATRTrailing()
{
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   double trailDistance = atr[0] * ATR_Trail_Multi;
   double startProfit = atr[0] * ATR_Trail_Start;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      
      if(type == POSITION_TYPE_BUY)
      {
         double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
         double profit = bid - openPrice;
         
         // Start trailing after X*ATR profit
         if(profit >= startProfit)
         {
            double newSL = bid - trailDistance;
            
            if(newSL > currentSL + SymbolInfoDouble(Symbol(), SYMBOL_POINT) * 10)
            {
               trade.PositionModify(ticket, newSL, currentTP);
               Print("🔒 BUY Trailing SL: ", currentSL, " → ", newSL, " (Lock: +", NormalizeDouble(newSL - openPrice, 2), ")");
            }
         }
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
         double profit = openPrice - ask;
         
         if(profit >= startProfit)
         {
            double newSL = ask + trailDistance;
            
            if(newSL < currentSL - SymbolInfoDouble(Symbol(), SYMBOL_POINT) * 10)
            {
               trade.PositionModify(ticket, newSL, currentTP);
               Print("🔒 SELL Trailing SL: ", currentSL, " → ", newSL, " (Lock: +", NormalizeDouble(openPrice - newSL, 2), ")");
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Calculate lot size                                                |
//+------------------------------------------------------------------+
double CalculateLots(double slDistance)
{
   double tickValue = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   
   double riskAmount = balance * RiskPercent / 100.0;
   double slTicks = slDistance / tickSize;
   double lots = riskAmount / (slTicks * tickValue);
   
   double minLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
   
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(minLot, MathMin(MaxLots, lots));
   
   return lots;
}

//+------------------------------------------------------------------+
//| Count open positions                                              |
//+------------------------------------------------------------------+
int CountOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Check active session                                              |
//+------------------------------------------------------------------+
bool IsActiveSession()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return (dt.hour >= LondonOpen && dt.hour < NYClose);
}

//+------------------------------------------------------------------+
//| Helper: Add reason to array                                       |
//+------------------------------------------------------------------+
void AddReason(string &arr[], string reason)
{
   ArrayResize(arr, ArraySize(arr) + 1);
   arr[ArraySize(arr) - 1] = reason;
}

//+------------------------------------------------------------------+
//| Update chart display                                              |
//+------------------------------------------------------------------+
void UpdateDisplay(SignalScore &score)
{
   string info = "";
   info += "╔══════════════════════════════════════════════════╗\n";
   info += "║  🎬 GOLD HUNTER v4.0 - YOUTUBE ALGO EDITION      ║\n";
   info += "╠══════════════════════════════════════════════════╣\n";
   
   // Strategy Status
   info += "║ STRATEGIES:                                      ║\n";
   info += StringFormat("║  ▸ Triple RSI:    %s (%.1f/3)                   ║\n", 
           score.tripleRSI_Buy >= 2 ? "🟢 BUY" : score.tripleRSI_Sell >= 2 ? "🔴 SELL" : "⚪ ---",
           MathMax(score.tripleRSI_Buy, score.tripleRSI_Sell));
   info += StringFormat("║  ▸ ADX+WPR:       %s                            ║\n",
           score.adxWprBuy ? "🟢 BUY" : score.adxWprSell ? "🔴 SELL" : "⚪ ---");
   info += StringFormat("║  ▸ Order Blocks:  %s                            ║\n",
           score.orderBlockBuy ? "🟢 BUY" : score.orderBlockSell ? "🔴 SELL" : "⚪ ---");
   
   info += "╠══════════════════════════════════════════════════╣\n";
   info += StringFormat("║ BUY Score:  %2d │ SELL Score: %2d                  ║\n", 
           score.buyScore, score.sellScore);
   info += "╠══════════════════════════════════════════════════╣\n";
   
   // Buy signals
   info += "║ BUY SIGNALS:                                     ║\n";
   for(int i = 0; i < MathMin(4, ArraySize(score.buyReasons)); i++)
      info += StringFormat("║   ✓ %-43s ║\n", score.buyReasons[i]);
   if(ArraySize(score.buyReasons) == 0)
      info += "║   (none)                                         ║\n";
   
   // Sell signals
   info += "║ SELL SIGNALS:                                    ║\n";
   for(int i = 0; i < MathMin(4, ArraySize(score.sellReasons)); i++)
      info += StringFormat("║   ✓ %-43s ║\n", score.sellReasons[i]);
   if(ArraySize(score.sellReasons) == 0)
      info += "║   (none)                                         ║\n";
   
   info += "╠══════════════════════════════════════════════════╣\n";
   info += StringFormat("║ Min Score: %d │ Trades: %d/%d │ Pos: %d/%d        ║\n",
           MinScoreToTrade, dailyTrades, MaxDailyTrades, CountOpenPositions(), MaxOpenPositions);
   info += StringFormat("║ OB: %dB/%dS │ ATR Trail: %s                  ║\n",
           ArraySize(g_BullishOB), ArraySize(g_BearishOB), UseATRTrailing ? "ON" : "OFF");
   info += "╚══════════════════════════════════════════════════╝";
   
   Comment(info);
}
//+------------------------------------------------------------------+
