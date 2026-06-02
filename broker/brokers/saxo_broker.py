from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


_SAXO_STATUS_MAP = {
    "Working": OrderStatus.NEW,
    "Pending": OrderStatus.NEW,
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELED,
    "Rejected": OrderStatus.REJECTED,
    "Expired": OrderStatus.EXPIRED,
}


def _parse_iso(ts: Optional[str]) -> int:
    if not ts:
        return 0
    # Saxo renvoie du ISO8601 UTC ("2024-01-01T12:00:00.000000Z")
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


class SaxoBroker(Broker):
    """
    Broker Saxo Banque via OpenAPI REST.

    Endpoints :
      - paper=True  -> https://gateway.saxobank.com/sim/openapi
      - paper=False -> https://gateway.saxobank.com/openapi

    Credentials (env vars si non passes) :
      SAXO_ACCESS_TOKEN  (token Bearer, 24h en demo)
      SAXO_ACCOUNT_KEY   (optionnel, auto-resolu via /port/v1/accounts/me)

    Notes :
      - Saxo identifie les instruments par Uic (entier interne), pas par ticker.
        SaxoBroker maintient un cache symbol -> Uic resolu via /ref/v1/instruments.
      - AssetType par defaut : Stock. Pour FxSpot, surcharger via place_order
        avec un symbole resolu manuellement (V2 : asset_type explicite).
      - V1 : token statique. L'OAuth refresh flow est hors-scope.
    """

    name = "saxo"

    _SIM_URL = "https://gateway.saxobank.com/sim/openapi"
    _LIVE_URL = "https://gateway.saxobank.com/openapi"

    def __init__(
        self,
        access_token: Optional[str] = None,
        account_key: Optional[str] = None,
        paper: bool = True,
    ) -> None:
        try:
            import requests  # noqa: F401
        except ImportError as e:
            raise ImportError("requests requis : pip install requests") from e

        self.paper = paper
        self._token = access_token or getenv("SAXO_ACCESS_TOKEN", required=True)
        self._account_key = account_key or getenv("SAXO_ACCOUNT_KEY")
        self._base_url = self._SIM_URL if paper else self._LIVE_URL

        import requests as _req
        self._session = _req.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._uic_cache: Dict[str, int] = {}
        self._default_asset_type = "Stock"

    # ---- HTTP ----------------------------------------------------------

    def _request(self, method: str, path: str, **kw) -> Any:
        import requests as _req
        url = f"{self._base_url}{path}"
        try:
            resp = self._session.request(method, url, timeout=20, **kw)
        except _req.RequestException as e:
            raise BrokerConnectionError(f"Saxo {method} {path} : {e}") from e

        if resp.status_code == 401:
            raise AuthError(
                "Saxo 401 : token invalide ou expire (24h en demo). "
                "Regenerer sur https://developer.saxo et mettre a jour SAXO_ACCESS_TOKEN."
            )
        if resp.status_code == 429:
            raise RateLimited("Saxo 429 : rate limit depasse")
        if resp.status_code == 404:
            raise OrderNotFound(f"Saxo 404 : {path}")
        if not resp.ok:
            try:
                err = resp.json()
            except ValueError:
                err = {"raw": resp.text}
            msg = err.get("Message") or err.get("ErrorInfo", {}).get("Message") or str(err)
            code = err.get("ErrorCode") or err.get("ErrorInfo", {}).get("ErrorCode") or ""
            full = f"Saxo {resp.status_code} {code} : {msg}"
            if "InsufficientCash" in str(code) or "insufficient" in msg.lower():
                raise InsufficientFunds(full)
            raise InvalidOrder(full)

        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as e:
            raise BrokerError(f"Saxo : reponse non-JSON ({path}) : {e}") from e

    # ---- Resolution AccountKey / Uic -----------------------------------

    def _ensure_account_key(self) -> str:
        if self._account_key:
            return self._account_key
        data = self._request("GET", "/port/v1/accounts/me")
        accounts = data.get("Data") or []
        if not accounts:
            raise AuthError("Saxo : aucun compte trouve sur /port/v1/accounts/me")
        self._account_key = accounts[0]["AccountKey"]
        return self._account_key

    def _resolve_uic(self, symbol: str, asset_type: Optional[str] = None) -> int:
        cache_key = f"{symbol}|{asset_type or self._default_asset_type}"
        if cache_key in self._uic_cache:
            return self._uic_cache[cache_key]
        params = {
            "Keywords": symbol,
            "AssetTypes": asset_type or self._default_asset_type,
        }
        data = self._request("GET", "/ref/v1/instruments", params=params)
        rows = data.get("Data") or []
        if not rows:
            raise SymbolNotFound(f"Saxo : aucun instrument pour {symbol!r}")
        uic = int(rows[0]["Identifier"])
        self._uic_cache[cache_key] = uic
        return uic

    # ---- Conversions ---------------------------------------------------

    def _to_order(self, raw: dict) -> Order:
        status_raw = raw.get("Status") or raw.get("OpenOrderStatus") or "Working"
        status = _SAXO_STATUS_MAP.get(status_raw, OrderStatus.NEW)
        qty = float(raw.get("Amount") or 0.0)
        filled = float(raw.get("FilledAmount") or 0.0)
        if status == OrderStatus.NEW and 0 < filled < qty:
            status = OrderStatus.PARTIALLY_FILLED

        side_raw = raw.get("BuySell") or raw.get("Side") or "Buy"
        side = OrderSide.BUY if side_raw.lower().startswith("b") else OrderSide.SELL
        otype_raw = raw.get("OrderType") or "Market"
        otype = OrderType.MARKET if otype_raw.lower() == "market" else OrderType.LIMIT

        price = raw.get("Price") or raw.get("OrderPrice")
        avg = raw.get("FilledPrice") or raw.get("AveragePrice")

        return Order(
            id=str(raw.get("OrderId") or raw.get("OrderID") or ""),
            client_order_id=raw.get("ExternalReference"),
            symbol=str(raw.get("Symbol") or raw.get("Uic") or ""),
            side=side,
            type=otype,
            quantity=qty,
            filled_quantity=filled,
            price=float(price) if price is not None else None,
            avg_fill_price=float(avg) if avg is not None else None,
            status=status,
            created_at=_parse_iso(raw.get("OrderTime") or raw.get("CreatedDate")),
            raw=raw,
        )

    # ---- Broker API ----------------------------------------------------

    def validate(self, symbol: str) -> None:
        # Force la resolution Uic (leve SymbolNotFound si absent).
        self._resolve_uic(symbol)

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
            raise InvalidOrder("OrderType.MIDPRICE n'est pas supporte par Saxo (IBKR uniquement)")
        if order_type == OrderType.LIMIT and price is None:
            raise InvalidOrder("price est requis pour un ordre LIMIT")
        uic = self._resolve_uic(symbol)
        account_key = self._ensure_account_key()

        body: Dict[str, Any] = {
            "Uic": uic,
            "AssetType": self._default_asset_type,
            "BuySell": "Buy" if side == OrderSide.BUY else "Sell",
            "OrderType": "Market" if order_type == OrderType.MARKET else "Limit",
            "Amount": quantity,
            "AccountKey": account_key,
            "ManualOrder": True,
            "OrderDuration": {"DurationType": "DayOrder"},
        }
        if order_type == OrderType.LIMIT:
            body["OrderPrice"] = price
        if client_order_id is not None:
            body["ExternalReference"] = client_order_id

        data = self._request("POST", "/trade/v2/orders", json=body)
        order_id = str(data.get("OrderId") or data.get("OrderID") or "")
        # Reponse Saxo : on enrichit avec les inputs (le POST ne renvoie pas le full order).
        merged = dict(data)
        merged.setdefault("Symbol", symbol)
        merged.setdefault("BuySell", body["BuySell"])
        merged.setdefault("OrderType", body["OrderType"])
        merged.setdefault("Amount", quantity)
        merged.setdefault("OrderPrice", price)
        merged["OrderId"] = order_id
        return self._to_order(merged)

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        account_key = self._ensure_account_key()
        self._request(
            "DELETE",
            f"/trade/v2/orders/{order_id}",
            params={"AccountKey": account_key},
        )
        # Saxo DELETE renvoie un body vide. Pour recuperer le filled effectif
        # (cas d'un cancel sur un ordre partiellement rempli), on relit l'historique.
        try:
            final = self.get_order(order_id, symbol=symbol)
            # Force le statut a CANCELED meme si l'API renvoie encore "Working".
            return Order(
                id=final.id,
                client_order_id=final.client_order_id,
                symbol=final.symbol or symbol or "",
                side=final.side,
                type=final.type,
                quantity=final.quantity,
                filled_quantity=final.filled_quantity,
                price=final.price,
                avg_fill_price=final.avg_fill_price,
                status=OrderStatus.CANCELED,
                created_at=final.created_at,
                raw=final.raw,
            )
        except OrderNotFound:
            return Order(
                id=order_id,
                client_order_id=None,
                symbol=symbol or "",
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                quantity=0.0,
                filled_quantity=0.0,
                price=None,
                avg_fill_price=None,
                status=OrderStatus.CANCELED,
                created_at=0,
                raw={"canceled": True, "note": "etat final non recuperable"},
            )

    def get_order(self, order_id: str, symbol: Optional[str] = None) -> Order:
        # Saxo n'a pas d'endpoint "single order par id" fiable : on filtre /orders/me/
        # puis on tombe sur /historicalorders si absent (assume filled si encore absent).
        data = self._request("GET", "/port/v1/orders/me/")
        for r in data.get("Data") or []:
            if str(r.get("OrderId") or r.get("OrderID") or "") == order_id:
                return self._to_order(r)
        # Pas dans les ouverts -> regarde l'historique recent.
        try:
            hist = self._request(
                "GET", "/hist/v3/orders/me/",
                params={"$top": 50},
            )
            for r in hist.get("Data") or []:
                if str(r.get("OrderId") or r.get("OrderID") or "") == order_id:
                    return self._to_order(r)
        except BrokerError:
            pass
        raise OrderNotFound(f"Saxo : ordre introuvable {order_id}")

    def get_quote(self, symbol: str) -> Quote:
        uic = self._resolve_uic(symbol)
        data = self._request(
            "GET", "/trade/v1/infoprices/",
            params={
                "Uic": uic,
                "AssetType": self._default_asset_type,
                "FieldGroups": "Quote",
            },
        )
        q = data.get("Quote") or {}
        bid = q.get("Bid")
        ask = q.get("Ask")
        if bid is None or ask is None:
            raise BrokerError(f"Saxo : bid/ask indisponible pour {symbol} (marche ferme ?)")
        return Quote(
            symbol=symbol,
            bid=float(bid),
            ask=float(ask),
            timestamp=_parse_iso(data.get("LastUpdated")),
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        data = self._request("GET", "/port/v1/orders/me/")
        rows = data.get("Data") or []
        orders = [self._to_order(r) for r in rows]
        if symbol is not None:
            uic = self._resolve_uic(symbol)
            orders = [o for o in orders if str(o.raw.get("Uic")) == str(uic)]
        return orders

    def get_position(self, symbol: str) -> Position:
        uic = self._resolve_uic(symbol)
        data = self._request("GET", "/port/v1/netpositions/me/", params={"$top": 500})
        rows = data.get("Data") or []
        for r in rows:
            base = r.get("NetPositionBase") or {}
            if int(base.get("Uic", -1)) == uic:
                view = r.get("NetPositionView") or {}
                qty = float(base.get("Amount") or 0.0)
                avg = base.get("AverageOpenPrice")
                pnl = view.get("ProfitLossOnTrade")
                return Position(
                    symbol=symbol,
                    quantity=qty,
                    avg_entry_price=float(avg) if avg is not None else None,
                    unrealized_pnl=float(pnl) if pnl is not None else None,
                    raw=r,
                )
        return Position(symbol=symbol, quantity=0.0)

    def get_balance(self) -> List[Balance]:
        account_key = self._ensure_account_key()
        data = self._request(
            "GET", "/port/v1/balances/", params={"AccountKey": account_key},
        )
        # /port/v1/balances/ renvoie un objet unique (pas une liste).
        cur = data.get("Currency")
        if cur is None:
            return []
        total = float(data.get("TotalValue") or 0.0)
        cash = float(data.get("CashBalance") or 0.0)
        margin_used = float(data.get("MarginUsedByCurrentPositions") or 0.0)
        free = cash - margin_used
        return [Balance(currency=cur, free=free, used=margin_used, total=total)]
