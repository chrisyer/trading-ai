# HYDRA SCALPER — MQL5 Translation Guide
## From Python Backtest to MetaTrader 5 Expert Advisor

**Target:** FOREX.com US account (MT5), FIFO compliant
**Author:** QUINN001 for CRELLA001
**Date:** 2026-02-10

---

## ARCHITECTURE MAPPING

| Python Module | MQL5 Equivalent |
|---|---|
| `indicators.py` (EMA, ATR, ADX, RSI, MACD, BB, Keltner) | Native MQL5: `iMA()`, `iATR()`, `iADX()`, `iRSI()`, `iMACD()`, `iBands()` + custom Keltner |
| `strategy.py` (signal generation) | `OnTick()` logic in the EA |
| `backtest.py` | MT5 Strategy Tester (built-in) |
| `risk.py` (position sizing) | Custom `CalculateLotSize()` function |
| `live_executor.py` (order management) | `OrderSend()`, `PositionModify()` |
| `config.py` (parameters) | `input` variables at top of EA |
| `optimizer.py` (Optuna) | MT5 Strategy Tester optimization (genetic algorithm) |

---

## MQL5 EA SKELETON

```mql5
//+------------------------------------------------------------------+
//| HYDRA SCALPER v1.0 — Expert Advisor                               |
//| Built by QUINN001/CRELLA001                                       |
//+------------------------------------------------------------------+
#property copyright "CRELLA001"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//--- Input Parameters (Layer 1: Regime)
input int    RegimeEMAFast     = 9;
input int    RegimeEMAMid      = 21;
input int    RegimeEMASlow     = 50;
input int    RegimeADXPeriod   = 14;
input double RegimeADXThresh   = 22.0;

//--- Input Parameters (Layer 2: Volatility)
input int    VolATRPeriod      = 14;
input double VolPercentile     = 75.0;
input int    VolLookback       = 200;

//--- Input Parameters (Layer 3: Entry)
input int    BBPeriod          = 20;
input double BBStd             = 2.0;
input int    KCPeriod          = 20;
input double KCMult            = 1.5;
input int    SqueezeMinBars    = 6;
input int    MACDFast          = 8;
input int    MACDSlow          = 17;
input int    MACDSignal        = 9;
input int    RSIPeriod         = 5;
input double RSIOB             = 85.0;
input double RSIOS             = 15.0;
input int    EntryEMAFast      = 8;
input int    EntryEMASlow      = 21;
input double PullbackTolATR    = 0.3;

//--- Input Parameters (Layer 4: Exit)
input double TPATRMult         = 2.0;
input double SLATRMult         = 0.8;
input double TPMaxPips         = 18.0;
input double SLMaxPips         = 12.0;
input double BreakevenPips     = 6.0;
input double TrailActivatePips = 8.0;
input double TrailDistATR      = 0.6;

//--- Input Parameters (Layer 5: Risk)
input double RiskPercent       = 1.0;
input int    MaxDailyTrades    = 150;
input int    CooldownBars      = 3;

//--- Global variables
CTrade trade;
int handleADX, handleATR, handleBB, handleMACD, handleRSI;
int handleEMAFast_HTF, handleEMAMid_HTF, handleEMASlow_HTF;
int handleEMAFast_M1, handleEMASlow_M1;
int squeezeCount = 0;
bool prevSqueeze = false;
int lastTradeBar = -100;
int dailyTradeCount = 0;
datetime lastDay = 0;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   // 15-min HTF indicator handles
   handleEMAFast_HTF = iMA(_Symbol, PERIOD_M15, RegimeEMAFast, 0, MODE_EMA, PRICE_CLOSE);
   handleEMAMid_HTF  = iMA(_Symbol, PERIOD_M15, RegimeEMAMid,  0, MODE_EMA, PRICE_CLOSE);
   handleEMASlow_HTF = iMA(_Symbol, PERIOD_M15, RegimeEMASlow, 0, MODE_EMA, PRICE_CLOSE);
   handleADX         = iADX(_Symbol, PERIOD_M15, RegimeADXPeriod);

   // 1-min indicator handles
   handleATR   = iATR(_Symbol, PERIOD_M1, VolATRPeriod);
   handleBB    = iBands(_Symbol, PERIOD_M1, BBPeriod, 0, BBStd, PRICE_CLOSE);
   handleMACD  = iMACD(_Symbol, PERIOD_M1, MACDFast, MACDSlow, MACDSignal, PRICE_CLOSE);
   handleRSI   = iRSI(_Symbol, PERIOD_M1, RSIPeriod, PRICE_CLOSE);
   handleEMAFast_M1 = iMA(_Symbol, PERIOD_M1, EntryEMAFast, 0, MODE_EMA, PRICE_CLOSE);
   handleEMASlow_M1 = iMA(_Symbol, PERIOD_M1, EntryEMASlow, 0, MODE_EMA, PRICE_CLOSE);

   trade.SetDeviationInPoints(30);  // 3 pip max slippage
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert tick function — main logic                                 |
//+------------------------------------------------------------------+
void OnTick()
{
   // Only process on new M1 bar
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_M1, 0);
   if(currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

   // --- SESSION FILTER ---
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.hour < 7 || dt.hour >= 17) return;  // London->NY only

   // Reset daily counter
   datetime today = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   if(today != lastDay) { dailyTradeCount = 0; lastDay = today; }
   if(dailyTradeCount >= MaxDailyTrades) return;

   // --- LAYER 1: REGIME FILTER (15-min) ---
   double emaFast_HTF[], emaMid_HTF[], emaSlow_HTF[], adxVal[];
   CopyBuffer(handleEMAFast_HTF, 0, 0, 2, emaFast_HTF);
   CopyBuffer(handleEMAMid_HTF,  0, 0, 2, emaMid_HTF);
   CopyBuffer(handleEMASlow_HTF, 0, 0, 2, emaSlow_HTF);
   CopyBuffer(handleADX, 0, 0, 2, adxVal);  // ADX main line

   int regime = 0;
   if(emaFast_HTF[0] > emaMid_HTF[0] && emaMid_HTF[0] > emaSlow_HTF[0] && adxVal[0] > RegimeADXThresh)
      regime = 1;   // Bullish
   else if(emaFast_HTF[0] < emaMid_HTF[0] && emaMid_HTF[0] < emaSlow_HTF[0] && adxVal[0] > RegimeADXThresh)
      regime = -1;  // Bearish
   if(regime == 0) return;  // Flat — skip

   // --- LAYER 2: VOLATILITY GATE ---
   double atrVal[];
   CopyBuffer(handleATR, 0, 0, VolLookback + 1, atrVal);
   double currentATR = atrVal[0];
   // Calculate percentile
   double sortedATR[];
   ArrayCopy(sortedATR, atrVal, 0, 1, VolLookback);
   ArraySort(sortedATR);
   int pctIdx = (int)(VolLookback * VolPercentile / 100.0);
   double atrThreshold = sortedATR[pctIdx];
   if(currentATR < atrThreshold) return;  // Too quiet

   // --- LAYER 3: SQUEEZE DETECTION ---
   double bbUpper[], bbLower[], bbMid[];
   CopyBuffer(handleBB, 1, 0, 2, bbUpper);  // Upper band
   CopyBuffer(handleBB, 2, 0, 2, bbLower);  // Lower band
   CopyBuffer(handleBB, 0, 0, 2, bbMid);    // Middle

   // Keltner Channel (custom — not native in MQL5)
   double kcUpper = bbMid[0] + KCMult * currentATR;  // Approximation using BB mid + ATR
   double kcLower = bbMid[0] - KCMult * currentATR;

   bool squeezeOn = (bbLower[0] > kcLower) && (bbUpper[0] < kcUpper);
   bool squeezeFire = prevSqueeze && !squeezeOn;
   if(squeezeOn) squeezeCount++; else { squeezeCount = 0; }
   prevSqueeze = squeezeOn;

   if(!squeezeFire || squeezeCount < SqueezeMinBars) return;

   // --- LAYER 3: MACD + RSI + PULLBACK ---
   double macdLine[], macdSignalLine[], macdHist[];
   CopyBuffer(handleMACD, 0, 0, 3, macdLine);
   CopyBuffer(handleMACD, 1, 0, 3, macdSignalLine);
   for(int j=0; j<3; j++) macdHist[j] = macdLine[j] - macdSignalLine[j];

   double rsiVal[];
   CopyBuffer(handleRSI, 0, 0, 2, rsiVal);

   double emaFast_M1[], emaSlow_M1[];
   CopyBuffer(handleEMAFast_M1, 0, 0, 2, emaFast_M1);
   CopyBuffer(handleEMASlow_M1, 0, 0, 2, emaSlow_M1);

   double close = iClose(_Symbol, PERIOD_M1, 0);
   double momentum = (close - bbMid[0]) / MathMax(currentATR, _Point);

   // Confirmation checks
   bool macdExpanding = (macdHist[0] > 0 && macdHist[0] > macdHist[1]) ||
                        (macdHist[0] < 0 && macdHist[0] < macdHist[1]);
   double emaZoneMid = (emaFast_M1[0] + emaSlow_M1[0]) / 2.0;
   bool inPullback = MathAbs(close - emaZoneMid) < PullbackTolATR * currentATR;
   bool confirm = macdExpanding || inPullback;
   if(!confirm) return;

   // Cooldown
   int currentBar = Bars(_Symbol, PERIOD_M1);
   if(currentBar - lastTradeBar < CooldownBars) return;

   // --- SIGNAL DECISION ---
   int signal = 0;
   if(regime == 1 && momentum > 0 && rsiVal[0] < RSIOB)
      signal = 1;   // BUY
   else if(regime == -1 && momentum < 0 && rsiVal[0] > RSIOS)
      signal = -1;  // SELL
   if(signal == 0) return;

   // --- FIFO CHECK: No existing position on same symbol ---
   if(PositionSelect(_Symbol)) return;

   // --- LAYER 4: CALCULATE SL/TP ---
   double pip = _Point * 10;  // For 5-digit broker
   double slPips = MathMin(currentATR * SLATRMult / pip, SLMaxPips);
   slPips = MathMax(slPips, 3.0);
   double tpPips = MathMin(currentATR * TPATRMult / pip, TPMaxPips);
   tpPips = MathMax(tpPips, 6.0);
   if(tpPips < 1.3 * slPips) return;  // RR too tight

   double slPrice, tpPrice, entryPrice;
   if(signal == 1) {
      entryPrice = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      slPrice = entryPrice - slPips * pip;
      tpPrice = entryPrice + tpPips * pip;
   } else {
      entryPrice = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      slPrice = entryPrice + slPips * pip;
      tpPrice = entryPrice - tpPips * pip;
   }

   // --- POSITION SIZING ---
   double lots = CalculateLotSize(slPips);

   // --- EXECUTE ---
   if(signal == 1)
      trade.Buy(lots, _Symbol, 0, slPrice, tpPrice, "HYDRA_LONG");
   else
      trade.Sell(lots, _Symbol, 0, slPrice, tpPrice, "HYDRA_SHORT");

   lastTradeBar = currentBar;
   dailyTradeCount++;
}

//+------------------------------------------------------------------+
//| Position sizing — risk-based                                      |
//+------------------------------------------------------------------+
double CalculateLotSize(double slPips)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * RiskPercent / 100.0;
   double pipValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE) *
                     (SymbolInfoDouble(_Symbol, SYMBOL_POINT) * 10) /
                     SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lots = riskAmount / (slPips * pipValue);
   lots = NormalizeDouble(lots, 2);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   return MathMax(minLot, MathMin(lots, maxLot));
}

//+------------------------------------------------------------------+
//| Trailing stop + breakeven management (call from OnTick)          |
//+------------------------------------------------------------------+
// NOTE: Add a TrailManage() function that runs on every tick when
// a position is open. Check profit in pips:
//   - At +BreakevenPips: modify SL to entry + 0.5 pip
//   - At +TrailActivatePips: start trailing SL at TrailDistATR * ATR
// Use PositionGetDouble(POSITION_PRICE_OPEN) for entry price.
// Use trade.PositionModify() to update SL.
```

---

## INDICATOR MAPPING TABLE

| Python | MQL5 Function | Notes |
|---|---|---|
| `ema(close, N)` | `iMA(sym, tf, N, 0, MODE_EMA, PRICE_CLOSE)` | Native, exact match |
| `sma(close, N)` | `iMA(sym, tf, N, 0, MODE_SMA, PRICE_CLOSE)` | Native, exact match |
| `atr(h, l, c, N)` | `iATR(sym, tf, N)` | Native, exact match |
| `adx(h, l, c, N)` | `iADX(sym, tf, N)` | Buffer 0=ADX, 1=+DI, 2=-DI |
| `rsi(close, N)` | `iRSI(sym, tf, N, PRICE_CLOSE)` | Native, exact match |
| `macd(c, F, S, Sig)` | `iMACD(sym, tf, F, S, Sig, PRICE_CLOSE)` | Buffer 0=MACD, 1=Signal |
| `bollinger_bands(c, N, S)` | `iBands(sym, tf, N, 0, S, PRICE_CLOSE)` | Buffer 0=Mid, 1=Upper, 2=Lower |
| `keltner_channel()` | **CUSTOM** — `EMA(close,N) ± mult*ATR(N)` | No native KC in MQL5 |
| `squeeze_detector()` | **CUSTOM** — compare BB and KC bounds | ~20 lines of MQL5 |
| `atr_percentile()` | **CUSTOM** — sort ATR array, pick index | ~15 lines of MQL5 |

---

## WHAT'S ACHIEVABLE IN PURE MQL5 vs NEEDS PYTHON BRIDGE

### Pure MQL5 (no bridge needed):
- All indicator calculations (native + Keltner custom)
- Signal generation (all 5 layers)
- Order execution and management
- Breakeven and trailing logic
- Position sizing
- Session and time filters
- Basic optimization via Strategy Tester

### Needs Python Bridge (if you want ML lift):
- LSTM micro-prediction model (would need Python -> DLL or HTTP API)
- Optuna-style optimization (MT5 Tester uses genetic algo instead)
- Walk-forward analysis (can approximate with MT5 forward testing)
- Advanced Kelly criterion with full history (can simplify in MQL5)

### Recommendation:
**Build the core EA in pure MQL5 first.** The 5-layer strategy is fully
implementable without any Python bridge. Once it's running profitably,
THEN consider adding an ML boost via HTTP API to your H100.

---

## FIFO COMPLIANCE NOTES (FOREX.com US)

1. **One position per pair per direction.** The EA checks `PositionSelect(_Symbol)`
   before opening — if a position exists, skip.
2. **No hedging.** Never open both long and short on the same pair simultaneously.
3. **FIFO closing.** If you have multiple positions on a pair (shouldn't happen
   with this EA), MT5 closes the oldest first. Our EA prevents this by design.
4. **No OCO orders.** FOREX.com US doesn't support OCO natively — the EA manages
   SL/TP via `trade.Buy()` / `trade.Sell()` with explicit SL/TP prices.

---

## ESTIMATED IMPLEMENTATION TIME

| Component | MQL5 Lines | Hours |
|---|---|---|
| Input parameters + globals | ~60 | 0.5 |
| OnInit() — indicator handles | ~30 | 0.5 |
| OnTick() — Layer 1-3 signals | ~120 | 2-3 |
| Keltner + Squeeze custom | ~50 | 1 |
| ATR percentile custom | ~30 | 0.5 |
| Position sizing | ~25 | 0.5 |
| Trailing + breakeven | ~60 | 1-2 |
| Session + cooldown filters | ~30 | 0.5 |
| **TOTAL** | **~405** | **6-9 hours** |
