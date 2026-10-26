from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os, time, threading, requests
from datetime import datetime
import math

app = FastAPI(title="Live Scalp Engine - Real Scanner Active")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# State
agents_status = {
    "scanner": {"status": "starting", "last_seen": time.time(), "scanning": []},
    "analyst": {"status": "starting", "last_seen": time.time()},
    "risk": {"status": "ready", "last_seen": time.time()},
    "signal": {"status": "ready", "last_seen": time.time()},
}

live_signals = []
scan_symbols = ["AAPL","TSLA","NVDA","SPY","QQQ","MSFT","AMD"]

def get_alpaca_quote(symbol):
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        return None
    try:
        # Latest quote + trade for real price
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest", headers=headers, timeout=4)
        if r.status_code != 200:
            return None
        data = r.json()
        q = data.get("quote", {})
        price = q.get("ap", 0) or q.get("bp", 0)  # ask or bid
        # also get bars for VWAP calc
        bars_r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars?timeframe=1Min&limit=30", headers=headers, timeout=4)
        bars = bars_r.json().get("bars", []) if bars_r.status_code==200 else []
        return {"symbol": symbol, "price": price, "bars": bars, "quote": q}
    except Exception as e:
        print(f"Error {symbol}: {e}")
        return None

def calculate_signal(symbol_data):
    if not symbol_data or not symbol_data["bars"]:
        return None
    bars = symbol_data["bars"]
    price = symbol_data["price"]
    if price == 0 or len(bars) < 5:
        return None
    
    # Simple real logic: VWAP approximation + momentum
    # VWAP = sum(price*volume)/sum(volume) from today's bars
    total_pv = 0
    total_v = 0
    closes = []
    for b in bars:
        c = b.get("c", 0)
        v = b.get("v", 0)
        closes.append(c)
        total_pv += c * v
        total_v += v
    vwap = total_pv / total_v if total_v else closes[-1]
    
    # Momentum: last 5 closes trend
    if len(closes) >= 5:
        mom = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] else 0
    else:
        mom = 0
    
    # Score logic (real, no mock)
    score = 0
    direction = None
    pattern = ""
    
    # VWAP reclaim long
    if price > vwap and mom > 0.2:
        score = 75 + min(abs(mom)*10, 20)
        direction = "LONG"
        pattern = "VWAP Reclaim + Momentum"
    elif price < vwap and mom < -0.2:
        score = 75 + min(abs(mom)*10, 20)
        direction = "SHORT"
        pattern = "VWAP Breakdown + Momentum"
    
    if score >= 80 and direction:
        # Risk: stop 0.5% away, targets 1% and 2%
        if direction == "LONG":
            stop = price * 0.995
            tp1 = price * 1.01
            tp2 = price * 1.02
        else:
            stop = price * 1.005
            tp1 = price * 0.99
            tp2 = price * 0.98
        
        risk_reward = abs(tp1 - price) / abs(price - stop) if price != stop else 0
        
        return {
            "symbol": symbol_data["symbol"],
            "direction": direction,
            "pattern": pattern,
            "entry": round(price, 2),
            "stop_loss": round(stop, 2),
            "take_profit_1": round(tp1, 2),
            "take_profit_2": round(tp2, 2),
            "risk_reward": round(risk_reward, 2),
            "confidence": round(min(score, 95), 1),
            "vwap": round(vwap, 2),
            "momentum_pct": round(mom, 3),
            "timeframe": "1m scalp",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "live": True
        }
    return None

def scanner_loop():
    global live_signals
    while True:
        try:
            agents_status["scanner"]["status"] = "scanning"
            agents_status["scanner"]["last_seen"] = time.time()
            agents_status["scanner"]["scanning"] = scan_symbols
            agents_status["analyst"]["status"] = "analyzing"
            agents_status["analyst"]["last_seen"] = time.time()
            
            new_signals = []
            for sym in scan_symbols:
                data = get_alpaca_quote(sym)
                if data:
                    sig = calculate_signal(data)
                    if sig:
                        new_signals.append(sig)
                time.sleep(0.3)  # avoid rate limit
            
            # Keep only last 20, newest first
            live_signals = sorted(new_signals, key=lambda x: x["confidence"], reverse=True)[:20]
            
            agents_status["scanner"]["status"] = "active"
            agents_status["analyst"]["status"] = "active"
            agents_status["signal"]["status"] = "active" if live_signals else "waiting"
            agents_status["signal"]["last_seen"] = time.time()
            agents_status["risk"]["last_seen"] = time.time()
            
            print(f"Scan complete: {len(live_signals)} signals")
            time.sleep(10)  # scan every 10s
        except Exception as e:
            print(f"Scanner error: {e}")
            time.sleep(5)

# Start scanner in background on startup
@app.on_event("startup")
def start_scanner():
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()

@app.get("/health")
def health():
    return {"status": "live", "mode": "real-data-only", "mock": False, "agents": agents_status, "signals_count": len(live_signals)}

@app.get("/api/health/full")
def full_health():
    results = {}
    alpaca_key = os.getenv("ALPACA_API_KEY")
    if alpaca_key:
        try:
            r = requests.get("https://data.alpaca.markets/v2/stocks/AAPL/quotes/latest", 
                             headers={"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY")},
                             timeout=5)
            results["alpaca"] = {"configured": True, "reachable": r.status_code==200, "status_code": r.status_code, "latency_test": "ok" if r.status_code==200 else "failed"}
        except Exception as e:
            results["alpaca"] = {"configured": True, "reachable": False, "error": str(e)}
    else:
        results["alpaca"] = {"configured": False, "reachable": False}

    try:
        r = requests.get("https://api.binance.us/api/v3/ticker/price?symbol=BTCUSD", timeout=5)
        price = r.json().get("price") if r.status_code==200 else None
        results["binance_us"] = {"configured": bool(os.getenv("BINANCEUS_API_KEY")), "reachable": r.status_code==200, "live_price_BTCUSD": price}
    except Exception as e:
        results["binance_us"] = {"configured": False, "reachable": False, "error": str(e)}

    results["agents"] = agents_status
    results["mode"] = "LIVE ONLY - NO MOCK - SCANNER ACTIVE"
    results["check_time"] = time.time()
    results["active_symbols"] = scan_symbols
    results["signals_count"] = len(live_signals)
    return results

@app.get("/api/signals")
def signals():
    # REAL signals only - never mock
    return {"signals": live_signals, "mode": "real-only", "count": len(live_signals), "last_scan": agents_status["scanner"]["last_seen"]}
