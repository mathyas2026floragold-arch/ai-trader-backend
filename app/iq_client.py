from __future__ import annotations
from typing import Any, Optional
from datetime import datetime
import time

class IQConnectionError(Exception):
    pass

class IQClient:
    def __init__(self) -> None:
        self.api: Optional[Any] = None
        self.email: str = ""
        self.account_type: str = "PRACTICE"
        self.connected: bool = False
        self.last_error: str = ""
        self.last_sync: str | None = None

    def connect(self, email: str, password: str, account_type: str = "PRACTICE") -> dict:
        account_type = (account_type or "PRACTICE").upper().strip()
        if account_type not in ["PRACTICE", "REAL"]:
            raise IQConnectionError("Tipo de conta inválido. Use PRACTICE ou REAL.")
        if account_type == "REAL":
            raise IQConnectionError("Conta REAL bloqueada nesta versão. Use apenas PRACTICE/DEMO.")
        try:
            from iqoptionapi.stable_api import IQ_Option  # type: ignore
        except Exception as exc:
            raise IQConnectionError("Biblioteca iqoptionapi não instalada no servidor.") from exc
        try:
            iq = IQ_Option(email, password)
            check, reason = iq.connect()
            if not check:
                raise IQConnectionError(f"Falha no login IQ Option: {reason}")
            iq.change_balance("PRACTICE")
            time.sleep(1)
            balance = float(iq.get_balance())
            self.api = iq
            self.email = email
            self.account_type = "PRACTICE"
            self.connected = True
            self.last_error = ""
            self.last_sync = datetime.now().strftime("%H:%M:%S")
            return {"connected": True, "balance": balance, "account_type": "PRACTICE", "last_sync": self.last_sync}
        except IQConnectionError:
            self.connected = False
            raise
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            raise IQConnectionError(f"Erro ao conectar na IQ Option: {exc}") from exc

    def disconnect(self) -> None:
        self.api = None
        self.connected = False
        self.email = ""
        self.last_sync = datetime.now().strftime("%H:%M:%S")

    def _require(self) -> None:
        if not self.connected or not self.api:
            raise IQConnectionError("IQ Option não conectada.")

    def balance(self) -> float:
        self._require()
        try:
            bal = float(self.api.get_balance())
            self.last_sync = datetime.now().strftime("%H:%M:%S")
            return bal
        except Exception as exc:
            self.last_error = str(exc)
            raise IQConnectionError(f"Falha ao atualizar saldo: {exc}") from exc

    def to_iq_active(self, asset: str) -> str:
        s = asset.upper().strip().replace(" ", "")
        otc = s.endswith("OTC") or "-OTC" in s
        s = s.replace("OTC", "").replace("/", "").replace("-", "")
        return f"{s}-OTC" if otc else s

    def display_asset(self, iq_active: str) -> str:
        otc = iq_active.endswith("-OTC")
        raw = iq_active.replace("-OTC", "")
        pairs = {"EURUSD":"EUR/USD", "GBPUSD":"GBP/USD", "USDJPY":"USD/JPY", "AUDUSD":"AUD/USD", "USDCAD":"USD/CAD", "EURGBP":"EUR/GBP"}
        return pairs.get(raw, raw) + (" OTC" if otc else "")

    def candles(self, asset: str, interval: int = 60, count: int = 80) -> list[dict]:
        self._require()
        try:
            active = self.to_iq_active(asset)
            raw = self.api.get_candles(active, interval, count, time.time())
            out = []
            for c in raw or []:
                out.append({
                    "time": datetime.fromtimestamp(c.get("from", time.time())).strftime("%H:%M"),
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("max", c.get("open", 0))),
                    "low": float(c.get("min", c.get("open", 0))),
                    "close": float(c.get("close", 0)),
                })
            self.last_sync = datetime.now().strftime("%H:%M:%S")
            return out
        except Exception as exc:
            raise IQConnectionError(f"Falha ao buscar candles de {asset}: {exc}") from exc

    def available_candidates(self, assets: list[str], market_type: str, expiration_minutes: int = 1) -> list[dict]:
        """V11: não usa get_all_open_time, porque a API comunitária quebra no Digital.
        O robô tenta enviar a ordem e, se a IQ recusar, pula para o próximo ativo/mercado.
        """
        mt = (market_type or "AUTO").upper().strip()
        candidates: list[dict] = []
        def otc_name(asset: str) -> str:
            return asset if "OTC" in asset.upper() else asset + " OTC"
        for asset in assets:
            if mt == "BINARY":
                candidates.append({"asset": asset, "market": "binary", "label": "Binary"})
            elif mt == "DIGITAL":
                candidates.append({"asset": asset, "market": "digital", "label": "Digital"})
            elif mt == "OTC":
                candidates.append({"asset": otc_name(asset), "market": "binary", "label": "Binary OTC"})
                candidates.append({"asset": otc_name(asset), "market": "digital", "label": "Digital OTC"})
            else:
                # prioridade mais estável: binary normal, OTC binary, digital normal, digital OTC
                candidates.append({"asset": asset, "market": "binary", "label": "Binary"})
                candidates.append({"asset": otc_name(asset), "market": "binary", "label": "Binary OTC"})
                candidates.append({"asset": asset, "market": "digital", "label": "Digital"})
                candidates.append({"asset": otc_name(asset), "market": "digital", "label": "Digital OTC"})
        return candidates

    def buy_demo(self, asset: str, direction: str, amount: float, expiration_minutes: int = 1, market: str = "binary") -> dict:
        self._require()
        action = direction.lower().strip()
        if action not in ["call", "put"]:
            raise IQConnectionError("Direção inválida. Use CALL ou PUT.")
        active = self.to_iq_active(asset)
        try:
            if market == "digital":
                if not hasattr(self.api, "buy_digital_spot_v2"):
                    raise IQConnectionError("Esta versão da iqoptionapi não possui compra digital.")
                check, order_id = self.api.buy_digital_spot_v2(active, float(amount), action, int(expiration_minutes))
            else:
                check, order_id = self.api.buy(float(amount), active, action, int(expiration_minutes))
            if not check:
                raise IQConnectionError(f"IQ Option recusou a ordem: {order_id}")
            self.last_sync = datetime.now().strftime("%H:%M:%S")
            return {"sent": True, "order_id": order_id, "asset": self.display_asset(active), "iq_active": active, "market": market, "direction": action.upper(), "amount": amount, "expiration_minutes": expiration_minutes}
        except IQConnectionError:
            raise
        except Exception as exc:
            raise IQConnectionError(f"Falha ao enviar ordem IQ Option: {exc}") from exc

    def wait_result(self, order_id: Any, expiration_minutes: int = 1, market: str = "binary") -> dict:
        self._require()
        time.sleep(max(5, int(expiration_minutes) * 60 + 3))
        try:
            raw = None
            if market == "digital" and hasattr(self.api, "check_win_digital_v2"):
                raw = self.api.check_win_digital_v2(order_id)
            elif hasattr(self.api, "check_win_v4"):
                raw = self.api.check_win_v4(order_id)
            elif hasattr(self.api, "check_win_v3"):
                raw = self.api.check_win_v3(order_id)
            else:
                raw = self.api.check_win_v2(order_id)
            profit = float(raw[-1]) if isinstance(raw, tuple) else float(raw)
        except Exception as exc:
            raise IQConnectionError(f"Ordem enviada, mas falhou ao consultar resultado: {exc}") from exc
        self.last_sync = datetime.now().strftime("%H:%M:%S")
        result = "WIN" if profit > 0 else "LOSS" if profit < 0 else "EMPATE"
        return {"order_id": order_id, "result": result, "profit": round(profit, 2), "raw": str(raw)}
