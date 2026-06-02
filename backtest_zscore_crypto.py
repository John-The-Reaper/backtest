import os

from backtest_analysis import BacktestAnalyzer
from data_manager import DataManager, CCXTProvider
from backtest_vectorbt import TradingSimulator
from strategies import ZScorePairsStrategy

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


# ==================== MAIN ====================
if __name__ == "__main__":
    config = Config()

    data_manager = DataManager(CCXTProvider(EXCHANGE), data_dir=config.DATA_DIR)
    # BTC/USDT est charge en plus des followers (necessaire pour le spread)
    all_symbols = list(dict.fromkeys([LEAD_SYMBOL] + SYMBOLS))
    data_dict = data_manager.get_many(
        symbols=all_symbols,
        timeframe=TIMEFRAME,
        start=START_DATE,
        end=END_DATE,
        max_workers=config.nb_workers,
    )

    strategy = ZScorePairsStrategy(lead_symbol=LEAD_SYMBOL)
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
