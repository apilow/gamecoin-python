import re
import unittest

import gamecoin
from gamecoin import GameCoin
from tests.support import API, BASE, KEY, catalog_json, make_client, movement_json, ok


class ConstructorTests(unittest.TestCase):
    def test_accepts_test_and_live_secret_keys(self):
        self.assertEqual(GameCoin("gc_sk_test_" + "a" * 43).environment, "test")
        self.assertEqual(GameCoin("gc_sk_live_" + "A1_-" * 10).environment, "live")

    def test_rejects_a_publishable_key_and_points_to_the_browser_sdk(self):
        publishable = "gc_pk_test_" + "p" * 43
        with self.assertRaises(ValueError) as caught:
            GameCoin(publishable)
        message = str(caught.exception)
        self.assertIn("publishable", message)
        self.assertIn("browser SDK", message)
        self.assertNotIn(publishable, message)

    def test_rejects_a_player_token(self):
        token = "gc_pt_" + "t" * 40
        with self.assertRaises(ValueError) as caught:
            GameCoin(token)
        self.assertIn("player token", str(caught.exception))
        self.assertNotIn(token, str(caught.exception))

    def test_rejects_anything_else_without_echoing_it(self):
        for bad in ("", "   ", "gc_sk_test_", "gc_sk_prod_" + "a" * 43, "sk_test_abc", "gc_sk_test_has space"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as caught:
                    GameCoin(bad)
                if len(bad.strip()) > len("gc_sk_test_"):  # shorter ones are just the prefix named in the hint
                    self.assertNotIn(bad.strip(), str(caught.exception))

    def test_the_key_is_trimmed(self):
        # A key read from a file or a variable often carries a newline: it is trimmed, and the trimmed key is the one sent.
        for padded in ("  " + KEY + "\n", KEY + "\r\n", "\t" + KEY):
            with self.subTest(padded=padded):
                _, transport, _ = make_client(ok(catalog_json()))
                client = GameCoin(padded, base_url=BASE, transport=transport)
                client.catalog.get()
                self.assertEqual(transport.last.headers["Authorization"], "Bearer " + KEY)
                self.assertEqual(client.environment, "test")
        with self.assertRaises(ValueError):
            GameCoin("gc_pk_test_" + "p" * 43 + "\n")

    def test_rejects_a_non_string_key(self):
        for bad in (None, 42, b"gc_sk_test_" + b"a" * 43):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    GameCoin(bad)  # type: ignore[arg-type]

    def test_options_are_validated(self):
        for kwargs in ({"timeout": 0}, {"timeout": -1}, {"timeout": "30"}, {"max_retries": -1}, {"max_retries": 11}, {"max_retries": 1.5}, {"max_retries": True}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    GameCoin(KEY, **kwargs)
        for base_url in (
            "", "   ", "gamecoin.apilow.com", "localhost:3400", "ftp://x", "http://", "http://a b", None,
            "https://x.test/?a=1", "https://x.test/#frag", "https://user:pw@x.test",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    GameCoin(KEY, base_url=base_url)  # type: ignore[arg-type]

    def test_base_url_is_normalised(self):
        self.assertEqual(GameCoin(KEY).base_url, "https://gamecoin.apilow.com")
        self.assertEqual(GameCoin(KEY, base_url="http://localhost:3400/").base_url, "http://localhost:3400")
        self.assertEqual(GameCoin(KEY, base_url="HTTPS://Gamecoin.Apilow.com//").base_url, "https://gamecoin.apilow.com")
        # A path prefix is kept (a reverse proxy mounting the API under /gamecoin); the SDK adds /api/v1 itself.
        self.assertEqual(GameCoin(KEY, base_url="https://proxy.test/gamecoin/").base_url, "https://proxy.test/gamecoin")
        self.assertEqual(GameCoin(KEY, base_url="  http://localhost:3400  ").base_url, "http://localhost:3400")

    def test_the_api_prefix_is_added_after_the_path_prefix(self):
        _, transport, _ = make_client(ok(catalog_json()))
        client = GameCoin(KEY, base_url="https://proxy.test/gamecoin/", transport=transport)
        client.catalog.get()
        self.assertEqual(transport.last.url, "https://proxy.test/gamecoin/api/v1/catalog")

    def test_resources_are_exposed(self):
        client = GameCoin(KEY)
        for name in ("catalog", "players", "wallet", "ledger", "inventory", "purchases", "checkouts", "orders"):
            self.assertTrue(hasattr(client, name), name)


class RequestHeaderTests(unittest.TestCase):
    def test_every_request_is_authenticated_and_identified(self):
        client, transport, _ = make_client(ok(catalog_json()))
        client.catalog.get()
        request = transport.last
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url, API + "/catalog")
        self.assertEqual(request.headers["Authorization"], "Bearer " + KEY)
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertNotIn("Content-Type", request.headers)  # no body, no content type
        self.assertNotIn("Idempotency-Key", request.headers)

    def test_user_agent_names_sdk_and_runtime(self):
        client, transport, _ = make_client(ok(catalog_json()))
        client.catalog.get()
        user_agent = transport.last.headers["User-Agent"]
        self.assertEqual(user_agent, f"gamecoin-python/{gamecoin.__version__} (python/{__import__('platform').python_version()})")
        self.assertRegex(user_agent, r"^gamecoin-python/0\.2\.0 \(python/3\.\d+\.\d+\)$")

    def test_json_bodies_carry_a_content_type(self):
        client, transport, _ = make_client(ok({"token": "gc_pt_x", "expiresAt": "2026-10-05T12:00:00Z"}), ok(movement_json()))
        client.players.create_token("ext:u")
        self.assertNotIn("Content-Type", transport.last.headers)  # token creation has no body
        client.wallet.grant("ext:u", currency="gems", amount=1)
        self.assertEqual(transport.last.headers["Content-Type"], "application/json")

    def test_timeout_is_handed_to_the_transport(self):
        client, transport, _ = make_client(ok(catalog_json()), timeout=7)
        client.catalog.get()
        self.assertEqual(transport.last.timeout, 7.0)


class RepresentationTests(unittest.TestCase):
    def test_client_representation_has_no_key(self):
        client = GameCoin(KEY, base_url=BASE)
        for text in (repr(client), str(client), f"{client!r}", repr([client])):
            self.assertNotIn(KEY, text)
            self.assertNotIn(KEY[len("gc_sk_test_"):][:8], text)  # no part of the secret itself
        # The prefix only: enough to tell test from live, nothing to steal.
        self.assertEqual(
            repr(client), "GameCoin(environment='test', base_url='https://gamecoin.test', secret_key='gc_sk_test_\u2026')"
        )

    def test_request_representation_has_no_key(self):
        client, transport, _ = make_client(ok(catalog_json()))
        client.catalog.get()
        self.assertNotIn(KEY, repr(transport.last))
        self.assertNotIn("Bearer", repr(transport.last))

    def test_player_token_representation_has_no_token(self):
        token = gamecoin.PlayerToken.from_api({"token": "gc_pt_" + "z" * 30, "expiresAt": "2026-10-05T12:00:00Z"})
        self.assertNotIn("gc_pt_", repr(token))


class PublicSurfaceTests(unittest.TestCase):
    def test_exports(self):
        for name in gamecoin.__all__:
            self.assertTrue(hasattr(gamecoin, name), name)
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", gamecoin.__version__))


if __name__ == "__main__":
    unittest.main()
