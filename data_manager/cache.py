from __future__ import annotations

import os
from typing import List, Tuple

import pandas as pd
import pyarrow.feather as feather

OHLCV_COLS = ["timestamp", "open", "high", "low", "close", "volume"]


class FeatherCache:
    """Cache OHLCV au format Feather, un fichier par (symbol, timeframe, source)."""

    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    @staticmethod
    def _safe(s: str) -> str:
        return s.replace("/", "_").replace(":", "_").replace("\\", "_")

    def path(self, symbol: str, timeframe: str, source: str) -> str:
        return os.path.join(
            self.data_dir,
            f"{self._safe(symbol)}_{self._safe(timeframe)}_{self._safe(source)}.feather",
        )

    def load(self, symbol: str, timeframe: str, source: str) -> pd.DataFrame:
        p = self.path(symbol, timeframe, source)
        if not os.path.exists(p):
            return pd.DataFrame(columns=OHLCV_COLS)
        df = feather.read_feather(p)
        if df.empty:
            return pd.DataFrame(columns=OHLCV_COLS)
        if "timestamp" not in df.columns:
            raise ValueError(f"Cache invalide (timestamp manquant): {p}")
        df["timestamp"] = df["timestamp"].astype("int64")
        return df[OHLCV_COLS]

    def save(self, symbol: str, timeframe: str, source: str, df: pd.DataFrame) -> None:
        out = df[OHLCV_COLS].reset_index(drop=True)
        feather.write_feather(out, self.path(symbol, timeframe, source))

    @staticmethod
    def missing_ranges(
        cache: pd.DataFrame,
        start_ms: int,
        end_ms: int,
        tolerance_ms: int = 0,
    ) -> List[Tuple[int, int]]:
        """
        Retourne la liste des intervalles [a, b] manquants dans le cache
        pour couvrir [start_ms, end_ms].

        On ne detecte que les trous "aux bords" (avant min, apres max).
        Les trous internes ne sont pas comblees (hypothese: le provider
        renvoie des series continues pour un timeframe donne).

        `tolerance_ms` permet d'ignorer un manque a la fin (bougie en cours).
        """
        if cache.empty:
            return [(start_ms, end_ms)]

        ranges: List[Tuple[int, int]] = []
        cmin = int(cache["timestamp"].min())
        cmax = int(cache["timestamp"].max())

        if start_ms < cmin:
            ranges.append((start_ms, cmin - 1))
        if end_ms > cmax + tolerance_ms:
            ranges.append((cmax + 1, end_ms))
        return ranges