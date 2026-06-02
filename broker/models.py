from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    MIDPRICE = "midprice"  # IBKR uniquement : execute au mid NBBO ou mieux


class OrderStatus(str, Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    quantity: float
    filled_quantity: float
    price: Optional[float]
    avg_fill_price: Optional[float]
    status: OrderStatus
    created_at: int  # ms epoch UTC
    client_order_id: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fee: float
    fee_currency: str
    timestamp: int  # ms epoch UTC


@dataclass
class Position:
    symbol: str
    quantity: float  # signed : negatif = short
    avg_entry_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    raw: dict = field(default_factory=dict)


@dataclass
class Balance:
    currency: str
    free: float
    used: float
    total: float


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    timestamp: int  # ms epoch UTC

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid
