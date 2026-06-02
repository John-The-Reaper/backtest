# Backtest Crypto Multi-Actifs

Ce projet est une alternative perso pour faire du backtest multi-actifs sur crypto avec une seule base simple :

- recuperation des donnees
- reutilisation de fichiers `.feather` si ils existent deja
- execution du backtest
- analyse des resultats

L'idee de depart etait simple : ne pas dependre d'un workflow trop lourd ou trop opaque, et garder un projet lisible qui permet de tester rapidement une strategie sur plusieurs cryptomonnaies.

Aujourd'hui, le cas principal du repo est une strategie **z-score** appliquee a un univers de cryptos spot, avec backtest via `vectorbt`.

## Base du projet

Le projet s'appuie sur 5 briques :

- [`data_manager/`](/home/faucheur/code/backtest/data_manager) : package de recuperation OHLCV. Cache Feather + providers interchangeables (`CCXTProvider`, `YFinanceProvider`, `IBKRProvider`)
- [`broker/`](/home/faucheur/code/backtest/broker) : execution d'ordres reels ou paper sur Binance / IBKR / Saxo Banque (interface unique, ordres normalises)
- [`backtest_zscore_crypto.py`](/home/faucheur/code/backtest/backtest_zscore_crypto.py) : point d'entree principal pour la strategie z-score
- [`backtest_vectorbt.py`](/home/faucheur/code/backtest/backtest_vectorbt.py) : moteur de backtest multi-actifs base sur `vectorbt`
- [`backtest_analysis.py`](/home/faucheur/code/backtest/backtest_analysis.py) : export CSV / JSON + graphiques

## Pourquoi je l'ai developpe

Je l'ai developpe pour avoir une alternative simple permettant :

- le backtest multi-actifs
- la recuperation de donnees integree
- la reutilisation de donnees locales en `.feather`
- une analyse lisible des resultats

Le but n'etait pas de faire une usine a gaz, mais un outil concret pour iterer vite sur des idees de strategies quantitatives.

## Strategie actuelle

La strategie principale du projet est une strategie de **mean reversion basee sur le z-score**.

L'idee est la suivante :

- on calcule une moyenne et un ecart-type sur une fenetre glissante
- on mesure a quel point le prix s'ecarte de son comportement recent
- si l'ecart devient trop fort, on considere qu'il y a peut-etre un exces de marche
- on teste ensuite si le prix revient vers sa moyenne


## Exemple de configuration

Le script principal est configure directement dans [`backtest_zscore_crypto.py`](/home/faucheur/code/backtest/backtest_zscore_crypto.py).

Exemple reel :

```python
START_DATE = "2022-03-28"
END_DATE = "2024-12-31"

SYMBOLS = [
    "BNB/USDT", "XRP/USDT", "INJ/USDT", "ETH/USDT", "BTC/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "DOT/USDT", ....
]

TIMEFRAME = "1h"
EXCHANGE = "binance"
```

Les parametres de la strategie sont centralises dans `Config.default_params` :

```python
self.default_params = {
    "z_score_entry": 2.0,
    "z_score_exit": 0.0,
    "window": 12,
    "capital_initial": 100.0,
    "timeframe": TIMEFRAME,
    "fees": 0.000,
    "slippage_base": 0.0005,
}
```

## Exemple de chargement des donnees

Le `DataManager` peut utiliser les fichiers `.feather` si ils existent deja, ou telecharger les donnees sinon.

Exemple :

```python
from data_manager import DataManager, CCXTProvider

data_manager = DataManager(CCXTProvider("binance"), data_dir="data")

data_dict = data_manager.get_many(
    symbols=SYMBOLS,
    timeframe="1h",
    start="2022-03-28",
    end="2024-12-31",
    max_workers=8,
)
```

Si un fichier comme `data/BTC_USDT_1h_binance.feather` est present, il est relu et seuls les intervalles manquants sont retelecharges.

Le provider peut etre echange selon la classe d'actif :

```python
from data_manager import DataManager, CCXTProvider, YFinanceProvider

# Crypto - mode auto: essaie binance, bybit, kraken, okx, coinbase, kucoin
dm_crypto = DataManager(CCXTProvider())

# Actions / ETF / indices via yfinance
dm_stocks = DataManager(YFinanceProvider())
mstr = dm_stocks.get("MSTR", "1d", "2023-01-01", "2024-12-31")
```

Pour un seul symbole, utiliser `get(...)` au lieu de `get_many(...)`.

## Exemple de backtest

Le moteur de backtest est dans [`backtest_vectorbt.py`](/home/faucheur/code/backtest/backtest_vectorbt.py), mais l'utilisation concrete ressemble a ca :

```python
strategy = ZScoreStrategy()
simulator = TradingSimulator(strategy=strategy, config=config)

results_dict, portfolios_data = simulator.run_batch_simulations(
    symbols=SYMBOLS,
    data_dict=data_dict,
    reload=True,
)
```

La strategie retourne simplement des signaux d'entree et de sortie :

```python
entries = data["Z-Score"] < -z_entry
exits = data["Z-Score"] >= z_exit
```

Et le moteur se charge ensuite de construire les portfolios `vectorbt`, de calculer les metriques et de sauvegarder les `.pkl`.

## Exemple d'analyse

Une fois le backtest termine, le resume JSON est produit puis relu par l'analyseur :

```python
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
```

## Execution

Le script principal est :

```bash
python3 backtest_zscore_crypto.py
```

Pour relancer uniquement l'analyse sur un resume existant :

```bash
python3 backtest_analysis.py --summary backtests/results/backtest_zscore_summary.json
```

## Format des donnees

Les fichiers de cache sont nommes `<symbol>_<timeframe>_<source>.feather` :

```text
data/BTC_USDT_1h_binance.feather
data/ETH_USDT_1h_binance.feather
data/MSTR_1d_yfinance.feather
```

Le mode hybride est integre : si le fichier existe il est charge, et seuls les intervalles manquants pour la plage demandee sont telecharges puis fusionnes.

## Resultats generes

Les sorties principales sont :

- `backtests/portfolios/*.pkl` : portfolios vectorbt
- `backtests/results/backtest_zscore_summary.json` : resume des performances
- `backtests/results/backtest_zscore_summary_equity_curves.csv` : equity curves exportees
- `backtests/results/backtest_analysis.csv` : tableau d'analyse
- `backtests/results/backtest_analysis_global.json` : stats globales
- `backtests/results/graphs/*.png` : graphiques

## Screens

### Equity curve globale

![Equity curve globale](backtests/results/graphs/equity_curve_global.png)

### Equity curves individuelles

![Equity curves individuelles](backtests/results/graphs/equity_curves_individual.png)

### Drawdown global

![Drawdown global](backtests/results/graphs/drawdown_global.png)

### Rendement par actif

![Rendement par actif](backtests/results/graphs/rendement_par_asset.png)

### Sharpe vs drawdown

![Sharpe vs drawdown](backtests/results/graphs/sharpe_vs_drawdown.png)

## Execution live (paper / real)

Le package [`broker/`](/home/faucheur/code/backtest/broker) ajoute une couche d'execution d'ordres reels, totalement decouplee du backtest. Une interface unique (`Broker`) pour les 3 brokers supportes :

- **`BinanceBroker`** (spot) via `ccxt` — testnet `binance.vision` quand `paper=True`
- **`IBKRBroker`** via `ib_insync` — TWS / IB Gateway paper (port 7497 / 4002) quand `paper=True`
- **`SaxoBroker`** via REST OpenAPI — endpoint `gateway.saxobank.com/sim/openapi` quand `paper=True`

Les ordres, positions et balances sont normalises dans des dataclasses (`Order`, `Position`, `Balance`, `Fill`) et toutes les exceptions natives sont wrappees dans `BrokerError` et ses sous-classes (`AuthError`, `InsufficientFunds`, `InvalidOrder`, `OrderNotFound`, `SymbolNotFound`, `RateLimited`, `BrokerConnectionError`).

Credentials via `.env` (voir `.env.example`) :

```text
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=
SAXO_ACCESS_TOKEN=...
SAXO_ACCOUNT_KEY=
```

Exemple minimal (testnet Binance) :

```python
from broker import BinanceBroker, OrderSide, OrderType

bro = BinanceBroker(paper=True)              # lit .env
bro.validate("BTC/USDT")

mkt = bro.place_order("BTC/USDT", OrderSide.BUY, 0.001, OrderType.MARKET)
print(bro.get_position("BTC/USDT"))

lmt = bro.place_order("BTC/USDT", OrderSide.SELL, 0.001, OrderType.LIMIT, price=200_000)
bro.cancel_order(lmt.id, symbol="BTC/USDT")

print(bro.get_balance())
```

Voir [`examples/live_broker_demo.py`](/home/faucheur/code/backtest/examples/live_broker_demo.py).

### Smart entry (marches illiquides)

Pour entrer en position au meilleur prix possible sans rester pendu indefiniment, chaque broker expose `smart_buy` / `smart_sell` :

```python
bro.smart_buy("AAPL", 10, max_wait_s=10, steps=3, allow_market_fallback=True)
```

- **IBKR** : utilise le type d'ordre natif `MIDPRICE` (execution au mid NBBO ou mieux), puis fallback `MARKET` au bout de `max_wait_s` si pas rempli.
- **Binance / Saxo** : *climbing limit* — lit le quote, poste un LIMIT au mid, grimpe en `steps` paliers vers l'ask (BUY) ou le bid (SELL), avec fallback `MARKET` final.

Si `allow_market_fallback=False`, leve `InvalidOrder` si la quantite restante n'est pas remplie dans le temps imparti (utile pour ne jamais payer le spread complet).

## Limites actuelles

- crypto spot uniquement via ccxt (futures/perp rejetes a la validation)
- providers fournis : ccxt, yfinance, ibkr (ce dernier requiert TWS/IB Gateway)
- brokers V1 : market + limit seulement, pas de streaming/websocket de fills
- la qualite du backtest depend directement de la qualite des donnees chargees
- la strategie z-score actuelle reste une base de travail, pas un systeme de production
