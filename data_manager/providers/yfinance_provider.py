from __future__ import annotations

import pandas as pd

from .base import Provider


class YFinanceProvider(Provider):
    """
    Provider actions/ETF/indices via yfinance.

    Timeframes supportes (mapping vers `interval` yfinance):
      1m, 2m, 5m, 15m, 30m, 60m/1h, 90m, 1d, 5d, 1wk, 1mo, 3mo

    Note yfinance:
      - les intraday (<1d) sont limites a ~60 jours d'historique
      - le volume peut etre 0 pour les indices
    """

    name = "yfinance"

    # Mapping timeframe interne -> yfinance
    _TF_MAP = {
        "1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "60m", "60m": "60m", "90m": "90m",
        "1d": "1d", "5d": "5d", "1w": "1wk", "1wk": "1wk",
        "1mo": "1mo", "3mo": "3mo",
    }

    def __init__(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as e:
            raise ImportError("yfinance requis: pip install yfinance") from e

    def _to_yf_interval(self, timeframe: str) -> str:
        if timeframe not in self._TF_MAP:
            raise ValueError(
                f"Timeframe '{timeframe}' non supporte par yfinance. "
                f"Valides: {sorted(self._TF_MAP)}"
            )
        return self._TF_MAP[timeframe]

    def validate(self, symbol: str) -> None:
        # yfinance accepte a peu pres tout en entree, l'erreur arrive au fetch.
        # On fait un check minimal de format.
        if not symbol or " " in symbol:
            raise ValueError(f"Symbole yfinance invalide: {symbol!r}")

    def fetch(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        import yfinance as yf

        interval = self._to_yf_interval(timeframe)
        start = pd.to_datetime(start_ms, unit="ms", utc=True)
        end = pd.to_datetime(end_ms, unit="ms", utc=True)

        df = yf.download(
            symbol,
            start=start,
            end=end,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
        )

        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)

        # yfinance peut renvoyer un MultiIndex sur les colonnes (ticker en niveau 1).
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        # La colonne d'index s'appelle "Date" (daily+) ou "Datetime" (intraday).
        ts_col = "Datetime" if "Datetime" in df.columns else "Date"
        df = df.rename(columns={
            ts_col: "timestamp",
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).astype("int64") // 1_000_000
        df = df[cols]
        df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)]
        return df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)