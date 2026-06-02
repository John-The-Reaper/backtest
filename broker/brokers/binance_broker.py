from __future__ import annotations

from typing import List, Optional

import ccxt

from ..base import Broker
from ..env import getenv
from ..exceptions import (
    AuthError,
    BrokerConnectionError,
    BrokerError,
    InsufficientFunds,
    InvalidOrder,
    OrderNotFound,
    RateLimited,
    SymbolNotFound,
)
from ..models import (
    Balance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)


_CCXT_STATUS_MAP = {
    "open": OrderStatus.NEW,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED,
    "expired": OrderStatus.EXPIRED,
}


class BinanceBroker(Broker):
    """
    Broker Binance via ccxt. Spot uniquement pour le V1.

    Endpoints :
      - paper=True  -> testnet.binance.vision (cles dediees a creer)
      - paper=False -> binance.com (cles reelles)

    Credentials (env vars si non passes en argument) :
      BINANCE_API_KEY, BINANCE_API_SECRET
    """

    name = "binance"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper: bool = True,
    ) -> None:
        self.paper = paper
        api_key = api_key or getenv("BINANCE_API_KEY", required=True)
        api_secret = api_secret or getenv("BINANCE_API_SECRET", required=True)

        self._exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if paper:
            self._exchange.set_sandbox_mode(True)

    # ---- Helpers -------------------------------------------------------

    @staticmethod
    def _base_asset(symbol: str) -> str:
        if "/" not in symbol:
            raise SymbolNotFound(f"Symbole spot attendu BASE/QUOTE, recu : {symbol}")
        return symbol.split("/")[0]

    def _to_order(self, raw: dict) -> Order:
        status_raw = (raw.get("status") or "").lower()
        status = _CCXT_STATUS_MAP.get(status_raw, OrderStatus.NEW)
        filled = float(raw.get("filled") or 0.0)
        qty = float(raw.get("amount") or 0.0)
        if status == OrderStatus.FILLED and filled < qty and filled > 0:
            status = OrderStatus.PARTIALLY_FILLED

        price = raw.get("price")
        avg = raw.get("average")
        ts = raw.get("timestamp")
        return Order(
            id=str(raw.get("id")),
            client_order_id=raw.get("clientOrderId"),
            symbol=raw.get("symbol"),
            side=OrderSide(raw["side"]),
            type=OrderType(raw["type"]),
            quantity=qty,
            filled_quantity=filled,
            price=float(price) if price is not None else None,
            avg_fill_price=float(avg) if avg is not None else None,
            status=status,
            created_at=int(ts) if ts is not None else 0,
            raw=raw,
        )

    def _wrap(self, exc: Exception) -> BrokerError:
        if isinstance(exc, ccxt.AuthenticationError):
            return AuthError(str(exc))
        if isinstance(exc, ccxt.InsufficientFunds):
            return InsufficientFunds(str(exc))
        if isinstance(exc, ccxt.OrderNotFound):
            return OrderNotFound(str(exc))
        if isinstance(exc, ccxt.BadSymbol):
            return SymbolNotFound(str(exc))
        if isinstance(exc, ccxt.InvalidOrder):
            return InvalidOrder(str(exc))
        if isinstance(exc, ccxt.RateLimitExceeded):
            return RateLimited(str(exc))
        if isinstance(exc, (ccxt.NetworkError, ccxt.ExchangeNotAvailable)):
            return BrokerConnectionError(str(exc))
        return BrokerError(str(exc))

    # ---- Broker API ----------------------------------------------------

    def validate(self, symbol: str) -> None:
        try:
            markets = self._exchange.load_markets()
        except Exception as e:
            raise self._wrap(e) from e
        m = markets.get(symbol)
        if m is None:
            raise SymbolNotFound(f"Symbole inconnu sur binance : {symbol}")
        if m.get("active") is False:
            raise SymbolNotFound(f"Symbole inactif : {symbol}")
        if m.get("spot") is False:
            raise InvalidOrder(f"Symbole non spot : {symbol}")

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Order:
        if order_type == OrderType.MIDPRICE:
            raise InvalidOrder("OrderType.MIDPRICE n'est pas supporte par Binance (IBKR uniquement)")
        if order_type == OrderType.LIMIT and price is None:
            raise InvalidOrder("price est requis pour un ordre LIMIT")
        params = {}
        if client_order_id is not None:
            params["newClientOrderId"] = client_order_id
        try:
            raw = self._exchange.create_order(
                symbol, order_type.value, side.value, quantity, price, params
            )
        except Exception as e:
            raise self._wrap(e) from e
        return self._to_order(raw)

    def get_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        if symbol is None:
            raise InvalidOrder("Binance exige le symbole pour fetch_order")
        try:
            raw = self._exchange.fetch_order(order_id, symbol)
        except Exception as e:
            raise self._wrap(e) from e
        return self._to_order(raw)

    def get_quote(self, symbol: str) -> Quote:
        try:
            ticker = self._exchange.fetch_ticker(symbol)
        except Exception as e:
            raise self._wrap(e) from e
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is None or ask is None:
            raise BrokerError(f"Binance : quote incomplet pour {symbol} ({ticker})")
        return Quote(
            symbol=symbol,
            bid=float(bid),
            ask=float(ask),
            timestamp=int(ticker.get("timestamp") or 0),
        )

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        if symbol is None:
            raise InvalidOrder("Binance exige le symbole pour annuler un ordre")
        try:
            raw = self._exchange.cancel_order(order_id, symbol)
        except Exception as e:
            raise self._wrap(e) from e
        return self._to_order(raw)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        try:
            raws = self._exchange.fetch_open_orders(symbol)
        except Exception as e:
            raise self._wrap(e) from e
        return [self._to_order(r) for r in raws]

    def get_position(self, symbol: str) -> Position:
        base = self._base_asset(symbol)
        try:
            bal = self._exchange.fetch_balance()
        except Exception as e:
            raise self._wrap(e) from e
        total = float((bal.get("total") or {}).get(base, 0.0) or 0.0)
        return Position(
            symbol=symbol,
            quantity=total,
            avg_entry_price=None,  # non fourni par Binance spot
            unrealized_pnl=None,
            raw={"base": base, "balance": bal.get(base)},
        )

    def get_balance(self) -> List[Balance]:
        try:
            bal = self._exchange.fetch_balance()
        except Exception as e:
            raise self._wrap(e) from e
        free = bal.get("free") or {}
        used = bal.get("used") or {}
        total = bal.get("total") or {}
        out: List[Balance] = []
        for cur, t in total.items():
            t = float(t or 0.0)
            if t == 0.0:
                continue
            out.append(Balance(
                currency=cur,
                free=float(free.get(cur) or 0.0),
                used=float(used.get(cur) or 0.0),
                total=t,
            ))
        return out
