//+------------------------------------------------------------------+
//|                                            Gold_Hunter_Live.mq5  |
//|              PRODUCTION EA: Observer + Controller + Strategy     |
//|                    AI can only TIGHTEN, never loosen             |
//+------------------------------------------------------------------+
#property copyright "AiiQ Gold Hunter"
#property version   "5.00"
#property strict

#include <Trade\Trade.mqh>
#include "Include\AiiQ_JsonIO.mqh"

//=== OBSERVER & CONTROLLER ===
input group "=== AI BRIDGE ==="
input bool   InpEnableObserver      = true;           // Export state for AI
input int    InpObserverSeconds     = 5;              // State export interval
input bool   InpEnableAIControl     = true;           // Read AI control file
input string InpStateFile           = "aiiq_state.json";    // State output file
input string InpControlFile         = "aiiq_control.json";  // Control input file

input group "=== AI SAFETY LIMITS (AI cannot exceed) ==="
input int    InpAI_MaxLayersFloor   = 1;              // Min layers AI can set
input double InpAI_DcaStepMinMult   = 1.0;            // Min DCA multiplier (>=1 = only widen)
input bool   InpAI_AllowDisableEntries = true;        // Allow AI to disable entries

//=== BASE TRADING PARAMETERS ===
input group "=== RISK MANAGEMENT ==="
input double InpRiskPercent = 1.0;                    // Risk per trade (%)
input double InpMaxLots = 0.5;                        // Hard max lots
input double InpRewardRisk = 2.0;                     // R:R ratio
input int    InpMaxLayers = 3;                        // Max DCA layers
input int    InpMaxDailyTrades = 10;                  // Max trades per day

input group "=== INDICATORS ==="
input int    InpMA_Fast = 20;
input int    InpMA_Slow = 50;
input int    InpMA_Trend = 200;
input int    InpRSI_Period = 14;
input int    InpRSI2_Period = 2;
input int    InpBB_Period = 20;
input double InpBB_Dev = 2.0;
input int    InpATR_Period = 14;
input double InpATR_SL_Mult = 2.0;
input int    InpMACD_Fast = 12;
input int    InpMACD_Slow = 26;
input int    InpMACD_Signal = 9;

input group "=== CONFLUENCE ==="
input int    InpMinSignals = 3;                       // Min signals to trade

input group "=== CHART VISION ==="
input bool   InpEnableChartShot   = true;           // Enable chart screenshots
input int    InpChartShotMinutes  = 15;             // Screenshot interval (minutes)
input string InpChartShotFile     = "aiiq_chart.png"; // Screenshot filename

input group "=== SYSTEM ==="
input int    InpMagic = 20260206;
input int    InpSlippage = 50;

//=== GLOBALS ===
CTrade trade;
int dailyTrades = 0;
datetime lastTradeDay = 0;
datetime g_lastObserverWrite = 0;
datetime g_lastChartShot = 0;

// AI Control Variables (read from control.json)
bool   g_disableNewEntries = false;
double g_dcaStepMult = 1.0;
int    g_maxLayersCap = 999;
double g_riskBias = 0;
string g_regime = "unknown";
string g_sentiment = "neutral";

// Indicator handles
int h_MA_Fast, h_MA_Slow, h_MA_Trend;
int h_RSI, h_RSI2;
int h_BB;
int h_ATR;
int h_MACD;

//+------------------------------------------------------------------+
//| Initialization                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   // Initialize indicators
   h_MA_Fast = iMA(Symbol(), PERIOD_CURRENT, InpMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_MA_Slow = iMA(Symbol(), PERIOD_CURRENT, InpMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   h_MA_Trend = iMA(Symbol(), PERIOD_CURRENT, InpMA_Trend, 0, MODE_EMA, PRICE_CLOSE);
   h_RSI = iRSI(Symbol(), PERIOD_CURRENT, InpRSI_Period, PRICE_CLOSE);
   h_RSI2 = iRSI(Symbol(), PERIOD_CURRENT, InpRSI2_Period, PRICE_CLOSE);
   h_BB = iBands(Symbol(), PERIOD_CURRENT, InpBB_Period, 0, InpBB_Dev, PRICE_CLOSE);
   h_ATR = iATR(Symbol(), PERIOD_CURRENT, InpATR_Period);
   h_MACD = iMACD(Symbol(), PERIOD_CURRENT, InpMACD_Fast, InpMACD_Slow, InpMACD_Signal, PRICE_CLOSE);
   
   if(h_MA_Fast == INVALID_HANDLE || h_RSI == INVALID_HANDLE)
   {
      Print("❌ Failed to create indicators");
      return INIT_FAILED;
   }
   
   Print("════════════════════════════════════════════════════════════════");
   Print("🥇 GOLD HUNTER LIVE v5.0");
   Print("════════════════════════════════════════════════════════════════");
   Print("Observer: ", InpEnableObserver ? "ON" : "OFF", " → ", InpStateFile);
   Print("AI Control: ", InpEnableAIControl ? "ON" : "OFF", " ← ", InpControlFile);
   Print("Safety: AI can only TIGHTEN risk, never loosen");
   Print("════════════════════════════════════════════════════════════════");
   
   EventSetTimer(1);
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Deinitialization                                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   IndicatorRelease(h_MA_Fast);
   IndicatorRelease(h_MA_Slow);
   IndicatorRelease(h_MA_Trend);
   IndicatorRelease(h_RSI);
   IndicatorRelease(h_RSI2);
   IndicatorRelease(h_BB);
   IndicatorRelease(h_ATR);
   IndicatorRelease(h_MACD);
   Comment("");
}

//+------------------------------------------------------------------+
//| Timer                                                             |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Periodic state export
   if(InpEnableObserver) WriteState();
}

//+------------------------------------------------------------------+
//| Main tick function                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1) Read AI controls FIRST (risk brakes apply immediately)
   if(InpEnableAIControl) ReadControl();
   
   // 2) Write state periodically
   if(InpEnableObserver) WriteState();
   
   // 3) Chart screenshot for vision agent
   MaybeChartScreenshot();
   
   // Reset daily trades
   if(TimeToString(TimeCurrent(), TIME_DATE) != TimeToString(lastTradeDay, TIME_DATE))
   {
      dailyTrades = 0;
      lastTradeDay = TimeCurrent();
   }
   
   // Only trade on new bar
   static datetime lastBar = 0;
   if(iTime(Symbol(), PERIOD_CURRENT, 0) == lastBar)
   {
      UpdateDisplay();
      return;
   }
   lastBar = iTime(Symbol(), PERIOD_CURRENT, 0);
   
   // Check if AI disabled entries
   if(g_disableNewEntries)
   {
      UpdateDisplay();
      return;
   }
   
   // Check limits (AI-adjusted)
   int maxLayers = MathMin(InpMaxLayers, g_maxLayersCap);
   if(dailyTrades >= InpMaxDailyTrades) return;
   if(CountPositions() >= maxLayers) return;
   
   // Analyze and execute
   int buySignals = 0, sellSignals = 0;
   string buyReasons[], sellReasons[];
   AnalyzeSignals(buySignals, sellSignals, buyReasons, sellReasons);
   
   // Execute trades
   if(buySignals >= InpMinSignals && buySignals > sellSignals + 1)
      OpenBuy(buySignals, buyReasons);
   else if(sellSignals >= InpMinSignals && sellSignals > buySignals + 1)
      OpenSell(sellSignals, sellReasons);
   
   // Manage positions
   ManagePositions(buySignals, sellSignals);
   
   UpdateDisplay();
}

//+------------------------------------------------------------------+
//| VISION: Chart screenshot for AI vision agent                      |
//+------------------------------------------------------------------+
void MaybeChartScreenshot()
{
   if(!InpEnableChartShot) return;
   
   datetime now = TimeCurrent();
   if(g_lastChartShot == 0) g_lastChartShot = now;
   
   // Only capture every N minutes
   if((now - g_lastChartShot) >= InpChartShotMinutes * 60)
   {
      long chart_id = ChartID();
      
      // 1280x720 is a good balance of quality vs file size
      bool ok = ChartScreenShot(chart_id, InpChartShotFile, 1280, 720, ALIGN_RIGHT);
      
      if(ok)
      {
         g_lastChartShot = now;
         Print("📸 Chart screenshot saved: ", InpChartShotFile);
      }
      else
      {
         Print("❌ Failed to save chart screenshot");
      }
   }
}

//+------------------------------------------------------------------+
//| OBSERVER: Export state to JSON                                    |
//+------------------------------------------------------------------+
void WriteState()
{
   datetime now = TimeCurrent();
   if((now - g_lastObserverWrite) < InpObserverSeconds) return;
   g_lastObserverWrite = now;
   
   // Get indicator values
   double maFast[], maSlow[], maTrend[];
   double rsi[], rsi2[];
   double bbUpper[], bbLower[], bbMiddle[];
   double atr[];
   double macdMain[], macdSignal[];
   
   ArraySetAsSeries(maFast, true);
   ArraySetAsSeries(maSlow, true);
   ArraySetAsSeries(maTrend, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(rsi2, true);
   ArraySetAsSeries(bbUpper, true);
   ArraySetAsSeries(bbLower, true);
   ArraySetAsSeries(bbMiddle, true);
   ArraySetAsSeries(atr, true);
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   
   CopyBuffer(h_MA_Fast, 0, 0, 1, maFast);
   CopyBuffer(h_MA_Slow, 0, 0, 1, maSlow);
   CopyBuffer(h_MA_Trend, 0, 0, 1, maTrend);
   CopyBuffer(h_RSI, 0, 0, 1, rsi);
   CopyBuffer(h_RSI2, 0, 0, 1, rsi2);
   CopyBuffer(h_BB, 1, 0, 1, bbUpper);
   CopyBuffer(h_BB, 2, 0, 1, bbLower);
   CopyBuffer(h_BB, 0, 0, 1, bbMiddle);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   CopyBuffer(h_MACD, 0, 0, 1, macdMain);
   CopyBuffer(h_MACD, 1, 0, 1, macdSignal);
   
   double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   int spread = (int)SymbolInfoInteger(Symbol(), SYMBOL_SPREAD);
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   
   // Position info
   int dir = 0;
   double netLots = 0, wavg = 0, floatingPnl = 0;
   int layers = GetPositionInfo(dir, netLots, wavg, floatingPnl);
   
   // Trend
   string trend = "neutral";
   if(bid > maFast[0] && maFast[0] > maSlow[0]) trend = "bullish";
   else if(bid < maFast[0] && maFast[0] < maSlow[0]) trend = "bearish";
   
   // BB position
   double bbPos = (bbUpper[0] > bbLower[0]) ? 
      (bid - bbLower[0]) / (bbUpper[0] - bbLower[0]) * 100 : 50;
   
   // Build JSON
   string json = "{\n";
   json += "  \"symbol\":\"" + Symbol() + "\",\n";
   json += "  \"tf\":\"" + EnumToString(Period()) + "\",\n";
   json += "  \"bid\":" + DoubleToString(bid, _Digits) + ",\n";
   json += "  \"ask\":" + DoubleToString(ask, _Digits) + ",\n";
   json += "  \"atr\":" + DoubleToString(atr[0], 2) + ",\n";
   json += "  \"rsi\":" + DoubleToString(rsi[0], 1) + ",\n";
   json += "  \"rsi2\":" + DoubleToString(rsi2[0], 1) + ",\n";
   json += "  \"bb_position\":" + DoubleToString(bbPos, 1) + ",\n";
   json += "  \"macd_hist\":" + DoubleToString(macdMain[0] - macdSignal[0], 4) + ",\n";
   json += "  \"trend\":\"" + trend + "\",\n";
   json += "  \"spread_points\":" + IntegerToString(spread) + ",\n";
   json += "  \"layers\":" + IntegerToString(layers) + ",\n";
   json += "  \"dir\":" + IntegerToString(dir) + ",\n";
   json += "  \"net_lots\":" + DoubleToString(netLots, 2) + ",\n";
   json += "  \"wavg\":" + DoubleToString(wavg, _Digits) + ",\n";
   json += "  \"floating_pnl\":" + DoubleToString(floatingPnl, 2) + ",\n";
   json += "  \"equity\":" + DoubleToString(eq, 2) + ",\n";
   json += "  \"balance\":" + DoubleToString(bal, 2) + ",\n";
   json += "  \"daily_trades\":" + IntegerToString(dailyTrades) + ",\n";
   json += "  \"timestamp\":\"" + TimeToString(now, TIME_DATE|TIME_SECONDS) + "\"\n";
   json += "}";
   
   WriteTextFile(InpStateFile, json);
}

//+------------------------------------------------------------------+
//| CONTROLLER: Read AI control parameters                            |
//+------------------------------------------------------------------+
void ReadControl()
{
   string raw;
   if(!ReadTextFile(InpControlFile, raw)) return; // No file is fine
   
   double v;
   
   // disable_new_entries
   if(InpAI_AllowDisableEntries && JsonGetNumber(raw, "disable_new_entries", v))
      g_disableNewEntries = (v >= 0.5);
   
   // dca_step_multiplier: AI can only WIDEN steps (increase multiplier)
   if(JsonGetNumber(raw, "dca_step_multiplier", v))
      g_dcaStepMult = MathMax(InpAI_DcaStepMinMult, v);
   
   // max_layers_cap: AI can only REDUCE layers
   if(JsonGetNumber(raw, "max_layers_cap", v))
      g_maxLayersCap = (int)MathMax((double)InpAI_MaxLayersFloor, MathMin((double)InpMaxLayers, v));
   
   // risk_bias: -1 to +1 (negative = reduce risk)
   if(JsonGetNumber(raw, "risk_bias", v))
      g_riskBias = MathMax(-1.0, MathMin(0.0, v)); // AI can only go negative (reduce)
   
   // Optional: regime and sentiment for display
   JsonGetString(raw, "regime", g_regime);
   JsonGetString(raw, "sentiment", g_sentiment);
}

//+------------------------------------------------------------------+
//| Get position info                                                 |
//+------------------------------------------------------------------+
int GetPositionInfo(int &dir, double &netLots, double &wavg, double &floatingPnl)
{
   int count = 0;
   dir = 0;
   netLots = 0;
   wavg = 0;
   floatingPnl = 0;
   double totalVolPrice = 0;
   double totalVol = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      
      count++;
      double vol = PositionGetDouble(POSITION_VOLUME);
      double price = PositionGetDouble(POSITION_PRICE_OPEN);
      
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
      {
         netLots += vol;
         dir = 1;
      }
      else
      {
         netLots -= vol;
         dir = -1;
      }
      
      totalVolPrice += vol * price;
      totalVol += vol;
      floatingPnl += PositionGetDouble(POSITION_PROFIT);
   }
   
   if(totalVol > 0)
      wavg = totalVolPrice / totalVol;
   
   return count;
}

//+------------------------------------------------------------------+
//| Count positions                                                   |
//+------------------------------------------------------------------+
int CountPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Signal Analysis                                                   |
//+------------------------------------------------------------------+
void AnalyzeSignals(int &buySignals, int &sellSignals, string &buyReasons[], string &sellReasons[])
{
   buySignals = 0;
   sellSignals = 0;
   ArrayResize(buyReasons, 0);
   ArrayResize(sellReasons, 0);
   
   double maFast[], maSlow[], maTrend[];
   double rsi[], rsi2[];
   double bbUpper[], bbLower[];
   double macdMain[], macdSignal[];
   
   ArraySetAsSeries(maFast, true);
   ArraySetAsSeries(maSlow, true);
   ArraySetAsSeries(maTrend, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(rsi2, true);
   ArraySetAsSeries(bbUpper, true);
   ArraySetAsSeries(bbLower, true);
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   
   CopyBuffer(h_MA_Fast, 0, 0, 3, maFast);
   CopyBuffer(h_MA_Slow, 0, 0, 3, maSlow);
   CopyBuffer(h_MA_Trend, 0, 0, 3, maTrend);
   CopyBuffer(h_RSI, 0, 0, 3, rsi);
   CopyBuffer(h_RSI2, 0, 0, 3, rsi2);
   CopyBuffer(h_BB, 1, 0, 2, bbUpper);
   CopyBuffer(h_BB, 2, 0, 2, bbLower);
   CopyBuffer(h_MACD, 0, 0, 3, macdMain);
   CopyBuffer(h_MACD, 1, 0, 3, macdSignal);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   
   // Signal 1: MA Stack
   if(price > maFast[0] && maFast[0] > maSlow[0] && maSlow[0] > maTrend[0])
   {
      buySignals++;
      ArrayResize(buyReasons, ArraySize(buyReasons)+1);
      buyReasons[ArraySize(buyReasons)-1] = "MA_STACK";
   }
   else if(price < maFast[0] && maFast[0] < maSlow[0] && maSlow[0] < maTrend[0])
   {
      sellSignals++;
      ArrayResize(sellReasons, ArraySize(sellReasons)+1);
      sellReasons[ArraySize(sellReasons)-1] = "MA_STACK";
   }
   
   // Signal 2: MA Cross
   if(maFast[1] <= maSlow[1] && maFast[0] > maSlow[0])
   {
      buySignals++;
      ArrayResize(buyReasons, ArraySize(buyReasons)+1);
      buyReasons[ArraySize(buyReasons)-1] = "MA_CROSS";
   }
   else if(maFast[1] >= maSlow[1] && maFast[0] < maSlow[0])
   {
      sellSignals++;
      ArrayResize(sellReasons, ArraySize(sellReasons)+1);
      sellReasons[ArraySize(sellReasons)-1] = "MA_CROSS";
   }
   
   // Signal 3: RSI extremes
   if(rsi[0] < 30)
   {
      buySignals++;
      ArrayResize(buyReasons, ArraySize(buyReasons)+1);
      buyReasons[ArraySize(buyReasons)-1] = "RSI_OS";
   }
   else if(rsi[0] > 70)
   {
      sellSignals++;
      ArrayResize(sellReasons, ArraySize(sellReasons)+1);
      sellReasons[ArraySize(sellReasons)-1] = "RSI_OB";
   }
   
   // Signal 4: RSI2 extreme (2x weight)
   if(rsi2[0] < 10)
   {
      buySignals += 2;
      ArrayResize(buyReasons, ArraySize(buyReasons)+1);
      buyReasons[ArraySize(buyReasons)-1] = "RSI2_EXTREME";
   }
   else if(rsi2[0] > 90)
   {
      sellSignals += 2;
      ArrayResize(sellReasons, ArraySize(sellReasons)+1);
      sellReasons[ArraySize(sellReasons)-1] = "RSI2_EXTREME";
   }
   
   // Signal 5: BB touch
   double close = iClose(Symbol(), PERIOD_CURRENT, 0);
   if(close <= bbLower[0])
   {
      buySignals++;
      ArrayResize(buyReasons, ArraySize(buyReasons)+1);
      buyReasons[ArraySize(buyReasons)-1] = "BB_LOWER";
   }
   else if(close >= bbUpper[0])
   {
      sellSignals++;
      ArrayResize(sellReasons, ArraySize(sellReasons)+1);
      sellReasons[ArraySize(sellReasons)-1] = "BB_UPPER";
   }
   
   // Signal 6: MACD cross
   if(macdMain[1] <= macdSignal[1] && macdMain[0] > macdSignal[0])
   {
      buySignals++;
      ArrayResize(buyReasons, ArraySize(buyReasons)+1);
      buyReasons[ArraySize(buyReasons)-1] = "MACD_CROSS";
   }
   else if(macdMain[1] >= macdSignal[1] && macdMain[0] < macdSignal[0])
   {
      sellSignals++;
      ArrayResize(sellReasons, ArraySize(sellReasons)+1);
      sellReasons[ArraySize(sellReasons)-1] = "MACD_CROSS";
   }
   
   // Signal 7: Higher highs/lows
   double h1 = iHigh(Symbol(), PERIOD_CURRENT, 1);
   double h2 = iHigh(Symbol(), PERIOD_CURRENT, 2);
   double h3 = iHigh(Symbol(), PERIOD_CURRENT, 3);
   double l1 = iLow(Symbol(), PERIOD_CURRENT, 1);
   double l2 = iLow(Symbol(), PERIOD_CURRENT, 2);
   double l3 = iLow(Symbol(), PERIOD_CURRENT, 3);
   
   if(h1 > h2 && h2 > h3 && l1 > l2 && l2 > l3)
   {
      buySignals++;
      ArrayResize(buyReasons, ArraySize(buyReasons)+1);
      buyReasons[ArraySize(buyReasons)-1] = "HH_HL";
   }
   else if(h1 < h2 && h2 < h3 && l1 < l2 && l2 < l3)
   {
      sellSignals++;
      ArrayResize(sellReasons, ArraySize(sellReasons)+1);
      sellReasons[ArraySize(sellReasons)-1] = "LH_LL";
   }
}

//+------------------------------------------------------------------+
//| Open BUY                                                          |
//+------------------------------------------------------------------+
void OpenBuy(int signals, string &reasons[])
{
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   
   // AI-adjusted SL (g_dcaStepMult >= 1.0 means wider = safer)
   double slMult = InpATR_SL_Mult * g_dcaStepMult;
   double sl = price - (atr[0] * slMult);
   double tp = price + (atr[0] * slMult * InpRewardRisk);
   
   // AI-adjusted risk (g_riskBias <= 0 means reduce risk)
   double riskPct = InpRiskPercent * (1.0 + g_riskBias);
   riskPct = MathMax(0.1, riskPct);
   
   double lots = CalcLots(price - sl, riskPct);
   
   // Build comment
   string comment = "GH|";
   for(int i = 0; i < MathMin(3, ArraySize(reasons)); i++)
      comment += reasons[i] + "|";
   
   if(trade.Buy(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("✅ BUY | Signals: ", signals, " | Risk: ", riskPct, "% | Lots: ", lots);
      LogTrade("BUY", price, sl, tp, lots, signals, reasons);
   }
}

//+------------------------------------------------------------------+
//| Open SELL                                                         |
//+------------------------------------------------------------------+
void OpenSell(int signals, string &reasons[])
{
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   
   double slMult = InpATR_SL_Mult * g_dcaStepMult;
   double sl = price + (atr[0] * slMult);
   double tp = price - (atr[0] * slMult * InpRewardRisk);
   
   double riskPct = InpRiskPercent * (1.0 + g_riskBias);
   riskPct = MathMax(0.1, riskPct);
   
   double lots = CalcLots(sl - price, riskPct);
   
   string comment = "GH|";
   for(int i = 0; i < MathMin(3, ArraySize(reasons)); i++)
      comment += reasons[i] + "|";
   
   if(trade.Sell(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("✅ SELL | Signals: ", signals, " | Risk: ", riskPct, "% | Lots: ", lots);
      LogTrade("SELL", price, sl, tp, lots, signals, reasons);
   }
}

//+------------------------------------------------------------------+
//| Calculate lots                                                    |
//+------------------------------------------------------------------+
double CalcLots(double slDist, double riskPct)
{
   double tickVal = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   
   double riskAmt = balance * riskPct / 100.0;
   double slTicks = slDist / tickSize;
   double lots = riskAmt / (slTicks * tickVal);
   
   double minLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
   
   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(minLot, MathMin(InpMaxLots, lots));
   
   return lots;
}

//+------------------------------------------------------------------+
//| Manage positions                                                  |
//+------------------------------------------------------------------+
void ManagePositions(int buySignals, int sellSignals)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      
      if(type == POSITION_TYPE_BUY && sellSignals >= 3 && sellSignals > buySignals)
      {
         trade.PositionClose(ticket);
         Print("📤 BUY closed by opposing signals");
      }
      else if(type == POSITION_TYPE_SELL && buySignals >= 3 && buySignals > sellSignals)
      {
         trade.PositionClose(ticket);
         Print("📤 SELL closed by opposing signals");
      }
   }
}

//+------------------------------------------------------------------+
//| Log trade for learning                                            |
//+------------------------------------------------------------------+
void LogTrade(string action, double entry, double sl, double tp, double lots, int signals, string &reasons[])
{
   int h = FileOpen("aiiq_trades.jsonl", FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   
   string reasonStr = "";
   for(int i = 0; i < ArraySize(reasons); i++)
   {
      if(i > 0) reasonStr += ",";
      reasonStr += "\"" + reasons[i] + "\"";
   }
   
   string json = "{";
   json += "\"ts\":\"" + TimeToString(TimeCurrent()) + "\",";
   json += "\"action\":\"" + action + "\",";
   json += "\"entry\":" + DoubleToString(entry, 2) + ",";
   json += "\"sl\":" + DoubleToString(sl, 2) + ",";
   json += "\"tp\":" + DoubleToString(tp, 2) + ",";
   json += "\"lots\":" + DoubleToString(lots, 2) + ",";
   json += "\"signals\":" + IntegerToString(signals) + ",";
   json += "\"reasons\":[" + reasonStr + "],";
   json += "\"regime\":\"" + g_regime + "\",";
   json += "\"risk_bias\":" + DoubleToString(g_riskBias, 2);
   json += "}\n";
   
   FileWriteString(h, json);
   FileClose(h);
}

//+------------------------------------------------------------------+
//| Update display                                                    |
//+------------------------------------------------------------------+
void UpdateDisplay()
{
   int buySignals = 0, sellSignals = 0;
   string buyR[], sellR[];
   AnalyzeSignals(buySignals, sellSignals, buyR, sellR);
   
   int maxLayers = MathMin(InpMaxLayers, g_maxLayersCap);
   
   string info = "";
   info += "════════════════════════════════════\n";
   info += "  🥇 GOLD HUNTER LIVE v5.0\n";
   info += "════════════════════════════════════\n";
   info += "AI CONTROL:\n";
   info += "  Regime: " + g_regime + "\n";
   info += "  Sentiment: " + g_sentiment + "\n";
   info += "  Risk Bias: " + DoubleToString(g_riskBias, 2) + "\n";
   info += "  DCA Mult: " + DoubleToString(g_dcaStepMult, 2) + "\n";
   if(g_disableNewEntries)
      info += "  ⚠️ ENTRIES DISABLED\n";
   info += "────────────────────────────────────\n";
   info += "SIGNALS:\n";
   info += "  BUY: " + IntegerToString(buySignals) + " | SELL: " + IntegerToString(sellSignals) + "\n";
   info += "  Min: " + IntegerToString(InpMinSignals) + "\n";
   info += "────────────────────────────────────\n";
   info += "POSITIONS:\n";
   info += "  Layers: " + IntegerToString(CountPositions()) + "/" + IntegerToString(maxLayers) + "\n";
   info += "  Daily: " + IntegerToString(dailyTrades) + "/" + IntegerToString(InpMaxDailyTrades) + "\n";
   info += "════════════════════════════════════";
   
   Comment(info);
}
//+------------------------------------------------------------------+
