# Backtest crypto

Ce repo contient un flux simple en 3 etapes :

1. telechargement OHLCV spot via `ccxt`
2. backtest vectorbt
3. analyse/export des resultats

## Fichiers utilises

- `backtest_zscore_crypto.py` : point d'entree principal
- `data_manager.py` : telechargement, cache local et validation des symboles
- `backtest_vectorbt.py` : execution du portefeuille vectorbt
- `backtest_analysis.py` : rapport CSV / JSON / graphiques

## Prerequis

- Python avec `ccxt`, `pandas`, `numpy`, `pyarrow`, `vectorbt`, `matplotlib`
- acces reseau vers l'exchange configure
- symboles au format `BASE/QUOTE` (ex: `BTC/USDT`)

## Contraintes de fonctionnement

- marche supporte : spot uniquement
- exchange par defaut : `binance`
- timeframe :
  `data_manager.py` n'impose pas de liste fixe. Le timeframe doit etre accepte par l'exchange `ccxt` choisi et par `exchange.parse_timeframe(...)`.
  Exemples deja utilises dans le repo : `5m`, `1h`, `4h`, `1d`
- dates :
  `START_DATE` et `END_DATE` acceptent une string parseable par `pandas.to_datetime`, un timestamp secondes ou millisecondes, ou un `datetime`
- plage de temps :
  il faut `start < end`
- duree `DataManager.parse_duration(...)` :
  formats acceptes `7j`, `14d`, `2w`, `6h`, `1mois`, `3mois`, `1an`, `1y`
- fenetre z-score :
  `window` doit etre au moins `2` en pratique, sinon l'ecart-type devient nul et les signaux disparaissent
- cache :
  les donnees sont stockees dans `data/cache/*.feather`
- resultats :
  les portfolios vectorbt sont stockes dans `backtests/portfolios/*.pkl`
  le resume JSON, les equity curves CSV, les rapports et les graphiques sont ecrits dans `backtests/results/`

## Limites pratiques

- pas de limite maximale codee sur la periode telechargee
- les donnees sont telechargees par pagination de `1000` bougies par requete
- la limite reelle depend donc du timeframe, du nombre de symboles, du rate limit exchange, du temps reseau et de l'espace disque

## Execution

Lancer le backtest principal :

```bash
python backtest_zscore_crypto.py
```

Lancer uniquement l'analyse a partir d'un resume existant :

```bash
python backtest_analysis.py --summary backtests/results/backtest_zscore_summary.json
```

## Sorties generees

- `backtests/portfolios/*.pkl`
- `backtests/results/backtest_zscore_summary.json`
- `backtests/results/backtest_zscore_summary_equity_curves.csv`
- `backtests/results/backtest_analysis.csv`
- `backtests/results/backtest_analysis_global.json`
- `backtests/results/graphs/*.png`
