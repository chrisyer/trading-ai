#!/usr/bin/env python3
"""
MT5 to IBKR Trade Mirror API
Receives trade signals from MT5 and executes on IBKR

Endpoint: POST /api/trade/mirror
Payload: {
    "action": "BUY" | "SELL" | "CLOSE",
    "symbol": "MGC",
    "contracts": 11,
    "price": 5050.00,
    "sl": 5000.00,
    "tp": 5075.00,
    "source": "MT5_SCALPER"
}
"""

import nest_asyncio
nest_asyncio.apply()

from flask import Flask, jsonify, request
from flask_cors import CORS
from ib_insync import IB, Future, MarketOrder, LimitOrder, StopOrder, Order
from datetime import datetime
import json
import os

app = Flask(__name__)
CORS(app)

# IBKR Connection Settings
IBKR_HOST = "100.119.161.65"  # Tailscale IP
IBKR_PORT = 7497
CLIENT_ID = 90  # Unique client ID for mirror service

# Trade log
TRADE_LOG_PATH = "/home/jbot/trading_ai/data/mt5_mirror_log.json"


def log_trade(trade_data, result):
    """Log trade to JSON file"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "trade": trade_data,
        "result": result
    }
    
    # Load existing log
    trades = []
    if os.path.exists(TRADE_LOG_PATH):
        try:
            with open(TRADE_LOG_PATH, 'r') as f:
                trades = json.load(f)
        except:
            trades = []
    
    trades.append(log_entry)
    
    # Keep last 500 trades
    if len(trades) > 500:
        trades = trades[-500:]
    
    with open(TRADE_LOG_PATH, 'w') as f:
        json.dump(trades, f, indent=2)


def get_mgc_contract():
    """Get MGC contract - use April 2026 to avoid near-expiration issues"""
    # MGC = Micro Gold Futures on COMEX
    # Gold futures months: Feb(G), Apr(J), Jun(M), Aug(Q), Oct(V), Dec(Z)
    # Using April 2026 (J) to avoid February expiration issues
    
    return Future(
        symbol='MGC',
        exchange='COMEX',
        lastTradeDateOrContractMonth='202604',  # April 2026
        currency='USD'
    )


def execute_ibkr_trade(action, contracts, sl_price=None, tp_price=None):
    """Execute trade on IBKR"""
    ib = IB()
    result = {"success": False, "message": "", "order_id": None}
    
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=CLIENT_ID, timeout=15)
        
        contract = get_mgc_contract()
        ib.qualifyContracts(contract)
        
        if action == "BUY":
            # Market order to buy
            order = Order(
                action='BUY',
                totalQuantity=contracts,
                orderType='MKT',
                tif='GTC',
                outsideRth=True
            )
            trade = ib.placeOrder(contract, order)
            ib.sleep(3)
            
            result["success"] = True
            result["message"] = f"BUY {contracts} MGC executed"
            result["order_id"] = trade.order.orderId
            
            # Place bracket orders if SL/TP provided
            if sl_price and tp_price:
                ib.sleep(1)
                
                # Stop Loss - using Order() for proper GTC
                sl_order = Order(
                    action='SELL',
                    totalQuantity=contracts,
                    orderType='STP',
                    auxPrice=sl_price,
                    tif='GTC',
                    outsideRth=True
                )
                sl_trade = ib.placeOrder(contract, sl_order)
                ib.sleep(1)
                print(f"SL Order placed: {sl_trade.order.orderId}")
                
                # Take Profit - using Order() for proper GTC
                tp_order = Order(
                    action='SELL',
                    totalQuantity=contracts,
                    orderType='LMT',
                    lmtPrice=tp_price,
                    tif='GTC',
                    outsideRth=True
                )
                tp_trade = ib.placeOrder(contract, tp_order)
                ib.sleep(1)
                print(f"TP Order placed: {tp_trade.order.orderId}")
                
                result["message"] += f" with SL@{sl_price} TP@{tp_price}"
                result["sl_order_id"] = sl_trade.order.orderId
                result["tp_order_id"] = tp_trade.order.orderId
                
        elif action == "SELL":
            # Market order to sell
            order = Order(
                action='SELL',
                totalQuantity=contracts,
                orderType='MKT',
                tif='GTC',
                outsideRth=True
            )
            trade = ib.placeOrder(contract, order)
            ib.sleep(3)
            
            result["success"] = True
            result["message"] = f"SELL {contracts} MGC executed"
            result["order_id"] = trade.order.orderId
            
            # Place bracket orders if SL/TP provided
            if sl_price and tp_price:
                ib.sleep(1)
                
                # Stop Loss (buy back to close short)
                sl_order = Order(
                    action='BUY',
                    totalQuantity=contracts,
                    orderType='STP',
                    auxPrice=sl_price,
                    tif='GTC',
                    outsideRth=True
                )
                sl_trade = ib.placeOrder(contract, sl_order)
                ib.sleep(1)
                print(f"SL Order placed: {sl_trade.order.orderId}")
                
                # Take Profit (buy back to close short)
                tp_order = Order(
                    action='BUY',
                    totalQuantity=contracts,
                    orderType='LMT',
                    lmtPrice=tp_price,
                    tif='GTC',
                    outsideRth=True
                )
                tp_trade = ib.placeOrder(contract, tp_order)
                ib.sleep(1)
                print(f"TP Order placed: {tp_trade.order.orderId}")
                
                result["message"] += f" with SL@{sl_price} TP@{tp_price}"
                result["sl_order_id"] = sl_trade.order.orderId
                result["tp_order_id"] = tp_trade.order.orderId
                
        elif action == "CLOSE":
            # Close all MGC positions
            positions = ib.positions()
            closed = 0
            
            for pos in positions:
                if pos.contract.symbol == 'MGC':
                    qty = abs(int(pos.position))
                    if pos.position > 0:
                        order = MarketOrder('SELL', qty)
                    else:
                        order = MarketOrder('BUY', qty)
                    
                    ib.placeOrder(pos.contract, order)
                    closed += qty
            
            # Cancel open orders for MGC
            open_orders = ib.openOrders()
            for order in open_orders:
                if hasattr(order, 'contract') and order.contract.symbol == 'MGC':
                    ib.cancelOrder(order)
            
            ib.sleep(1)
            result["success"] = True
            result["message"] = f"Closed {closed} MGC contracts, cancelled pending orders"
            
        ib.disconnect()
        
    except Exception as e:
        result["success"] = False
        result["message"] = f"Error: {str(e)}"
        try:
            ib.disconnect()
        except:
            pass
    
    return result


@app.route('/')
def home():
    return jsonify({
        "service": "MT5-IBKR Trade Mirror",
        "status": "running",
        "endpoints": {
            "/api/trade/mirror": "POST - Mirror MT5 trade to IBKR",
            "/api/trades/log": "GET - View recent mirrored trades"
        }
    })


@app.route('/trade', methods=['POST'])
@app.route('/api/trade/mirror', methods=['POST'])
def mirror_trade():
    """Receive trade from MT5 and execute on IBKR"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "No JSON data received"}), 400
        
        action = data.get("action", "").upper()
        symbol = data.get("symbol", "MGC")
        contracts = int(data.get("contracts", 11))
        price = float(data.get("price", 0))
        sl = float(data.get("sl", 0)) if data.get("sl") else None
        tp = float(data.get("tp", 0)) if data.get("tp") else None
        source = data.get("source", "MT5")
        
        print(f"📥 Received from {source}: {action} {contracts}x {symbol}")
        print(f"   Price: {price}, SL: {sl}, TP: {tp}")
        
        if action not in ["BUY", "SELL", "CLOSE", "CLOSE_ALL"]:
            return jsonify({"success": False, "error": f"Invalid action: {action}"}), 400
        
        # Normalize CLOSE_ALL to CLOSE
        if action == "CLOSE_ALL":
            action = "CLOSE"
        
        # Execute on IBKR
        result = execute_ibkr_trade(action, contracts, sl, tp)
        
        # Log the trade
        log_trade(data, result)
        
        print(f"📤 Result: {result['message']}")
        
        return jsonify({
            "success": result["success"],
            "message": result["message"],
            "order_id": result.get("order_id"),
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        error_msg = f"Mirror error: {str(e)}"
        print(f"❌ {error_msg}")
        return jsonify({"success": False, "error": error_msg}), 500


@app.route('/api/trades/log', methods=['GET'])
def get_trade_log():
    """Get recent mirrored trades"""
    if os.path.exists(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, 'r') as f:
            trades = json.load(f)
        return jsonify({"trades": trades[-50:]})  # Last 50
    return jsonify({"trades": []})


@app.route('/api/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        "status": "healthy",
        "service": "mt5-ibkr-mirror",
        "timestamp": datetime.now().isoformat()
    })


if __name__ == "__main__":
    print("=" * 60)
    print("  MT5 to IBKR Trade Mirror API")
    print("=" * 60)
    print(f"  Listening on: http://0.0.0.0:8460")
    print(f"  IBKR Target: {IBKR_HOST}:{IBKR_PORT}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=8460, threaded=False)
