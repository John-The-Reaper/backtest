from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .base import Provider


class IBKRProvider(Provider):
    """
    Provider Interactive Brokers via ib_insync (TWS/IB Gateway requis).

    Prerequis:
      - TWS ou IB Gateway lance et logge
      - "Enable ActiveX and Socket Clients" coche dans API settings
      - Port API ouvert (par defaut 4002 paper / 4001 live pour IB Gateway,
        7497 paper / 7496 live pour TWS)

    Timeframes supportes (mapping vers `barSizeSetting`):
      1m, 2m, 3m, 5m, 10m, 15m, 20m, 30m, 1h, 2h, 3h, 4h, 8h, 1d, 1w, 1mo

    Notes:
      - clientId aleatoire par defaut pour eviter les collisions
      - une seule connexion persistante reutilisee entre les fetch
      - les symboles sont resolus en Stock SMART/USD par defaut; passer un
        Contract ib_insync directement pour les futures/forex/options
    """

    name = "ibkr"

    _TF_MAP = {
        "1m": "1 min", "2m": "2 mins", "3m": "3 mins", "5m": "5 mins",
        "10m": "10 mins", "15m": "15 mins", "20m": "20 mins", "30m": "30 mins",
        "1h": "1 hour", "2h": "2 hours", "3h": "3 hours", "4h": "4 hours",
        "8h": "8 hours",
        "1d": "1 day", "1w": "1 week", "1mo": "1 month",
    }

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: Optional[int] = None,
        what_to_show: str = "TRADES",
        use_rth: bool = True,
        timeout: float = 15.0,
    ) -> None:
        try:
            from ib_insync import IB  # noqa: F401
        except ImportError as e:
            raise ImportError("ib_insync requis: pip install ib_insync") from e

        self.host = host
        self.port = port
        self.client_id = client_id if client_id is not None else random.randint(1000, 9999)
        self.what_to_show = what_to_show
        self.use_rth = use_rth
        self.timeout = timeout
        self._ib = None

    # ---- Connexion -----------------------------------------------------

    def _ensure_connected(self):
        from ib_insync import IB
        if self._ib is None or not self._ib.isConnected():
            self._ib = IB()
            self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout)
        return self._ib

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
        self._ib = None

    def __deepcopy__(self, memo):
        # Pas de copie de socket : on rend une instance neuve, non connectee,
        # avec un clientId different pour eviter la collision.
        return IBKRProvider(
            host=self.host, port=self.port, client_id=None,
            what_to_show=self.what_to_show, use_rth=self.use_rth,
            timeout=self.timeout,
        )

    # ---- Provider API --------------------------------------------------

    def _to_bar_size(self, timeframe: str) -> str:
        if timeframe not in self._TF_MAP:
            raise ValueError(
                f"Timeframe '{timeframe}' non supporte par IBKR. "
                f"Valides: {sorted(self._TF_MAP)}"
            )
        return self._TF_MAP[timeframe]

    @staticmethod
    def _build_duration(start_ms: int, end_ms: int) -> str:
        """Convertit la plage en durationStr IBKR ('30 D', '2 Y', ...)."""
        delta_s = max(1, (end_ms - start_ms) // 1000)
        if delta_s < 86400:
            return f"{delta_s} S"
        days = delta_s // 86400 + 1
        if days <= 365:
            return f"{days} D"
        years = days // 365 + 1
        return f"{years} Y"

    @staticmethod
    def _end_dt(end_ms: int) -> str:
        """Format endDateTime IBKR : 'YYYYMMDD-HH:MM:SS' en UTC, ou vide si ~now."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if abs(now_ms - end_ms) < 60_000:
            return ""
        dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y%m%d-%H:%M:%S")

    def _resolve_contract(self, symbol):
        from ib_insync import Contract, Stock
        if isinstance(symbol, Contract):
            return symbol
        # Par defaut : action US sur SMART en USD
        return Stock(symbol, "SMART", "USD")

    def validate(self, symbol) -> None:
        ib = self._ensure_connected()
        contract = self._resolve_contract(symbol)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Symbole IBKR non resolu: {symbol!r}")

    def fetch(self, symbol, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        from ib_insync import util

        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        ib = self._ensure_connected()
        contract = self._resolve_contract(symbol)
        ib.qualifyContracts(contract)

        bars = ib.reqHistoricalData(
            contract,
            endDateTime=self._end_dt(end_ms),
            durationStr=self._build_duration(start_ms, end_ms),
            barSizeSetting=self._to_bar_size(timeframe),
            whatToShow=self.what_to_show,
            useRTH=self.use_rth,
            formatDate=2,  # epoch / UTC
        )

        df = util.df(bars)
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)

        df["timestamp"] = pd.to_datetime(df["date"], utc=True).astype("int64") // 1_000_000
        df = df.rename(columns={"open": "open", "high": "high", "low": "low",
                                "close": "close", "volume": "volume"})
        df = df[cols]
        df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)]
        return df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
