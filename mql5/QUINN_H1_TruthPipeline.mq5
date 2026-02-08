//+------------------------------------------------------------------+
//|                                    QUINN_H1_TruthPipeline.mq5    |
//|                              Copyright 2026, AiiQ-tAIq Platform  |
//|      H1 Truth Report — 10 Metrics → JSON → QUINN → Telegram     |
//+------------------------------------------------------------------+
//| MISSION: Every H1 bar close, compute 10 metrics on XAUUSD and   |
//| write a structured JSON report for QUINN001 to relay to Telegram.|
//|                                                                  |
//| OUTPUT:                                                          |
//|   MQL5/Files/quinn_truth_latest.json   (overwritten each bar)    |
//|   MQL5/Files/quinn_truth_YYYYMMDD_HHMM.json (historical copy)   |
//|                                                                  |
//| METRICS:                                                         |
//|   1. VWAP (Volume-Weighted Average Price)                        |
//|   2. RSI(14)                                                     |
//|   3. ATR(14)                                                     |
//|   4. Session (Asian/London/NY/Closed)                            |
//|   5. Spread                                                      |
//|   6. EMA 50/200                                                  |
//|   7. Ichimoku Cloud                                              |
//|   8. H1 High/Low Breakout                                       |
//|   9. H4 Bias (EMA 50/200 on H4)                                 |
//|  10. Regime (ADX trend strength)                                 |
//|                                                                  |
//| NETTING SAFE: This EA does NOT trade. Read-only indicator.       |
//| MAGIC: 999001                                                    |
//+------------------------------------------------------------------+
#property copyright "AiiQ-tAIq Platform — QUINN001"
#property link      "https://github.com/AiiQ-tAIq"
#property version   "1.00"
#property description "H1 Truth Pipeline — 10 metrics → JSON for Telegram relay"
#property strict

#include <Trade\Trade.mqh>

//--- Inputs
input int      InpMagic         = 999001;    // Magic number (identification only)
input string   InpSymbol        = "XAUUSD";  // Symbol to monitor
input int      InpRSIPeriod     = 14;        // RSI period
input int      InpATRPeriod     = 14;        // ATR period
input int      InpEMAFast       = 50;        // EMA fast period
input int      InpEMASlow       = 200;       // EMA slow period
input int      InpADXPeriod     = 14;        // ADX period
input int      InpIchiTenkan    = 9;         // Ichimoku Tenkan period
input int      InpIchiKijun     = 26;        // Ichimoku Kijun period
input int      InpIchiSenkou    = 52;        // Ichimoku Senkou period
input int      InpVWAPBars      = 20;        // VWAP lookback (H1 bars)
input bool     InpWriteHistory  = true;      // Write historical copies

//--- Globals
datetime g_lastBarTime = 0;
int      g_reportsToday = 0;
int      g_totalReports = 0;
datetime g_todayStart = 0;

//--- Indicator handles
int h_rsi, h_atr, h_ema50, h_ema200, h_adx;
int h_ichi;
int h_ema50_h4, h_ema200_h4;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Validate symbol
   if(!SymbolSelect(InpSymbol, true))
   {
      Print("[TRUTH] Symbol ", InpSymbol, " not available");
      return INIT_FAILED;
   }
   
   //--- Create indicator handles on H1
   h_rsi    = iRSI(InpSymbol, PERIOD_H1, InpRSIPeriod, PRICE_CLOSE);
   h_atr    = iATR(InpSymbol, PERIOD_H1, InpATRPeriod);
   h_ema50  = iMA(InpSymbol, PERIOD_H1, InpEMAFast, 0, MODE_EMA, PRICE_CLOSE);
   h_ema200 = iMA(InpSymbol, PERIOD_H1, InpEMASlow, 0, MODE_EMA, PRICE_CLOSE);
   h_adx    = iADX(InpSymbol, PERIOD_H1, InpADXPeriod);
   h_ichi   = iIchimoku(InpSymbol, PERIOD_H1, InpIchiTenkan, InpIchiKijun, InpIchiSenkou);
   
   //--- H4 indicators for bias
   h_ema50_h4  = iMA(InpSymbol, PERIOD_H4, InpEMAFast, 0, MODE_EMA, PRICE_CLOSE);
   h_ema200_h4 = iMA(InpSymbol, PERIOD_H4, InpEMASlow, 0, MODE_EMA, PRICE_CLOSE);
   
   if(h_rsi==INVALID_HANDLE || h_atr==INVALID_HANDLE || h_ema50==INVALID_HANDLE ||
      h_ema200==INVALID_HANDLE || h_adx==INVALID_HANDLE || h_ichi==INVALID_HANDLE ||
      h_ema50_h4==INVALID_HANDLE || h_ema200_h4==INVALID_HANDLE)
   {
      Print("[TRUTH] Failed to create indicator handles");
      return INIT_FAILED;
   }
   
   g_todayStart = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   
   Print("══════════════════════════════════════════════");
   Print("  QUINN H1 TRUTH PIPELINE v1.0");
   Print("  Symbol: ", InpSymbol, " | Magic: ", InpMagic);
   Print("  Metrics: 10 | Output: quinn_truth_latest.json");
   Print("══════════════════════════════════════════════");
   
   //--- Generate initial report immediately
   GenerateReport();
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(h_rsi!=INVALID_HANDLE)    IndicatorRelease(h_rsi);
   if(h_atr!=INVALID_HANDLE)    IndicatorRelease(h_atr);
   if(h_ema50!=INVALID_HANDLE)  IndicatorRelease(h_ema50);
   if(h_ema200!=INVALID_HANDLE) IndicatorRelease(h_ema200);
   if(h_adx!=INVALID_HANDLE)    IndicatorRelease(h_adx);
   if(h_ichi!=INVALID_HANDLE)   IndicatorRelease(h_ichi);
   if(h_ema50_h4!=INVALID_HANDLE)  IndicatorRelease(h_ema50_h4);
   if(h_ema200_h4!=INVALID_HANDLE) IndicatorRelease(h_ema200_h4);
   
   Print("[TRUTH] Shutting down. Total reports: ", g_totalReports);
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- Check for new H1 bar
   datetime barTime = iTime(InpSymbol, PERIOD_H1, 0);
   if(barTime == g_lastBarTime) return;
   g_lastBarTime = barTime;
   
   //--- New day reset
   datetime today = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   if(today != g_todayStart)
   {
      g_todayStart = today;
      g_reportsToday = 0;
   }
   
   //--- Generate report on every new H1 bar
   GenerateReport();
}

//+------------------------------------------------------------------+
//| Generate the full truth report                                    |
//+------------------------------------------------------------------+
void GenerateReport()
{
   //--- Fetch all indicator values
   double rsi[], atr[], ema50[], ema200[], adx_main[], adx_plus[], adx_minus[];
   double ichi_tenkan[], ichi_kijun[], ichi_ssa[], ichi_ssb[];
   double ema50_h4[], ema200_h4[];
   
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(atr, true);
   ArraySetAsSeries(ema50, true);
   ArraySetAsSeries(ema200, true);
   ArraySetAsSeries(adx_main, true);
   ArraySetAsSeries(adx_plus, true);
   ArraySetAsSeries(adx_minus, true);
   ArraySetAsSeries(ichi_tenkan, true);
   ArraySetAsSeries(ichi_kijun, true);
   ArraySetAsSeries(ichi_ssa, true);
   ArraySetAsSeries(ichi_ssb, true);
   ArraySetAsSeries(ema50_h4, true);
   ArraySetAsSeries(ema200_h4, true);
   
   if(CopyBuffer(h_rsi, 0, 0, 3, rsi) < 3) return;
   if(CopyBuffer(h_atr, 0, 0, 3, atr) < 3) return;
   if(CopyBuffer(h_ema50, 0, 0, 3, ema50) < 3) return;
   if(CopyBuffer(h_ema200, 0, 0, 3, ema200) < 3) return;
   if(CopyBuffer(h_adx, 0, 0, 3, adx_main) < 3) return;    // ADX main line
   if(CopyBuffer(h_adx, 1, 0, 3, adx_plus) < 3) return;    // +DI
   if(CopyBuffer(h_adx, 2, 0, 3, adx_minus) < 3) return;   // -DI
   if(CopyBuffer(h_ichi, 0, 0, 3, ichi_tenkan) < 3) return; // Tenkan
   if(CopyBuffer(h_ichi, 1, 0, 3, ichi_kijun) < 3) return;  // Kijun
   if(CopyBuffer(h_ichi, 2, 0, 3, ichi_ssa) < 3) return;    // Senkou Span A
   if(CopyBuffer(h_ichi, 3, 0, 3, ichi_ssb) < 3) return;    // Senkou Span B
   if(CopyBuffer(h_ema50_h4, 0, 0, 3, ema50_h4) < 3) return;
   if(CopyBuffer(h_ema200_h4, 0, 0, 3, ema200_h4) < 3) return;
   
   //--- Current market data
   double price = SymbolInfoDouble(InpSymbol, SYMBOL_LAST);
   if(price == 0) price = SymbolInfoDouble(InpSymbol, SYMBOL_BID);
   double bid = SymbolInfoDouble(InpSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(InpSymbol, SYMBOL_ASK);
   int spread = (int)SymbolInfoInteger(InpSymbol, SYMBOL_SPREAD);
   
   //--- H1 high/low of previous completed bar
   double h1_high = iHigh(InpSymbol, PERIOD_H1, 1);
   double h1_low  = iLow(InpSymbol, PERIOD_H1, 1);
   
   //--- VWAP calculation (volume-weighted over lookback)
   double vwap = CalculateVWAP(InpVWAPBars);
   
   //--- ATR change vs previous bar
   double atr_change = 0;
   if(atr[1] > 0) atr_change = ((atr[0] - atr[1]) / atr[1]) * 100.0;
   
   //--- Average spread (rough — use current as baseline)
   int avg_spread = spread; // Simplified; could track rolling average
   
   //--- Session detection
   string session = DetectSession();
   bool marketOpen = IsMarketOpen();
   
   //--- Build metrics array
   // Metric 1: VWAP
   double vwap_diff = price - vwap;
   string vwap_interp = (vwap_diff > 0) ? "ABOVE (" + DoubleToString(vwap_diff, 2) + ")" : "BELOW (" + DoubleToString(vwap_diff, 2) + ")";
   string vwap_bias = (vwap_diff > 0) ? "BUY" : (vwap_diff < 0 ? "SELL" : "HOLD");
   
   // Metric 2: RSI
   string rsi_interp, rsi_bias;
   if(rsi[0] > 70)      { rsi_interp = "OVERBOUGHT";         rsi_bias = "SELL"; }
   else if(rsi[0] > 60) { rsi_interp = "LEANING OVERBOUGHT"; rsi_bias = "SELL"; }
   else if(rsi[0] < 30) { rsi_interp = "OVERSOLD";           rsi_bias = "BUY"; }
   else if(rsi[0] < 40) { rsi_interp = "LEANING OVERSOLD";   rsi_bias = "BUY"; }
   else                  { rsi_interp = "NEUTRAL";            rsi_bias = "HOLD"; }
   
   // Metric 3: ATR
   string atr_interp;
   if(MathAbs(atr_change) < 5) atr_interp = "STABLE (" + DoubleToString(atr_change, 1) + "%)";
   else if(atr_change > 0)     atr_interp = "EXPANDING (+" + DoubleToString(atr_change, 1) + "%)";
   else                        atr_interp = "CONTRACTING (" + DoubleToString(atr_change, 1) + "%)";
   
   // Metric 4: Session
   string session_bias = "HOLD";
   if(session == "LONDON/NY" || session == "LONDON" || session == "NEW_YORK") session_bias = "HOLD";
   
   // Metric 5: Spread
   string spread_interp;
   if(spread <= 35)     spread_interp = "TIGHT";
   else if(spread <= 50) spread_interp = "NORMAL";
   else                  spread_interp = "WIDE";
   
   // Metric 6: EMA 50/200
   string ema_interp, ema_bias;
   if(ema50[0] > ema200[0]) { ema_interp = "BULLISH (50 > 200)"; ema_bias = "BUY"; }
   else                      { ema_interp = "BEARISH (50 < 200)"; ema_bias = "SELL"; }
   
   // Metric 7: Ichimoku
   double cloud_top = MathMax(ichi_ssa[0], ichi_ssb[0]);
   double cloud_bot = MathMin(ichi_ssa[0], ichi_ssb[0]);
   string ichi_interp, ichi_bias;
   if(price > cloud_top)      { ichi_interp = "ABOVE CLOUD"; ichi_bias = "BUY"; }
   else if(price < cloud_bot) { ichi_interp = "BELOW CLOUD"; ichi_bias = "SELL"; }
   else                        { ichi_interp = "IN CLOUD";    ichi_bias = "HOLD"; }
   
   // Metric 8: H1 High/Low Breakout
   string hl_interp, hl_bias;
   if(price > h1_high)      { hl_interp = "BROKE HIGH"; hl_bias = "BUY"; }
   else if(price < h1_low)  { hl_interp = "BROKE LOW";  hl_bias = "SELL"; }
   else                      { hl_interp = "INSIDE RANGE"; hl_bias = "HOLD"; }
   
   // Metric 9: H4 Bias
   string h4_interp, h4_bias;
   if(ema50_h4[0] > ema200_h4[0]) { h4_interp = "BULL"; h4_bias = "BUY"; }
   else                             { h4_interp = "BEAR"; h4_bias = "SELL"; }
   
   // Metric 10: Regime (ADX)
   string regime_interp, regime_bias;
   if(adx_main[0] > 25) regime_interp = "TREND (ADX " + DoubleToString(adx_main[0], 1) + ")";
   else                  regime_interp = "RANGE (ADX " + DoubleToString(adx_main[0], 1) + ")";
   regime_bias = "HOLD";
   
   //--- Scorecard
   int buyCount = 0, holdCount = 0, sellCount = 0;
   CountBias(vwap_bias, buyCount, holdCount, sellCount);
   CountBias(rsi_bias, buyCount, holdCount, sellCount);
   CountBias("HOLD", buyCount, holdCount, sellCount);  // ATR always HOLD
   CountBias(session_bias, buyCount, holdCount, sellCount);
   CountBias("HOLD", buyCount, holdCount, sellCount);  // Spread always HOLD
   CountBias(ema_bias, buyCount, holdCount, sellCount);
   CountBias(ichi_bias, buyCount, holdCount, sellCount);
   CountBias(hl_bias, buyCount, holdCount, sellCount);
   CountBias(h4_bias, buyCount, holdCount, sellCount);
   CountBias(regime_bias, buyCount, holdCount, sellCount);
   
   //--- If market closed, override all to HOLD
   if(!marketOpen)
   {
      buyCount = 0; sellCount = 0; holdCount = 10;
   }
   
   //--- Timestamps
   datetime now = TimeCurrent();
   datetime barOpen = iTime(InpSymbol, PERIOD_H1, 0);
   MqlDateTime mdt;
   TimeToStruct(now, mdt);
   
   // Next update = next hour
   datetime nextUpdate = barOpen + 3600;
   
   g_reportsToday++;
   g_totalReports++;
   
   //--- Build JSON
   string json = "{\n";
   json += "  \"version\": \"1.0\",\n";
   json += "  \"source\": \"QUINN_H1_TruthPipeline\",\n";
   json += "  \"symbol\": \"" + InpSymbol + "\",\n";
   json += "  \"timeframe\": \"H1\",\n";
   json += "\n";
   
   //--- Timestamps block
   json += "  \"timestamps\": {\n";
   json += "    \"broker\": \"" + TimeToString(now, TIME_DATE|TIME_MINUTES) + "\",\n";
   // UTC offset: broker time to UTC (MT5 TimeCurrent is broker time)
   datetime gmtNow = TimeGMT();
   json += "    \"utc\": \"" + TimeToString(gmtNow, TIME_DATE|TIME_MINUTES) + "\",\n";
   json += "    \"unix\": " + IntegerToString((long)gmtNow) + ",\n";
   json += "    \"last_tick\": \"" + TimeToString(now, TIME_DATE|TIME_SECONDS) + "\",\n";
   json += "    \"next_update\": \"" + TimeToString(nextUpdate, TIME_DATE|TIME_MINUTES) + "\"\n";
   json += "  },\n\n";
   
   //--- Market block
   json += "  \"market\": {\n";
   json += "    \"open\": " + (marketOpen ? "true" : "false") + ",\n";
   json += "    \"session\": \"" + (marketOpen ? session : "CLOSED") + "\",\n";
   json += "    \"price\": " + DoubleToString(price, 2) + ",\n";
   json += "    \"bid\": " + DoubleToString(bid, 2) + ",\n";
   json += "    \"ask\": " + DoubleToString(ask, 2) + ",\n";
   json += "    \"spread\": " + IntegerToString(spread) + "\n";
   json += "  },\n\n";
   
   //--- Metrics array
   json += "  \"metrics\": [\n";
   json += MetricJSON(1,  "VWAP",         DoubleToString(vwap, 2),                                              vwap_interp,   vwap_bias) + ",\n";
   json += MetricJSON(2,  "RSI(14)",      DoubleToString(rsi[0], 2),                                            rsi_interp,    rsi_bias)  + ",\n";
   json += MetricJSON(3,  "ATR(14)",      DoubleToString(atr[0], 2),                                            atr_interp,    "HOLD")    + ",\n";
   json += MetricJSON(4,  "Session",      session,                                                               session,       session_bias) + ",\n";
   json += MetricJSON(5,  "Spread",       IntegerToString(spread) + " (avg: " + IntegerToString(avg_spread) + ")", spread_interp, "HOLD") + ",\n";
   json += MetricJSON(6,  "EMA 50/200",   "50=" + DoubleToString(ema50[0],2) + " 200=" + DoubleToString(ema200[0],2), ema_interp, ema_bias) + ",\n";
   json += MetricJSON(7,  "Ichimoku",     DoubleToString(cloud_bot,2) + " - " + DoubleToString(cloud_top,2),    ichi_interp,   ichi_bias) + ",\n";
   json += MetricJSON(8,  "H1 H/L Break", "H=" + DoubleToString(h1_high,2) + " L=" + DoubleToString(h1_low,2), hl_interp,     hl_bias) + ",\n";
   json += MetricJSON(9,  "H4 Bias",      "H4_50=" + DoubleToString(ema50_h4[0],2) + " H4_200=" + DoubleToString(ema200_h4[0],2), h4_interp, h4_bias) + ",\n";
   json += MetricJSON(10, "Regime",        "ADX=" + DoubleToString(adx_main[0],1),                               regime_interp, regime_bias) + "\n";
   json += "  ],\n\n";
   
   //--- Scorecard
   json += "  \"scorecard\": {\n";
   json += "    \"buy\": " + IntegerToString(buyCount) + ",\n";
   json += "    \"hold\": " + IntegerToString(holdCount) + ",\n";
   json += "    \"sell\": " + IntegerToString(sellCount) + "\n";
   json += "  },\n\n";
   
   //--- System
   json += "  \"system\": {\n";
   json += "    \"ea_status\": \"RUNNING\",\n";
   json += "    \"reports_sent\": " + IntegerToString(g_totalReports) + ",\n";
   json += "    \"reports_today\": " + IntegerToString(g_reportsToday) + ",\n";
   json += "    \"magic\": " + IntegerToString(InpMagic) + "\n";
   json += "  }\n";
   json += "}\n";
   
   //--- Write latest (atomic: write tmp, then rename)
   string tmpFile = "quinn_truth_tmp.json";
   string latestFile = "quinn_truth_latest.json";
   
   int h = FileOpen(tmpFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
      
      // Delete old latest, rename tmp → latest
      FileDelete(latestFile);
      // MQL5 doesn't have FileRename, so we write directly to latest as well
      int h2 = FileOpen(latestFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
      if(h2 != INVALID_HANDLE)
      {
         FileWriteString(h2, json);
         FileClose(h2);
      }
      FileDelete(tmpFile);
   }
   
   //--- Write historical copy
   if(InpWriteHistory)
   {
      string histFile = "quinn_truth_" + TimeToString(gmtNow, TIME_DATE) + "_" + 
                         StringFormat("%02d%02d", mdt.hour, mdt.min) + ".json";
      StringReplace(histFile, ".", "");
      StringReplace(histFile, " ", "");
      // Format: quinn_truth_20260208_1400.json
      string cleanHist = "quinn_truth_" + 
                          IntegerToString(mdt.year) +
                          StringFormat("%02d", mdt.mon) +
                          StringFormat("%02d", mdt.day) + "_" +
                          StringFormat("%02d", mdt.hour) +
                          StringFormat("%02d", mdt.min) + ".json";
      int h3 = FileOpen(cleanHist, FILE_WRITE|FILE_TXT|FILE_ANSI);
      if(h3 != INVALID_HANDLE)
      {
         FileWriteString(h3, json);
         FileClose(h3);
      }
   }
   
   //--- Dashboard log
   Print("══════════════════════════════════════════════");
   PrintFormat("  [TRUTH] #%d | %s | $%.2f | %s", g_totalReports, InpSymbol, price, session);
   PrintFormat("  BUY:%d HOLD:%d SELL:%d | RSI:%.1f ADX:%.1f", buyCount, holdCount, sellCount, rsi[0], adx_main[0]);
   PrintFormat("  Written: quinn_truth_latest.json");
   Print("══════════════════════════════════════════════");
}

//+------------------------------------------------------------------+
//| Calculate VWAP over N H1 bars                                     |
//+------------------------------------------------------------------+
double CalculateVWAP(int bars)
{
   double sumPV = 0, sumV = 0;
   for(int i = 1; i <= bars; i++)
   {
      double h = iHigh(InpSymbol, PERIOD_H1, i);
      double l = iLow(InpSymbol, PERIOD_H1, i);
      double c = iClose(InpSymbol, PERIOD_H1, i);
      long   v = iVolume(InpSymbol, PERIOD_H1, i);
      
      double tp = (h + l + c) / 3.0;
      sumPV += tp * (double)v;
      sumV  += (double)v;
   }
   
   if(sumV <= 0) return iClose(InpSymbol, PERIOD_H1, 0);
   return sumPV / sumV;
}

//+------------------------------------------------------------------+
//| Detect current trading session                                    |
//+------------------------------------------------------------------+
string DetectSession()
{
   MqlDateTime mdt;
   TimeToStruct(TimeGMT(), mdt);
   int hour = mdt.hour;
   
   // UTC-based session windows
   if(hour >= 0 && hour < 7)    return "ASIAN";
   if(hour >= 7 && hour < 12)   return "LONDON";
   if(hour >= 12 && hour < 16)  return "LONDON/NY";
   if(hour >= 16 && hour < 21)  return "NEW_YORK";
   return "OFF_HOURS";
}

//+------------------------------------------------------------------+
//| Check if market is open (rough check)                             |
//+------------------------------------------------------------------+
bool IsMarketOpen()
{
   MqlDateTime mdt;
   TimeToStruct(TimeGMT(), mdt);
   
   // Forex/Gold: closed Sat-Sun (approx)
   if(mdt.day_of_week == 0) return false; // Sunday
   if(mdt.day_of_week == 6) return false; // Saturday
   
   // Friday close around 21:00 UTC
   if(mdt.day_of_week == 5 && mdt.hour >= 22) return false;
   
   // Sunday open around 22:00 UTC (handled by day_of_week check)
   return true;
}

//+------------------------------------------------------------------+
//| Count bias votes                                                  |
//+------------------------------------------------------------------+
void CountBias(string bias, int &buy, int &hold, int &sell)
{
   if(bias == "BUY")       buy++;
   else if(bias == "SELL")  sell++;
   else                     hold++;
}

//+------------------------------------------------------------------+
//| Build JSON for a single metric                                    |
//+------------------------------------------------------------------+
string MetricJSON(int id, string name, string raw, string interp, string bias)
{
   return "    {\"id\": " + IntegerToString(id) + 
          ", \"name\": \"" + name + 
          "\", \"raw\": \"" + raw + 
          "\", \"interpretation\": \"" + interp + 
          "\", \"bias\": \"" + bias + "\"}";
}
//+------------------------------------------------------------------+
