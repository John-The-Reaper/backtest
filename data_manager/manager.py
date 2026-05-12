from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

import pandas as pd
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
    TextColumn, TimeElapsedColumn,
)

from .cache import FeatherCache, OHLCV_COLS
from .providers.base import Provider
from .timeutils import parse_duration, to_ms

DateLike = Union[str, int, float, datetime]


class DataManager:
    """
    Gestionnaire de donnees OHLCV avec cache local Feather.

    Une seule API publique:
      - get(symbol, timeframe, start, end)     : une serie
      - get_many(symbols, timeframe, ...)      : plusieurs en parallele

    `start`/`end` peuvent etre soit deux dates, soit (start=duration, end=None)
    pour exprimer "les N derniers ...".

    Exemples:
        dm = DataManager(CCXTProvider("binance"))
        dm.get("BTC/USDT", "1h", "3mois")                 # 3 derniers mois
        dm.get("BTC/USDT", "1h", "2024-01-01", "2024-06-01")

        dm = DataManager(YFinanceProvider())
        dm.get("AAPL", "1d", "2y")
    """

    def __init__(self, provider: Provider, data_dir: str = "data") -> None:
        self.provider = provider
        self.cache = FeatherCache(data_dir)

    # ---- API publique --------------------------------------------------

    def get(
        self,
        symbol: str,
        timeframe: str,
        start: Union[DateLike, str],   # date OU duration string ("3mois")
        end: Optional[DateLike] = None,
        validate: bool = True,
    ) -> pd.DataFrame:
        start_ms, end_ms = self._resolve_range(start, end)
        if validate:
            self.provider.validate(symbol)
        return self._get_with_cache(self.provider, symbol, timeframe, start_ms, end_ms)

    def get_many(
        self,
        symbols: List[str],
        timeframe: str,
        start: Union[DateLike, str],
        end: Optional[DateLike] = None,
        max_workers: int = 1,
        validate: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        start_ms, end_ms = self._resolve_range(start, end)

        if validate:
            for s in symbols:
                self.provider.validate(s)

        def task(sym: str, prov: Provider) -> pd.DataFrame:
            return self._get_with_cache(prov, sym, timeframe, start_ms, end_ms)

        return self._run_parallel(symbols, task, max_workers)

    # ---- Logique interne -----------------------------------------------

    @staticmethod
    def _resolve_range(start, end):
        """
        Si end est None, start est interprete comme une duree ('3mois')
        et la plage devient [now - duration, now].
        Sinon les deux sont parsees comme des dates.
        """
        if end is None:
            now = datetime.now(timezone.utc)
            return int((now - parse_duration(str(start))).timestamp() * 1000), int(now.timestamp() * 1000)

        s_ms, e_ms = to_ms(start), to_ms(end)
        if s_ms >= e_ms:
            raise ValueError("start doit etre < end")
        return s_ms, e_ms

    def _get_with_cache(
        self,
        provider: Provider,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """Pipeline cache: load -> detect missing -> fetch -> merge -> save -> window."""
        source = provider.name
        cached = self.cache.load(symbol, timeframe, source)

        # Tolerance = 1 timeframe pour ignorer la bougie en cours non fermee.
        # On la calcule via le provider quand c'est ccxt, sinon 0 (yfinance gere
        # mal les requetes sur la barre courante de toute facon).
        tol_ms = self._timeframe_tolerance(provider, timeframe)
        missing = self.cache.missing_ranges(cached, start_ms, end_ms, tolerance_ms=tol_ms)

        if missing:
            new_pieces = [provider.fetch(symbol, timeframe, a, b) for a, b in missing]
            full = pd.concat(
                [cached] + [p for p in new_pieces if not p.empty],
                ignore_index=True,
            )
            if full.empty:
                raise ValueError(f"Aucune donnee pour {symbol} ({timeframe}) sur la plage demandee.")
            full = (full.drop_duplicates(subset=["timestamp"])
                        .sort_values("timestamp")
                        .reset_index(drop=True))
            self.cache.save(symbol, timeframe, source, full)
        else:
            full = cached

        return self._window(full, start_ms, end_ms)

    @staticmethod
    def _timeframe_tolerance(provider: Provider, timeframe: str) -> int:
        """Retourne la duree d'une bougie en ms si on peut la calculer, sinon 0."""
        ex = getattr(provider, "_exchange", None)  # ccxt
        if ex is not None:
            try:
                return int(ex.parse_timeframe(timeframe) * 1000)
            except Exception:
                return 0
        return 0

    @staticmethod
    def _window(df: pd.DataFrame, start_ms: int, end_ms: int) -> pd.DataFrame:
        """Filtre par fenetre et passe en DatetimeIndex UTC."""
        out = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)].copy()
        out["timestamp"] = pd.to_datetime(out["timestamp"], unit="ms", utc=True)
        return out.set_index("timestamp")[OHLCV_COLS[1:]]  # drop "timestamp" since it's now the index

    # ---- Parallelisme --------------------------------------------------

    def _run_parallel(self, symbols, task_fn, max_workers: int):
        """
        Execute task_fn(symbol, provider) pour chaque symbole avec barre de progression.
        Chaque worker recoit une copie du provider (deepcopy) pour eviter le partage
        d'etat interne (sessions HTTP, markets cache de ccxt, etc.).
        """
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description:<30}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )

        results: Dict[str, pd.DataFrame] = {}
        errors: Dict[str, Exception] = {}

        with progress:
            task_id = progress.add_task("Telechargement", total=len(symbols))

            if max_workers <= 1:
                for s in symbols:
                    progress.update(task_id, description=s)
                    try:
                        results[s] = task_fn(s, self.provider)
                    except Exception as e:
                        errors[s] = e
                        progress.console.print(f"[red][ERREUR][/red] {s}: {e}")
                    progress.advance(task_id)
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futs = {
                        pool.submit(task_fn, s, copy.deepcopy(self.provider)): s
                        for s in symbols
                    }
                    for fut in as_completed(futs):
                        s = futs[fut]
                        try:
                            results[s] = fut.result()
                        except Exception as e:
                            errors[s] = e
                            progress.console.print(f"[red][ERREUR][/red] {s}: {e}")
                        progress.advance(task_id)

        if errors:
            raise RuntimeError(f"Echec pour {len(errors)} symbole(s): {list(errors)}")

        return {s: results[s] for s in symbols if s in results}