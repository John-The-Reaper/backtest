from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


class BacktestAnalyzer:
    """Analyse complete des resultats de backtest avec rapport console, CSV, JSON et graphiques."""

    def __init__(
        self,
        summary_path: str = os.path.join("backtests", "results", "backtest_zscore_summary.json"),
        graphs_dir: str = os.path.join("backtests", "results", "graphs"),
        out_csv: str = os.path.join("backtests", "results", "backtest_analysis.csv"),
        out_json: str = os.path.join("backtests", "results", "backtest_analysis_global.json"),
    ) -> None:
        self.summary_path = summary_path
        self.graphs_dir = graphs_dir
        self.out_csv = out_csv
        self.out_json = out_json

        self._equity_curves_path = summary_path.replace(".json", "_equity_curves.csv")

    # ------------------------------------------------------------------ IO
    def _load_summary(self) -> Dict[str, Any]:
        if not os.path.exists(self.summary_path):
            raise FileNotFoundError(f"Fichier introuvable: {self.summary_path}")
        with open(self.summary_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_equity_curves(self) -> Optional[pd.DataFrame]:
        if os.path.exists(self._equity_curves_path):
            df = pd.read_csv(self._equity_curves_path, index_col=0, parse_dates=True)
            if not df.empty:
                df = df.ffill().bfill()
                return df
        return None

    def _rebuild_equity_curves_from_pkl(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Reconstruit les equity curves a partir des fichiers .pkl de vectorbt."""
        try:
            import vectorbt as vbt
        except ImportError:
            print("  [warn] vectorbt non disponible, equity curves non generees")
            return None

        if "portfolio_path" not in df.columns:
            return None

        curves: Dict[str, pd.Series] = {}
        for _, row in df.iterrows():
            pkl_path = row["portfolio_path"]
            symbol = row["symbol"]
            if not os.path.exists(pkl_path):
                continue
            try:
                pf = vbt.Portfolio.load(pkl_path)
                curves[symbol] = pf.value()
            except Exception as e:
                print(f"  [warn] Impossible de charger {pkl_path}: {e}")

        if not curves:
            return None

        # Aligner sur un index commun
        all_idx = pd.DatetimeIndex([])
        for eq in curves.values():
            all_idx = all_idx.union(eq.index)
        all_idx = all_idx.sort_values()

        eq_df = pd.DataFrame(index=all_idx)
        for symbol, eq in curves.items():
            eq_df[symbol] = eq.reindex(all_idx).ffill().bfill()

        # Sauvegarder pour les prochains runs
        eq_df.to_csv(self._equity_curves_path)
        print(f"  -> Equity curves reconstruites depuis les .pkl ({len(curves)} assets)")
        return eq_df

    # --------------------------------------------------------- Tableaux
    def _build_table(self, payload: Dict[str, Any]) -> pd.DataFrame:
        rows = payload.get("results", [])
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        # Colonnes derivees pour le rapport
        df["return_pct"] = df["total_return"] * 100.0
        df["annualized_pct"] = df["annualized_return"] * 100.0
        df["max_dd_pct"] = df["max_drawdown"] * 100.0
        df["win_rate_pct"] = df["win_rate"] * 100.0
        if "beta_underlying" not in df.columns:
            df["beta_underlying"] = 0.0

        # Ratio rendement / risque
        df["reward_risk"] = np.where(
            df["max_drawdown"] != 0,
            df["total_return"] / df["max_drawdown"].abs(),
            0.0,
        )

        # Calmar ratio : rendement annualise / |max drawdown|
        df["calmar_ratio"] = np.where(
            df["max_drawdown"] != 0,
            df["annualized_return"] / df["max_drawdown"].abs(),
            0.0,
        )

        df = df.sort_values(by="total_return", ascending=False).reset_index(drop=True)
        return df

    # ------------------------------------------------------- Stats globales
    @staticmethod
    def _global_stats(df: pd.DataFrame, capital_initial: float = 100.0) -> Dict[str, Any]:
        if df.empty:
            return {}

        n = len(df)
        total_initial = capital_initial * n
        total_final = df["final_value"].sum()
        total_profit = df["total_profit"].sum()
        total_return = (total_final / total_initial - 1.0) if total_initial > 0 else 0.0

        profitable = df[df["total_return"] > 0]
        losing = df[df["total_return"] <= 0]

        return {
            "nb_assets": n,
            "capital_initial_total": round(total_initial, 2),
            "valeur_finale_totale": round(total_final, 2),
            "profit_total": round(total_profit, 2),
            "rendement_total_pct": round(total_return * 100, 2),
            # Rendements
            "rendement_moyen_pct": round(df["total_return"].mean() * 100, 2),
            "rendement_median_pct": round(df["total_return"].median() * 100, 2),
            "rendement_std_pct": round(df["total_return"].std() * 100, 2),
            "meilleur_asset": df.iloc[0]["symbol"] if not df.empty else "",
            "meilleur_return_pct": round(df.iloc[0]["total_return"] * 100, 2) if not df.empty else 0,
            "pire_asset": df.iloc[-1]["symbol"] if not df.empty else "",
            "pire_return_pct": round(df.iloc[-1]["total_return"] * 100, 2) if not df.empty else 0,
            # Sharpe
            "sharpe_moyen": round(df["sharpe_ratio"].mean(), 3),
            "sharpe_median": round(df["sharpe_ratio"].median(), 3),
            "sharpe_std": round(df["sharpe_ratio"].std(), 3),
            "calmar_moyen": round(df["calmar_ratio"].mean(), 3),
            "calmar_median": round(df["calmar_ratio"].median(), 3),
            "beta_moyen": round(df["beta_underlying"].mean(), 3),
            "beta_median": round(df["beta_underlying"].median(), 3),
            "beta_std": round(df["beta_underlying"].std(), 3),
            # Drawdown
            "max_dd_moyen_pct": round(df["max_drawdown"].mean() * 100, 2),
            "max_dd_pire_pct": round(df["max_drawdown"].min() * 100, 2),
            # Win rate
            "win_rate_moyen_pct": round(df["win_rate"].mean() * 100, 2),
            "win_rate_median_pct": round(df["win_rate"].median() * 100, 2),
            # Trades
            "trades_total": int(df["trade_count"].sum()),
            "trades_moyen": round(df["trade_count"].mean(), 1),
            # Profitabilite
            "nb_assets_profitables": int(len(profitable)),
            "nb_assets_perdants": int(len(losing)),
            "pct_assets_profitables": round(len(profitable) / n * 100, 1) if n > 0 else 0,
        }

    # ------------------------------------------------------- Graphiques
    def _plot_bar_returns(self, df: pd.DataFrame) -> None:
        """Bar chart horizontal des rendements par asset."""
        fig, ax = plt.subplots(figsize=(12, max(6, len(df) * 0.35)))
        colors = ["#2ecc71" if r > 0 else "#e74c3c" for r in df["return_pct"]]
        y_pos = range(len(df))
        ax.barh(y_pos, df["return_pct"], color=colors, edgecolor="none", height=0.7)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(df["symbol"], fontsize=9)
        ax.set_xlabel("Rendement total (%)")
        ax.set_title("Rendement par asset")
        ax.axvline(0, color="black", linewidth=0.8)
        ax.grid(axis="x", alpha=0.3)
        ax.invert_yaxis()
        for i, val in enumerate(df["return_pct"]):
            offset = 0.4 if val >= 0 else -0.4
            ha = "left" if val >= 0 else "right"
            ax.text(val + offset, i, f"{val:.1f}%", va="center", ha=ha, fontsize=8)
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "rendement_par_asset.png"), dpi=140)
        plt.close()

    def _plot_sharpe_vs_drawdown(self, df: pd.DataFrame) -> None:
        """Scatter : Sharpe ratio vs Max Drawdown."""
        fig, ax = plt.subplots(figsize=(10, 7))
        scatter = ax.scatter(
            df["max_dd_pct"],
            df["sharpe_ratio"],
            c=df["return_pct"],
            cmap="RdYlGn",
            s=60,
            edgecolors="grey",
            linewidths=0.5,
        )
        for _, row in df.iterrows():
            ax.annotate(
                row["symbol"].split("/")[0],
                (row["max_dd_pct"], row["sharpe_ratio"]),
                fontsize=7,
                alpha=0.8,
                textcoords="offset points",
                xytext=(5, 3),
            )
        plt.colorbar(scatter, label="Rendement total (%)")
        ax.set_xlabel("Max Drawdown (%)")
        ax.set_ylabel("Sharpe Ratio")
        ax.set_title("Sharpe Ratio vs Max Drawdown")
        ax.axhline(0, color="grey", linestyle="--", linewidth=0.7)
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "sharpe_vs_drawdown.png"), dpi=140)
        plt.close()

    def _plot_win_rate_vs_trades(self, df: pd.DataFrame) -> None:
        """Scatter : Win rate vs nombre de trades."""
        fig, ax = plt.subplots(figsize=(10, 7))
        scatter = ax.scatter(
            df["trade_count"],
            df["win_rate_pct"],
            c=df["return_pct"],
            cmap="RdYlGn",
            s=60,
            edgecolors="grey",
            linewidths=0.5,
        )
        for _, row in df.iterrows():
            ax.annotate(
                row["symbol"].split("/")[0],
                (row["trade_count"], row["win_rate_pct"]),
                fontsize=7,
                alpha=0.8,
                textcoords="offset points",
                xytext=(5, 3),
            )
        plt.colorbar(scatter, label="Rendement total (%)")
        ax.set_xlabel("Nombre de trades")
        ax.set_ylabel("Win Rate (%)")
        ax.set_title("Win Rate vs Nombre de trades")
        ax.axhline(50, color="grey", linestyle="--", linewidth=0.7)
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "winrate_vs_trades.png"), dpi=140)
        plt.close()

    def _plot_distribution_returns(self, df: pd.DataFrame) -> None:
        """Histogramme de la distribution des rendements."""
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(df["return_pct"], bins=20, color="#3498db", edgecolor="white", alpha=0.85)
        ax.axvline(df["return_pct"].mean(), color="red", linestyle="--", linewidth=1.2, label="Moyenne")
        ax.axvline(df["return_pct"].median(), color="orange", linestyle="--", linewidth=1.2, label="Mediane")
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Rendement total (%)")
        ax.set_ylabel("Nombre d'assets")
        ax.set_title("Distribution des rendements")
        ax.legend()
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "distribution_rendements.png"), dpi=140)
        plt.close()

    def _plot_equity_curves(self, eq_df: pd.DataFrame) -> None:
        """Equity curves individuelles normalisees + courbe globale + drawdown."""
        # 1) Courbes individuelles
        fig, ax = plt.subplots(figsize=(14, 7))
        for col in eq_df.columns:
            normalized = eq_df[col] / eq_df[col].iloc[0] - 1.0
            ax.plot(normalized.index, normalized.values, label=col.split("/")[0], linewidth=1.0)
        ax.set_title("Equity Curves Individuelles (normalisees)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Performance cumulee")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper left", fontsize=7, ncol=3)
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "equity_curves_individual.png"), dpi=140)
        plt.close()

        # 2) Courbe globale (moyenne equal-weight)
        global_eq = eq_df.mean(axis=1)
        global_norm = global_eq / global_eq.iloc[0] - 1.0

        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(global_norm.index, global_norm.values, color="black", linewidth=1.4)
        ax.set_title("Equity Curve Globale (moyenne equal-weight)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Performance cumulee")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "equity_curve_global.png"), dpi=140)
        plt.close()

        # 3) Drawdown global
        running_max = global_eq.cummax()
        dd = (global_eq / running_max - 1.0) * 100.0

        fig, ax = plt.subplots(figsize=(14, 5))
        ax.fill_between(dd.index, dd.values, 0, color="crimson", alpha=0.35)
        ax.set_title("Drawdown Global")
        ax.set_xlabel("Date")
        ax.set_ylabel("Drawdown (%)")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "drawdown_global.png"), dpi=140)
        plt.close()

    def _plot_heatmap_metrics(self, df: pd.DataFrame) -> None:
        """Heatmap des metriques cles par asset."""
        col_label_pairs = [
            ("return_pct", "Return %"),
            ("annualized_pct", "Ann. Return %"),
            ("sharpe_ratio", "Sharpe"),
            ("calmar_ratio", "Calmar"),
            ("reward_risk", "Reward/Risk"),
            ("max_dd_pct", "Max DD %"),
            ("win_rate_pct", "Win Rate %"),
            ("beta_underlying", "Beta"),
        ]
        cols = [c for c, _ in col_label_pairs]
        labels = [l for _, l in col_label_pairs]
        hm_df = df.set_index("symbol")[cols].copy()
        hm_df.columns = labels

        # Normalisation min-max par colonne pour la colormap.
        # Max DD % : valeurs negatives, min = pire → normalisation directe ok (pire=0=rouge).
        # Beta : colonne neutre, on l'inverse pour que beta proche de 0 soit vert.
        hm_norm = (hm_df - hm_df.min()) / (hm_df.max() - hm_df.min() + 1e-9)
        beta_col = labels.index("Beta")
        hm_norm.iloc[:, beta_col] = 1.0 - hm_norm.iloc[:, beta_col]

        fig, ax = plt.subplots(figsize=(10, max(6, len(df) * 0.35)))
        im = ax.imshow(hm_norm.values, aspect="auto", cmap="RdYlGn", interpolation="nearest")

        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_yticks(range(len(hm_df)))
        ax.set_yticklabels(hm_df.index, fontsize=8)

        # Annotations avec valeurs reelles
        for i in range(len(hm_df)):
            for j in range(len(labels)):
                val = hm_df.iloc[i, j]
                fmt = f"{val:.1f}" if abs(val) >= 1 else f"{val:.3f}"
                ax.text(j, i, fmt, ha="center", va="center", fontsize=7, color="black")

        ax.set_title("Heatmap des metriques par asset")
        plt.colorbar(im, ax=ax, shrink=0.6, label="Normalise (0=pire, 1=meilleur)")
        plt.tight_layout()
        plt.savefig(os.path.join(self.graphs_dir, "heatmap_metriques.png"), dpi=140)
        plt.close()

    # ------------------------------------------------------- Rapport console
    @staticmethod
    def _print_report(df: pd.DataFrame, stats: Dict[str, Any]) -> None:

        print("\n" + "=" * 80)
        print("              RAPPORT D'ANALYSE DU BACKTEST")
        print("=" * 80)

        # Tableau par asset
        display_cols = [
            "symbol", "return_pct", "annualized_pct", "sharpe_ratio", "calmar_ratio",
            "reward_risk", "beta_underlying", "max_dd_pct", "win_rate_pct", "trade_count", "final_value",
        ]
        display_names = {
            "symbol": "Symbol",
            "return_pct": "Return %",
            "annualized_pct": "Ann. Ret %",
            "sharpe_ratio": "Sharpe",
            "calmar_ratio": "Calmar",
            "reward_risk": "Rew/Risk",
            "beta_underlying": "Beta",
            "max_dd_pct": "Max DD %",
            "win_rate_pct": "Win Rate %",
            "trade_count": "Trades",
            "final_value": "Final Value",
        }
        table = df[display_cols].rename(columns=display_names).copy()
        for col in ["Return %", "Ann. Ret %", "Sharpe", "Calmar", "Rew/Risk", "Beta", "Max DD %", "Win Rate %", "Final Value"]:
            if col in table.columns:
                table[col] = table[col].apply(lambda x: f"{x:.2f}")
        table["Trades"] = table["Trades"].astype(int)

        print("\n--- PERFORMANCES PAR ASSET (trie par rendement) ---")
        print(table.to_string(index=False))

        # Stats globales
        print("\n--- STATISTIQUES GLOBALES ---")
        print(f"  Nombre d'assets           : {stats['nb_assets']}")
        print(f"  Capital initial total      : {stats['capital_initial_total']:.2f}")
        print(f"  Valeur finale totale       : {stats['valeur_finale_totale']:.2f}")
        print(f"  Profit total               : {stats['profit_total']:.2f}")
        print(f"  Rendement total            : {stats['rendement_total_pct']:.2f}%")
        print()
        print(f"  Rendement moyen            : {stats['rendement_moyen_pct']:.2f}%")
        print(f"  Rendement median           : {stats['rendement_median_pct']:.2f}%")
        print(f"  Ecart-type des rendements  : {stats['rendement_std_pct']:.2f}%")
        print(f"  Meilleur asset             : {stats['meilleur_asset']} ({stats['meilleur_return_pct']:.2f}%)")
        print(f"  Pire asset                 : {stats['pire_asset']} ({stats['pire_return_pct']:.2f}%)")
        print()
        print(f"  Sharpe moyen               : {stats['sharpe_moyen']:.3f}")
        print(f"  Sharpe median              : {stats['sharpe_median']:.3f}")
        print(f"  Sharpe ecart-type          : {stats['sharpe_std']:.3f}")
        print()
        print(f"  Calmar moyen               : {stats['calmar_moyen']:.3f}")
        print(f"  Calmar median              : {stats['calmar_median']:.3f}")
        print()
        print(f"  Beta moyen                 : {stats['beta_moyen']:.3f}")
        print(f"  Beta median                : {stats['beta_median']:.3f}")
        print(f"  Beta ecart-type            : {stats['beta_std']:.3f}")
        print()
        print(f"  Max Drawdown moyen         : {stats['max_dd_moyen_pct']:.2f}%")
        print(f"  Max Drawdown pire          : {stats['max_dd_pire_pct']:.2f}%")
        print()
        print(f"  Win rate moyen             : {stats['win_rate_moyen_pct']:.2f}%")
        print(f"  Win rate median            : {stats['win_rate_median_pct']:.2f}%")
        print()
        print(f"  Nombre total de trades     : {stats['trades_total']}")
        print(f"  Trades moyen par asset     : {stats['trades_moyen']:.1f}")
        print()
        print(f"  Assets profitables         : {stats['nb_assets_profitables']} / {stats['nb_assets']} ({stats['pct_assets_profitables']:.1f}%)")
        print(f"  Assets perdants            : {stats['nb_assets_perdants']} / {stats['nb_assets']}")
        print("=" * 80)

    # ------------------------------------------------------- Run
    def run(self) -> Dict[str, Any]:
        """Point d'entree principal : charge, analyse, genere le rapport complet."""

        os.makedirs(self.graphs_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.out_csv) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(self.out_json) or ".", exist_ok=True)

        # 1) Charger les donnees
        payload = self._load_summary()
        df = self._build_table(payload)

        if df.empty:
            print("Aucun resultat a analyser.")
            return {}

        eq_df = self._load_equity_curves()
        if eq_df is None:
            print("Reconstruction des equity curves depuis les portfolios .pkl ...")
            eq_df = self._rebuild_equity_curves_from_pkl(df)

        # 2) Stats globales — capital_initial est serialise dans le summary par
        # TradingSimulator.export_results_to_json depuis params.capital_initial.
        capital_initial = float(payload.get("capital_initial") or 0.0)
        if capital_initial <= 0 and not df.empty:
            # Fallback (anciens summaries sans le champ) : reconstitue par
            # final_value / (1 + total_return). Imprecis si total_return ~= -1.
            row0 = df.iloc[0]
            if row0["total_return"] != -1.0:
                estimated = row0["final_value"] / (1.0 + row0["total_return"])
                if estimated > 0:
                    capital_initial = round(estimated, 2)
        if capital_initial <= 0:
            capital_initial = 100.0

        stats = self._global_stats(df, capital_initial=capital_initial)

        # 3) Rapport console
        self._print_report(df, stats)

        # 4) Exports fichiers
        df.to_csv(self.out_csv, index=False)
        with open(self.out_json, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

        # 5) Graphiques
        print(f"\nGeneration des graphiques dans {self.graphs_dir}/ ...")
        self._plot_bar_returns(df)
        self._plot_sharpe_vs_drawdown(df)
        self._plot_win_rate_vs_trades(df)
        self._plot_distribution_returns(df)
        self._plot_heatmap_metrics(df)

        if eq_df is not None and not eq_df.empty:
            self._plot_equity_curves(eq_df)
            print(f"  -> {self.graphs_dir}/equity_curves_individual.png")
            print(f"  -> {self.graphs_dir}/equity_curve_global.png")
            print(f"  -> {self.graphs_dir}/drawdown_global.png")
        else:
            print("  [warn] Pas d'equity curves disponibles (ni CSV ni .pkl)")

        print(f"  -> {self.graphs_dir}/rendement_par_asset.png")
        print(f"  -> {self.graphs_dir}/sharpe_vs_drawdown.png")
        print(f"  -> {self.graphs_dir}/winrate_vs_trades.png")
        print(f"  -> {self.graphs_dir}/distribution_rendements.png")
        print(f"  -> {self.graphs_dir}/heatmap_metriques.png")
        print(f"\n  CSV  : {self.out_csv}")
        print(f"  JSON : {self.out_json}")
        print()

        return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Rapport d'analyse complet des backtests")
    parser.add_argument("--summary", default=os.path.join("backtests", "results", "backtest_zscore_summary.json"),
                        help="Chemin du fichier JSON de resultats")
    parser.add_argument("--graphs-dir", default=os.path.join("backtests", "results", "graphs"),
                        help="Dossier de sortie des graphiques")
    parser.add_argument("--out-csv", default=os.path.join("backtests", "results", "backtest_analysis.csv"),
                        help="Chemin du CSV de sortie")
    parser.add_argument("--out-json", default=os.path.join("backtests", "results", "backtest_analysis_global.json"),
                        help="Chemin du JSON de stats globales")
    args = parser.parse_args()

    analyzer = BacktestAnalyzer(
        summary_path=args.summary,
        graphs_dir=args.graphs_dir,
        out_csv=args.out_csv,
        out_json=args.out_json,
    )
    analyzer.run()


if __name__ == "__main__":
    main()
