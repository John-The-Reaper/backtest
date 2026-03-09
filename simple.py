import pandas as pd


class MovingAverageStrategy:
    """Strategie croisement de moyennes mobiles."""

    def __init__(self, name="MovingAverageStrategy"):
        self.name = name

    def prepare_data(self, data_dict, symbol):
        asset_name = symbol.split("/")[0]
        close = pd.DataFrame({asset_name: data_dict[symbol]["close"].astype(float)})
        close = close.dropna().sort_index()
        return close, asset_name

    def generate_signals(self, data, asset_name, params):
        data = data.copy()

        ma_short_period = int(params.get("ma_short", 20))
        ma_long_period = int(params.get("ma_long", 50))

        data[f"MA_{ma_short_period}"] = data[asset_name].rolling(window=ma_short_period).mean()
        data[f"MA_{ma_long_period}"] = data[asset_name].rolling(window=ma_long_period).mean()

        ma_short = data[f"MA_{ma_short_period}"]
        ma_long = data[f"MA_{ma_long_period}"]

        entries = (ma_short > ma_long) & (ma_short.shift(1) <= ma_long.shift(1))
        exits = (ma_short < ma_long) & (ma_short.shift(1) >= ma_long.shift(1))

        entries_df = pd.DataFrame({asset_name: entries.fillna(False)}, index=data.index)
        exits_df = pd.DataFrame({asset_name: exits.fillna(False)}, index=data.index)

        return data, entries_df, exits_df
