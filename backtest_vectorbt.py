from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple
import concurrent.futures

import pandas as pd
import vectorbt as vbt


class TradingSimulator:
    """Moteur de backtest vectorbt (encapsule la logique portefeuille + reporting)."""

    def __init__(self, config=None, strategy=None):
        self.config = config
        self.strategy = strategy

        self.portfolio_dir = getattr(config, "PORTFOLIO_DIR", os.path.join("backtests", "portfolios"))
        os.makedirs(self.portfolio_dir, exist_ok=True)

    @staticmethod
    def _safe_float(value) -> float:
        if isinstance(value, (pd.Series, pd.DataFrame)):
            value = value.squeeze()
        out = float(value)
        return 0.0 if pd.isna(out) else out

    @staticmethod
    def _to_bool_frame(x, asset_name: str, index: pd.Index) -> pd.DataFrame:
        if isinstance(x, pd.DataFrame):
            if asset_name in x.columns:
                return pd.DataFrame({asset_name: x[asset_name].astype(bool)}, index=index)
            return pd.DataFrame({asset_name: x.iloc[:, 0].astype(bool)}, index=index)
        if isinstance(x, pd.Series):
            return pd.DataFrame({asset_name: x.astype(bool)}, index=index)
        return pd.DataFrame({asset_name: pd.Series(x, index=index).astype(bool)}, index=index)

    @staticmethod
    def _compute_beta(equity_curve: pd.Series, asset_close: pd.Series) -> float:
        equity_returns = equity_curve.astype(float).pct_change()
        asset_returns = asset_close.astype(float).pct_change()

        returns_df = pd.concat(
            [equity_returns.rename("equity"), asset_returns.rename("asset")],
            axis=1,
        ).dropna()

        if len(returns_df) < 2:
            return 0.0

        asset_variance = returns_df["asset"].var()
        if pd.isna(asset_variance) or asset_variance == 0.0:
            return 0.0

        beta = returns_df["equity"].cov(returns_df["asset"]) / asset_variance
        return 0.0 if pd.isna(beta) else float(beta)



    def run_simulation(self, symbol, data_dict, params=None, reload=False):
        if params is None:
            params = self.config.default_params

        asset_name = symbol.split("/")[0]
        strategy_name = getattr(self.strategy, "name", "Strategy")
        portfolio_path = os.path.join(self.portfolio_dir, f"{asset_name}_{strategy_name}_portfolio.pkl")
        data, asset_name = self.strategy.prepare_data(data_dict, symbol)

        if asset_name not in data.columns:
            if "close" in data.columns:
                data = data.copy()
                data[asset_name] = data["close"]
            else:
                raise ValueError(f"Colonne {asset_name} absente de data")

        close = data[asset_name].astype(float)

        if os.path.exists(portfolio_path) and not reload:
            pf = vbt.Portfolio.load(portfolio_path)
        else:
            signals = self.strategy.generate_signals(data, asset_name, params)
            if not isinstance(signals, tuple):
                raise ValueError("generate_signals doit retourner un tuple")

            short_entries_df = None
            short_exits_df = None

            if len(signals) == 3:
                data, entries_df, exits_df = signals
            elif len(signals) == 5:
                data, entries_df, exits_df, short_entries_df, short_exits_df = signals
            else:
                raise ValueError("generate_signals doit retourner 3 ou 5 elements")

            close = data[asset_name].astype(float)
            entries_df = self._to_bool_frame(entries_df, asset_name, close.index)
            exits_df = self._to_bool_frame(exits_df, asset_name, close.index)

            kwargs = {
                "close": close,
                "entries": entries_df[asset_name],
                "exits": exits_df[asset_name],
                "init_cash": params.get("capital_initial", 10_000.0),
                "fees": params.get("fees", 0.001),
                "slippage": params.get("slippage_base", 0.0),
                "freq": params.get("timeframe", "1h"),
            }

            if short_entries_df is not None and short_exits_df is not None:
                short_entries_df = self._to_bool_frame(short_entries_df, asset_name, close.index)
                short_exits_df = self._to_bool_frame(short_exits_df, asset_name, close.index)
                kwargs["short_entries"] = short_entries_df[asset_name]
                kwargs["short_exits"] = short_exits_df[asset_name]

            pf = vbt.Portfolio.from_signals(**kwargs)
            pf.save(portfolio_path)

        equity_curve = pf.value()
        beta_underlying = self._compute_beta(equity_curve, close.reindex(equity_curve.index).ffill().bfill())

        result = {
            "symbol": symbol,
            "final_value": self._safe_float(pf.final_value()),
            "total_profit": self._safe_float(pf.total_profit()),
            "total_return": self._safe_float(pf.total_return()),
            "annualized_return": self._safe_float(pf.annualized_return()),
            "sharpe_ratio": self._safe_float(pf.sharpe_ratio()),
            "beta_underlying": beta_underlying,
            "max_drawdown": self._safe_float(pf.max_drawdown()),
            "win_rate": self._safe_float(pf.trades.win_rate()),
            "trade_count": int(self._safe_float(pf.trades.count())),
            "portfolio_path": portfolio_path,
            "equity_curve": equity_curve,
        }

        return {"symbol": symbol, "portfolio_data": result}

    def run_batch_simulations(self, symbols, data_dict, params=None, reload=False):
        if params is None:
            params = self.config.default_params
        print("Avant")
        results = {}
        portfolios_data = {}
        with concurrent.futures.ProcessPoolExecutor() as executor:
            futures = {
                executor.submit(self.run_simulation, symbol=symbol, data_dict=data_dict, params=params, reload=reload): symbol
                for symbol in symbols
            }
            for future in concurrent.futures.as_completed(futures):
                symbol = futures[future]
                try:
                    res = future.result()
                    results[symbol] = res
                    portfolios_data[symbol] = res["portfolio_data"]
                except Exception as e:
                    print(f"Erreur lors de la simulation pour {symbol}: {e}")

        print("Après")

        return results, portfolios_data

    def export_results_to_json(self, portfolios_data, symbols, output_path=os.path.join("backtests", "results", "results.json")):
        rows = []
        equity_curves = {}

        for symbol in symbols:
            if symbol not in portfolios_data:
                continue
            perf = portfolios_data[symbol]
            eq = perf.get("equity_curve")
            start = str(eq.index[0]) if eq is not None and len(eq) > 0 else ""
            end = str(eq.index[-1]) if eq is not None and len(eq) > 0 else ""
            nb_rows = len(eq) if eq is not None else 0

            rows.append(
                {
                    "symbol": symbol,
                    "rows": nb_rows,
                    "start": start,
                    "end": end,
                    "final_value": perf["final_value"],
                    "total_profit": perf["total_profit"],
                    "total_return": perf["total_return"],
                    "annualized_return": perf["annualized_return"],
                    "sharpe_ratio": perf["sharpe_ratio"],
                    "beta_underlying": perf["beta_underlying"],
                    "max_drawdown": perf["max_drawdown"],
                    "win_rate": perf["win_rate"],
                    "trade_count": perf["trade_count"],
                    "portfolio_path": perf["portfolio_path"],
                }
            )
            if eq is not None:
                equity_curves[symbol] = eq

        payload = {"results": rows}
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        # Sauvegarde des equity curves en CSV pour l'analyse
        if equity_curves:
            eq_df = pd.DataFrame(equity_curves)
            eq_path = output_path.replace(".json", "_equity_curves.csv")
            eq_df.to_csv(eq_path)

        print(f"Resultats exportes: {output_path}")
        print(f"Resultats exportes: {output_path}")
