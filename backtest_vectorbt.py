from __future__ import annotations

import concurrent.futures
import json
import os
import traceback

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

    def _subset_data(self, data_dict, symbol):
        """
        Ne garde que les series utiles a la simulation de `symbol`.

        Evite de pickler tout data_dict vers chaque worker du
        ProcessPoolExecutor (N symboles x N DataFrames sinon). Les symboles
        annexes sont lus sur la strategie via les attributs conventionnels
        `lead_symbol` / `reference_symbol` ; une strategie qui a besoin
        d'autres series peut exposer `required_symbols(symbol) -> iterable`.
        """
        required = getattr(self.strategy, "required_symbols", None)
        if callable(required):
            needed = {symbol, *required(symbol)}
        else:
            needed = {symbol}
            for attr in ("lead_symbol", "reference_symbol"):
                extra = getattr(self.strategy, attr, None)
                if extra:
                    needed.add(extra)
        return {s: df for s, df in data_dict.items() if s in needed}

    def _resolve_params(self, params):
        if params is not None:
            return params
        if self.config is None or not hasattr(self.config, "default_params"):
            raise ValueError("params doit etre fourni ou self.config.default_params doit exister")
        return self.config.default_params

    def run_simulation(self, symbol, data_dict, params=None, reload=False):
        params = self._resolve_params(params)

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

        load_from_file = os.path.exists(portfolio_path) and not reload

        if load_from_file:
            pf = vbt.Portfolio.load(portfolio_path)
        else:
            signals = self.strategy.generate_signals(data, asset_name, params)
            if not isinstance(signals, tuple) or len(signals) not in (3, 5):
                raise ValueError("generate_signals doit retourner un tuple de 3 ou 5 elements")

            if len(signals) == 3:
                data, entries_df, exits_df = signals
                short_entries_df = short_exits_df = None
            else:
                data, entries_df, exits_df, short_entries_df, short_exits_df = signals

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

            size = getattr(self.strategy, "_size", None)
            if size is not None:
                kwargs["size"] = size.reindex(close.index).ffill().bfill().values

            pf = vbt.Portfolio.from_signals(**kwargs)
            pf.save(portfolio_path)

        close = data[asset_name].astype(float)
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

    def run_batch_simulations(self, symbols, data_dict, params=None, reload=False, max_workers=None):
        params = self._resolve_params(params)
        results = {}
        portfolios_data = {}
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.run_simulation,
                    symbol=symbol,
                    data_dict=self._subset_data(data_dict, symbol),
                    params=params,
                    reload=reload,
                ): symbol
                for symbol in symbols
            }
            for future in concurrent.futures.as_completed(futures):
                symbol = futures[future]
                try:
                    res = future.result()
                    results[symbol] = res
                    portfolios_data[symbol] = res["portfolio_data"]
                except Exception as e:
                    print(f"[ERREUR] Simulation {symbol} : {e}")
                    print(traceback.format_exc())

        return results, portfolios_data

    def export_results_to_json(
        self,
        portfolios_data,
        symbols,
        output_path=None,
        params=None,
    ):
        if output_path is None:
            output_path = os.path.join("backtests", "results", "results.json")
        params = self._resolve_params(params)
        capital_initial = float(params.get("capital_initial", 0.0))

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

        payload = {"capital_initial": capital_initial, "results": rows}
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        if equity_curves:
            eq_df = pd.DataFrame(equity_curves)
            eq_path = output_path.replace(".json", "_equity_curves.csv")
            eq_df.to_csv(eq_path)

        print(f"Resultats exportes: {output_path}")
