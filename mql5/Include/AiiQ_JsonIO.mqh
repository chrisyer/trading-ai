//+------------------------------------------------------------------+
//| AiiQ_JsonIO.mqh  - minimal JSON helpers for MT5                   |
//| Writes state.json, reads control.json (simple key/value)          |
//+------------------------------------------------------------------+
#property strict

string JsonEscape(const string s)
{
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\n", "\\n");
   StringReplace(r, "\r", "\\r");
   StringReplace(r, "\t", "\\t");
   return r;
}

string StringTrim(const string s)
{
   string r = s;
   StringTrimLeft(r);
   StringTrimRight(r);
   return r;
}

bool WriteTextFile(const string relPath, const string content)
{
   int h = FileOpen(relPath, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   FileWriteString(h, content);
   FileClose(h);
   return true;
}

bool ReadTextFile(const string relPath, string &out)
{
   int h = FileOpen(relPath, FILE_READ|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   out = "";
   while(!FileIsEnding(h))
      out += FileReadString(h) + "\n";
   FileClose(h);
   return true;
}

// naive JSON number extraction for key: "k": 1.23 or "k": true/false
bool JsonGetNumber(const string json, const string key, double &val)
{
   string k = "\"" + key + "\"";
   int p = StringFind(json, k);
   if(p < 0) return false;
   p = StringFind(json, ":", p);
   if(p < 0) return false;

   // skip spaces
   p++;
   while(p < (int)StringLen(json) && (StringGetCharacter(json, p) == ' ' || StringGetCharacter(json, p) == '\t'))
      p++;

   // read token until comma/brace/newline
   int e = p;
   while(e < (int)StringLen(json))
   {
      ushort c = StringGetCharacter(json, e);
      if(c==',' || c=='}' || c=='\n' || c=='\r') break;
      e++;
   }
   string token = StringSubstr(json, p, e-p);
   token = StringTrim(token);

   // handle true/false
   if(StringCompare(token, "true", false)==0) { val = 1.0; return true; }
   if(StringCompare(token, "false", false)==0){ val = 0.0; return true; }

   val = StringToDouble(token);
   return true;
}

bool JsonGetString(const string json, const string key, string &val)
{
   string k = "\"" + key + "\"";
   int p = StringFind(json, k);
   if(p < 0) return false;
   p = StringFind(json, ":", p);
   if(p < 0) return false;
   p++;

   // find first quote
   while(p < (int)StringLen(json) && StringGetCharacter(json, p) != '\"') p++;
   if(p >= (int)StringLen(json)) return false;
   p++;
   int e = p;
   while(e < (int)StringLen(json) && StringGetCharacter(json, e) != '\"') e++;
   if(e >= (int)StringLen(json)) return false;

   val = StringSubstr(json, p, e-p);
   return true;
}
//+------------------------------------------------------------------+
