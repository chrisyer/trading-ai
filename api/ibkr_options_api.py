#!/usr/bin/env python3
"""
IBKR Options API - Serves BTC Miner options positions
PM2 managed service for Ghost Commander dashboard
"""

import nest_asyncio
nest_asyncio.apply()

from flask import Flask, jsonify, Response
from flask_cors import CORS
from ib_insync import IB, Option
from datetime import datetime
import time
import math
import json
import yfinance as yf

app = Flask(__name__)
CORS(app)


def clean_nan(obj):
    """Recursively replace NaN/Inf with None for JSON serialization"""
    if isinstance(obj, dict):
        return {k: clean_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nan(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def safe_jsonify(data):
    """JSON response that handles NaN values"""
    cleaned = clean_nan(data)
    return Response(
        json.dumps(cleaned),
        mimetype='application/json'
    )

IBKR_HOST = "100.119.161.65"
IBKR_PORT = 7497

# Cache to prevent excessive API calls
_cache = {"data": None, "time": 0, "ttl": 5}

def get_stock_price(symbol):
    """Get current stock price from yfinance"""
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period='1d')
        if not data.empty:
            return data['Close'].iloc[-1]
    except:
        pass
    return 0

def fetch_options_positions():
    """Fetch all BTC miner options from IBKR"""
    
    # Check cache
    if time.time() - _cache["time"] < _cache["ttl"] and _cache["data"]:
        return _cache["data"]
    
    ib = IB()
    
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=85, timeout=15)
        
        positions = ib.positions()
        
        # Filter for options only
        options_data = {
            "IREN": [],
            "CLSK": [],
            "CIFR": []
        }
        
        btc_miners = ["IREN", "CLSK", "CIFR"]
        
        for pos in positions:
            contract = pos.contract
            if contract.secType == 'OPT' and contract.symbol in btc_miners:
                symbol = contract.symbol
                qty = int(pos.position)
                avg_cost = pos.avgCost / 100  # Per share
                
                # Set exchange for market data request
                contract.exchange = 'SMART'
                
                # Get current option price (use delayed data)
                ib.reqMarketDataType(4)  # 4 = delayed frozen data
                ticker = ib.reqMktData(contract, genericTickList='', snapshot=False, regulatorySnapshot=False)
                ib.sleep(2)  # Give more time for data
                
                # Try multiple price sources
                current_price = None
                if ticker.last and ticker.last > 0:
                    current_price = ticker.last
                elif ticker.close and ticker.close > 0:
                    current_price = ticker.close
                elif ticker.bid and ticker.ask and ticker.bid > 0 and ticker.ask > 0:
                    current_price = (ticker.bid + ticker.ask) / 2  # Midpoint
                elif ticker.modelGreeks and ticker.modelGreeks.optPrice:
                    current_price = ticker.modelGreeks.optPrice
                else:
                    current_price = avg_cost  # Fallback to cost basis
                
                bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
                ask = ticker.ask if ticker.ask and ticker.ask > 0 else None
                
                # Calculate P&L
                if current_price and avg_cost:
                    pnl = (current_price - avg_cost) * qty * 100
                    pnl_pct = ((current_price / avg_cost) - 1) * 100 if avg_cost > 0 else 0
                    total_value = current_price * qty * 100
                else:
                    pnl = None
                    pnl_pct = None
                    total_value = None
                
                option_info = {
                    "symbol": symbol,
                    "strike": contract.strike,
                    "expiry": contract.lastTradeDateOrContractMonth,
                    "type": "CALL" if contract.right == 'C' else "PUT",
                    "quantity": qty,
                    "avg_cost": round(avg_cost, 2) if avg_cost else 0,
                    "current_price": round(current_price, 2) if current_price else None,
                    "bid": round(bid, 2) if bid else None,
                    "ask": round(ask, 2) if ask else None,
                    "pnl": round(pnl, 2) if pnl is not None else None,
                    "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                    "total_value": round(total_value, 2) if total_value else None,
                    "total_cost": round(avg_cost * qty * 100, 2) if avg_cost else 0
                }
                
                options_data[symbol].append(option_info)
                ib.cancelMktData(contract)
        
        # Get stock prices
        stock_prices = {}
        for sym in btc_miners:
            stock_prices[sym] = get_stock_price(sym)
        
        # Get open orders (TP orders)
        open_orders = []
        for trade in ib.openTrades():
            contract = trade.contract
            if contract.secType == 'OPT' and contract.symbol in btc_miners:
                order = trade.order
                open_orders.append({
                    "symbol": contract.symbol,
                    "strike": contract.strike,
                    "expiry": contract.lastTradeDateOrContractMonth,
                    "action": order.action,
                    "quantity": int(order.totalQuantity),
                    "limit_price": order.lmtPrice,
                    "status": trade.orderStatus.status
                })
        
        result = {
            "connected": True,
            "last_update": datetime.now().strftime("%H:%M:%S"),
            "timestamp": datetime.now().isoformat(),
            "positions": options_data,
            "stock_prices": stock_prices,
            "open_orders": open_orders
        }
        
        # Update cache
        _cache["data"] = result
        _cache["time"] = time.time()
        
        ib.disconnect()
        return result
        
    except Exception as e:
        try:
            ib.disconnect()
        except:
            pass
        return {
            "connected": False,
            "error": str(e),
            "last_update": datetime.now().strftime("%H:%M:%S"),
            "positions": {"IREN": [], "CLSK": [], "CIFR": []},
            "stock_prices": {},
            "open_orders": []
        }


@app.route('/')
def home():
    return safe_jsonify({
        "service": "IBKR Options API",
        "endpoints": ["/api/options/all", "/api/options/iren", "/api/options/clsk", "/api/options/cifr"]
    })


@app.route('/api/options/all')
def get_all_options():
    """Get all BTC miner options positions"""
    return safe_jsonify(fetch_options_positions())


@app.route('/api/options/iren')
def get_iren_options():
    """Get IREN options positions"""
    data = fetch_options_positions()
    stock_price = data.get("stock_prices", {}).get("IREN", 0)
    positions = data.get("positions", {}).get("IREN", [])
    orders = [o for o in data.get("open_orders", []) if o["symbol"] == "IREN"]
    
    total_contracts = sum(p["quantity"] for p in positions)
    total_cost = sum(p["total_cost"] for p in positions)
    total_value = sum(p["total_value"] for p in positions)
    total_pnl = sum(p["pnl"] for p in positions)
    
    return safe_jsonify({
        "symbol": "IREN",
        "connected": data.get("connected", False),
        "last_update": data.get("last_update"),
        "stock_price": round(stock_price, 2) if stock_price else 0,
        "positions": positions,
        "open_orders": orders,
        "summary": {
            "total_contracts": total_contracts,
            "total_cost": round(total_cost, 2) if total_cost else 0,
            "total_value": round(total_value, 2) if total_value else 0,
            "total_pnl": round(total_pnl, 2) if total_pnl else 0,
            "pnl_pct": round((total_pnl / total_cost * 100) if total_cost and total_cost > 0 else 0, 2)
        }
    })


@app.route('/api/options/clsk')
def get_clsk_options():
    """Get CLSK options positions"""
    data = fetch_options_positions()
    stock_price = data.get("stock_prices", {}).get("CLSK", 0)
    positions = data.get("positions", {}).get("CLSK", [])
    orders = [o for o in data.get("open_orders", []) if o["symbol"] == "CLSK"]
    
    total_contracts = sum(p["quantity"] for p in positions)
    total_cost = sum(p["total_cost"] for p in positions)
    total_value = sum(p["total_value"] for p in positions)
    total_pnl = sum(p["pnl"] for p in positions)
    
    return safe_jsonify({
        "symbol": "CLSK",
        "connected": data.get("connected", False),
        "last_update": data.get("last_update"),
        "stock_price": round(stock_price, 2) if stock_price else 0,
        "positions": positions,
        "open_orders": orders,
        "summary": {
            "total_contracts": total_contracts,
            "total_cost": round(total_cost, 2) if total_cost else 0,
            "total_value": round(total_value, 2) if total_value else 0,
            "total_pnl": round(total_pnl, 2) if total_pnl else 0,
            "pnl_pct": round((total_pnl / total_cost * 100) if total_cost and total_cost > 0 else 0, 2)
        }
    })


@app.route('/api/options/cifr')
def get_cifr_options():
    """Get CIFR options positions"""
    data = fetch_options_positions()
    stock_price = data.get("stock_prices", {}).get("CIFR", 0)
    positions = data.get("positions", {}).get("CIFR", [])
    orders = [o for o in data.get("open_orders", []) if o["symbol"] == "CIFR"]
    
    total_contracts = sum(p["quantity"] for p in positions)
    total_cost = sum(p["total_cost"] for p in positions)
    total_value = sum(p["total_value"] for p in positions)
    total_pnl = sum(p["pnl"] for p in positions)
    
    return safe_jsonify({
        "symbol": "CIFR",
        "connected": data.get("connected", False),
        "last_update": data.get("last_update"),
        "stock_price": round(stock_price, 2) if stock_price else 0,
        "positions": positions,
        "open_orders": orders,
        "summary": {
            "total_contracts": total_contracts,
            "total_cost": round(total_cost, 2) if total_cost else 0,
            "total_value": round(total_value, 2) if total_value else 0,
            "total_pnl": round(total_pnl, 2) if total_pnl else 0,
            "pnl_pct": round((total_pnl / total_cost * 100) if total_cost and total_cost > 0 else 0, 2)
        }
    })


if __name__ == "__main__":
    print("IBKR Options API starting on port 8457...")
    app.run(host="0.0.0.0", port=8457, threaded=False)
