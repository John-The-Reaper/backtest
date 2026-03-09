from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict

import pandas as pd


class BacktestAnalyzer:
    """Analyse un fichier de resume produit par backtest_vectorbt.py."""

    def __init__(self, summary_path: str) -> None:
        self.summary_path = summary_path

    def load_summary(self) -> Dict[str, Any]:
        if not os.path.exists(self.summary_path):
            raise FileNotFoundError(f"Fichier introuvable: {self.summary_path}")
        with open(self.summary_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def build_table(self, payload: Dict[str, Any]) -> pd.DataFrame:
        rows = payload.get("results", [])
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df = df[
            [
                "symbol",
                "rows",
                "start",
                "end",
                "total_return",
                "annualized_return",
                "sharpe_ratio",
                "max_drawdown",
                "win_rate",
                "trades",
                "final_value",
                "portfolio_path",
            ]
        ].copy()
        df = df.sort_values(by="total_return", ascending=False).reset_index(drop=True)
        return df

    @staticmethod
    def global_stats(df: pd.DataFrame) -> Dict[str, float]:
        if df.empty:
            return {}
        return {
            "assets": int(len(df)),
            "avg_total_return": float(df["total_return"].mean()),
            "median_total_return": float(df["total_return"].median()),
            "avg_sharpe": float(df["sharpe_ratio"].mean()),
            "avg_max_drawdown": float(df["max_drawdown"].mean()),
            "avg_win_rate": float(df["win_rate"].mean()),
            "total_trades": int(df["trades"].sum()),
            "avg_final_value": float(df["final_value"].mean()),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse des resultats backtest vectorbt")
    parser.add_argument("--summary", default="backtests/backtest_summary.json")
    parser.add_argument("--out-csv", default="backtests/backtest_analysis.csv")
    parser.add_argument("--out-json", default="backtests/backtest_analysis_global.json")
    args = parser.parse_args()

    analyzer = BacktestAnalyzer(args.summary)
    payload = analyzer.load_summary()
    table = analyzer.build_table(payload)

    if table.empty:
        print("Aucun resultat a analyser.")
        return

    stats = analyzer.global_stats(table)

    os.makedirs(os.path.dirname(args.out_csv) or ".", exist_ok=True)
    table.to_csv(args.out_csv, index=False)

    os.makedirs(os.path.dirname(args.out_json) or ".", exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(table.to_string(index=False))
    print("\nGlobal stats:")
    for k, v in stats.items():
        print(f"- {k}: {v}")
    print(f"\nCSV: {args.out_csv}")
    print(f"JSON: {args.out_json}")


if __name__ == "__main__":
    main()
