//+------------------------------------------------------------------+
//|                                              Gold_Hunter_RL.mq5  |
//|                                    Anti-Market Maker RL Trader   |
//|                    DUAL-SOURCE: H100 Primary + Ollama Fallback   |
//|                              Optimized for XAUUSD                |
//+------------------------------------------------------------------+
#property copyright "Gold Hunter - Quinn"
#property link      "https://github.com/AI4Finance-Foundation/FinRL"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>
#include <Files\FileTxt.mqh>

//--- Input parameters - DUAL SOURCE CONFIGURATION
input string   H100_SignalURL = "http://146.190.188.208:8894/gold/signal";  // H100 RL Agent API (Primary)
input string   OllamaSignalFile = "gold_ollama_signal.json";                 // Local Ollama signal file (Fallback)
input double   BaseLotSize = 0.10;                                           // Base lot size (Gold is expensive!)
input double   RiskPercent = 1.0;                                            // Risk per trade (%)
input double   MaxPositionSize = 1.0;                                        // Max lots for Gold
input int      SlippagePoints = 50;                                          // Max slippage (Gold is volatile)
input int      MagicNumber = 20260203;                                       // Magic number
input int      SignalCheckSeconds = 15;                                      // Check signal every N seconds
input double   DefaultSL_Points = 500;                                       // Default SL in points ($5)
input double   DefaultTP_Points = 1000;                                      // Default TP in points ($10)
input int      H100_TimeoutMS = 5000;                                        // H100 API timeout (ms)
input int      MinConfidence = 60;                                           // Minimum confidence to trade

//--- Global variables
CTrade trade;
datetime lastSignalTime = 0;
datetime lastCheck = 0;
string currentSignal = "HOLD";
double signalPrice = 0;
int signalConfidence = 0;
string signalSource = "NONE";
int h100FailCount = 0;
int h100SuccessCount = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   Print("════════════════════════════════════════════════════════════════");
   Print("🥇 GOLD HUNTER RL v2.0 - DUAL SOURCE INTELLIGENCE");
   Print("════════════════════════════════════════════════════════════════");
   Print("┌─────────────────────────────────────────────────────────────┐");
   Print("│  PRIMARY:  H100 RL Agent (Quinn) - XAUUSD Specialist       │");
   Print("│            ", H100_SignalURL);
   Print("│            200K timesteps training, hourly signals         │");
   Print("├─────────────────────────────────────────────────────────────┤");
   Print("│  FALLBACK: Local Ollama (mistral:latest)                   │");
   Print("│            ", OllamaSignalFile);
   Print("│            Active when H100 unavailable                    │");
   Print("└─────────────────────────────────────────────────────────────┘");
   Print("Check Interval: ", SignalCheckSeconds, " seconds");
   Print("Base Lot Size: ", BaseLotSize, " (Gold)");
   Print("Min Confidence: ", MinConfidence, "%");
   Print("════════════════════════════════════════════════════════════════");
   
   EventSetTimer(SignalCheckSeconds);
   UpdateChartDisplay();
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
   Print("════════════════════════════════════════════════════════════════");
   Print("Gold Hunter RL stopped.");
   Print("H100 Success: ", h100SuccessCount, " | Failures: ", h100FailCount);
   Print("════════════════════════════════════════════════════════════════");
}

//+------------------------------------------------------------------+
//| Timer function                                                     |
//+------------------------------------------------------------------+
void OnTimer()
{
   CheckAndExecuteSignal();
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   if(TimeCurrent() - lastCheck < SignalCheckSeconds)
      return;
      
   lastCheck = TimeCurrent();
   CheckAndExecuteSignal();
   UpdateChartDisplay();
}

//+------------------------------------------------------------------+
//| DUAL SOURCE: Try H100 first, fallback to Ollama                    |
//+------------------------------------------------------------------+
void CheckAndExecuteSignal()
{
   string signal = "";
   double price = 0;
   int confidence = 0;
   datetime signalTime = 0;
   string source = "";
   
   //--- STEP 1: Try H100 RL Agent (Primary)
   if(ReadSignalFromH100(signal, price, confidence, signalTime))
   {
      source = "H100_RL";
      h100SuccessCount++;
      
      if(h100FailCount > 0)
      {
         Print("🟢 H100 Gold connection restored after ", h100FailCount, " failures");
         h100FailCount = 0;
      }
   }
   else
   {
      h100FailCount++;
      
      //--- STEP 2: Fallback to Local Ollama
      if(ReadSignalFromOllama(signal, price, confidence, signalTime))
      {
         source = "OLLAMA_LOCAL";
         
         if(h100FailCount == 1)
            Print("⚠️ H100 unavailable, using Ollama Gold fallback");
      }
      else
      {
         if(h100FailCount % 10 == 1)
            Print("❌ Both Gold signal sources unavailable (H100 fails: ", h100FailCount, ")");
         return;
      }
   }
   
   //--- Check if new signal
   if(signalTime <= lastSignalTime)
      return;
      
   lastSignalTime = signalTime;
   signalSource = source;
   currentSignal = signal;
   signalPrice = price;
   signalConfidence = confidence;
   
   //--- Log signal
   Print("════════════════════════════════════════════════════════════════");
   Print("📡 NEW GOLD SIGNAL - Source: ", source);
   Print("   Action: ", signal);
   Print("   Price: $", DoubleToString(price, 2));
   Print("   Confidence: ", confidence, "%");
   Print("   Time: ", TimeToString(signalTime));
   if(source == "H100_RL")
      Print("   🤖 Quinn's Gold RL Agent (200K timesteps)");
   else
      Print("   🦙 Local Ollama Gold Fallback");
   Print("════════════════════════════════════════════════════════════════");
   
   //--- Check confidence threshold
   if(confidence < MinConfidence)
   {
      Print("⚠️ Confidence too low (", confidence, "% < ", MinConfidence, "%), skipping...");
      return;
   }
   
   //--- Execute signal
   ExecuteSignal(signal, price, confidence, source);
   UpdateChartDisplay();
}

//+------------------------------------------------------------------+
//| Read signal from H100 RL Agent API (Primary)                       |
//+------------------------------------------------------------------+
bool ReadSignalFromH100(string &signal, double &price, int &confidence, datetime &signalTime)
{
   string headers = "";
   string result_headers;
   char post[];
   char result[];
   
   int res = WebRequest("GET", H100_SignalURL, headers, H100_TimeoutMS, post, result, result_headers);
   
   if(res == -1)
   {
      int err = GetLastError();
      if(h100FailCount % 20 == 1)
         Print("H100 Gold WebRequest error: ", err, " (add URL to MT5 allowed list)");
      return false;
   }
   
   if(res != 200)
   {
      if(h100FailCount % 20 == 1)
         Print("H100 Gold HTTP error: ", res);
      return false;
   }
   
   string content = CharArrayToString(result);
   
   signal = ExtractJsonString(content, "action");
   price = ExtractJsonDouble(content, "price");
   confidence = (int)ExtractJsonDouble(content, "confidence");
   
   string timestamp = ExtractJsonString(content, "timestamp");
   if(StringLen(timestamp) > 0)
   {
      signalTime = StringToTime(StringSubstr(timestamp, 0, 10) + " " + StringSubstr(timestamp, 11, 8));
   }
   else
   {
      signalTime = TimeCurrent();
   }
   
   if(signal == "" || (signal == "HOLD" && confidence == 0))
      return false;
   
   return true;
}

//+------------------------------------------------------------------+
//| Read signal from Local Ollama file (Fallback)                      |
//+------------------------------------------------------------------+
bool ReadSignalFromOllama(string &signal, double &price, int &confidence, datetime &signalTime)
{
   int handle = FileOpen(OllamaSignalFile, FILE_READ|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      handle = FileOpen(OllamaSignalFile, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(handle == INVALID_HANDLE)
         return false;
   }
   
   string content = "";
   while(!FileIsEnding(handle))
   {
      content += FileReadString(handle);
   }
   FileClose(handle);
   
   signal = ExtractJsonString(content, "action");
   price = ExtractJsonDouble(content, "price");
   confidence = (int)ExtractJsonDouble(content, "confidence");
   
   string timestamp = ExtractJsonString(content, "timestamp");
   if(StringLen(timestamp) > 0)
   {
      signalTime = StringToTime(StringSubstr(timestamp, 0, 10) + " " + StringSubstr(timestamp, 11, 8));
   }
   else
   {
      signalTime = TimeCurrent();
   }
   
   return(signal != "");
}

//+------------------------------------------------------------------+
//| Extract string value from JSON                                     |
//+------------------------------------------------------------------+
string ExtractJsonString(string json, string key)
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
//| Extract double value from JSON                                     |
//+------------------------------------------------------------------+
double ExtractJsonDouble(string json, string key)
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
   
   string numStr = StringSubstr(json, startPos, endPos - startPos);
   return StringToDouble(numStr);
}

//+------------------------------------------------------------------+
//| Execute trading signal                                             |
//+------------------------------------------------------------------+
void ExecuteSignal(string signal, double signalPrice, int confidence, string source)
{
   string symbol = Symbol();
   double currentPrice = SymbolInfoDouble(symbol, SYMBOL_BID);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   
   int currentPosition = GetCurrentPosition();
   double lotSize = CalculateLotSize(confidence, source);
   
   if(signal == "BUY")
   {
      if(currentPosition < 0)
      {
         CloseAllPositions();
         Print("✅ Closed SHORT before BUY");
      }
      
      if(currentPosition <= 0)
      {
         double sl = currentPrice - DefaultSL_Points * point;
         double tp = currentPrice + DefaultTP_Points * point;
         
         string comment = "GOLD_" + source + "_BUY";
         
         if(trade.Buy(lotSize, symbol, 0, sl, tp, comment))
         {
            Print("✅ GOLD BUY executed: ", lotSize, " lots @ $", DoubleToString(currentPrice, 2));
            Print("   Source: ", source, " | SL: $", DoubleToString(sl, 2), " | TP: $", DoubleToString(tp, 2));
         }
         else
         {
            Print("❌ GOLD BUY failed: ", trade.ResultRetcode(), " - ", trade.ResultRetcodeDescription());
         }
      }
      else
      {
         Print("ℹ️ Already LONG Gold, holding position");
      }
   }
   else if(signal == "SELL")
   {
      if(currentPosition > 0)
      {
         CloseAllPositions();
         Print("✅ Closed LONG before SELL");
      }
      
      if(currentPosition >= 0)
      {
         double sl = currentPrice + DefaultSL_Points * point;
         double tp = currentPrice - DefaultTP_Points * point;
         
         string comment = "GOLD_" + source + "_SELL";
         
         if(trade.Sell(lotSize, symbol, 0, sl, tp, comment))
         {
            Print("✅ GOLD SELL executed: ", lotSize, " lots @ $", DoubleToString(currentPrice, 2));
            Print("   Source: ", source, " | SL: $", DoubleToString(sl, 2), " | TP: $", DoubleToString(tp, 2));
         }
         else
         {
            Print("❌ GOLD SELL failed: ", trade.ResultRetcode(), " - ", trade.ResultRetcodeDescription());
         }
      }
      else
      {
         Print("ℹ️ Already SHORT Gold, holding position");
      }
   }
   else if(signal == "HOLD")
   {
      Print("ℹ️ HOLD signal - no action taken");
   }
   else if(signal == "CLOSE" || signal == "FLAT")
   {
      CloseAllPositions();
      Print("✅ All Gold positions closed");
   }
}

//+------------------------------------------------------------------+
//| Get current position direction                                     |
//+------------------------------------------------------------------+
int GetCurrentPosition()
{
   string symbol = Symbol();
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      
      if(PositionGetString(POSITION_SYMBOL) != symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if(type == POSITION_TYPE_BUY)
         return 1;
      else if(type == POSITION_TYPE_SELL)
         return -1;
   }
   
   return 0;
}

//+------------------------------------------------------------------+
//| Calculate lot size (Gold-specific)                                 |
//+------------------------------------------------------------------+
double CalculateLotSize(int confidence, string source)
{
   double lotSize = BaseLotSize;
   
   //--- Scale by confidence
   double confidenceMultiplier = confidence / 100.0;
   lotSize *= confidenceMultiplier;
   
   //--- H100 RL signals get full size, Ollama gets 70%
   if(source == "OLLAMA_LOCAL")
   {
      lotSize *= 0.7;
      Print("   📉 Reduced lot size for fallback: ", DoubleToString(lotSize, 2));
   }
   
   //--- Apply limits (Gold has smaller lot sizes)
   double minLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double maxLot = MathMin(MaxPositionSize, SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX));
   double lotStep = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
   
   lotSize = MathMax(minLot, MathMin(maxLot, lotSize));
   lotSize = MathFloor(lotSize / lotStep) * lotStep;
   
   return lotSize;
}

//+------------------------------------------------------------------+
//| Close all positions                                                |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   string symbol = Symbol();
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      
      if(PositionGetString(POSITION_SYMBOL) != symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      
      trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
//| Update chart display                                               |
//+------------------------------------------------------------------+
void UpdateChartDisplay()
{
   string sourceIcon = (signalSource == "H100_RL") ? "🤖" : (signalSource == "OLLAMA_LOCAL") ? "🦙" : "⏳";
   string sourceLabel = (signalSource == "H100_RL") ? "H100 Quinn" : (signalSource == "OLLAMA_LOCAL") ? "Ollama" : "Waiting...";
   
   string posIcon = "";
   int pos = GetCurrentPosition();
   if(pos > 0) posIcon = "📈 LONG";
   else if(pos < 0) posIcon = "📉 SHORT";
   else posIcon = "⏸️ FLAT";
   
   string info = "";
   info += "════════════════════════════════\n";
   info += "  🥇 GOLD HUNTER RL v2.0\n";
   info += "════════════════════════════════\n";
   info += "Source: " + sourceIcon + " " + sourceLabel + "\n";
   info += "Signal: " + currentSignal + "\n";
   info += "Price: $" + DoubleToString(signalPrice, 2) + "\n";
   info += "Confidence: " + IntegerToString(signalConfidence) + "%\n";
   info += "Position: " + posIcon + "\n";
   info += "────────────────────────────────\n";
   info += "H100: ✅" + IntegerToString(h100SuccessCount) + " ❌" + IntegerToString(h100FailCount) + "\n";
   info += "════════════════════════════════";
   
   Comment(info);
}
//+------------------------------------------------------------------+
