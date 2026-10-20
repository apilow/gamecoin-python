"""Integration test against the local sandbox game (`npm run sdk:sandbox` in the application repository).

Skipped unless GAMECOIN_SECRET_KEY and GAMECOIN_BASE_URL are both set. The sandbox game has the currencies `gems`
(paid) and `gold` (free), the items `potion` (consumable, 20 gems) and `fire-sword` (durable, 300 gems, one at most)
and the packs `starter` and `gems-500`. Every run uses a brand new player, so it can be repeated.
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from gamecoin import ErrorCode, GameCoin, GameCoinError, ext

SECRET_KEY = os.environ.get("GAMECOIN_SECRET_KEY")
BASE_URL = os.environ.get("GAMECOIN_BASE_URL")


@unittest.skipUnless(SECRET_KEY and BASE_URL, "set GAMECOIN_SECRET_KEY and GAMECOIN_BASE_URL to run the sandbox tests")
class SandboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gamecoin = GameCoin(SECRET_KEY, base_url=BASE_URL)
        cls.external_id = "sdk-py-" + uuid.uuid4().hex[:12]
        cls.player = ext(cls.external_id)

    def test_full_flow(self):
        gc, player = self.gamecoin, self.player
        self.assertEqual(gc.environment, "test")

        # catalog
        catalog = gc.catalog.get()
        self.assertTrue({"gems", "gold"} <= {c.code for c in catalog.currencies})
        items = {i.sku: i for i in catalog.items}
        self.assertEqual((items["potion"].type, items["potion"].price.currency, items["potion"].price.amount), ("consumable", "gems", 20))
        self.assertEqual((items["fire-sword"].type, items["fire-sword"].price.amount, items["fire-sword"].max_owned), ("durable", 300, 1))
        self.assertTrue({"starter", "gems-500"} <= {p.sku for p in catalog.packs})

        # a fresh ext: player
        created = gc.players.upsert(player, display_name="SDK test player")
        self.assertEqual((created.kind, created.external_id, created.display_name), ("external", self.external_id, "SDK test player"))
        self.assertEqual(gc.players.get(player).id, created.id)

        # grant 100 gems, then replay the same key
        key = "grant-" + uuid.uuid4().hex
        first = gc.wallet.grant(player, currency="gems", amount=100, reason="level_up", metadata={"level": "7"}, idempotency_key=key)
        self.assertFalse(first.replayed)
        self.assertEqual((first.balance.total, first.balance.bonus, first.balance.paid), (100, 100, 0))
        self.assertEqual((first.entry.type, first.entry.bonus_delta, first.entry.reason, first.entry.metadata), ("grant", 100, "level_up", {"level": "7"}))
        again = gc.wallet.grant(player, currency="gems", amount=100, reason="level_up", metadata={"level": "7"}, idempotency_key=key)
        self.assertTrue(again.replayed)
        self.assertEqual(again.entry.id, first.entry.id)
        self.assertEqual(again.balance.total, 100)

        # same key, another request
        with self.assertRaises(GameCoinError) as conflict:
            gc.wallet.grant(player, currency="gems", amount=999, idempotency_key=key)
        self.assertEqual((conflict.exception.code, conflict.exception.status), (ErrorCode.IDEMPOTENCY_CONFLICT, 409))

        # spend 30, then too much
        spent = gc.wallet.spend(player, currency="gems", amount=30, reason="shop")
        self.assertEqual((spent.balance.total, spent.entry.type, spent.entry.bonus_delta), (70, "spend", -30))
        with self.assertRaises(GameCoinError) as broke:
            gc.wallet.spend(player, currency="gems", amount=1000)
        self.assertEqual((broke.exception.code, broke.exception.status), (ErrorCode.INSUFFICIENT_FUNDS, 409))
        self.assertEqual((broke.exception.details["required"], broke.exception.details["available"]), (1000, 70))

        # wallet
        wallet = gc.wallet.get(player)
        self.assertEqual(wallet.balance_of("gems").total, 70)
        self.assertEqual(wallet.balance_of("gold").total, 0)

        # ledger: a page of one entry, then the whole iteration
        page = gc.ledger.list(player, currency="gems", limit=1)
        self.assertEqual(len(page.entries), 1)
        self.assertIsNotNone(page.next_cursor)
        entries = list(gc.ledger.iterate(player, currency="gems", limit=1))
        self.assertEqual([e.type for e in entries], ["spend", "grant"])  # newest first
        self.assertEqual(entries[0].balance_after.total, 70)
        self.assertEqual([e.id for e in entries], [e.id for e in gc.ledger.iterate(player)])

        # player token
        token = gc.players.create_token(player)
        self.assertTrue(token.token.startswith("gc_pt_"))
        self.assertGreater(token.expires_at, datetime.now(timezone.utc) + timedelta(seconds=30))

        # inventory
        granted = gc.inventory.grant(player, sku="potion", quantity=3)
        self.assertEqual((granted.sku, granted.quantity, granted.replayed), ("potion", 3, False))
        consumed = gc.inventory.consume(player, sku="potion")
        self.assertEqual(consumed.quantity, 2)
        self.assertEqual([(i.sku, i.quantity) for i in gc.inventory.list(player)], [("potion", 2)])

        # purchase a potion with gems
        bought = gc.purchases.create(player, sku="potion")
        self.assertEqual((bought.balance.total, bought.item.sku, bought.item.quantity, bought.entry.ref.item_sku), (50, "potion", 3, "potion"))

        # checkout of the gems-500 pack, then the order
        checkout = gc.checkouts.create(player, pack_sku="gems-500")
        self.assertEqual(checkout.order.status, "pending")
        self.assertEqual((checkout.order.pack.sku, checkout.order.currency), ("gems-500", "EUR"))
        self.assertTrue(checkout.checkout_url.startswith("http"))
        order = gc.orders.get(checkout.order.id)
        self.assertEqual((order.id, order.status, order.paid_at), (checkout.order.id, "pending", None))

        # refunding an order nobody paid is a conflict
        with self.assertRaises(GameCoinError) as refund:
            gc.orders.refund(order.id, reason="test")
        self.assertEqual((refund.exception.code, refund.exception.status), (ErrorCode.CONFLICT, 409))

        # an unknown player
        with self.assertRaises(GameCoinError) as missing:
            gc.wallet.get(ext("sdk-py-nobody-" + uuid.uuid4().hex))
        self.assertEqual((missing.exception.code, missing.exception.status), (ErrorCode.NOT_FOUND, 404))

    def test_awkward_external_ids_survive_the_round_trip(self):
        for suffix in ("with space", "a/b", "100%", "café 日本", "q?x=1&y=2#z", "plus+sign"):
            with self.subTest(suffix=suffix):
                external_id = f"sdk-py-{uuid.uuid4().hex[:8]} {suffix}"
                created = self.gamecoin.players.upsert(ext(external_id))
                self.assertEqual(created.external_id, external_id)
                fetched = self.gamecoin.players.get(ext(external_id))
                self.assertEqual((fetched.id, fetched.external_id), (created.id, external_id))

    def test_catalog_management(self):
        gc = self.gamecoin
        # A fresh suffix for every run: a code or a sku is never reusable, even once archived.
        suffix = uuid.uuid4().hex[:10]
        code, item_sku, pack_sku = f"ruby_{suffix}", f"elixir-{suffix}", f"mega-{suffix}"

        currency = gc.currencies.create(code=code, name="Ruby", purchasable=True)
        self.assertEqual((currency.status, currency.purchasable), ("active", True))
        self.assertEqual(gc.currencies.update(code, name="Red ruby").name, "Red ruby")

        item = gc.items.create(
            sku=item_sku, name="Elixir", type="consumable", price={"currency": code, "amount": 15}, client_purchasable=True, metadata={"heal": "30"}
        )
        self.assertEqual((item.price.currency, item.price.amount, item.max_owned), (code, 15, None))
        self.assertEqual(gc.items.update(item_sku, price={"currency": code, "amount": 25}).price.amount, 25)

        pack = gc.packs.create(
            sku=pack_sku,
            name="Mega pack",
            price_cents=999,
            grants=[{"currency": code, "amount": 1200, "bonus": 100}],
            items=[{"sku": item_sku, "quantity": 2}],
            badge="best_value",
        )
        self.assertEqual((pack.status, pack.availability), ("draft", None))

        starts = datetime(2030, 1, 1, tzinfo=timezone.utc)
        live = gc.packs.update(pack_sku, price_cents=1099, status="active", availability={"starts_at": starts, "stock": 50, "founder": True})
        self.assertEqual(live.status, "active")
        self.assertEqual((live.availability.starts_at, live.availability.ends_at, live.availability.stock, live.availability.founder, live.availability.sold), (starts, None, 50, True, 0))
        public = {p.sku: p for p in gc.catalog.get().packs}[pack_sku]
        self.assertEqual(public.availability.remaining, 50)
        self.assertIn(item_sku, [i.sku for i in gc.items.list()])

        # Archiving goes top-down: a pack, then the item it holds, then the currency.
        self.assertEqual(gc.packs.archive(pack_sku).status, "archived")
        self.assertEqual(gc.items.archive(item_sku).status, "archived")
        self.assertEqual(gc.currencies.archive(code).status, "archived")
        self.assertEqual(gc.currencies.archive(code).status, "archived")
        self.assertEqual(gc.currencies.get(code).status, "archived")
        self.assertNotIn(code, [c.code for c in gc.currencies.list()])
        self.assertIn(code, [c.code for c in gc.currencies.list(include_archived=True)])
        left = gc.catalog.get()
        self.assertNotIn(code, [c.code for c in left.currencies])
        self.assertNotIn(pack_sku, [p.sku for p in left.packs])

        with self.assertRaises(GameCoinError) as taken:
            gc.currencies.create(code="gems", name="Again")
        self.assertEqual(taken.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(taken.exception.details["fieldErrors"], {"code": ["CODE_TAKEN"]})
        with self.assertRaises(GameCoinError) as blocked:
            gc.currencies.archive("gems")
        self.assertEqual(blocked.exception.code, ErrorCode.CONFLICT)
        self.assertIn("blockedBy", blocked.exception.details)
        with self.assertRaises(GameCoinError) as invalid:
            gc.packs.create(sku=f"empty-{suffix}", name="Empty", price_cents=500)
        self.assertEqual(invalid.exception.code, ErrorCode.VALIDATION_FAILED)
        with self.assertRaises(GameCoinError) as missing:
            gc.items.get(f"nope-{suffix}")
        self.assertEqual(missing.exception.code, ErrorCode.NOT_FOUND)

    def test_a_wrong_key_is_unauthenticated(self):
        wrong = GameCoin("gc_sk_test_" + "x" * 43, base_url=BASE_URL, max_retries=0)
        with self.assertRaises(GameCoinError) as caught:
            wrong.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.UNAUTHENTICATED, 401))

    def test_a_publishable_key_is_refused_before_any_request(self):
        publishable = os.environ.get("GAMECOIN_PUBLISHABLE_KEY")
        if not publishable:
            self.skipTest("GAMECOIN_PUBLISHABLE_KEY is not set")
        with self.assertRaises(ValueError):
            GameCoin(publishable, base_url=BASE_URL)


if __name__ == "__main__":
    unittest.main()
