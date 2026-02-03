#!/usr/bin/env python3
"""
CRELLA COMMANDER
================
Send signals/commands to Crella trading bots on the desktop.

Two modes:
1. Direct file write (for local MT5)
2. Network share write (for remote MT5)

Usage:
  python crella_commander.py --action HOLD
  python crella_commander.py --action BUY --conviction 75
  python crella_commander.py --halt
  python crella_commander.py --resume
  python crella_commander.py --status
"""

import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import os

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Local MT5 (Wine on H100)
LOCAL_MT5_FILES = Path("/home/jbot/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files")

# Windows Desktop MT5 (Crella's machine) - via Tailscale/network share
# This is the path on the WINDOWS machine: C:\Users\Gringot\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files
# Mounted or accessed via SMB share
DESKTOP_MT5_FILES = Path("/mnt/crella_mt5")  # Mount point for Windows share

# Alternative: Direct Tailscale path if share is set up
TAILSCALE_SHARE = Path("//100.119.161.65/mt5_files")

# Signal file names
EA_SIGNAL_FILE = "ea_signal.json"
DEFCON_FILE = "defcon_state.json"
CONTROL_FILE = "aiiq_control.json"

# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════

def create_ea_signal(
    direction: str = "HOLD",
    conviction: int = 50,
    defcon: int = 3,
    symbol: str = "XAUUSD",
    tp: float = 0,
    sl: float = 0,
    pause_longs: bool = False,
    pause_shorts: bool = False,
    lot_multiplier: float = 1.0,
    close_partial: int = 0,
    reasoning: str = ""
) -> dict:
    """Create a standard EA signal"""
    
    defcon_colors = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "BLACK"}
    
    return {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "direction": direction.upper(),
        "conviction": conviction,
        "defcon": defcon,
        "defcon_color": defcon_colors.get(defcon, "ORANGE"),
        "action": f"{direction.upper()} signal with {conviction}% conviction",
        
        "targets": {
            "tp": tp,
            "sl": sl,
            "hunt_zone": 0
        },
        
        "ea_instructions": {
            "pause_longs": pause_longs,
            "pause_shorts": pause_shorts,
            "reduce_lot_multiplier": lot_multiplier,
            "tighten_sl_pips": 0,
            "max_drawdown_override": 0,
            "close_partial": close_partial,
            "set_breakeven": False,
            "consider_hedge": False
        },
        
        "reasoning": reasoning,
        "valid_until": (datetime.now() + timedelta(hours=1)).isoformat(),
        "source": "crella_commander"
    }


def create_halt_signal() -> dict:
    """Create emergency halt signal"""
    return create_ea_signal(
        direction="HOLD",
        conviction=0,
        defcon=5,
        pause_longs=True,
        pause_shorts=True,
        lot_multiplier=0,
        reasoning="EMERGENCY HALT - Commander override"
    )


def create_resume_signal() -> dict:
    """Create resume trading signal"""
    return create_ea_signal(
        direction="HOLD",
        conviction=50,
        defcon=3,
        pause_longs=False,
        pause_shorts=False,
        lot_multiplier=1.0,
        reasoning="Trading resumed - Commander override"
    )


# ══════════════════════════════════════════════════════════════════════════════
# FILE OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def atomic_write(path: Path, content: str):
    """Write atomically"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


def write_signal(signal: dict, target: str = "local"):
    """Write signal to MT5 files directory"""
    
    if target == "local":
        base = LOCAL_MT5_FILES
    elif target == "network":
        base = NETWORK_SHARE
    else:
        base = Path(target)
    
    base.mkdir(parents=True, exist_ok=True)
    
    # Write EA signal
    signal_path = base / EA_SIGNAL_FILE
    atomic_write(signal_path, json.dumps(signal, indent=2))
    print(f"✅ Signal written: {signal_path}")
    
    # Also update control.json if it exists
    control_path = base / CONTROL_FILE
    if control_path.exists() or target == "local":
        control = {
            "disable_new_entries": signal["ea_instructions"]["pause_longs"] and signal["ea_instructions"]["pause_shorts"],
            "dca_step_multiplier": 1.0 if signal["ea_instructions"]["reduce_lot_multiplier"] > 0 else 2.0,
            "max_layers_cap": 3 if signal["defcon"] <= 3 else 1,
            "source": "crella_commander",
            "timestamp": datetime.now().isoformat()
        }
        atomic_write(control_path, json.dumps(control, indent=2))
        print(f"✅ Control written: {control_path}")
    
    return signal_path


def read_current_signal(target: str = "local") -> dict:
    """Read current signal"""
    if target == "local":
        base = LOCAL_MT5_FILES
    else:
        base = NETWORK_SHARE
    
    signal_path = base / EA_SIGNAL_FILE
    
    if signal_path.exists():
        return json.loads(signal_path.read_text(encoding="utf-8"))
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Send commands to Crella trading bots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python crella_commander.py --status
  python crella_commander.py --action BUY --conviction 75
  python crella_commander.py --action SELL --conviction 60 --reason "Bearish divergence"
  python crella_commander.py --halt
  python crella_commander.py --resume
  python crella_commander.py --defcon 4
        """
    )
    
    parser.add_argument("--action", "-a", choices=["BUY", "SELL", "HOLD"], help="Trading direction")
    parser.add_argument("--conviction", "-c", type=int, default=50, help="Conviction 0-100")
    parser.add_argument("--defcon", "-d", type=int, choices=[1,2,3,4,5], default=3, help="DEFCON level (1=green, 5=black)")
    parser.add_argument("--reason", "-r", type=str, default="", help="Reasoning for the signal")
    parser.add_argument("--halt", action="store_true", help="Emergency halt all trading")
    parser.add_argument("--resume", action="store_true", help="Resume normal trading")
    parser.add_argument("--status", "-s", action="store_true", help="Show current signal status")
    parser.add_argument("--target", "-t", default="local", help="Target: local, network, or path")
    parser.add_argument("--pause-longs", action="store_true", help="Pause long entries")
    parser.add_argument("--pause-shorts", action="store_true", help="Pause short entries")
    parser.add_argument("--lot-mult", type=float, default=1.0, help="Lot multiplier (0.5 = half size)")
    
    args = parser.parse_args()
    
    print("="*60)
    print("🤖 CRELLA COMMANDER")
    print("="*60)
    
    if args.status:
        signal = read_current_signal(args.target)
        if signal:
            print(f"Current Signal:")
            print(f"  Direction:  {signal.get('direction', 'N/A')}")
            print(f"  Conviction: {signal.get('conviction', 'N/A')}%")
            print(f"  DEFCON:     {signal.get('defcon', 'N/A')} ({signal.get('defcon_color', 'N/A')})")
            print(f"  Timestamp:  {signal.get('timestamp', 'N/A')}")
            print(f"  Valid:      {signal.get('valid_until', 'N/A')}")
            instr = signal.get('ea_instructions', {})
            print(f"  Pause Long: {instr.get('pause_longs', False)}")
            print(f"  Pause Short:{instr.get('pause_shorts', False)}")
            print(f"  Lot Mult:   {instr.get('reduce_lot_multiplier', 1.0)}")
        else:
            print("No current signal found")
        return
    
    if args.halt:
        print("🛑 SENDING HALT SIGNAL")
        signal = create_halt_signal()
        write_signal(signal, args.target)
        print("\n⚠️ ALL TRADING HALTED")
        return
    
    if args.resume:
        print("▶️ SENDING RESUME SIGNAL")
        signal = create_resume_signal()
        write_signal(signal, args.target)
        print("\n✅ TRADING RESUMED")
        return
    
    if args.action:
        print(f"📡 SENDING {args.action} SIGNAL")
        signal = create_ea_signal(
            direction=args.action,
            conviction=args.conviction,
            defcon=args.defcon,
            pause_longs=args.pause_longs,
            pause_shorts=args.pause_shorts,
            lot_multiplier=args.lot_mult,
            reasoning=args.reason or f"{args.action} signal from commander"
        )
        write_signal(signal, args.target)
        print(f"\n✅ Signal sent: {args.action} @ {args.conviction}% conviction")
        return
    
    # Interactive mode
    print("Interactive mode. Commands:")
    print("  b <conviction> - BUY signal")
    print("  s <conviction> - SELL signal")
    print("  h              - HOLD signal")
    print("  halt           - Emergency halt")
    print("  resume         - Resume trading")
    print("  d <1-5>        - Set DEFCON level")
    print("  q              - Quit")
    print()
    
    while True:
        try:
            cmd = input("crella> ").strip().lower()
            
            if cmd == "q" or cmd == "quit":
                break
            elif cmd == "halt":
                write_signal(create_halt_signal(), args.target)
                print("🛑 HALTED")
            elif cmd == "resume":
                write_signal(create_resume_signal(), args.target)
                print("▶️ RESUMED")
            elif cmd.startswith("b"):
                parts = cmd.split()
                conv = int(parts[1]) if len(parts) > 1 else 60
                write_signal(create_ea_signal("BUY", conv), args.target)
                print(f"📈 BUY @ {conv}%")
            elif cmd.startswith("s"):
                parts = cmd.split()
                conv = int(parts[1]) if len(parts) > 1 else 60
                write_signal(create_ea_signal("SELL", conv), args.target)
                print(f"📉 SELL @ {conv}%")
            elif cmd == "h":
                write_signal(create_ea_signal("HOLD", 50), args.target)
                print("⏸️ HOLD")
            elif cmd.startswith("d"):
                parts = cmd.split()
                defcon = int(parts[1]) if len(parts) > 1 else 3
                write_signal(create_ea_signal("HOLD", 50, defcon=defcon), args.target)
                print(f"🚦 DEFCON {defcon}")
            else:
                print("Unknown command")
                
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
