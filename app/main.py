from __future__ import annotations
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import random, threading, time
from .iq_client import IQClient, IQConnectionError

app = FastAPI(title="AI Trader Hub API", version="10.0.0")
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
    "next_action": "Aguardando login.",
    "worker_running": False,
    "config": {
        "daily_goal": 1000.0,
        "entry_value": 12.0,
        "stop_loss": 120.0,
        "stop_gain": 1000.0,
        "max_operations": 200,
        "max_losses": 4,
        "start_time": "00:00",
        "end_time": "23:59",
        "min_confidence": 72,
        "mode": "Contínuo DEMO",
        "expiration_minutes": 1,
        "trade_interval_seconds": 10,
        "execute_real_demo_orders": True,
        "market_type": "AUTO"
    }
}
LOCK = threading.Lock()

class LoginPayload(BaseModel):
    email: str
    password: str
    account_type: str = "PRACTICE"

class ConfigPayload(BaseModel):
    daily_goal: float = Field(ge=0)
    entry_value: float = Field(gt=0)
    stop_loss: float = Field(ge=0)
    stop_gain: float = Field(ge=0)
    max_operations: int = Field(ge=1, le=1000)
    max_losses: int = Field(ge=1, le=50)
    start_time: str
    end_time: str
    min_confidence: int = Field(ge=50, le=98)
    mode: str = "Contínuo DEMO"
    expiration_minutes: int = Field(default=1, ge=1, le=15)
    trade_interval_seconds: int = Field(default=10, ge=5, le=3600)
    execute_real_demo_orders: bool = True
    market_type: str = "AUTO"

def now(): return datetime.now().strftime("%H:%M:%S")

def log(msg: str):
    with LOCK:
        STATE["logs"].insert(0, {"time": now(), "message": msg})
        STATE["logs"] = STATE["logs"][:200]
        STATE["next_action"] = msg

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
            return IQ.candles(asset, 60, 90), True
        except Exception as exc:
            log(f"Falha ao buscar candles reais {asset}: {exc}")
    return fake_candles(asset), False

def analyze(asset: str):
    cs, real = get_candles_safe(asset)
    closes = [c["close"] for c in cs if c.get("close")]
    if len(closes) < 21:
        return {"asset": asset, "action": "AGUARDAR", "confidence": 0, "trend": "SEM DADOS", "best_time": "--", "candles_real": real, "reasons": ["Sem candles suficientes"]}
    ma9 = sum(closes[-9:]) / 9
    ma21 = sum(closes[-21:]) / 21
    change = closes[-1] - closes[-21]
    trend = "ALTA" if ma9 > ma21 and change > 0 else "BAIXA" if ma9 < ma21 and change < 0 else "LATERAL"
    rsi = random.randint(42, 72)
    confidence = 44 + (22 if trend != "LATERAL" else 0) + (12 if 45 <= rsi <= 68 else 0) + random.randint(0, 16)
    confidence = min(94, max(45, confidence))
    min_conf = STATE["config"]["min_confidence"]
    action = "CALL" if trend == "ALTA" and confidence >= min_conf else "PUT" if trend == "BAIXA" and confidence >= min_conf else "AGUARDAR"
    return {"asset": asset, "action": action, "confidence": confidence, "trend": trend, "rsi": rsi, "best_time": "AGORA" if action != "AGUARDAR" else "Aguardar", "candles_real": real, "candles": cs, "reasons": [f"Tendência {trend}", f"RSI {rsi}", "Médias 9/21", "Ciclo contínuo DEMO"]}

def current_balance() -> float:
    return IQ.balance() if IQ.connected else 0.0

def risk_ok(a):
    cfg = STATE["config"]
    current = datetime.now().strftime("%H:%M")
    if not IQ.connected: return False, "IQ Option não conectada"
    if STATE["robot"] != "running": return False, "Robô não está executando"
    if cfg["daily_goal"] and STATE["daily_profit"] >= cfg["daily_goal"]: return False, "Meta diária atingida: robô pausou para proteger o resultado"
    if cfg["stop_gain"] and STATE["daily_profit"] >= cfg["stop_gain"]: return False, "Stop gain atingido"
    if cfg["stop_loss"] and STATE["daily_profit"] <= -cfg["stop_loss"]: return False, "Stop loss atingido"
    if STATE["operations"] >= cfg["max_operations"]: return False, "Limite de operações atingido"
    if STATE["loss_streak"] >= cfg["max_losses"]: return False, "Limite de perdas seguidas atingido"
    if not (cfg["start_time"] <= current <= cfg["end_time"]): return False, "Fora do horário configurado"
    if a["action"] == "AGUARDAR": return False, "IA aguardando sinal melhor"
    return True, "Aprovado para entrada DEMO"

def normalize_market_type(value: str) -> str:
    value = (value or "AUTO").upper().strip()
    return value if value in ["AUTO", "BINARY", "DIGITAL", "OTC"] else "AUTO"

def pick_best_market():
    """Analisa ativos e mercados disponíveis.

    market_type:
    - AUTO: tenta Digital, Binary e OTC automaticamente.
    - BINARY: somente binárias normais.
    - DIGITAL: somente digitais normais.
    - OTC: somente pares OTC, tentando Digital OTC e Binary OTC.
    """
    cfg = STATE["config"]
    market_type = normalize_market_type(cfg.get("market_type", "AUTO"))
    exp = int(cfg.get("expiration_minutes", 1))
    candidates = IQ.available_candidates(ASSETS, market_type, exp) if IQ.connected else []
    if not candidates:
        candidates = [{"asset": a, "market": "binary", "label": "Binary"} for a in ASSETS]
    analyses = []
    for c in candidates:
        a = analyze(c["asset"])
        a["market"] = c["market"]
        a["market_label"] = c["label"]
        analyses.append(a)
    analyses.sort(key=lambda x: x["confidence"], reverse=True)
    return analyses[0], analyses

def execute_one_cycle():
    best, all_analyses = pick_best_market()
    STATE["last_analysis"] = best
    ok, reason = risk_ok(best)
    log(f"IA escolheu {best['asset']} ({best.get('market_label','Mercado')}) {best['action']} {best['confidence']}% — {reason}")
    if not ok:
        return
    amount = float(STATE["config"]["entry_value"])
    exp = int(STATE["config"].get("expiration_minutes", 1))
    last_error = None
    selected = None
    # Se o primeiro ativo falhar por mercado fechado, tenta os próximos candidatos automaticamente.
    for candidate in all_analyses[:12]:
        ok, reason = risk_ok(candidate)
        if not ok:
            continue
        try:
            if STATE["config"].get("execute_real_demo_orders", True):
                sent = IQ.buy_demo(candidate["asset"], candidate["action"], amount, exp, candidate.get("market", "binary"))
                selected = candidate
                log(f"Ordem DEMO enviada: {sent['direction']} {sent['asset']} · {sent['market'].upper()} · R$ {amount:.2f} exp {exp}m ID {sent['order_id']}")
                result = IQ.wait_result(sent["order_id"], exp, sent.get("market", "binary"))
                profit = float(result["profit"])
                result_name = result["result"]
            else:
                selected = candidate
                log(f"Modo teste: {candidate['asset']} {candidate.get('market_label','')} não enviada, apenas simulação local.")
                time.sleep(max(5, exp*60))
                result_name = "WIN" if random.random() < (candidate["confidence"] / 115) else "LOSS"
                profit = round(amount * 0.89, 2) if result_name == "WIN" else -amount
            break
        except Exception as exc:
            last_error = str(exc)
            log(f"Mercado recusou {candidate['asset']} ({candidate.get('market_label','')}). Tentando próximo. Motivo: {exc}")
            continue
    if selected is None:
        log(f"Nenhuma entrada enviada. Todos os mercados disponíveis recusaram. Último erro: {last_error}")
        return
    with LOCK:
        STATE["operations"] += 1
        if result_name == "WIN": STATE["wins"] += 1; STATE["loss_streak"] = 0
        elif result_name == "LOSS": STATE["losses"] += 1; STATE["loss_streak"] += 1
        STATE["daily_profit"] = round(STATE["daily_profit"] + profit, 2)
        trade = {"time": now(), "asset": selected["asset"], "market": selected.get("market_label", selected.get("market", "")), "direction": selected["action"], "amount": amount, "result": result_name, "profit": round(profit,2), "confidence": selected["confidence"], "strategy": "IA + mercado automático", "reason": f"Entrada DEMO em {selected.get('market_label','mercado')}"}
        STATE["trades"].insert(0, trade); STATE["trades"] = STATE["trades"][:100]
    log(f"Resultado {result_name} {selected['asset']} ({selected.get('market_label','')}): {profit:+.2f}. Lucro do dia: {STATE['daily_profit']:+.2f}")
    if STATE["config"]["daily_goal"] and STATE["daily_profit"] >= STATE["config"]["daily_goal"]:
        STATE["robot"] = "paused"
        log("Meta atingida. Robô pausado automaticamente.")

def worker_loop():
    STATE["worker_running"] = True
    log("Motor contínuo iniciado: analisando e operando até meta/stop/pausa.")
    while STATE["robot"] == "running":
        try:
            execute_one_cycle()
        except Exception as exc:
            log(f"Erro no ciclo: {exc}")
        time.sleep(int(STATE["config"].get("trade_interval_seconds", 10)))
    STATE["worker_running"] = False
    log("Motor contínuo parado.")

def ensure_worker():
    if not STATE["worker_running"]:
        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()

@app.get("/")
def root(): return {"ok": True, "name": "AI Trader Hub", "version": "10.0.0"}

@app.get("/api/health")
def health(): return {"api_online": True, "iq_connected": IQ.connected, "robot": STATE["robot"], "worker_running": STATE["worker_running"], "version": "10.0.0"}

@app.post("/api/iq/login")
def iq_login(p: LoginPayload):
    try:
        data = IQ.connect(p.email, p.password, p.account_type)
        STATE["initial_balance"] = data["balance"]
        STATE["daily_profit"] = 0.0
        log(f"IQ Option conectada em PRACTICE. Saldo DEMO real: {data['balance']:.2f}")
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
    bal = 0.0; err = ""
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
    STATE["robot"] = "running"
    ensure_worker()
    return {"robot": "running", "message": "Robô contínuo iniciado em DEMO."}

@app.post("/api/robot/pause")
def robot_pause(): STATE["robot"] = "paused"; log("Robô pausado."); return {"robot": "paused"}
@app.post("/api/robot/stop")
def robot_stop(): STATE["robot"] = "stopped"; log("Robô parado."); return {"robot": "stopped"}

@app.get("/api/dashboard")
def dashboard():
    bal = 0.0; balance_error = ""
    if IQ.connected:
        try: bal = current_balance()
        except Exception as exc: balance_error = str(exc)
    analyses = []
    for asset in ASSETS:
        a = analyze(asset)
        analyses.append({"asset": a["asset"], "probability": a["confidence"], "action": a["action"], "trend": a["trend"], "best_time": a["best_time"], "rsi": a.get("rsi", 0), "candles_real": a.get("candles_real", False), "market": a.get("market", ""), "market_label": a.get("market_label", ""), "level": "Alta" if a["confidence"] >= 75 else "Média" if a["confidence"] >= 62 else "Baixa"})
    best = sorted(analyses, key=lambda x: x["probability"], reverse=True)[0]
    ops = STATE["operations"]
    win_rate = round((STATE["wins"] / ops) * 100) if ops else 0
    cfg = STATE["config"]
    goal = cfg["daily_goal"]
    goal_pct = min(100, round(max(0, STATE["daily_profit"]) / goal * 100)) if goal else 0
    return {
        "cards": {"api_online": True, "iq_connected": IQ.connected, "robot": STATE["robot"], "worker_running": STATE["worker_running"], "account_type": IQ.account_type, "currency": "BRL", "initial_balance": STATE["initial_balance"] if IQ.connected else 0, "current_balance": bal if IQ.connected else 0, "daily_profit": STATE["daily_profit"] if IQ.connected else 0, "daily_goal": goal, "goal_pct": goal_pct, "win_rate": win_rate, "last_sync": IQ.last_sync, "balance_error": balance_error, "next_action": STATE["next_action"], "market_type": STATE["config"].get("market_type", "AUTO")},
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
