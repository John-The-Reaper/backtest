from __future__ import annotations

from abc import ABC, abstractmethod
import pandas as pd


class Provider(ABC):
    """
    Interface minimale pour une source de donnees OHLCV.

    Toute nouvelle source (ccxt, yfinance, databento, alpaca, ...) implemente
    juste `name` et `fetch`. Le `DataManager` ne connait que cette interface.
    """

    name: str  # identifiant court utilise dans le nom de fichier cache

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """
        Retourne un DataFrame avec les colonnes:
            timestamp (int64, ms epoch UTC), open, high, low, close, volume

        Doit etre trie par timestamp croissant, sans doublons.
        Peut retourner un DataFrame vide si aucune donnee dans l'intervalle.
        """
        raise NotImplementedError

    def validate(self, symbol: str) -> None:
        """
        Verifie que le symbole existe et est exploitable.
        Implementation par defaut: no-op. Les providers peuvent override.
        """
        return None