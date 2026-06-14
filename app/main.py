from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import random, os, math

app = FastAPI(title="AI Trader Hub API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

STATE = {
    "connected": False,
    "account": "DEMO_SIMULADO",
    "robot": "stopped",
    "started_at": None,
    "initial_balance": 1000.0,
    "current_balance": 1000.0,
    "daily_profit": 0.0,
    "loss_streak": 0,
    "wins": 0,
    "losses": 0,
    "operations": 0,
    "config": {
        "daily_goal": 300.0,
        "weekly_goal": 1500.0,
        "entry_value": 2.0,
        "stop_loss": 50.0,
        "stop_gain": 100.0,
        "max_operations": 20,
        "max_losses": 3,
        "start_time": "08:00",
        "end_time": "18:00",
        "min_confidence": 75,
        "mode": "Balanceado"
    },
    "trades": []
}

ASSETS = [("🇺🇸", "EUR/USD"), ("🇬🇧", "GBP/USD"), ("🇺🇸🇯🇵", "USD/JPY"), ("🇦🇺", "AUD/USD"), ("🇺🇸🇨🇦", "USD/CAD")]

class LoginPayload(BaseModel):
    email: str
    password: str
    account_type: str = "PRACTICE"

class ConfigPayload(BaseModel):
    daily_goal: float = Field(ge=1)
    weekly_goal: float = Field(ge=1)
    entry_value: float = Field(ge=1)
    stop_loss: float = Field(ge=1)
    stop_gain: float = Field(ge=1)
    max_operations: int = Field(ge=1, le=200)
    max_losses: int = Field(ge=1, le=20)
    start_time: str
    end_time: str
    min_confidence: int = Field(ge=50, le=95)
    mode: str = "Balanceado"

def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def now_in_window():
    cfg = STATE["config"]
    try:
        now = datetime.now().strftime("%H:%M")
        return cfg["start_time"] <= now <= cfg["end_time"]
    except Exception:
        return True

def fake_candles(asset="EUR/USD", points=80):
    base = 1.084 if asset != "USD/JPY" else 156.20
    drift = random.choice([-1,1]) * random.uniform(0.00001, 0.00008)
    out=[]; price=base + random.uniform(-0.003,0.003)
    for i in range(points):
        noise = random.uniform(-0.00045,0.00045) if asset != "USD/JPY" else random.uniform(-0.045,0.045)
        openp=price; closep=price+drift+noise; high=max(openp,closep)+abs(noise)*0.7; low=min(openp,closep)-abs(noise)*0.7
        price=closep
        out.append({"time": (datetime.now()-timedelta(minutes=points-i)).strftime("%H:%M"), "open":round(openp,5), "high":round(high,5), "low":round(low,5), "close":round(closep,5)})
    return out

def calc_analysis(asset):
    candles = fake_candles(asset, 70)
    closes=[c["close"] for c in candles]
    ma9=sum(closes[-9:])/9; ma21=sum(closes[-21:])/21
    diff=(closes[-1]-closes[-21])
    trend = "ALTA" if ma9>ma21 and diff>0 else "BAIXA" if ma9<ma21 and diff<0 else "LATERAL"
    volatility = abs(max(closes[-20:])-min(closes[-20:]))
    vol_label = "ALTA" if volatility > (0.002 if asset != "USD/JPY" else .20) else "MÉDIA" if volatility > (0.001 if asset != "USD/JPY" else .10) else "BAIXA"
    rsi = random.randint(42,72)
    pattern = random.choice(["Pullback", "Rompimento", "Suporte", "Resistência", "Canal", "Engolfo", "Sem padrão forte"])
    base_score = 45
    if trend in ["ALTA","BAIXA"]: base_score += 18
    if vol_label == "MÉDIA": base_score += 10
    if 48 <= rsi <= 66: base_score += 12
    if pattern != "Sem padrão forte": base_score += 10
    hour_bonus = random.randint(0,12)
    confidence = max(45, min(92, base_score + hour_bonus + random.randint(-5,5)))
    action = "CALL" if trend == "ALTA" and confidence >= STATE["config"]["min_confidence"] else "PUT" if trend == "BAIXA" and confidence >= STATE["config"]["min_confidence"] else "AGUARDAR"
    best_time = random.choice(["09:00-10:30","10:30-12:00","14:00-16:00","15:00-17:00"])
    reasons=[f"Tendência {trend.lower()}", f"RSI {rsi}", f"Volatilidade {vol_label.lower()}", f"Padrão: {pattern}", f"Melhor janela: {best_time}"]
    return {"asset":asset,"trend":trend,"volatility":vol_label,"rsi":rsi,"pattern":pattern,"confidence":confidence,"action":action,"best_time":best_time,"reasons":reasons,"candles":candles}

def risk_check(analysis):
    cfg=STATE["config"]
    if STATE["robot"] != "running": return False, "Robô parado ou pausado"
    if not STATE["connected"]: return False, "IQ Option não conectada"
    if not now_in_window(): return False, "Fora do horário configurado"
    if STATE["daily_profit"] <= -abs(cfg["stop_loss"]): return False, "Stop loss diário atingido"
    if STATE["daily_profit"] >= abs(cfg["stop_gain"]): return False, "Stop gain diário atingido"
    if STATE["operations"] >= cfg["max_operations"]: return False, "Limite de operações atingido"
    if STATE["loss_streak"] >= cfg["max_losses"]: return False, "Máximo de perdas seguidas atingido"
    if analysis["confidence"] < cfg["min_confidence"]: return False, "Confiança abaixo do mínimo"
    if analysis["action"] == "AGUARDAR": return False, "IA mandou aguardar"
    return True, "Aprovado pelo motor de risco"

def maybe_auto_trade():
    if STATE["robot"] != "running" or not STATE["connected"]: return
    if random.random() > 0.35: return
    asset=random.choice([a[1] for a in ASSETS])
    analysis=calc_analysis(asset)
    ok, reason = risk_check(analysis)
    if not ok: return
    result = "WIN" if random.random() < (analysis["confidence"] / 105) else "LOSS"
    profit = round(STATE["config"]["entry_value"]*0.89,2) if result=="WIN" else -STATE["config"]["entry_value"]
    STATE["current_balance"] += profit; STATE["daily_profit"] += profit; STATE["operations"] += 1
    if result=="WIN": STATE["wins"]+=1; STATE["loss_streak"]=0
    else: STATE["losses"]+=1; STATE["loss_streak"]+=1
    trade={"asset":asset,"direction":analysis["action"],"amount":STATE["config"]["entry_value"],"result":result,"profit":profit,"time":datetime.now().strftime("%H:%M:%S"),"strategy":"IA híbrida + risco","confidence":analysis["confidence"],"reason":reason}
    STATE["trades"].insert(0,trade); STATE["trades"]=STATE["trades"][:30]
    if STATE["daily_profit"] >= STATE["config"]["stop_gain"] or STATE["daily_profit"] <= -STATE["config"]["stop_loss"] or STATE["loss_streak"] >= STATE["config"]["max_losses"]:
        STATE["robot"]="paused"

def weekly_data():
    days=["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"]
    wins=[120,180,90,250,200,60,30]
    losses=[40,30,120,50,35,10,5]
    return days,wins,losses

@app.get("/api/health")
def health():
    return {"ok": True, "robot": STATE["robot"], "connected": STATE["connected"], "mode": STATE["account"]}

@app.get("/api/dashboard")
def dashboard():
    maybe_auto_trade()
    cfg=STATE["config"]
    days,wins,losses=weekly_data(); net=[w-l for w,l in zip(wins,losses)]
    ops=max(STATE["operations"], len(STATE["trades"]))
    total_wins=STATE["wins"] or 21; total_losses=STATE["losses"] or 7; operations=total_wins+total_losses
    win_rate=round((total_wins/operations)*100) if operations else 0
    current=STATE["current_balance"]
    daily_profit=round(current-STATE["initial_balance"],2)
    goal_pct=max(0,min(100,round((max(0,daily_profit)/cfg["daily_goal"])*100)))
    opportunities=[]
    for flag,asset in ASSETS:
        a=calc_analysis(asset)
        opportunities.append({"flag":flag,"asset":asset,"probability":a["confidence"],"action":a["action"],"level":"Alta" if a["confidence"]>=75 else "Média" if a["confidence"]>=62 else "Baixa","best_time":a["best_time"],"trend":a["trend"]})
    trades=STATE["trades"] or [{"asset":"EUR/USD","direction":"CALL","amount":2.0,"result":"WIN","profit":1.78,"time":"--:--:--","strategy":"Demo inicial","confidence":78,"reason":"Exemplo"}]
    return {
      "cards":{"initial_balance":STATE["initial_balance"],"current_balance":round(current,2),"balance_pct":round(((current-STATE["initial_balance"])/STATE["initial_balance"])*100,2),"daily_profit":daily_profit,"daily_goal":cfg["daily_goal"],"goal_pct":goal_pct,"win_rate":win_rate,"connected":STATE["connected"],"robot":STATE["robot"]},
      "stats":{"operations":operations,"wins":total_wins,"losses":total_losses,"avg_win":8.81,"loss_streak":STATE["loss_streak"]},
      "performance":{"labels":["00h","03h","06h","09h","12h","15h","18h"],"net":[0,40,70,110,150, daily_profit, daily_profit],"losses":[0,-5,-30,-70,-20,0,0]},
      "weekly":{"goal":cfg["weekly_goal"],"profit":sum(net),"total_wins":sum(wins),"total_losses":sum(losses),"days":days,"wins":wins,"losses":losses,"best_day":"Qui R$ 200,00","worst_day":"Qua -R$ 120,00"},
      "opportunities": opportunities,
      "trades": trades,
      "hours":{"labels":["08h","09h","10h","11h","12h","13h","14h","15h","16h","17h"],"values":[60,65,72,70,55,68,78,82,63,58]},
      "summary":{"Status":"Executando" if STATE["robot"]=="running" else STATE["robot"].title(),"Conta":STATE["account"],"Valor por entrada":brl(cfg["entry_value"]),"Horário de operação":f"{cfg['start_time']} - {cfg['end_time']}","Stop loss diário":brl(cfg["stop_loss"]),"Stop gain diário":brl(cfg["stop_gain"]),"Máx. operações/dia":str(cfg["max_operations"]),"Máx. perdas seguidas":str(cfg["max_losses"]),"Confiança mínima":f"{cfg['min_confidence']}%","Modo de operação":cfg["mode"],"Melhor horário":"15:00 · 82% de acerto","Pior horário":"12:00 · 55% de acerto"}
    }

@app.get("/api/analysis/{asset}")
def analysis(asset: str):
    return calc_analysis(asset.replace("-","/"))

@app.post("/api/iq/login")
def login(payload: LoginPayload):
    if payload.account_type != "PRACTICE":
        raise HTTPException(status_code=403, detail="Conta REAL bloqueada nesta versão. Use PRACTICE/DEMO.")
    STATE["connected"] = True
    STATE["account"] = "DEMO/PRACTICE"
    return {"connected": True, "mode": STATE["account"], "message":"Conectado em modo DEMO/PRACTICE simulado."}

@app.post("/api/iq/logout")
def logout():
    STATE["connected"] = False; STATE["robot"]="stopped"
    return {"connected": False, "robot": STATE["robot"]}

@app.get("/api/config")
def get_config(): return STATE["config"]

@app.post("/api/config")
def save_config(payload: ConfigPayload):
    STATE["config"].update(payload.model_dump())
    return {"ok": True, "config": STATE["config"]}

@app.post("/api/robot/start")
def start():
    if not STATE["connected"]: raise HTTPException(status_code=400, detail="Faça login DEMO antes de iniciar o robô.")
    STATE["robot"]="running"; STATE["started_at"] = datetime.now().isoformat()
    return {"robot": STATE["robot"]}

@app.post("/api/robot/pause")
def pause(): STATE["robot"]="paused"; return {"robot": STATE["robot"]}
@app.post("/api/robot/stop")
def stop(): STATE["robot"]="stopped"; return {"robot": STATE["robot"]}

@app.post("/api/trade/manual/{asset}/{direction}")
def manual_trade(asset: str, direction: str):
    if direction.upper() not in ["CALL","PUT"]: raise HTTPException(status_code=400, detail="Direção inválida")
    analysis=calc_analysis(asset.replace("-","/")); analysis["action"]=direction.upper(); ok, reason=risk_check(analysis)
    if not ok: raise HTTPException(status_code=400, detail=reason)
    result="WIN" if random.random()<0.65 else "LOSS"; profit=round(STATE["config"]["entry_value"]*0.89,2) if result=="WIN" else -STATE["config"]["entry_value"]
    STATE["current_balance"] += profit; STATE["daily_profit"] += profit; STATE["operations"] += 1
    if result=="WIN": STATE["wins"]+=1; STATE["loss_streak"]=0
    else: STATE["losses"]+=1; STATE["loss_streak"]+=1
    trade={"asset":asset.replace("-","/"),"direction":direction.upper(),"amount":STATE["config"]["entry_value"],"result":result,"profit":profit,"time":datetime.now().strftime("%H:%M:%S"),"strategy":"Manual validado","confidence":analysis["confidence"],"reason":reason}
    STATE["trades"].insert(0,trade)
    return trade
