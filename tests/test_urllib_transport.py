"""The default transport against a real local HTTP server (loopback only, no network)."""

import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import gamecoin
from gamecoin import ErrorCode, GameCoin, GameCoinError, HttpRequest, UrllibTransport
from tests.support import KEY, entry_json, balance_json


class Recorder:
    def __init__(self):
        self.calls = []
        self.script = []  # callables (handler) -> None, consumed in order; the last one repeats


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    recorder: Recorder

    def log_message(self, *args):  # keep the test output clean
        pass

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.recorder.calls.append(
            {"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
        )
        script = self.recorder.script
        step = script.pop(0) if len(script) > 1 else script[0]
        step(self)

    do_GET = do_POST = do_PUT = _serve

    def reply(self, status, payload=None, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class LocalServerTestCase(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        handler = type("BoundHandler", (Handler,), {"recorder": self.recorder})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.client = GameCoin(KEY, base_url=self.base_url, timeout=5, sleep=lambda seconds: None)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class UrllibTransportTests(LocalServerTestCase):
    def test_a_full_round_trip(self):
        payload = {"entry": entry_json(), "balance": balance_json()}
        self.recorder.script = [lambda h: h.reply(200, payload, {"Idempotent-Replayed": "true"})]
        movement = self.client.wallet.grant("ext:café /1", currency="gems", amount=100, reason="épée", idempotency_key="k-1")
        self.assertTrue(movement.replayed)
        self.assertEqual(movement.balance.total, 100)
        (call,) = self.recorder.calls
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], "/api/v1/players/ext%3Acaf%C3%A9%20%2F1/wallet/grant")
        self.assertEqual(call["headers"]["authorization"], "Bearer " + KEY)
        self.assertEqual(call["headers"]["idempotency-key"], "k-1")
        self.assertEqual(call["headers"]["content-type"], "application/json")
        self.assertEqual(call["headers"]["accept"], "application/json")
        self.assertTrue(call["headers"]["user-agent"].startswith("gamecoin-python/" + gamecoin.__version__))
        self.assertEqual(json.loads(call["body"].decode("utf-8")), {"currency": "gems", "amount": 100, "reason": "épée"})

    def test_an_error_status_is_an_error_not_an_exception_of_urllib(self):
        self.recorder.script = [lambda h: h.reply(409, {"error": {"code": "INSUFFICIENT_FUNDS", "message": "Insufficient funds", "details": {"required": 50, "available": 20}}})]
        with self.assertRaises(GameCoinError) as caught:
            self.client.wallet.spend("ext:u", currency="gems", amount=50)
        error = caught.exception
        self.assertEqual((error.code, error.status, error.details), ("INSUFFICIENT_FUNDS", 409, {"required": 50, "available": 20}))
        self.assertEqual(len(self.recorder.calls), 1)

    def test_retry_after_travels_through_http(self):
        ok_payload = {"entry": entry_json(), "balance": balance_json()}
        self.recorder.script = [
            lambda h: h.reply(429, {"error": {"code": "RATE_LIMITED", "message": "Slow down"}}, {"Retry-After": "2"}),
            lambda h: h.reply(200, ok_payload),
        ]
        sleeps = []
        client = GameCoin(KEY, base_url=self.base_url, sleep=sleeps.append)
        client.wallet.grant("ext:u", currency="gems", amount=1)
        self.assertEqual(sleeps, [2.0])
        keys = [call["headers"]["idempotency-key"] for call in self.recorder.calls]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])

    def test_5xx_is_retried_over_real_http(self):
        self.recorder.script = [lambda h: h.reply(503, raw=b"<html>unavailable</html>"), lambda h: h.reply(200, {"entries": [], "nextCursor": None})]
        self.assertEqual(self.client.ledger.list("ext:u").entries, ())
        self.assertEqual(len(self.recorder.calls), 2)

    def test_one_client_can_be_shared_between_threads(self):
        self.recorder.script = [lambda h: h.reply(200, {"entries": [], "nextCursor": None})]
        errors = []

        def work():
            try:
                for _ in range(5):
                    self.client.ledger.list("ext:u")
            except Exception as error:  # noqa: BLE001 - collected and asserted below
                errors.append(error)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.recorder.calls), 40)

    def test_redirects_are_not_followed(self):
        self.recorder.script = [lambda h: (h.send_response(302), h.send_header("Location", "http://127.0.0.1:1/stolen"), h.send_header("Content-Length", "0"), h.end_headers())]
        with self.assertRaises(GameCoinError) as caught:
            self.client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.INVALID_RESPONSE, 302))
        self.assertEqual(len(self.recorder.calls), 1)

    def test_a_slow_server_is_a_timeout(self):
        self.recorder.script = [lambda h: time.sleep(1.0)]
        client = GameCoin(KEY, base_url=self.base_url, timeout=0.2, max_retries=0)
        started = time.monotonic()
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.TIMEOUT, 0))
        self.assertLess(time.monotonic() - started, 0.9)

    def test_a_dropped_connection_is_a_network_error(self):
        def drop(handler):
            handler.close_connection = True
            handler.connection.shutdown(socket.SHUT_RDWR)

        self.recorder.script = [drop]
        client = GameCoin(KEY, base_url=self.base_url, max_retries=0)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.NETWORK_ERROR, 0))


class NoServerTests(unittest.TestCase):
    def test_connection_refused_is_a_network_error(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # The port is free again: nothing listens on it.
        client = GameCoin(KEY, base_url=f"http://127.0.0.1:{port}", max_retries=0)
        with self.assertRaises(GameCoinError) as caught:
            client.catalog.get()
        self.assertEqual((caught.exception.code, caught.exception.status), (ErrorCode.NETWORK_ERROR, 0))
        self.assertNotIn(KEY, str(caught.exception))

    def test_transport_contract_on_its_own(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(OSError):
            UrllibTransport()(HttpRequest("GET", f"http://127.0.0.1:{port}/x", {"Accept": "*/*"}, None, 1.0))


if __name__ == "__main__":
    unittest.main()
