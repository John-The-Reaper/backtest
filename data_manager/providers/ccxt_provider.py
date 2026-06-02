from __future__ import annotations

import time
from typing import List, Optional

import ccxt
import pandas as pd

from .base import Provider


class CCXTProvider(Provider):
    """
    Provider crypto base sur ccxt.

    Deux modes:
      - exchange explicite: CCXTProvider("binance")
      - mode auto: CCXTProvider() — essaie les exchanges de FALLBACK_EXCHANGES
        dans l'ordre et prend le premier qui supporte le symbole.

    En mode auto, le `name` exporte est l'exchange resolu (donc le cache
    reste correctement separe par exchange).
    """

    FALLBACK_EXCHANGES = ["binance", "bybit", "kraken", "okx", "coinbase", "kucoin"]
    RETRYABLE = (
        ccxt.NetworkError,
        ccxt.RequestTimeout,
        ccxt.RateLimitExceeded,
        ccxt.ExchangeNotAvailable,
    )

    def __init__(self, exchange: Optional[str] = None) -> None:
        self._auto = exchange is None
        self._explicit_name = exchange
        # En mode auto, le nom est resolu lazy au premier validate/fetch.
        # En mode explicite, on construit tout de suite.
        self._exchange: Optional[ccxt.Exchange] = None
        if not self._auto:
            self._exchange = self._build(exchange)

    @property
    def name(self) -> str:
        if self._exchange is not None:
            return self._exchange.id
        # Mode auto pas encore resolu: nom generique. Sera ecrase apres validate().
        return "ccxt_auto"

    @staticmethod
    def _build(exchange_name: str) -> ccxt.Exchange:
        return getattr(ccxt, exchange_name)({"enableRateLimit": True})

    def _resolve_auto(self, symbol: str) -> ccxt.Exchange:
        """Mode auto: trouve le premier exchange qui supporte le symbole."""
        last_exc: Optional[Exception] = None
        for ex_name in self.FALLBACK_EXCHANGES:
            try:
                ex = self._build(ex_name)
                markets = ex.load_markets()
                m = markets.get(symbol)
                if m and m.get("active") is not False and m.get("spot") is not False:
                    return ex
            except Exception as e:
                last_exc = e
                continue
        raise ValueError(
            f"Symbole {symbol} introuvable sur {self.FALLBACK_EXCHANGES}"
        ) from last_exc

    def validate(self, symbol: str) -> None:
        if "/" not in symbol:
            raise ValueError(f"Symbole ccxt invalide (attendu BASE/QUOTE): {symbol}")

        if self._auto:
            # Resout l'exchange et le fixe pour la suite.
            self._exchange = self._resolve_auto(symbol)
            return

        markets = self._exchange.load_markets()
        m = markets.get(symbol)
        if m is None:
            raise ValueError(f"Symbole inconnu sur {self._exchange.id}: {symbol}")
        if m.get("active") is False:
            raise ValueError(f"Symbole inactif: {symbol}")
        if m.get("spot") is False:
            raise ValueError(f"Symbole non spot: {symbol}")

    def timeframe_ms(self, timeframe: str) -> int:
        if self._exchange is None:
            return 0
        try:
            return int(self._exchange.parse_timeframe(timeframe) * 1000)
        except Exception:
            return 0

    def _fetch_with_retry(self, symbol, timeframe, since, limit, max_retries=5):
        for attempt in range(max_retries):
            try:
                return self._exchange.fetch_ohlcv(symbol, timeframe, since, limit)
            except self.RETRYABLE:
                if attempt == max_retries - 1:
                    raise
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        return []

    def fetch(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        if self._exchange is None:
            # validate() pas encore appele en mode auto: on resout maintenant.
            self._exchange = self._resolve_auto(symbol)

        tf_ms = int(self._exchange.parse_timeframe(timeframe) * 1000)
        rows: List[List[float]] = []
        since = int(start_ms)
        limit = 1000

        while since <= end_ms:
            candles = self._fetch_with_retry(symbol, timeframe, since, limit)
            if not candles:
                break

            valid = [c for c in candles if c[0] <= end_ms]
            if valid:
                rows.extend(valid)

            last_ts = candles[-1][0]
            if last_ts >= end_ms or last_ts < since:
                break
            since = last_ts + tf_ms

        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows, columns=cols)
        df["timestamp"] = df["timestamp"].astype("int64")
        return df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)