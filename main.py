
import os, json, time, math, random
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import threading

app = FastAPI(title="SCALP PRO - FAST 0.3% Scalper")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# --- SETTINGS - FAST SCALP ---
auto_trading={
    "stocks_enabled": True,
    "forex_enabled": True,
    "min_confidence": 70,
    "risk_pct": 0.5,
    "capital": 10000,
    "stocks_trades": [],
    "forex_trades": [],
    "strategy": "FAST SCALP EMA5/9 + VWAP + RSI7",
    "tp_pct": 0.3,
    "sl_pct": 0.2,
    "max_hold_sec": 90
}

live_signals: List[Dict] = []
scan_log: List[str] = []

# --- INDICATOR CALCS ---
def ema(values: List[float], period: int) -> float:
    if len(values) < period: return sum(values)/len(values) if values else 0
    k = 2/(period+1)
    ema_val = sum(values[:period])/period
    for price in values[period:]:
        ema_val = price*k + ema_val*(1-k)
    return ema_val

def rsi(values: List[float], period: int = 7) -> float:
    if len(values) < period+1: return 50
    deltas = [values[i]-values[i-1] for i in range(1,len(values))]
    gains = [d if d>0 else 0 for d in deltas[-period:]]
    losses = [-d if d<0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains)/period if gains else 0.00001
    avg_loss = sum(losses)/period if losses else 0.00001
    if avg_loss == 0: return 100
    rs = avg_gain/avg_loss
    return 100 - (100/(1+rs))

def vwap(prices: List[float], volumes: List[float]) -> float:
    if not prices or not volumes or len(prices)!=len(volumes): return prices[-1] if prices else 0
    pv = sum(p*v for p,v in zip(prices, volumes))
    vv = sum(volumes)
    return pv/vv if vv else prices[-1]

# --- FETCH OANDA CANDLES FOR FAST SCALP ---
def get_oanda_candles(instrument: str, granularity: str = "M1", count: int = 100) -> List[Dict]:
    api_key = os.getenv("OANDA_API_KEY")
    account_id = os.getenv("OANDA_ACCOUNT_ID")
    if not api_key or not account_id:
        return []
    try:
        url = f"https://api-fxpractice.oanda.com/v3/instruments/{instrument}/candles?count={count}&granularity={granularity}&price=M"
        headers = {"Authorization": f"Bearer {api_key}"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            candles = r.json().get("candles", [])
            result=[]
            for c in candles:
                if c.get("complete"):
                    mid = c.get("mid",{})
                    o = float(mid.get("o",0)); h=float(mid.get("h",0)); l=float(mid.get("l",0)); cl=float(mid.get("c",0))
                    vol = int(c.get("volume",100))
                    result.append({"o":o,"h":h,"l":l,"c":cl,"vol":vol,"time":c.get("time")})
            return result
    except Exception as e:
        scan_log.append(f"OANDA candle error {instrument}: {e}")
    return []

def analyze_fast_scalp(instrument: str) -> Dict:
    """EMA5/9 + VWAP + RSI7 fast scalp analysis"""
    candles = get_oanda_candles(instrument, "M1", 100)
    if len(candles) < 20:
        # Fallback using random walk if no OANDA key or not enough data - but still produce signal logic
        # Try public price
        try:
            pr = requests.get(f"https://api.exchangerate.host/convert?from={instrument[:3]}&to={instrument[3:]}", timeout=5).json()
            price = pr.get("result", 1.08)
        except:
            price = 1.085
        return {"skip": True, "reason": "not enough candles", "price": price}
    
    closes = [c["c"] for c in candles]
    volumes = [c["vol"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    
    ema5 = ema(closes, 5)
    ema9 = ema(closes, 9)
    ema20 = ema(closes, 20)
    rsi7 = rsi(closes, 7)
    vw = vwap(closes[-20:], volumes[-20:])
    current = closes[-1]
    prev_close = closes[-2]
    atr = sum([highs[i]-lows[i] for i in range(-14,0)])/14 if len(highs)>=14 else 0.0005
    
    # Fast scalp logic
    long_cond = current > vw and ema5 > ema9 and ema9 > ema20 and 50 < rsi7 < 70 and current > prev_close
    short_cond = current < vw and ema5 < ema9 and ema9 < ema20 and 30 < rsi7 < 50 and current < prev_close
    
    confidence = 0
    direction = None
    if long_cond:
        direction = "LONG"
        # Confidence based on strength
        conf = 60 + (rsi7-50)*0.8 + (5 if ema5-ema9>0.0001 else 0) + (5 if current>vw else 0)
        confidence = min(95, max(65, conf))
    elif short_cond:
        direction = "SHORT"
        conf = 60 + (50-rsi7)*0.8 + (5 if ema9-ema5>0.0001 else 0) + (5 if current<vw else 0)
        confidence = min(95, max(65, conf))
    
    return {
        "instrument": instrument,
        "price": current,
        "ema5": ema5, "ema9": ema9, "ema20": ema20,
        "rsi7": rsi7, "vwap": vw, "atr": atr,
        "direction": direction,
        "confidence": round(confidence,1) if direction else 0,
        "closes": closes
    }

def generate_fast_signals():
    instruments = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]
    new_signals=[]
    for inst in instruments:
        try:
            analysis = analyze_fast_scalp(inst)
            if analysis.get("direction") and analysis.get("confidence",0) >= auto_trading["min_confidence"]:
                tp_pct = auto_trading["tp_pct"]/100
                sl_pct = auto_trading["sl_pct"]/100
                if analysis["direction"]=="LONG":
                    tp = analysis["price"]*(1+tp_pct)
                    sl = analysis["price"]*(1-sl_pct)
                else:
                    tp = analysis["price"]*(1-tp_pct)
                    sl = analysis["price"]*(1+sl_pct)
                sig={
                    "symbol": inst,
                    "direction": analysis["direction"],
                    "entry": round(analysis["price"],5),
                    "tp": round(tp,5),
                    "sl": round(sl,5),
                    "confidence": analysis["confidence"],
                    "strategy": auto_trading["strategy"],
                    "indicators": {
                        "ema5": round(analysis["ema5"],5),
                        "ema9": round(analysis["ema9"],5),
                        "ema20": round(analysis["ema20"],5),
                        "vwap": round(analysis["vwap"],5),
                        "rsi7": round(analysis["rsi7"],1),
                        "atr": round(analysis["atr"],5)
                    },
                    "hold_sec": auto_trading["max_hold_sec"],
                    "time": datetime.utcnow().isoformat()+"Z"
                }
                new_signals.append(sig)
                # Auto trade if enabled
                if auto_trading["forex_enabled"]:
                    execute_oanda_fast(sig)
            scan_log.append(f"{datetime.utcnow().strftime('%H:%M:%S')} {inst} EMA5:{analysis.get('ema5',0):.5f} EMA9:{analysis.get('ema9',0):.5f} VWAP:{analysis.get('vwap',0):.5f} RSI7:{analysis.get('rsi7',0):.1f} -> {analysis.get('direction','NO')} {analysis.get('confidence',0)}%")
            scan_log[:] = scan_log[-50:]
        except Exception as e:
            scan_log.append(f"Error {inst}: {e}")
    global live_signals
    live_signals = new_signals[-20:]

def execute_oanda_fast(signal: Dict):
    api_key=os.getenv("OANDA_API_KEY")
    account_id=os.getenv("OANDA_ACCOUNT_ID")
    if not api_key or not account_id: return
    try:
        headers={"Authorization": f"Bearer {api_key}"}
        # Get live price
        pricing_url=f"https://api-fxpractice.oanda.com/v3/accounts/{account_id}/pricing?instruments={signal['symbol']}"
        pr=requests.get(pricing_url, headers=headers, timeout=6)
        live_price=None
        if pr.status_code==200:
            prices=pr.json().get("prices",[])
            if prices:
                asks=prices[0].get("asks",[]); bids=prices[0].get("bids",[])
                if signal["direction"]=="LONG" and asks: live_price=float(asks[0]["price"])
                elif bids: live_price=float(bids[0]["price"])
        if not live_price: live_price=signal["entry"]
        
        # FAST TP/SL: 0.3% / 0.2% from LIVE price
        if signal["direction"]=="LONG":
            tp_price=round(live_price*1.003,5)
            sl_price=round(live_price*0.998,5)
        else:
            tp_price=round(live_price*0.997,5)
            sl_price=round(live_price*1.002,5)
        if "JPY" in signal["symbol"]:
            tp_price=round(tp_price,3); sl_price=round(sl_price,3); live_price=round(live_price,3)
        
        units=10000
        order_units = units if signal["direction"]=="LONG" else -units
        url=f"https://api-fxpractice.oanda.com/v3/accounts/{account_id}/orders"
        body={
            "order":{
                "type":"MARKET",
                "instrument":signal["symbol"],
                "units":str(order_units),
                "timeInForce":"FOK",
                "takeProfitOnFill":{"price":str(tp_price),"timeInForce":"GTC"},
                "stopLossOnFill":{"price":str(sl_price),"timeInForce":"GTC"}
            }
        }
        r=requests.post(url, headers={"Authorization": f"Bearer {api_key}","Content-Type":"application/json"}, json=body, timeout=10)
        res=r.json()
        if r.status_code in [200,201]:
            auto_trading["forex_trades"].append({
                "symbol":signal["symbol"],
                "direction":signal["direction"],
                "entry":live_price,
                "tp":tp_price,
                "sl":sl_price,
                "conf":signal["confidence"],
                "time":datetime.utcnow().isoformat(),
                "strategy":auto_trading["strategy"],
                "indicators":signal["indicators"],
                "result":res
            })
            # Keep last 50 trades
            auto_trading["forex_trades"]=auto_trading["forex_trades"][-50:]
        return res
    except Exception as e:
        return {"error":str(e)}

# --- BACKGROUND SCANNER ---
def scanner_loop():
    while True:
        try:
            generate_fast_signals()
        except Exception as e:
            scan_log.append(f"Scanner error: {e}")
        time.sleep(3)  # FAST - every 3 seconds

threading.Thread(target=scanner_loop, daemon=True).start()

# --- ENDPOINTS ---
@app.get("/")
async def root():
    html = """
    <html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>SCALP PRO - FAST 0.3%</title>
    <style>
    body{background:#0a0e12;color:#e0e6eb;font-family:monospace;padding:20px}
    .card{background:#111820;border:1px solid #1e2a36;padding:15px;border-radius:12px;margin:10px 0}
    .green{color:#00ff88} .red{color:#ff4455} .yellow{color:#ffcc00}
    .live{animation:pulse 1s infinite} @keyframes pulse{0%{opacity:1}50%{opacity:0.5}}
    </style></head><body>
    <h1>⚡ SCALP PRO - FAST SCALP <span class='live green'>● LIVE</span></h1>
    <div class='card'>
    <b>Strategy:</b> EMA5/9 + VWAP + RSI7 | <b>TP:</b> 0.3% | <b>SL:</b> 0.2% | <b>Max Hold:</b> 90s | <b>Scan:</b> 3s
    <br><b>Auto:</b> <span id='auto'></span> | <b>Min Conf:</b> 70% | <b>Mode:</b> FAST IN/OUT
    </div>
    <div class='card'><h3>📡 Live Fast Signals</h3><div id='signals'>Loading...</div></div>
    <div class='card'><h3>📜 Scan Log</h3><pre id='log' style='max-height:200px;overflow:auto'></pre></div>
    <div class='card'><h3>💰 Recent Fast Trades</h3><div id='trades'></div></div>
    <script>
    async function load(){
      try{
        let s=await fetch('/api/public/status').then(r=>r.json());
        document.getElementById('auto').innerHTML=`Forex:${s.auto_trading.forex_enabled} Stocks:${s.auto_trading.stocks_enabled} Conf:${s.auto_trading.min_confidence}% Strat:${s.auto_trading.strategy}`;
        let sig=await fetch('/api/signals').then(r=>r.json());
        let html='';
        sig.forEach(x=>{
          html+=`<div style='border-left:3px solid ${x.direction=='LONG'?'#00ff88':'#ff4455'};padding:8px;margin:5px;background:#0f1a24'>
          <b>${x.symbol} ${x.direction}</b> @ ${x.entry} TP:${x.tp} SL:${x.sl} <span class='${x.confidence>=80?'green':x.confidence>=70?'yellow':'red'}'>${x.confidence}%</span><br>
          <small>EMA5:${x.indicators.ema5} EMA9:${x.indicators.ema9} VWAP:${x.indicators.vwap} RSI7:${x.indicators.rsi7} ATR:${x.indicators.atr}</small>
          <br><small>${x.strategy} | Hold max ${x.hold_sec}s | ${x.time}</small></div>`;
        });
        document.getElementById('signals').innerHTML=html||'Scanning... no 70%+ setups yet';
        let logs=await fetch('/api/scan-log').then(r=>r.json());
        document.getElementById('log').innerText=logs.join('\n');
        let trades=await fetch('/api/trades').then(r=>r.json());
        let thtml='';
        trades.slice(-10).reverse().forEach(t=>{
          thtml+=`<div style='padding:5px;border-bottom:1px solid #222'>${t.time?.slice(11,19)} ${t.symbol} ${t.direction} ${t.entry} -> TP ${t.tp} SL ${t.sl} Conf ${t.conf}% EMA5/9 ${t.indicators?.ema5}/${t.indicators?.ema9} RSI7 ${t.indicators?.rsi7}</div>`;
        });
        document.getElementById('trades').innerHTML=thtml||'No trades yet';
      }catch(e){}
    }
    setInterval(load,3000); load();
    </script></body></html>
    """
    return HTMLResponse(html)

@app.get("/api/public/status")
async def public_status():
    return {"app":"live-scalp-bot","status":"LIVE","mode":"FAST SCALP EMA5/9 VWAP RSI7","auto_trading":auto_trading,"time":datetime.utcnow().isoformat()+"Z"}

@app.get("/api/auto-trading/status")
async def auto_status():
    return auto_trading

@app.get("/api/signals")
async def signals():
    return live_signals

@app.get("/api/scan-log")
async def scan_log_endpoint():
    return scan_log

@app.get("/api/trades")
async def trades():
    return auto_trading["forex_trades"][-20:] + auto_trading["stocks_trades"][-20:]

@app.get("/api/auto-trading/enable-all")
async def enable_all(min_confidence: int = 70, risk_pct: float = 0.5):
    auto_trading["stocks_enabled"]=True
    auto_trading["forex_enabled"]=True
    auto_trading["min_confidence"]=min_confidence
    auto_trading["risk_pct"]=risk_pct
    return auto_trading

@app.get("/api/auto-trading/disable-all")
async def disable_all():
    auto_trading["stocks_enabled"]=False
    auto_trading["forex_enabled"]=False
    return auto_trading

@app.get("/api/auto-trading/push-forex")
async def push_forex(symbol: str = "EUR_USD"):
    # Force push test using fast logic
    analysis=analyze_fast_scalp(symbol)
    if not analysis.get("direction"):
        # Force a test LONG if no signal
        analysis={"instrument":symbol,"price":1.085,"ema5":1.084,"ema9":1.083,"ema20":1.082,"rsi7":55,"vwap":1.083,"atr":0.0005,"direction":"LONG","confidence":75}
        # Build signal
    tp_pct=auto_trading["tp_pct"]/100; sl_pct=auto_trading["sl_pct"]/100
    entry=analysis["price"]
    if analysis["direction"]=="LONG":
        tp=entry*(1+tp_pct); sl=entry*(1-sl_pct)
    else:
        tp=entry*(1-tp_pct); sl=entry*(1+sl_pct)
    sig={"symbol":symbol,"direction":analysis["direction"],"entry":round(entry,5),"tp":round(tp,5),"sl":round(sl,5),"confidence":analysis["confidence"],"strategy":auto_trading["strategy"],"indicators":{"ema5":round(analysis["ema5"],5),"ema9":round(analysis["ema9"],5),"ema20":round(analysis["ema20"],5),"vwap":round(analysis["vwap"],5),"rsi7":round(analysis["rsi7"],1),"atr":round(analysis["atr"],5)},"hold_sec":auto_trading["max_hold_sec"],"time":datetime.utcnow().isoformat()+"Z"}
    res=execute_oanda_fast(sig)
    return {"signal":sig,"execution":res}

@app.get("/api/health/full")
async def health_full():
    return {"status":"LIVE","strategy":auto_trading["strategy"],"tp":f"{auto_trading['tp_pct']}%","sl":f"{auto_trading['sl_pct']}%","hold":f"{auto_trading['max_hold_sec']}s","scan":"3s","auto":auto_trading,"signals_count":len(live_signals)}

@app.get("/api/forex")
async def forex_pairs():
    return ["EUR_USD","GBP_USD","USD_JPY","AUD_USD","USD_CAD"]
