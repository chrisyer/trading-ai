#!/usr/bin/env python3
"""
IBKR Ghost Commander API
Serves real-time IBKR MGC data for Ghost Commander dashboard
"""

import nest_asyncio
nest_asyncio.apply()

from flask import Flask, jsonify
from flask_cors import CORS
from ib_insync import IB, Future
from datetime import datetime
import time

app = Flask(__name__)
CORS(app)

IBKR_HOST = "100.119.161.65"
IBKR_PORT = 7497

# Cache
_cache = {"data": None, "time": 0}


def get_ibkr_data():
    """Fetch data from IBKR with caching"""
    if time.time() - _cache["time"] < 3 and _cache["data"]:
        return _cache["data"]
    
    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=99, timeout=10)
        
        mgc = Future(symbol='MGC', exchange='COMEX', lastTradeDateOrContractMonth='202604')
        ib.qualifyContracts(mgc)
        
        ib.reqMarketDataType(4)
        ticker = ib.reqMktData(mgc)
        ib.sleep(2)
        
        positions = ib.positions()
        mgc_pos = next((p for p in positions if p.contract.symbol == 'MGC'), None)
        
        account = {item.tag: float(item.value) for item in ib.accountSummary() 
                   if item.tag in ['NetLiquidation', 'AvailableFunds', 'MaintMarginReq']}
        
        price = ticker.last or ticker.close or 0
        
        if mgc_pos and price:
            qty = int(mgc_pos.position)
            avg = mgc_pos.avgCost / 10
            pnl = (price - avg) * 10 * qty
            
            result = {
                "mode": "IBKR_LIVE",
                "connected": True,
                "last_update": datetime.now().strftime("%H:%M:%S"),
                "net_position": qty,
                "long_positions": 1,
                "long_contracts": qty,
                "short_positions": 0,
                "short_contracts": 0,
                "buy_levels_armed": 0,
                "entry_price": round(avg, 2),
                "current_price": round(price, 2),
                "bid": ticker.bid,
                "ask": ticker.ask,
                "unrealized_pnl": round(pnl, 2),
                "tp_target": round(avg + 50, 2),
                "account_balance": round(account.get('NetLiquidation', 0), 2),
                "account_equity": round(account.get('NetLiquidation', 0), 2),
                "margin": round(account.get('MaintMarginReq', 0), 2),
                "free_margin": round(account.get('AvailableFunds', 0), 2),
                "margin_level_pct": round(account.get('NetLiquidation', 0) / max(account.get('MaintMarginReq', 1), 1) * 100, 2),
                "freeroll_status": "BUILDING",
                "strategy": "IBKR_GHOST"
            }
            _cache["data"] = result
            _cache["time"] = time.time()
        else:
            result = {"connected": False, "error": "No position"}
        
        ib.cancelMktData(mgc)
        ib.disconnect()
        return result
        
    except Exception as e:
        try:
            ib.disconnect()
        except:
            pass
        return {"connected": False, "error": str(e)}


@app.route('/')
def root():
    return jsonify({"status": "IBKR Ghost API", "port": 8456})

@app.route('/api/ghost/state')
@app.route('/api/mgc/state')
def get_state():
    return jsonify(get_ibkr_data())


if __name__ == "__main__":
    print("IBKR Ghost API starting on port 8456...")
    app.run(host="0.0.0.0", port=8456, threaded=False)
