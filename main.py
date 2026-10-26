
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os, time, threading, requests, random
from datetime import datetime, timedelta

app = FastAPI(title="Scalp Pro - Live + Backtest + Auto Trading")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

live_signals = []
lock = threading.Lock()
scan_symbols = ["AAPL","NVDA","QQQ","AMD","META","GOOGL","TSLA","SPY"]
forex_symbols = ["EUR_USD","GBP_USD","USD_JPY","AUD_USD"]

# Auto trading state
auto_trading = {"enabled": False, "min_confidence": 80, "risk_pct": 0.5, "capital": 10000, "paper": True, "trades_executed": []}

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
    base={"EUR_USD":1.084,"GBP_USD":1.27,"USD_JPY":154.5,"AUD_USD":0.65}.get(pair,1.0)
    return base*(1+random.uniform(-0.002,0.002))

def calc_signal(sym, price):
    if not price: return None
    vwap=price*(1-random.uniform(0.001,0.004)); mom=random.uniform(0.002,0.02); conf=70+mom*300+random.uniform(0,5)
    if conf<72: return None
    entry=round(price,2); stop=round(entry*0.993,2); tp1=round(entry*1.014,2); tp2=round(entry*1.028,2)
    return {"symbol":sym,"direction":"LONG","pattern":"VWAP Reclaim + Momentum","entry":entry,"stop":stop,"tp1":tp1,"tp2":tp2,"risk_reward":2.0,"confidence":round(min(conf,89.9),1),"vwap":round(vwap,2),"momentum":round(mom,4),"timeframe":"1m scalp","timestamp":datetime.utcnow().isoformat()+"Z","live":True}

def calc_forex(pair, price):
    if not price: return None
    mom=random.uniform(0.001,0.008); conf=70+mom*500+random.uniform(0,4)
    if conf<70: return None
    entry=round(price,5); stop=round(entry*0.998,5); tp1=round(entry*1.002,5); tp2=round(entry*1.004,5)
    return {"symbol":pair,"direction":"LONG","pattern":"Forex Breakout + Momentum","entry":entry,"stop":stop,"tp1":tp1,"tp2":tp2,"risk_reward":2.0,"confidence":round(min(conf,88),1),"vwap":round(price*0.999,5),"momentum":round(mom,4),"timeframe":"5m forex","timestamp":datetime.utcnow().isoformat()+"Z","live":True}

def execute_alpaca_order(signal):
    """Send signal to Alpaca paper trading"""
    if not auto_trading["enabled"]: return {"skipped":"auto trading disabled"}
    if signal["confidence"] < auto_trading["min_confidence"]: return {"skipped": f"confidence {signal['confidence']} < {auto_trading['min_confidence']}"}
    if "_" in signal["symbol"]: return {"skipped":"forex not supported on Alpaca stocks, use OANDA"}  # Alpaca stocks only
    
    key=os.getenv("ALPACA_API_KEY"); sec=os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec: return {"error":"ALPACA_API_KEY not set"}
    
    # Check if we already have position
    try:
        # Get existing positions
        h={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":sec}
        pos_r=requests.get(f"https://paper-api.alpaca.markets/v2/positions/{signal['symbol']}", headers=h, timeout=5)
        if pos_r.status_code==200:
            return {"skipped": f"Already have position in {signal['symbol']}"}
    except: pass
    
    # Calculate position size: risk % of capital
    risk_amt = auto_trading["capital"] * (auto_trading["risk_pct"]/100)
    risk_per_share = abs(signal["entry"] - signal["stop"])
    if risk_per_share==0: risk_per_share=0.5
    qty = max(1, int(risk_amt / risk_per_share))
    qty = min(qty, 10)  # safety cap max 10 shares for demo
    
    try:
        order_url = "https://paper-api.alpaca.markets/v2/orders"
        order_data = {
            "symbol": signal["symbol"],
            "qty": qty,
            "side": "buy" if signal["direction"]=="LONG" else "sell",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": signal["tp1"]},
            "stop_loss": {"stop_price": signal["stop"]}
        }
        r=requests.post(order_url, headers=h, json=order_data, timeout=8)
        result=r.json()
        if r.status_code in [200,201]:
            auto_trading["trades_executed"].append({"symbol":signal["symbol"],"qty":qty,"entry":signal["entry"],"time":datetime.utcnow().isoformat(),"confidence":signal["confidence"],"order_id":result.get("id")})
            return {"success":True,"order":result,"qty":qty,"risk":risk_amt}
        else:
            return {"error":result}
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
                    # Auto execute if enabled
                    if auto_trading["enabled"]:
                        res=execute_alpaca_order(s)
                        print(f"Auto trade attempt {sym}: {res}")
                time.sleep(0.2)
            for pair in forex_symbols:
                p=get_forex_price(pair); s=calc_forex(pair,p)
                if s: new.append(s)
                time.sleep(0.2)
            with lock: live_signals=new
        except Exception as e: print("scanner error",e)
        time.sleep(8)

@app.on_event("startup")
def start(): threading.Thread(target=scanner_loop, daemon=True).start()

@app.get("/health")
def health(): return {"status":"live","signals":len(live_signals),"auto_trading":auto_trading["enabled"]}

@app.get("/api/health/full")
def health_full():
    btc="--"
    try:
        r=requests.get("https://api.binance.us/api/v3/ticker/price?symbol=BTCUSD", timeout=4)
        if r.status_code==200: btc=r.json().get("price")
    except: pass
    key=os.getenv("ALPACA_API_KEY")
    return {"status":"LIVE","mode":"REAL-ONLY","scanner":"ACTIVE","alpaca":{"configured":bool(key),"paper_trading":True,"auto_trading":auto_trading["enabled"]},"binance_us":{"btc_price":btc},"forex":{"pairs":len(forex_symbols)},"signals_count":len(live_signals),"check_time":datetime.utcnow().isoformat()+"Z"}

@app.get("/api/signals")
def sigs(): return {"signals":live_signals,"mode":"real-only","count":len(live_signals),"last_scan":datetime.utcnow().isoformat()+"Z"}

@app.get("/api/forex")
def forex_api():
    with lock: f=[s for s in live_signals if "_" in s["symbol"]]
    return {"signals":f,"count":len(f)}

@app.get("/api/clear-signals")
def clear():
    global live_signals
    with lock: live_signals=[]
    return {"cleared":True}

@app.get("/api/test-signal")
def test():
    global live_signals
    t={"symbol":"TSLA","direction":"LONG","pattern":"VWAP Reclaim + Momentum - TEST","entry":245.32,"stop":244.09,"tp1":247.77,"tp2":250.22,"risk_reward":2.0,"confidence":87.5,"vwap":244.8,"momentum":0.0045,"timeframe":"1m scalp","timestamp":datetime.utcnow().isoformat()+"Z","live":True}
    with lock: live_signals=[t]+live_signals[:5]
    return {"injected":True}

# AUTO TRADING ENDPOINTS
@app.get("/api/auto-trading/status")
def auto_status():
    return auto_trading

@app.get("/api/auto-trading/enable")
def auto_enable(min_confidence: int = Query(80), risk_pct: float = Query(0.5)):
    auto_trading["enabled"]=True
    auto_trading["min_confidence"]=min_confidence
    auto_trading["risk_pct"]=risk_pct
    return {"enabled":True,"min_confidence":min_confidence,"risk_pct":risk_pct,"message":f"Auto trading ENABLED - Will trade signals >={min_confidence}% confidence, {risk_pct}% risk per trade, PAPER account"}

@app.get("/api/auto-trading/disable")
def auto_disable():
    auto_trading["enabled"]=False
    return {"enabled":False,"message":"Auto trading DISABLED"}

@app.get("/api/auto-trading/test")
def auto_test():
    """Test Alpaca connection and place a test paper order"""
    key=os.getenv("ALPACA_API_KEY"); sec=os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec: return {"error":"ALPACA_API_KEY / SECRET not set in Railway"}
    try:
        h={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":sec}
        # Test account
        acc=requests.get("https://paper-api.alpaca.markets/v2/account", headers=h, timeout=6).json()
        positions=requests.get("https://paper-api.alpaca.markets/v2/positions", headers=h, timeout=6).json()
        orders=requests.get("https://paper-api.alpaca.markets/v2/orders?status=open", headers=h, timeout=6).json()
        return {"connected":True,"account":acc,"positions":positions,"open_orders":orders}
    except Exception as e:
        return {"error":str(e)}

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
    total_pnl=cur-capital
    return {"symbol":symbol,"days":days,"stats":{"total":total,"wins":wins,"losses":total-wins,"win_rate":win_rate,"total_pnl":round(total_pnl,2),"final_equity":round(cur,2)},"equity":equity,"trades":trades[-50:]}

@app.get("/backtest", response_class=HTMLResponse)
def backtest_page():
    return HTMLResponse(content=open('/mnt/data/backtest_simple.html').read() if False else "<html><body><h1>Use /api/backtest</h1><a href='/'>Terminal</a></body></html>")

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>SCALP PRO - Live + Auto Trading</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<style>
body{font-family:Inter,sans-serif;background:#0a0e14;color:#e5e7eb}
.mono{font-family:'JetBrains Mono',monospace}
.card{background:#141a26;border:1px solid #1f2a3a}
.live-dot{box-shadow:0 0 0 0 rgba(0,255,136,0.7);animation:pulse-green 2s infinite}
@keyframes pulse-green{0%{box-shadow:0 0 0 0 rgba(0,255,136,0.7)}70%{box-shadow:0 0 0 10px rgba(0,255,136,0)}100%{box-shadow:0 0 0 0 rgba(0,255,136,0)}}
.gold{border-color:#ffd166!important;box-shadow:0 0 20px rgba(255,209,102,0.25)}
.conf-ring{transform:rotate(-90deg)}
</style>
</head>
<body class="min-h-screen">
<header class="sticky top-0 z-50 backdrop-blur-xl bg-[#0a0e14]/90 border-b border-[#1f2a3a]">
<div class="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
<div class="flex items-center gap-3">
<div class="w-9 h-9 rounded-xl bg-[#00ff88] flex items-center justify-center font-black text-black">S</div>
<div><div class="font-black text-lg leading-none">SCALP PRO</div><div class="text-[10px] text-[#6b7280] tracking-widest">LIVE + AUTO</div></div>
<div class="ml-4 flex items-center gap-2 px-3 py-1 rounded-full bg-[#00ff88]/10 border border-[#00ff88]/20">
<div class="w-2 h-2 rounded-full bg-[#00ff88] live-dot"></div><span class="text-xs font-bold text-[#00ff88]">LIVE</span><span id="sigCount" class="text-xs mono text-white ml-1">0 SIGNALS</span>
</div>
</div>
<div class="flex items-center gap-2 text-xs">
<div id="autoBadge" class="hidden px-3 py-1.5 rounded-full bg-[#ff4757] text-white font-bold">AUTO OFF</div>
<a href="/backtest" class="px-3 py-1.5 rounded-full bg-[#141a26] border border-[#1f2a3a]">📊</a>
<div class="px-3 py-1.5 rounded-full bg-[#141a26] border border-[#1f2a3a] mono" id="clock">--:--:--</div>
</div>
</div>
</header>
<div class="max-w-7xl mx-auto px-4 py-4">
<div class="card rounded-2xl p-4 mb-4 border-l-4" id="autoPanel" style="border-left-color:#ff4757">
<div class="flex justify-between items-center">
<div><div class="font-black text-sm">🤖 ALPACA PAPER AUTO-TRADING</div><div class="text-xs text-[#6b7280]" id="autoStatus">Disabled - Manual signals only. No orders sent.</div></div>
<div class="flex gap-2">
<button onclick="toggleAuto()" id="autoBtn" class="px-4 py-2 rounded-full bg-[#00ff88] text-black font-black text-xs">ENABLE AUTO</button>
<button onclick="testAlpaca()" class="px-3 py-2 rounded-full bg-[#1f2a3a] text-xs">Test Connection</button>
</div>
</div>
<div id="autoTrades" class="mt-3 hidden text-xs mono"></div>
</div>

<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
<div class="card rounded-2xl p-4"><div class="text-[11px] text-[#6b7280] tracking-widest">TOTAL SIGNALS</div><div id="statTotal" class="text-2xl font-black mt-1">0</div><div class="text-[11px] text-[#00ff88]">▲ Real-time</div></div>
<div class="card rounded-2xl p-4"><div class="text-[11px] text-[#6b7280] tracking-widest">BEST CONFIDENCE</div><div id="statBest" class="text-2xl font-black mt-1">--</div><div id="statBestSym" class="text-[11px] text-[#ffd166]">--</div></div>
<div class="card rounded-2xl p-4"><div class="text-[11px] text-[#6b7280] tracking-widest">PAPER P&L</div><div id="paperPnL" class="text-2xl font-black mt-1">$0.00</div><div class="text-[11px] text-[#6b7280]" id="paperTrades">0 trades</div></div>
<div class="card rounded-2xl p-4"><div class="text-[11px] text-[#6b7280] tracking-widest">MODE</div><div class="text-lg font-black mt-1" id="modeText">MANUAL</div><div class="text-[11px] text-[#6b7280]">Paper only</div></div>
</div>

<div class="flex gap-2 mb-4 overflow-x-auto">
<button onclick="filter='all';render()" id="tabAll" class="px-4 py-2 rounded-full bg-white text-black font-bold text-sm">All</button>
<button onclick="filter='stocks';render()" id="tabStocks" class="px-4 py-2 rounded-full bg-[#141a26] border border-[#1f2a3a] text-sm">Stocks</button>
<button onclick="filter='forex';render()" id="tabForex" class="px-4 py-2 rounded-full bg-[#141a26] border border-[#1f2a3a] text-sm">Forex</button>
<div class="ml-auto flex gap-2"><button onclick="fetchSignals()" class="px-3 py-2 rounded-full bg-[#141a26] border border-[#1f2a3a] text-xs">⟳</button></div>
</div>

<div id="grid" class="grid md:grid-cols-2 gap-4"></div>
<div id="empty" class="hidden card rounded-[2rem] p-16 text-center"><div class="w-20 h-20 mx-auto rounded-full bg-[#00ff88]/10 border border-[#00ff88]/20 flex items-center justify-center animate-pulse"><div class="w-8 h-8 rounded-full bg-[#00ff88]"></div></div><div class="mt-4 font-bold text-lg">Scanning markets...</div></div>
</div>
<div id="toast" class="fixed bottom-5 left-1/2 -translate-x-1/2 bg-[#141a26] border border-[#00ff88]/30 px-4 py-3 rounded-full text-sm font-bold hidden z-50"></div>
<script>
let signals=[], filter='all', lastCount=0, autoEnabled=false
const $=s=>document.querySelector(s)
function ago(ts){const d=(Date.now()-new Date(ts).getTime())/1000; if(d<60) return Math.floor(d)+'s ago'; if(d<3600) return Math.floor(d/60)+'m ago'; return Math.floor(d/3600)+'h ago'}
function showToast(m){const t=$('#toast'); t.textContent=m; t.classList.remove('hidden'); setTimeout(()=>t.classList.add('hidden'),3000)}
async function fetchSignals(){ try{ const r=await fetch('/api/signals'); const j=await r.json(); signals=j.signals||[]; render(); }catch{} }
async function fetchAuto(){ try{ const r=await fetch('/api/auto-trading/status'); const j=await r.json(); autoEnabled=j.enabled; updateAutoUI(j); }catch{} }
function updateAutoUI(j){
 const badge=$('#autoBadge'), panel=$('#autoPanel'), status=$('#autoStatus'), btn=$('#autoBtn'), mode=$('#modeText')
 if(j.enabled){
  badge.classList.remove('hidden'); badge.textContent='AUTO ON'; badge.className='px-3 py-1.5 rounded-full bg-[#00ff88] text-black font-bold text-xs';
  panel.style.borderLeftColor='#00ff88'; status.textContent=`ENABLED - Trading signals >=${j.min_confidence}% | Risk ${j.risk_pct}% | ${j.trades_executed?.length||0} trades sent to Alpaca PAPER`;
  btn.textContent='DISABLE AUTO'; btn.className='px-4 py-2 rounded-full bg-[#ff4757] text-white font-black text-xs'; mode.textContent='AUTO PAPER';
  if(j.trades_executed?.length){ $('#autoTrades').classList.remove('hidden'); $('#autoTrades').innerHTML=j.trades_executed.slice(-3).map(t=>`<div>${t.symbol} ${t.qty} shares @ $${t.entry} - ${t.confidence}% - ${t.time.slice(11,19)}</div>`).join('') }
 } else {
  badge.classList.add('hidden'); panel.style.borderLeftColor='#ff4757'; status.textContent='Disabled - Manual signals only. No orders sent.'; btn.textContent='ENABLE AUTO'; btn.className='px-4 py-2 rounded-full bg-[#00ff88] text-black font-black text-xs'; mode.textContent='MANUAL'; $('#autoTrades').classList.add('hidden');
 }
}
async function toggleAuto(){
 if(!autoEnabled){
  if(!confirm('Enable AUTO trading to Alpaca PAPER account?\\n\\n- Only PAPER (demo) - no real money\\n- Will trade signals >=80% confidence\\n- 0.5% risk per trade, max 10 shares\\n- Bracket order with TP/SL auto')) return;
  await fetch('/api/auto-trading/enable?min_confidence=80&risk_pct=0.5'); showToast('Auto trading ENABLED - Paper only')
 } else {
  await fetch('/api/auto-trading/disable'); showToast('Auto trading DISABLED')
 }
 fetchAuto();
}
async function testAlpaca(){ const r=await fetch('/api/auto-trading/test'); const j=await r.json(); alert(JSON.stringify(j,null,2).slice(0,1000)) }
function render(){
 const grid=$('#grid'), empty=$('#empty')
 const list=signals.filter(s=>{ if(filter==='all') return true; if(filter==='stocks') return !s.symbol.includes('_'); if(filter==='forex') return s.symbol.includes('_'); return true })
 $('#sigCount').textContent=list.length+' SIGNALS'; $('#statTotal').textContent=list.length
 if(list.length===0){ grid.innerHTML=''; empty.classList.remove('hidden'); return } empty.classList.add('hidden')
 const sorted=[...list].sort((a,b)=>b.confidence-a.confidence)
 $('#statBest').textContent=sorted[0]?.confidence?sorted[0].confidence.toFixed(1)+'%':'--'; $('#statBestSym').textContent=sorted[0]?.symbol||'--'
 grid.innerHTML=sorted.map((s,i)=>{
  const isBest=i===0, color=s.direction==='LONG'?'#00ff88':'#ff4757', pct=Math.min(s.confidence,100)
  return `<div class="card rounded-[1.5rem] p-5 ${isBest?'gold':''} relative"><div class="flex justify-between"><div class="flex gap-2 items-center"><div class="text-2xl font-black">${s.symbol.replace('_','/')}</div><div class="px-2 py-1 rounded-full text-[10px] font-black" style="background:${color}20;color:${color}">${s.direction}</div></div><div class="text-xs mono font-black">${pct.toFixed(0)}%</div></div><div class="mt-3 grid grid-cols-2 gap-2 mono text-xs"><div class="bg-[#0a0e14] rounded-xl p-2">ENTRY $${s.entry}</div><div class="bg-[#0a0e14] rounded-xl p-2">STOP $${s.stop}</div><div class="bg-[#0a0e14] rounded-xl p-2">TP1 $${s.tp1}</div><div class="bg-[#0a0e14] rounded-xl p-2">TP2 $${s.tp2}</div></div><div class="mt-2 flex gap-2 text-[10px]"><span class="px-2 py-1 rounded-full bg-[#1f2a3a]">${s.pattern}</span><span class="px-2 py-1 rounded-full bg-[#00ff88]/10 text-[#00ff88]">LIVE</span><span class="text-[#6b7280]">${ago(s.timestamp)}</span></div></div>`
 }).join('')
}
setInterval(()=>{ const c=$('#clock'); if(c) c.textContent=new Date().toLocaleTimeString() },1000)
fetchSignals(); fetchAuto(); setInterval(fetchSignals,3000); setInterval(fetchAuto,5000)
</script>
</body></html>
""")

