"""v0.4: catalog management (currencies, items, packs): requests, results, validation, retries."""

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

import gamecoin
from gamecoin import (
    ErrorCode,
    GameCoinError,
    ManagedCurrency,
    ManagedItem,
    ManagedPack,
    ManagedPackAvailability,
    PackGrant,
    PackItem,
    Price,
)
from tests.support import API, api_error, body_of, make_client, ok

UTC = timezone.utc
CREATED = "2026-10-05T12:00:00.000Z"
UPDATED = "2026-10-05T13:00:00.000Z"


def currency_json(**over):
    data = {"code": "gems", "name": "Gems", "purchasable": True, "status": "active", "createdAt": CREATED, "updatedAt": UPDATED}
    data.update(over)
    return data


def item_json(**over):
    data = {
        "sku": "potion",
        "name": "Potion",
        "description": None,
        "type": "consumable",
        "price": {"currency": "gems", "amount": 20},
        "maxOwned": None,
        "clientPurchasable": True,
        "metadata": {"heal": "50"},
        "status": "active",
        "createdAt": CREATED,
        "updatedAt": UPDATED,
    }
    data.update(over)
    return data


def pack_json(**over):
    data = {
        "sku": "gems-500",
        "name": "500 gems",
        "description": None,
        "priceCents": 499,
        "currency": "EUR",
        "grants": [{"currency": "gems", "amount": 500, "bonus": 50}],
        "items": [{"sku": "potion", "quantity": 2}],
        "badge": "popular",
        "maxPerPlayer": None,
        "sortOrder": 10,
        "status": "active",
        "availability": {"startsAt": "2026-11-01T00:00:00.000Z", "endsAt": None, "stock": 1000, "founder": False, "sold": 4},
        "createdAt": CREATED,
        "updatedAt": UPDATED,
    }
    data.update(over)
    return data


class RequestTests(unittest.TestCase):
    """The verb, the URL and the body of every call. None sends an Idempotency-Key."""

    def check(self, reply, call, method, url, body=None):
        client, transport, _ = make_client(ok(reply))
        result = call(client)
        request = transport.last
        self.assertEqual((request.method, request.url), (method, url))
        if body is None:
            self.assertIsNone(request.body)
        else:
            self.assertEqual(body_of(request), body)
        self.assertNotIn("Idempotency-Key", request.headers)
        self.assertTrue(request.headers["Authorization"].startswith("Bearer gc_sk_test_"))
        return result

    def test_currencies(self):
        self.check({"currencies": [currency_json()]}, lambda c: c.currencies.list(), "GET", f"{API}/currencies")
        self.check({"currencies": []}, lambda c: c.currencies.list(include_archived=True), "GET", f"{API}/currencies?includeArchived=true")
        self.check({"currencies": []}, lambda c: c.currencies.list(include_archived=False), "GET", f"{API}/currencies")
        self.check(
            currency_json(),
            lambda c: c.currencies.create(code="gems", name="Gems", purchasable=True),
            "POST",
            f"{API}/currencies",
            {"code": "gems", "name": "Gems", "purchasable": True},
        )
        self.check(currency_json(), lambda c: c.currencies.create(code="gems", name="Gems"), "POST", f"{API}/currencies", {"code": "gems", "name": "Gems"})
        self.check(currency_json(), lambda c: c.currencies.get("gems"), "GET", f"{API}/currencies/gems")
        self.check(
            currency_json(),
            lambda c: c.currencies.update("gems", name="Shiny", purchasable=False),
            "PATCH",
            f"{API}/currencies/gems",
            {"name": "Shiny", "purchasable": False},
        )
        self.check(currency_json(status="archived"), lambda c: c.currencies.archive("gems"), "DELETE", f"{API}/currencies/gems")

    def test_items(self):
        self.check({"items": [item_json()]}, lambda c: c.items.list(), "GET", f"{API}/items")
        self.check(item_json(), lambda c: c.items.get("a b/c"), "GET", f"{API}/items/a%20b%2Fc")
        self.check(
            item_json(),
            lambda c: c.items.create(
                sku="potion",
                name="Potion",
                type="consumable",
                price=Price("gems", 20),
                client_purchasable=True,
                metadata={"heal": "50"},
            ),
            "POST",
            f"{API}/items",
            {
                "sku": "potion",
                "name": "Potion",
                "type": "consumable",
                "price": {"currency": "gems", "amount": 20},
                "clientPurchasable": True,
                "metadata": {"heal": "50"},
            },
        )
        # max_owned left out is not sent; None means "no limit" and is sent as null.
        self.check(item_json(), lambda c: c.items.create(sku="x", name="X", type="durable"), "POST", f"{API}/items", {"sku": "x", "name": "X", "type": "durable"})
        self.check(
            item_json(),
            lambda c: c.items.create(sku="x", name="X", type="durable", max_owned=None, price={"currency": "gems", "amount": 5}),
            "POST",
            f"{API}/items",
            {"sku": "x", "name": "X", "type": "durable", "maxOwned": None, "price": {"currency": "gems", "amount": 5}},
        )
        self.check(
            item_json(),
            lambda c: c.items.update("potion", price=None, description=None, max_owned=None),
            "PATCH",
            f"{API}/items/potion",
            {"price": None, "description": None, "maxOwned": None},
        )
        self.check(item_json(), lambda c: c.items.update("potion"), "PATCH", f"{API}/items/potion", {})
        self.check(item_json(status="archived"), lambda c: c.items.archive("potion"), "DELETE", f"{API}/items/potion")

    def test_packs(self):
        self.check({"packs": [pack_json()]}, lambda c: c.packs.list(include_archived=True), "GET", f"{API}/packs?includeArchived=true")
        self.check(pack_json(), lambda c: c.packs.get("gems-500"), "GET", f"{API}/packs/gems-500")
        self.check(
            pack_json(),
            lambda c: c.packs.create(
                sku="gems-500",
                name="500 gems",
                price_cents=499,
                grants=[PackGrant("gems", 500, 50), {"currency": "gold", "amount": 0, "bonus": 5}],
                items=[PackItem("potion", 2)],
                badge="popular",
                sort_order=10,
                availability={"starts_at": datetime(2026, 11, 1, tzinfo=UTC), "ends_at": "2026-12-01T00:00:00+01:00", "stock": 1000},
                status="active",
            ),
            "POST",
            f"{API}/packs",
            {
                "sku": "gems-500",
                "name": "500 gems",
                "priceCents": 499,
                "grants": [{"currency": "gems", "amount": 500, "bonus": 50}, {"currency": "gold", "amount": 0, "bonus": 5}],
                "items": [{"sku": "potion", "quantity": 2}],
                "badge": "popular",
                "sortOrder": 10,
                "availability": {"startsAt": "2026-11-01T00:00:00+00:00", "endsAt": "2026-12-01T00:00:00+01:00", "stock": 1000, "founder": False},
                "status": "active",
            },
        )
        self.check(
            pack_json(),
            lambda c: c.packs.update("gems-500", price_cents=599, sort_order=-3, availability={}, status="active", badge=None),
            "PATCH",
            f"{API}/packs/gems-500",
            {
                "priceCents": 599,
                "sortOrder": -3,
                "availability": {"startsAt": None, "endsAt": None, "stock": None, "founder": False},
                "status": "active",
                "badge": None,
            },
        )
        self.check(pack_json(status="archived"), lambda c: c.packs.archive("gems-500"), "DELETE", f"{API}/packs/gems-500")

    def test_an_availability_read_back_can_be_sent_again(self):
        client, transport, _ = make_client(ok(pack_json()), ok(pack_json()))
        pack = client.packs.get("gems-500")
        client.packs.update("gems-500", availability=pack.availability)
        self.assertEqual(
            body_of(transport.last),
            {"availability": {"startsAt": "2026-11-01T00:00:00+00:00", "endsAt": None, "stock": 1000, "founder": False}},
        )


class ResultTests(unittest.TestCase):
    def test_objects_are_typed_with_aware_datetimes(self):
        client, _, _ = make_client(ok(currency_json()), ok(item_json()), ok(pack_json()))
        currency = client.currencies.get("gems")
        self.assertIsInstance(currency, ManagedCurrency)
        self.assertEqual((currency.code, currency.name, currency.purchasable, currency.status), ("gems", "Gems", True, "active"))
        self.assertEqual(currency.created_at, datetime(2026, 10, 5, 12, tzinfo=UTC))
        self.assertEqual(currency.updated_at - currency.created_at, timedelta(hours=1))

        item = client.items.get("potion")
        self.assertIsInstance(item, ManagedItem)
        self.assertEqual(item.price, Price("gems", 20))
        self.assertEqual(item.metadata, {"heal": "50"})
        self.assertEqual(item.status, "active")

        pack = client.packs.get("gems-500")
        self.assertIsInstance(pack, ManagedPack)
        self.assertEqual(pack.grants, (PackGrant("gems", 500, 50),))
        self.assertEqual(pack.items, (PackItem("potion", 2),))
        self.assertEqual((pack.sort_order, pack.status, pack.badge), (10, "active", "popular"))
        self.assertEqual(
            pack.availability,
            ManagedPackAvailability(datetime(2026, 11, 1, tzinfo=UTC), None, 1000, False, 4),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            pack.status = "draft"  # type: ignore[misc]

    def test_a_pack_without_availability_reads_as_none(self):
        client, _, _ = make_client(ok(pack_json(availability=None)))
        self.assertIsNone(client.packs.get("gems-500").availability)

    def test_lists_are_lists(self):
        client, _, _ = make_client(ok({"items": [item_json(), item_json(sku="elixir")]}))
        self.assertEqual([i.sku for i in client.items.list()], ["potion", "elixir"])

    def test_a_response_of_the_wrong_shape_is_invalid_response(self):
        client, _, _ = make_client(ok({"code": "gems"}))
        with self.assertRaises(GameCoinError) as raised:
            client.currencies.get("gems")
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_RESPONSE)

    def test_api_errors_carry_their_details(self):
        client, _, _ = make_client(api_error(409, "CONFLICT", "in use", {"blockedBy": {"packs": ["gems-500"], "items": []}}))
        with self.assertRaises(GameCoinError) as raised:
            client.currencies.archive("gems")
        self.assertEqual(raised.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(raised.exception.details["blockedBy"]["packs"], ["gems-500"])


class ArgumentTests(unittest.TestCase):
    def test_bad_arguments_never_reach_the_network(self):
        client, transport, _ = make_client()
        bad = [
            (lambda: client.currencies.get(""), ValueError),
            (lambda: client.items.get(None), TypeError),
            (lambda: client.currencies.create(code="gems"), TypeError),
            (lambda: client.currencies.create(code="gems", name="Gems", purchasable="yes"), TypeError),
            (lambda: client.items.create(sku="x", name="X", type="weapon"), ValueError),
            (lambda: client.items.create(sku="x", name="X", type="durable", price={"currency": "gems", "amount": 0}), ValueError),
            (lambda: client.items.create(sku="x", name="X", type="durable", price={"currency": "gems", "amount": 1, "rate": 2}), ValueError),
            (lambda: client.items.create(sku="x", name="X", type="durable", max_owned=0), ValueError),
            (lambda: client.items.update("x", metadata={"heal": 50}), TypeError),
            (lambda: client.packs.create(sku="x", name="X", price_cents=4.5), TypeError),
            (lambda: client.packs.create(sku="x", name="X", price_cents=True), TypeError),
            (lambda: client.packs.create(sku="x", name="X", price_cents=500, grants="gems"), TypeError),
            (lambda: client.packs.create(sku="x", name="X", price_cents=500, grants=[{"currency": "gems", "amount": 5}]), TypeError),
            (lambda: client.packs.create(sku="x", name="X", price_cents=500, grants=[{"currency": "gems", "amount": 5, "bonus": 0, "x": 1}]), ValueError),
            (lambda: client.packs.create(sku="x", name="X", price_cents=500, badge="hot"), ValueError),
            (lambda: client.packs.create(sku="x", name="X", price_cents=500, status="archived"), ValueError),
            (lambda: client.packs.update("x", availability={"starts_at": datetime(2026, 11, 1)}), ValueError),
            (lambda: client.packs.update("x", availability={"ends_at": "tomorrow"}), ValueError),
            (lambda: client.packs.update("x", availability={"stock": 0}), ValueError),
            (lambda: client.packs.update("x", availability={"stocks": 5}), ValueError),
            (lambda: client.packs.update("x", availability=None), TypeError),
            (lambda: client.packs.update("x", status="deleted"), ValueError),
            (lambda: client.items.list(include_archived="true"), TypeError),
        ]
        for call, error in bad:
            with self.subTest(call=call), self.assertRaises(error):
                call()
        self.assertEqual(transport.requests, [])

    def test_unknown_keyword_arguments_are_python_errors(self):
        client, transport, _ = make_client()
        with self.assertRaises(TypeError):
            client.currencies.update("gems", code="other")  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            client.items.update("x", type="durable")  # type: ignore[call-arg]
        self.assertEqual(transport.requests, [])


class RetryTests(unittest.TestCase):
    def test_a_create_is_retried_on_429_only(self):
        client, transport, sleeps = make_client(api_error(429, "RATE_LIMITED", "slow down", headers={"Retry-After": "2"}), ok(currency_json()))
        client.currencies.create(code="gems", name="Gems")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(sleeps, [2])

        creates = (
            lambda c: c.currencies.create(code="gems", name="Gems"),
            lambda c: c.items.create(sku="x", name="X", type="durable"),
            lambda c: c.packs.create(sku="x", name="X", price_cents=500, items=[{"sku": "y", "quantity": 1}]),
        )
        for create in creates:
            client, transport, _ = make_client(api_error(502, "INTERNAL", "bad gateway"))
            with self.assertRaises(GameCoinError):
                create(client)
            self.assertEqual(len(transport.requests), 1)
            client, transport, _ = make_client(OSError("boom"))
            with self.assertRaises(GameCoinError) as raised:
                create(client)
            self.assertEqual(raised.exception.code, ErrorCode.NETWORK_ERROR)
            self.assertEqual(len(transport.requests), 1)

    def test_reads_updates_and_archives_are_retried_on_5xx(self):
        calls = (
            lambda c: c.currencies.update("gems", name="G"),
            lambda c: c.items.archive("potion"),
            lambda c: c.packs.get("x"),
            lambda c: c.packs.list(),
        )
        for call in calls:
            client, transport, _ = make_client(api_error(503, "INTERNAL", "unavailable"), ok({"unrelated": True}))
            try:
                call(client)
            except GameCoinError:
                pass
            self.assertEqual(len(transport.requests), 2)


class VersionTests(unittest.TestCase):
    def test_version_and_exports(self):
        self.assertEqual(gamecoin.__version__, "0.4.0")
        for name in ("ManagedCurrency", "ManagedItem", "ManagedPack", "ManagedPackAvailability", "CatalogStatus", "PackStatus"):
            self.assertIn(name, gamecoin.__all__)
        client, _, _ = make_client()
        for resource in ("currencies", "items", "packs"):
            for method in ("create", "get", "update", "archive", "list"):
                self.assertTrue(callable(getattr(getattr(client, resource), method)), f"{resource}.{method}")


if __name__ == "__main__":
    unittest.main()
