from __future__ import annotations
from typing import Any, Optional
from datetime import datetime

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
        account_type = account_type.upper().strip()
        if account_type not in ["PRACTICE", "REAL"]:
            raise IQConnectionError("Tipo de conta inválido. Use PRACTICE ou REAL.")
        if account_type == "REAL":
            raise IQConnectionError("Conta REAL bloqueada nesta versão. Use PRACTICE/DEMO.")

        try:
            from iqoptionapi.stable_api import IQ_Option  # type: ignore
        except Exception as exc:
            raise IQConnectionError(
                "Biblioteca iqoptionapi não instalada no servidor. Verifique requirements.txt e logs do Render."
            ) from exc

        try:
            iq = IQ_Option(email, password)
            check, reason = iq.connect()
            if not check:
                raise IQConnectionError(f"Falha no login IQ Option: {reason}")

            iq.change_balance(account_type)
            balance = float(iq.get_balance())
            self.api = iq
            self.email = email
            self.account_type = account_type
            self.connected = True
            self.last_error = ""
            self.last_sync = datetime.now().strftime("%H:%M:%S")
            return {"connected": True, "balance": balance, "account_type": account_type, "last_sync": self.last_sync}
        except IQConnectionError:
            self.connected = False
            raise
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            raise IQConnectionError(f"Erro ao conectar na IQ Option: {exc}") from exc

    def disconnect(self) -> None:
        try:
            if self.api:
                # Algumas versões da lib não possuem close estável; por isso só descartamos a sessão.
                pass
        finally:
            self.api = None
            self.connected = False
            self.email = ""
            self.last_sync = datetime.now().strftime("%H:%M:%S")

    def balance(self) -> float:
        if not self.connected or not self.api:
            raise IQConnectionError("IQ Option não conectada.")
        try:
            bal = float(self.api.get_balance())
            self.last_sync = datetime.now().strftime("%H:%M:%S")
            return bal
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            raise IQConnectionError(f"Falha ao atualizar saldo: {exc}") from exc

    def candles(self, asset: str, interval: int = 60, count: int = 80) -> list[dict]:
        if not self.connected or not self.api:
            raise IQConnectionError("IQ Option não conectada.")
        try:
            raw = self.api.get_candles(asset.replace("/", ""), interval, count, datetime.now().timestamp())
            out = []
            for c in raw or []:
                out.append({
                    "time": datetime.fromtimestamp(c.get("from", datetime.now().timestamp())).strftime("%H:%M"),
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("max", c.get("open", 0))),
                    "low": float(c.get("min", c.get("open", 0))),
                    "close": float(c.get("close", 0)),
                })
            self.last_sync = datetime.now().strftime("%H:%M:%S")
            return out
        except Exception as exc:
            raise IQConnectionError(f"Falha ao buscar candles de {asset}: {exc}") from exc
