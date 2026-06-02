import numpy as np
import pandas as pd


class ZScorePairsStrategy:
    """
    Strategie z-score sur le spread log(follower / lead) -- mean reversion pairs trading.

    Le `lead_symbol` est la reference (typiquement BTC/USDT). Les signaux sont
    generes sur la paire follower (celle qui sera tradee).
    """

    def __init__(self, lead_symbol: str = "BTC/USDT") -> None:
        self.name = "ZScorePairsStrategy"
        self.lead_symbol = lead_symbol

    def prepare_data(self, data_dict, symbol):
        asset_name = symbol.split("/")[0]

        follower_close = data_dict[symbol]["close"].astype(float)
        lead_close = data_dict[self.lead_symbol]["close"].astype(float)

        data = pd.DataFrame({
            asset_name: follower_close,
            "LEAD": lead_close,
        })
        data.dropna(inplace=True)

        return data, asset_name

    def generate_signals(self, data, asset_name, params):
        data = data.copy()

        # Spread log : positif = follower cher vs lead, negatif = follower bon marche
        data["Spread"] = np.log(data[asset_name]) - np.log(data["LEAD"])

        data["Mean"] = data["Spread"].rolling(window=params["window"]).mean()
        data["Std"] = data["Spread"].rolling(window=params["window"]).std(ddof=0).replace(0.0, np.nan)
        data["Z-Score"] = (data["Spread"] - data["Mean"]) / data["Std"]
        data = data.dropna()

        z_entry = float(params.get("z_score_entry", 2.0))
        z_exit = float(params.get("z_score_exit", 0.0))
        z_stop = float(params.get("z_score_stop", 0.0))
        lead_filter_window = int(params.get("btc_filter_window", 0))

        # Long follower quand spread sous-evalue vs lead ; sortie au retour a la moyenne
        entries = data["Z-Score"] < -z_entry
        exits = data["Z-Score"] >= z_exit

        # Stop-loss z-score : coupe si le spread diverge trop (mean reversion echouee)
        if z_stop > 0:
            exits = exits | (data["Z-Score"] < -z_stop)

        # Filtre lead : n'entre pas en long quand le lead est sous sa MA
        if lead_filter_window > 0:
            lead_ma = data["LEAD"].rolling(window=lead_filter_window).mean()
            entries = entries & (data["LEAD"] > lead_ma)

        entries_df = pd.DataFrame({asset_name: entries}, index=data.index)
        exits_df = pd.DataFrame({asset_name: exits}, index=data.index)

        return data, entries_df, exits_df
