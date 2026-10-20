"""The API resources reached through a client: ``gamecoin.wallet``, ``gamecoin.ledger``, ``gamecoin.orders``..."""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union

from . import _catalog_body as b
from . import _validate as v
from ._core import RETRY_RATE_LIMIT_ONLY, Core
from .models import (
    Catalog,
    Checkout,
    InventoryItem,
    LedgerEntry,
    LedgerPage,
    ManagedCurrency,
    ManagedItem,
    ManagedPack,
    ManagedPackAvailability,
    Movement,
    Order,
    PackGrant,
    PackItem,
    Player,
    PlayerToken,
    Price,
    Purchase,
    Redemption,
    Wallet,
)
from ._validate import PlayerLike

EXTERNAL_PREFIX = "ext:"


class _Unset:
    """Type of :data:`UNSET`."""

    _instance: Optional["_Unset"] = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()
"""Default of the optional profile fields of :meth:`PlayersResource.upsert`: "leave this field as it is".
Pass ``None`` instead to erase a field."""


def ext(external_id: str) -> str:
    """Designates a player by the id your own game uses for them: ``ext("user-42")`` is ``"ext:user-42"``.

    The id is opaque to GameCoin (1 to 128 characters, no leading or trailing space, case sensitive). With a secret
    key, the first write for an unknown ``ext`` player creates it.
    """
    return EXTERNAL_PREFIX + v.require_str(external_id, "external_id")


class _Resource:
    def __init__(self, core: Core) -> None:
        self._core = core

    @staticmethod
    def _player_path(player: PlayerLike, suffix: str = "") -> str:
        return f"/players/{v.segment(v.player_ref(player))}{suffix}"


class CatalogResource(_Resource):
    def get(self) -> Catalog:
        """Active currencies, items and packs of the game."""
        return self._core.request("GET", "/catalog", lambda d, _: Catalog.from_api(d))


class PlayersResource(_Resource):
    def upsert(
        self,
        player: PlayerLike,
        *,
        display_name: Union[str, None, _Unset] = UNSET,
        email: Union[str, None, _Unset] = UNSET,
        country: Union[str, None, _Unset] = UNSET,
        birth_year: Union[int, None, _Unset] = UNSET,
    ) -> Player:
        """Creates the player if it does not exist (``ext`` references only), then updates the given profile fields.

        A field left out is not touched; ``None`` erases it. Calling it with no profile field is a plain
        "create if missing".
        """
        body: Dict[str, Any] = {}
        for key, value in (("displayName", display_name), ("email", email), ("country", country)):
            if value is not UNSET:
                body[key] = v.nullable_str(value, key)
        if birth_year is not UNSET:
            if birth_year is not None:
                v.positive_int(birth_year, "birth_year")
            body["birthYear"] = birth_year
        return self._core.request("PUT", self._player_path(player), lambda d, _: Player.from_api(d), body=body)

    def get(self, player: PlayerLike) -> Player:
        return self._core.request("GET", self._player_path(player), lambda d, _: Player.from_api(d))

    def create_token(self, player: PlayerLike) -> PlayerToken:
        """A short-lived token for the player's browser or app, to use with the browser SDK. Never store it."""
        return self._core.request("POST", self._player_path(player, "/tokens"), lambda d, _: PlayerToken.from_api(d))


class WalletResource(_Resource):
    def get(self, player: PlayerLike) -> Wallet:
        return self._core.request("GET", self._player_path(player, "/wallet"), lambda d, _: Wallet.from_api(d))

    def grant(
        self,
        player: PlayerLike,
        *,
        currency: str,
        amount: int,
        reason: Optional[str] = None,
        metadata: Optional[Mapping[str, str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Movement:
        """Credits free (``bonus``) coins. Safe to retry: the same ``idempotency_key`` never credits twice."""
        return self._move("grant", player, currency, amount, reason, metadata, idempotency_key)

    def spend(
        self,
        player: PlayerLike,
        *,
        currency: str,
        amount: int,
        reason: Optional[str] = None,
        metadata: Optional[Mapping[str, str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Movement:
        """Debits coins; the game's spend order decides how ``paid`` and ``bonus`` are used up.

        Raises ``INSUFFICIENT_FUNDS`` (with ``required`` and ``available`` in ``details``) when the balance is too low.
        """
        return self._move("spend", player, currency, amount, reason, metadata, idempotency_key)

    def _move(
        self,
        action: str,
        player: PlayerLike,
        currency: str,
        amount: int,
        reason: Optional[str],
        metadata: Optional[Mapping[str, str]],
        idempotency_key: Optional[str],
    ) -> Movement:
        path = self._player_path(player, f"/wallet/{action}")
        body: Dict[str, Any] = {
            "currency": v.require_str(currency, "currency"),
            "amount": v.positive_int(amount, "amount"),
        }
        if reason is not None:
            body["reason"] = v.optional_str(reason, "reason")
        meta = v.metadata(metadata)
        if meta is not None:
            body["metadata"] = meta
        return self._core.request(
            "POST", path, Movement.from_api, body=body, idempotent=True, idempotency_key=v.idempotency_key(idempotency_key)
        )


class LedgerResource(_Resource):
    def list(
        self,
        player: PlayerLike,
        *,
        currency: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> LedgerPage:
        """One page of entries, newest first. Pass ``next_cursor`` back as ``cursor`` to get the next page."""
        path = self._player_path(player, "/ledger")
        query = {
            "currency": None if currency is None else v.require_str(currency, "currency"),
            "limit": v.page_limit(limit, "limit"),
            "cursor": None if cursor is None else v.require_str(cursor, "cursor"),
        }
        return self._core.request("GET", path, lambda d, _: LedgerPage.from_api(d), query=query)

    def iterate(
        self,
        player: PlayerLike,
        *,
        currency: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Iterator[LedgerEntry]:
        """Every entry, page after page, fetching the next page only when the loop reaches it.

        ``limit`` is the page size (the API default when omitted), not a cap on the number of entries.
        """
        # Arguments are checked now, not at the first ``next()``: a generator body only runs lazily.
        self._player_path(player)
        v.page_limit(limit, "limit")
        if currency is not None:
            v.require_str(currency, "currency")
        return self._entries(player, currency, limit)

    def _entries(self, player: PlayerLike, currency: Optional[str], limit: Optional[int]) -> Iterator[LedgerEntry]:
        cursor: Optional[str] = None
        while True:
            page = self.list(player, currency=currency, limit=limit, cursor=cursor)
            yield from page.entries
            if page.next_cursor is None:
                return
            cursor = page.next_cursor


class InventoryResource(_Resource):
    def list(self, player: PlayerLike) -> List[InventoryItem]:
        """Items the player owns (quantity above zero)."""
        return self._core.request(
            "GET",
            self._player_path(player, "/inventory"),
            lambda d, _: [InventoryItem.from_api(i) for i in d["items"]],
        )

    def grant(
        self,
        player: PlayerLike,
        *,
        sku: str,
        quantity: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> InventoryItem:
        """Gives items for free. ``quantity`` is 1 when omitted."""
        return self._change("grant", player, sku, quantity, idempotency_key)

    def consume(
        self,
        player: PlayerLike,
        *,
        sku: str,
        quantity: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> InventoryItem:
        """Uses up items. The returned item may have a quantity of 0."""
        return self._change("consume", player, sku, quantity, idempotency_key)

    def _change(
        self, action: str, player: PlayerLike, sku: str, quantity: Optional[int], idempotency_key: Optional[str]
    ) -> InventoryItem:
        return self._core.request(
            "POST",
            self._player_path(player, f"/inventory/{action}"),
            lambda d, replayed: InventoryItem.from_api(d["item"], replayed),
            body=_item_body(sku, quantity),
            idempotent=True,
            idempotency_key=v.idempotency_key(idempotency_key),
        )


class PurchasesResource(_Resource):
    def create(
        self,
        player: PlayerLike,
        *,
        sku: str,
        quantity: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> Purchase:
        """Buys an item with its price in virtual currency. ``quantity`` is 1 when omitted."""
        return self._core.request(
            "POST",
            self._player_path(player, "/purchases"),
            Purchase.from_api,
            body=_item_body(sku, quantity),
            idempotent=True,
            idempotency_key=v.idempotency_key(idempotency_key),
        )


class CheckoutsResource(_Resource):
    def create(
        self,
        player: PlayerLike,
        *,
        pack_sku: str,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        country: Optional[str] = None,
        locale: Optional[str] = None,
        creator_code: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Checkout:
        """Starts the purchase of a pack with real money: send the player to ``checkout.checkout_url``.

        ``country`` (ISO 3166-1 alpha-2) sets the tax country of the order. ``locale`` (``"fr"``, ``"en"`` or
        ``"nl"``) sets the language of the hosted payment page, the receipt and the e-mails of the order; the API
        ignores any other value, and a pending order that is reused takes the language of the latest request.
        ``creator_code`` is a creator's code typed by the buyer: the order keeps it, the buyer gets a bonus of free coins on
        delivery and the creator a commission. A refused code is a ``VALIDATION_FAILED`` error with
        ``details["fieldErrors"]["creatorCode"] == ["CODE_INVALID"]``. A pack outside its sale window or out of stock is
        refused with ``PACK_NOT_AVAILABLE`` (``details`` ``startsAt`` and ``endsAt``) or ``PACK_SOLD_OUT``.
        Coins and items are credited when the order is paid; follow the order with :meth:`OrdersResource.get`.
        """
        path = self._player_path(player, "/checkouts")
        body: Dict[str, Any] = {"packSku": v.require_str(pack_sku, "pack_sku")}
        for key, value in (
            ("successUrl", success_url),
            ("cancelUrl", cancel_url),
            ("country", country),
            ("locale", locale),
            ("creatorCode", creator_code),
        ):
            if value is not None:
                body[key] = v.require_str(value, key)
        return self._core.request(
            "POST", path, Checkout.from_api, body=body, idempotent=True, idempotency_key=v.idempotency_key(idempotency_key)
        )


class CodesResource(_Resource):
    def redeem(
        self,
        player: PlayerLike,
        code: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Redemption:
        """Redeems a gift code for the player: free (``bonus``) coins and items the studio chose.

        Pass the code as the player typed it: the API ignores case, spaces and dashes, so the SDK does not normalise it.
        An unknown, expired, used up, disabled or already used code is always the same ``VALIDATION_FAILED`` error with
        ``details["fieldErrors"]["code"] == ["CODE_INVALID"]``, never retried. ``PLAYER_BLOCKED``, ``LIMIT_REACHED``,
        ``CONFLICT`` and ``RATE_LIMITED`` come through the usual error mapping. Safe to retry: the same
        ``idempotency_key`` never credits twice (``replayed`` tells).
        """
        return self._core.request(
            "POST",
            self._player_path(player, "/codes/redeem"),
            Redemption.from_api,
            body={"code": v.require_str(code, "code")},
            idempotent=True,
            idempotency_key=v.idempotency_key(idempotency_key),
        )


class OrdersResource(_Resource):
    def get(self, order_id: str) -> Order:
        return self._core.request(
            "GET", f"/orders/{v.segment(v.require_str(order_id, 'order_id'))}", lambda d, _: Order.from_api(d)
        )

    def refund(self, order_id: str, *, reason: Optional[str] = None) -> Order:
        """Refunds a paid order: the coins and items it granted are taken back.

        A refund has no idempotency key, so it is retried on ``429`` only. After a network error or a ``5xx`` the
        outcome is unknown: the error is raised and you check the order with :meth:`get`.
        """
        body: Dict[str, Any] = {}
        if reason is not None:
            body["reason"] = v.optional_str(reason, "reason")
        return self._core.request(
            "POST",
            f"/orders/{v.segment(v.require_str(order_id, 'order_id'))}/refund",
            lambda d, _: Order.from_api(d),
            body=body,
            retry=RETRY_RATE_LIMIT_ONLY,
        )


def _item_body(sku: str, quantity: Optional[int]) -> Dict[str, Any]:
    body: Dict[str, Any] = {"sku": v.require_str(sku, "sku")}
    if quantity is not None:
        body["quantity"] = v.positive_int(quantity, "quantity")
    return body


# --- Catalog management ----------------------------------------------------------------------------------------
# The catalog is shared by the ``test`` and ``live`` environments of the game: a resource created with a test key exists
# for live keys too. The code or the sku is the natural key of a create (a taken one is ``CONFLICT``), so a create has no
# idempotency key and is retried on ``429`` only, like a refund; changes, archives and reads are retried as usual.


def _managed_body(fields: Dict[str, Any]) -> Dict[str, Any]:
    """The given fields of an update, minus those left as :data:`UNSET`."""
    return {key: value for key, value in fields.items() if value is not UNSET}


class CurrenciesResource(_Resource):
    """The currencies of the game: ``create``, ``get``, ``update``, ``archive``, ``list``."""

    @staticmethod
    def _path(code: str) -> str:
        return f"/currencies/{v.segment(v.require_str(code, 'code'))}"

    def create(self, *, code: str, name: str, purchasable: Optional[bool] = None) -> ManagedCurrency:
        """Creates a currency. Its ``code`` is final. A taken code (archived currencies keep theirs) is ``CONFLICT`` with
        ``details["fieldErrors"]["code"] == ["CODE_TAKEN"]``; an invalid one is ``VALIDATION_FAILED``.

        ``purchasable`` (default true): packs can sell the currency for real money; false for a currency that is only given.
        After a network error, a timeout or a ``5xx`` the outcome is unknown: read the currency back with :meth:`get`.
        """
        body: Dict[str, Any] = {"code": b.text(code, "code"), "name": b.text(name, "name")}
        if purchasable is not None:
            body["purchasable"] = b.boolean(purchasable, "purchasable")
        return self._core.request(
            "POST", "/currencies", lambda d, _: ManagedCurrency.from_api(d), body=body, retry=RETRY_RATE_LIMIT_ONLY
        )

    def get(self, code: str) -> ManagedCurrency:
        """A currency, archived ones included; ``NOT_FOUND`` for an unknown code."""
        return self._core.request("GET", self._path(code), lambda d, _: ManagedCurrency.from_api(d))

    def update(
        self,
        code: str,
        *,
        name: Union[str, _Unset] = UNSET,
        purchasable: Union[bool, _Unset] = UNSET,
    ) -> ManagedCurrency:
        """Changes a currency: an argument left out is unchanged. ``purchasable`` cannot become false while a pack that
        is not archived sells the currency for money (``CONFLICT``, ``details["blockedBy"]["packs"]``). An archived
        currency cannot be changed (``CONFLICT``)."""
        path = self._path(code)
        body = _managed_body(
            {
                "name": UNSET if name is UNSET else b.text(name, "name"),
                "purchasable": UNSET if purchasable is UNSET else b.boolean(purchasable, "purchasable"),
            }
        )
        return self._core.request("PATCH", path, lambda d, _: ManagedCurrency.from_api(d), body=body)

    def archive(self, code: str) -> ManagedCurrency:
        """Archives a currency (nothing is deleted; balances stay) and returns it. Refused with ``CONFLICT``
        (``details["blockedBy"]``) while an active pack credits it or an active item is priced in it. Archiving an
        archived currency has no effect."""
        return self._core.request("DELETE", self._path(code), lambda d, _: ManagedCurrency.from_api(d))

    def list(self, *, include_archived: bool = False) -> List[ManagedCurrency]:
        """The currencies of the game, oldest first; the archived ones only with ``include_archived=True``."""
        query = b.include_archived(include_archived)
        return self._core.request(
            "GET", "/currencies", lambda d, _: [ManagedCurrency.from_api(c) for c in d["currencies"]], query=query
        )


class ItemsResource(_Resource):
    """The items of the game: ``create``, ``get``, ``update``, ``archive``, ``list``."""

    @staticmethod
    def _path(sku: str) -> str:
        return f"/items/{v.segment(v.require_str(sku, 'sku'))}"

    def create(
        self,
        *,
        sku: str,
        name: str,
        type: str,
        description: Optional[str] = None,
        price: Union[Price, Mapping[str, Any], None] = None,
        max_owned: Union[int, None, _Unset] = UNSET,
        client_purchasable: Optional[bool] = None,
        metadata: Optional[Mapping[str, str]] = None,
    ) -> ManagedItem:
        """Creates an item. Its ``sku`` and ``type`` (``"consumable"`` or ``"durable"``) are final. A taken sku is
        ``CONFLICT`` with ``details["fieldErrors"]["sku"] == ["SKU_TAKEN"]``; an unknown price currency, or
        ``client_purchasable`` without a price, is ``VALIDATION_FAILED``.

        ``price`` is a :class:`~gamecoin.models.Price` or ``{"currency": "gems", "amount": 20}``; leave it out for an item
        that cannot be bought with currency. ``max_owned`` left out: 1 for a durable item, no limit for a consumable;
        ``None``: no limit. After a network error, a timeout or a ``5xx`` the outcome is unknown: read the item back with
        :meth:`get`.
        """
        body: Dict[str, Any] = {
            "sku": b.text(sku, "sku"),
            "name": b.text(name, "name"),
            "type": b.choice(type, "type", ("consumable", "durable")),
        }
        if description is not None:
            body["description"] = b.nullable_text(description, "description")
        if price is not None:
            body["price"] = b.price(price, "price")
        if max_owned is not UNSET:
            body["maxOwned"] = b.nullable_integer(max_owned, "max_owned", 1)
        if client_purchasable is not None:
            body["clientPurchasable"] = b.boolean(client_purchasable, "client_purchasable")
        if metadata is not None:
            body["metadata"] = b.string_map(metadata, "metadata")
        return self._core.request("POST", "/items", lambda d, _: ManagedItem.from_api(d), body=body, retry=RETRY_RATE_LIMIT_ONLY)

    def get(self, sku: str) -> ManagedItem:
        """An item, archived ones included; ``NOT_FOUND`` for an unknown sku."""
        return self._core.request("GET", self._path(sku), lambda d, _: ManagedItem.from_api(d))

    def update(
        self,
        sku: str,
        *,
        name: Union[str, _Unset] = UNSET,
        description: Union[str, None, _Unset] = UNSET,
        price: Union[Price, Mapping[str, Any], None, _Unset] = UNSET,
        max_owned: Union[int, None, _Unset] = UNSET,
        client_purchasable: Union[bool, _Unset] = UNSET,
        metadata: Union[Mapping[str, str], _Unset] = UNSET,
    ) -> ManagedItem:
        """Changes an item: an argument left out is unchanged; ``None`` clears the description, the price or the limit.
        ``metadata`` replaces the whole mapping. The sku and the type are final. An archived item cannot be changed
        (``CONFLICT``)."""
        path = self._path(sku)
        body = _managed_body(
            {
                "name": UNSET if name is UNSET else b.text(name, "name"),
                "description": UNSET if description is UNSET else b.nullable_text(description, "description"),
                "price": UNSET if price is UNSET else b.price(price, "price"),
                "maxOwned": UNSET if max_owned is UNSET else b.nullable_integer(max_owned, "max_owned", 1),
                "clientPurchasable": UNSET
                if client_purchasable is UNSET
                else b.boolean(client_purchasable, "client_purchasable"),
                "metadata": UNSET if metadata is UNSET else b.string_map(metadata, "metadata"),
            }
        )
        return self._core.request("PATCH", path, lambda d, _: ManagedItem.from_api(d), body=body)

    def archive(self, sku: str) -> ManagedItem:
        """Archives an item (nothing is deleted; inventories keep it) and returns it. Refused with ``CONFLICT``
        (``details["blockedBy"]["packs"]``) while an active pack contains it. Archiving an archived item has no effect."""
        return self._core.request("DELETE", self._path(sku), lambda d, _: ManagedItem.from_api(d))

    def list(self, *, include_archived: bool = False) -> List[ManagedItem]:
        """The items of the game, oldest first; the archived ones only with ``include_archived=True``."""
        query = b.include_archived(include_archived)
        return self._core.request("GET", "/items", lambda d, _: [ManagedItem.from_api(i) for i in d["items"]], query=query)


class PacksResource(_Resource):
    """The packs of the game: ``create``, ``get``, ``update``, ``archive``, ``list``."""

    @staticmethod
    def _path(sku: str) -> str:
        return f"/packs/{v.segment(v.require_str(sku, 'sku'))}"

    def create(
        self,
        *,
        sku: str,
        name: str,
        price_cents: int,
        description: Optional[str] = None,
        grants: Optional[Sequence[Union[PackGrant, Mapping[str, Any]]]] = None,
        items: Optional[Sequence[Union[PackItem, Mapping[str, Any]]]] = None,
        badge: Optional[str] = None,
        max_per_player: Optional[int] = None,
        sort_order: Optional[int] = None,
        availability: Union[ManagedPackAvailability, Mapping[str, Any], None] = None,
        status: Optional[str] = None,
    ) -> ManagedPack:
        """Creates a pack, as a draft unless ``status`` is ``"active"``. Its ``sku`` is final. ``price_cents`` is in euro
        cents, VAT included (50 to 50,000). ``grants`` are ``{"currency", "amount", "bonus"}`` lines, ``items`` are
        ``{"sku", "quantity"}`` lines; a pack needs at least one of the two. A taken sku is ``CONFLICT``; an invalid pack
        is ``VALIDATION_FAILED`` with the faulty lines in ``details["fieldErrors"]``.

        ``availability`` is a mapping ``{"starts_at", "ends_at", "stock", "founder"}`` (datetimes with a time zone or ISO
        8601 strings; a key left out is ``None``, ``founder`` ``False``). After a network error, a timeout or a ``5xx``
        the outcome is unknown: read the pack back with :meth:`get`.
        """
        body: Dict[str, Any] = {
            "sku": b.text(sku, "sku"),
            "name": b.text(name, "name"),
            "priceCents": b.integer(price_cents, "price_cents", 0),
        }
        if description is not None:
            body["description"] = b.nullable_text(description, "description")
        if grants is not None:
            body["grants"] = b.grants(grants, "grants")
        if items is not None:
            body["items"] = b.pack_items(items, "items")
        if badge is not None:
            body["badge"] = b.choice(badge, "badge", b.BADGES)
        if max_per_player is not None:
            body["maxPerPlayer"] = b.integer(max_per_player, "max_per_player", 1)
        if sort_order is not None:
            body["sortOrder"] = b.integer(sort_order, "sort_order", -(2**53))
        if availability is not None:
            body["availability"] = b.availability(availability, "availability")
        if status is not None:
            body["status"] = b.choice(status, "status", ("draft", "active"))
        return self._core.request("POST", "/packs", lambda d, _: ManagedPack.from_api(d), body=body, retry=RETRY_RATE_LIMIT_ONLY)

    def get(self, sku: str) -> ManagedPack:
        """A pack, archived ones included; ``NOT_FOUND`` for an unknown sku."""
        return self._core.request("GET", self._path(sku), lambda d, _: ManagedPack.from_api(d))

    def update(
        self,
        sku: str,
        *,
        name: Union[str, _Unset] = UNSET,
        description: Union[str, None, _Unset] = UNSET,
        price_cents: Union[int, _Unset] = UNSET,
        grants: Union[Sequence[Union[PackGrant, Mapping[str, Any]]], _Unset] = UNSET,
        items: Union[Sequence[Union[PackItem, Mapping[str, Any]]], _Unset] = UNSET,
        badge: Union[str, None, _Unset] = UNSET,
        max_per_player: Union[int, None, _Unset] = UNSET,
        sort_order: Union[int, _Unset] = UNSET,
        availability: Union[ManagedPackAvailability, Mapping[str, Any], _Unset] = UNSET,
        status: Union[str, _Unset] = UNSET,
    ) -> ManagedPack:
        """Changes a pack: an argument left out is unchanged; ``None`` clears the description, the badge or the limit.
        ``grants``, ``items`` and ``availability`` replace the previous value as a whole (``availability={}`` removes
        every limit). The content is applied first, then ``status`` (``"draft"``, ``"active"`` or ``"archived"``); turning
        a pack active checks that every currency and item it uses is still active, and when that check fails the content
        change stays and the status does not move. An archived pack cannot be changed (``CONFLICT``), except to bring it
        back with ``status`` alone."""
        path = self._path(sku)
        body = _managed_body(
            {
                "name": UNSET if name is UNSET else b.text(name, "name"),
                "description": UNSET if description is UNSET else b.nullable_text(description, "description"),
                "priceCents": UNSET if price_cents is UNSET else b.integer(price_cents, "price_cents", 0),
                "grants": UNSET if grants is UNSET else b.grants(grants, "grants"),
                "items": UNSET if items is UNSET else b.pack_items(items, "items"),
                "badge": UNSET if badge is UNSET else b.nullable_choice(badge, "badge", b.BADGES),
                "maxPerPlayer": UNSET if max_per_player is UNSET else b.nullable_integer(max_per_player, "max_per_player", 1),
                "sortOrder": UNSET if sort_order is UNSET else b.integer(sort_order, "sort_order", -(2**53)),
                "availability": UNSET if availability is UNSET else b.availability(availability, "availability"),
                "status": UNSET if status is UNSET else b.choice(status, "status", b.PACK_STATUSES),
            }
        )
        if body.get("availability", 0) is None:
            raise TypeError("availability must be a mapping (use {} to remove every limit), not None")
        return self._core.request("PATCH", path, lambda d, _: ManagedPack.from_api(d), body=body)

    def archive(self, sku: str) -> ManagedPack:
        """Archives a pack (it is no longer sold; the orders already placed are unaffected) and returns it."""
        return self._core.request("DELETE", self._path(sku), lambda d, _: ManagedPack.from_api(d))

    def list(self, *, include_archived: bool = False) -> List[ManagedPack]:
        """The packs of the game in display order, drafts included; the archived ones only with ``include_archived=True``."""
        query = b.include_archived(include_archived)
        return self._core.request("GET", "/packs", lambda d, _: [ManagedPack.from_api(p) for p in d["packs"]], query=query)
