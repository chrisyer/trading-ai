"""
Prepare fine-tuning dataset for LoRA Governor.
Converts CRELLA basket outcomes into instruction-tuning format
for dolphin-llama3:8b (or compatible model).
"""

import json
import logging
from pathlib import Path
from datetime import datetime
import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PREP-DATA] %(message)s")
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ML_DIR / "data" / "trading.duckdb"
OUTPUT_PATH = ML_DIR / "finetune" / "governor_training.jsonl"

SYSTEM_PROMPT = (
    "You are a risk governor for an automated XAUUSD trading system. "
    "Given current market state, decide: disable_new_entries (true/false), "
    "dca_step_multiplier (0.5-2.0), max_layers_cap (1-5), risk_bias (-1.0 to 1.0). "
    "Prioritize capital preservation. Tighten risk during high volatility. "
    "Only loosen when conditions are clearly favorable."
)


def build_instruction(state: dict) -> str:
    return (
        f"Market State:\n"
        f"- ATR: {state['atr']:.1f} (percentile: {state['atr_pctl']:.2f}, bucket: {state['atr_bucket']})\n"
        f"- Active layers: {state['layers']}, Direction: {'LONG' if state['direction'] > 0 else 'SHORT'}\n"
        f"- Spread: {state['spread_points']:.0f} points\n"
        f"- Sentiment: {state['sentiment_regime']} (intensity: {state['intensity']:.1f}, event_risk: {state['event_risk']})\n"
        f"- Equity: ${state['equity']:,.0f}, Net lots: {state['net_lots']:.2f}\n"
        f"\nWhat risk controls should be applied?"
    )


def build_response(control: dict, outcome: dict) -> str:
    was_profitable = outcome["pnl"] > 0
    pnl_str = f"${outcome['pnl']:,.0f}"
    dd_str = f"{outcome['dd']:.1f}%"

    response = (
        f'{{"disable_new_entries": {str(control["disable_entries"]).lower()}, '
        f'"dca_step_multiplier": {control["dca_mult"]:.1f}, '
        f'"max_layers_cap": {control["layer_cap"]}, '
        f'"risk_bias": 0.0}}\n\n'
        f"Reasoning: "
    )

    if was_profitable and outcome["dd"] < 2.0:
        response += f"Conservative approach preserved capital. Basket closed at {pnl_str} with only {dd_str} drawdown."
    elif was_profitable and outcome["dd"] >= 2.0:
        response += f"Position was profitable ({pnl_str}) but drawdown of {dd_str} was elevated. Consider tighter controls next time."
    elif not was_profitable and outcome["dd"] < 3.0:
        response += f"Loss of {pnl_str} with {dd_str} drawdown. Manageable but controls should be reviewed."
    else:
        response += f"Significant loss of {pnl_str} with {dd_str} drawdown. Controls were insufficient — tighten for similar conditions."

    return response


def prepare():
    log.info("Preparing LoRA fine-tuning dataset...")

    if not DB_PATH.exists():
        log.error("DuckDB not found. Run data ingest first.")
        return

    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = con.execute("""
        WITH ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (PARTITION BY basket_id ORDER BY ts DESC) as rn
            FROM basket_outcomes
            WHERE pnl IS NOT NULL AND basket_id > 0
        )
        SELECT
            basket_id, atr, atr_pctl,
            COALESCE(atr_bucket, 'unknown') as atr_bucket,
            layers, direction, spread_points, equity, net_lots,
            COALESCE(sentiment_regime, 'unknown') as sentiment_regime,
            COALESCE(event_risk, 'unknown') as event_risk,
            intensity,
            control_disable_entries, control_dca_mult, control_layer_cap,
            pnl, dd, duration_min, max_layers
        FROM ranked
        WHERE rn = 1
        ORDER BY basket_id
    """).fetchall()
    con.close()

    log.info(f"Found {len(rows)} unique baskets")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with open(OUTPUT_PATH, "w") as f:
        for r in rows:
            state = {
                "atr": r[1], "atr_pctl": r[2], "atr_bucket": r[3],
                "layers": r[4], "direction": r[5], "spread_points": r[6],
                "equity": r[7], "net_lots": r[8],
                "sentiment_regime": r[9], "event_risk": r[10], "intensity": r[11],
            }
            control = {
                "disable_entries": r[12], "dca_mult": r[13], "layer_cap": r[14],
            }
            outcome = {
                "pnl": r[15], "dd": r[16], "duration_min": r[17], "max_layers": r[18],
            }

            instruction = build_instruction(state)
            response = build_response(control, outcome)

            record = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": response},
                ]
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    log.info(f"Wrote {count} training examples to {OUTPUT_PATH}")
    return count


if __name__ == "__main__":
    prepare()
