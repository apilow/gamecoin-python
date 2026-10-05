"""Typed objects returned by the API (``docs/tech/API.md`` § Objects), field for field.

Every class is a frozen dataclass: fields are ``snake_case``, timestamps are timezone-aware ``datetime`` values in
UTC, collections are tuples. Fields the SDK does not know about are ignored, so a newer API never breaks an older
SDK. The ``from_api`` constructors take the decoded JSON of the API; they are what the SDK uses internally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Tuple


# --- String values ---------------------------------------------------------------------------------------------
# Plain constants: the fields below stay ``str``, so a value added by a newer API is delivered unchanged.


class PlayerKind:
    EXTERNAL = "external"
    HOSTED = "hosted"


class ItemType:
    CONSUMABLE = "consumable"
    DURABLE = "durable"


class LedgerEntryType:
    PURCHASE_CREDIT = "purchase_credit"
    GRANT = "grant"
    SPEND = "spend"
    REFUND_CLAWBACK = "refund_clawback"
    CHARGEBACK_CLAWBACK = "chargeback_clawback"
    ADJUSTMENT = "adjustment"


class OrderStatus:
    PENDING = "pending"
    PAID = "paid"
    FULFILLED = "fulfilled"
    REFUNDED = "refunded"
    CHARGEBACK = "chargeback"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"


# --- Decoding helpers ------------------------------------------------------------------------------------------

_ISO_8601 = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?(Z|z|[+-]\d{2}(?::?\d{2})?)?"
)


def parse_datetime(value: Any) -> datetime:
    """ISO 8601 string (``2026-10-05T12:00:00.000Z``) to an aware ``datetime``.

    Written by hand because ``datetime.fromisoformat`` only accepts ``Z`` and milliseconds from Python 3.11.
    A string without an offset is read as UTC. Raises ``ValueError`` on anything else.
    """
    match = _ISO_8601.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"not an ISO 8601 timestamp: {value!r}")
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    microsecond = int((fraction or "0")[:6].ljust(6, "0"))
    tz = timezone.utc
    if offset and offset not in ("Z", "z"):
        digits = offset[1:].replace(":", "")
        delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
        tz = timezone(-delta if offset[0] == "-" else delta)
    return datetime(
        int(year), int(month), int(day), int(hour), int(minute), int(second), microsecond, tzinfo=tz
    ).astimezone(timezone.utc)


def _optional_datetime(value: Any) -> Optional[datetime]:
    return None if value is None else parse_datetime(value)


def _string_map(value: Any) -> Dict[str, str]:
    return dict(value) if value else {}


# --- Players and wallet ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Player:
    id: str
    external_id: Optional[str]
    kind: str
    display_name: Optional[str]
    country: Optional[str]
    blocked: bool
    created_at: datetime

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Player":
        return cls(
            id=data["id"],
            external_id=data.get("externalId"),
            kind=data["kind"],
            display_name=data.get("displayName"),
            country=data.get("country"),
            blocked=bool(data["blocked"]),
            created_at=parse_datetime(data["createdAt"]),
        )


@dataclass(frozen=True)
class PlayerToken:
    """Short-lived token a game client uses with the browser SDK. ``token`` is a credential: kept out of ``repr``."""

    token: str = field(repr=False)
    expires_at: datetime

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "PlayerToken":
        return cls(token=data["token"], expires_at=parse_datetime(data["expiresAt"]))


@dataclass(frozen=True)
class Balance:
    """Balance of one currency: ``paid`` (bought with real money), ``bonus`` (granted or earned), and their sum."""

    currency: str
    paid: int
    bonus: int
    total: int

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Balance":
        return cls(currency=data["currency"], paid=data["paid"], bonus=data["bonus"], total=data["total"])


@dataclass(frozen=True)
class Wallet:
    """One balance per active currency of the game, zeros included."""

    player_id: str
    balances: Tuple[Balance, ...]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Wallet":
        return cls(player_id=data["playerId"], balances=tuple(Balance.from_api(b) for b in data["balances"]))

    def balance_of(self, currency: str) -> Optional[Balance]:
        """The balance of ``currency``, or ``None`` when the game has no such active currency."""
        return next((b for b in self.balances if b.currency == currency), None)


# --- Ledger ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BalanceAfter:
    paid: int
    bonus: int
    total: int

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "BalanceAfter":
        return cls(paid=data["paid"], bonus=data["bonus"], total=data["total"])


@dataclass(frozen=True)
class EntryRef:
    """What a ledger entry relates to: the order of a pack purchase or refund, the item of an item purchase."""

    order_id: Optional[str] = None
    item_sku: Optional[str] = None

    @classmethod
    def from_api(cls, data: Optional[Mapping[str, Any]]) -> "EntryRef":
        data = data or {}
        return cls(order_id=data.get("orderId"), item_sku=data.get("itemSku"))


@dataclass(frozen=True)
class LedgerEntry:
    id: str
    type: str
    currency: str
    paid_delta: int
    bonus_delta: int
    balance_after: BalanceAfter
    reason: Optional[str]
    metadata: Dict[str, str]
    ref: EntryRef
    created_at: datetime

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "LedgerEntry":
        return cls(
            id=data["id"],
            type=data["type"],
            currency=data["currency"],
            paid_delta=data["paidDelta"],
            bonus_delta=data["bonusDelta"],
            balance_after=BalanceAfter.from_api(data["balanceAfter"]),
            reason=data.get("reason"),
            metadata=_string_map(data.get("metadata")),
            ref=EntryRef.from_api(data.get("ref")),
            created_at=parse_datetime(data["createdAt"]),
        )


@dataclass(frozen=True)
class LedgerPage:
    """One page of ledger entries. ``next_cursor`` is ``None`` on the last page."""

    entries: Tuple[LedgerEntry, ...]
    next_cursor: Optional[str]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "LedgerPage":
        return cls(
            entries=tuple(LedgerEntry.from_api(e) for e in data["entries"]),
            next_cursor=data.get("nextCursor"),
        )


@dataclass(frozen=True)
class Movement:
    """Result of ``wallet.grant`` and ``wallet.spend``: the ledger entry and the new balance of the currency.

    ``replayed`` is true when the API recognised the idempotency key and returned the original result: nothing
    was credited or debited a second time.
    """

    entry: LedgerEntry
    balance: Balance
    replayed: bool = False

    @classmethod
    def from_api(cls, data: Mapping[str, Any], replayed: bool = False) -> "Movement":
        return cls(entry=LedgerEntry.from_api(data["entry"]), balance=Balance.from_api(data["balance"]), replayed=replayed)


# --- Inventory and purchases -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class InventoryItem:
    """An item of a player's inventory. ``replayed`` only means something on the result of a ``grant`` or ``consume``."""

    sku: str
    quantity: int
    updated_at: datetime
    replayed: bool = False

    @classmethod
    def from_api(cls, data: Mapping[str, Any], replayed: bool = False) -> "InventoryItem":
        return cls(sku=data["sku"], quantity=data["quantity"], updated_at=parse_datetime(data["updatedAt"]), replayed=replayed)


@dataclass(frozen=True)
class Purchase:
    """Result of ``purchases.create``: the debit, the new balance and the item as it now stands in the inventory."""

    entry: LedgerEntry
    balance: Balance
    item: InventoryItem
    replayed: bool = False

    @classmethod
    def from_api(cls, data: Mapping[str, Any], replayed: bool = False) -> "Purchase":
        return cls(
            entry=LedgerEntry.from_api(data["entry"]),
            balance=Balance.from_api(data["balance"]),
            item=InventoryItem.from_api(data["item"]),
            replayed=replayed,
        )


# --- Catalog ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Currency:
    code: str
    name: str
    purchasable: bool

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Currency":
        return cls(code=data["code"], name=data["name"], purchasable=bool(data["purchasable"]))


@dataclass(frozen=True)
class Price:
    currency: str
    amount: int

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Price":
        return cls(currency=data["currency"], amount=data["amount"])


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    description: Optional[str]
    type: str
    price: Optional[Price]
    max_owned: Optional[int]
    client_purchasable: bool
    metadata: Dict[str, str]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Item":
        price = data.get("price")
        return cls(
            sku=data["sku"],
            name=data["name"],
            description=data.get("description"),
            type=data["type"],
            price=None if price is None else Price.from_api(price),
            max_owned=data.get("maxOwned"),
            client_purchasable=bool(data["clientPurchasable"]),
            metadata=_string_map(data.get("metadata")),
        )


@dataclass(frozen=True)
class PackGrant:
    currency: str
    amount: int
    bonus: int

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "PackGrant":
        return cls(currency=data["currency"], amount=data["amount"], bonus=data["bonus"])


@dataclass(frozen=True)
class PackItem:
    sku: str
    quantity: int

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "PackItem":
        return cls(sku=data["sku"], quantity=data["quantity"])


@dataclass(frozen=True)
class Pack:
    """A pack sold for real money. ``price_cents`` is in ``currency`` (euros)."""

    sku: str
    name: str
    description: Optional[str]
    price_cents: int
    currency: str
    grants: Tuple[PackGrant, ...]
    items: Tuple[PackItem, ...]
    badge: Optional[str]
    max_per_player: Optional[int]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Pack":
        return cls(
            sku=data["sku"],
            name=data["name"],
            description=data.get("description"),
            price_cents=data["priceCents"],
            currency=data["currency"],
            grants=tuple(PackGrant.from_api(g) for g in data["grants"]),
            items=tuple(PackItem.from_api(i) for i in data["items"]),
            badge=data.get("badge"),
            max_per_player=data.get("maxPerPlayer"),
        )


@dataclass(frozen=True)
class Catalog:
    """What the game sells: active currencies, items and packs."""

    currencies: Tuple[Currency, ...]
    items: Tuple[Item, ...]
    packs: Tuple[Pack, ...]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Catalog":
        return cls(
            currencies=tuple(Currency.from_api(c) for c in data["currencies"]),
            items=tuple(Item.from_api(i) for i in data["items"]),
            packs=tuple(Pack.from_api(p) for p in data["packs"]),
        )


# --- Orders and checkouts --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderPack:
    sku: str
    name: str

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "OrderPack":
        return cls(sku=data["sku"], name=data["name"])


@dataclass(frozen=True)
class Order:
    id: str
    player_id: str
    status: str
    pack: OrderPack
    amount_cents: int
    currency: str
    country: str
    vat_rate_bp: int
    vat_cents: int
    net_cents: int
    created_at: datetime
    paid_at: Optional[datetime]
    fulfilled_at: Optional[datetime]
    refunded_at: Optional[datetime]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Order":
        return cls(
            id=data["id"],
            player_id=data["playerId"],
            status=data["status"],
            pack=OrderPack.from_api(data["pack"]),
            amount_cents=data["amountCents"],
            currency=data["currency"],
            country=data["country"],
            vat_rate_bp=data["vatRateBp"],
            vat_cents=data["vatCents"],
            net_cents=data["netCents"],
            created_at=parse_datetime(data["createdAt"]),
            paid_at=_optional_datetime(data.get("paidAt")),
            fulfilled_at=_optional_datetime(data.get("fulfilledAt")),
            refunded_at=_optional_datetime(data.get("refundedAt")),
        )


@dataclass(frozen=True)
class Checkout:
    """Result of ``checkouts.create``: the pending order and the hosted payment page to send the player to.

    A replayed checkout (same idempotency key) carries the order in its **current** state, not the original one.
    """

    order: Order
    checkout_url: str
    replayed: bool = False

    @classmethod
    def from_api(cls, data: Mapping[str, Any], replayed: bool = False) -> "Checkout":
        return cls(order=Order.from_api(data["order"]), checkout_url=data["checkoutUrl"], replayed=replayed)
