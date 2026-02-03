"""
Intel Reports API
Serves research reports and intel briefs to the dashboard
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import glob
import re
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Report directories
REPORT_DIRS = [
    "/home/jbot/trading_ai/neo/reports",
    "/home/jbot/trading_ai/claudia/research",
    "/home/jbot/trading_ai/reports",
]

# Asset detection patterns
ASSET_PATTERNS = {
    "XAUUSD": ["GOLD", "XAUUSD", "XAU"],
    "MGC": ["MGC"],
    "IREN": ["IREN"],
    "CLSK": ["CLSK", "CLEANSPARK"],
    "CIFR": ["CIFR", "CIPHER"],
    "BTC": ["BTC", "BITCOIN", "CRYPTO", "MINER"],
}

def detect_asset(filename, content=None):
    """Detect which asset a report is about"""
    text_to_check = filename.upper()
    if content:
        # Check first 500 chars for asset mentions
        text_to_check += " " + content[:500].upper()
    
    for asset, keywords in ASSET_PATTERNS.items():
        for keyword in keywords:
            if keyword in text_to_check:
                return asset
    return None

def get_all_reports():
    """Get all markdown reports from report directories"""
    reports = []
    
    for report_dir in REPORT_DIRS:
        if not os.path.exists(report_dir):
            continue
            
        # Find all markdown files
        for filepath in glob.glob(os.path.join(report_dir, "*.md")):
            filename = os.path.basename(filepath)
            stat = os.stat(filepath)
            
            # Read first bit of content for asset detection
            try:
                with open(filepath, 'r') as f:
                    preview = f.read(500)
            except:
                preview = ""
            
            # Determine report type based on filename/path
            report_type = "general"
            if "DAILY_BRIEF" in filename.upper():
                report_type = "quicklook"
            elif "WEEKLY_INTEL" in filename.upper() or "INTEL_BRIEF" in filename.upper():
                report_type = "intel"
            elif "INTEL" in filename.upper() or "BRIEF" in filename.upper():
                report_type = "intel"
            elif "IREN" in filename.upper():
                report_type = "miners"
            elif "MINER" in filename.upper() or "CLSK" in filename.upper() or "CIFR" in filename.upper():
                report_type = "miners"
            elif "BREAKOUT" in filename.upper():
                report_type = "breakout"
            elif "RESEARCH" in filename.upper():
                report_type = "research"
            
            # Detect asset
            asset = detect_asset(filename, preview)
            if not asset:
                # Daily briefs default to gold unless specified
                if report_type == "quicklook":
                    asset = "XAUUSD"
            
            # Extract title from filename
            title = filename.replace("_", " ").replace(".md", "")
            
            # Extract date from filename if present (YYYYMMDD pattern)
            date_match = re.search(r'(\d{8})', filename)
            report_date = None
            if date_match:
                try:
                    report_date = datetime.strptime(date_match.group(1), "%Y%m%d").isoformat()
                except:
                    pass
            
            reports.append({
                "filename": filename,
                "filepath": filepath,
                "title": title,
                "type": report_type,
                "asset": asset,
                "report_date": report_date,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size": stat.st_size,
                "directory": os.path.basename(report_dir)
            })
    
    # Sort by modified date (newest first)
    reports.sort(key=lambda x: x["modified"], reverse=True)
    return reports

def read_report(filename):
    """Read a specific report by filename"""
    for report_dir in REPORT_DIRS:
        filepath = os.path.join(report_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                content = f.read()
            return {
                "filename": filename,
                "content": content,
                "modified": datetime.fromtimestamp(os.stat(filepath).st_mtime).isoformat()
            }
    return None

@app.route("/api/intel/reports", methods=["GET"])
def list_reports():
    """List all available reports"""
    reports = get_all_reports()
    return jsonify({
        "reports": reports,
        "count": len(reports),
        "timestamp": datetime.now().isoformat()
    })

@app.route("/api/intel/reports/<filename>", methods=["GET"])
def get_report(filename):
    """Get a specific report by filename"""
    report = read_report(filename)
    if report:
        return jsonify(report)
    return jsonify({"error": "Report not found"}), 404

@app.route("/api/intel/latest", methods=["GET"])
def get_latest():
    """Get the latest intel brief"""
    reports = get_all_reports()
    intel_reports = [r for r in reports if r["type"] == "intel"]
    
    if intel_reports:
        latest = intel_reports[0]
        report = read_report(latest["filename"])
        return jsonify(report)
    
    return jsonify({"error": "No intel reports found"}), 404

@app.route("/api/intel/quicklook", methods=["POST"])
def save_quicklook():
    """
    Save a War Room analysis as a Commander's Quicklook report.
    
    Expected JSON:
    {
        "title": "Analysis Title",
        "asset": "XAUUSD",  # or MGC, IREN, etc.
        "content": "Analysis content...",
        "recommendation": "BUY",  # or SELL, HOLD
        "defcon": 3,
        "key_levels": {"support": 4800, "resistance": 4950},
        "commander_note": "Optional personal note"
    }
    """
    data = request.json
    
    if not data or not data.get("content"):
        return jsonify({"error": "Content is required"}), 400
    
    # Generate filename
    timestamp = datetime.now()
    date_str = timestamp.strftime("%Y%m%d_%H%M")
    asset = data.get("asset", "XAUUSD").upper()
    filename = f"QUICKLOOK_{asset}_{date_str}.md"
    
    # Build markdown content
    md_content = f"""# COMMANDER'S QUICKLOOK
## {asset} - {timestamp.strftime('%B %d, %Y %H:%M')}

---

## ⚡ QUICK ASSESSMENT

**Asset:** {asset}
**DEFCON Level:** {data.get('defcon', 3)}
**Recommendation:** {data.get('recommendation', 'N/A').upper()}

---

## 📊 ANALYSIS

{data.get('content', '')}

"""
    
    # Add key levels if provided
    if data.get('key_levels'):
        levels = data['key_levels']
        md_content += """
---

## 🎯 KEY LEVELS

"""
        for level_name, level_value in levels.items():
            md_content += f"- **{level_name.title()}:** ${level_value}\n"
    
    # Add commander's note if provided
    if data.get('commander_note'):
        md_content += f"""
---

## 📝 COMMANDER'S NOTE

> {data['commander_note']}
"""
    
    # Add footer
    md_content += f"""
---

*Generated: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}*
*Source: War Room Analysis*
"""
    
    # Save to reports directory
    filepath = os.path.join(REPORT_DIRS[0], filename)  # Save to neo/reports
    
    with open(filepath, 'w') as f:
        f.write(md_content)
    
    return jsonify({
        "status": "saved",
        "filename": filename,
        "filepath": filepath,
        "asset": asset
    })


@app.route("/api/intel/quicklook/from-agents", methods=["POST"])
def save_from_agents():
    """
    Save a multi-agent War Room analysis as a Quicklook report.
    This endpoint takes the raw agent analysis response and formats it nicely.
    """
    data = request.json
    
    if not data:
        return jsonify({"error": "Data is required"}), 400
    
    timestamp = datetime.now()
    date_str = timestamp.strftime("%Y%m%d_%H%M")
    
    # Extract data from agent response
    commander = data.get("commander_synthesis", {})
    agents = data.get("agents", [])
    defcon = data.get("defcon", 3)
    
    # Get asset from commander or default
    asset = "XAUUSD"
    if "symbol" in data:
        asset = data["symbol"]
    elif "MGC" in str(commander.get("action", "")):
        asset = "MGC"
    
    filename = f"QUICKLOOK_{asset}_{date_str}.md"
    
    # Build markdown
    md_content = f"""# COMMANDER'S QUICKLOOK
## {asset} Analysis - {timestamp.strftime('%B %d, %Y %H:%M')}

---

## ⚡ COMMANDER DIRECTIVE

**DEFCON Level:** {defcon}
**Primary Action:** {commander.get('action', 'N/A')}
**Confidence:** {commander.get('confidence', 'N/A')}%
**Reasoning:** {commander.get('reasoning', 'N/A')}

### Key Levels
"""
    
    # Add key levels from commander
    if commander.get('key_levels'):
        for level in commander['key_levels']:
            md_content += f"- {level}\n"
    
    # Add urgency flags
    if commander.get('urgency_flags'):
        md_content += f"""
### ⚠️ Urgency Flags
"""
        for flag in commander['urgency_flags']:
            md_content += f"- {flag}\n"
    
    # Add agent reports
    if agents:
        md_content += """
---

## 🤖 AGENT REPORTS

"""
        for agent in agents:
            name = agent.get('agent', 'Unknown').upper()
            rec = agent.get('recommendation', 'N/A')
            conf = agent.get('confidence', 'N/A')
            analysis = agent.get('analysis', agent.get('raw_response', 'No analysis'))[:500]
            
            md_content += f"""### {name}
**Recommendation:** {rec} | **Confidence:** {conf}%

{analysis}

"""
    
    md_content += f"""
---

*Generated: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}*
*Source: Multi-Agent War Room Analysis*
"""
    
    # Save
    filepath = os.path.join(REPORT_DIRS[0], filename)
    with open(filepath, 'w') as f:
        f.write(md_content)
    
    return jsonify({
        "status": "saved",
        "filename": filename,
        "filepath": filepath,
        "asset": asset
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "intel-reports-api"})

if __name__ == "__main__":
    print("Starting Intel Reports API on port 8455...")
    app.run(host="0.0.0.0", port=8455, debug=False)
