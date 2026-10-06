"""What each method puts on the wire: path, query, body. And the arguments refused before any request."""

import unittest
from urllib.parse import unquote

from gamecoin import UNSET, Player, ext
from tests.support import (
    API,
    ORDER_ID,
    PLAYER_ID,
    balance_json,
    body_of,
    catalog_json,
    entry_json,
    item_json,
    make_client,
    movement_json,
    ok,
    order_json,
    player_json,
)


class PathEncodingTests(unittest.TestCase):
    CASES = [
        ("user-42", "ext%3Auser-42"),
        ("user 42", "ext%3Auser%2042"),
        ("a/b", "ext%3Aa%2Fb"),
        ("100%", "ext%3A100%25"),
        ("%2F", "ext%3A%252F"),
        ("é日本", "ext%3A%C3%A9%E6%97%A5%E6%9C%AC"),
        ("a:b?c#d&e=f+g", "ext%3Aa%3Ab%3Fc%23d%26e%3Df%2Bg"),
        ("☃ \U0001F600", "ext%3A%E2%98%83%20%F0%9F%98%80"),
    ]

    def test_ext_helper_builds_the_reference(self):
        self.assertEqual(ext("user-42"), "ext:user-42")
        self.assertEqual(ext("a b/c"), "ext:a b/c")
        for bad in ("", None, 42):
            with self.assertRaises((ValueError, TypeError)):
                ext(bad)  # type: ignore[arg-type]

    def test_ext_ids_are_encoded_exactly_once(self):
        for external_id, encoded in self.CASES:
            with self.subTest(external_id=external_id):
                client, transport, _ = make_client(ok({"playerId": PLAYER_ID, "balances": []}))
                client.wallet.get(ext(external_id))
                self.assertEqual(transport.last.url, f"{API}/players/{encoded}/wallet")
                # One decoding gives the id back: it was encoded once, not twice.
                self.assertEqual(unquote(encoded), "ext:" + external_id)

    def test_a_player_id_is_left_alone(self):
        client, transport, _ = make_client(ok({"playerId": PLAYER_ID, "balances": []}))
        client.wallet.get(PLAYER_ID)
        self.assertEqual(transport.last.url, f"{API}/players/{PLAYER_ID}/wallet")

    def test_a_player_object_can_stand_for_its_id(self):
        player = Player.from_api(player_json())
        client, transport, _ = make_client(ok({"playerId": PLAYER_ID, "balances": []}))
        client.wallet.get(player)
        self.assertEqual(transport.last.url, f"{API}/players/{PLAYER_ID}/wallet")

    def test_every_player_route_encodes_the_reference(self):
        encoded = "ext%3Aa%2Fb%20c"
        ref = ext("a/b c")
        calls = [
            (lambda c: c.players.upsert(ref), ok(player_json()), "PUT", f"/players/{encoded}"),
            (lambda c: c.players.get(ref), ok(player_json()), "GET", f"/players/{encoded}"),
            (lambda c: c.players.create_token(ref), ok({"token": "t", "expiresAt": "2026-10-05T12:00:00Z"}), "POST", f"/players/{encoded}/tokens"),
            (lambda c: c.wallet.get(ref), ok({"playerId": "p", "balances": []}), "GET", f"/players/{encoded}/wallet"),
            (lambda c: c.wallet.grant(ref, currency="gems", amount=1), ok(movement_json()), "POST", f"/players/{encoded}/wallet/grant"),
            (lambda c: c.wallet.spend(ref, currency="gems", amount=1), ok(movement_json()), "POST", f"/players/{encoded}/wallet/spend"),
            (lambda c: c.ledger.list(ref), ok({"entries": [], "nextCursor": None}), "GET", f"/players/{encoded}/ledger"),
            (lambda c: c.inventory.list(ref), ok({"items": []}), "GET", f"/players/{encoded}/inventory"),
            (lambda c: c.inventory.grant(ref, sku="potion"), ok({"item": item_json()}), "POST", f"/players/{encoded}/inventory/grant"),
            (lambda c: c.inventory.consume(ref, sku="potion"), ok({"item": item_json()}), "POST", f"/players/{encoded}/inventory/consume"),
            (
                lambda c: c.purchases.create(ref, sku="potion"),
                ok({"entry": entry_json(), "balance": balance_json(), "item": item_json()}),
                "POST",
                f"/players/{encoded}/purchases",
            ),
            (
                lambda c: c.checkouts.create(ref, pack_sku="gems-500"),
                ok({"order": order_json(), "checkoutUrl": "https://pay.test/x"}, 201),
                "POST",
                f"/players/{encoded}/checkouts",
            ),
        ]
        for call, response, method, path in calls:
            with self.subTest(method=method, path=path):
                client, transport, _ = make_client(response)
                call(client)
                self.assertEqual((transport.last.method, transport.last.url), (method, API + path))

    def test_order_ids_are_encoded_too(self):
        client, transport, _ = make_client(ok(order_json()), ok(order_json()))
        client.orders.get("a/b")
        self.assertEqual(transport.last.url, f"{API}/orders/a%2Fb")
        client.orders.refund("a b")
        self.assertEqual(transport.last.url, f"{API}/orders/a%20b/refund")


class BodyAndQueryTests(unittest.TestCase):
    def test_catalog(self):
        client, transport, _ = make_client(ok(catalog_json()))
        client.catalog.get()
        self.assertEqual((transport.last.method, transport.last.url, transport.last.body), ("GET", f"{API}/catalog", None))

    def test_players_upsert_sends_only_the_given_fields(self):
        client, transport, _ = make_client(*[ok(player_json())] * 4)
        client.players.upsert("ext:u")
        self.assertEqual(body_of(transport.last), {})
        self.assertEqual(transport.last.method, "PUT")
        client.players.upsert("ext:u", display_name="Alice", country="BE", birth_year=1990, email="a@example.com")
        self.assertEqual(body_of(transport.last), {"displayName": "Alice", "country": "BE", "birthYear": 1990, "email": "a@example.com"})
        # None erases a field (JSON null); leaving it out keeps it.
        client.players.upsert("ext:u", display_name=None, birth_year=None)
        self.assertEqual(body_of(transport.last), {"displayName": None, "birthYear": None})
        client.players.upsert("ext:u", country=UNSET)
        self.assertEqual(body_of(transport.last), {})

    def test_players_get_and_token_have_no_body(self):
        client, transport, _ = make_client(ok(player_json()), ok({"token": "t", "expiresAt": "2026-10-05T12:00:00Z"}))
        client.players.get("ext:u")
        self.assertIsNone(transport.last.body)
        client.players.create_token("ext:u")
        self.assertEqual(transport.last.method, "POST")
        self.assertIsNone(transport.last.body)

    def test_wallet_grant_and_spend(self):
        client, transport, _ = make_client(*[ok(movement_json())] * 4)
        client.wallet.grant("ext:u", currency="gems", amount=100)
        self.assertEqual(body_of(transport.last), {"currency": "gems", "amount": 100})
        client.wallet.grant("ext:u", currency="gems", amount=100, reason="level_up", metadata={"level": "7", "né": "éé"})
        self.assertEqual(
            body_of(transport.last),
            {"currency": "gems", "amount": 100, "reason": "level_up", "metadata": {"level": "7", "né": "éé"}},
        )
        client.wallet.spend("ext:u", currency="gold", amount=5, reason="shop")
        self.assertEqual(transport.last.url, f"{API}/players/ext%3Au/wallet/spend")
        self.assertEqual(body_of(transport.last), {"currency": "gold", "amount": 5, "reason": "shop"})
        client.wallet.spend("ext:u", currency="gold", amount=5, metadata={})
        self.assertEqual(body_of(transport.last), {"currency": "gold", "amount": 5, "metadata": {}})

    def test_bodies_are_utf8_json(self):
        client, transport, _ = make_client(ok(movement_json()))
        client.wallet.grant("ext:u", currency="gems", amount=1, reason="café")
        self.assertIn("café".encode("utf-8"), transport.last.body)

    def test_ledger_list_query(self):
        client, transport, _ = make_client(*[ok({"entries": [], "nextCursor": None})] * 3)
        client.ledger.list("ext:u")
        self.assertEqual(transport.last.url, f"{API}/players/ext%3Au/ledger")
        client.ledger.list("ext:u", currency="gems", limit=50, cursor="abc/+=")
        self.assertEqual(transport.last.url, f"{API}/players/ext%3Au/ledger?currency=gems&limit=50&cursor=abc%2F%2B%3D")
        client.ledger.list("ext:u", limit=1)
        self.assertEqual(transport.last.url, f"{API}/players/ext%3Au/ledger?limit=1")
        self.assertIsNone(transport.last.body)

    def test_inventory(self):
        client, transport, _ = make_client(ok({"items": []}), ok({"item": item_json()}), ok({"item": item_json()}), ok({"item": item_json()}))
        client.inventory.list("ext:u")
        self.assertEqual((transport.last.method, transport.last.body), ("GET", None))
        client.inventory.grant("ext:u", sku="potion")
        self.assertEqual(body_of(transport.last), {"sku": "potion"})  # quantity defaults to 1 server side
        client.inventory.grant("ext:u", sku="potion", quantity=5)
        self.assertEqual(body_of(transport.last), {"sku": "potion", "quantity": 5})
        client.inventory.consume("ext:u", sku="potion", quantity=2)
        self.assertEqual(transport.last.url, f"{API}/players/ext%3Au/inventory/consume")
        self.assertEqual(body_of(transport.last), {"sku": "potion", "quantity": 2})

    def test_purchases(self):
        client, transport, _ = make_client(ok({"entry": entry_json(), "balance": balance_json(), "item": item_json()}))
        client.purchases.create("ext:u", sku="potion", quantity=3)
        self.assertEqual(body_of(transport.last), {"sku": "potion", "quantity": 3})

    def test_checkouts(self):
        client, transport, _ = make_client(*[ok({"order": order_json(), "checkoutUrl": "https://pay.test/x"}, 201)] * 4)
        client.checkouts.create("ext:u", pack_sku="gems-500")
        self.assertEqual(body_of(transport.last), {"packSku": "gems-500"})  # no locale sent when absent
        client.checkouts.create("ext:u", pack_sku="gems-500", locale="nl")
        self.assertEqual(body_of(transport.last), {"packSku": "gems-500", "locale": "nl"})
        client.checkouts.create("ext:u", pack_sku="gems-500", locale="xx")  # the API ignores unknown languages
        self.assertEqual(body_of(transport.last), {"packSku": "gems-500", "locale": "xx"})
        client.checkouts.create("ext:u", pack_sku="gems-500", success_url="https://game.test/ok", cancel_url="https://game.test/no", country="BE", locale="fr")
        self.assertEqual(
            body_of(transport.last),
            {
                "packSku": "gems-500",
                "successUrl": "https://game.test/ok",
                "cancelUrl": "https://game.test/no",
                "country": "BE",
                "locale": "fr",
            },
        )

    def test_orders(self):
        client, transport, _ = make_client(ok(order_json()), ok(order_json()), ok(order_json()))
        client.orders.get(ORDER_ID)
        self.assertEqual((transport.last.method, transport.last.url, transport.last.body), ("GET", f"{API}/orders/{ORDER_ID}", None))
        client.orders.refund(ORDER_ID)
        self.assertEqual((transport.last.method, transport.last.url), ("POST", f"{API}/orders/{ORDER_ID}/refund"))
        self.assertEqual(body_of(transport.last), {})
        client.orders.refund(ORDER_ID, reason="chargeback risk")
        self.assertEqual(body_of(transport.last), {"reason": "chargeback risk"})


class ArgumentValidationTests(unittest.TestCase):
    """Bad arguments raise ValueError / TypeError and never reach the transport."""

    def assert_refused(self, exception, call):
        client, transport, _ = make_client()  # an empty script fails the test if a request leaves
        with self.assertRaises(exception):
            call(client)
        self.assertEqual(transport.requests, [])

    def test_player(self):
        for bad in ("", None, 42, b"ext:u"):
            with self.subTest(player=bad):
                exception = ValueError if bad == "" else TypeError
                self.assert_refused(exception, lambda c: c.wallet.get(bad))
        self.assert_refused(ValueError, lambda c: c.players.get(""))
        self.assert_refused(ValueError, lambda c: c.wallet.grant("", currency="gems", amount=1))
        self.assert_refused(ValueError, lambda c: c.ledger.iterate(""))

    def test_amount(self):
        for bad, exception in ((0, ValueError), (-5, ValueError), (1.5, TypeError), (10.0, TypeError), ("10", TypeError), (True, TypeError), (None, TypeError)):
            with self.subTest(amount=bad):
                self.assert_refused(exception, lambda c: c.wallet.grant("ext:u", currency="gems", amount=bad))
                self.assert_refused(exception, lambda c: c.wallet.spend("ext:u", currency="gems", amount=bad))

    def test_quantity(self):
        for bad, exception in ((0, ValueError), (-1, ValueError), (2.5, TypeError), ("2", TypeError), (False, TypeError)):
            with self.subTest(quantity=bad):
                self.assert_refused(exception, lambda c: c.inventory.grant("ext:u", sku="potion", quantity=bad))
                self.assert_refused(exception, lambda c: c.inventory.consume("ext:u", sku="potion", quantity=bad))
                self.assert_refused(exception, lambda c: c.purchases.create("ext:u", sku="potion", quantity=bad))

    def test_names_and_texts(self):
        self.assert_refused(ValueError, lambda c: c.wallet.grant("ext:u", currency="", amount=1))
        self.assert_refused(TypeError, lambda c: c.wallet.grant("ext:u", currency=None, amount=1))
        self.assert_refused(ValueError, lambda c: c.inventory.grant("ext:u", sku=""))
        self.assert_refused(ValueError, lambda c: c.purchases.create("ext:u", sku=""))
        self.assert_refused(ValueError, lambda c: c.checkouts.create("ext:u", pack_sku=""))
        self.assert_refused(ValueError, lambda c: c.orders.get(""))
        self.assert_refused(ValueError, lambda c: c.orders.refund(""))
        self.assert_refused(TypeError, lambda c: c.wallet.grant("ext:u", currency="gems", amount=1, reason=42))
        self.assert_refused(TypeError, lambda c: c.players.upsert("ext:u", display_name=42))
        for field in ("display_name", "email", "country"):
            self.assert_refused(ValueError, lambda c: c.players.upsert("ext:u", **{field: ""}))
        self.assert_refused(ValueError, lambda c: c.checkouts.create("ext:u", pack_sku="p", country=""))
        self.assert_refused(ValueError, lambda c: c.checkouts.create("ext:u", pack_sku="p", success_url=""))
        self.assert_refused(ValueError, lambda c: c.checkouts.create("ext:u", pack_sku="p", locale=""))
        self.assert_refused(TypeError, lambda c: c.checkouts.create("ext:u", pack_sku="p", locale=33))
        self.assert_refused(TypeError, lambda c: c.players.upsert("ext:u", birth_year="1990"))
        self.assert_refused(TypeError, lambda c: c.checkouts.create("ext:u", pack_sku="p", country=33))

    def test_metadata(self):
        for bad in ({"level": 7}, {1: "a"}, ["a"], "level=7"):
            with self.subTest(metadata=bad):
                self.assert_refused(TypeError, lambda c: c.wallet.grant("ext:u", currency="gems", amount=1, metadata=bad))

    def test_listing_arguments(self):
        self.assert_refused(ValueError, lambda c: c.ledger.list("ext:u", limit=0))
        self.assert_refused(ValueError, lambda c: c.ledger.list("ext:u", limit=101))
        self.assert_refused(ValueError, lambda c: c.ledger.iterate("ext:u", limit=101))
        self.assert_refused(TypeError, lambda c: c.ledger.list("ext:u", limit=True))
        self.assert_refused(TypeError, lambda c: c.ledger.list("ext:u", limit="10"))
        self.assert_refused(ValueError, lambda c: c.ledger.list("ext:u", currency=""))
        self.assert_refused(ValueError, lambda c: c.ledger.iterate("ext:u", limit=-1))

    def test_a_limit_of_100_is_the_largest_accepted(self):
        client, transport, _ = make_client(ok({"entries": [], "nextCursor": None}))
        client.ledger.list("ext:u", limit=100)
        self.assertTrue(transport.requests[0].url.endswith("?limit=100"))

    def test_an_empty_reason_is_allowed(self):
        client, transport, _ = make_client(ok(movement_json()))
        client.wallet.grant("ext:u", currency="gems", amount=1, reason="")
        self.assertEqual(body_of(transport.last)["reason"], "")

    def test_messages_never_contain_the_faulty_value(self):
        calls = [
            lambda c: c.wallet.grant("ext:u", currency="gems", amount=-98765),
            lambda c: c.inventory.grant("ext:u", sku="potion", quantity=-43210),
            lambda c: c.ledger.list("ext:u", limit=424242),
            lambda c: c.wallet.grant("ext:u", currency="gems", amount=1, idempotency_key="caf\u00e9-FAULTY-KEY"),
            lambda c: c.wallet.grant("ext:u", currency="gems", amount=1, metadata={"k": 77777}),
            lambda c: c.wallet.get(31337.5),
        ]
        for call in calls:
            client, _, _ = make_client()
            with self.assertRaises((ValueError, TypeError)) as caught:
                call(client)
            message = str(caught.exception)
            for value in ("98765", "43210", "424242", "FAULTY-KEY", "77777", "31337"):
                self.assertNotIn(value, message)

    def test_idempotency_key(self):
        for bad, exception in (("", ValueError), ("x" * 101, ValueError), ("café", ValueError), ("line\nbreak", ValueError), ("tab\t", ValueError), (42, TypeError)):
            with self.subTest(key=bad):
                self.assert_refused(exception, lambda c: c.wallet.grant("ext:u", currency="gems", amount=1, idempotency_key=bad))
        client, transport, _ = make_client(ok(movement_json()), ok(movement_json()))
        client.wallet.grant("ext:u", currency="gems", amount=1, idempotency_key="x" * 100)  # the longest valid key
        client.wallet.grant("ext:u", currency="gems", amount=1, idempotency_key="order #1 (retry) ~!")
        self.assertEqual(transport.last.headers["Idempotency-Key"], "order #1 (retry) ~!")


if __name__ == "__main__":
    unittest.main()
