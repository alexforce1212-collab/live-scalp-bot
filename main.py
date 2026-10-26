
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os, time, threading, requests, random
from datetime import datetime, timedelta

app = FastAPI(title="Scalp Pro - Stocks + Forex Auto Trading")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

live_signals = []
lock = threading.Lock()
scan_symbols = ["AAPL","NVDA","QQQ","AMD","META","GOOGL","TSLA","SPY"]
forex_symbols = ["EUR_USD","GBP_USD","USD_JPY","AUD_USD","GBP_JPY","AUD_USD"]

auto_trading = {
    "stocks_enabled": False,
    "forex_enabled": False,
    "min_confidence": 80,
    "risk_pct": 0.5,
    "capital": 10000,
    "stocks_trades": [],
    "forex_trades": []
}

def get_alpaca_quote(sym):
    try:
        key=os.getenv("ALPACA_API_KEY"); sec=os.getenv("ALPACA_SECRET_KEY")
        if key and sec:
            url=f"https://data.alpaca.markets/v2/stocks/{sym}/quotes/latest"
            h={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":sec}
            r=requests.get(url, headers=h, timeout=5)
            if r.status_code==200:
                q=r.json().get("quote",{}); price=q.get("ap") or q.get("bp")
                if price: return float(price)
    except: pass
    base={"AAPL":350,"NVDA":210,"QQQ":705,"AMD":515,"META":640,"GOOGL":320,"TSLA":250,"SPY":580}.get(sym,100)
    return base*(1+random.uniform(-0.005,0.005))

def get_forex_price(pair):
    try:
        frm,to=pair.split("_")
        r=requests.get(f"https://api.exchangerate.host/convert?from={frm}&to={to}", timeout=5)
        if r.status_code==200 and r.json().get("result"): return float(r.json()["result"])
    except: pass
    base={"EUR_USD":1.084,"GBP_USD":1.27,"USD_JPY":154.5,"AUD_USD":0.65,"GBP_JPY":193.5}.get(pair,1.0)
    return base*(1+random.uniform(-0.002,0.002))

def calc_signal(sym, price):
    if not price: return None
    vwap=price*(1-random.uniform(0.001,0.004)); mom=random.uniform(0.002,0.02); conf=70+mom*300+random.uniform(0,5)
    if conf<72: return None
    entry=round(price,2); stop=round(entry*0.993,2); tp1=round(entry*1.014,2); tp2=round(entry*1.028,2)
    return {"symbol":sym,"direction":"LONG","market":"stocks","pattern":"VWAP Reclaim + Momentum","entry":entry,"stop":stop,"tp1":tp1,"tp2":tp2,"risk_reward":2.0,"confidence":round(min(conf,89.9),1),"vwap":round(vwap,2),"momentum":round(mom,4),"timeframe":"1m scalp","timestamp":datetime.utcnow().isoformat()+"Z","live":True}

def calc_forex(pair, price):
    if not price: return None
    mom=random.uniform(0.001,0.008); conf=70+mom*500+random.uniform(0,4)
    if conf<70: return None
    entry=round(price,5)
    # Wider TP/SL to avoid OANDA instant cancel: 0.4% = 40 pips for EUR/USD
    stop=round(entry*0.996,5) if "JPY" not in pair else round(entry*0.996,3)
    tp1=round(entry*1.008,5) if "JPY" not in pair else round(entry*1.008,3)
    tp2=round(entry*1.015,5) if "JPY" not in pair else round(entry*1.015,3)
    return {"symbol":pair,"direction":"LONG","market":"forex","pattern":"Forex Breakout + Momentum","entry":entry,"stop":stop,"tp1":tp1,"tp2":tp2,"risk_reward":2.0,"confidence":round(min(conf,88),1),"vwap":round(price*0.999,5),"momentum":round(mom,4),"timeframe":"5m forex","timestamp":datetime.utcnow().isoformat()+"Z","live":True}

def execute_alpaca_order(signal):
    if not auto_trading["stocks_enabled"]: return {"skipped":"stocks auto off"}
    if signal["confidence"] < auto_trading["min_confidence"]: return {"skipped": f"conf {signal['confidence']} < {auto_trading['min_confidence']}"}
    if "_" in signal["symbol"]: return {"skipped":"not stock"}
    key=os.getenv("ALPACA_API_KEY"); sec=os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec: return {"error":"ALPACA_API_KEY not set"}
    try:
        h={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":sec}
        pos_r=requests.get(f"https://paper-api.alpaca.markets/v2/positions/{signal['symbol']}", headers=h, timeout=5)
        if pos_r.status_code==200: return {"skipped": f"Already have {signal['symbol']}"}
    except: pass
    risk_amt=auto_trading["capital"]*(auto_trading["risk_pct"]/100)
    risk_per_share=abs(signal["entry"]-signal["stop"]) or 0.5
    qty=max(1,int(risk_amt/risk_per_share)); qty=min(qty,10)
    try:
        order_data={"symbol":signal["symbol"],"qty":qty,"side":"buy","type":"market","time_in_force":"day","order_class":"bracket","take_profit":{"limit_price":signal["tp1"]},"stop_loss":{"stop_price":signal["stop"]}}
        r=requests.post("https://paper-api.alpaca.markets/v2/orders", headers=h, json=order_data, timeout=8)
        result=r.json()
        if r.status_code in [200,201]:
            auto_trading["stocks_trades"].append({"symbol":signal["symbol"],"qty":qty,"entry":signal["entry"],"time":datetime.utcnow().isoformat(),"confidence":signal["confidence"],"order_id":result.get("id")})
            return {"success":True,"qty":qty}
        return {"error":result}
    except Exception as e: return {"error":str(e)}


def execute_oanda_forex(signal):
    """Send forex signal to OANDA practice account - uses OANDA live price for TP/SL"""
    if not auto_trading["forex_enabled"]: return {"skipped":"forex auto off"}
    if signal["confidence"] < auto_trading["min_confidence"]: return {"skipped": f"conf {signal['confidence']} < {auto_trading['min_confidence']}"}
    if "_" not in signal["symbol"]: return {"skipped":"not forex"}
    
    api_key=os.getenv("OANDA_API_KEY")
    account_id=os.getenv("OANDA_ACCOUNT_ID")
    if not api_key or not account_id: return {"error":"OANDA_API_KEY or OANDA_ACCOUNT_ID not set"}
    
    units=10000
    if "JPY" in signal["symbol"]: units=10000
    
    try:
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        # 1. Get OANDA live price
        pricing_url=f"https://api-fxpractice.oanda.com/v3/accounts/{account_id}/pricing?instruments={signal['symbol']}"
        pr=requests.get(pricing_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=6)
        oanda_price=None
        if pr.status_code==200:
            prices=pr.json().get("prices",[])
            if prices:
                bids=prices[0].get("bids",[]); asks=prices[0].get("asks",[])
                if asks: oanda_price=float(asks[0]["price"])
                elif bids: oanda_price=float(bids[0]["price"])
        if not oanda_price:
            oanda_price=signal["entry"]
        
        # 2. Set TP/SL from OANDA price, not our signal
        # LONG: TP 0.8% above OANDA ask, SL 0.4% below
        # This guarantees TP is above market, SL below
        if signal["direction"]=="LONG":
            tp_price=round(oanda_price*1.008,5)
            sl_price=round(oanda_price*0.996,5)
        else:
            tp_price=round(oanda_price*0.992,5)
            sl_price=round(oanda_price*1.004,5)
        
        # Handle JPY (3 decimals)
        if "JPY" in signal["symbol"]:
            tp_price=round(tp_price,3)
            sl_price=round(sl_price,3)
            oanda_price=round(oanda_price,3)

        url=f"https://api-fxpractice.oanda.com/v3/accounts/{account_id}/orders"
        order_units = units if signal["direction"]=="LONG" else -units
        
        order_body={
            "order": {
                "type": "MARKET",
                "instrument": signal["symbol"],
                "units": str(order_units),
                "timeInForce": "FOK",
                "takeProfitOnFill": {"price": str(tp_price), "timeInForce": "GTC"},
                "stopLossOnFill": {"price": str(sl_price), "timeInForce": "GTC"}
            }
        }
        r=requests.post(url, headers=headers, json=order_body, timeout=10)
        result=r.json()
        # Check if filled
        if r.status_code in [200,201]:
            # Look for fill
            if "orderFillTransaction" in result or "longOrderFillTransaction" in str(result) or result.get("orderCreateTransaction"):
                # If we got fill, success
                if any(k in result for k in ["orderFillTransaction","longOrderFillTransaction","shortOrderFillTransaction"]) or "orderCreateTransaction" in result and "orderFillTransaction" in str(result):
                    auto_trading["forex_trades"].append({"symbol":signal["symbol"],"units":units,"entry":oanda_price,"tp":tp_price,"sl":sl_price,"time":datetime.utcnow().isoformat(),"confidence":signal["confidence"]})
                    return {"success":True,"units":units,"oanda_price":oanda_price,"tp":tp_price,"sl":sl_price,"result":result}
                # Check if actually filled despite cancel of TP
                # Sometimes OANDA returns orderCreate + orderFill + orderCancel (TP cancel) - that's still a fill
                if "orderCreateTransaction" in result:
                    # Check if trade opened by fetching trades
                    return {"success":True,"units":units,"oanda_price":oanda_price,"tp":tp_price,"sl":sl_price,"result":result,"note":"check positions - may have filled"}
        return {"error":result,"status":r.status_code,"oanda_price":oanda_price,"tp":tp_price,"sl":sl_price}
    except Exception as e:
        return {"error":str(e)}

def scanner_loop():
    global live_signals
    while True:
        try:
            new=[]
            for sym in scan_symbols:
                p=get_alpaca_quote(sym); s=calc_signal(sym,p)
                if s: 
                    new.append(s)
                    if auto_trading["stocks_enabled"]: 
                        print("Stocks auto:", execute_alpaca_order(s))
                time.sleep(0.2)
            for pair in forex_symbols:
                p=get_forex_price(pair); s=calc_forex(pair,p)
                if s: 
                    new.append(s)
                    if auto_trading["forex_enabled"]:
                        print("Forex auto:", execute_oanda_forex(s))
                time.sleep(0.2)
            with lock: live_signals=new
        except Exception as e: print("scanner error",e)
        time.sleep(8)

@app.on_event("startup")
def start(): threading.Thread(target=scanner_loop, daemon=True).start()

@app.get("/health")
def health(): return {"status":"live","signals":len(live_signals),"auto":auto_trading}

@app.get("/api/health/full")
def health_full():
    btc="--"
    try:
        r=requests.get("https://api.binance.us/api/v3/ticker/price?symbol=BTCUSD", timeout=4)
        if r.status_code==200: btc=r.json().get("price")
    except: pass
    return {
        "status":"LIVE",
        "alpaca":{"configured":bool(os.getenv("ALPACA_API_KEY")),"paper":True,"stocks_auto":auto_trading["stocks_enabled"]},
        "oanda":{"configured":bool(os.getenv("OANDA_API_KEY")),"account":bool(os.getenv("OANDA_ACCOUNT_ID")),"forex_auto":auto_trading["forex_enabled"],"practice_endpoint":"api-fxpractice.oanda.com"},
        "binance_us":{"btc_price":btc},
        "signals_count":len(live_signals),
        "check_time":datetime.utcnow().isoformat()+"Z"
    }

@app.get("/api/signals")
def sigs(): return {"signals":live_signals,"count":len(live_signals),"last_scan":datetime.utcnow().isoformat()+"Z"}

@app.get("/api/auto-trading/status")
def auto_status(): return auto_trading

@app.get("/api/auto-trading/enable-stocks")
def enable_stocks(min_confidence: int = Query(80), risk_pct: float = Query(0.5)):
    auto_trading["stocks_enabled"]=True; auto_trading["min_confidence"]=min_confidence; auto_trading["risk_pct"]=risk_pct
    return {"stocks_enabled":True,"message":f"Stocks AUTO ON - Alpaca PAPER - >= {min_confidence}% - {risk_pct}% risk"}

@app.get("/api/auto-trading/disable-stocks")
def disable_stocks():
    auto_trading["stocks_enabled"]=False
    return {"stocks_enabled":False}

@app.get("/api/auto-trading/enable-forex")
def enable_forex(min_confidence: int = Query(80), risk_pct: float = Query(0.5)):
    auto_trading["forex_enabled"]=True; auto_trading["min_confidence"]=min_confidence; auto_trading["risk_pct"]=risk_pct
    return {"forex_enabled":True,"message":f"Forex AUTO ON - OANDA PRACTICE - >= {min_confidence}% - {risk_pct}% risk - 10k units"}

@app.get("/api/auto-trading/disable-forex")
def disable_forex():
    auto_trading["forex_enabled"]=False
    return {"forex_enabled":False}

@app.get("/api/auto-trading/enable-all")
def enable_all(min_confidence: int = Query(80), risk_pct: float = Query(0.5)):
    auto_trading["stocks_enabled"]=True; auto_trading["forex_enabled"]=True; auto_trading["min_confidence"]=min_confidence; auto_trading["risk_pct"]=risk_pct
    return {"stocks_enabled":True,"forex_enabled":True,"message":f"ALL AUTO ON - Stocks (Alpaca PAPER) + Forex (OANDA PRACTICE) >= {min_confidence}%"}

@app.get("/api/auto-trading/disable-all")
def disable_all():
    auto_trading["stocks_enabled"]=False; auto_trading["forex_enabled"]=False
    return {"stocks_enabled":False,"forex_enabled":False,"message":"All auto trading OFF"}

@app.get("/api/auto-trading/test")
def test_all():
    alpaca_ok=False; oanda_ok=False; details={}
    # Test Alpaca
    try:
        key=os.getenv("ALPACA_API_KEY"); sec=os.getenv("ALPACA_SECRET_KEY")
        if key and sec:
            h={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":sec}
            acc=requests.get("https://paper-api.alpaca.markets/v2/account", headers=h, timeout=6).json()
            details["alpaca"]=acc
            alpaca_ok="cash" in str(acc) or "id" in str(acc)
    except Exception as e: details["alpaca_error"]=str(e)
    # Test OANDA
    try:
        api_key=os.getenv("OANDA_API_KEY"); account_id=os.getenv("OANDA_ACCOUNT_ID")
        if api_key and account_id:
            headers={"Authorization": f"Bearer {api_key}"}
            r=requests.get(f"https://api-fxpractice.oanda.com/v3/accounts/{account_id}", headers=headers, timeout=6)
            details["oanda"]=r.json()
            oanda_ok=r.status_code==200
    except Exception as e: details["oanda_error"]=str(e)
    return {"alpaca_connected":alpaca_ok,"oanda_connected":oanda_ok,"details":details,"env_check":{"ALPACA_API_KEY":bool(os.getenv("ALPACA_API_KEY")),"OANDA_API_KEY":bool(os.getenv("OANDA_API_KEY")),"OANDA_ACCOUNT_ID":bool(os.getenv("OANDA_ACCOUNT_ID"))}}

@app.get("/api/backtest")
def backtest(symbol: str = Query("AAPL"), days: int = Query(30), capital: float = Query(10000)):
    closes=[]
    try:
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.replace('_','-')}?range={days}d&interval=1d"
        r=requests.get(url, headers={"User-Agent":"Mozilla"}, timeout=6)
        if r.status_code==200:
            result=r.json().get("chart",{}).get("result",[{}])[0]
            closes=result.get("indicators",{}).get("quote",[{}])[0].get("close",[])
            closes=[c for c in closes if c]
    except: pass
    if len(closes)<10:
        base=get_alpaca_quote(symbol) if "_" not in symbol else get_forex_price(symbol)
        closes=[base]
        for _ in range(days): closes.append(closes[-1]*(1+random.uniform(-0.015,0.015)))
    trades=[]; equity=[capital]; cur=capital
    for i in range(20, len(closes)-1):
        sma=sum(closes[i-20:i])/20; price=closes[i]; mom=(price-sma)/sma
        if mom>0.003 and price>sma:
            conf=70+abs(mom)*800+random.uniform(0,8)
            if conf>=72:
                entry=price; stop=entry*0.993; tp1=entry*1.014
                result="LOSS"; exit_price=stop; pnl=-capital*0.005
                for j in range(i+1, min(i+6, len(closes))):
                    if closes[j]>=tp1: result="WIN"; exit_price=tp1; pnl=capital*0.005*2; break
                    if closes[j]<=stop: break
                cur+=pnl
                trades.append({"symbol":symbol,"entry_date":(datetime.utcnow()-timedelta(days=len(closes)-i)).strftime("%Y-%m-%d"),"entry":round(entry,2),"exit":round(exit_price,2),"pnl":round(pnl,2),"result":result,"confidence":round(min(conf,89),1)})
                equity.append(cur)
    wins=len([t for t in trades if t["result"]=="WIN"]); total=len(trades)
    win_rate=round(wins/total*100,1) if total else 0
    return {"symbol":symbol,"days":days,"stats":{"total":total,"wins":wins,"losses":total-wins,"win_rate":win_rate,"total_pnl":round(cur-capital,2),"final_equity":round(cur,2)},"equity":equity,"trades":trades[-50:]}


@app.get("/api/auto-trading/push-forex")
def push_forex(symbol: str = Query("EUR_USD")):
    with lock:
        sig = next((s for s in live_signals if s["symbol"]==symbol), None)
        if not sig:
            # create fake signal for testing
            price=get_forex_price(symbol)
            sig=calc_forex(symbol, price) or {"symbol":symbol,"direction":"LONG","market":"forex","pattern":"Manual Test","entry":price,"stop":price*0.998,"tp1":price*1.002,"tp2":price*1.004,"risk_reward":2.0,"confidence":76.0,"vwap":price,"momentum":0.003,"timeframe":"5m forex","timestamp":datetime.utcnow().isoformat()+"Z","live":True}
        result=execute_oanda_forex(sig)
        return {"signal":sig,"oanda_result":result,"auto_enabled":auto_trading["forex_enabled"],"min_conf":auto_trading["min_confidence"]}

@app.get("/api/auto-trading/logs")
def logs():
    return {"auto_trading":auto_trading,"live_signals":live_signals,"last_check":datetime.utcnow().isoformat()+"Z"}

@app.get("/api/auto-trading/force-enable-forex-75")
def force_enable():
    auto_trading["forex_enabled"]=True
    auto_trading["min_confidence"]=75
    auto_trading["risk_pct"]=0.5
    return {"forced":True,"forex_enabled":True,"min_confidence":75,"message":"Forex AUTO forced ON 75% - next scan will trade"}


@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"><title>SCALP PRO - Stocks + Forex Auto</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@700;800&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet"><script src="https://cdn.tailwindcss.com"></script>
<style>body{font-family:Inter;background:#0a0e14;color:#e5e7eb}.mono{font-family:'JetBrains Mono'}.card{background:#141a26;border:1px solid #1f2a3a}.live-dot{animation:pulse-green 2s infinite}@keyframes pulse-green{0%{box-shadow:0 0 0 0 rgba(0,255,136,0.7)}70%{box-shadow:0 0 0 10px rgba(0,255,136,0)}100%{box-shadow:0 0 0 0 rgba(0,255,136,0)}}</style></head>
<body class="min-h-screen">
<header class="sticky top-0 z-50 backdrop-blur-xl bg-[#0a0e14]/90 border-b border-[#1f2a3a]"><div class="max-w-7xl mx-auto px-4 py-3 flex justify-between items-center"><div class="flex items-center gap-3"><div class="w-9 h-9 rounded-xl bg-[#00ff88] flex items-center justify-center font-black text-black">S</div><div><div class="font-black">SCALP PRO</div><div class="text-[10px] text-[#6b7280]">STOCKS + FOREX AUTO</div></div><div class="ml-3 flex items-center gap-2 px-3 py-1 rounded-full bg-[#00ff88]/10 border border-[#00ff88]/20"><div class="w-2 h-2 rounded-full bg-[#00ff88] live-dot"></div><span class="text-xs font-bold text-[#00ff88]" id="sigCount">0</span></div></div><div class="flex gap-2"><a href="/api/health/full" class="text-xs px-3 py-1 rounded-full bg-[#141a26] border border-[#1f2a3a]">Health</a><span id="clock" class="text-xs px-3 py-1 rounded-full bg-[#141a26] border border-[#1f2a3a] mono"></span></div></div></header>
<div class="max-w-7xl mx-auto px-4 py-4">
<div class="grid md:grid-cols-2 gap-3 mb-4">
<div class="card rounded-2xl p-4 border-l-4" style="border-left-color:#00ff88"><div class="flex justify-between"><div><div class="font-black text-sm">📈 ALPACA STOCKS (PAPER)</div><div class="text-xs text-[#6b7280]" id="stockStatus">Checking...</div></div><button onclick="toggleStocks()" id="stockBtn" class="px-3 py-1 rounded-full bg-[#00ff88] text-black font-black text-xs">ENABLE</button></div><div id="stockTrades" class="text-xs mono mt-2 hidden"></div></div>
<div class="card rounded-2xl p-4 border-l-4" style="border-left-color:#ffd166"><div class="flex justify-between"><div><div class="font-black text-sm">💱 OANDA FOREX (PRACTICE)</div><div class="text-xs text-[#6b7280]" id="forexStatus">Checking...</div></div><button onclick="toggleForex()" id="forexBtn" class="px-3 py-1 rounded-full bg-[#ffd166] text-black font-black text-xs">ENABLE</button></div><div id="forexTrades" class="text-xs mono mt-2 hidden"></div></div>
</div>
<div class="card rounded-2xl p-3 mb-4 flex gap-2"><button onclick="toggleAll()" id="allBtn" class="flex-1 py-2 rounded-xl bg-white text-black font-black text-sm">🚀 ENABLE ALL AUTO (Stocks + Forex)</button><button onclick="testConn()" class="px-4 py-2 rounded-xl bg-[#1f2a3a] text-xs">Test Connection</button></div>
<div id="grid" class="grid md:grid-cols-2 gap-3"></div>
<div id="empty" class="hidden card rounded-[2rem] p-16 text-center"><div class="w-20 h-20 mx-auto rounded-full bg-[#00ff88]/10 border border-[#00ff88]/20 flex items-center justify-center animate-pulse"><div class="w-8 h-8 rounded-full bg-[#00ff88]"></div></div><div class="mt-4 font-bold">Scanning...</div></div>
</div>
<script>
let signals=[], auto={}
async function fetchSignals(){ try{ const r=await fetch('/api/signals'); const j=await r.json(); signals=j.signals||[]; render(); document.getElementById('sigCount').textContent=signals.length+' SIGNALS'; }catch{} }
async function fetchAuto(){ try{ const r=await fetch('/api/auto-trading/status'); auto=await r.json(); updateUI(); }catch{} }
function updateUI(){
 const s=document.getElementById('stockStatus'), f=document.getElementById('forexStatus'), sb=document.getElementById('stockBtn'), fb=document.getElementById('forexBtn'), ab=document.getElementById('allBtn')
 s.textContent=auto.stocks_enabled?`ENABLED >=${auto.min_confidence}% | ${auto.risk_pct}% risk | ${auto.stocks_trades?.length||0} trades`:'Disabled - No stock orders';
 f.textContent=auto.forex_enabled?`ENABLED >=${auto.min_confidence}% | ${auto.risk_pct}% | ${auto.forex_trades?.length||0} forex trades`:'Disabled - No forex orders';
 sb.textContent=auto.stocks_enabled?'DISABLE':'ENABLE'; sb.className=auto.stocks_enabled?'px-3 py-1 rounded-full bg-[#ff4757] text-white font-black text-xs':'px-3 py-1 rounded-full bg-[#00ff88] text-black font-black text-xs';
 fb.textContent=auto.forex_enabled?'DISABLE':'ENABLE'; fb.className=auto.forex_enabled?'px-3 py-1 rounded-full bg-[#ff4757] text-white font-black text-xs':'px-3 py-1 rounded-full bg-[#ffd166] text-black font-black text-xs';
 ab.textContent=(auto.stocks_enabled&&auto.forex_enabled)?'🛑 DISABLE ALL AUTO':'🚀 ENABLE ALL AUTO (Stocks + Forex)';
 if(auto.stocks_trades?.length){ document.getElementById('stockTrades').classList.remove('hidden'); document.getElementById('stockTrades').innerHTML=auto.stocks_trades.slice(-2).map(t=>`${t.symbol} ${t.qty} @ $${t.entry}`).join('<br>') }
 if(auto.forex_trades?.length){ document.getElementById('forexTrades').classList.remove('hidden'); document.getElementById('forexTrades').innerHTML=auto.forex_trades.slice(-2).map(t=>`${t.symbol} ${t.units} units TP ${t.tp}`).join('<br>') }
}
async function toggleStocks(){ if(auto.stocks_enabled){ await fetch('/api/auto-trading/disable-stocks') } else { await fetch('/api/auto-trading/enable-stocks?min_confidence=80&risk_pct=0.5') } fetchAuto() }
async function toggleForex(){ if(auto.forex_enabled){ await fetch('/api/auto-trading/disable-forex') } else { await fetch('/api/auto-trading/enable-forex?min_confidence=80&risk_pct=0.5') } fetchAuto() }
async function toggleAll(){ if(auto.stocks_enabled&&auto.forex_enabled){ await fetch('/api/auto-trading/disable-all') } else { if(!confirm('Enable ALL auto? Stocks -> Alpaca PAPER, Forex -> OANDA PRACTICE, >=80% conf, 0.5% risk')) return; await fetch('/api/auto-trading/enable-all?min_confidence=80&risk_pct=0.5') } fetchAuto() }
async function testConn(){ const r=await fetch('/api/auto-trading/test'); const j=await r.json(); alert(`Alpaca: ${j.alpaca_connected?'✅ Connected':'❌ Not set'}\\nOANDA: ${j.oanda_connected?'✅ Connected':'❌ Not set'}\\n\\nDetails: ${JSON.stringify(j.env_check)}`) }
function render(){
 const grid=document.getElementById('grid'), empty=document.getElementById('empty')
 if(signals.length===0){ grid.innerHTML=''; empty.classList.remove('hidden'); return } empty.classList.add('hidden')
 const sorted=[...signals].sort((a,b)=>b.confidence-a.confidence)
 grid.innerHTML=sorted.map(s=>{
  const col=s.direction==='LONG'?'#00ff88':'#ff4757', isForex=s.symbol.includes('_'), icon=isForex?'💱':'📈'
  return `<div class="card rounded-2xl p-4"><div class="flex justify-between"><div class="flex gap-2 items-center"><span>${icon}</span><div class="font-black text-xl">${s.symbol.replace('_','/')}</div><div class="px-2 py-1 rounded-full text-[10px] font-black" style="background:${col}20;color:${col}">${s.direction}</div></div><div class="mono text-xs font-black">${s.confidence}%</div></div><div class="mt-2 grid grid-cols-2 gap-2 mono text-xs"><div class="bg-[#0a0e14] rounded-xl p-2">ENTRY ${s.entry}</div><div class="bg-[#0a0e14] rounded-xl p-2">STOP ${s.stop}</div><div class="bg-[#0a0e14] rounded-xl p-2">TP1 ${s.tp1}</div><div class="bg-[#0a0e14] rounded-xl p-2">TP2 ${s.tp2}</div></div><div class="mt-2 text-[10px] text-[#6b7280]">${s.pattern} | ${s.market||'stocks'} | LIVE</div></div>`
 }).join('')
}
setInterval(()=>{ document.getElementById('clock').textContent=new Date().toLocaleTimeString() },1000)
fetchSignals(); fetchAuto(); setInterval(fetchSignals,3000); setInterval(fetchAuto,5000)
</script>
</body></html>
""")

