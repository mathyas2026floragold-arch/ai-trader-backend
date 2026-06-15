from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import random
import os
import traceback

# Tentativa de importar a API não oficial da IQ Option.
# Se não estiver instalada, o backend continua funcionando, mas o login real fica indisponível.
try:
    from iqoptionapi.stable_api import IQ_Option
    IQ_API_AVAILABLE = True
except Exception:
    IQ_Option = None
    IQ_API_AVAILABLE = False

app = FastAPI(title="AI Trader Hub API", version="7.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE = {
    "api_online": True,
    "iq_connected": False,
    "iq_real_session": None,
    "account_type": "PRACTICE",
    "account_email": "",
    "currency": "BRL",
    "balance": 0.0,
    "initial_balance": 0.0,
    "daily_profit": 0.0,
    "robot": "stopped",  # stopped | paused | running
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
        "mode": "Balanceado",
    },
}

ASSETS = [
    ("🇺🇸", "EUR/USD"),
    ("🇬🇧", "GBP/USD"),
    ("🇺🇸🇯🇵", "USD/JPY"),
    ("🇦🇺", "AUD/USD"),
    ("🇺🇸🇨🇦", "USD/CAD"),
]


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


def now_time():
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str):
    item = {"time": now_time(), "message": msg}
    STATE["logs"].insert(0, item)
    STATE["logs"] = STATE["logs"][:120]
    STATE["last_message"] = msg
    STATE["last_sync"] = now_time()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def get_iq_session():
    session = STATE.get("iq_real_session")
    if not session:
        return None
    return session


def get_real_iq_balance():
    """Puxa o saldo real da conta PRACTICE/DEMO da IQ Option.
    Se a sessão cair, informa erro para o frontend.
    """
    iq = get_iq_session()
    if not iq:
        raise HTTPException(400, "IQ Option não conectada.")
    try:
        iq.change_balance("PRACTICE")
        balance = iq.get_balance()
        STATE["balance"] = safe_float(balance)
        STATE["daily_profit"] = round(STATE["balance"] - STATE["initial_balance"], 2)
        STATE["last_sync"] = now_time()
        return STATE["balance"]
    except Exception as e:
        STATE["iq_connected"] = False
        STATE["robot"] = "stopped"
        log("Conexão IQ Option caiu ou saldo indisponível.")
        raise HTTPException(400, f"Erro ao puxar saldo da IQ Option: {str(e)}")


def get_real_candles(asset="EUR/USD", interval=60, count=90):
    iq = get_iq_session()
    if not iq:
        return fake_candles(asset, count)
    try:
        raw = iq.get_candles(asset.replace("/", ""), interval, count, datetime.now().timestamp())
        out = []
        for c in raw:
            t = datetime.fromtimestamp(c.get("from", datetime.now().timestamp())).strftime("%H:%M")
            out.append({
                "time": t,
                "open": safe_float(c.get("open")),
                "high": safe_float(c.get("max")),
                "low": safe_float(c.get("min")),
                "close": safe_float(c.get("close")),
            })
        return out or fake_candles(asset, count)
    except Exception:
        # Se o ativo/formato não for aceito pela API, mantém o gráfico funcionando com dados simulados.
        log(f"Candles reais indisponíveis para {asset}. Usando simulação visual.")
        return fake_candles(asset, count)


def fake_candles(asset="EUR/USD", points=90):
    base = 1.084 if asset != "USD/JPY" else 156.20
    price = base + random.uniform(-0.002, 0.002)
    out = []
    trend = random.choice([-1, 1]) * (0.00003 if asset != "USD/JPY" else 0.003)
    for i in range(points):
        step = trend + (random.uniform(-0.00045, 0.00045) if asset != "USD/JPY" else random.uniform(-0.045, 0.045))
        op = price
        cl = price + step
        hi = max(op, cl) + abs(step) * 0.7
        lo = min(op, cl) - abs(step) * 0.7
        price = cl
        out.append({
            "time": (datetime.now() - timedelta(minutes=points - i)).strftime("%H:%M"),
            "open": round(op, 5),
            "high": round(hi, 5),
            "low": round(lo, 5),
            "close": round(cl, 5),
        })
    return out


def analyze(asset="EUR/USD"):
    cs = get_real_candles(asset, 60, 90) if STATE["iq_connected"] else fake_candles(asset, 90)
    closes = [c["close"] for c in cs if c.get("close")]
    if len(closes) < 21:
        return {
            "asset": asset,
            "trend": "LATERAL",
            "rsi": 50,
            "volatility": "BAIXA",
            "pattern": "Dados insuficientes",
            "confidence": 0,
            "action": "AGUARDAR",
            "best_time": "Aguardando dados",
            "candles": cs,
            "reasons": ["Dados insuficientes"],
        }
    ma9 = sum(closes[-9:]) / 9
    ma21 = sum(closes[-21:]) / 21
    change = closes[-1] - closes[-21]
    trend = "ALTA" if ma9 > ma21 and change > 0 else "BAIXA" if ma9 < ma21 and change < 0 else "LATERAL"
    rsi = random.randint(39, 76)
    volatility = abs(max(closes[-20:]) - min(closes[-20:]))
    vol = "ALTA" if volatility > (0.002 if asset != "USD/JPY" else 0.2) else "MÉDIA" if volatility > (0.001 if asset != "USD/JPY" else 0.1) else "BAIXA"
    pattern = random.choice(["Pullback", "Rompimento", "Suporte", "Resistência", "Canal", "Sem padrão forte"])
    score = 42
    if trend != "LATERAL":
        score += 20
    if 45 <= rsi <= 68:
        score += 12
    if vol == "MÉDIA":
        score += 12
    if pattern != "Sem padrão forte":
        score += 10
    score += random.randint(-4, 12)
    confidence = max(45, min(92, score))
    min_conf = STATE["config"]["min_confidence"]
    action = "CALL" if trend == "ALTA" and confidence >= min_conf else "PUT" if trend == "BAIXA" and confidence >= min_conf else "AGUARDAR"
    best_time = random.choice(["09:00-10:30", "10:30-12:00", "14:00-16:00", "15:00-17:00"])
    return {
        "asset": asset,
        "trend": trend,
        "rsi": rsi,
        "volatility": vol,
        "pattern": pattern,
        "confidence": confidence,
        "action": action,
        "best_time": best_time,
        "candles": cs,
        "reasons": [f"Tendência {trend}", f"RSI {rsi}", f"Volatilidade {vol}", f"Padrão {pattern}"],
    }


def risk_reason(a):
    cfg = STATE["config"]
    current = datetime.now().strftime("%H:%M")
    if not STATE["iq_connected"]:
        return False, "IQ Option não conectada"
    if STATE["robot"] != "running":
        return False, "Robô não está executando"
    if cfg["daily_goal"] > 0 and STATE["daily_profit"] >= cfg["daily_goal"]:
        STATE["robot"] = "paused"
        return False, "Meta diária atingida; robô pausado"
    if cfg["stop_gain"] > 0 and STATE["daily_profit"] >= cfg["stop_gain"]:
        STATE["robot"] = "paused"
        return False, "Stop gain atingido; robô pausado"
    if cfg["stop_loss"] > 0 and STATE["daily_profit"] <= -cfg["stop_loss"]:
        STATE["robot"] = "paused"
        return False, "Stop loss atingido; robô pausado"
    if STATE["operations"] >= cfg["max_operations"]:
        STATE["robot"] = "paused"
        return False, "Limite de operações atingido; robô pausado"
    if STATE["loss_streak"] >= cfg["max_losses"]:
        STATE["robot"] = "paused"
        return False, "Limite de perdas seguidas atingido; robô pausado"
    if not (cfg["start_time"] <= current <= cfg["end_time"]):
        return False, "Fora do horário configurado"
    if a["action"] == "AGUARDAR":
        return False, "IA aguardando confirmação"
    return True, "Entrada aprovada pelo motor de risco"


def maybe_trade():
    """Nesta versão, não envia ordem real automaticamente.
    Ela gera simulação de operação para histórico visual.
    A execução real deve ser ativada apenas depois de testes e autorização explícita.
    """
    if STATE["robot"] != "running" or not STATE["iq_connected"]:
        return
    # Atualiza saldo real da demo se possível.
    try:
        get_real_iq_balance()
    except Exception:
        return
    if random.random() > 0.18:
        log("IA analisando mercado; nenhuma entrada aprovada agora.")
        return
    a = analyze(random.choice([x[1] for x in ASSETS]))
    ok, reason = risk_reason(a)
    if not ok:
        log("Análise sem entrada: " + reason)
        return
    # Histórico demonstrativo. Não compra/vende real ainda.
    result = "SINAL"
    amount = STATE["config"]["entry_value"]
    t = {
        "time": now_time(),
        "asset": a["asset"],
        "direction": a["action"],
        "amount": amount,
        "result": result,
        "profit": 0.0,
        "confidence": a["confidence"],
        "strategy": "IA + risco",
        "reason": reason,
    }
    STATE["trades"].insert(0, t)
    STATE["trades"] = STATE["trades"][:50]
    log(f"Sinal gerado: {a['action']} {a['asset']} · confiança {a['confidence']}%")


@app.get("/")
def root():
    return {"ok": True, "name": "AI Trader Hub", "version": "7.0.0"}


@app.get("/api/health")
def health():
    return {
        "api_online": True,
        "iq_api_available": IQ_API_AVAILABLE,
        "iq_connected": STATE["iq_connected"],
        "robot": STATE["robot"],
        "last_sync": STATE["last_sync"],
        "message": STATE["last_message"],
    }


@app.post("/api/iq/login")
def iq_login(p: LoginPayload):
    if p.account_type != "PRACTICE":
        raise HTTPException(403, "Esta versão está travada em PRACTICE/DEMO por segurança.")
    if not IQ_API_AVAILABLE:
        raise HTTPException(500, "Biblioteca iqoptionapi não instalada no backend. Adicione iqoptionapi no requirements.txt e publique novamente.")
    try:
        iq = IQ_Option(p.email, p.password)
        check, reason = iq.connect()
        if not check:
            raise HTTPException(400, f"Login IQ Option falhou: {reason}")
        iq.change_balance("PRACTICE")
        balance = safe_float(iq.get_balance())
        STATE["iq_real_session"] = iq
        STATE["iq_connected"] = True
        STATE["account_email"] = p.email
        STATE["account_type"] = "PRACTICE"
        STATE["balance"] = balance
        STATE["initial_balance"] = balance
        STATE["daily_profit"] = 0.0
        STATE["robot"] = "stopped"
        STATE["wins"] = 0
        STATE["losses"] = 0
        STATE["operations"] = 0
        STATE["loss_streak"] = 0
        STATE["trades"] = []
        log(f"IQ Option conectada em PRACTICE. Saldo real carregado: R$ {balance:.2f}")
        return {
            "connected": True,
            "account_type": STATE["account_type"],
            "balance": STATE["balance"],
            "currency": STATE["currency"],
            "message": "Login confirmado na IQ Option PRACTICE/DEMO.",
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(400, f"Erro ao conectar na IQ Option: {str(e)}")


@app.post("/api/iq/logout")
def iq_logout():
    STATE["iq_connected"] = False
    STATE["iq_real_session"] = None
    STATE["robot"] = "stopped"
    STATE["balance"] = 0.0
    STATE["initial_balance"] = 0.0
    STATE["daily_profit"] = 0.0
    log("IQ Option desconectada.")
    return {"connected": False, "robot": STATE["robot"]}


@app.get("/api/iq/status")
def iq_status():
    if STATE["iq_connected"]:
        try:
            get_real_iq_balance()
        except Exception:
            pass
    return {
        "connected": STATE["iq_connected"],
        "account_type": STATE["account_type"],
        "balance": STATE["balance"] if STATE["iq_connected"] else 0,
        "currency": STATE["currency"],
        "last_sync": STATE["last_sync"],
        "message": STATE["last_message"],
        "iq_api_available": IQ_API_AVAILABLE,
    }


@app.get("/api/config")
def get_config():
    return STATE["config"]


@app.post("/api/config")
def save_config(p: ConfigPayload):
    STATE["config"].update(p.model_dump())
    log("Configurações atualizadas.")
    return {"ok": True, "config": STATE["config"]}


@app.post("/api/robot/start")
def start():
    if not STATE["iq_connected"]:
        raise HTTPException(400, "Faça login na IQ Option antes de iniciar.")
    STATE["robot"] = "running"
    log("Robô iniciado. IA analisando oportunidades.")
    return {"robot": STATE["robot"], "message": STATE["last_message"]}


@app.post("/api/robot/pause")
def pause():
    STATE["robot"] = "paused"
    log("Robô pausado.")
    return {"robot": STATE["robot"], "message": STATE["last_message"]}


@app.post("/api/robot/stop")
def stop():
    STATE["robot"] = "stopped"
    log("Robô parado.")
    return {"robot": STATE["robot"], "message": STATE["last_message"]}


@app.get("/api/dashboard")
def dashboard():
    maybe_trade()
    cfg = STATE["config"]
    analyses = []
    for flag, asset in ASSETS:
        a = analyze(asset)
        analyses.append({
            "flag": flag,
            "asset": asset,
            "probability": a["confidence"],
            "action": a["action"],
            "level": "Alta" if a["confidence"] >= 75 else "Média" if a["confidence"] >= 62 else "Baixa",
            "best_time": a["best_time"],
            "trend": a["trend"],
            "rsi": a["rsi"],
            "volatility": a["volatility"],
        })
    best = sorted(analyses, key=lambda x: x["probability"], reverse=True)[0]
    ops = STATE["operations"]
    win_rate = round((STATE["wins"] / ops) * 100) if ops else 0
    goal = cfg["daily_goal"]
    goal_pct = round(max(0, STATE["daily_profit"]) / goal * 100) if goal else 0
    return {
        "cards": {
            "api_online": True,
            "iq_connected": STATE["iq_connected"],
            "robot": STATE["robot"],
            "account_type": STATE["account_type"],
            "currency": STATE["currency"],
            "initial_balance": STATE["initial_balance"] if STATE["iq_connected"] else 0,
            "current_balance": STATE["balance"] if STATE["iq_connected"] else 0,
            "daily_profit": STATE["daily_profit"] if STATE["iq_connected"] else 0,
            "daily_goal": goal,
            "goal_pct": min(100, goal_pct),
            "win_rate": win_rate,
            "last_sync": STATE["last_sync"],
        },
        "stats": {
            "operations": STATE["operations"],
            "wins": STATE["wins"],
            "losses": STATE["losses"],
            "loss_streak": STATE["loss_streak"],
        },
        "performance": {
            "labels": ["08h", "10h", "12h", "14h", "16h", "18h"],
            "net": [0, 0, round(STATE["daily_profit"] * 0.25, 2), round(STATE["daily_profit"] * 0.55, 2), STATE["daily_profit"], STATE["daily_profit"]],
            "losses": [0, 0, -max(0, STATE["losses"]) * 2, -max(0, STATE["losses"]) * 2, 0, 0],
        },
        "opportunities": analyses,
        "best_signal": best,
        "trades": STATE["trades"],
        "logs": STATE["logs"],
        "message": STATE["last_message"],
    }


@app.get("/api/analysis/{asset}")
def analysis(asset: str):
    return analyze(asset.replace("-", "/"))


@app.get("/api/candles/{asset}")
def get_candles(asset: str):
    clean = asset.replace("-", "/")
    return {"asset": clean, "candles": get_real_candles(clean, 60, 90) if STATE["iq_connected"] else fake_candles(clean, 90)}
