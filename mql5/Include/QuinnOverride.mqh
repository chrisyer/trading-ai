//+------------------------------------------------------------------+
//|                                               QuinnOverride.mqh  |
//|            H100 Command & Control Override for Desktop Bots      |
//|                   Quinn can TIGHTEN, never loosen                |
//+------------------------------------------------------------------+
#property copyright "AiiQ-tAIq Platform"
#property version   "1.00"

/*
INTEGRATION:
============
1. Copy this file to: MQL5/Include/QuinnOverride.mqh

2. Add to your EA at the top:
   #include <QuinnOverride.mqh>

3. Add inputs:
   input bool UseQuinnOverride = true;  // Allow H100 override
   
4. In OnInit():
   QuinnOverride_Init();

5. Before any trade logic:
   if(!QuinnOverride_CanTrade()) return;  // Blocked by Quinn
   
6. For position sizing:
   double lots = CalculateYourLots();
   lots = QuinnOverride_AdjustLots(lots);  // May reduce

7. For direction checks:
   if(QuinnOverride_BlockLongs()) { skip buy logic }
   if(QuinnOverride_BlockShorts()) { skip sell logic }

FILES READ:
===========
- aiiq_control.json   (bridge-style control)
- ea_signal.json      (NEO-style signal)
- defcon_state.json   (risk level)
*/

//=== SETTINGS ===
input group "=== QUINN H100 OVERRIDE ==="
input bool   InpQuinnEnabled     = true;          // Enable H100 override
input int    InpQuinnCheckSec    = 5;             // Check interval (seconds)
input string InpControlFile      = "aiiq_control.json";
input string InpSignalFile       = "ea_signal.json";
input string InpDefconFile       = "defcon_state.json";

//=== GLOBALS ===
datetime g_quinnLastCheck = 0;

// Override state
bool   g_quinnDisableAll     = false;
bool   g_quinnPauseLongs     = false;
bool   g_quinnPauseShorts    = false;
double g_quinnLotMultiplier  = 1.0;
int    g_quinnMaxLayers      = 99;
double g_quinnDcaMultiplier  = 1.0;
int    g_quinnDefcon         = 3;
string g_quinnRegime         = "unknown";

//+------------------------------------------------------------------+
//| Initialize                                                        |
//+------------------------------------------------------------------+
void QuinnOverride_Init()
{
   if(!InpQuinnEnabled) return;
   
   Print("════════════════════════════════════════════════════════════════");
   Print("🎮 QUINN OVERRIDE ENABLED");
   Print("════════════════════════════════════════════════════════════════");
   Print("   Control: ", InpControlFile);
   Print("   Signal:  ", InpSignalFile);
   Print("   Defcon:  ", InpDefconFile);
   Print("   Check:   Every ", InpQuinnCheckSec, " seconds");
   Print("════════════════════════════════════════════════════════════════");
   
   // Initial read
   QuinnOverride_Update();
}

//+------------------------------------------------------------------+
//| Update from files (call periodically)                            |
//+------------------------------------------------------------------+
void QuinnOverride_Update()
{
   if(!InpQuinnEnabled) return;
   
   datetime now = TimeCurrent();
   if(now - g_quinnLastCheck < InpQuinnCheckSec) return;
   g_quinnLastCheck = now;
   
   // Read control file (bridge-style)
   ReadControlFile();
   
   // Read signal file (NEO-style)
   ReadSignalFile();
   
   // Read defcon file
   ReadDefconFile();
}

//+------------------------------------------------------------------+
//| Read aiiq_control.json                                           |
//+------------------------------------------------------------------+
void ReadControlFile()
{
   string content = "";
   if(!ReadFile(InpControlFile, content)) return;
   
   // Parse disable_new_entries
   string disable = ExtractJson(content, "disable_new_entries");
   if(disable == "true") g_quinnDisableAll = true;
   else if(disable == "false") g_quinnDisableAll = false;
   
   // Parse dca_step_multiplier
   string dca = ExtractJson(content, "dca_step_multiplier");
   if(dca != "") g_quinnDcaMultiplier = MathMax(1.0, StringToDouble(dca));
   
   // Parse max_layers_cap
   string layers = ExtractJson(content, "max_layers_cap");
   if(layers != "") g_quinnMaxLayers = (int)StringToInteger(layers);
   
   // Parse regime
   string regime = ExtractJson(content, "regime");
   if(regime != "") g_quinnRegime = regime;
}

//+------------------------------------------------------------------+
//| Read ea_signal.json                                              |
//+------------------------------------------------------------------+
void ReadSignalFile()
{
   string content = "";
   if(!ReadFile(InpSignalFile, content)) return;
   
   // Parse ea_instructions
   string pauseLongs = ExtractJson(content, "pause_longs");
   if(pauseLongs == "true") g_quinnPauseLongs = true;
   else if(pauseLongs == "false") g_quinnPauseLongs = false;
   
   string pauseShorts = ExtractJson(content, "pause_shorts");
   if(pauseShorts == "true") g_quinnPauseShorts = true;
   else if(pauseShorts == "false") g_quinnPauseShorts = false;
   
   string lotMult = ExtractJson(content, "reduce_lot_multiplier");
   if(lotMult != "") g_quinnLotMultiplier = MathMin(1.0, StringToDouble(lotMult));
   
   // Parse defcon from signal
   string defcon = ExtractJson(content, "defcon");
   if(defcon != "") g_quinnDefcon = (int)StringToInteger(defcon);
}

//+------------------------------------------------------------------+
//| Read defcon_state.json                                           |
//+------------------------------------------------------------------+
void ReadDefconFile()
{
   string content = "";
   if(!ReadFile(InpDefconFile, content)) return;
   
   string level = ExtractJson(content, "level");
   if(level != "") g_quinnDefcon = (int)StringToInteger(level);
}

//+------------------------------------------------------------------+
//| Check if trading allowed                                         |
//+------------------------------------------------------------------+
bool QuinnOverride_CanTrade()
{
   if(!InpQuinnEnabled) return true;
   
   QuinnOverride_Update();
   
   // DEFCON 5 = full stop
   if(g_quinnDefcon >= 5) return false;
   
   // Explicit disable
   if(g_quinnDisableAll) return false;
   
   return true;
}

//+------------------------------------------------------------------+
//| Check if longs blocked                                           |
//+------------------------------------------------------------------+
bool QuinnOverride_BlockLongs()
{
   if(!InpQuinnEnabled) return false;
   return g_quinnPauseLongs || g_quinnDefcon >= 4;
}

//+------------------------------------------------------------------+
//| Check if shorts blocked                                          |
//+------------------------------------------------------------------+
bool QuinnOverride_BlockShorts()
{
   if(!InpQuinnEnabled) return false;
   return g_quinnPauseShorts || g_quinnDefcon >= 4;
}

//+------------------------------------------------------------------+
//| Adjust lot size (can only reduce, never increase)                |
//+------------------------------------------------------------------+
double QuinnOverride_AdjustLots(double lots)
{
   if(!InpQuinnEnabled) return lots;
   
   // Apply lot multiplier (0.0 to 1.0)
   double adjusted = lots * g_quinnLotMultiplier;
   
   // DEFCON scaling
   if(g_quinnDefcon == 4) adjusted *= 0.5;  // Half size at DEFCON 4
   if(g_quinnDefcon >= 5) adjusted = 0;     // No trading at DEFCON 5
   
   return adjusted;
}

//+------------------------------------------------------------------+
//| Get DCA multiplier (can only widen, never tighten)               |
//+------------------------------------------------------------------+
double QuinnOverride_DcaMultiplier()
{
   if(!InpQuinnEnabled) return 1.0;
   return g_quinnDcaMultiplier;  // >= 1.0 always
}

//+------------------------------------------------------------------+
//| Get max layers (can only reduce, never increase)                 |
//+------------------------------------------------------------------+
int QuinnOverride_MaxLayers(int defaultMax)
{
   if(!InpQuinnEnabled) return defaultMax;
   return MathMin(defaultMax, g_quinnMaxLayers);
}

//+------------------------------------------------------------------+
//| Get current DEFCON level                                         |
//+------------------------------------------------------------------+
int QuinnOverride_Defcon()
{
   return g_quinnDefcon;
}

//+------------------------------------------------------------------+
//| Get regime string                                                |
//+------------------------------------------------------------------+
string QuinnOverride_Regime()
{
   return g_quinnRegime;
}

//+------------------------------------------------------------------+
//| Display override status on chart                                 |
//+------------------------------------------------------------------+
void QuinnOverride_Display(int x = 10, int y = 50)
{
   if(!InpQuinnEnabled) return;
   
   string defconColors[] = {"", "clrLime", "clrYellow", "clrOrange", "clrRed", "clrBlack"};
   
   string status = "";
   if(g_quinnDisableAll) status = "🛑 HALTED";
   else if(g_quinnDefcon >= 4) status = "⚠️ RESTRICTED";
   else status = "✅ ACTIVE";
   
   Comment(
      "══════════════════════════════════════\n",
      "🎮 QUINN OVERRIDE: ", status, "\n",
      "══════════════════════════════════════\n",
      "DEFCON:      ", g_quinnDefcon, "\n",
      "Regime:      ", g_quinnRegime, "\n",
      "Block Longs: ", (g_quinnPauseLongs ? "YES" : "no"), "\n",
      "Block Shorts:", (g_quinnPauseShorts ? "YES" : "no"), "\n",
      "Lot Mult:    ", DoubleToString(g_quinnLotMultiplier, 2), "\n",
      "DCA Mult:    ", DoubleToString(g_quinnDcaMultiplier, 2), "\n",
      "Max Layers:  ", g_quinnMaxLayers, "\n",
      "══════════════════════════════════════"
   );
}

//+------------------------------------------------------------------+
//| UTILITY: Read file to string                                     |
//+------------------------------------------------------------------+
bool ReadFile(const string filename, string &content)
{
   content = "";
   
   if(!FileIsExist(filename, FILE_COMMON))
   {
      if(!FileIsExist(filename)) return false;
   }
   
   int handle = FileOpen(filename, FILE_READ|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      handle = FileOpen(filename, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(handle == INVALID_HANDLE) return false;
   }
   
   while(!FileIsEnding(handle))
   {
      content += FileReadString(handle) + "\n";
   }
   FileClose(handle);
   
   return StringLen(content) > 0;
}

//+------------------------------------------------------------------+
//| UTILITY: Extract JSON value (simple parser)                      |
//+------------------------------------------------------------------+
string ExtractJson(const string json, const string key)
{
   string searchKey = "\"" + key + "\"";
   int pos = StringFind(json, searchKey);
   if(pos < 0) return "";
   
   // Find the colon after the key
   int colonPos = StringFind(json, ":", pos);
   if(colonPos < 0) return "";
   
   // Find the value start
   int valueStart = colonPos + 1;
   while(valueStart < StringLen(json) && 
         (StringGetCharacter(json, valueStart) == ' ' || 
          StringGetCharacter(json, valueStart) == '\n' ||
          StringGetCharacter(json, valueStart) == '\r'))
   {
      valueStart++;
   }
   
   if(valueStart >= StringLen(json)) return "";
   
   // Check if it's a string value
   if(StringGetCharacter(json, valueStart) == '"')
   {
      int valueEnd = StringFind(json, "\"", valueStart + 1);
      if(valueEnd < 0) return "";
      return StringSubstr(json, valueStart + 1, valueEnd - valueStart - 1);
   }
   
   // Numeric or boolean value
   int valueEnd = valueStart;
   while(valueEnd < StringLen(json))
   {
      ushort c = StringGetCharacter(json, valueEnd);
      if(c == ',' || c == '}' || c == '\n' || c == '\r') break;
      valueEnd++;
   }
   
   string value = StringSubstr(json, valueStart, valueEnd - valueStart);
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}
//+------------------------------------------------------------------+
