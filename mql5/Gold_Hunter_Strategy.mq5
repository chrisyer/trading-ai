//+------------------------------------------------------------------+
//|                                         Gold_Hunter_Strategy.mq5 |
//|                          REAL STRATEGY BOT - Not a signal slave  |
//|                     Built-in multi-signal confluence for XAUUSD  |
//+------------------------------------------------------------------+
#property copyright "Gold Hunter Strategy"
#property version   "3.00"
#property strict

#include <Trade\Trade.mqh>

//=== INPUT PARAMETERS ===
input group "=== RISK MANAGEMENT ==="
input double   RiskPercent = 1.0;              // Risk per trade (%)
input double   MaxLots = 0.5;                  // Maximum lot size
input double   RewardRiskRatio = 2.0;          // Reward:Risk ratio
input int      MaxDailyTrades = 5;             // Max trades per day
input int      MaxOpenPositions = 2;           // Max simultaneous positions

input group "=== TREND SIGNALS ==="
input int      MA_Fast = 20;                   // Fast MA period
input int      MA_Slow = 50;                   // Slow MA period
input int      MA_Trend = 200;                 // Trend MA period
input ENUM_MA_METHOD MA_Method = MODE_EMA;     // MA Method

input group "=== MOMENTUM SIGNALS ==="
input int      RSI_Period = 14;                // RSI period
input int      RSI2_Period = 2;                // RSI(2) mean reversion
input int      RSI_Overbought = 70;            // RSI overbought level
input int      RSI_Oversold = 30;              // RSI oversold level
input int      RSI2_Extreme_High = 90;         // RSI2 extreme high
input int      RSI2_Extreme_Low = 10;          // RSI2 extreme low

input group "=== VOLATILITY SIGNALS ==="
input int      BB_Period = 20;                 // Bollinger Bands period
input double   BB_Deviation = 2.0;             // BB standard deviation
input int      ATR_Period = 14;                // ATR period
input double   ATR_SL_Multiplier = 2.0;        // ATR multiplier for SL

input group "=== MACD SIGNALS ==="
input int      MACD_Fast = 12;                 // MACD fast EMA
input int      MACD_Slow = 26;                 // MACD slow EMA
input int      MACD_Signal = 9;                // MACD signal line

input group "=== CONFLUENCE SETTINGS ==="
input int      MinSignalsToTrade = 3;          // Minimum signals to open trade
input int      MinSignalsToClose = 2;          // Minimum opposite signals to close

input group "=== SESSION FILTER ==="
input bool     UseSessionFilter = true;        // Only trade during active sessions
input int      LondonOpen = 8;                 // London session start (server time)
input int      NYClose = 22;                   // NY session end (server time)

input group "=== SYSTEM ==="
input int      MagicNumber = 20260204;         // Magic number
input int      Slippage = 50;                  // Max slippage points

//=== GLOBAL VARIABLES ===
CTrade trade;
int dailyTrades = 0;
datetime lastTradeDay = 0;

// Indicator handles
int h_MA_Fast, h_MA_Slow, h_MA_Trend;
int h_RSI, h_RSI2;
int h_BB;
int h_ATR;
int h_MACD;

// Signal structure
struct SignalScore {
   int buySignals;
   int sellSignals;
   string buyReasons[];
   string sellReasons[];
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
   h_MA_Fast = iMA(Symbol(), PERIOD_CURRENT, MA_Fast, 0, MA_Method, PRICE_CLOSE);
   h_MA_Slow = iMA(Symbol(), PERIOD_CURRENT, MA_Slow, 0, MA_Method, PRICE_CLOSE);
   h_MA_Trend = iMA(Symbol(), PERIOD_CURRENT, MA_Trend, 0, MA_Method, PRICE_CLOSE);
   h_RSI = iRSI(Symbol(), PERIOD_CURRENT, RSI_Period, PRICE_CLOSE);
   h_RSI2 = iRSI(Symbol(), PERIOD_CURRENT, RSI2_Period, PRICE_CLOSE);
   h_BB = iBands(Symbol(), PERIOD_CURRENT, BB_Period, 0, BB_Deviation, PRICE_CLOSE);
   h_ATR = iATR(Symbol(), PERIOD_CURRENT, ATR_Period);
   h_MACD = iMACD(Symbol(), PERIOD_CURRENT, MACD_Fast, MACD_Slow, MACD_Signal, PRICE_CLOSE);
   
   if(h_MA_Fast == INVALID_HANDLE || h_RSI == INVALID_HANDLE || h_BB == INVALID_HANDLE)
   {
      Print("❌ Failed to create indicator handles");
      return INIT_FAILED;
   }
   
   Print("════════════════════════════════════════════════════════════════");
   Print("🥇 GOLD HUNTER STRATEGY v3.0 - REAL TRADING BOT");
   Print("════════════════════════════════════════════════════════════════");
   Print("STRATEGY: Multi-Signal Confluence");
   Print("├── Signal 1: MA Trend (", MA_Fast, "/", MA_Slow, "/", MA_Trend, ")");
   Print("├── Signal 2: RSI Momentum (", RSI_Period, ") + RSI2 Mean Reversion");
   Print("├── Signal 3: Bollinger Band Position");
   Print("├── Signal 4: MACD Crossover");
   Print("├── Signal 5: Higher Highs/Lows Pattern");
   Print("├── Signal 6: Candle Strength");
   Print("└── Minimum ", MinSignalsToTrade, " signals required to trade");
   Print("");
   Print("RISK: ", RiskPercent, "% per trade | R:R = 1:", RewardRiskRatio);
   Print("════════════════════════════════════════════════════════════════");
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
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
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // Reset daily trade count
   if(TimeToString(TimeCurrent(), TIME_DATE) != TimeToString(lastTradeDay, TIME_DATE))
   {
      dailyTrades = 0;
      lastTradeDay = TimeCurrent();
   }
   
   // Check session filter
   if(UseSessionFilter && !IsActiveSession())
      return;
   
   // Check max daily trades
   if(dailyTrades >= MaxDailyTrades)
      return;
   
   // Check max open positions
   if(CountOpenPositions() >= MaxOpenPositions)
   {
      ManageOpenPositions();
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
   
   // Execute trades based on confluence
   ExecuteTrade(score);
   
   // Manage existing positions
   ManageOpenPositions();
}

//+------------------------------------------------------------------+
//| SIGNAL 1: MA Trend Analysis                                       |
//+------------------------------------------------------------------+
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
   
   // Strong bullish: Price > Fast > Slow > Trend
   if(price > maFast[0] && maFast[0] > maSlow[0] && maSlow[0] > maTrend[0])
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "MA BULLISH STACK";
   }
   // Strong bearish: Price < Fast < Slow < Trend
   else if(price < maFast[0] && maFast[0] < maSlow[0] && maSlow[0] < maTrend[0])
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "MA BEARISH STACK";
   }
   
   // MA crossover
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

//+------------------------------------------------------------------+
//| SIGNAL 2: RSI Momentum + RSI2 Mean Reversion                      |
//+------------------------------------------------------------------+
void Signal_RSI(SignalScore &score)
{
   double rsi[], rsi2[];
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(rsi2, true);
   
   CopyBuffer(h_RSI, 0, 0, 3, rsi);
   CopyBuffer(h_RSI2, 0, 0, 3, rsi2);
   
   // RSI(14) momentum
   if(rsi[0] < RSI_Oversold)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = StringFormat("RSI OVERSOLD %.1f", rsi[0]);
   }
   else if(rsi[0] > RSI_Overbought)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = StringFormat("RSI OVERBOUGHT %.1f", rsi[0]);
   }
   
   // RSI(2) extreme mean reversion - POWERFUL for Gold
   if(rsi2[0] < RSI2_Extreme_Low)
   {
      score.buySignals += 2;  // Double weight for extreme
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = StringFormat("RSI2 EXTREME LOW %.1f", rsi2[0]);
   }
   else if(rsi2[0] > RSI2_Extreme_High)
   {
      score.sellSignals += 2;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = StringFormat("RSI2 EXTREME HIGH %.1f", rsi2[0]);
   }
   
   // RSI momentum direction
   if(rsi[0] > rsi[1] && rsi[1] > rsi[2] && rsi[0] < 60)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "RSI RISING MOMENTUM";
   }
   else if(rsi[0] < rsi[1] && rsi[1] < rsi[2] && rsi[0] > 40)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "RSI FALLING MOMENTUM";
   }
}

//+------------------------------------------------------------------+
//| SIGNAL 3: Bollinger Band Position                                 |
//+------------------------------------------------------------------+
void Signal_BollingerBands(SignalScore &score)
{
   double bbUpper[], bbLower[], bbMiddle[];
   ArraySetAsSeries(bbUpper, true);
   ArraySetAsSeries(bbLower, true);
   ArraySetAsSeries(bbMiddle, true);
   
   CopyBuffer(h_BB, 1, 0, 3, bbUpper);  // Upper band
   CopyBuffer(h_BB, 2, 0, 3, bbLower);  // Lower band
   CopyBuffer(h_BB, 0, 0, 3, bbMiddle); // Middle band
   
   double close = iClose(Symbol(), PERIOD_CURRENT, 0);
   double prevClose = iClose(Symbol(), PERIOD_CURRENT, 1);
   
   // Touch/pierce lower band = potential buy
   if(close <= bbLower[0] || (prevClose < bbLower[1] && close > bbLower[0]))
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "BB LOWER BAND TOUCH";
   }
   // Touch/pierce upper band = potential sell
   else if(close >= bbUpper[0] || (prevClose > bbUpper[1] && close < bbUpper[0]))
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "BB UPPER BAND TOUCH";
   }
   
   // BB squeeze breakout
   double bbWidth = (bbUpper[0] - bbLower[0]) / bbMiddle[0];
   double prevWidth = (bbUpper[1] - bbLower[1]) / bbMiddle[1];
   
   if(bbWidth > prevWidth * 1.5)  // Band expansion
   {
      if(close > bbMiddle[0])
      {
         score.buySignals++;
         ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
         score.buyReasons[ArraySize(score.buyReasons)-1] = "BB EXPANSION BULLISH";
      }
      else
      {
         score.sellSignals++;
         ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
         score.sellReasons[ArraySize(score.sellReasons)-1] = "BB EXPANSION BEARISH";
      }
   }
}

//+------------------------------------------------------------------+
//| SIGNAL 4: MACD Crossover                                          |
//+------------------------------------------------------------------+
void Signal_MACD(SignalScore &score)
{
   double macdMain[], macdSignal[], macdHist[];
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   ArraySetAsSeries(macdHist, true);
   
   CopyBuffer(h_MACD, 0, 0, 3, macdMain);
   CopyBuffer(h_MACD, 1, 0, 3, macdSignal);
   
   // Bullish crossover
   if(macdMain[1] <= macdSignal[1] && macdMain[0] > macdSignal[0])
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "MACD BULLISH CROSS";
   }
   // Bearish crossover
   else if(macdMain[1] >= macdSignal[1] && macdMain[0] < macdSignal[0])
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "MACD BEARISH CROSS";
   }
   
   // MACD above/below zero
   if(macdMain[0] > 0 && macdMain[0] > macdSignal[0])
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "MACD POSITIVE MOMENTUM";
   }
   else if(macdMain[0] < 0 && macdMain[0] < macdSignal[0])
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "MACD NEGATIVE MOMENTUM";
   }
}

//+------------------------------------------------------------------+
//| SIGNAL 5: Higher Highs/Lows Pattern                               |
//+------------------------------------------------------------------+
void Signal_SwingPattern(SignalScore &score)
{
   double high1 = iHigh(Symbol(), PERIOD_CURRENT, 1);
   double high2 = iHigh(Symbol(), PERIOD_CURRENT, 2);
   double high3 = iHigh(Symbol(), PERIOD_CURRENT, 3);
   double low1 = iLow(Symbol(), PERIOD_CURRENT, 1);
   double low2 = iLow(Symbol(), PERIOD_CURRENT, 2);
   double low3 = iLow(Symbol(), PERIOD_CURRENT, 3);
   
   // Higher highs and higher lows = uptrend
   if(high1 > high2 && high2 > high3 && low1 > low2 && low2 > low3)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "HIGHER HIGHS/LOWS";
   }
   // Lower highs and lower lows = downtrend
   else if(high1 < high2 && high2 < high3 && low1 < low2 && low2 < low3)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "LOWER HIGHS/LOWS";
   }
}

//+------------------------------------------------------------------+
//| SIGNAL 6: Candle Strength                                         |
//+------------------------------------------------------------------+
void Signal_CandleStrength(SignalScore &score)
{
   double open1 = iOpen(Symbol(), PERIOD_CURRENT, 1);
   double close1 = iClose(Symbol(), PERIOD_CURRENT, 1);
   double high1 = iHigh(Symbol(), PERIOD_CURRENT, 1);
   double low1 = iLow(Symbol(), PERIOD_CURRENT, 1);
   
   double body = MathAbs(close1 - open1);
   double range = high1 - low1;
   double bodyRatio = (range > 0) ? body / range : 0;
   
   // Strong bullish candle (body > 60% of range, close near high)
   if(close1 > open1 && bodyRatio > 0.6 && (high1 - close1) < body * 0.3)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = StringFormat("STRONG BULL CANDLE %.0f%%", bodyRatio*100);
   }
   // Strong bearish candle
   else if(close1 < open1 && bodyRatio > 0.6 && (close1 - low1) < body * 0.3)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = StringFormat("STRONG BEAR CANDLE %.0f%%", bodyRatio*100);
   }
   
   // Pin bar / rejection
   double upperWick = high1 - MathMax(open1, close1);
   double lowerWick = MathMin(open1, close1) - low1;
   
   if(lowerWick > body * 2 && lowerWick > upperWick * 2)
   {
      score.buySignals++;
      ArrayResize(score.buyReasons, ArraySize(score.buyReasons)+1);
      score.buyReasons[ArraySize(score.buyReasons)-1] = "BULLISH PIN BAR";
   }
   else if(upperWick > body * 2 && upperWick > lowerWick * 2)
   {
      score.sellSignals++;
      ArrayResize(score.sellReasons, ArraySize(score.sellReasons)+1);
      score.sellReasons[ArraySize(score.sellReasons)-1] = "BEARISH PIN BAR";
   }
}

//+------------------------------------------------------------------+
//| Analyze all signals                                               |
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

//+------------------------------------------------------------------+
//| Execute trade based on confluence                                 |
//+------------------------------------------------------------------+
void ExecuteTrade(SignalScore &score)
{
   // Need minimum signals and clear direction
   if(score.buySignals >= MinSignalsToTrade && score.buySignals > score.sellSignals + 1)
   {
      OpenBuy(score);
   }
   else if(score.sellSignals >= MinSignalsToTrade && score.sellSignals > score.buySignals + 1)
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
   double sl = price - (atr[0] * ATR_SL_Multiplier);
   double tp = price + (atr[0] * ATR_SL_Multiplier * RewardRiskRatio);
   
   double lots = CalculateLots(price - sl);
   
   // Build comment with reasons
   string comment = "GH_BUY|";
   for(int i = 0; i < MathMin(3, ArraySize(score.buyReasons)); i++)
      comment += score.buyReasons[i] + "|";
   
   if(trade.Buy(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("════════════════════════════════════════════════════════════════");
      Print("✅ BUY OPENED - ", score.buySignals, " SIGNALS ALIGNED");
      for(int i = 0; i < ArraySize(score.buyReasons); i++)
         Print("   ├── ", score.buyReasons[i]);
      Print("   Entry: ", price, " | SL: ", sl, " | TP: ", tp);
      Print("   Lots: ", lots, " | R:R = 1:", RewardRiskRatio);
      Print("════════════════════════════════════════════════════════════════");
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
   double sl = price + (atr[0] * ATR_SL_Multiplier);
   double tp = price - (atr[0] * ATR_SL_Multiplier * RewardRiskRatio);
   
   double lots = CalculateLots(sl - price);
   
   string comment = "GH_SELL|";
   for(int i = 0; i < MathMin(3, ArraySize(score.sellReasons)); i++)
      comment += score.sellReasons[i] + "|";
   
   if(trade.Sell(lots, Symbol(), price, sl, tp, comment))
   {
      dailyTrades++;
      Print("════════════════════════════════════════════════════════════════");
      Print("✅ SELL OPENED - ", score.sellSignals, " SIGNALS ALIGNED");
      for(int i = 0; i < ArraySize(score.sellReasons); i++)
         Print("   ├── ", score.sellReasons[i]);
      Print("   Entry: ", price, " | SL: ", sl, " | TP: ", tp);
      Print("   Lots: ", lots, " | R:R = 1:", RewardRiskRatio);
      Print("════════════════════════════════════════════════════════════════");
   }
}

//+------------------------------------------------------------------+
//| Calculate lot size based on risk                                  |
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
//| Manage open positions                                             |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   SignalScore score;
   AnalyzeSignals(score);
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      
      // Close long if sell signals dominate
      if(type == POSITION_TYPE_BUY && score.sellSignals >= MinSignalsToClose && 
         score.sellSignals > score.buySignals)
      {
         trade.PositionClose(ticket);
         Print("📤 BUY closed - ", score.sellSignals, " sell signals detected");
      }
      // Close short if buy signals dominate
      else if(type == POSITION_TYPE_SELL && score.buySignals >= MinSignalsToClose && 
              score.buySignals > score.sellSignals)
      {
         trade.PositionClose(ticket);
         Print("📤 SELL closed - ", score.buySignals, " buy signals detected");
      }
   }
}

//+------------------------------------------------------------------+
//| Count open positions for this EA                                  |
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
//| Check if within active trading session                            |
//+------------------------------------------------------------------+
bool IsActiveSession()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int hour = dt.hour;
   
   return (hour >= LondonOpen && hour < NYClose);
}

//+------------------------------------------------------------------+
//| Update chart display                                              |
//+------------------------------------------------------------------+
void UpdateDisplay(SignalScore &score)
{
   string info = "";
   info += "════════════════════════════════════════\n";
   info += "  🥇 GOLD HUNTER STRATEGY v3.0\n";
   info += "════════════════════════════════════════\n";
   info += StringFormat("BUY Signals:  %d\n", score.buySignals);
   for(int i = 0; i < MathMin(4, ArraySize(score.buyReasons)); i++)
      info += "  ✓ " + score.buyReasons[i] + "\n";
   info += StringFormat("\nSELL Signals: %d\n", score.sellSignals);
   for(int i = 0; i < MathMin(4, ArraySize(score.sellReasons)); i++)
      info += "  ✓ " + score.sellReasons[i] + "\n";
   info += "\n────────────────────────────────────────\n";
   info += StringFormat("Min to Trade: %d | Daily: %d/%d\n", MinSignalsToTrade, dailyTrades, MaxDailyTrades);
   info += StringFormat("Positions: %d/%d\n", CountOpenPositions(), MaxOpenPositions);
   info += "════════════════════════════════════════";
   
   Comment(info);
}
//+------------------------------------------------------------------+
