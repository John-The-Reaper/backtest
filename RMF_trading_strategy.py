import numpy as np
import pandas as pd


class RMEFTradingStrategy:
    """
    Mean-reversion avec bandes dynamiques + ROC.
    Compatible TradingSimulator (retourne long + short signals).
    """

    def __init__(self):
        self.name = "RMEFTradingStrategy"

    def prepare_data(self, data_dict, symbol):
        asset_name = symbol.split("/")[0]
        data = pd.DataFrame(
            {
                "open": data_dict[symbol]["open"].astype(float),
                "high": data_dict[symbol]["high"].astype(float),
                "low": data_dict[symbol]["low"].astype(float),
                "close": data_dict[symbol]["close"].astype(float),
                "volume": data_dict[symbol]["volume"].astype(float),
            }
        )
        data[asset_name] = data["close"]
        data = data.dropna().sort_index()
        return data, asset_name

    @staticmethod
    def _atr(high, low, close, period):
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                (high - low).abs(),
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.rolling(period).mean()

    def generate_signals(self, data, asset_name, params):
        data = data.copy()

        sma_period = int(params.get("smaPeriod", 25))
        bollinger_multiplier_base = float(params.get("bollingerMultiplierBase", 2.25))
        atr_period = int(params.get("atrPeriod", 14))
        roc_period = int(params.get("rocPeriod", 14))

        close = data["close"]
        high = data["high"]
        low = data["low"]

        sma = close.rolling(sma_period).mean()
        std = close.rolling(sma_period).std(ddof=0)
        atr = self._atr(high, low, close, atr_period)

        atr_factor = (atr / sma.replace(0.0, np.nan)).fillna(0.0)
        dynamic_mult = bollinger_multiplier_base + atr_factor

        upper_band = sma + dynamic_mult * std
        lower_band = sma - dynamic_mult * std

        roc = ((close / close.shift(roc_period)) - 1.0) * 100.0
        roc_threshold = (atr / sma.replace(0.0, np.nan)) * 100.0

        long_condition = (close < lower_band) & (roc > -roc_threshold)
        short_condition = (close > upper_band) & (roc < roc_threshold)

        # Sortie sur croisement prix/SMA
        cross_up = (close > sma) & (close.shift(1) <= sma.shift(1))
        cross_down = (close < sma) & (close.shift(1) >= sma.shift(1))
        exit_condition = cross_up | cross_down

        long_entries = long_condition.fillna(False)
        short_entries = short_condition.fillna(False)
        long_exits = (exit_condition & ~long_entries).fillna(False)
        short_exits = (exit_condition & ~short_entries).fillna(False)

        data["SMA"] = sma
        data["Upper_Band"] = upper_band
        data["Lower_Band"] = lower_band
        data["ROC"] = roc
        data = data.ffill().bfill()

        long_entries_df = pd.DataFrame({asset_name: long_entries}, index=data.index)
        long_exits_df = pd.DataFrame({asset_name: long_exits}, index=data.index)
        short_entries_df = pd.DataFrame({asset_name: short_entries}, index=data.index)
        short_exits_df = pd.DataFrame({asset_name: short_exits}, index=data.index)

        return data, long_entries_df, long_exits_df, short_entries_df, short_exits_df
