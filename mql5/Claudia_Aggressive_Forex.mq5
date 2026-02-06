//+------------------------------------------------------------------+
//|                                    Claudia_Aggressive_Forex.mq5  |
//|                              Copyright 2026, AiiQ-tAIq Platform  |
//|           Claudia Swarm — Aggressive Forex Algo for Practice     |
//+------------------------------------------------------------------+
//| STRATEGY: Hybrid London-Session Breakout + Trend Momentum Scalper|
//|                                                                  |
//| CORE LOGIC (synthesized from top 3 research candidates):         |
//|  1) London Breakout: Pre-session range → breakout entry          |
//|  2) EMA Crossover + RSI + ADX: Triple-confirmation momentum      |
//|  3) Bollinger Squeeze → Expansion: Volatility breakout           |
//|                                                                  |
//| The EA uses two entry modes:                                     |
//|  MODE A: Session Breakout — during London/NY overlap             |
//|  MODE B: Trend Momentum — EMA cross confirmed by RSI + ADX      |
//| Both modes use ATR-based dynamic TP/SL for pair-agnostic sizing. |
//|                                                                  |
//| NETTING COMPLIANT: Max 1 position at a time. No hedging.        |
//| MAGIC: 779901                                                    |
//+------------------------------------------------------------------+
#property copyright   "Copyright 2026, AiiQ-tAIq Claudia Swarm"
#property link        "https://aiiq-taiq.com"
#property version     "1.00"
#property description "Claudia Aggressive Forex — Hybrid Breakout + Momentum"
#property description "Netting mode compliant. ATR-based risk. Session filtered."
#property description "Deploy on AUDUSD M15 or GBPUSD H1."
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//+------------------------------------------------------------------+
//| INPUT GROUPS                                                      |
//+------------------------------------------------------------------+

//=== CORE SETTINGS ===
input group "=== CORE SETTINGS ==="
input int    InpMagicNumber       = 779901;     // Magic Number
input double InpRiskPercent       = 4.0;        // Risk % per trade (3-5 aggressive)
input int    InpMaxTradesPerDay   = 5;          // Max trades per day
input double InpMaxDailyLossPercent = 10.0;     // Daily loss circuit breaker %
input int    InpMaxConsecLosses   = 4;          // Max consecutive losses before cooldown
input int    InpCooldownMinutes   = 120;        // Cooldown after max losses (minutes)

//=== ENTRY MODE ===
input group "=== ENTRY MODE ==="
input bool   InpUseBreakout       = true;       // Enable Breakout Mode
input bool   InpUseMomentum       = true;       // Enable Momentum Mode
input bool   InpUseSqueezeBreak   = true;       // Enable Bollinger Squeeze Breakout

//=== EMA SETTINGS ===
input group "=== EMA CROSSOVER ==="
input int    InpEmaFast           = 9;          // Fast EMA Period
input int    InpEmaSlow           = 21;         // Slow EMA Period
input int    InpEmaTrend          = 50;         // Trend EMA Period (higher TF filter)

//=== RSI SETTINGS ===
input group "=== RSI FILTER ==="
input int    InpRsiPeriod         = 14;         // RSI Period
input double InpRsiBuyLevel       = 55.0;       // RSI Buy threshold (above = bullish)
input double InpRsiSellLevel      = 45.0;       // RSI Sell threshold (below = bearish)
input double InpRsiOverbought     = 75.0;       // RSI Overbought (skip buy)
input double InpRsiOversold       = 25.0;       // RSI Oversold (skip sell)

//=== ADX SETTINGS ===
input group "=== ADX TREND STRENGTH ==="
input int    InpAdxPeriod         = 14;         // ADX Period
input double InpAdxMinimum        = 20.0;       // Min ADX to trade (trend exists)
input double InpAdxStrong         = 30.0;       // Strong trend threshold (tighten TP)

//=== ATR / RISK SETTINGS ===
input group "=== ATR-BASED TP/SL ==="
input int    InpAtrPeriod         = 14;         // ATR Period
input double InpSlAtrMultiplier   = 1.5;        // SL = ATR * this multiplier
input double InpTpAtrMultiplier   = 2.5;        // TP = ATR * this multiplier
input double InpTpTightMultiplier = 1.5;        // TP when ADX > strong (scalp mode)

//=== BOLLINGER SQUEEZE ===
input group "=== BOLLINGER SQUEEZE BREAKOUT ==="
input int    InpBbPeriod          = 20;         // Bollinger Period
input double InpBbDeviation       = 2.0;        // Bollinger StdDev
input double InpSqueezeThreshold  = 0.5;        // Squeeze: BB width < ATR * this

//=== SESSION FILTER ===
input group "=== SESSION FILTER ==="
input bool   InpSessionFilter     = true;       // Only trade during active sessions
input int    InpLondonStartHour   = 7;          // London session start (UTC)
input int    InpNyStartHour       = 12;         // NY session start (UTC)
input int    InpSessionEndHour    = 20;         // Session end (UTC) — no trades after
input int    InpBreakoutRangeStart = 4;         // Pre-London range start (UTC)
input int    InpBreakoutRangeEnd   = 7;         // Pre-London range end (UTC)

//=== SPREAD FILTER ===
input group "=== SPREAD FILTER ==="
input double InpMaxSpreadMultiple = 3.0;        // Skip if spread > normal * this
input int    InpNormalSpreadPoints = 15;         // Normal spread (points) — auto if 0

//=== AGGRESSIVE FEATURES ===
input group "=== AGGRESSIVE FEATURES ==="
input bool   InpMartingaleLite    = true;       // After win: +25% lot. After loss: reset
input double InpMartingaleBoost   = 1.25;       // Win multiplier (1.25 = +25%)
input bool   InpMomentumStack     = true;       // Tighten TP on strong trends
input bool   InpQuickExitRSI      = true;       // Close early on RSI divergence

//=== NEWS FILTER ===
input group "=== NEWS FILTER ==="
input bool   InpNewsFilter        = false;      // Pause before major news (manual toggle)
input int    InpNewsPauseMinutes  = 30;         // Minutes before news to pause

//=== DISPLAY ===
input group "=== DISPLAY ==="
input bool   InpShowDashboard     = true;       // Show on-chart dashboard
input bool   InpLogToCSV          = true;       // Log trades to CSV
input color  InpColorBuy          = clrLime;    // Buy signal color
input color  InpColorSell         = clrRed;     // Sell signal color
input color  InpColorNeutral      = clrGray;    // Neutral color

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                  |
//+------------------------------------------------------------------+
CTrade         g_trade;
CPositionInfo  g_position;
CAccountInfo   g_account;
CSymbolInfo    g_symbol;

// Indicator handles
int g_hEmaFast, g_hEmaSlow, g_hEmaTrend;
int g_hRsi;
int g_hAdx;
int g_hAtr;
int g_hBb;

// State tracking
int    g_tradesToday        = 0;
int    g_consecutiveLosses  = 0;
double g_dayStartBalance    = 0;
double g_dailyPnL           = 0;
bool   g_circuitBroken      = false;
bool   g_cooldownActive     = false;
datetime g_cooldownUntil    = 0;
datetime g_lastTradeTime    = 0;
int    g_lastTradeDay       = 0;
double g_lotBoost           = 1.0;        // Martingale-lite multiplier
bool   g_emergencyClose     = false;

// Breakout range tracking
double g_rangeHigh          = 0;
double g_rangeLow           = 0;
bool   g_rangeCalculated    = false;
int    g_rangeDay           = 0;

// Performance tracking
int    g_totalTrades        = 0;
int    g_totalWins          = 0;
int    g_totalLosses        = 0;
double g_totalProfit        = 0;

// Dashboard object names
string g_dashPrefix         = "CLAUD_";

// CSV file handle
int    g_csvHandle          = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize trade object
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(30);   // 3 pip slippage tolerance
   g_trade.SetTypeFilling(ORDER_FILLING_FOK);
   
   // Initialize symbol info
   g_symbol.Name(Symbol());
   g_symbol.Refresh();
   
   // Create indicator handles
   g_hEmaFast  = iMA(Symbol(), PERIOD_CURRENT, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   g_hEmaSlow  = iMA(Symbol(), PERIOD_CURRENT, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   g_hEmaTrend = iMA(Symbol(), PERIOD_CURRENT, InpEmaTrend, 0, MODE_EMA, PRICE_CLOSE);
   g_hRsi      = iRSI(Symbol(), PERIOD_CURRENT, InpRsiPeriod, PRICE_CLOSE);
   g_hAdx      = iADX(Symbol(), PERIOD_CURRENT, InpAdxPeriod);
   g_hAtr      = iATR(Symbol(), PERIOD_CURRENT, InpAtrPeriod);
   g_hBb       = iBands(Symbol(), PERIOD_CURRENT, InpBbPeriod, 0, InpBbDeviation, PRICE_CLOSE);
   
   // Validate handles
   if(g_hEmaFast == INVALID_HANDLE || g_hEmaSlow == INVALID_HANDLE ||
      g_hEmaTrend == INVALID_HANDLE || g_hRsi == INVALID_HANDLE ||
      g_hAdx == INVALID_HANDLE || g_hAtr == INVALID_HANDLE ||
      g_hBb == INVALID_HANDLE)
   {
      Print("ERROR: Failed to create indicator handles!");
      return(INIT_FAILED);
   }
   
   // Initialize daily tracking
   g_dayStartBalance = g_account.Balance();
   g_lastTradeDay = DayOfYear();
   
   // Open CSV log file
   if(InpLogToCSV)
   {
      string csvName = "Claudia_Forex_" + Symbol() + "_" + 
                        IntegerToString(Year()) + IntegerToString(Month(),2,'0') + 
                        IntegerToString(Day(),2,'0') + ".csv";
      g_csvHandle = FileOpen(csvName, FILE_WRITE|FILE_CSV|FILE_COMMON, ',');
      if(g_csvHandle != INVALID_HANDLE)
      {
         FileWrite(g_csvHandle, "Time", "Symbol", "Action", "Price", "Lots", "SL", "TP",
                   "ATR", "RSI", "ADX", "EMA_Fast", "EMA_Slow", "Spread", "Mode", "Result");
      }
   }
   
   // Print startup banner
   Print("+=============================================================+");
   Print("|  CLAUDIA AGGRESSIVE FOREX v1.00                             |");
   Print("|  Hybrid Breakout + Momentum Scalper                         |");
   Print("|  Symbol: ", Symbol(), "  TF: ", EnumToString(Period()));
   Print("|  Risk: ", InpRiskPercent, "% | Max Trades/Day: ", InpMaxTradesPerDay);
   Print("|  Magic: ", InpMagicNumber);
   Print("|  Breakout: ", InpUseBreakout ? "ON" : "OFF",
         "  Momentum: ", InpUseMomentum ? "ON" : "OFF",
         "  Squeeze: ", InpUseSqueezeBreak ? "ON" : "OFF");
   Print("|  Press 'X' for EMERGENCY CLOSE ALL                          |");
   Print("+=============================================================+");
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Release indicator handles
   if(g_hEmaFast  != INVALID_HANDLE) IndicatorRelease(g_hEmaFast);
   if(g_hEmaSlow  != INVALID_HANDLE) IndicatorRelease(g_hEmaSlow);
   if(g_hEmaTrend != INVALID_HANDLE) IndicatorRelease(g_hEmaTrend);
   if(g_hRsi      != INVALID_HANDLE) IndicatorRelease(g_hRsi);
   if(g_hAdx      != INVALID_HANDLE) IndicatorRelease(g_hAdx);
   if(g_hAtr      != INVALID_HANDLE) IndicatorRelease(g_hAtr);
   if(g_hBb       != INVALID_HANDLE) IndicatorRelease(g_hBb);
   
   // Close CSV
   if(g_csvHandle != INVALID_HANDLE)
      FileClose(g_csvHandle);
   
   // Clean dashboard
   CleanDashboard();
   
   Print("Claudia Aggressive Forex removed. Total trades: ", g_totalTrades,
         " W:", g_totalWins, " L:", g_totalLosses);
}

//+------------------------------------------------------------------+
//| Chart event handler — emergency close on 'X'                     |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   if(id == CHARTEVENT_KEYDOWN)
   {
      // 'X' key = Emergency Close All
      if(lparam == 'X' || lparam == 'x')
      {
         Print("!!! EMERGENCY CLOSE ALL TRIGGERED !!!");
         ClosePosition("EMERGENCY");
         g_emergencyClose = true;
         g_circuitBroken  = true;
      }
   }
}

//+------------------------------------------------------------------+
//| MAIN TICK HANDLER                                                 |
//+------------------------------------------------------------------+
void OnTick()
{
   // Refresh symbol data
   g_symbol.Refresh();
   g_symbol.RefreshRates();
   
   //--- Daily reset check
   CheckDailyReset();
   
   //--- Circuit breaker check
   if(g_circuitBroken)
   {
      if(InpShowDashboard) UpdateDashboard("CIRCUIT BREAKER ACTIVE", clrOrangeRed);
      return;
   }
   
   //--- Cooldown check
   if(g_cooldownActive && TimeCurrent() < g_cooldownUntil)
   {
      if(InpShowDashboard) UpdateDashboard("COOLDOWN: " + TimeToString(g_cooldownUntil - TimeCurrent(), TIME_MINUTES), clrYellow);
      return;
   }
   else if(g_cooldownActive)
   {
      g_cooldownActive = false;
      g_consecutiveLosses = 0;
      Print("Cooldown ended. Resuming trading.");
   }
   
   //--- Check daily loss
   double currentBalance = g_account.Balance();
   g_dailyPnL = currentBalance - g_dayStartBalance;
   double dailyLossPercent = 0;
   if(g_dayStartBalance > 0)
      dailyLossPercent = (-g_dailyPnL / g_dayStartBalance) * 100.0;
   
   if(dailyLossPercent >= InpMaxDailyLossPercent)
   {
      g_circuitBroken = true;
      Print("!!! DAILY LOSS CIRCUIT BREAKER: ", DoubleToString(dailyLossPercent, 2), "% loss reached !!!");
      return;
   }
   
   //--- Max trades check
   if(g_tradesToday >= InpMaxTradesPerDay)
   {
      if(InpShowDashboard) UpdateDashboard("MAX TRADES REACHED: " + IntegerToString(g_tradesToday), clrOrange);
      return;
   }
   
   //--- Manage existing position (quick exit logic)
   if(HasOpenPosition())
   {
      ManageOpenPosition();
      if(InpShowDashboard) UpdateDashboard("IN TRADE", clrDodgerBlue);
      return;
   }
   
   //--- Session filter
   if(InpSessionFilter && !IsInTradingSession())
   {
      // Still calculate breakout range during pre-session
      CalculateBreakoutRange();
      if(InpShowDashboard) UpdateDashboard("OUTSIDE SESSION", clrGray);
      return;
   }
   
   //--- Spread filter
   if(!CheckSpread())
   {
      if(InpShowDashboard) UpdateDashboard("SPREAD TOO WIDE", clrOrange);
      return;
   }
   
   //--- News filter (manual toggle)
   if(InpNewsFilter)
   {
      if(InpShowDashboard) UpdateDashboard("NEWS PAUSE ACTIVE", clrYellow);
      return;
   }
   
   //--- Only act on new bar to avoid over-trading within a bar
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(Symbol(), PERIOD_CURRENT, 0);
   if(currentBarTime == lastBarTime) 
   {
      if(InpShowDashboard) UpdateDashboard("SCANNING...", clrWhite);
      return;
   }
   lastBarTime = currentBarTime;
   
   //--- Get indicator values
   double emaFast[], emaSlow[], emaTrend[];
   double rsi[], adxMain[], adxPlus[], adxMinus[];
   double atr[];
   double bbUpper[], bbMiddle[], bbLower[];
   
   // Copy indicator buffers (2 bars for crossover detection)
   if(CopyBuffer(g_hEmaFast, 0, 1, 2, emaFast) < 2) return;
   if(CopyBuffer(g_hEmaSlow, 0, 1, 2, emaSlow) < 2) return;
   if(CopyBuffer(g_hEmaTrend, 0, 1, 2, emaTrend) < 2) return;
   if(CopyBuffer(g_hRsi, 0, 1, 2, rsi) < 2) return;
   if(CopyBuffer(g_hAdx, 0, 1, 2, adxMain) < 2) return;      // ADX main line
   if(CopyBuffer(g_hAdx, 1, 1, 2, adxPlus) < 2) return;      // +DI
   if(CopyBuffer(g_hAdx, 2, 1, 2, adxMinus) < 2) return;     // -DI
   if(CopyBuffer(g_hAtr, 0, 1, 2, atr) < 2) return;
   if(CopyBuffer(g_hBb, 0, 1, 2, bbMiddle) < 2) return;      // Middle band
   if(CopyBuffer(g_hBb, 1, 1, 2, bbUpper) < 2) return;       // Upper band
   if(CopyBuffer(g_hBb, 2, 1, 2, bbLower) < 2) return;       // Lower band
   
   // Latest bar values (index 1 = latest completed bar in the copied array)
   double curEmaFast  = emaFast[1];
   double curEmaSlow  = emaSlow[1];
   double curEmaTrend = emaTrend[1];
   double prevEmaFast = emaFast[0];
   double prevEmaSlow = emaSlow[0];
   double curRsi      = rsi[1];
   double curAdx      = adxMain[1];
   double curAdxPlus  = adxPlus[1];
   double curAdxMinus = adxMinus[1];
   double curAtr      = atr[1];
   double curBbUpper  = bbUpper[1];
   double curBbLower  = bbLower[1];
   double curBbMiddle = bbMiddle[1];
   double prevBbUpper = bbUpper[0];
   double prevBbLower = bbLower[0];
   
   double price = g_symbol.Ask();  // Current price
   
   //--- Evaluate signals from each mode
   int breakoutSignal  = 0;   // +1 = buy, -1 = sell, 0 = no signal
   int momentumSignal  = 0;
   int squeezeSignal   = 0;
   string signalMode   = "";
   
   //=== MODE A: BREAKOUT ===
   if(InpUseBreakout && g_rangeCalculated)
   {
      breakoutSignal = EvaluateBreakout(price, curAtr, curAdx);
      if(breakoutSignal != 0) signalMode = "BREAKOUT";
   }
   
   //=== MODE B: MOMENTUM (EMA Cross + RSI + ADX) ===
   if(InpUseMomentum)
   {
      momentumSignal = EvaluateMomentum(curEmaFast, curEmaSlow, prevEmaFast, prevEmaSlow,
                                         curEmaTrend, curRsi, curAdx, curAdxPlus, curAdxMinus, price);
      if(momentumSignal != 0 && signalMode == "") signalMode = "MOMENTUM";
   }
   
   //=== MODE C: BOLLINGER SQUEEZE BREAKOUT ===
   if(InpUseSqueezeBreak)
   {
      squeezeSignal = EvaluateSqueezeBreak(curBbUpper, curBbLower, prevBbUpper, prevBbLower,
                                            curAtr, curRsi, price);
      if(squeezeSignal != 0 && signalMode == "") signalMode = "SQUEEZE";
   }
   
   //--- Determine final signal (priority: breakout > momentum > squeeze)
   int finalSignal = 0;
   if(breakoutSignal != 0)
      finalSignal = breakoutSignal;
   else if(momentumSignal != 0)
      finalSignal = momentumSignal;
   else if(squeezeSignal != 0)
      finalSignal = squeezeSignal;
   
   //--- Execute trade if we have a signal
   if(finalSignal != 0)
   {
      // Calculate ATR-based SL/TP
      double slDistance = curAtr * InpSlAtrMultiplier;
      double tpDistance = curAtr * InpTpAtrMultiplier;
      
      // Momentum stacking: tighten TP on strong trends for faster scalps
      if(InpMomentumStack && curAdx > InpAdxStrong)
      {
         tpDistance = curAtr * InpTpTightMultiplier;
         Print("ADX ", DoubleToString(curAdx,1), " > ", DoubleToString(InpAdxStrong,1), 
               " — Tightened TP for quick scalp");
      }
      
      // Calculate lot size
      double lots = CalculateLotSize(slDistance);
      
      // Apply martingale-lite boost
      if(InpMartingaleLite)
         lots = NormalizeDouble(lots * g_lotBoost, 2);
      
      // Ensure minimum lot
      double minLot = g_symbol.LotsMin();
      double maxLot = g_symbol.LotsMax();
      double lotStep = g_symbol.LotsStep();
      lots = MathMax(minLot, lots);
      lots = MathMin(maxLot, lots);
      lots = NormalizeDouble(MathFloor(lots / lotStep) * lotStep, 2);
      
      if(finalSignal > 0)
         ExecuteBuy(lots, slDistance, tpDistance, curAtr, curRsi, curAdx, 
                    curEmaFast, curEmaSlow, signalMode);
      else
         ExecuteSell(lots, slDistance, tpDistance, curAtr, curRsi, curAdx,
                     curEmaFast, curEmaSlow, signalMode);
   }
   else
   {
      if(InpShowDashboard) UpdateDashboard("NO SIGNAL", clrWhite);
   }
}

//+------------------------------------------------------------------+
//| MODE A: Evaluate Breakout Signal                                  |
//+------------------------------------------------------------------+
int EvaluateBreakout(double price, double atr, double adx)
{
   if(!g_rangeCalculated) return 0;
   if(g_rangeHigh <= g_rangeLow) return 0;
   
   double rangeSize = g_rangeHigh - g_rangeLow;
   
   // Range must be meaningful (at least 0.3 * ATR)
   if(rangeSize < atr * 0.3) return 0;
   
   // Need minimum trend strength for breakout follow-through
   if(adx < InpAdxMinimum * 0.8) return 0;  // Slightly relaxed for breakouts
   
   double breakBuffer = atr * 0.1;  // Small buffer above/below range
   
   if(price > g_rangeHigh + breakBuffer)
   {
      Print("BREAKOUT BUY: Price ", DoubleToString(price, _Digits), 
            " broke above range high ", DoubleToString(g_rangeHigh, _Digits));
      return 1;
   }
   
   if(price < g_rangeLow - breakBuffer)
   {
      Print("BREAKOUT SELL: Price ", DoubleToString(price, _Digits),
            " broke below range low ", DoubleToString(g_rangeLow, _Digits));
      return -1;
   }
   
   return 0;
}

//+------------------------------------------------------------------+
//| MODE B: Evaluate Momentum Signal (EMA + RSI + ADX)               |
//+------------------------------------------------------------------+
int EvaluateMomentum(double emaFast, double emaSlow, double prevEmaFast, double prevEmaSlow,
                      double emaTrend, double rsi, double adx, double adxPlus, double adxMinus,
                      double price)
{
   // Check ADX minimum — need a trend to exist
   if(adx < InpAdxMinimum) return 0;
   
   //--- BUY conditions
   bool emaCrossBuy   = (prevEmaFast <= prevEmaSlow && emaFast > emaSlow);  // Fresh crossover
   bool emaAboveTrend = (emaFast > emaTrend);                                // Above trend EMA
   bool rsiBullish    = (rsi > InpRsiBuyLevel && rsi < InpRsiOverbought);   // RSI in buy zone
   bool diPlusBull    = (adxPlus > adxMinus);                                // +DI above -DI
   bool priceBullish  = (price > emaSlow);                                   // Price above slow EMA
   
   if(emaCrossBuy && emaAboveTrend && rsiBullish && diPlusBull && priceBullish)
   {
      Print("MOMENTUM BUY: EMA Cross + RSI ", DoubleToString(rsi,1), 
            " + ADX ", DoubleToString(adx,1), " +DI>-DI");
      return 1;
   }
   
   //--- SELL conditions
   bool emaCrossSell  = (prevEmaFast >= prevEmaSlow && emaFast < emaSlow);  // Fresh crossover down
   bool emaBelowTrend = (emaFast < emaTrend);                                // Below trend EMA
   bool rsiBearish    = (rsi < InpRsiSellLevel && rsi > InpRsiOversold);    // RSI in sell zone
   bool diMinusBear   = (adxMinus > adxPlus);                                // -DI above +DI
   bool priceBearish  = (price < emaSlow);                                   // Price below slow EMA
   
   if(emaCrossSell && emaBelowTrend && rsiBearish && diMinusBear && priceBearish)
   {
      Print("MOMENTUM SELL: EMA Cross + RSI ", DoubleToString(rsi,1),
            " + ADX ", DoubleToString(adx,1), " -DI>+DI");
      return -1;
   }
   
   return 0;
}

//+------------------------------------------------------------------+
//| MODE C: Evaluate Bollinger Squeeze Breakout                       |
//+------------------------------------------------------------------+
int EvaluateSqueezeBreak(double bbUpper, double bbLower, double prevBbUpper, double prevBbLower,
                          double atr, double rsi, double price)
{
   double bbWidth     = bbUpper - bbLower;
   double prevBbWidth = prevBbUpper - prevBbLower;
   
   // Squeeze condition: BB width was tight (less than ATR * threshold)
   bool wasSqueezed = (prevBbWidth < atr * InpSqueezeThreshold);
   
   // Expansion condition: BB width is now wider
   bool expanding = (bbWidth > prevBbWidth * 1.2);  // 20% expansion
   
   if(!wasSqueezed || !expanding) return 0;
   
   // Direction based on price position relative to middle band + RSI
   double bbMiddle = (bbUpper + bbLower) / 2.0;
   
   if(price > bbUpper && rsi > 50)
   {
      Print("SQUEEZE BREAKOUT BUY: Price above upper BB, RSI ", DoubleToString(rsi,1),
            " BB expanding from squeeze");
      return 1;
   }
   
   if(price < bbLower && rsi < 50)
   {
      Print("SQUEEZE BREAKOUT SELL: Price below lower BB, RSI ", DoubleToString(rsi,1),
            " BB expanding from squeeze");
      return -1;
   }
   
   return 0;
}

//+------------------------------------------------------------------+
//| Calculate pre-session breakout range                              |
//+------------------------------------------------------------------+
void CalculateBreakoutRange()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   
   // Only recalculate once per day
   if(dt.day_of_year == g_rangeDay && g_rangeCalculated) return;
   
   // Calculate during pre-London period
   if(dt.hour < InpBreakoutRangeEnd)
   {
      // Get bars in the range window
      g_rangeHigh = 0;
      g_rangeLow  = DBL_MAX;
      
      int totalBars = Bars(Symbol(), PERIOD_CURRENT);
      if(totalBars < 100) return;
      
      for(int i = 1; i <= 50; i++)  // Check last 50 bars
      {
         datetime barTime = iTime(Symbol(), PERIOD_CURRENT, i);
         MqlDateTime barDt;
         TimeToStruct(barTime, barDt);
         
         // Only include bars from today's pre-session range
         if(barDt.day_of_year != dt.day_of_year) continue;
         if(barDt.hour < InpBreakoutRangeStart || barDt.hour >= InpBreakoutRangeEnd) continue;
         
         double high = iHigh(Symbol(), PERIOD_CURRENT, i);
         double low  = iLow(Symbol(), PERIOD_CURRENT, i);
         
         if(high > g_rangeHigh) g_rangeHigh = high;
         if(low  < g_rangeLow)  g_rangeLow  = low;
      }
      
      if(g_rangeHigh > 0 && g_rangeLow < DBL_MAX && g_rangeHigh > g_rangeLow)
      {
         g_rangeCalculated = true;
         g_rangeDay = dt.day_of_year;
         Print("Breakout range set: High=", DoubleToString(g_rangeHigh, _Digits),
               " Low=", DoubleToString(g_rangeLow, _Digits),
               " Size=", DoubleToString(g_rangeHigh - g_rangeLow, _Digits));
      }
   }
}

//+------------------------------------------------------------------+
//| Check if we're in an active trading session                       |
//+------------------------------------------------------------------+
bool IsInTradingSession()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int hour = dt.hour;
   
   // Active: London start to session end
   return (hour >= InpLondonStartHour && hour < InpSessionEndHour);
}

//+------------------------------------------------------------------+
//| Check spread                                                      |
//+------------------------------------------------------------------+
bool CheckSpread()
{
   double spread = g_symbol.Spread();
   double maxSpread;
   
   if(InpNormalSpreadPoints > 0)
      maxSpread = InpNormalSpreadPoints * InpMaxSpreadMultiple;
   else
      maxSpread = spread * InpMaxSpreadMultiple;  // Dynamic
   
   return (spread <= maxSpread);
}

//+------------------------------------------------------------------+
//| Check if there's an open position for this EA                     |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(g_position.SelectByIndex(i))
      {
         if(g_position.Symbol() == Symbol() && g_position.Magic() == InpMagicNumber)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Manage open position — quick exit on RSI divergence               |
//+------------------------------------------------------------------+
void ManageOpenPosition()
{
   if(!InpQuickExitRSI) return;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!g_position.SelectByIndex(i)) continue;
      if(g_position.Symbol() != Symbol() || g_position.Magic() != InpMagicNumber) continue;
      
      double profit = g_position.Profit();
      
      // Only apply quick exit if in profit
      if(profit <= 0) return;
      
      // Get current RSI
      double rsi[];
      if(CopyBuffer(g_hRsi, 0, 1, 3, rsi) < 3) return;
      
      double curRsi  = rsi[2];
      double prevRsi = rsi[1];
      
      if(g_position.PositionType() == POSITION_TYPE_BUY)
      {
         // In a buy: if RSI is falling from overbought while in profit
         if(curRsi < prevRsi && prevRsi > InpRsiOverbought && profit > 0)
         {
            Print("QUICK EXIT BUY: RSI divergence (", DoubleToString(prevRsi,1), 
                  " -> ", DoubleToString(curRsi,1), ") Profit: ", DoubleToString(profit,2));
            ClosePosition("RSI_QUICK_EXIT");
         }
      }
      else if(g_position.PositionType() == POSITION_TYPE_SELL)
      {
         // In a sell: if RSI is rising from oversold while in profit
         if(curRsi > prevRsi && prevRsi < InpRsiOversold && profit > 0)
         {
            Print("QUICK EXIT SELL: RSI divergence (", DoubleToString(prevRsi,1),
                  " -> ", DoubleToString(curRsi,1), ") Profit: ", DoubleToString(profit,2));
            ClosePosition("RSI_QUICK_EXIT");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Calculate lot size based on risk % and ATR stop distance          |
//+------------------------------------------------------------------+
double CalculateLotSize(double slDistancePrice)
{
   double balance    = g_account.Balance();
   double riskAmount = balance * (InpRiskPercent / 100.0);
   
   // Get tick value and size for this symbol
   double tickValue  = g_symbol.TickValue();
   double tickSize   = g_symbol.TickSize();
   
   if(tickSize == 0 || tickValue == 0 || slDistancePrice == 0) 
      return g_symbol.LotsMin();
   
   // SL in ticks
   double slTicks = slDistancePrice / tickSize;
   
   // Lot size = risk amount / (SL in ticks * tick value)
   double lots = riskAmount / (slTicks * tickValue);
   
   return lots;
}

//+------------------------------------------------------------------+
//| Execute BUY                                                       |
//+------------------------------------------------------------------+
void ExecuteBuy(double lots, double slDist, double tpDist, double atr,
                double rsi, double adx, double emaF, double emaS, string mode)
{
   g_symbol.RefreshRates();
   double ask = g_symbol.Ask();
   double sl  = NormalizeDouble(ask - slDist, _Digits);
   double tp  = NormalizeDouble(ask + tpDist, _Digits);
   
   string comment = "Claudia_" + mode + "_BUY";
   
   if(g_trade.Buy(lots, Symbol(), ask, sl, tp, comment))
   {
      Print(">>> BUY EXECUTED: ", lots, " lots @ ", DoubleToString(ask, _Digits),
            " SL:", DoubleToString(sl, _Digits), " TP:", DoubleToString(tp, _Digits),
            " Mode:", mode);
      
      g_tradesToday++;
      g_totalTrades++;
      g_lastTradeTime = TimeCurrent();
      
      // Log to CSV
      LogTradeCSV("BUY", ask, lots, sl, tp, atr, rsi, adx, emaF, emaS, mode);
      
      if(InpShowDashboard) UpdateDashboard("BUY " + mode, InpColorBuy);
   }
   else
   {
      Print("!!! BUY FAILED: Error ", GetLastError(), " — ", g_trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
//| Execute SELL                                                      |
//+------------------------------------------------------------------+
void ExecuteSell(double lots, double slDist, double tpDist, double atr,
                 double rsi, double adx, double emaF, double emaS, string mode)
{
   g_symbol.RefreshRates();
   double bid = g_symbol.Bid();
   double sl  = NormalizeDouble(bid + slDist, _Digits);
   double tp  = NormalizeDouble(bid - tpDist, _Digits);
   
   string comment = "Claudia_" + mode + "_SELL";
   
   if(g_trade.Sell(lots, Symbol(), bid, sl, tp, comment))
   {
      Print(">>> SELL EXECUTED: ", lots, " lots @ ", DoubleToString(bid, _Digits),
            " SL:", DoubleToString(sl, _Digits), " TP:", DoubleToString(tp, _Digits),
            " Mode:", mode);
      
      g_tradesToday++;
      g_totalTrades++;
      g_lastTradeTime = TimeCurrent();
      
      // Log to CSV
      LogTradeCSV("SELL", bid, lots, sl, tp, atr, rsi, adx, emaF, emaS, mode);
      
      if(InpShowDashboard) UpdateDashboard("SELL " + mode, InpColorSell);
   }
   else
   {
      Print("!!! SELL FAILED: Error ", GetLastError(), " — ", g_trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
//| Close position                                                    |
//+------------------------------------------------------------------+
void ClosePosition(string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!g_position.SelectByIndex(i)) continue;
      if(g_position.Symbol() != Symbol() || g_position.Magic() != InpMagicNumber) continue;
      
      double profit = g_position.Profit();
      ulong ticket  = g_position.Ticket();
      
      if(g_trade.PositionClose(ticket))
      {
         Print("<<< POSITION CLOSED [", reason, "]: Profit=", DoubleToString(profit, 2));
         
         // Track win/loss for martingale-lite and consecutive losses
         if(profit >= 0)
         {
            g_totalWins++;
            g_consecutiveLosses = 0;
            
            // Martingale-lite: boost after win
            if(InpMartingaleLite)
            {
               g_lotBoost = InpMartingaleBoost;
               Print("Martingale-lite: Next trade boost x", DoubleToString(g_lotBoost, 2));
            }
         }
         else
         {
            g_totalLosses++;
            g_consecutiveLosses++;
            g_lotBoost = 1.0;  // Reset boost on loss
            
            Print("Consecutive losses: ", g_consecutiveLosses, "/", InpMaxConsecLosses);
            
            // Check consecutive loss limit
            if(g_consecutiveLosses >= InpMaxConsecLosses)
            {
               g_cooldownActive = true;
               g_cooldownUntil  = TimeCurrent() + InpCooldownMinutes * 60;
               Print("!!! COOLDOWN ACTIVATED: ", InpCooldownMinutes, " minutes until ", 
                     TimeToString(g_cooldownUntil));
            }
         }
         
         g_totalProfit += profit;
      }
   }
}

//+------------------------------------------------------------------+
//| Daily reset                                                       |
//+------------------------------------------------------------------+
void CheckDailyReset()
{
   int currentDay = DayOfYear();
   if(currentDay != g_lastTradeDay)
   {
      Print("=== NEW TRADING DAY === Resetting daily counters.");
      Print("Yesterday: Trades=", g_tradesToday, " PnL=", DoubleToString(g_dailyPnL, 2));
      
      g_lastTradeDay     = currentDay;
      g_tradesToday      = 0;
      g_circuitBroken    = false;
      g_emergencyClose   = false;
      g_dayStartBalance  = g_account.Balance();
      g_dailyPnL         = 0;
      g_rangeCalculated  = false;
      g_rangeDay         = 0;
      g_rangeHigh        = 0;
      g_rangeLow         = 0;
      
      // Open new daily CSV log
      if(InpLogToCSV && g_csvHandle != INVALID_HANDLE)
      {
         FileClose(g_csvHandle);
         string csvName = "Claudia_Forex_" + Symbol() + "_" +
                           IntegerToString(Year()) + IntegerToString(Month(),2,'0') +
                           IntegerToString(Day(),2,'0') + ".csv";
         g_csvHandle = FileOpen(csvName, FILE_WRITE|FILE_CSV|FILE_COMMON, ',');
         if(g_csvHandle != INVALID_HANDLE)
         {
            FileWrite(g_csvHandle, "Time", "Symbol", "Action", "Price", "Lots", "SL", "TP",
                      "ATR", "RSI", "ADX", "EMA_Fast", "EMA_Slow", "Spread", "Mode", "Result");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Helper: Day of Year                                               |
//+------------------------------------------------------------------+
int DayOfYear()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return dt.day_of_year;
}

//+------------------------------------------------------------------+
//| Log trade to CSV                                                  |
//+------------------------------------------------------------------+
void LogTradeCSV(string action, double price, double lots, double sl, double tp,
                  double atr, double rsi, double adx, double emaF, double emaS, string mode)
{
   if(g_csvHandle == INVALID_HANDLE) return;
   
   FileWrite(g_csvHandle,
      TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS),
      Symbol(),
      action,
      DoubleToString(price, _Digits),
      DoubleToString(lots, 2),
      DoubleToString(sl, _Digits),
      DoubleToString(tp, _Digits),
      DoubleToString(atr, _Digits),
      DoubleToString(rsi, 1),
      DoubleToString(adx, 1),
      DoubleToString(emaF, _Digits),
      DoubleToString(emaS, _Digits),
      IntegerToString(g_symbol.Spread()),
      mode,
      "OPEN");
   
   FileFlush(g_csvHandle);
}

//+------------------------------------------------------------------+
//| ON-CHART DASHBOARD                                                |
//+------------------------------------------------------------------+
void UpdateDashboard(string status, color statusColor)
{
   if(!InpShowDashboard) return;
   
   int x = 10;
   int y = 30;
   int lineHeight = 18;
   int line = 0;
   
   // Get latest indicator values for display
   double emaFast[], emaSlow[], rsi[], adxMain[], atr[];
   CopyBuffer(g_hEmaFast, 0, 1, 1, emaFast);
   CopyBuffer(g_hEmaSlow, 0, 1, 1, emaSlow);
   CopyBuffer(g_hRsi, 0, 1, 1, rsi);
   CopyBuffer(g_hAdx, 0, 1, 1, adxMain);
   CopyBuffer(g_hAtr, 0, 1, 1, atr);
   
   double curPrice = g_symbol.Bid();
   
   // Determine trend
   string trend = "---";
   color trendColor = InpColorNeutral;
   if(ArraySize(emaFast) > 0 && ArraySize(emaSlow) > 0)
   {
      if(emaFast[0] > emaSlow[0]) { trend = "BULLISH"; trendColor = InpColorBuy; }
      else                         { trend = "BEARISH"; trendColor = InpColorSell; }
   }
   
   // Create/update labels
   CreateLabel(g_dashPrefix + "header", x, y + lineHeight * line++, 
               "CLAUDIA AGGRESSIVE FOREX v1.00", clrGold, 10);
   CreateLabel(g_dashPrefix + "sep1", x, y + lineHeight * line++,
               "-------------------------------------", clrDarkGray, 8);
   CreateLabel(g_dashPrefix + "status", x, y + lineHeight * line++,
               "Status: " + status, statusColor, 9);
   CreateLabel(g_dashPrefix + "trend", x, y + lineHeight * line++,
               "Trend: " + trend, trendColor, 9);
   CreateLabel(g_dashPrefix + "price", x, y + lineHeight * line++,
               "Price: " + DoubleToString(curPrice, _Digits) + 
               "  Spread: " + IntegerToString(g_symbol.Spread()), clrWhite, 9);
   
   string emaStr = "EMA: ";
   if(ArraySize(emaFast) > 0) emaStr += DoubleToString(emaFast[0], _Digits);
   emaStr += " / ";
   if(ArraySize(emaSlow) > 0) emaStr += DoubleToString(emaSlow[0], _Digits);
   CreateLabel(g_dashPrefix + "ema", x, y + lineHeight * line++, emaStr, clrWhite, 9);
   
   string rsiStr = "RSI: ";
   if(ArraySize(rsi) > 0) rsiStr += DoubleToString(rsi[0], 1);
   string adxStr = "  ADX: ";
   if(ArraySize(adxMain) > 0) adxStr += DoubleToString(adxMain[0], 1);
   CreateLabel(g_dashPrefix + "rsiadx", x, y + lineHeight * line++, rsiStr + adxStr, clrWhite, 9);
   
   string atrStr = "ATR: ";
   if(ArraySize(atr) > 0) atrStr += DoubleToString(atr[0], _Digits);
   CreateLabel(g_dashPrefix + "atr", x, y + lineHeight * line++, atrStr, clrWhite, 9);
   
   CreateLabel(g_dashPrefix + "sep2", x, y + lineHeight * line++,
               "-------------------------------------", clrDarkGray, 8);
   
   // Performance stats
   double winRate = (g_totalTrades > 0) ? (double)g_totalWins / g_totalTrades * 100.0 : 0;
   CreateLabel(g_dashPrefix + "trades", x, y + lineHeight * line++,
               "Trades Today: " + IntegerToString(g_tradesToday) + "/" + IntegerToString(InpMaxTradesPerDay) +
               "  Total: " + IntegerToString(g_totalTrades), clrWhite, 9);
   CreateLabel(g_dashPrefix + "winloss", x, y + lineHeight * line++,
               "W:" + IntegerToString(g_totalWins) + " L:" + IntegerToString(g_totalLosses) +
               " WR:" + DoubleToString(winRate, 1) + "%", clrWhite, 9);
   CreateLabel(g_dashPrefix + "pnl", x, y + lineHeight * line++,
               "Daily PnL: " + DoubleToString(g_dailyPnL, 2) + 
               "  Total: " + DoubleToString(g_totalProfit, 2),
               (g_dailyPnL >= 0 ? clrLime : clrRed), 9);
   
   // Martingale status
   if(InpMartingaleLite)
   {
      CreateLabel(g_dashPrefix + "mart", x, y + lineHeight * line++,
                  "Lot Boost: x" + DoubleToString(g_lotBoost, 2) + 
                  "  ConsecLoss: " + IntegerToString(g_consecutiveLosses),
                  (g_lotBoost > 1.0 ? clrGold : clrWhite), 9);
   }
   
   // Breakout range
   if(InpUseBreakout && g_rangeCalculated)
   {
      CreateLabel(g_dashPrefix + "range", x, y + lineHeight * line++,
                  "Range: " + DoubleToString(g_rangeLow, _Digits) + " - " + 
                  DoubleToString(g_rangeHigh, _Digits), clrCyan, 9);
   }
   
   CreateLabel(g_dashPrefix + "sep3", x, y + lineHeight * line++,
               "-------------------------------------", clrDarkGray, 8);
   CreateLabel(g_dashPrefix + "hotkey", x, y + lineHeight * line++,
               "Press X = EMERGENCY CLOSE ALL", clrOrangeRed, 8);
   
   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Create or update chart label                                      |
//+------------------------------------------------------------------+
void CreateLabel(string name, int x, int y, string text, color clr, int fontSize)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
}

//+------------------------------------------------------------------+
//| Clean all dashboard objects                                       |
//+------------------------------------------------------------------+
void CleanDashboard()
{
   int total = ObjectsTotal(0, 0, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, g_dashPrefix) == 0)
         ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| OnTradeTransaction — Track closed trades for martingale/stats     |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                         const MqlTradeRequest &request,
                         const MqlTradeResult &result)
{
   // Detect when our position is closed (by SL/TP/manual)
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      // Check if this deal is for our symbol and magic
      if(trans.symbol == Symbol())
      {
         ulong dealTicket = trans.deal;
         
         if(dealTicket > 0)
         {
            // Select the deal to check properties
            if(HistoryDealSelect(dealTicket))
            {
               long dealMagic = HistoryDealGetInteger(dealTicket, DEAL_MAGIC);
               long dealEntry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
               
               if(dealMagic == InpMagicNumber && dealEntry == DEAL_ENTRY_OUT)
               {
                  double dealProfit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
                  double dealComm   = HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
                  double dealSwap   = HistoryDealGetDouble(dealTicket, DEAL_SWAP);
                  double netProfit  = dealProfit + dealComm + dealSwap;
                  
                  Print("DEAL CLOSED: Ticket=", dealTicket, 
                        " Profit=", DoubleToString(dealProfit,2),
                        " Comm=", DoubleToString(dealComm,2),
                        " Net=", DoubleToString(netProfit,2));
                  
                  if(netProfit >= 0)
                  {
                     g_totalWins++;
                     g_consecutiveLosses = 0;
                     if(InpMartingaleLite)
                        g_lotBoost = InpMartingaleBoost;
                  }
                  else
                  {
                     g_totalLosses++;
                     g_consecutiveLosses++;
                     g_lotBoost = 1.0;
                     
                     if(g_consecutiveLosses >= InpMaxConsecLosses)
                     {
                        g_cooldownActive = true;
                        g_cooldownUntil  = TimeCurrent() + InpCooldownMinutes * 60;
                        Print("COOLDOWN ACTIVATED after ", g_consecutiveLosses, " losses");
                     }
                  }
                  
                  g_totalProfit += netProfit;
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
