import numpy as np
import pandas as pd


class PairsTradingStrategy:
    """
    Strategy 'pairs-like': spread z-score entre actif courant et symbole de reference.
    Par defaut reference = BTC/USDT.
    """

    def __init__(self, reference_symbol: str = "BTC/USDT") -> None:
        self.name = "PairsTradingStrategy"
        self.reference_symbol = reference_symbol

    def prepare_data(self, data_dict, symbol):
        asset_name = symbol.split("/")[0]

        ref_symbol = self.reference_symbol
        if ref_symbol == symbol:
            ref_symbol = "ETH/USDT" if "ETH/USDT" in data_dict else None
        if ref_symbol is None or ref_symbol not in data_dict:
            raise ValueError(f"Reference symbol indisponible pour {symbol}: {self.reference_symbol}")

        ref_name = ref_symbol.split("/")[0]

        data = pd.DataFrame(
            {
                asset_name: data_dict[symbol]["close"].astype(float),
                ref_name: data_dict[ref_symbol]["close"].astype(float),
            }
        )
        data = data.dropna().sort_index()
        return data, asset_name

    def generate_signals(self, data, asset_name, params):
        data = data.copy()

        window = int(params.get("window", 48))
        z_entry = float(params.get("z_score_entry", 2.0))
        z_exit = float(params.get("z_score_exit", 0.0))

        ref_col = [c for c in data.columns if c != asset_name][0]

        spread = data[asset_name] - data[ref_col]
        spread_mean = spread.rolling(window).mean()
        spread_std = spread.rolling(window).std(ddof=0).replace(0.0, np.nan)

        data["Spread"] = spread
        data["Spread_Z"] = (spread - spread_mean) / spread_std
        data = data.ffill().bfill()

        entries = data["Spread_Z"] < -z_entry
        exits = data["Spread_Z"] >= z_exit

        entries_df = pd.DataFrame({asset_name: entries.fillna(False)}, index=data.index)
        exits_df = pd.DataFrame({asset_name: exits.fillna(False)}, index=data.index)

        return data, entries_df, exits_df
