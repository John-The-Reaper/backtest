from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import List, Optional

from .exceptions import BrokerError, InvalidOrder, OrderNotFound
from .models import Balance, Order, OrderSide, OrderStatus, OrderType, Position, Quote


class Broker(ABC):
    """
    Interface minimale pour un broker d'execution.

    Toute nouvelle integration (ccxt, ib_insync, REST custom, ...) implemente
    cette interface. Les ordres sont normalises dans `Order`, les positions
    dans `Position`, les soldes dans `Balance`. Les exceptions natives doivent
    etre wrappees dans `broker.exceptions.*`.
    """

    name: str  # "binance" | "ibkr" | "saxo"
    paper: bool  # True = testnet/sim

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Order:
        """Place un ordre. Pour LIMIT, `price` est obligatoire."""
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        """Annule un ordre par id. `symbol` peut etre requis selon le broker."""
        raise NotImplementedError

    @abstractmethod
    def get_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        """Recupere l'etat courant d'un ordre par id."""
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Liste les ordres ouverts (eventuellement filtres par symbole)."""
        raise NotImplementedError

    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        """Position courante sur `symbol`. Quantite = 0 si absente."""
        raise NotImplementedError

    @abstractmethod
    def get_balance(self) -> List[Balance]:
        """Liste des soldes par devise."""
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Snapshot bid/ask. Necessaire pour smart_buy / smart_sell."""
        raise NotImplementedError

    def validate(self, symbol: str) -> None:
        """Verifie que le symbole est tradable. No-op par defaut."""
        return None

    def disconnect(self) -> None:
        """Libere les ressources (socket, session). No-op par defaut."""
        return None

    # ---- Smart entry (climbing limit + market fallback) ----------------

    def smart_buy(
        self,
        symbol: str,
        quantity: float,
        max_wait_s: float = 10.0,
        steps: int = 3,
        allow_market_fallback: bool = True,
        poll_interval_s: float = 0.5,
    ) -> Order:
        """
        Achat au meilleur prix possible en au plus `max_wait_s` secondes.

        Strategie generique (LIMIT grimpant) :
          1) Lit le quote bid/ask, calcule le mid
          2) Poste un LIMIT au mid, puis grimpe par paliers vers ask sur `steps` etapes
          3) A chaque palier, attend `max_wait_s / steps` secondes en pollant
          4) Cancel si pas rempli, repart sur la quantite restante au palier suivant
          5) Au dernier palier, optionnellement bascule en MARKET pour garantir le fill

        Les brokers peuvent override (ex: IBKR utilise MIDPRICE natif).
        """
        return self._smart_entry_climb(
            symbol, OrderSide.BUY, quantity, max_wait_s, steps,
            allow_market_fallback, poll_interval_s,
        )

    def smart_sell(
        self,
        symbol: str,
        quantity: float,
        max_wait_s: float = 10.0,
        steps: int = 3,
        allow_market_fallback: bool = True,
        poll_interval_s: float = 0.5,
    ) -> Order:
        """Vente au meilleur prix possible. Symetrique de `smart_buy`."""
        return self._smart_entry_climb(
            symbol, OrderSide.SELL, quantity, max_wait_s, steps,
            allow_market_fallback, poll_interval_s,
        )

    def _smart_entry_climb(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        max_wait_s: float,
        steps: int,
        allow_market_fallback: bool,
        poll_interval_s: float,
    ) -> Order:
        if steps < 1:
            raise InvalidOrder("steps doit etre >= 1")
        if quantity <= 0:
            raise InvalidOrder("quantity doit etre > 0")

        quote = self.get_quote(symbol)
        bid, ask, mid = quote.bid, quote.ask, quote.mid
        if bid <= 0 or ask <= 0 or ask < bid:
            raise InvalidOrder(f"quote invalide pour {symbol} : bid={bid} ask={ask}")

        # Prix par palier : mid -> ask (BUY) ou mid -> bid (SELL).
        # steps=1 -> [mid], steps=3 -> [mid, mid+1/3*delta, mid+2/3*delta].
        if side == OrderSide.BUY:
            prices = [mid + i * (ask - mid) / steps for i in range(steps)]
        else:
            prices = [mid - i * (mid - bid) / steps for i in range(steps)]

        step_time = max_wait_s / steps
        remaining = float(quantity)
        last_order: Optional[Order] = None

        for price in prices:
            if remaining <= 0:
                break
            order = self.place_order(symbol, side, remaining, OrderType.LIMIT, price=price)
            last_order = order
            deadline = time.monotonic() + step_time
            while time.monotonic() < deadline:
                time.sleep(poll_interval_s)
                current = self._safe_get_order(order.id, symbol)
                if current is not None and current.status == OrderStatus.FILLED:
                    return current
            # timeout : cancel et recupere le filled effectif
            try:
                canceled = self.cancel_order(order.id, symbol=symbol)
                filled_round = canceled.filled_quantity
                last_order = canceled
            except OrderNotFound:
                refreshed = self._safe_get_order(order.id, symbol)
                if refreshed is not None and refreshed.status == OrderStatus.FILLED:
                    return refreshed
                filled_round = refreshed.filled_quantity if refreshed else 0.0
            except BrokerError:
                refreshed = self._safe_get_order(order.id, symbol)
                filled_round = refreshed.filled_quantity if refreshed else 0.0
            remaining -= filled_round

        if remaining > 0 and allow_market_fallback:
            return self.place_order(symbol, side, remaining, OrderType.MARKET)
        if remaining > 0:
            raise InvalidOrder(
                f"smart_{side.value} {symbol} : {remaining}/{quantity} non remplis apres {max_wait_s}s "
                f"(allow_market_fallback=False)"
            )
        return last_order  # type: ignore[return-value]

    def _safe_get_order(self, order_id: str, symbol: Optional[str]) -> Optional[Order]:
        try:
            return self.get_order(order_id, symbol=symbol)
        except (OrderNotFound, BrokerError):
            return None
