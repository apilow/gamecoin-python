"""The API resources reached through a client: ``gamecoin.wallet``, ``gamecoin.ledger``, ``gamecoin.orders``..."""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Mapping, Optional, Union

from . import _validate as v
from ._core import RETRY_RATE_LIMIT_ONLY, Core
from .models import (
    Catalog,
    Checkout,
    InventoryItem,
    LedgerEntry,
    LedgerPage,
    Movement,
    Order,
    Player,
    PlayerToken,
    Purchase,
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
        idempotency_key: Optional[str] = None,
    ) -> Checkout:
        """Starts the purchase of a pack with real money: send the player to ``checkout.checkout_url``.

        ``country`` (ISO 3166-1 alpha-2) sets the tax country of the order. Coins and items are credited when the
        order is paid; follow the order with :meth:`OrdersResource.get`.
        """
        path = self._player_path(player, "/checkouts")
        body: Dict[str, Any] = {"packSku": v.require_str(pack_sku, "pack_sku")}
        for key, value in (("successUrl", success_url), ("cancelUrl", cancel_url), ("country", country)):
            if value is not None:
                body[key] = v.require_str(value, key)
        return self._core.request(
            "POST", path, Checkout.from_api, body=body, idempotent=True, idempotency_key=v.idempotency_key(idempotency_key)
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
