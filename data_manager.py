from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

import ccxt
import pandas as pd
import pyarrow.feather as feather
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


class DataManager:
    """Gestionnaire de donnees crypto (ccxt) avec cache local Feather."""

    RETRYABLE_EXCEPTIONS = (
        ccxt.NetworkError,
        ccxt.RequestTimeout,
        ccxt.RateLimitExceeded,
        ccxt.ExchangeNotAvailable,
    )

    # Ordre de preference pour le mode auto (exchange_name=None)
    FALLBACK_EXCHANGES = ["binance", "bybit", "kraken", "okx", "coinbase", "kucoin"]

    def __init__(self, exchange_name: Optional[str] = None, data_dir: str = "data") -> None:
        self._auto_mode = exchange_name is None
        self.exchange_name = exchange_name or self.FALLBACK_EXCHANGES[0]
        self.exchange = self._build_exchange()
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self._markets = None
        # Cache: symbol -> exchange instance resolue en mode auto
        self._symbol_exchange_cache: Dict[str, ccxt.Exchange] = {}

    def _build_exchange(self, name: str = None) -> ccxt.Exchange:
        return getattr(ccxt, name or self.exchange_name)({"enableRateLimit": True})

    def _resolve_exchange_for_symbol(self, symbol: str) -> ccxt.Exchange:
        """
        Retourne l'exchange a utiliser pour ce symbole.
        En mode normal: retourne self.exchange.
        En mode auto: teste les exchanges dans FALLBACK_EXCHANGES et retourne le premier
        qui supporte le symbole (spot, actif). Le resultat est mis en cache.
        """
        if not self._auto_mode:
            return self.exchange

        if symbol in self._symbol_exchange_cache:
            return self._symbol_exchange_cache[symbol]

        last_exc: Optional[Exception] = None
        for name in self.FALLBACK_EXCHANGES:
            try:
                ex = self._build_exchange(name)
                markets = ex.load_markets()
                market = markets.get(symbol)
                if (market is not None
                        and market.get("active") is not False
                        and market.get("spot") is not False):
                    self._symbol_exchange_cache[symbol] = ex
                    return ex
            except Exception as e:
                last_exc = e
                continue

        raise ValueError(
            f"Symbole {symbol} introuvable sur tous les exchanges: {self.FALLBACK_EXCHANGES}"
        ) from last_exc

    def _ensure_symbol_valid(self, symbol: str) -> None:
        """Valide le symbole. En mode auto, resout et cache l'exchange associe."""
        if self._auto_mode:
            self._resolve_exchange_for_symbol(symbol)
        else:
            self._validate_symbol(symbol)

    @staticmethod
    def parse_duration(duration: str) -> timedelta:
        """
        Parse une duree utilisateur:
        - 1mois, 3mois
        - 7j, 14d
        - 2w
        - 6h
        - 1an, 1y
        """
        raw = duration.strip().lower().replace(" ", "")
        match = re.fullmatch(r"(\d+)([a-z]+)", raw)
        if not match:
            raise ValueError(f"Format de duree invalide: {duration}")

        value = int(match.group(1))
        unit = match.group(2)

        if unit in {"j", "d", "day", "days"}:
            return timedelta(days=value)
        if unit in {"w", "week", "weeks"}:
            return timedelta(weeks=value)
        if unit in {"h", "hour", "hours"}:
            return timedelta(hours=value)
        if unit in {"mois", "month", "months"}:
            return timedelta(days=30 * value)
        if unit in {"an", "ans", "y", "year", "years"}:
            return timedelta(days=365 * value)

        raise ValueError(f"Unite de duree non supportee: {unit}")

    @staticmethod
    def _make_progress() -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description:<30}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )

    @staticmethod
    def _sanitize_symbol(symbol: str) -> str:
        return symbol.replace("/", "_").replace(":", "_")

    def _data_file_path(self, symbol: str, timeframe: str) -> str:
        safe_symbol = self._sanitize_symbol(symbol)
        safe_tf = timeframe.replace("/", "_")
        return os.path.join(self.data_dir, f"{safe_symbol}_{safe_tf}_data.feather")

    def _load_markets(self) -> Dict:
        if self._markets is None:
            self._markets = self.exchange.load_markets()
        return self._markets

    def _validate_symbol(self, symbol: str) -> None:
        if "/" not in symbol:
            raise ValueError(f"Symbole invalide (format attendu BASE/QUOTE): {symbol}")

        markets = self._load_markets()
        if symbol not in markets:
            raise ValueError(f"Symbole inconnu sur {self.exchange_name}: {symbol}")

        market = markets[symbol]
        if market.get("active") is False:
            raise ValueError(f"Symbole inactif: {symbol}")

        # On force du crypto spot (pas futures/perpetual/swap)
        if market.get("spot") is False:
            raise ValueError(f"Symbole non spot (crypto uniquement): {symbol}")

    def _fetch_ohlcv_with_retry(
        self,
        exchange,
        symbol: str,
        timeframe: str,
        since: int,
        limit: int,
        max_retries: int = 5,
    ):
        for attempt in range(max_retries):
            try:
                return exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
            except self.RETRYABLE_EXCEPTIONS:
                if attempt == max_retries - 1:
                    raise
                sleep_s = min(8.0, 0.5 * (2 ** attempt))
                time.sleep(sleep_s)
        return []

    def _download_chunk(self, exchange, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        """Telecharge un intervalle [start_ms, end_ms] via pagination ccxt."""
        rows: List[List[float]] = []
        since = int(start_ms)
        limit = 1000
        tf_ms = int(exchange.parse_timeframe(timeframe) * 1000)

        while since <= end_ms:
            candles = self._fetch_ohlcv_with_retry(exchange, symbol, timeframe, since, limit)
            if not candles:
                break

            valid = [c for c in candles if c[0] <= end_ms]
            if valid:
                rows.extend(valid)

            last_ts = candles[-1][0]
            if last_ts >= end_ms:
                break

            if last_ts < since:
                # Protection contre boucle infinie si l'exchange renvoie des timestamps en arriere.
                break

            # Avance propre d'un pas de timeframe pour eviter chevauchement/duplication excessive.
            since = int(last_ts) + tf_ms

        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return df

    @staticmethod
    def _cache_covers(cache_df: pd.DataFrame, start_ms: int, end_ms: int, tolerance_ms: int = 0) -> bool:
        """
        Retourne True si le cache couvre integralement la plage [start_ms, end_ms].

        `tolerance_ms` permet d'ignorer un ecart en fin de plage (ex: bougie en cours non fermee).
        Typiquement passe a tf_ms pour fetch_symbol, 0 pour fetch_symbol_range.
        """
        if cache_df.empty:
            return False
        return int(cache_df["timestamp"].min()) <= start_ms and int(cache_df["timestamp"].max()) >= end_ms - tolerance_ms

    def _load_cache(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self._data_file_path(symbol, timeframe)
        if not os.path.exists(path):
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = feather.read_feather(path)
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        if "timestamp" not in df.columns:
            raise ValueError(f"Cache invalide (timestamp manquant): {path}")

        df["timestamp"] = df["timestamp"].astype("int64")
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        return df

    def _save_cache(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        path = self._data_file_path(symbol, timeframe)
        out = df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
        feather.write_feather(out, path)

    @staticmethod
    def _normalize_ohlcv_dataframe(df: pd.DataFrame, source: str) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        if "timestamp" in df.columns:
            ts = df["timestamp"]
            if pd.api.types.is_numeric_dtype(ts):
                df["timestamp"] = pd.to_datetime(ts.astype("int64"), unit="ms", utc=True)
            else:
                df["timestamp"] = pd.to_datetime(ts, utc=True)
            df = df.set_index("timestamp")
        elif not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError(f"Impossible de trouver un timestamp exploitable dans {source}")

        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "timestamp"

        required_columns = ["open", "high", "low", "close", "volume"]
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"Colonnes OHLCV manquantes dans {source}: {missing_columns}")

        out = df[required_columns].copy()
        out = out.sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out

    @staticmethod
    def _to_timestamp_ms(value: Union[str, int, float, datetime]) -> int:
        """Convertit une date/heure en timestamp millisecondes UTC."""
        if isinstance(value, datetime):
            dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)

        if isinstance(value, (int, float)):
            # Heuristique: valeur en secondes si trop petite pour des ms epoch.
            if value < 10_000_000_000:
                return int(value * 1000)
            return int(value)

        if isinstance(value, str):
            dt = pd.to_datetime(value, utc=True)
            if pd.isna(dt):
                raise ValueError(f"Date invalide: {value}")
            return int(dt.timestamp() * 1000)

        raise TypeError(f"Type de date non supporte: {type(value)}")

    def fetch_symbol(
        self,
        symbol: str,
        timeframe: str,
        duration: str,
        exchange_override=None,
        validate_symbol: bool = True,
    ) -> pd.DataFrame:
        """Retourne un DataFrame indexe par timestamp pour la duree demandee."""
        if validate_symbol and not self._auto_mode:
            self._validate_symbol(symbol)

        exchange = exchange_override or self._resolve_exchange_for_symbol(symbol)

        now_utc = datetime.now(timezone.utc)
        start_utc = now_utc - self.parse_duration(duration)

        start_ms = int(start_utc.timestamp() * 1000)
        end_ms = int(now_utc.timestamp() * 1000)
        tf_ms = int(exchange.parse_timeframe(timeframe) * 1000)

        cache_df = self._load_cache(symbol, timeframe)

        if self._cache_covers(cache_df, start_ms, end_ms, tolerance_ms=tf_ms):
            window_df = cache_df[(cache_df["timestamp"] >= start_ms) & (cache_df["timestamp"] <= end_ms)].copy()
            window_df["timestamp"] = pd.to_datetime(window_df["timestamp"], unit="ms", utc=True)
            return window_df.set_index("timestamp")

        pieces: List[pd.DataFrame] = []

        if cache_df.empty:
            pieces.append(self._download_chunk(exchange, symbol, timeframe, start_ms, end_ms))
        else:
            min_ts = int(cache_df["timestamp"].min())
            max_ts = int(cache_df["timestamp"].max())

            if start_ms < min_ts:
                pieces.append(self._download_chunk(exchange, symbol, timeframe, start_ms, min_ts - 1))
            if end_ms > max_ts:
                pieces.append(self._download_chunk(exchange, symbol, timeframe, max_ts + 1, end_ms))

            pieces.append(cache_df)

        full_df = pd.concat(pieces, ignore_index=True) if pieces else cache_df
        if full_df.empty:
            raise ValueError(f"Aucune donnee recuperee pour {symbol} ({timeframe}, {duration}).")

        full_df = full_df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        self._save_cache(symbol, timeframe, full_df)

        window_df = full_df[(full_df["timestamp"] >= start_ms) & (full_df["timestamp"] <= end_ms)].copy()
        window_df["timestamp"] = pd.to_datetime(window_df["timestamp"], unit="ms", utc=True)
        return window_df.set_index("timestamp")

    def fetch_symbol_range(
        self,
        symbol: str,
        timeframe: str,
        start: Union[str, int, float, datetime],
        end: Union[str, int, float, datetime],
        exchange_override=None,
        validate_symbol: bool = True,
    ) -> pd.DataFrame:
        """Retourne un DataFrame indexe par timestamp pour une plage [start, end]."""
        if validate_symbol and not self._auto_mode:
            self._validate_symbol(symbol)

        exchange = exchange_override or self._resolve_exchange_for_symbol(symbol)

        start_ms = self._to_timestamp_ms(start)
        end_ms = self._to_timestamp_ms(end)
        if start_ms >= end_ms:
            raise ValueError(f"Plage invalide pour {symbol}: start >= end")

        cache_df = self._load_cache(symbol, timeframe)

        if self._cache_covers(cache_df, start_ms, end_ms):
            window_df = cache_df[(cache_df["timestamp"] >= start_ms) & (cache_df["timestamp"] <= end_ms)].copy()
            window_df["timestamp"] = pd.to_datetime(window_df["timestamp"], unit="ms", utc=True)
            return window_df.set_index("timestamp")

        pieces: List[pd.DataFrame] = []

        if cache_df.empty:
            pieces.append(self._download_chunk(exchange, symbol, timeframe, start_ms, end_ms))
        else:
            min_ts = int(cache_df["timestamp"].min())
            max_ts = int(cache_df["timestamp"].max())

            if start_ms < min_ts:
                pieces.append(self._download_chunk(exchange, symbol, timeframe, start_ms, min_ts - 1))
            if end_ms > max_ts:
                pieces.append(self._download_chunk(exchange, symbol, timeframe, max_ts + 1, end_ms))

            pieces.append(cache_df)

        full_df = pd.concat(pieces, ignore_index=True) if pieces else cache_df
        if full_df.empty:
            raise ValueError(f"Aucune donnee recuperee pour {symbol} ({timeframe}, {start} -> {end}).")

        full_df = full_df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        self._save_cache(symbol, timeframe, full_df)

        window_df = full_df[(full_df["timestamp"] >= start_ms) & (full_df["timestamp"] <= end_ms)].copy()
        window_df["timestamp"] = pd.to_datetime(window_df["timestamp"], unit="ms", utc=True)
        return window_df.set_index("timestamp")

    def load_symbol_from_feather(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[Union[str, int, float, datetime]] = None,
        end: Optional[Union[str, int, float, datetime]] = None,
        file_path: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Charge un symbole depuis un fichier Feather existant.

        Si `file_path` est omis, on cherche `data/<symbol>_<timeframe>_data.feather`.
        """
        path = file_path or self._data_file_path(symbol, timeframe)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Fichier Feather introuvable: {path}")

        df = feather.read_feather(path)
        df = self._normalize_ohlcv_dataframe(df, path)

        if start is not None:
            start_dt = pd.to_datetime(self._to_timestamp_ms(start), unit="ms", utc=True)
            df = df[df.index >= start_dt]
        if end is not None:
            end_dt = pd.to_datetime(self._to_timestamp_ms(end), unit="ms", utc=True)
            df = df[df.index <= end_dt]

        if df.empty:
            raise ValueError(f"Aucune donnee exploitable dans {path} pour la plage demandee")

        return df

    def load_or_fetch_symbol_range(
        self,
        symbol: str,
        timeframe: str,
        start: Union[str, int, float, datetime],
        end: Union[str, int, float, datetime],
        file_path: Optional[str] = None,
        prefer_file: bool = True,
        exchange_override=None,
    ) -> pd.DataFrame:
        """
        Charge un Feather local si disponible, sinon telecharge via l'exchange.
        """
        candidate_path = file_path or self._data_file_path(symbol, timeframe)
        if prefer_file and os.path.exists(candidate_path):
            return self.load_symbol_from_feather(
                symbol=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                file_path=candidate_path,
            )

        return self.fetch_symbol_range(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            exchange_override=exchange_override,
            validate_symbol=False,
        )

    def _build_worker_exchange(self, symbol: str) -> ccxt.Exchange:
        """Construit une instance exchange locale pour un worker parallele."""
        if self._auto_mode and symbol in self._symbol_exchange_cache:
            return self._build_exchange(self._symbol_exchange_cache[symbol].id)
        return self._build_exchange()

    def download_period(
        self,
        symbols: List[str],
        timeframe: str,
        duration: str,
        max_workers: int = 1,
    ) -> Dict[str, pd.DataFrame]:
        """Telecharge la periode demandee pour tous les symboles crypto."""
        for symbol in symbols:
            self._ensure_symbol_valid(symbol)

        if max_workers <= 1:
            data: Dict[str, pd.DataFrame] = {}
            with self._make_progress() as progress:
                task = progress.add_task("Téléchargement", total=len(symbols))
                for symbol in symbols:
                    progress.update(task, description=symbol)
                    data[symbol] = self.fetch_symbol(
                        symbol=symbol,
                        timeframe=timeframe,
                        duration=duration,
                        validate_symbol=False,
                    )
                    progress.advance(task)
            return data

        # Parallelisation I/O: chaque thread construit son instance exchange.
        results: Dict[str, pd.DataFrame] = {}
        errors: Dict[str, Exception] = {}

        def _worker(sym: str):
            local_exchange = self._build_worker_exchange(sym)
            return sym, self.fetch_symbol(
                sym,
                timeframe,
                duration,
                exchange_override=local_exchange,
                validate_symbol=False,
            )

        with self._make_progress() as progress:
            task = progress.add_task("Téléchargement", total=len(symbols))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(_worker, s): s for s in symbols}
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        _, df = fut.result()
                        results[sym] = df
                    except Exception as e:
                        errors[sym] = e
                        progress.console.print(f"[red][ERREUR][/red] {sym}: {e}")
                    progress.advance(task)

        if errors:
            failed = ", ".join(errors)
            raise RuntimeError(f"Echec pour {len(errors)} symbole(s): {failed}")

        # Preserve input order
        return {s: results[s] for s in symbols if s in results}

    def download_range(
        self,
        symbols: List[str],
        timeframe: str,
        start: Union[str, int, float, datetime],
        end: Union[str, int, float, datetime],
        max_workers: int = 1,
    ) -> Dict[str, pd.DataFrame]:
        """Telecharge les donnees pour une plage [start, end] pour tous les symboles."""
        for symbol in symbols:
            self._ensure_symbol_valid(symbol)

        if max_workers <= 1:
            data: Dict[str, pd.DataFrame] = {}
            with self._make_progress() as progress:
                task = progress.add_task("Téléchargement", total=len(symbols))
                for symbol in symbols:
                    progress.update(task, description=symbol)
                    data[symbol] = self.fetch_symbol_range(
                        symbol=symbol,
                        timeframe=timeframe,
                        start=start,
                        end=end,
                        validate_symbol=False,
                    )
                    progress.advance(task)
            return data

        results: Dict[str, pd.DataFrame] = {}
        errors: Dict[str, Exception] = {}

        def _worker(sym: str):
            local_exchange = self._build_worker_exchange(sym)
            return sym, self.fetch_symbol_range(
                symbol=sym,
                timeframe=timeframe,
                start=start,
                end=end,
                exchange_override=local_exchange,
                validate_symbol=False,
            )

        with self._make_progress() as progress:
            task = progress.add_task("Téléchargement", total=len(symbols))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(_worker, s): s for s in symbols}
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        _, df = fut.result()
                        results[sym] = df
                    except Exception as e:
                        errors[sym] = e
                        progress.console.print(f"[red][ERREUR][/red] {sym}: {e}")
                    progress.advance(task)

        if errors:
            failed = ", ".join(errors)
            raise RuntimeError(f"Echec pour {len(errors)} symbole(s): {failed}")

        return {s: results[s] for s in symbols if s in results}

    def load_or_download_range(
        self,
        symbols: List[str],
        timeframe: str,
        start: Union[str, int, float, datetime],
        end: Union[str, int, float, datetime],
        max_workers: int = 1,
        prefer_file: bool = True,
        file_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Charge les donnees depuis des fichiers Feather existants quand ils sont presents,
        sinon telecharge via l'exchange.

        `file_map` permet de donner un chemin `.feather` explicite par symbole.
        """
        file_map = file_map or {}

        # Pre-validation: resout l'exchange en mode auto, valide en mode normal.
        # On ne valide que les symboles qui necessiteront un telechargement.
        for symbol in symbols:
            candidate = file_map.get(symbol) or self._data_file_path(symbol, timeframe)
            if not (prefer_file and os.path.exists(candidate)):
                self._ensure_symbol_valid(symbol)

        if max_workers <= 1:
            data: Dict[str, pd.DataFrame] = {}
            with self._make_progress() as progress:
                task = progress.add_task("Chargement", total=len(symbols))
                for symbol in symbols:
                    progress.update(task, description=symbol)
                    data[symbol] = self.load_or_fetch_symbol_range(
                        symbol=symbol,
                        timeframe=timeframe,
                        start=start,
                        end=end,
                        file_path=file_map.get(symbol),
                        prefer_file=prefer_file,
                    )
                    progress.advance(task)
            return data

        results: Dict[str, pd.DataFrame] = {}
        errors: Dict[str, Exception] = {}

        def _worker(sym: str):
            local_exchange = self._build_worker_exchange(sym)
            return sym, self.load_or_fetch_symbol_range(
                symbol=sym,
                timeframe=timeframe,
                start=start,
                end=end,
                file_path=file_map.get(sym),
                prefer_file=prefer_file,
                exchange_override=local_exchange,
            )

        with self._make_progress() as progress:
            task = progress.add_task("Chargement", total=len(symbols))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(_worker, s): s for s in symbols}
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        _, df = fut.result()
                        results[sym] = df
                    except Exception as e:
                        errors[sym] = e
                        progress.console.print(f"[red][ERREUR][/red] {sym}: {e}")
                    progress.advance(task)

        if errors:
            failed = ", ".join(errors)
            raise RuntimeError(f"Echec pour {len(errors)} symbole(s): {failed}")

        return {s: results[s] for s in symbols if s in results}
