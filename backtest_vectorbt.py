from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import pandas as pd
import vectorbt as vbt


class TradingSimulator:
    """Moteur de backtest vectorbt (encapsule la logique portefeuille + reporting)."""

    def __init__(self, config=None, strategy=None):
        self.config = config
        self.strategy = strategy

        self.backtest_dir = getattr(config, "BACKTEST_DIR", "backtests")
        os.makedirs(self.backtest_dir, exist_ok=True)

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

    def run_simulation(self, symbol, data_dict, params=None, reload=False):
        if params is None:
            params = self.config.default_params

        asset_name = symbol.split("/")[0]
        strategy_name = getattr(self.strategy, "name", "Strategy")
        portfolio_path = os.path.join(self.backtest_dir, f"{asset_name}_{strategy_name}_portfolio.pkl")

        if os.path.exists(portfolio_path) and not reload:
            pf = vbt.Portfolio.load(portfolio_path)
            close = None
        else:
            data, asset_name = self.strategy.prepare_data(data_dict, symbol)

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

            if asset_name not in data.columns:
                if "close" in data.columns:
                    data = data.copy()
                    data[asset_name] = data["close"]
                else:
                    raise ValueError(f"Colonne {asset_name} absente de data")

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

        result = {
            "symbol": symbol,
            "final_value": self._safe_float(pf.final_value()),
            "total_profit": self._safe_float(pf.total_profit()),
            "total_return": self._safe_float(pf.total_return()),
            "annualized_return": self._safe_float(pf.annualized_return()),
            "sharpe_ratio": self._safe_float(pf.sharpe_ratio()),
            "max_drawdown": self._safe_float(pf.max_drawdown()),
            "win_rate": self._safe_float(pf.trades.win_rate()),
            "trade_count": int(self._safe_float(pf.trades.count())),
            "portfolio_path": portfolio_path,
            "equity_curve": pf.value(),
        }

        return {"symbol": symbol, "portfolio_data": result}

    def run_batch_simulations(self, symbols, data_dict, params=None, reload=False):
        if params is None:
            params = self.config.default_params

        results = {}
        portfolios_data = {}

        for symbol in symbols:
            if symbol not in data_dict or data_dict[symbol].empty:
                continue
            res = self.run_simulation(symbol=symbol, data_dict=data_dict, params=params, reload=reload)
            results[symbol] = res
            portfolios_data[symbol] = res["portfolio_data"]

        return results, portfolios_data

    def visualize_results(self, portfolios_data, symbols, capital_initial=None):
        if capital_initial is None:
            capital_initial = self.config.default_params.get("capital_initial", 10_000.0)

        rows = []
        for symbol in symbols:
            if symbol not in portfolios_data:
                continue
            perf = portfolios_data[symbol]
            rows.append(
                {
                    "Symbol": symbol,
                    "Final Value": perf["final_value"],
                    "Total Profit": perf["total_profit"],
                    "Return (%)": perf["total_return"] * 100.0,
                    "Annualized Return (%)": perf["annualized_return"] * 100.0,
                    "Sharpe Ratio": perf["sharpe_ratio"],
                    "Max Drawdown (%)": perf["max_drawdown"] * 100.0,
                    "Win Rate (%)": perf["win_rate"] * 100.0,
                    "Trade Count": perf["trade_count"],
                }
            )

        if not rows:
            print("Aucun resultat a visualiser")
            return {}

        perf_df = pd.DataFrame(rows).sort_values("Return (%)", ascending=False).reset_index(drop=True)
        print("\n=== TABLEAU DES PERFORMANCES ===")
        print(perf_df.to_string(index=False))

        total_initial_capital = capital_initial * len(perf_df)
        total_profit = perf_df["Total Profit"].sum()
        total_final_value = total_initial_capital + total_profit
        total_return = (total_final_value / total_initial_capital - 1.0) if total_initial_capital > 0 else 0.0

        print("\n=== PERFORMANCE GLOBALE ===")
        print(f"Capital initial total: {total_initial_capital:.2f}")
        print(f"Valeur finale totale: {total_final_value:.2f}")
        print(f"Profit total: {total_profit:.2f}")
        print(f"Rendement total: {total_return:.2%}")

        # Courbes d'equite
        all_idx = pd.DatetimeIndex([])
        for symbol in symbols:
            if symbol in portfolios_data:
                all_idx = all_idx.union(portfolios_data[symbol]["equity_curve"].index)
        all_idx = all_idx.sort_values()

        eq_df = pd.DataFrame(index=all_idx)
        for symbol in symbols:
            if symbol in portfolios_data:
                eq = portfolios_data[symbol]["equity_curve"].reindex(all_idx).ffill().bfill()
                eq_df[symbol] = eq

        if not eq_df.empty:
            plt.figure(figsize=(14, 7))
            for col in eq_df.columns:
                normalized = eq_df[col] / eq_df[col].iloc[0] - 1.0
                plt.plot(normalized.index, normalized.values, label=col, linewidth=1.1)
            plt.title("Equity Curves Individuelles")
            plt.xlabel("Date")
            plt.ylabel("Performance cumulee")
            plt.grid(alpha=0.3)
            plt.legend(loc="upper left", fontsize=8)
            plt.tight_layout()
            plt.savefig(os.path.join(self.backtest_dir, "equity_curves_individual.png"), dpi=140)
            plt.close()

            global_eq = eq_df.sum(axis=1)
            global_norm = global_eq / global_eq.iloc[0] - 1.0
            plt.figure(figsize=(14, 6))
            plt.plot(global_norm.index, global_norm.values, color="black", linewidth=1.4)
            plt.title("Equity Curve Globale")
            plt.xlabel("Date")
            plt.ylabel("Performance cumulee")
            plt.grid(alpha=0.3)
            plt.tight_layout()
            plt.savefig(os.path.join(self.backtest_dir, "equity_curve_global.png"), dpi=140)
            plt.close()

            running_max = global_eq.cummax()
            dd = global_eq / running_max - 1.0
            plt.figure(figsize=(14, 5))
            plt.fill_between(dd.index, dd.values, 0, color="crimson", alpha=0.35)
            plt.title("Drawdown Global")
            plt.xlabel("Date")
            plt.ylabel("Drawdown")
            plt.grid(alpha=0.3)
            plt.tight_layout()
            plt.savefig(os.path.join(self.backtest_dir, "drawdown_global.png"), dpi=140)
            plt.close()

        return {
            "total_initial_capital": float(total_initial_capital),
            "total_final_value": float(total_final_value),
            "total_profit": float(total_profit),
            "total_return": float(total_return),
            "performance_df": perf_df,
        }

    def export_results_to_json(self, portfolios_data, symbols, output_path="backtests/results.json"):
        rows = []
        for symbol in symbols:
            if symbol not in portfolios_data:
                continue
            perf = portfolios_data[symbol]
            rows.append(
                {
                    "symbol": symbol,
                    "final_value": perf["final_value"],
                    "total_profit": perf["total_profit"],
                    "total_return": perf["total_return"],
                    "annualized_return": perf["annualized_return"],
                    "sharpe_ratio": perf["sharpe_ratio"],
                    "max_drawdown": perf["max_drawdown"],
                    "win_rate": perf["win_rate"],
                    "trade_count": perf["trade_count"],
                    "portfolio_path": perf["portfolio_path"],
                }
            )

        payload = {"results": rows}
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Resultats exportes: {output_path}")
