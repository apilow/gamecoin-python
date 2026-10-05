"""Idempotency keys, the replayed flag, and the retry rules (SDK-SPEC § 4)."""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from gamecoin import ErrorCode, GameCoinError
from gamecoin._core import MAX_RETRY_AFTER, backoff_delay, parse_retry_after, retry_delay
from tests.support import (
    ORDER_ID,
    api_error,
    balance_json,
    entry_json,
    item_json,
    make_client,
    movement_json,
    ok,
    order_json,
    player_json,
    replayed,
)

GRANT = {"currency": "gems", "amount": 100}


class IdempotencyKeyTests(unittest.TestCase):
    def test_every_mutation_sends_a_uuid_v4_key(self):
        client, transport, _ = make_client(
            ok(movement_json()),
            ok(movement_json()),
            ok({"item": item_json()}),
            ok({"item": item_json()}),
            ok({"entry": entry_json(), "balance": balance_json(), "item": item_json()}),
            ok({"order": order_json(), "checkoutUrl": "https://pay.test/x"}, 201),
        )
        client.wallet.grant("ext:u", **GRANT)
        client.wallet.spend("ext:u", **GRANT)
        client.inventory.grant("ext:u", sku="potion")
        client.inventory.consume("ext:u", sku="potion")
        client.purchases.create("ext:u", sku="potion")
        client.checkouts.create("ext:u", pack_sku="gems-500")
        keys = [request.headers["Idempotency-Key"] for request in transport.requests]
        self.assertEqual(len(keys), 6)
        for key in keys:
            parsed = uuid.UUID(key)
            self.assertEqual(parsed.version, 4)
            self.assertEqual(str(parsed), key)
        self.assertEqual(len(set(keys)), 6, "each call gets its own key")

    def test_reads_and_unkeyed_writes_send_no_key(self):
        client, transport, _ = make_client(
            ok(player_json()),
            ok(player_json()),
            ok({"token": "t", "expiresAt": "2026-10-05T12:00:00Z"}),
            ok(order_json()),
            ok(order_json(status="refunded")),
        )
        client.players.upsert("ext:u")
        client.players.get("ext:u")
        client.players.create_token("ext:u")
        client.orders.get(ORDER_ID)
        client.orders.refund(ORDER_ID)
        for request in transport.requests:
            self.assertNotIn("Idempotency-Key", request.headers, request.url)

    def test_the_caller_can_choose_the_key(self):
        client, transport, _ = make_client(ok(movement_json()), ok({"item": item_json()}), ok({"entry": entry_json(), "balance": balance_json(), "item": item_json()}), ok({"order": order_json(), "checkoutUrl": "u"}, 201))
        client.wallet.grant("ext:u", idempotency_key="quest-17-user-42", **GRANT)
        client.inventory.grant("ext:u", sku="potion", idempotency_key="inv-1")
        client.purchases.create("ext:u", sku="potion", idempotency_key="buy-1")
        client.checkouts.create("ext:u", pack_sku="gems-500", idempotency_key="co-1")
        self.assertEqual([r.headers["Idempotency-Key"] for r in transport.requests], ["quest-17-user-42", "inv-1", "buy-1", "co-1"])

    def test_the_key_is_generated_once_per_call_and_kept_across_retries(self):
        client, transport, _ = make_client(
            ConnectionError("reset"),
            api_error(503, ErrorCode.INTERNAL),
            api_error(429, ErrorCode.RATE_LIMITED, headers={"Retry-After": "1"}),
            ok(movement_json()),
            max_retries=3,
        )
        client.wallet.grant("ext:u", **GRANT)
        keys = {request.headers["Idempotency-Key"] for request in transport.requests}
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(len(keys), 1)
        # A new call is a new operation: new key.
        transport.script.append(ok(movement_json()))
        client.wallet.grant("ext:u", **GRANT)
        self.assertNotIn(transport.last.headers["Idempotency-Key"], keys)

    def test_a_chosen_key_is_kept_across_retries(self):
        client, transport, _ = make_client(api_error(500, ErrorCode.INTERNAL), ok(movement_json()))
        client.wallet.grant("ext:u", idempotency_key="mine", **GRANT)
        self.assertEqual([r.headers["Idempotency-Key"] for r in transport.requests], ["mine", "mine"])


class ReplayedFlagTests(unittest.TestCase):
    def test_wallet(self):
        client, _, _ = make_client(ok(movement_json()), replayed(movement_json()), ok(movement_json(), headers={"Idempotent-Replayed": "false"}), ok(movement_json(), headers={"idempotent-replayed": "TRUE"}))
        self.assertFalse(client.wallet.grant("ext:u", **GRANT).replayed)
        self.assertTrue(client.wallet.grant("ext:u", idempotency_key="k", **GRANT).replayed)
        self.assertFalse(client.wallet.spend("ext:u", **GRANT).replayed)
        self.assertTrue(client.wallet.spend("ext:u", **GRANT).replayed)  # header names and values are case-insensitive

    def test_inventory_purchase_and_checkout(self):
        client, _, _ = make_client(
            replayed({"item": item_json()}),
            ok({"item": item_json()}),
            replayed({"entry": entry_json(), "balance": balance_json(), "item": item_json()}),
            replayed({"order": order_json(status="paid"), "checkoutUrl": "https://pay.test/x"}),
        )
        self.assertTrue(client.inventory.grant("ext:u", sku="potion").replayed)
        self.assertFalse(client.inventory.consume("ext:u", sku="potion").replayed)
        self.assertTrue(client.purchases.create("ext:u", sku="potion").replayed)
        checkout = client.checkouts.create("ext:u", pack_sku="gems-500")
        self.assertTrue(checkout.replayed)
        self.assertEqual(checkout.order.status, "paid")  # a replayed checkout shows the order as it is now

    def test_listing_items_are_not_replays(self):
        client, _, _ = make_client(ok({"items": [item_json()]}))
        self.assertFalse(client.inventory.list("ext:u")[0].replayed)


class RetryTests(unittest.TestCase):
    def test_network_error_then_success(self):
        for failure, expected in ((ConnectionError("connection refused"), 1), (TimeoutError("timed out"), 1)):
            with self.subTest(failure=type(failure).__name__):
                client, transport, sleeps = make_client(failure, ok(movement_json()))
                self.assertEqual(client.wallet.grant("ext:u", **GRANT).balance.total, 100)
                self.assertEqual(len(transport.requests), 2)
                self.assertEqual(len(sleeps), expected)

    def test_5xx_then_success(self):
        for status in (500, 502, 503, 504, 599):
            with self.subTest(status=status):
                client, transport, sleeps = make_client(api_error(status, ErrorCode.INTERNAL), ok(movement_json()))
                client.wallet.grant("ext:u", **GRANT)
                self.assertEqual(len(transport.requests), 2)
                self.assertEqual(len(sleeps), 1)

    def test_429_honours_retry_after(self):
        client, transport, sleeps = make_client(
            api_error(429, ErrorCode.RATE_LIMITED, "Too many requests", headers={"Retry-After": "3"}), ok(movement_json())
        )
        client.wallet.grant("ext:u", **GRANT)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(sleeps, [3.0])

    def test_429_without_retry_after_uses_the_backoff(self):
        client, _, sleeps = make_client(api_error(429, ErrorCode.RATE_LIMITED), ok(movement_json()))
        client.wallet.grant("ext:u", **GRANT)
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(0.375 <= sleeps[0] <= 0.625, sleeps)

    def test_backoff_doubles_with_jitter(self):
        client, transport, sleeps = make_client(
            api_error(503, ErrorCode.INTERNAL), api_error(503, ErrorCode.INTERNAL), api_error(503, ErrorCode.INTERNAL), ok(movement_json()), max_retries=3
        )
        client.wallet.grant("ext:u", **GRANT)
        self.assertEqual(len(transport.requests), 4)
        for delay, base in zip(sleeps, (0.5, 1.0, 2.0)):
            self.assertTrue(base * 0.75 <= delay <= base * 1.25, (delay, base))

    def test_backoff_formula(self):
        self.assertEqual([backoff_delay(n, lambda: 0.5) for n in range(4)], [0.5, 1.0, 2.0, 4.0])
        self.assertEqual(backoff_delay(0, lambda: 0.0), 0.375)
        self.assertEqual(backoff_delay(0, lambda: 1.0), 0.625)
        self.assertEqual(backoff_delay(5, lambda: 0.5), 16.0)  # no cap: max_retries is at most 10
        self.assertEqual(retry_delay(0, 7), 7.0)
        self.assertEqual(retry_delay(2, 0), 0.0)
        self.assertEqual(retry_delay(0, MAX_RETRY_AFTER), MAX_RETRY_AFTER)
        self.assertIsNone(retry_delay(0, MAX_RETRY_AFTER + 0.1))

    def test_retry_after_parsing(self):
        self.assertEqual(parse_retry_after("3"), 3)
        self.assertEqual(parse_retry_after(" 12 "), 12)
        self.assertEqual(parse_retry_after("1.5"), 1.5)
        self.assertEqual(parse_retry_after("0"), 0)
        for bad in (None, "", "soon", "-4", "nan", "inf", "1e3", "2026-10-21"):
            self.assertIsNone(parse_retry_after(bad), bad)

    def test_retry_after_as_an_http_date(self):
        now = datetime(2026, 10, 21, 7, 27, 50, tzinfo=timezone.utc)
        self.assertEqual(parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", now), 10)
        self.assertEqual(parse_retry_after("Wed, 21 Oct 2026 07:27:50 GMT", now), 0)
        self.assertEqual(parse_retry_after("Wed, 21 Oct 2026 07:27:00 GMT", now), 0, "a date in the past is no wait")
        later = datetime(2026, 10, 21, 7, 27, 49, 400000, tzinfo=timezone.utc)
        self.assertEqual(parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", later), 11, "rounded up to the next second")

    def test_an_http_date_is_honoured_by_the_client(self):
        soon = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5), usegmt=True)
        client, transport, sleeps = make_client(api_error(503, ErrorCode.INTERNAL, headers={"Retry-After": soon}), ok(movement_json()))
        client.wallet.grant("ext:u", **GRANT)
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(3 <= sleeps[0] <= 5, sleeps)

    def test_retry_after_is_honoured_on_a_5xx_up_to_the_cap_included(self):
        client, transport, sleeps = make_client(api_error(503, ErrorCode.INTERNAL, headers={"Retry-After": "30"}), ok(movement_json()))
        client.wallet.grant("ext:u", **GRANT)
        self.assertEqual((len(transport.requests), sleeps), (2, [30.0]))
        client, transport, sleeps = make_client(api_error(503, ErrorCode.INTERNAL, headers={"Retry-After": "31"}))
        with self.assertRaises(GameCoinError) as caught:
            client.wallet.grant("ext:u", **GRANT)
        self.assertEqual((len(transport.requests), sleeps, caught.exception.status, caught.exception.retry_after), (1, [], 503, 31))

    def test_retry_after_beyond_the_cap_is_not_waited_for(self):
        client, transport, sleeps = make_client(api_error(429, ErrorCode.RATE_LIMITED, "Slow down", headers={"Retry-After": "45"}))
        with self.assertRaises(GameCoinError) as caught:
            client.wallet.grant("ext:u", **GRANT)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(caught.exception.code, ErrorCode.RATE_LIMITED)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 45)

    def test_gives_up_after_max_retries(self):
        client, transport, sleeps = make_client(*[api_error(503, ErrorCode.INTERNAL, "down")] * 3)
        with self.assertRaises(GameCoinError) as caught:
            client.wallet.grant("ext:u", **GRANT)
        self.assertEqual(len(transport.requests), 3)  # one attempt + two retries
        self.assertEqual(len(sleeps), 2)
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.INTERNAL, 503))

    def test_max_retries_is_configurable(self):
        client, transport, sleeps = make_client(ConnectionError("down"), max_retries=0)
        with self.assertRaises(GameCoinError) as caught:
            client.wallet.grant("ext:u", **GRANT)
        self.assertEqual((len(transport.requests), sleeps), (1, []))
        self.assertEqual(caught.exception.code, ErrorCode.NETWORK_ERROR)
        client, transport, _ = make_client(*[api_error(500, ErrorCode.INTERNAL)] * 6, max_retries=5)
        with self.assertRaises(GameCoinError):
            client.wallet.grant("ext:u", **GRANT)
        self.assertEqual(len(transport.requests), 6)

    def test_network_failures_end_as_network_error_or_timeout(self):
        client, transport, _ = make_client(*[ConnectionError("connection refused")] * 3)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.NETWORK_ERROR, 0))
        self.assertIn("connection refused", caught.exception.message)
        self.assertIsInstance(caught.exception.__cause__, ConnectionError)
        self.assertEqual(len(transport.requests), 3)

        client, _, _ = make_client(*[TimeoutError("slow")] * 3)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.TIMEOUT, 0))

    def test_reads_and_unkeyed_writes_are_retried_too(self):
        # Reads and PUT are safe to replay; POST .../tokens only mints one more short-lived token.
        client, transport, _ = make_client(
            api_error(502, ErrorCode.INTERNAL),
            ok(player_json()),
            ConnectionError("reset"),
            ok(player_json()),
            api_error(503, ErrorCode.INTERNAL),
            ok({"token": "t", "expiresAt": "2026-10-05T12:00:00Z"}),
        )
        client.players.get("ext:u")
        client.players.upsert("ext:u", display_name="A")
        client.players.create_token("ext:u")
        self.assertEqual(len(transport.requests), 6)

    def test_client_errors_are_never_retried(self):
        cases = [
            (400, ErrorCode.VALIDATION_FAILED),
            (400, ErrorCode.IDEMPOTENCY_KEY_REQUIRED),
            (401, ErrorCode.UNAUTHENTICATED),
            (402, ErrorCode.PAYMENT_FAILED),
            (403, ErrorCode.FORBIDDEN),
            (403, ErrorCode.PLAYER_BLOCKED),
            (404, ErrorCode.NOT_FOUND),
            (409, ErrorCode.INSUFFICIENT_FUNDS),
            (409, ErrorCode.LIMIT_REACHED),
            (409, ErrorCode.IDEMPOTENCY_CONFLICT),
            (409, ErrorCode.CONFLICT),
            (423, ErrorCode.READ_ONLY),
        ]
        for status, code in cases:
            with self.subTest(status=status, code=code):
                client, transport, sleeps = make_client(api_error(status, code), ok(movement_json()))
                with self.assertRaises(GameCoinError) as caught:
                    client.wallet.grant("ext:u", **GRANT)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(sleeps, [])


class RefundRetryTests(unittest.TestCase):
    """A refund has no idempotency key: only a 429 (the request was not processed) is retried."""

    def test_429_is_retried(self):
        client, transport, sleeps = make_client(api_error(429, ErrorCode.RATE_LIMITED, headers={"Retry-After": "2"}), ok(order_json(status="refunded")))
        self.assertEqual(client.orders.refund(ORDER_ID).status, "refunded")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(sleeps, [2.0])

    def test_429_beyond_the_cap_is_raised(self):
        client, transport, _ = make_client(api_error(429, ErrorCode.RATE_LIMITED, headers={"Retry-After": "60"}))
        with self.assertRaises(GameCoinError) as caught:
            client.orders.refund(ORDER_ID)
        self.assertEqual((len(transport.requests), caught.exception.retry_after), (1, 60))

    def test_5xx_is_not_retried(self):
        for status in (500, 502, 503):
            with self.subTest(status=status):
                client, transport, sleeps = make_client(api_error(status, ErrorCode.INTERNAL), ok(order_json()))
                with self.assertRaises(GameCoinError) as caught:
                    client.orders.refund(ORDER_ID)
                self.assertEqual(caught.exception.status, status)
                self.assertEqual((len(transport.requests), sleeps), (1, []))

    def test_network_error_and_timeout_are_not_retried(self):
        for failure, code in ((ConnectionError("reset"), ErrorCode.NETWORK_ERROR), (TimeoutError("slow"), ErrorCode.TIMEOUT)):
            with self.subTest(code=code):
                client, transport, sleeps = make_client(failure, ok(order_json()))
                with self.assertRaises(GameCoinError) as caught:
                    client.orders.refund(ORDER_ID)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual((len(transport.requests), sleeps), (1, []))

    def test_conflict_is_raised(self):
        client, _, _ = make_client(api_error(409, ErrorCode.CONFLICT, "Order is not paid"))
        with self.assertRaises(GameCoinError) as caught:
            client.orders.refund(ORDER_ID)
        self.assertEqual(caught.exception.code, ErrorCode.CONFLICT)


if __name__ == "__main__":
    unittest.main()
