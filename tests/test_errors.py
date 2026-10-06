"""Error mapping, `ledger.iterate`, and the secret key staying out of every message."""

import pickle
import traceback
import unittest

from gamecoin import ErrorCode, GameCoinError, HttpResponse
from tests.support import KEY, api_error, entry_json, make_client, ok, player_json


class ErrorMappingTests(unittest.TestCase):
    def test_api_error_fields(self):
        client, _, _ = make_client(
            api_error(409, "INSUFFICIENT_FUNDS", "Insufficient funds", {"required": 50, "available": 20}),
        )
        with self.assertRaises(GameCoinError) as caught:
            client.wallet.spend("ext:u", currency="gems", amount=50)
        error = caught.exception
        self.assertEqual(error.code, ErrorCode.INSUFFICIENT_FUNDS)
        self.assertEqual(error.code, "INSUFFICIENT_FUNDS")
        self.assertEqual(error.status, 409)
        self.assertEqual(error.message, "Insufficient funds")
        self.assertEqual(error.details, {"required": 50, "available": 20})
        self.assertIsNone(error.retry_after)
        self.assertIsInstance(error, Exception)

    def test_validation_details_are_kept_whole(self):
        details = {"fieldErrors": {"amount": ["Too small"], "player": ["PLAYER_REF_INVALID"]}}
        client, _, _ = make_client(api_error(400, "VALIDATION_FAILED", "Invalid request", details))
        with self.assertRaises(GameCoinError) as caught:
            client.players.get("not-a-player")
        self.assertEqual(caught.exception.details, details)

    def test_details_are_empty_when_absent(self):
        client, _, _ = make_client(api_error(404, "NOT_FOUND", "Player not found"))
        with self.assertRaises(GameCoinError) as caught:
            client.players.get("ext:ghost")
        self.assertEqual(caught.exception.details, {})
        self.assertIsNone(caught.exception.retry_after)

    def test_retry_after_is_exposed_in_seconds(self):
        client, _, _ = make_client(api_error(429, "RATE_LIMITED", "Too many", headers={"Retry-After": "42"}), max_retries=0)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual(caught.exception.retry_after, 42)

    def test_unknown_codes_pass_through(self):
        client, _, _ = make_client(api_error(418, "TEAPOT", "I am a teapot"))
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), ("TEAPOT", 418))

    def test_a_body_that_is_not_the_error_envelope_is_an_invalid_response(self):
        bodies = [
            b"<html><body>502 Bad Gateway</body></html>",
            b"",
            b"not json",
            b"[]",
            b'{"message": "nope"}',
            b'{"error": "flat string"}',
            b'{"error": {"code": 500, "message": "number code"}}',
            b'{"error": {"code": "X"}}',
            b"\xff\xfe\x00",
        ]
        for body in bodies:
            with self.subTest(body=body):
                client, _, _ = make_client(HttpResponse(400, {}, body))
                with self.assertRaises(GameCoinError) as caught:
                    client.catalog.get()
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_RESPONSE)
                self.assertEqual(caught.exception.status, 400)

    def test_a_gateway_page_on_5xx_is_retried_then_reported(self):
        client, transport, _ = make_client(*[HttpResponse(502, {}, b"<html>Bad Gateway</html>")] * 3)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.INVALID_RESPONSE, 502))
        self.assertEqual(len(transport.requests), 3)

    def test_a_2xx_that_is_not_json_is_an_invalid_response(self):
        for body in (b"", b"OK", b"[1, 2]", b'"text"', b"\xff"):
            with self.subTest(body=body):
                client, _, _ = make_client(HttpResponse(200, {}, body))
                with self.assertRaises(GameCoinError) as caught:
                    client.players.get("ext:u")
                self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.INVALID_RESPONSE, 200))

    def test_redirects_are_not_a_success(self):
        client, transport, _ = make_client(HttpResponse(302, {"Location": "https://elsewhere.test/"}, b""))
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.INVALID_RESPONSE, 302))
        self.assertEqual(len(transport.requests), 1)

    def test_text_forms(self):
        error = GameCoinError("NOT_FOUND", 404, "Player not found", {"x": 1}, 3)
        self.assertEqual(str(error), "NOT_FOUND (HTTP 404): Player not found")
        self.assertEqual(str(GameCoinError("NETWORK_ERROR", 0, "boom")), "NETWORK_ERROR: boom")
        self.assertEqual(
            repr(error),
            "GameCoinError(code='NOT_FOUND', status=404, message='Player not found', details={'x': 1}, retry_after=3)",
        )

    def test_the_error_survives_pickling(self):
        error = GameCoinError("INSUFFICIENT_FUNDS", 409, "Insufficient funds", {"required": 5, "available": 1}, None)
        clone = pickle.loads(pickle.dumps(error))
        self.assertEqual((clone.code, clone.status, clone.message, clone.details, clone.retry_after), (error.code, 409, "Insufficient funds", {"required": 5, "available": 1}, None))

    def test_every_documented_code_has_a_constant(self):
        documented = {
            "VALIDATION_FAILED", "IDEMPOTENCY_KEY_REQUIRED", "UNAUTHENTICATED", "PAYMENT_FAILED", "FORBIDDEN",
            "PLAYER_BLOCKED", "ENV_NOT_ENABLED", "NOT_FOUND", "INSUFFICIENT_FUNDS", "LIMIT_REACHED",
            "IDEMPOTENCY_CONFLICT", "CONFLICT", "RATE_LIMITED", "INTERNAL", "READ_ONLY",
            "PACK_NOT_AVAILABLE", "PACK_SOLD_OUT",
            "NETWORK_ERROR", "TIMEOUT", "INVALID_RESPONSE",
        }
        defined = {name: value for name, value in vars(ErrorCode).items() if name.isupper()}
        self.assertEqual(set(defined), documented)
        for name, value in defined.items():
            self.assertEqual(name, value)


class LedgerIterationTests(unittest.TestCase):
    def pages(self):
        def entries(*ids):
            return [entry_json(id=i) for i in ids]

        return [
            ok({"entries": entries("e1", "e2"), "nextCursor": "cursor-1"}),
            ok({"entries": entries("e3", "e4"), "nextCursor": "cursor-2"}),
            ok({"entries": entries("e5"), "nextCursor": None}),
        ]

    def test_iterates_across_pages(self):
        client, transport, _ = make_client(*self.pages())
        ids = [entry.id for entry in client.ledger.iterate("ext:u", currency="gems", limit=2)]
        self.assertEqual(ids, ["e1", "e2", "e3", "e4", "e5"])
        urls = [request.url.split("/ledger")[1] for request in transport.requests]
        self.assertEqual(urls, ["?currency=gems&limit=2", "?currency=gems&limit=2&cursor=cursor-1", "?currency=gems&limit=2&cursor=cursor-2"])

    def test_is_lazy(self):
        client, transport, _ = make_client(*self.pages())
        iterator = client.ledger.iterate("ext:u")
        self.assertEqual(transport.requests, [], "nothing is fetched before the loop starts")
        self.assertEqual(next(iterator).id, "e1")
        self.assertEqual(next(iterator).id, "e2")
        self.assertEqual(len(transport.requests), 1, "the second page waits until the loop needs it")
        self.assertEqual(next(iterator).id, "e3")
        self.assertEqual(len(transport.requests), 2)

    def test_empty_ledger(self):
        client, transport, _ = make_client(ok({"entries": [], "nextCursor": None}))
        self.assertEqual(list(client.ledger.iterate("ext:u")), [])
        self.assertEqual(len(transport.requests), 1)

    def test_an_error_on_a_later_page_surfaces_in_the_loop(self):
        client, _, _ = make_client(self.pages()[0], api_error(404, "NOT_FOUND", "gone"))
        seen = []
        with self.assertRaises(GameCoinError) as caught:
            for entry in client.ledger.iterate("ext:u"):
                seen.append(entry.id)
        self.assertEqual(seen, ["e1", "e2"])
        self.assertEqual(caught.exception.code, ErrorCode.NOT_FOUND)

    def test_without_a_page_size_the_api_default_applies(self):
        client, transport, _ = make_client(ok({"entries": [], "nextCursor": None}))
        list(client.ledger.iterate("ext:u"))
        self.assertTrue(transport.last.url.endswith("/ledger"))


class SecretKeyHygieneTests(unittest.TestCase):
    def collect_texts(self, error):
        return [
            str(error),
            repr(error),
            error.message,
            repr(error.details),
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        ]

    def assert_clean(self, error):
        for text in self.collect_texts(error):
            self.assertNotIn(KEY, text)

    def test_api_errors(self):
        for response in (
            api_error(401, "UNAUTHENTICATED", "Invalid API key"),
            api_error(400, "VALIDATION_FAILED", "Invalid", {"fieldErrors": {"amount": ["x"]}}),
            HttpResponse(502, {}, b"<html>bad gateway</html>"),
        ):
            client, _, _ = make_client(response, max_retries=0)
            with self.assertRaises(GameCoinError) as caught:
                client.catalog.get()
            self.assert_clean(caught.exception)

    def test_transport_errors(self):
        for failure in (ConnectionError("refused"), TimeoutError("slow"), OSError("")):
            client, _, _ = make_client(failure, max_retries=0)
            with self.assertRaises(GameCoinError) as caught:
                client.catalog.get()
            self.assert_clean(caught.exception)

    def test_a_key_echoed_by_a_proxy_or_the_api_is_masked(self):
        client, _, _ = make_client(ConnectionError(f"proxy refused Bearer {KEY}"), max_retries=0)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertNotIn(KEY, caught.exception.message)
        self.assertIn("gc_sk_***", caught.exception.message)

        client, _, _ = make_client(api_error(401, "UNAUTHENTICATED", f"Key {KEY} is revoked"), max_retries=0)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertNotIn(KEY, caught.exception.message)

    def test_argument_errors(self):
        client, _, _ = make_client()
        with self.assertRaises(ValueError) as caught:
            client.wallet.grant("", currency="gems", amount=1)
        self.assertNotIn(KEY, str(caught.exception))

    def test_the_client_and_its_resources_do_not_print_the_key(self):
        client, _, _ = make_client(ok(player_json()))
        for obj in (client, client.wallet, client.ledger, client.orders):
            self.assertNotIn(KEY, repr(obj))
            self.assertNotIn(KEY, str(obj))


if __name__ == "__main__":
    unittest.main()
