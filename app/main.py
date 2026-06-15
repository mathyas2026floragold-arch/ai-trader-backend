from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import random, math, os

app = FastAPI(title="AI Trader Hub API", version="6.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

STATE = {
    "iq_connected": False,
    "api_online": True,
    "account_type": "PRACTICE",
    "account_email": "",
    "currency": "BRL",
    "balance": 0.0,
    "initial_balance": 0.0,
    "daily_profit": 0.0,
    "robot": "stopped",
    "last_sync": None,
    "last_message": "Aguardando login na IQ Option.",
    "wins": 0,
    "losses": 0,
    "operations": 0,
    "loss_streak": 0,
    "trades": [],
    "logs": [],
    "config": {
        "daily_goal": 0.0,
        "weekly_goal": 0.0,
        "entry_value": 2.0,
        "stop_loss": 0.0,
        "stop_gain": 0.0,
        "max_operations": 20,
        "max_losses": 3,
        "start_time": "08:00",
        "end_time": "18:00",
        "min_confidence": 75,
        "mode": "Balanceado"
    }
}
ASSETS = [("🇺🇸", "EUR/USD"), ("🇬🇧", "GBP/USD"), ("🇺🇸🇯🇵", "USD/JPY"), ("🇦🇺", "AUD/USD"), ("🇺🇸🇨🇦", "USD/CAD")]

class LoginPayload(BaseModel):
    email: str
    password: str
    account_type: str = "PRACTICE"

class ConfigPayload(BaseModel):
    daily_goal: float = Field(ge=0)
    weekly_goal: float = Field(ge=0)
    entry_value: float = Field(gt=0)
    stop_loss: float = Field(ge=0)
    stop_gain: float = Field(ge=0)
    max_operations: int = Field(ge=1, le=300)
    max_losses: int = Field(ge=1, le=30)
    start_time: str
    end_time: str
    min_confidence: int = Field(ge=50, le=98)
    mode: str = "Balanceado"

def log(msg):
    item = {"time": datetime.now().strftime("%H:%M:%S"), "message": msg}
    STATE["logs"].insert(0, item)
    STATE["logs"] = STATE["logs"][:80]
    STATE["last_message"] = msg
    STATE["last_sync"] = datetime.now().strftime("%H:%M:%S")

def get_balance_from_iq_or_demo(email=""):
    # PONTO DE INTEGRAÇÃO REAL:
    # aqui entra o adaptador real da IQ Option no backend, nunca no frontend.
    # Por enquanto, retorna saldo DEMO para não fingir operação real.
    return float(os.getenv("DEMO_BALANCE", "10000"))

def candles(asset="EUR/USD", points=80):
    base = 1.084 if asset != "USD/JPY" else 156.20
    price = base + random.uniform(-0.002, 0.002)
    out = []
    trend = random.choice([-1, 1]) * (0.00003 if asset != "USD/JPY" else 0.003)
    for i in range(points):
        step = trend + (random.uniform(-0.00045, 0.00045) if asset != "USD/JPY" else random.uniform(-0.045, 0.045))
        op = price; cl = price + step; hi = max(op, cl) + abs(step)*.7; lo = min(op, cl) - abs(step)*.7
        price = cl
        out.append({"time": (datetime.now()-timedelta(minutes=points-i)).strftime("%H:%M"), "open": round(op, 5), "high": round(hi, 5), "low": round(lo, 5), "close": round(cl, 5)})
    return out

def analyze(asset="EUR/USD"):
    cs = candles(asset, 90)
    closes = [c["close"] for c in cs]
    ma9 = sum(closes[-9:]) / 9
    ma21 = sum(closes[-21:]) / 21
    change = closes[-1] - closes[-21]
    trend = "ALTA" if ma9 > ma21 and change > 0 else "BAIXA" if ma9 < ma21 and change < 0 else "LATERAL"
    rsi = random.randint(39, 76)
    volatility = abs(max(closes[-20:]) - min(closes[-20:]))
    vol = "ALTA" if volatility > (0.002 if asset != "USD/JPY" else .2) else "MÉDIA" if volatility > (0.001 if asset != "USD/JPY" else .1) else "BAIXA"
    pattern = random.choice(["Pullback", "Rompimento", "Suporte", "Resistência", "Canal", "Sem padrão forte"])
    score = 42
    if trend != "LATERAL": score += 20
    if 45 <= rsi <= 68: score += 12
    if vol == "MÉDIA": score += 12
    if pattern != "Sem padrão forte": score += 10
    score += random.randint(-4, 12)
    confidence = max(45, min(92, score))
    min_conf = STATE["config"]["min_confidence"]
    action = "CALL" if trend == "ALTA" and confidence >= min_conf else "PUT" if trend == "BAIXA" and confidence >= min_conf else "AGUARDAR"
    best_time = random.choice(["09:00-10:30", "10:30-12:00", "14:00-16:00", "15:00-17:00"])
    return {"asset": asset, "trend": trend, "rsi": rsi, "volatility": vol, "pattern": pattern, "confidence": confidence, "action": action, "best_time": best_time, "candles": cs, "reasons": [f"Tendência {trend}", f"RSI {rsi}", f"Volatilidade {vol}", f"Padrão {pattern}"]}

def risk_reason(a):
    cfg = STATE["config"]
    now = datetime.now().strftime("%H:%M")
    if not STATE["iq_connected"]: return False, "IQ Option não conectada"
    if STATE["robot"] != "running": return False, "Robô não está executando"
    if cfg["daily_goal"] > 0 and STATE["daily_profit"] >= cfg["daily_goal"]: return False, "Meta diária atingida"
    if cfg["stop_gain"] > 0 and STATE["daily_profit"] >= cfg["stop_gain"]: return False, "Stop gain atingido"
    if cfg["stop_loss"] > 0 and STATE["daily_profit"] <= -cfg["stop_loss"]: return False, "Stop loss atingido"
    if STATE["operations"] >= cfg["max_operations"]: return False, "Limite de operações atingido"
    if STATE["loss_streak"] >= cfg["max_losses"]: return False, "Limite de perdas seguidas atingido"
    if not (cfg["start_time"] <= now <= cfg["end_time"]): return False, "Fora do horário configurado"
    if a["action"] == "AGUARDAR": return False, "IA aguardando confirmação"
    return True, "Entrada aprovada pelo motor de risco"

def maybe_trade():
    if STATE["robot"] != "running" or not STATE["iq_connected"]: return
    if random.random() > 0.25: return
    a = analyze(random.choice([x[1] for x in ASSETS]))
    ok, reason = risk_reason(a)
    if not ok:
        log("Análise sem entrada: " + reason)
        return
    result = "WIN" if random.random() < (a["confidence"]/110) else "LOSS"
    amount = STATE["config"]["entry_value"]
    profit = round(amount*.89, 2) if result == "WIN" else -amount
    STATE["balance"] += profit
    STATE["daily_profit"] = round(STATE["balance"] - STATE["initial_balance"], 2)
    STATE["operations"] += 1
    if result == "WIN": STATE["wins"] += 1; STATE["loss_streak"] = 0
    else: STATE["losses"] += 1; STATE["loss_streak"] += 1
    t = {"time": datetime.now().strftime("%H:%M:%S"), "asset": a["asset"], "direction": a["action"], "amount": amount, "result": result, "profit": profit, "confidence": a["confidence"], "strategy": "IA + risco", "reason": reason}
    STATE["trades"].insert(0, t); STATE["trades"] = STATE["trades"][:50]
    log(f"Operação {a['action']} {a['asset']} finalizada: {result} {profit:+.2f}")

@app.get("/")
def root(): return {"ok": True, "name": "AI Trader Hub", "version": "6.0.0"}

@app.get("/api/health")
def health(): return {"api_online": True, "iq_connected": STATE["iq_connected"], "robot": STATE["robot"], "last_sync": STATE["last_sync"]}

@app.post("/api/iq/login")
def iq_login(p: LoginPayload):
    if p.account_type != "PRACTICE":
        raise HTTPException(403, "Esta versão está travada em PRACTICE/DEMO por segurança.")
    STATE["iq_connected"] = True
    STATE["account_email"] = p.email
    STATE["account_type"] = "PRACTICE"
    STATE["balance"] = get_balance_from_iq_or_demo(p.email)
    STATE["initial_balance"] = STATE["balance"]
    STATE["daily_profit"] = 0.0
    log(f"IQ Option conectada em PRACTICE. Saldo carregado: {STATE['balance']:.2f}")
    return {"connected": True, "account_type": STATE["account_type"], "balance": STATE["balance"], "currency": STATE["currency"], "message": "Login confirmado em PRACTICE/DEMO."}

@app.post("/api/iq/logout")
def iq_logout():
    STATE["iq_connected"] = False; STATE["robot"] = "stopped"; log("IQ Option desconectada.")
    return {"connected": False, "robot": STATE["robot"]}

@app.get("/api/iq/status")
def iq_status():
    return {"connected": STATE["iq_connected"], "account_type": STATE["account_type"], "balance": STATE["balance"] if STATE["iq_connected"] else 0, "currency": STATE["currency"], "last_sync": STATE["last_sync"], "message": STATE["last_message"]}

@app.get("/api/config")
def get_config(): return STATE["config"]

@app.post("/api/config")
def save_config(p: ConfigPayload):
    STATE["config"].update(p.model_dump()); log("Configurações atualizadas.")
    return {"ok": True, "config": STATE["config"]}

@app.post("/api/robot/start")
def start():
    if not STATE["iq_connected"]: raise HTTPException(400, "Faça login na IQ Option antes de iniciar.")
    STATE["robot"] = "running"; log("Robô iniciado. IA analisando oportunidades.")
    return {"robot": STATE["robot"], "message": STATE["last_message"]}

@app.post("/api/robot/pause")
def pause(): STATE["robot"] = "paused"; log("Robô pausado."); return {"robot": STATE["robot"]}
@app.post("/api/robot/stop")
def stop(): STATE["robot"] = "stopped"; log("Robô parado."); return {"robot": STATE["robot"]}

@app.get("/api/dashboard")
def dashboard():
    maybe_trade()
    cfg = STATE["config"]
    analyses = []
    for flag, asset in ASSETS:
        a = analyze(asset)
        analyses.append({"flag": flag, "asset": asset, "probability": a["confidence"], "action": a["action"], "level": "Alta" if a["confidence"] >= 75 else "Média" if a["confidence"] >= 62 else "Baixa", "best_time": a["best_time"], "trend": a["trend"], "rsi": a["rsi"], "volatility": a["volatility"]})
    best = sorted(analyses, key=lambda x: x["probability"], reverse=True)[0]
    ops = STATE["operations"]
    win_rate = round((STATE["wins"] / ops) * 100) if ops else 0
    goal = cfg["daily_goal"]
    goal_pct = round(max(0, STATE["daily_profit"]) / goal * 100) if goal else 0
    return {
        "cards": {"api_online": True, "iq_connected": STATE["iq_connected"], "robot": STATE["robot"], "account_type": STATE["account_type"], "currency": STATE["currency"], "initial_balance": STATE["initial_balance"] if STATE["iq_connected"] else 0, "current_balance": STATE["balance"] if STATE["iq_connected"] else 0, "daily_profit": STATE["daily_profit"] if STATE["iq_connected"] else 0, "daily_goal": goal, "goal_pct": min(100, goal_pct), "win_rate": win_rate, "last_sync": STATE["last_sync"]},
        "stats": {"operations": STATE["operations"], "wins": STATE["wins"], "losses": STATE["losses"], "loss_streak": STATE["loss_streak"]},
        "performance": {"labels": ["08h", "10h", "12h", "14h", "16h", "18h"], "net": [0, 0, round(STATE["daily_profit"]*.25,2), round(STATE["daily_profit"]*.55,2), STATE["daily_profit"], STATE["daily_profit"]], "losses": [0, 0, -max(0, STATE["losses"])*2, -max(0, STATE["losses"])*2, 0, 0]},
        "opportunities": analyses,
        "best_signal": best,
        "trades": STATE["trades"],
        "logs": STATE["logs"],
        "message": STATE["last_message"]
    }

@app.get("/api/analysis/{asset}")
def analysis(asset: str): return analyze(asset.replace("-", "/"))
@app.get("/api/candles/{asset}")
def get_candles(asset: str): return {"asset": asset.replace("-", "/"), "candles": candles(asset.replace("-", "/"), 90)}
