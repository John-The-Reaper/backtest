import numpy as np
import pandas as pd


class ZScoreStrategy:
    """Strategie z-score mean-reversion sur un actif."""

    def __init__(self):
        self.name = "ZScoreStrategy"

    def prepare_data(self, data_dict, symbol):
        asset_name = symbol.split("/")[0]
        close = pd.DataFrame({asset_name: data_dict[symbol]["close"].astype(float)})
        close = close.dropna().sort_index()
        return close, asset_name

    def generate_signals(self, data, asset_name, params):
        data = data.copy()

        window = int(params.get("window", 12))
        z_score_entry = float(params.get("z_score_entry", 2.0))
        z_score_exit = float(params.get("z_score_exit", 0.0))

        data["Mean"] = data[asset_name].rolling(window=window).mean()
        data["Std"] = data[asset_name].rolling(window=window).std(ddof=0)
        data["Std"] = data["Std"].replace(0.0, np.nan)
        data["Z-Score"] = (data[asset_name] - data["Mean"]) / data["Std"]
        data = data.ffill().bfill()

        entries = data["Z-Score"] < -z_score_entry
        exits = data["Z-Score"] >= z_score_exit

        entries_df = pd.DataFrame({asset_name: entries.fillna(False)}, index=data.index)
        exits_df = pd.DataFrame({asset_name: exits.fillna(False)}, index=data.index)

        return data, entries_df, exits_df
