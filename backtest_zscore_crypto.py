import os
import numpy as np
import pandas as pd

from backtest_analysis import BacktestAnalyzer
from data_manager import DataManager, CCXTProvider
from backtest_vectorbt import TradingSimulator

# ==================== PARAMETRES ====================
START_DATE = "2022-03-28"  # format YYYY-MM-DD, ou timestamp
END_DATE = "2024-12-31"    # format YYYY-MM-DD, ou timestamp

LEAD_SYMBOL = "BTC/USDT"

# BTC/USDT est exclu : c'est le lead, pas un follower tradeable
SYMBOLS = [
    "BNB/USDT", "XRP/USDT", "INJ/USDT", "ETH/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "DOT/USDT",
    "LINK/USDT", "TRX/USDT", "LTC/USDT", "ATOM/USDT", "SOL/USDT",
    "ETC/USDT", "FIL/USDT", "XLM/USDT", "FET/USDT", "VET/USDT",
    "ALGO/USDT", "FLOW/USDT", "SAND/USDT", "EGLD/USDT",
    "QNT/USDT", "GALA/USDT", "NEO/USDT", "THETA/USDT",
    "XTZ/USDT", "MANA/USDT", "GRT/USDT", "SNX/USDT", "ZIL/USDT",
    "OP/USDT", "WOO/USDT", "PEOPLE/USDT", "DYDX/USDT", "NEAR/USDT",
]

TIMEFRAME = "1h"
EXCHANGE = "binance"


# ==================== CONFIGURATION ====================
class Config:
    """Classe de configuration centralisee"""

    def __init__(self):
        self.DATA_DIR = "data"
        self.BACKTEST_DIR = "backtests"
        self.PORTFOLIO_DIR = os.path.join(self.BACKTEST_DIR, "portfolios")
        self.RESULTS_DIR = os.path.join(self.BACKTEST_DIR, "results")
        self.GRAPHS_DIR = os.path.join(self.RESULTS_DIR, "graphs")
        self.nb_workers = 8

        self.default_params = {
            "z_score_entry": 2.0,
            "z_score_exit": 0.0,
            "z_score_stop": 4.0,       # coupe si le z-score descend sous -z_score_stop (mean reversion echouee)
            "btc_filter_window": 24,   # entre uniquement si BTC > MA(n) ; 0 = desactive
            "window": 12,
            "capital_initial": 100.0,
            "timeframe": TIMEFRAME,
            "fees": 0.000,
            "slippage_base": 0.0005,
        }

        os.makedirs(self.DATA_DIR, exist_ok=True)
        os.makedirs(self.BACKTEST_DIR, exist_ok=True)
        os.makedirs(self.PORTFOLIO_DIR, exist_ok=True)
        os.makedirs(self.RESULTS_DIR, exist_ok=True)
        os.makedirs(self.GRAPHS_DIR, exist_ok=True)


# ==================== STRATEGIE ====================
class ZScoreStrategy:
    """Strategie z-score sur le spread log(follower/BTC) — mean reversion pairs trading.

    BTC/USDT est le lead : ses mouvements servent de référence.
    Les signaux sont générés sur la paire follower (celle qui est tradée).
    """

    def __init__(self, lead_symbol: str = "BTC/USDT"):
        self.name = "ZScoreStrategy"
        self.lead_symbol = lead_symbol

    def prepare_data(self, data_dict, symbol):
        asset_name = symbol.split("/")[0]

        follower_close = data_dict[symbol]["close"].astype(float)
        btc_close = data_dict[self.lead_symbol]["close"].astype(float)

        data = pd.DataFrame({
            asset_name: follower_close,
            "BTC": btc_close,
        })
        data.dropna(inplace=True)

        return data, asset_name

    def generate_signals(self, data, asset_name, params):
        data = data.copy()

        # Spread log : positif = follower cher vs BTC, négatif = follower bon marché
        data["Spread"] = np.log(data[asset_name]) - np.log(data["BTC"])

        data["Mean"] = data["Spread"].rolling(window=params["window"]).mean()
        data["Std"] = data["Spread"].rolling(window=params["window"]).std(ddof=0)
        data["Std"] = data["Std"].replace(0.0, np.nan)
        data["Z-Score"] = (data["Spread"] - data["Mean"]) / data["Std"]
        data = data.dropna()

        z_entry = float(params.get("z_score_entry", 2.0))
        z_exit = float(params.get("z_score_exit", 0.0))
        z_stop = float(params.get("z_score_stop", 0.0))
        btc_filter_window = int(params.get("btc_filter_window", 0))

        # Long follower quand spread sous-évalué vs BTC ; sortie au retour à la moyenne
        entries = data["Z-Score"] < -z_entry
        exits = data["Z-Score"] >= z_exit

        # Stop-loss z-score : coupe si le spread diverge trop (mean reversion echouee)
        if z_stop > 0:
            exits = exits | (data["Z-Score"] < -z_stop)

        # Filtre BTC : n'entre pas en long quand BTC est sous sa MA (marche baissier)
        if btc_filter_window > 0:
            btc_ma = data["BTC"].rolling(window=btc_filter_window).mean()
            entries = entries & (data["BTC"] > btc_ma)

        entries_df = pd.DataFrame({asset_name: entries}, index=data.index)
        exits_df = pd.DataFrame({asset_name: exits}, index=data.index)

        return data, entries_df, exits_df


# ==================== MAIN ====================
if __name__ == "__main__":
    config = Config()

    data_manager = DataManager(CCXTProvider(EXCHANGE), data_dir=config.DATA_DIR)
    # BTC/USDT est chargé en plus des followers (nécessaire pour le spread)
    all_symbols = list(dict.fromkeys([LEAD_SYMBOL] + SYMBOLS))
    data_dict = data_manager.get_many(
        symbols=all_symbols,
        timeframe=TIMEFRAME,
        start=START_DATE,
        end=END_DATE,
        max_workers=config.nb_workers,
    )

    strategy = ZScoreStrategy(lead_symbol=LEAD_SYMBOL)
    simulator = TradingSimulator(strategy=strategy, config=config)

    results_dict, portfolios_data = simulator.run_batch_simulations(
        symbols=SYMBOLS,
        data_dict=data_dict,
        reload=True,
    )

    summary_path = os.path.join(config.RESULTS_DIR, "backtest_zscore_summary.json")
    simulator.export_results_to_json(
        portfolios_data,
        SYMBOLS,
        output_path=summary_path,
    )

    analyzer = BacktestAnalyzer(
        summary_path=summary_path,
        graphs_dir=config.GRAPHS_DIR,
        out_csv=os.path.join(config.RESULTS_DIR, "backtest_analysis.csv"),
        out_json=os.path.join(config.RESULTS_DIR, "backtest_analysis_global.json"),
    )
    analyzer.run()
