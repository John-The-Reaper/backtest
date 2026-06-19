from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional

from ..base import Broker
from ..env import getenv
from ..exceptions import (
    BrokerConnectionError,
    BrokerError,
    InvalidOrder,
    OrderNotFound,
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


# Statuts IBKR -> statuts normalises
_IBKR_STATUS_MAP = {
    "ApiPending": OrderStatus.NEW,
    "PendingSubmit": OrderStatus.NEW,
    "PendingCancel": OrderStatus.NEW,
    "PreSubmitted": OrderStatus.NEW,
    "Submitted": OrderStatus.NEW,
    "ApiCancelled": OrderStatus.CANCELED,
    "Cancelled": OrderStatus.CANCELED,
    "Filled": OrderStatus.FILLED,
    "Inactive": OrderStatus.REJECTED,
}


class IBKRBroker(Broker):
    """
    Broker Interactive Brokers via ib_insync (TWS ou IB Gateway requis).

    Prerequis :
      - TWS / IB Gateway lance et logge
      - "Enable ActiveX and Socket Clients" coche dans API settings
      - Port API ouvert

    Ports par defaut :
      - TWS         : 7497 (paper) / 7496 (live)
      - IB Gateway  : 4002 (paper) / 4001 (live)

    Credentials (env vars si non passes) :
      IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

    Notes :
      - clientId aleatoire par defaut pour eviter les collisions avec un
        IBKRProvider connecte en parallele
      - connexion persistante reutilisee, fermee via disconnect()
      - les symboles sont resolus en Stock SMART/USD par defaut ;
        passer un ib_insync.Contract complet pour futures/forex/options
    """

    name = "ibkr"

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        paper: bool = True,
        timeout: float = 15.0,
    ) -> None:
        try:
            from ib_insync import IB  # noqa: F401
        except ImportError as e:
            raise ImportError("ib_insync requis : pip install ib_insync") from e

        self.paper = paper
        self.host = host or getenv("IBKR_HOST", "127.0.0.1")
        env_port = getenv("IBKR_PORT")
        if port is not None:
            self.port = port
        elif env_port is not None:
            self.port = int(env_port)
        else:
            self.port = 7497 if paper else 7496
        env_cid = getenv("IBKR_CLIENT_ID")
        if client_id is not None:
            self.client_id = client_id
        elif env_cid is not None:
            self.client_id = int(env_cid)
        else:
            self.client_id = random.randint(1000, 9999)
        self.timeout = timeout
        self._ib = None
        self._orders: Dict[str, object] = {}  # order_id -> ib_insync.Trade

    # ---- Connexion -----------------------------------------------------

    def _ensure_connected(self):
        from ib_insync import IB
        try:
            if self._ib is None or not self._ib.isConnected():
                self._ib = IB()
                self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout)
        except Exception as e:
            raise BrokerConnectionError(
                f"Connexion IBKR impossible ({self.host}:{self.port}) : {e}"
            ) from e
        return self._ib

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
        self._ib = None

    def __deepcopy__(self, memo):
        # Pas de copie de socket : on rend une instance neuve, non connectee,
        # avec un clientId different pour eviter la collision.
        return IBKRBroker(
            host=self.host, port=self.port, client_id=None,
            paper=self.paper, timeout=self.timeout,
        )

    # ---- Helpers -------------------------------------------------------

    def _resolve_contract(self, symbol):
        from ib_insync import Contract, Stock
        if isinstance(symbol, Contract):
            return symbol
        return Stock(symbol, "SMART", "USD")

    def _symbol_of(self, contract) -> str:
        sym = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", None)
        return str(sym) if sym is not None else ""

    def _to_order(self, trade) -> Order:
        order_obj = trade.order
        status_raw = trade.orderStatus.status if trade.orderStatus else "Submitted"
        status = _IBKR_STATUS_MAP.get(status_raw, OrderStatus.NEW)
        filled = float(trade.orderStatus.filled or 0.0) if trade.orderStatus else 0.0
        qty = float(order_obj.totalQuantity or 0.0)
        if status == OrderStatus.NEW and 0 < filled < qty:
            status = OrderStatus.PARTIALLY_FILLED

        side = OrderSide.BUY if order_obj.action == "BUY" else OrderSide.SELL
        if order_obj.orderType == "MKT":
            otype = OrderType.MARKET
        elif order_obj.orderType == "MIDPRICE":
            otype = OrderType.MIDPRICE
        else:
            otype = OrderType.LIMIT
        # ib_insync utilise UNSET_DOUBLE (= sys.float_info.max ~1.79e308) pour
        # "non defini" (cas des MARKET orders), pas 0.0. On nettoie les deux.
        price = getattr(order_obj, "lmtPrice", None)
        if price is None or price == 0.0 or price > 1e100 or (
            isinstance(price, float) and math.isnan(price)
        ):
            price = None
        avg = float(trade.orderStatus.avgFillPrice) if trade.orderStatus and trade.orderStatus.avgFillPrice else None

        ts = 0
        if trade.log:
            log_time = getattr(trade.log[0], "time", None)
            if log_time is not None:
                ts = int(log_time.timestamp() * 1000)

        oid = str(order_obj.orderId)
        self._orders[oid] = trade
        return Order(
            id=oid,
            client_order_id=None,
            symbol=self._symbol_of(trade.contract),
            side=side,
            type=otype,
            quantity=qty,
            filled_quantity=filled,
            price=price,
            avg_fill_price=avg,
            status=status,
            created_at=ts,
            raw={"orderRef": getattr(order_obj, "orderRef", None)},
        )

    # ---- Broker API ----------------------------------------------------

    def validate(self, symbol) -> None:
        ib = self._ensure_connected()
        contract = self._resolve_contract(symbol)
        try:
            qualified = ib.qualifyContracts(contract)
        except Exception as e:
            raise BrokerError(f"Erreur qualifyContracts : {e}") from e
        if not qualified:
            raise SymbolNotFound(f"Symbole IBKR non resolu : {symbol!r}")

    def place_order(
        self,
        symbol,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        outside_rth: bool = False,
    ) -> Order:
        from ib_insync import LimitOrder, MarketOrder
        from ib_insync import Order as IBOrder

        if order_type == OrderType.LIMIT and price is None:
            raise InvalidOrder("price est requis pour un ordre LIMIT")

        # IBKR refuse MARKET et MIDPRICE hors RTH (doc IBKR : "clients are
        # restricted to limit order types" en pre/after-market).
        if outside_rth and order_type == OrderType.MARKET:
            raise InvalidOrder(
                "MARKET refuse par IBKR hors RTH. Utiliser LIMIT @ ask + buffer "
                "(ou smart_buy avec outside_rth=True pour bascule auto)."
            )
        if outside_rth and order_type == OrderType.MIDPRICE:
            raise InvalidOrder(
                "MIDPRICE non supporte hors RTH par IBKR. "
                "Utiliser smart_buy avec outside_rth=True (climb LIMIT)."
            )

        ib = self._ensure_connected()
        contract = self._resolve_contract(symbol)
        ib.qualifyContracts(contract)

        action = "BUY" if side == OrderSide.BUY else "SELL"
        if order_type == OrderType.MARKET:
            order = MarketOrder(action, quantity)
        elif order_type == OrderType.LIMIT:
            order = LimitOrder(action, quantity, price, outsideRth=outside_rth)
        else:  # MIDPRICE : ordre IBKR natif, price utilise comme cap (optionnel)
            order = IBOrder()
            order.action = action
            order.totalQuantity = quantity
            order.orderType = "MIDPRICE"
            order.tif = "DAY"
            if price is not None:
                order.lmtPrice = price  # cap : ne paiera/recevra jamais pire que ce prix

        if client_order_id is not None:
            order.orderRef = client_order_id

        try:
            trade = ib.placeOrder(contract, order)
        except Exception as e:
            raise BrokerError(f"placeOrder IBKR : {e}") from e
        return self._to_order(trade)

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        ib = self._ensure_connected()
        trade = self._orders.get(order_id)
        if trade is None:
            # Tentative de retrouver l'ordre dans la session courante
            ib.reqAllOpenOrders()
            for t in ib.openTrades():
                if str(t.order.orderId) == order_id:
                    trade = t
                    self._orders[order_id] = t
                    break
        if trade is None:
            raise OrderNotFound(f"Ordre IBKR introuvable : {order_id}")
        try:
            ib.cancelOrder(trade.order)
        except Exception as e:
            raise BrokerError(f"cancelOrder IBKR : {e}") from e
        return self._to_order(trade)

    def get_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        ib = self._ensure_connected()
        trade = self._orders.get(order_id)
        if trade is None:
            try:
                ib.reqAllOpenOrders()
                for t in ib.openTrades():
                    if str(t.order.orderId) == order_id:
                        trade = t
                        self._orders[order_id] = t
                        break
            except Exception as e:
                raise BrokerError(f"reqAllOpenOrders IBKR : {e}") from e
        if trade is None:
            raise OrderNotFound(f"Ordre IBKR introuvable : {order_id}")
        # `Trade` est mis a jour par ib_insync au fil des events ; suffit de relire.
        return self._to_order(trade)

    def get_quote(self, symbol) -> Quote:
        ib = self._ensure_connected()
        contract = self._resolve_contract(symbol)
        ib.qualifyContracts(contract)
        try:
            tickers = ib.reqTickers(contract)
        except Exception as e:
            raise BrokerError(f"reqTickers IBKR : {e}") from e
        if not tickers:
            raise SymbolNotFound(f"IBKR : pas de ticker pour {symbol!r}")
        t = tickers[0]

        def _valid_px(v) -> bool:
            try:
                v = float(v)
            except (TypeError, ValueError):
                return False
            return v > 0 and not math.isnan(v)

        bid = float(t.bid) if _valid_px(t.bid) else 0.0
        ask = float(t.ask) if _valid_px(t.ask) else 0.0
        if bid == 0.0 or ask == 0.0:
            raise BrokerError(f"IBKR : bid/ask indisponible pour {symbol} (marche ferme ?)")
        ts = int(t.time.timestamp() * 1000) if t.time else 0
        return Quote(
            symbol=symbol if isinstance(symbol, str) else self._symbol_of(contract),
            bid=bid, ask=ask, timestamp=ts,
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        ib = self._ensure_connected()
        try:
            ib.reqAllOpenOrders()
            trades = ib.openTrades()
        except Exception as e:
            raise BrokerError(f"reqAllOpenOrders IBKR : {e}") from e
        out = [self._to_order(t) for t in trades]
        if symbol is not None:
            sym_str = symbol if isinstance(symbol, str) else getattr(symbol, "symbol", "")
            out = [o for o in out if o.symbol == sym_str]
        return out

    def get_position(self, symbol) -> Position:
        ib = self._ensure_connected()
        try:
            positions = ib.positions()
        except Exception as e:
            raise BrokerError(f"positions IBKR : {e}") from e
        sym_str = symbol if isinstance(symbol, str) else getattr(symbol, "symbol", "")
        for p in positions:
            if self._symbol_of(p.contract) == sym_str:
                return Position(
                    symbol=sym_str,
                    quantity=float(p.position),
                    avg_entry_price=float(p.avgCost) if p.avgCost else None,
                    unrealized_pnl=None,
                    raw={"account": p.account},
                )
        return Position(symbol=sym_str, quantity=0.0)

    def get_balance(self) -> List[Balance]:
        ib = self._ensure_connected()
        try:
            rows = ib.accountSummary()
        except Exception as e:
            raise BrokerError(f"accountSummary IBKR : {e}") from e
        # On agrege par devise : TotalCashValue (total) et AvailableFunds (free).
        by_cur: Dict[str, Dict[str, float]] = {}
        for r in rows:
            if r.tag == "TotalCashValue":
                by_cur.setdefault(r.currency, {})["total"] = float(r.value or 0.0)
            elif r.tag == "AvailableFunds":
                by_cur.setdefault(r.currency, {})["free"] = float(r.value or 0.0)
        out: List[Balance] = []
        for cur, d in by_cur.items():
            if cur == "BASE":
                continue
            total = d.get("total", 0.0)
            free = d.get("free", 0.0)
            out.append(Balance(currency=cur, free=free, used=max(0.0, total - free), total=total))
        return out

    # ---- Smart entry override : MIDPRICE natif IBKR en RTH, climb sinon -

    def smart_buy(
        self,
        symbol,
        quantity: float,
        max_wait_s: float = 10.0,
        steps: int = 3,  # ignore en mode MIDPRICE, utilise en mode climb
        allow_market_fallback: bool = True,
        poll_interval_s: float = 0.5,
        outside_rth: bool = False,
    ) -> Order:
        if outside_rth:
            # MIDPRICE et MARKET refuses hors RTH par IBKR.
            # Bascule sur le climb LIMIT generique du Broker base.
            return Broker._smart_entry_climb(
                self, symbol, OrderSide.BUY, quantity, max_wait_s, steps,
                allow_market_fallback, poll_interval_s, outside_rth=True,
            )
        return self._smart_midprice(symbol, OrderSide.BUY, quantity,
                                    max_wait_s, allow_market_fallback, poll_interval_s)

    def smart_sell(
        self,
        symbol,
        quantity: float,
        max_wait_s: float = 10.0,
        steps: int = 3,
        allow_market_fallback: bool = True,
        poll_interval_s: float = 0.5,
        outside_rth: bool = False,
    ) -> Order:
        if outside_rth:
            return Broker._smart_entry_climb(
                self, symbol, OrderSide.SELL, quantity, max_wait_s, steps,
                allow_market_fallback, poll_interval_s, outside_rth=True,
            )
        return self._smart_midprice(symbol, OrderSide.SELL, quantity,
                                    max_wait_s, allow_market_fallback, poll_interval_s)

    def _smart_midprice(
        self,
        symbol,
        side: OrderSide,
        quantity: float,
        max_wait_s: float,
        allow_market_fallback: bool,
        poll_interval_s: float,
    ) -> Order:
        if quantity <= 0:
            raise InvalidOrder("quantity doit etre > 0")
        order = self.place_order(symbol, side, quantity, OrderType.MIDPRICE)
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            time.sleep(poll_interval_s)
            current = self.get_order(order.id, symbol=symbol)
            if current.status == OrderStatus.FILLED:
                return current
        # timeout : cancel + fallback market sur la quantite restante
        try:
            canceled = self.cancel_order(order.id, symbol=symbol)
        except OrderNotFound:
            return self.get_order(order.id, symbol=symbol)
        remaining = quantity - canceled.filled_quantity
        if remaining > 0 and allow_market_fallback:
            return self.place_order(symbol, side, remaining, OrderType.MARKET)
        if remaining > 0:
            raise InvalidOrder(
                f"smart_{side.value} IBKR {symbol} : {remaining}/{quantity} non remplis "
                f"apres {max_wait_s}s (allow_market_fallback=False)"
            )
        return canceled
