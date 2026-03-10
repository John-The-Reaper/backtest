import os
import numpy as np
import pandas as pd

from backtest_analysis import BacktestAnalyzer
from data_manager import DataManager
from backtest_vectorbt import TradingSimulator

# ==================== PARAMETRES ====================
START_DATE = "2022-03-28"  # format YYYY-MM-DD, ou timestamp
END_DATE = "2024-12-31"    # format YYYY-MM-DD, ou timestamp

SYMBOLS = [
    "BNB/USDT", "XRP/USDT", "INJ/USDT", "ETH/USDT", "BTC/USDT",
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
        self.nb_workers = 8

        self.default_params = {
            "z_score_entry": 2.0,
            "z_score_exit": 0.0,
            "window": 12,
            "capital_initial": 100.0,
            "timeframe": TIMEFRAME,
            "fees": 0.001,
            "slippage_base": 0.0005,
        }

        os.makedirs(self.DATA_DIR, exist_ok=True)
        os.makedirs(self.BACKTEST_DIR, exist_ok=True)


# ==================== STRATEGIE ====================
class ZScoreStrategy:
    """Strategie z-score single asset (mean reversion)."""

    def __init__(self):
        self.name = "ZScoreStrategy"

    def prepare_data(self, data_dict, symbol):
        asset_name = symbol.split("/")[0]

        close = pd.DataFrame({asset_name: data_dict[symbol]["close"].astype(float)})
        data = close.copy()
        data.dropna(inplace=True)

        return data, asset_name

    def generate_signals(self, data, asset_name, params):
        data = data.copy()
        data["Mean"] = data[asset_name].rolling(window=params["window"]).mean()
        data["Std"] = data[asset_name].rolling(window=params["window"]).std(ddof=0)
        data["Std"] = data["Std"].replace(0.0, np.nan)
        data["Z-Score"] = (data[asset_name] - data["Mean"]) / data["Std"]
        data = data.dropna()

        z_entry = float(params.get("z_score_entry", 2.0))
        z_exit = float(params.get("z_score_exit", 0.0))

        # Long entry quand sur-vendu ; sortie sur retour a la moyenne
        entries = data["Z-Score"] < -z_entry
        exits = data["Z-Score"] >= z_exit

        entries_df = pd.DataFrame({asset_name: entries}, index=data.index)
        exits_df = pd.DataFrame({asset_name: exits}, index=data.index)

        return data, entries_df, exits_df


# ==================== MAIN ====================
if __name__ == "__main__":
    config = Config()

    data_manager = DataManager(exchange_name=EXCHANGE, data_dir=config.DATA_DIR)
    data_dict = data_manager.download_range(
        symbols=SYMBOLS,
        timeframe=TIMEFRAME,
        start=START_DATE,
        end=END_DATE,
        max_workers=config.nb_workers,
    )

    strategy = ZScoreStrategy()
    simulator = TradingSimulator(strategy=strategy, config=config)

    # reload=True => recalcule et remplace les portefeuilles existants
    results_dict, portfolios_data = simulator.run_batch_simulations(
        symbols=SYMBOLS,
        data_dict=data_dict,
        reload=True,
    )

    summary_path = os.path.join(config.BACKTEST_DIR, "backtest_zscore_summary.json")
    simulator.export_results_to_json(
        portfolios_data,
        SYMBOLS,
        output_path=summary_path,
    )

    analyzer = BacktestAnalyzer(summary_path=summary_path)
    analyzer.run()
