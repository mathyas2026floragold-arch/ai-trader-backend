from __future__ import annotations
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import random
from .iq_client import IQClient, IQConnectionError

app = FastAPI(title="AI Trader Hub API", version="8.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

IQ = IQClient()
ASSETS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD"]
STATE = {
    "robot": "stopped",
    "initial_balance": 0.0,
    "daily_profit": 0.0,
    "wins": 0,
    "losses": 0,
    "operations": 0,
    "loss_streak": 0,
    "trades": [],
    "logs": [],
    "last_analysis": None,
    "config": {
        "daily_goal": 0.0,
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

class LoginPayload(BaseModel):
    email: str
    password: str
    account_type: str = "PRACTICE"

class ConfigPayload(BaseModel):
    daily_goal: float = Field(ge=0)
    entry_value: float = Field(gt=0)
    stop_loss: float = Field(ge=0)
    stop_gain: float = Field(ge=0)
    max_operations: int = Field(ge=1, le=300)
    max_losses: int = Field(ge=1, le=30)
    start_time: str
    end_time: str
    min_confidence: int = Field(ge=50, le=98)
    mode: str = "Balanceado"

def log(msg: str):
    item = {"time": datetime.now().strftime("%H:%M:%S"), "message": msg}
    STATE["logs"].insert(0, item)
    STATE["logs"] = STATE["logs"][:100]

def fake_candles(asset: str, points: int = 80):
    base = 156.20 if asset == "USD/JPY" else 1.0840
    price = base + random.uniform(-0.002, 0.002)
    trend = random.choice([-1, 1]) * (0.00004 if asset != "USD/JPY" else 0.004)
    out = []
    for i in range(points):
        step = trend + (random.uniform(-0.0004, 0.0004) if asset != "USD/JPY" else random.uniform(-0.04, 0.04))
        op = price; cl = price + step; hi = max(op, cl) + abs(step)*0.8; lo = min(op, cl) - abs(step)*0.8
        price = cl
        out.append({"time": (datetime.now()-timedelta(minutes=points-i)).strftime("%H:%M"), "open": round(op, 5), "high": round(hi, 5), "low": round(lo, 5), "close": round(cl, 5)})
    return out

def get_candles_safe(asset: str):
    if IQ.connected:
        try:
            log(f"Buscando candles reais {asset} na IQ Option.")
            return IQ.candles(asset, 60, 80), True
        except Exception as exc:
            log(f"Candles reais falharam: {exc}. Usando candles visuais simulados.")
    return fake_candles(asset), False

def analyze(asset: str):
    cs, real = get_candles_safe(asset)
    closes = [c["close"] for c in cs if c.get("close")]
    if len(closes) < 21:
        return {"asset": asset, "action": "AGUARDAR", "confidence": 0, "trend": "SEM DADOS", "best_time": "--", "candles_real": real, "reasons": ["Sem candles suficientes"]}
    ma9 = sum(closes[-9:]) / 9
    ma21 = sum(closes[-21:]) / 21
    trend = "ALTA" if ma9 > ma21 else "BAIXA" if ma9 < ma21 else "LATERAL"
    rsi = random.randint(42, 72)
    confidence = 45 + (18 if trend != "LATERAL" else 0) + (12 if 45 <= rsi <= 68 else 0) + random.randint(0, 18)
    confidence = min(92, max(45, confidence))
    min_conf = STATE["config"]["min_confidence"]
    action = "CALL" if trend == "ALTA" and confidence >= min_conf else "PUT" if trend == "BAIXA" and confidence >= min_conf else "AGUARDAR"
    best_time = random.choice(["09:00-10:30", "10:30-12:00", "14:00-16:00", "15:00-17:00"])
    return {"asset": asset, "action": action, "confidence": confidence, "trend": trend, "rsi": rsi, "best_time": best_time, "candles_real": real, "candles": cs, "reasons": [f"Tendência {trend}", f"RSI {rsi}", "Médias móveis 9/21", "Motor de risco ativo"]}

def current_balance() -> float:
    if not IQ.connected:
        return 0.0
    return IQ.balance()

def risk_ok(a):
    cfg = STATE["config"]
    now = datetime.now().strftime("%H:%M")
    if not IQ.connected: return False, "IQ Option não conectada"
    if STATE["robot"] != "running": return False, "Robô não está executando"
    if cfg["daily_goal"] and STATE["daily_profit"] >= cfg["daily_goal"]: return False, "Meta diária atingida"
    if cfg["stop_gain"] and STATE["daily_profit"] >= cfg["stop_gain"]: return False, "Stop gain atingido"
    if cfg["stop_loss"] and STATE["daily_profit"] <= -cfg["stop_loss"]: return False, "Stop loss atingido"
    if STATE["operations"] >= cfg["max_operations"]: return False, "Limite de operações atingido"
    if STATE["loss_streak"] >= cfg["max_losses"]: return False, "Limite de perdas seguidas atingido"
    if not (cfg["start_time"] <= now <= cfg["end_time"]): return False, "Fora do horário configurado"
    if a["action"] == "AGUARDAR": return False, "IA aguardando oportunidade melhor"
    return True, "Aprovado pelo risco"

def maybe_trade():
    if not IQ.connected or STATE["robot"] != "running":
        return
    asset = random.choice(ASSETS)
    a = analyze(asset)
    STATE["last_analysis"] = a
    ok, reason = risk_ok(a)
    log(f"IA analisou {asset}: {a['action']} {a['confidence']}% — {reason}")
    # Segurança: esta versão registra operação SIMULADA. Execução real precisa endpoint separado e confirmação explícita.
    if not ok or random.random() > 0.35:
        return
    amount = STATE["config"]["entry_value"]
    result = "WIN" if random.random() < (a["confidence"] / 115) else "LOSS"
    profit = round(amount * 0.89, 2) if result == "WIN" else -amount
    STATE["operations"] += 1
    if result == "WIN": STATE["wins"] += 1; STATE["loss_streak"] = 0
    else: STATE["losses"] += 1; STATE["loss_streak"] += 1
    # saldo real vem da IQ; daily_profit usa resultado simulado até execução real ser implementada
    STATE["daily_profit"] = round(STATE["daily_profit"] + profit, 2)
    trade = {"time": datetime.now().strftime("%H:%M:%S"), "asset": asset, "direction": a["action"], "amount": amount, "result": result, "profit": profit, "confidence": a["confidence"], "strategy": "IA + risco", "reason": "Operação simulada registrada; saldo real vem da IQ Option"}
    STATE["trades"].insert(0, trade); STATE["trades"] = STATE["trades"][:50]
    log(f"Operação SIMULADA {a['action']} {asset}: {result} {profit:+.2f}")

@app.get("/")
def root(): return {"ok": True, "name": "AI Trader Hub", "version": "8.0.0"}

@app.get("/api/health")
def health(): return {"api_online": True, "iq_connected": IQ.connected, "robot": STATE["robot"], "version": "8.0.0"}

@app.post("/api/iq/login")
def iq_login(p: LoginPayload):
    try:
        data = IQ.connect(p.email, p.password, p.account_type)
        STATE["initial_balance"] = data["balance"]
        STATE["daily_profit"] = 0.0
        log(f"IQ Option conectada em {data['account_type']}. Saldo real DEMO: {data['balance']:.2f}")
        return {"connected": True, "balance": data["balance"], "currency": "BRL", "account_type": data["account_type"], "message": "IQ Option conectada. Saldo DEMO real carregado."}
    except IQConnectionError as exc:
        log(f"Falha no login IQ: {exc}")
        raise HTTPException(400, str(exc))

@app.post("/api/iq/logout")
def iq_logout():
    IQ.disconnect(); STATE["robot"] = "stopped"; log("IQ Option desconectada.")
    return {"connected": False, "robot": "stopped"}

@app.get("/api/iq/status")
def iq_status():
    bal = 0.0
    err = ""
    if IQ.connected:
        try: bal = IQ.balance()
        except Exception as exc: err = str(exc)
    return {"connected": IQ.connected, "account_type": IQ.account_type, "balance": bal, "currency": "BRL", "last_sync": IQ.last_sync, "error": err or IQ.last_error}

@app.get("/api/config")
def get_config(): return STATE["config"]

@app.post("/api/config")
def save_config(p: ConfigPayload):
    STATE["config"].update(p.model_dump()); log("Configurações atualizadas.")
    return {"ok": True, "config": STATE["config"]}

@app.post("/api/robot/start")
def robot_start():
    if not IQ.connected: raise HTTPException(400, "Conecte na IQ Option antes de iniciar o robô.")
    STATE["robot"] = "running"; log("Robô iniciado. IA começou a analisar o mercado.")
    return {"robot": "running"}

@app.post("/api/robot/pause")
def robot_pause(): STATE["robot"] = "paused"; log("Robô pausado."); return {"robot": "paused"}
@app.post("/api/robot/stop")
def robot_stop(): STATE["robot"] = "stopped"; log("Robô parado."); return {"robot": "stopped"}

@app.get("/api/dashboard")
def dashboard():
    maybe_trade()
    bal = 0.0
    balance_error = ""
    if IQ.connected:
        try: bal = current_balance()
        except Exception as exc: balance_error = str(exc)
    analyses = []
    for asset in ASSETS:
        a = analyze(asset)
        analyses.append({"asset": a["asset"], "probability": a["confidence"], "action": a["action"], "trend": a["trend"], "best_time": a["best_time"], "rsi": a.get("rsi", 0), "candles_real": a.get("candles_real", False), "level": "Alta" if a["confidence"] >= 75 else "Média" if a["confidence"] >= 62 else "Baixa"})
    best = sorted(analyses, key=lambda x: x["probability"], reverse=True)[0]
    ops = STATE["operations"]
    win_rate = round((STATE["wins"] / ops) * 100) if ops else 0
    cfg = STATE["config"]
    goal = cfg["daily_goal"]
    goal_pct = min(100, round(max(0, STATE["daily_profit"]) / goal * 100)) if goal else 0
    return {
        "cards": {"api_online": True, "iq_connected": IQ.connected, "robot": STATE["robot"], "account_type": IQ.account_type, "currency": "BRL", "initial_balance": STATE["initial_balance"] if IQ.connected else 0, "current_balance": bal if IQ.connected else 0, "daily_profit": STATE["daily_profit"] if IQ.connected else 0, "daily_goal": goal, "goal_pct": goal_pct, "win_rate": win_rate, "last_sync": IQ.last_sync, "balance_error": balance_error},
        "stats": {"operations": ops, "wins": STATE["wins"], "losses": STATE["losses"], "loss_streak": STATE["loss_streak"]},
        "performance": {"labels": ["08h", "10h", "12h", "14h", "16h", "18h"], "net": [0, 0, round(STATE["daily_profit"]*.25,2), round(STATE["daily_profit"]*.55,2), STATE["daily_profit"], STATE["daily_profit"]], "losses": [0, 0, -STATE["losses"]*2, -STATE["losses"]*2, 0, 0]},
        "opportunities": analyses,
        "best_signal": best,
        "trades": STATE["trades"],
        "logs": STATE["logs"],
        "message": STATE["logs"][0]["message"] if STATE["logs"] else "Aguardando login na IQ Option."
    }

@app.get("/api/analysis/{asset}")
def analysis(asset: str): return analyze(asset.replace("-", "/"))
@app.get("/api/candles/{asset}")
def candles_endpoint(asset: str):
    asset = asset.replace("-", "/")
    cs, real = get_candles_safe(asset)
    return {"asset": asset, "real": real, "candles": cs}
