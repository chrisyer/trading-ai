//+------------------------------------------------------------------+
//|                                              Gold_Hunter_AI.mq5  |
//|                     AI-ENHANCED GOLD STRATEGY                    |
//|          Execution + State Export + AI Parameter Override        |
//+------------------------------------------------------------------+
#property copyright "Gold Hunter AI"
#property version   "4.00"
#property strict

#include <Trade\Trade.mqh>

//=== INPUT PARAMETERS (AI can override these) ===
input group "=== BASE RISK MANAGEMENT ==="
input double   BaseRiskPercent = 1.0;          // Base risk per trade (%)
input double   MaxLots = 0.5;                  // Hard max lots (AI cannot exceed)
input double   BaseRewardRisk = 2.0;           // Base R:R ratio
input int      HardMaxDailyTrades = 10;        // Hard max trades (AI cannot exceed)
input int      HardMaxPositions = 3;           // Hard max positions

input group "=== INDICATOR SETTINGS ==="
input int      MA_Fast = 20;
input int      MA_Slow = 50;
input int      MA_Trend = 200;
input int      RSI_Period = 14;
input int      RSI2_Period = 2;
input int      BB_Period = 20;
input double   BB_Deviation = 2.0;
input int      ATR_Period = 14;
input double   ATR_SL_Mult = 2.0;
input int      MACD_Fast = 12;
input int      MACD_Slow = 26;
input int      MACD_Signal = 9;

input group "=== AI INTEGRATION ==="
input bool     EnableAI = true;                // Enable AI parameter override
input string   StateFile = "gold_state.json";  // State export file
input string   ControlFile = "gold_control.json"; // AI control file
input int      StateExportSeconds = 5;         // Export state every N seconds
input int      AICheckSeconds = 10;            // Check AI control every N seconds

input group "=== CONFLUENCE SETTINGS ==="
input int      BaseMinSignals = 3;             // Base min signals to trade

input group "=== SYSTEM ==="
input int      MagicNumber = 20260205;
input int      Slippage = 50;

//=== GLOBAL VARIABLES ===
CTrade trade;
int dailyTrades = 0;
datetime lastTradeDay = 0;
datetime lastStateExport = 0;
datetime lastAICheck = 0;

// Indicator handles
int h_MA_Fast, h_MA_Slow, h_MA_Trend;
int h_RSI, h_RSI2;
int h_BB;
int h_ATR;
int h_MACD;

// AI Control Parameters (can be overridden)
struct AIControl {
   double riskBias;           // -1 to +1 (negative = reduce risk)
   double dcaMultiplier;      // DCA step multiplier
   int    maxLayers;          // Max DCA layers
   int    minSignalsOverride; // Min signals override
   bool   disableNewEntries;  // Emergency stop
   bool   closeAllPositions;  // Emergency close
   string regime;             // Market regime
   string sentiment;          // Social sentiment
   int    sentimentIntensity; // 0-100
   string eventRisk;          // low/medium/high
   datetime lastUpdate;
};

AIControl aiCtrl;

// Signal structure
struct SignalScore {
   int buySignals;
   int sellSignals;
   string buyReasons[];
   string sellReasons[];
};

// State for export
struct MarketState {
   double price;
   double atr;
   double rsi;
   double rsi2;
   double bbWidth;
   double bbPosition;
   double emaDistance;
   double macdHist;
   int    openPositions;
   double netLots;
   double floatingPnl;
   double spread;
   int    buySignals;
   int    sellSignals;
   string trend;
};

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   // Initialize indicators
   h_MA_Fast = iMA(Symbol(), PERIOD_CURRENT, MA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_MA_Slow = iMA(Symbol(), PERIOD_CURRENT, MA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   h_MA_Trend = iMA(Symbol(), PERIOD_CURRENT, MA_Trend, 0, MODE_EMA, PRICE_CLOSE);
   h_RSI = iRSI(Symbol(), PERIOD_CURRENT, RSI_Period, PRICE_CLOSE);
   h_RSI2 = iRSI(Symbol(), PERIOD_CURRENT, RSI2_Period, PRICE_CLOSE);
   h_BB = iBands(Symbol(), PERIOD_CURRENT, BB_Period, 0, BB_Deviation, PRICE_CLOSE);
   h_ATR = iATR(Symbol(), PERIOD_CURRENT, ATR_Period);
   h_MACD = iMACD(Symbol(), PERIOD_CURRENT, MACD_Fast, MACD_Slow, MACD_Signal, PRICE_CLOSE);
   
   if(h_MA_Fast == INVALID_HANDLE || h_RSI == INVALID_HANDLE)
   {
      Print("❌ Failed to create indicators");
      return INIT_FAILED;
   }
   
   // Initialize AI control with safe defaults
   ResetAIControl();
   
   Print("════════════════════════════════════════════════════════════════");
   Print("🥇 GOLD HUNTER AI v4.0 - OLLAMA ENHANCED");
   Print("════════════════════════════════════════════════════════════════");
   Print("ARCHITECTURE:");
   Print("├── MT5: Execution + State Export");
   Print("├── Ollama: Regime Classification + Risk Governance");
   Print("├── Bridge: Parameter Override (AI cannot exceed hard limits)");
   Print("└── Learning: Experience Replay + Memory Injection");
   Print("");
   Print("AI Integration: ", EnableAI ? "ENABLED" : "DISABLED");
   Print("State Export: Every ", StateExportSeconds, "s → ", StateFile);
   Print("AI Control: Every ", AICheckSeconds, "s ← ", ControlFile);
   Print("════════════════════════════════════════════════════════════════");
   
   EventSetTimer(1);
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Reset AI control to safe defaults                                 |
//+------------------------------------------------------------------+
void ResetAIControl()
{
   aiCtrl.riskBias = 0;
   aiCtrl.dcaMultiplier = 1.0;
   aiCtrl.maxLayers = HardMaxPositions;
   aiCtrl.minSignalsOverride = BaseMinSignals;
   aiCtrl.disableNewEntries = false;
   aiCtrl.closeAllPositions = false;
   aiCtrl.regime = "unknown";
   aiCtrl.sentiment = "neutral";
   aiCtrl.sentimentIntensity = 50;
   aiCtrl.eventRisk = "low";
   aiCtrl.lastUpdate = 0;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
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
//| Timer - handle state export and AI control                        |
//+------------------------------------------------------------------+
void OnTimer()
{
   datetime now = TimeCurrent();
   
   // Export state to file
   if(EnableAI && now - lastStateExport >= StateExportSeconds)
   {
      ExportState();
      lastStateExport = now;
   }
   
   // Read AI control
   if(EnableAI && now - lastAICheck >= AICheckSeconds)
   {
      ReadAIControl();
      lastAICheck = now;
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // Reset daily trades
   if(TimeToString(TimeCurrent(), TIME_DATE) != TimeToString(lastTradeDay, TIME_DATE))
   {
      dailyTrades = 0;
      lastTradeDay = TimeCurrent();
   }
   
   // Emergency close all
   if(aiCtrl.closeAllPositions)
   {
      CloseAllPositions();
      aiCtrl.closeAllPositions = false;
      return;
   }
   
   // Only trade on new bar
   static datetime lastBar = 0;
   if(iTime(Symbol(), PERIOD_CURRENT, 0) == lastBar)
      return;
   lastBar = iTime(Symbol(), PERIOD_CURRENT, 0);
   
   // Analyze signals
   SignalScore score;
   AnalyzeSignals(score);
   
   // Update display
   UpdateDisplay(score);
   
   // Check if AI disabled entries
   if(aiCtrl.disableNewEntries)
   {
      Print("⚠️ AI has disabled new entries");
      ManageOpenPositions(score);
      return;
   }
   
   // Check hard limits
   if(dailyTrades >= HardMaxDailyTrades)
      return;
   if(CountOpenPositions() >= MathMin(HardMaxPositions, aiCtrl.maxLayers))
      return;
   
   // Execute trades
   ExecuteTrade(score);
   
   // Manage positions
   ManageOpenPositions(score);
}

//+------------------------------------------------------------------+
//| Export market state to JSON file                                  |
//+------------------------------------------------------------------+
void ExportState()
{
   MarketState state;
   
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
   
   state.price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   state.atr = atr[0];
   state.rsi = rsi[0];
   state.rsi2 = rsi2[0];
   state.bbWidth = (bbUpper[0] - bbLower[0]) / bbMiddle[0] * 100;
   state.bbPosition = (state.price - bbLower[0]) / (bbUpper[0] - bbLower[0]) * 100;
   state.emaDistance = (state.price - maFast[0]) / state.price * 100;
   state.macdHist = macdMain[0] - macdSignal[0];
   state.spread = (int)SymbolInfoInteger(Symbol(), SYMBOL_SPREAD);
   
   // Position info
   state.openPositions = 0;
   state.netLots = 0;
   state.floatingPnl = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      
      state.openPositions++;
      double lots = PositionGetDouble(POSITION_VOLUME);
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
         state.netLots += lots;
      else
         state.netLots -= lots;
      state.floatingPnl += PositionGetDouble(POSITION_PROFIT);
   }
   
   // Get signal counts
   SignalScore score;
   AnalyzeSignals(score);
   state.buySignals = score.buySignals;
   state.sellSignals = score.sellSignals;
   
   // Determine trend
   if(state.price > maFast[0] && maFast[0] > maSlow[0])
      state.trend = "bullish";
   else if(state.price < maFast[0] && maFast[0] < maSlow[0])
      state.trend = "bearish";
   else
      state.trend = "neutral";
   
   // Write JSON
   int handle = FileOpen(StateFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      string json = "{";
      json += "\"symbol\":\"" + Symbol() + "\",";
      json += "\"timeframe\":\"" + EnumToString(Period()) + "\",";
      json += "\"price\":" + DoubleToString(state.price, 2) + ",";
      json += "\"atr\":" + DoubleToString(state.atr, 2) + ",";
      json += "\"rsi\":" + DoubleToString(state.rsi, 1) + ",";
      json += "\"rsi2\":" + DoubleToString(state.rsi2, 1) + ",";
      json += "\"bb_width\":" + DoubleToString(state.bbWidth, 1) + ",";
      json += "\"bb_position\":" + DoubleToString(state.bbPosition, 1) + ",";
      json += "\"ema_distance\":" + DoubleToString(state.emaDistance, 2) + ",";
      json += "\"macd_hist\":" + DoubleToString(state.macdHist, 4) + ",";
      json += "\"trend\":\"" + state.trend + "\",";
      json += "\"open_positions\":" + IntegerToString(state.openPositions) + ",";
      json += "\"net_lots\":" + DoubleToString(state.netLots, 2) + ",";
      json += "\"floating_pnl\":" + DoubleToString(state.floatingPnl, 2) + ",";
      json += "\"spread\":" + IntegerToString(state.spread) + ",";
      json += "\"buy_signals\":" + IntegerToString(state.buySignals) + ",";
      json += "\"sell_signals\":" + IntegerToString(state.sellSignals) + ",";
      json += "\"daily_trades\":" + IntegerToString(dailyTrades) + ",";
      json += "\"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\"";
      json += "}";
      
      FileWriteString(handle, json);
      FileClose(handle);
   }
}

//+------------------------------------------------------------------+
//| Read AI control parameters                                        |
//+------------------------------------------------------------------+
void ReadAIControl()
{
   if(!FileIsExist(ControlFile))
      return;
   
   int handle = FileOpen(ControlFile, FILE_READ|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;
   
   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle);
   FileClose(handle);
   
   // Parse JSON (simple extraction)
   double newRiskBias = ExtractDouble(content, "risk_bias");
   double newDcaMult = ExtractDouble(content, "dca_multiplier");
   int newMaxLayers = (int)ExtractDouble(content, "max_layers");
   int newMinSignals = (int)ExtractDouble(content, "min_signals");
   bool newDisable = ExtractBool(content, "disable_new_entries");
   bool newCloseAll = ExtractBool(content, "close_all_positions");
   string newRegime = ExtractString(content, "regime");
   string newSentiment = ExtractString(content, "sentiment");
   int newIntensity = (int)ExtractDouble(content, "sentiment_intensity");
   string newEventRisk = ExtractString(content, "event_risk");
   
   // Apply with HARD LIMITS (AI cannot exceed)
   aiCtrl.riskBias = MathMax(-1.0, MathMin(1.0, newRiskBias));
   aiCtrl.dcaMultiplier = MathMax(0.5, MathMin(2.0, newDcaMult));
   aiCtrl.maxLayers = MathMin(HardMaxPositions, MathMax(1, newMaxLayers));
   aiCtrl.minSignalsOverride = MathMax(2, MathMin(6, newMinSignals));
   aiCtrl.disableNewEntries = newDisable;
   aiCtrl.closeAllPositions = newCloseAll;
   
   if(newRegime != "") aiCtrl.regime = newRegime;
   if(newSentiment != "") aiCtrl.sentiment = newSentiment;
   aiCtrl.sentimentIntensity = MathMax(0, MathMin(100, newIntensity));
   if(newEventRisk != "") aiCtrl.eventRisk = newEventRisk;
   
   aiCtrl.lastUpdate = TimeCurrent();
   
   // Log significant changes
   if(newDisable)
      Print("🛑 AI DISABLED NEW ENTRIES");
   if(newCloseAll)
      Print("🚨 AI TRIGGERED EMERGENCY CLOSE ALL");
   if(newRiskBias < -0.5)
      Print("⚠️ AI REDUCING RISK: bias=", aiCtrl.riskBias);
}

//+------------------------------------------------------------------+
//| Extract double from JSON                                          |
//+------------------------------------------------------------------+
double ExtractDouble(string json, string key)
{
   string search = "\"" + key + "\"";
   int pos = StringFind(json, search);
   if(pos < 0) return 0;
   
   int colonPos = StringFind(json, ":", pos);
   if(colonPos < 0) return 0;
   
   int startPos = colonPos + 1;
   while(startPos < StringLen(json) && StringGetCharacter(json, startPos) == ' ')
      startPos++;
   
   int endPos = startPos;
   while(endPos < StringLen(json))
   {
      ushort c = StringGetCharacter(json, endPos);
      if((c >= '0' && c <= '9') || c == '.' || c == '-')
         endPos++;
      else
         break;
   }
   
   return StringToDouble(StringSubstr(json, startPos, endPos - startPos));
}

//+------------------------------------------------------------------+
//| Extract bool from JSON                                            |
//+------------------------------------------------------------------+
bool ExtractBool(string json, string key)
{
   string search = "\"" + key + "\"";
   int pos = StringFind(json, search);
   if(pos < 0) return false;
   
   return (StringFind(json, "true", pos) < StringFind(json, ",", pos) + 20 &&
           StringFind(json, "true", pos) > pos);
}

//+------------------------------------------------------------------+
//| Extract string from JSON                                          |
//+------------------------------------------------------------------+
string ExtractString(string json, string key)
{
   string search = "\"" + key + "\"";
   int pos = StringFind(json, search);
   if(pos < 0) return "";
   
   int colonPos = StringFind(json, ":", pos);
   if(colonPos < 0) return "";
   
   int startQuote = StringFind(json, "\"", colonPos);
   if(startQuote < 0) return "";
   
   int endQuote = StringFind(json, "\"", startQuote + 1);
   if(endQuote < 0) return "";
   
   return StringSubstr(json, startQuote + 1, endQuote - startQuote - 1);
}

//+------------------------------------------------------------------+
//| SIGNAL ANALYSIS (same as before)                                  |
//+------------------------------------------------------------------+
void AnalyzeSignals(SignalScore &score)
{
   score.buySignals = 0;
   score.sellSignals = 0;
   ArrayResize(score.buyReasons, 0);
   ArrayResize(score.sellReasons, 0);
   
   Signal_MATrend(score);
   Signal_RSI(score);
   Signal_BollingerBands(score);
   Signal_MACD(score);
   Signal_SwingPattern(score);
   Signal_CandleStrength(score);
}

void Signal_MATrend(SignalScore &score)
{
   double maFast[], maSlow[], maTrend[];
   ArraySetAsSeries(maFast, true);
   ArraySetAsSeries(maSlow, true);
   ArraySetAsSeries(maTrend, true);
   
   CopyBuffer(h_MA_Fast, 0, 0, 3, maFast);
   CopyBuffer(h_MA_Slow, 0, 0, 3, maSlow);
   CopyBuffer(h_MA_Trend, 0, 0, 3, maTrend);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   
   if(price > maFast[0] && maFast[0] > maSlow[0] && maSlow[0] > maTrend[0])
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "MA BULLISH STACK";
   }
   else if(price < maFast[0] && maFast[0] < maSlow[0] && maSlow[0] < maTrend[0])
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "MA BEARISH STACK";
   }
   
   if(maFast[1] <= maSlow[1] && maFast[0] > maSlow[0])
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "MA BULLISH CROSS";
   }
   else if(maFast[1] >= maSlow[1] && maFast[0] < maSlow[0])
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "MA BEARISH CROSS";
   }
}

void Signal_RSI(SignalScore &score)
{
   double rsi[], rsi2[];
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(rsi2, true);
   
   CopyBuffer(h_RSI, 0, 0, 3, rsi);
   CopyBuffer(h_RSI2, 0, 0, 3, rsi2);
   
   if(rsi[0] < 30)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = StringFormat("RSI OVERSOLD %.1f", rsi[0]);
   }
   else if(rsi[0] > 70)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = StringFormat("RSI OVERBOUGHT %.1f", rsi[0]);
   }
   
   if(rsi2[0] < 10)
   {
      score.buySignals += 2;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = StringFormat("RSI2 EXTREME LOW %.1f", rsi2[0]);
   }
   else if(rsi2[0] > 90)
   {
      score.sellSignals += 2;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = StringFormat("RSI2 EXTREME HIGH %.1f", rsi2[0]);
   }
}

void Signal_BollingerBands(SignalScore &score)
{
   double bbUpper[], bbLower[];
   ArraySetAsSeries(bbUpper, true);
   ArraySetAsSeries(bbLower, true);
   
   CopyBuffer(h_BB, 1, 0, 2, bbUpper);
   CopyBuffer(h_BB, 2, 0, 2, bbLower);
   
   double close = iClose(Symbol(), PERIOD_CURRENT, 0);
   
   if(close <= bbLower[0])
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "BB LOWER BAND";
   }
   else if(close >= bbUpper[0])
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "BB UPPER BAND";
   }
}

void Signal_MACD(SignalScore &score)
{
   double macdMain[], macdSignal[];
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   
   CopyBuffer(h_MACD, 0, 0, 3, macdMain);
   CopyBuffer(h_MACD, 1, 0, 3, macdSignal);
   
   if(macdMain[1] <= macdSignal[1] && macdMain[0] > macdSignal[0])
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "MACD BULLISH CROSS";
   }
   else if(macdMain[1] >= macdSignal[1] && macdMain[0] < macdSignal[0])
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "MACD BEARISH CROSS";
   }
}

void Signal_SwingPattern(SignalScore &score)
{
   double high1 = iHigh(Symbol(), PERIOD_CURRENT, 1);
   double high2 = iHigh(Symbol(), PERIOD_CURRENT, 2);
   double high3 = iHigh(Symbol(), PERIOD_CURRENT, 3);
   double low1 = iLow(Symbol(), PERIOD_CURRENT, 1);
   double low2 = iLow(Symbol(), PERIOD_CURRENT, 2);
   double low3 = iLow(Symbol(), PERIOD_CURRENT, 3);
   
   if(high1 > high2 && high2 > high3 && low1 > low2 && low2 > low3)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "HIGHER H/L";
   }
   else if(high1 < high2 && high2 < high3 && low1 < low2 && low2 < low3)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "LOWER H/L";
   }
}

void Signal_CandleStrength(SignalScore &score)
{
   double open1 = iOpen(Symbol(), PERIOD_CURRENT, 1);
   double close1 = iClose(Symbol(), PERIOD_CURRENT, 1);
   double high1 = iHigh(Symbol(), PERIOD_CURRENT, 1);
   double low1 = iLow(Symbol(), PERIOD_CURRENT, 1);
   
   double body = MathAbs(close1 - open1);
   double range = high1 - low1;
   double bodyRatio = (range > 0) ? body / range : 0;
   
   if(close1 > open1 && bodyRatio > 0.6)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "STRONG BULL CANDLE";
   }
   else if(close1 < open1 && bodyRatio > 0.6)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "STRONG BEAR CANDLE";
   }
}

//+------------------------------------------------------------------+
//| Execute trade with AI-adjusted parameters                         |
//+------------------------------------------------------------------+
void ExecuteTrade(SignalScore &score)
{
   int minSignals = aiCtrl.minSignalsOverride;
   
   if(score.buySignals >= minSignals && score.buySignals > score.sellSignals + 1)
   {
      OpenBuy(score);
   }
   else if(score.sellSignals >= minSignals && score.sellSignals > score.buySignals + 1)
   {
      OpenSell(score);
   }
}

void OpenBuy(SignalScore &score)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   
   // AI-adjusted SL multiplier
   double slMult = ATR_SL_Mult * aiCtrl.dcaMultiplier;
   if(aiCtrl.riskBias < 0) slMult *= (1.0 + MathAbs(aiCtrl.riskBias) * 0.5);  // Wider SL when reducing risk
   
   double sl = price - (atr[0] * slMult);
   double tp = price + (atr[0] * slMult * BaseRewardRisk);
   
   // AI-adjusted risk
   double riskPct = BaseRiskPercent;
   if(aiCtrl.riskBias < 0) riskPct *= (1.0 + aiCtrl.riskBias);  // Reduce risk
   riskPct = MathMax(0.1, riskPct);  // Never go below 0.1%
   
   double lots = CalculateLots(price - sl, riskPct);
   
   string comment = StringFormat("GH_AI|%s|%s|", aiCtrl.regime, aiCtrl.sentiment);
   
   if(trade.Buy(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("════════════════════════════════════════════════════════════════");
      Print("✅ AI-ENHANCED BUY - ", score.buySignals, " signals");
      Print("   AI Regime: ", aiCtrl.regime, " | Sentiment: ", aiCtrl.sentiment);
      Print("   Risk Bias: ", aiCtrl.riskBias, " | Adjusted Risk: ", riskPct, "%");
      Print("   Entry: ", price, " | SL: ", sl, " | TP: ", tp);
      Print("════════════════════════════════════════════════════════════════");
      
      // Log for learning
      LogTradeForLearning("BUY", price, sl, tp, lots, score);
   }
}

void OpenSell(SignalScore &score)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   CopyBuffer(h_ATR, 0, 0, 1, atr);
   
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   
   double slMult = ATR_SL_Mult * aiCtrl.dcaMultiplier;
   if(aiCtrl.riskBias < 0) slMult *= (1.0 + MathAbs(aiCtrl.riskBias) * 0.5);
   
   double sl = price + (atr[0] * slMult);
   double tp = price - (atr[0] * slMult * BaseRewardRisk);
   
   double riskPct = BaseRiskPercent;
   if(aiCtrl.riskBias < 0) riskPct *= (1.0 + aiCtrl.riskBias);
   riskPct = MathMax(0.1, riskPct);
   
   double lots = CalculateLots(sl - price, riskPct);
   
   string comment = StringFormat("GH_AI|%s|%s|", aiCtrl.regime, aiCtrl.sentiment);
   
   if(trade.Sell(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("════════════════════════════════════════════════════════════════");
      Print("✅ AI-ENHANCED SELL - ", score.sellSignals, " signals");
      Print("   AI Regime: ", aiCtrl.regime, " | Sentiment: ", aiCtrl.sentiment);
      Print("   Risk Bias: ", aiCtrl.riskBias, " | Adjusted Risk: ", riskPct, "%");
      Print("   Entry: ", price, " | SL: ", sl, " | TP: ", tp);
      Print("════════════════════════════════════════════════════════════════");
      
      LogTradeForLearning("SELL", price, sl, tp, lots, score);
   }
}

//+------------------------------------------------------------------+
//| Log trade for AI learning                                         |
//+------------------------------------------------------------------+
void LogTradeForLearning(string action, double entry, double sl, double tp, double lots, SignalScore &score)
{
   int handle = FileOpen("gold_trades_log.jsonl", FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileSeek(handle, 0, SEEK_END);
      
      string json = "{";
      json += "\"timestamp\":\"" + TimeToString(TimeCurrent()) + "\",";
      json += "\"action\":\"" + action + "\",";
      json += "\"entry\":" + DoubleToString(entry, 2) + ",";
      json += "\"sl\":" + DoubleToString(sl, 2) + ",";
      json += "\"tp\":" + DoubleToString(tp, 2) + ",";
      json += "\"lots\":" + DoubleToString(lots, 2) + ",";
      json += "\"buy_signals\":" + IntegerToString(score.buySignals) + ",";
      json += "\"sell_signals\":" + IntegerToString(score.sellSignals) + ",";
      json += "\"regime\":\"" + aiCtrl.regime + "\",";
      json += "\"sentiment\":\"" + aiCtrl.sentiment + "\",";
      json += "\"risk_bias\":" + DoubleToString(aiCtrl.riskBias, 2);
      json += "}\n";
      
      FileWriteString(handle, json);
      FileClose(handle);
   }
}

//+------------------------------------------------------------------+
//| Calculate lots                                                    |
//+------------------------------------------------------------------+
double CalculateLots(double slDistance, double riskPct)
{
   double tickValue = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   
   double riskAmount = balance * riskPct / 100.0;
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
//| Manage open positions                                             |
//+------------------------------------------------------------------+
void ManageOpenPositions(SignalScore &score)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      
      if(type == POSITION_TYPE_BUY && score.sellSignals >= 3 && score.sellSignals > score.buySignals)
      {
         trade.PositionClose(ticket);
         Print("📤 BUY closed by opposing signals");
      }
      else if(type == POSITION_TYPE_SELL && score.buySignals >= 3 && score.buySignals > score.sellSignals)
      {
         trade.PositionClose(ticket);
         Print("📤 SELL closed by opposing signals");
      }
   }
}

//+------------------------------------------------------------------+
//| Close all positions                                               |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      
      trade.PositionClose(ticket);
   }
   Print("🚨 ALL POSITIONS CLOSED BY AI");
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
//| Update display                                                    |
//+------------------------------------------------------------------+
void UpdateDisplay(SignalScore &score)
{
   string info = "";
   info += "════════════════════════════════════════\n";
   info += "  🥇 GOLD HUNTER AI v4.0\n";
   info += "════════════════════════════════════════\n";
   info += "AI BRAIN:\n";
   info += "  Regime: " + aiCtrl.regime + "\n";
   info += "  Sentiment: " + aiCtrl.sentiment + " (" + IntegerToString(aiCtrl.sentimentIntensity) + "%)\n";
   info += "  Risk Bias: " + DoubleToString(aiCtrl.riskBias, 2) + "\n";
   info += "  Event Risk: " + aiCtrl.eventRisk + "\n";
   info += "────────────────────────────────────────\n";
   info += StringFormat("BUY Signals:  %d\n", score.buySignals);
   info += StringFormat("SELL Signals: %d\n", score.sellSignals);
   info += StringFormat("Min Required: %d\n", aiCtrl.minSignalsOverride);
   info += "────────────────────────────────────────\n";
   info += StringFormat("Positions: %d/%d\n", CountOpenPositions(), aiCtrl.maxLayers);
   info += StringFormat("Daily Trades: %d/%d\n", dailyTrades, HardMaxDailyTrades);
   if(aiCtrl.disableNewEntries)
      info += "⚠️ NEW ENTRIES DISABLED BY AI\n";
   info += "════════════════════════════════════════";
   
   Comment(info);
}
//+------------------------------------------------------------------+
