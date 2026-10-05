"""Parsing of every object, field for field, through the public methods."""

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from gamecoin import (
    Balance,
    BalanceAfter,
    Catalog,
    Checkout,
    Currency,
    EntryRef,
    ErrorCode,
    GameCoinError,
    InventoryItem,
    Item,
    LedgerEntry,
    LedgerPage,
    Movement,
    Order,
    OrderPack,
    Pack,
    PackGrant,
    PackItem,
    Player,
    PlayerToken,
    Price,
    Purchase,
    Wallet,
)
from gamecoin.models import parse_datetime
from tests.support import (
    ORDER_ID,
    PLAYER_ID,
    balance_json,
    catalog_json,
    entry_json,
    item_json,
    make_client,
    movement_json,
    ok,
    order_json,
    player_json,
)

UTC = timezone.utc


class ParsingTests(unittest.TestCase):
    def test_catalog(self):
        client, _, _ = make_client(ok(catalog_json()))
        catalog = client.catalog.get()
        self.assertEqual(
            catalog,
            Catalog(
                currencies=(Currency("gems", "Gems", True), Currency("gold", "Gold", False)),
                items=(
                    Item("potion", "Potion", "Heals", "consumable", Price("gems", 20), None, True, {"heal": "50"}),
                    Item("fire-sword", "Fire sword", None, "durable", None, 1, False, {}),
                ),
                packs=(
                    Pack(
                        "gems-500",
                        "500 gems",
                        None,
                        499,
                        "EUR",
                        (PackGrant("gems", 500, 50),),
                        (PackItem("potion", 2),),
                        "best value",
                        None,
                    ),
                ),
            ),
        )

    def test_player(self):
        client, _, _ = make_client(ok(player_json()), ok(player_json(externalId=None, displayName=None, country=None, kind="hosted", blocked=True)))
        self.assertEqual(
            client.players.get("ext:user-42"),
            Player(PLAYER_ID, "user-42", "external", "Alice", "BE", False, datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)),
        )
        hosted = client.players.get(PLAYER_ID)
        self.assertEqual((hosted.external_id, hosted.display_name, hosted.country, hosted.kind, hosted.blocked), (None, None, None, "hosted", True))

    def test_player_token(self):
        client, _, _ = make_client(ok({"token": "gc_pt_abc", "expiresAt": "2026-10-05T13:00:00.000Z"}))
        token = client.players.create_token("ext:u")
        self.assertEqual(token, PlayerToken("gc_pt_abc", datetime(2026, 10, 5, 13, 0, tzinfo=UTC)))

    def test_wallet(self):
        payload = {"playerId": PLAYER_ID, "balances": [balance_json(), balance_json(currency="gold", paid=0, bonus=0, total=0)]}
        client, _, _ = make_client(ok(payload))
        wallet = client.wallet.get("ext:u")
        self.assertEqual(wallet, Wallet(PLAYER_ID, (Balance("gems", 0, 100, 100), Balance("gold", 0, 0, 0))))
        self.assertEqual(wallet.balance_of("gold"), Balance("gold", 0, 0, 0))
        self.assertIsNone(wallet.balance_of("rubies"))

    def test_movement_and_ledger_entry(self):
        client, _, _ = make_client(ok(movement_json()))
        movement = client.wallet.grant("ext:u", currency="gems", amount=100, reason="level_up")
        self.assertEqual(
            movement,
            Movement(
                entry=LedgerEntry(
                    id="ffffffffffffffffffffff01",
                    type="grant",
                    currency="gems",
                    paid_delta=0,
                    bonus_delta=100,
                    balance_after=BalanceAfter(0, 100, 100),
                    reason="level_up",
                    metadata={"level": "7"},
                    ref=EntryRef(),
                    created_at=datetime(2026, 10, 5, 12, 0, 1, 250000, tzinfo=UTC),
                ),
                balance=Balance("gems", 0, 100, 100),
                replayed=False,
            ),
        )

    def test_ledger_entry_references(self):
        entry = LedgerEntry.from_api(entry_json(type="purchase_credit", reason=None, metadata={}, ref={"orderId": "o1", "itemSku": "potion"}))
        self.assertEqual(entry.ref, EntryRef(order_id="o1", item_sku="potion"))
        self.assertIsNone(entry.reason)
        self.assertEqual(entry.metadata, {})

    def test_ledger_page(self):
        client, _, _ = make_client(ok({"entries": [entry_json(), entry_json(id="ffffffffffffffffffffff02")], "nextCursor": "c1"}), ok({"entries": [], "nextCursor": None}))
        page = client.ledger.list("ext:u")
        self.assertIsInstance(page, LedgerPage)
        self.assertEqual([e.id for e in page.entries], ["ffffffffffffffffffffff01", "ffffffffffffffffffffff02"])
        self.assertEqual(page.next_cursor, "c1")
        self.assertIsNone(client.ledger.list("ext:u").next_cursor)

    def test_inventory(self):
        client, _, _ = make_client(ok({"items": [item_json(), item_json(sku="fire-sword", quantity=1)]}), ok({"item": item_json(quantity=0)}))
        items = client.inventory.list("ext:u")
        self.assertEqual(
            items,
            [
                InventoryItem("potion", 3, datetime(2026, 10, 5, 12, 0, 2, tzinfo=UTC)),
                InventoryItem("fire-sword", 1, datetime(2026, 10, 5, 12, 0, 2, tzinfo=UTC)),
            ],
        )
        self.assertEqual(client.inventory.consume("ext:u", sku="potion", quantity=3).quantity, 0)

    def test_purchase(self):
        client, _, _ = make_client(ok({"entry": entry_json(type="spend", bonusDelta=-20), "balance": balance_json(bonus=80, total=80), "item": item_json()}))
        purchase = client.purchases.create("ext:u", sku="potion")
        self.assertIsInstance(purchase, Purchase)
        self.assertEqual(purchase.entry.type, "spend")
        self.assertEqual(purchase.entry.bonus_delta, -20)
        self.assertEqual(purchase.balance.total, 80)
        self.assertEqual(purchase.item.sku, "potion")
        self.assertFalse(purchase.replayed)

    def test_order_and_checkout(self):
        client, _, _ = make_client(ok({"order": order_json(), "checkoutUrl": "https://pay.test/pay/1?t=x"}, 201), ok(order_json(status="refunded", paidAt="2026-10-05T12:05:00Z", fulfilledAt="2026-10-05T12:05:01.5+02:00", refundedAt="2026-10-05T13:00:00Z")))
        checkout = client.checkouts.create("ext:u", pack_sku="gems-500")
        self.assertIsInstance(checkout, Checkout)
        self.assertEqual(checkout.checkout_url, "https://pay.test/pay/1?t=x")
        self.assertEqual(
            checkout.order,
            Order(
                ORDER_ID, PLAYER_ID, "pending", OrderPack("gems-500", "500 gems"), 499, "EUR", "BE", 2100, 87, 412,
                datetime(2026, 10, 5, 12, 0, 3, tzinfo=UTC), None, None, None,
            ),
        )
        order = client.orders.get(ORDER_ID)
        self.assertEqual(order.status, "refunded")
        self.assertEqual(order.paid_at, datetime(2026, 10, 5, 12, 5, tzinfo=UTC))
        self.assertEqual(order.fulfilled_at, datetime(2026, 10, 5, 10, 5, 1, 500000, tzinfo=UTC))
        self.assertEqual(order.refunded_at, datetime(2026, 10, 5, 13, 0, tzinfo=UTC))

    def test_objects_are_frozen(self):
        balance = Balance("gems", 0, 1, 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            balance.total = 5  # type: ignore[misc]

    def test_unknown_fields_are_ignored(self):
        client, _, _ = make_client(ok(player_json(newField={"a": 1}, anotherOne=[1, 2])), ok({"extra": True, "item": item_json(color="red")}))
        self.assertEqual(client.players.get("ext:u").id, PLAYER_ID)
        self.assertEqual(client.inventory.grant("ext:u", sku="potion").quantity, 3)

    def test_unknown_enum_values_are_delivered_as_they_come(self):
        client, _, _ = make_client(ok(order_json(status="disputed_v2")))
        self.assertEqual(client.orders.get(ORDER_ID).status, "disputed_v2")

    def test_a_response_that_does_not_fit_is_an_invalid_response(self):
        for payload in ({"id": PLAYER_ID}, player_json(createdAt="yesterday"), player_json(createdAt=None), []):
            with self.subTest(payload=payload):
                client, _, _ = make_client(ok(payload))
                with self.assertRaises(GameCoinError) as caught:
                    client.players.get("ext:u")
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_RESPONSE)
                self.assertEqual(caught.exception.status, 200)


class DateTimeTests(unittest.TestCase):
    def test_formats_are_read_as_aware_utc(self):
        cases = {
            "2026-10-05T12:00:00Z": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
            "2026-10-05T12:00:00.000Z": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
            "2026-10-05T12:00:00.123Z": datetime(2026, 10, 5, 12, 0, 0, 123000, tzinfo=UTC),
            "2026-10-05T12:00:00.123456Z": datetime(2026, 10, 5, 12, 0, 0, 123456, tzinfo=UTC),
            "2026-10-05T12:00:00.123456789Z": datetime(2026, 10, 5, 12, 0, 0, 123456, tzinfo=UTC),
            "2026-10-05T12:00:00.5Z": datetime(2026, 10, 5, 12, 0, 0, 500000, tzinfo=UTC),
            "2026-10-05T12:00:00": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
            "2026-10-05T14:00:00+02:00": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
            "2026-10-05T14:00:00+0200": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
            "2026-10-05T07:30:00-04:30": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
            "2026-10-05T12:00:00+00:00": datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_datetime(text)
                self.assertEqual(parsed, expected)
                self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_garbage_is_refused(self):
        for bad in ("", "2026-10-05", "yesterday", "2026-13-45T00:00:00Z", None, 1759665600, "2026-10-05T12:00:00Zjunk"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_datetime(bad)


if __name__ == "__main__":
    unittest.main()
