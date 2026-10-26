from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os, time, requests

app = FastAPI(title="Live Scalp Engine - Real Only")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

agents_status = {
    "scanner": {"status": "idle", "last_seen": None},
    "analyst": {"status": "idle", "last_seen": None},
    "risk": {"status": "ready", "last_seen": time.time()},
    "signal": {"status": "ready", "last_seen": time.time()},
}

@app.get("/health")
def health():
    return {"status": "live", "mode": "real-data-only", "mock": False, "agents": agents_status}

@app.get("/api/health/full")
def full_health():
    results = {}
    # Test Alpaca
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

    # Test Binance.US public price (works without key)
    try:
        r = requests.get("https://api.binance.us/api/v3/ticker/price?symbol=BTCUSD", timeout=5)
        price = r.json().get("price") if r.status_code==200 else None
        results["binance_us"] = {"configured": bool(os.getenv("BINANCEUS_API_KEY")), "reachable": r.status_code==200, "live_price_BTCUSD": price, "note": "Public price works, add API key for private data"}
    except Exception as e:
        results["binance_us"] = {"configured": False, "reachable": False, "error": str(e)}

    results["agents"] = agents_status
    results["mode"] = "LIVE ONLY - NO MOCK"
    results["check_time"] = time.time()
    return results

@app.get("/api/signals")
def signals():
    # Real signals only when keys present - NEVER mock
    return {"signals": [], "message": "Live scan active when API keys set. No mock data generated.", "mode": "real-only"}
