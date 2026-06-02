"""
Demo minimal du package broker en mode paper (testnet Binance).

Prerequis :
  1) Creer une cle API sur https://testnet.binance.vision
  2) Copier .env.example en .env et y mettre BINANCE_API_KEY / BINANCE_API_SECRET
  3) Lancer : python examples/live_broker_demo.py
"""
from broker import (
    BinanceBroker,
    BrokerError,
    OrderSide,
    OrderType,
)


def main() -> None:
    bro = BinanceBroker(paper=True)  # lit .env
    symbol = "BTC/USDT"

    bro.validate(symbol)
    print("Balances initiales :")
    for b in bro.get_balance():
        print(f"  {b.currency}: total={b.total} free={b.free}")

    print(f"\n>> Market BUY 0.001 {symbol}")
    mkt = bro.place_order(symbol, OrderSide.BUY, 0.001, OrderType.MARKET)
    print(f"   order id={mkt.id} status={mkt.status.value} filled={mkt.filled_quantity}")

    pos = bro.get_position(symbol)
    print(f"\nPosition {symbol}: qty={pos.quantity}")

    print(f"\n>> Limit SELL 0.001 {symbol} @ 200000 (loin du marche)")
    lmt = bro.place_order(symbol, OrderSide.SELL, 0.001, OrderType.LIMIT, price=200_000)
    print(f"   order id={lmt.id} status={lmt.status.value}")

    open_orders = bro.get_open_orders(symbol)
    print(f"\nOpen orders sur {symbol} : {len(open_orders)}")

    print(f"\n>> Cancel {lmt.id}")
    try:
        bro.cancel_order(lmt.id, symbol=symbol)
    except BrokerError as e:
        print(f"   cancel error : {e}")

    open_orders = bro.get_open_orders(symbol)
    print(f"Open orders apres cancel : {len(open_orders)}")


if __name__ == "__main__":
    main()
