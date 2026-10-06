"""Webhook signature verification: signature, tolerance, header, body, event types, no leak of secrets."""

import hashlib
import hmac
import json
import pickle
import unittest
from datetime import datetime, timezone

import gamecoin
from gamecoin import (
    Order,
    WalletCredited,
    WebhookEvent,
    WebhookEventType,
    WebhookSignatureError,
    WebhookSignatureReason,
    WebhookTest,
    webhooks,
)
from tests.support import ORDER_ID, PLAYER_ID, order_json

SECRET = "whsec_gc_" + "s3cr3t" * 7
NOW = 1_760_000_000  # the clock of the tests, in Unix seconds


def sign(body, secret=SECRET, timestamp=NOW):
    raw = body.encode("utf-8") if isinstance(body, str) else body
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + raw, hashlib.sha256).hexdigest()


def header_for(body, secret=SECRET, timestamp=NOW):
    return f"t={timestamp},v1={sign(body, secret, timestamp)}"


def envelope(event_type, data, **over):
    payload = {
        "id": "evt_6ac419fbdbe02af6c99c3d47_ful",
        "type": event_type,
        "createdAt": "2026-10-06T10:00:02.000Z",
        "env": "test",
        "data": data,
    }
    payload.update(over)
    return payload


def body_of(payload):
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def construct(body, header=None, **options):
    options.setdefault("now", NOW)
    return webhooks.construct_event(body, header_for(body) if header is None else header, SECRET, **options)


class ReasonMixin:
    def assert_reason(self, reason, *args, **kwargs):
        with self.assertRaises(WebhookSignatureError) as caught:
            webhooks.construct_event(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception


class SignatureTests(ReasonMixin, unittest.TestCase):
    def test_known_vector(self):
        # Computed with `openssl dgst -sha256 -hmac`, independently of the code above.
        body = '{"a":1}'
        self.assertEqual(sign(body, "whsec_gc_testsecret", 1760000000), "6d0fd60b8366f7a19b4c5fcaf5e166551490dd446c0ff52e0bc2674d2597fb33")

    def test_valid_signature_returns_the_event(self):
        body = body_of(envelope("webhook.test", {"message": "hello"}))
        event = construct(body)
        self.assertIsInstance(event, WebhookEvent)
        self.assertEqual(event.id, "evt_6ac419fbdbe02af6c99c3d47_ful")
        self.assertEqual(event.type, "webhook.test")
        self.assertEqual(event.env, "test")
        self.assertEqual(event.created_at, datetime(2026, 10, 6, 10, 0, 2, tzinfo=timezone.utc))

    def test_text_and_bytes_bodies(self):
        text = body_of(envelope("webhook.test", {"message": "héllo ☃"}))
        header = header_for(text)
        for raw in (text, text.encode("utf-8"), bytearray(text.encode("utf-8")), memoryview(text.encode("utf-8"))):
            with self.subTest(type=type(raw).__name__):
                self.assertEqual(construct(raw, header).data.message, "héllo ☃")

    def test_several_v1_one_right(self):
        body = body_of(envelope("webhook.test", {"message": "m"}))
        good, bad = sign(body), "0" * 64
        for header in (f"t={NOW},v1={bad},v1={good}", f"t={NOW},v1={good},v1={bad}", f"v1={bad}, t={NOW} ,v1={good}"):
            with self.subTest(header=header):
                self.assertEqual(construct(body, header).type, "webhook.test")

    def test_several_v1_all_wrong(self):
        body = body_of(envelope("webhook.test", {"message": "m"}))
        self.assert_reason(
            WebhookSignatureReason.NO_MATCHING_SIGNATURE, body, f"t={NOW},v1={'0' * 64},v1={'f' * 64}", SECRET, now=NOW
        )

    def test_unknown_header_keys_are_ignored(self):
        body = body_of(envelope("webhook.test", {"message": "m"}))
        self.assertEqual(construct(body, f"t={NOW},v0=abc,v2=zzz,v1={sign(body)},future=1,junk").type, "webhook.test")

    def test_wrong_secret(self):
        body = body_of(envelope("webhook.test", {"message": "m"}))
        header = header_for(body, secret="whsec_gc_other")
        self.assert_reason(WebhookSignatureReason.NO_MATCHING_SIGNATURE, body, header, SECRET, now=NOW)

    def test_secret_is_trimmed(self):
        body = body_of(envelope("webhook.test", {"message": "m"}))
        event = webhooks.construct_event(body, header_for(body), f"  {SECRET}\n", now=NOW)
        self.assertEqual(event.type, "webhook.test")

    def test_altered_body(self):
        body = body_of(envelope("webhook.test", {"message": "hello"}))
        header = header_for(body)
        for altered in (body.replace("hello", "hellp"), body + " ", " " + body, json.dumps(json.loads(body))):
            with self.subTest(body=altered[:20]):
                self.assert_reason(WebhookSignatureReason.NO_MATCHING_SIGNATURE, altered, header, SECRET, now=NOW)

    def test_signature_of_another_timestamp(self):
        body = body_of(envelope("webhook.test", {"message": "m"}))
        header = f"t={NOW + 1},v1={sign(body)}"  # the timestamp is part of what is signed
        self.assert_reason(WebhookSignatureReason.NO_MATCHING_SIGNATURE, body, header, SECRET, now=NOW)

    def test_wrong_signature_shapes_never_crash(self):
        body = body_of(envelope("webhook.test", {"message": "m"}))
        for candidate in ("zz", "é" * 64, sign(body).upper(), sign(body)[:-1], sign(body) + "0", "0x" + sign(body)):
            with self.subTest(candidate=candidate[:10]):
                self.assert_reason(WebhookSignatureReason.NO_MATCHING_SIGNATURE, body, f"t={NOW},v1={candidate}", SECRET, now=NOW)


class TimestampTests(ReasonMixin, unittest.TestCase):
    BODY = body_of(envelope("webhook.test", {"message": "m"}))

    def test_default_tolerance_is_five_minutes(self):
        self.assertEqual(webhooks.DEFAULT_TOLERANCE_SECONDS, 300)
        header = header_for(self.BODY)
        self.assertEqual(construct(self.BODY, header, now=NOW + 300).type, "webhook.test")
        self.assertEqual(construct(self.BODY, header, now=NOW - 300).type, "webhook.test")
        self.assert_reason(WebhookSignatureReason.TIMESTAMP_OUT_OF_TOLERANCE, self.BODY, header, SECRET, now=NOW + 301)

    def test_future_timestamp(self):
        header = header_for(self.BODY, timestamp=NOW + 301)
        self.assert_reason(WebhookSignatureReason.TIMESTAMP_OUT_OF_TOLERANCE, self.BODY, header, SECRET, now=NOW)
        self.assertEqual(construct(self.BODY, header_for(self.BODY, timestamp=NOW + 299)).type, "webhook.test")

    def test_custom_tolerance(self):
        header = header_for(self.BODY, timestamp=NOW - 3600)
        self.assert_reason(WebhookSignatureReason.TIMESTAMP_OUT_OF_TOLERANCE, self.BODY, header, SECRET, now=NOW)
        self.assertEqual(construct(self.BODY, header, tolerance_seconds=3600).type, "webhook.test")
        self.assert_reason(
            WebhookSignatureReason.TIMESTAMP_OUT_OF_TOLERANCE, self.BODY, header, SECRET, now=NOW, tolerance_seconds=3599
        )

    def test_tolerance_zero_disables_the_check(self):
        for timestamp in (1, NOW - 10**6, NOW + 10**6):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(construct(self.BODY, header_for(self.BODY, timestamp=timestamp), tolerance_seconds=0).type, "webhook.test")

    def test_signature_is_checked_before_the_timestamp(self):
        header = f"t={NOW - 10**6},v1={'0' * 64}"
        self.assert_reason(WebhookSignatureReason.NO_MATCHING_SIGNATURE, self.BODY, header, SECRET, now=NOW)

    def test_clock_forms(self):
        header = header_for(self.BODY)
        aware = datetime.fromtimestamp(NOW, tz=timezone.utc)
        for clock in (NOW, float(NOW), aware, lambda: NOW, lambda: aware):
            with self.subTest(clock=clock):
                self.assertEqual(construct(self.BODY, header, now=clock).type, "webhook.test")
        with self.assertRaises(ValueError):
            construct(self.BODY, header, now=datetime(2025, 10, 9))  # naive
        with self.assertRaises(TypeError):
            construct(self.BODY, header, now="now")

    def test_system_clock_by_default(self):
        import time

        body = self.BODY
        header = header_for(body, timestamp=int(time.time()))
        self.assertEqual(webhooks.construct_event(body, header, SECRET).type, "webhook.test")
        self.assert_reason(
            WebhookSignatureReason.TIMESTAMP_OUT_OF_TOLERANCE, body, header_for(body, timestamp=int(time.time()) - 3600), SECRET
        )


class HeaderTests(ReasonMixin, unittest.TestCase):
    BODY = body_of(envelope("webhook.test", {"message": "m"}))

    def test_missing_header(self):
        for header in (None, "", "   ", "\n"):
            with self.subTest(header=header):
                self.assert_reason(WebhookSignatureReason.MISSING_HEADER, self.BODY, header, SECRET, now=NOW)

    def test_malformed_header(self):
        good = sign(self.BODY)
        cases = [
            "garbage",
            "t=",
            f"t=,v1={good}",
            f"t=abc,v1={good}",
            f"t=-5,v1={good}",
            f"t=1.5,v1={good}",
            f"t=٢٠,v1={good}",  # Arabic-Indic digits
            f"t={NOW}",
            f"t={NOW},v1=",
            f"t={NOW},v2={good}",
            f"v1={good}",
            f"t={NOW},t={NOW + 1},v1={good}",  # two different timestamps
            ",,,",
            "v1",
        ]
        for header in cases:
            with self.subTest(header=header):
                self.assert_reason(WebhookSignatureReason.MALFORMED_HEADER, self.BODY, header, SECRET, now=NOW)

    def test_same_timestamp_twice_is_fine(self):
        self.assertEqual(construct(self.BODY, f"t={NOW},t={NOW},v1={sign(self.BODY)}").type, "webhook.test")

    def test_header_name_constant(self):
        self.assertEqual(webhooks.SIGNATURE_HEADER, "GameCoin-Signature")


class PayloadTests(ReasonMixin, unittest.TestCase):
    def assert_invalid_payload(self, body):
        # The signature is right: only the content is refused.
        self.assert_reason(WebhookSignatureReason.INVALID_PAYLOAD, body, header_for(body), SECRET, now=NOW)

    def test_not_json(self):
        for body in ("", "not json", "{", "{'a': 1}", "<html></html>", b"\xff\xfe\x00"):
            with self.subTest(body=body):
                self.assert_invalid_payload(body)

    def test_not_an_envelope(self):
        base = envelope("webhook.test", {"message": "m"})
        for body in ("null", "[]", "42", '"text"', "{}", '{"type": "webhook.test"}'):
            with self.subTest(body=body):
                self.assert_invalid_payload(body)
        for missing in ("id", "type", "createdAt", "env", "data"):
            with self.subTest(missing=missing):
                payload = {key: value for key, value in base.items() if key != missing}
                self.assert_invalid_payload(body_of(payload))
        for field, bad in (("id", 7), ("type", None), ("env", 1), ("createdAt", "yesterday"), ("createdAt", 1760000000)):
            with self.subTest(field=field, value=bad):
                self.assert_invalid_payload(body_of(dict(base, **{field: bad})))

    def test_known_type_with_broken_data(self):
        for event_type, data in (
            ("order.fulfilled", None),
            ("order.fulfilled", []),
            ("order.fulfilled", {"id": ORDER_ID}),
            ("wallet.credited", {"playerId": PLAYER_ID}),
            ("wallet.credited", {"playerId": PLAYER_ID, "orderId": ORDER_ID, "credits": "none"}),
            ("wallet.credited", {"playerId": PLAYER_ID, "orderId": ORDER_ID, "credits": [{"currency": "gems", "paid": "5", "bonus": 0}]}),
            ("wallet.credited", {"playerId": PLAYER_ID, "orderId": ORDER_ID, "credits": [{"currency": "gems", "paid": True, "bonus": 0}]}),
            ("webhook.test", "hello"),
            ("webhook.test", {"message": 5}),
        ):
            with self.subTest(type=event_type, data=data):
                self.assert_invalid_payload(body_of(envelope(event_type, data)))

    def test_every_event_type_is_typed(self):
        self.assertEqual(
            (
                WebhookEventType.ORDER_FULFILLED,
                WebhookEventType.ORDER_REFUNDED,
                WebhookEventType.ORDER_CHARGEBACK,
                WebhookEventType.WALLET_CREDITED,
                WebhookEventType.WEBHOOK_TEST,
            ),
            ("order.fulfilled", "order.refunded", "order.chargeback", "wallet.credited", "webhook.test"),
        )
        for event_type, status in (("order.fulfilled", "fulfilled"), ("order.refunded", "refunded"), ("order.chargeback", "chargeback")):
            with self.subTest(type=event_type):
                order = order_json(status=status, paidAt="2026-10-06T10:00:01.000Z", fulfilledAt="2026-10-06T10:00:01.000Z")
                event = construct(body_of(envelope(event_type, order)))
                self.assertEqual(event.type, event_type)
                self.assertIsInstance(event.data, Order)
                self.assertEqual(event.data.id, ORDER_ID)
                self.assertEqual(event.data.status, status)
                self.assertEqual(event.data.pack.sku, "gems-500")
                self.assertEqual(event.data.paid_at, datetime(2026, 10, 6, 10, 0, 1, tzinfo=timezone.utc))
                self.assertIsNone(event.data.refunded_at)

    def test_wallet_credited(self):
        data = {
            "playerId": PLAYER_ID,
            "orderId": ORDER_ID,
            "credits": [{"currency": "gems", "paid": 500, "bonus": 50}, {"currency": "gold", "paid": 0, "bonus": 10}],
        }
        event = construct(body_of(envelope("wallet.credited", data, id="evt_x_cre")))
        self.assertEqual(event.type, WebhookEventType.WALLET_CREDITED)
        self.assertIsInstance(event.data, WalletCredited)
        self.assertEqual((event.data.player_id, event.data.order_id), (PLAYER_ID, ORDER_ID))
        self.assertEqual([(c.currency, c.paid, c.bonus) for c in event.data.credits], [("gems", 500, 50), ("gold", 0, 10)])
        self.assertIsInstance(event.data.credits, tuple)

    def test_webhook_test(self):
        event = construct(body_of(envelope("webhook.test", {"message": "This is a test event"})))
        self.assertIsInstance(event.data, WebhookTest)
        self.assertEqual(event.data.message, "This is a test event")

    def test_unknown_type_is_passed_through(self):
        for data in ({"anything": [1, 2, {"deep": None}]}, {}, [1, 2], "text", None, 7):
            with self.subTest(data=data):
                event = construct(body_of(envelope("coupon.redeemed", data)))
                self.assertEqual(event.type, "coupon.redeemed")
                self.assertEqual(event.data, data)

    def test_unknown_fields_are_ignored(self):
        payload = envelope("webhook.test", {"message": "m", "extra": 1}, version=2)
        self.assertEqual(construct(body_of(payload)).data.message, "m")

    def test_live_environment(self):
        self.assertEqual(construct(body_of(envelope("webhook.test", {"message": "m"}, env="live"))).env, "live")

    def test_events_are_immutable(self):
        event = construct(body_of(envelope("webhook.test", {"message": "m"})))
        with self.assertRaises(AttributeError):
            event.type = "x"


class ArgumentTests(unittest.TestCase):
    BODY = body_of(envelope("webhook.test", {"message": "m"}))

    def test_parsed_body_is_refused(self):
        for parsed in (json.loads(self.BODY), [1], None, 42, object()):
            with self.subTest(body=type(parsed).__name__):
                with self.assertRaises(TypeError) as caught:
                    webhooks.construct_event(parsed, header_for(self.BODY), SECRET, now=NOW)
                self.assertIn("raw", str(caught.exception))

    def test_secret(self):
        header = header_for(self.BODY)
        for bad in ("", "   ", "\n"):
            with self.subTest(secret=bad):
                with self.assertRaises(ValueError):
                    webhooks.construct_event(self.BODY, header, bad, now=NOW)
        for bad in (None, 42, b"whsec_gc_x"):
            with self.subTest(secret=bad):
                with self.assertRaises(TypeError):
                    webhooks.construct_event(self.BODY, header, bad, now=NOW)

    def test_header_and_tolerance_types(self):
        with self.assertRaises(TypeError):
            webhooks.construct_event(self.BODY, 42, SECRET, now=NOW)
        with self.assertRaises(TypeError):
            webhooks.construct_event(self.BODY, header_for(self.BODY), SECRET, now=NOW, tolerance_seconds="300")
        with self.assertRaises(TypeError):
            webhooks.construct_event(self.BODY, header_for(self.BODY), SECRET, now=NOW, tolerance_seconds=True)
        with self.assertRaises(ValueError):
            webhooks.construct_event(self.BODY, header_for(self.BODY), SECRET, now=NOW, tolerance_seconds=-1)

    def test_options_are_keyword_only(self):
        with self.assertRaises(TypeError):
            webhooks.construct_event(self.BODY, header_for(self.BODY), SECRET, 300)


class ErrorTests(unittest.TestCase):
    def test_error_is_not_an_api_error(self):
        self.assertFalse(issubclass(WebhookSignatureError, gamecoin.GameCoinError))
        self.assertTrue(issubclass(WebhookSignatureError, Exception))

    def test_reason_constants(self):
        self.assertEqual(
            (
                WebhookSignatureReason.MISSING_HEADER,
                WebhookSignatureReason.MALFORMED_HEADER,
                WebhookSignatureReason.NO_MATCHING_SIGNATURE,
                WebhookSignatureReason.TIMESTAMP_OUT_OF_TOLERANCE,
                WebhookSignatureReason.INVALID_PAYLOAD,
            ),
            ("missing_header", "malformed_header", "no_matching_signature", "timestamp_out_of_tolerance", "invalid_payload"),
        )

    def test_error_survives_pickling(self):
        error = WebhookSignatureError("no_matching_signature", "no signature matches")
        copy = pickle.loads(pickle.dumps(error))
        self.assertEqual((copy.reason, copy.message), (error.reason, error.message))

    def test_secret_body_and_signature_never_appear_in_errors(self):
        body = body_of(envelope("order.fulfilled", order_json(), id="evt_distinctive_body_marker"))
        good = sign(body)
        scenarios = [
            (body, None),
            (body, "garbage-header"),
            (body, f"t={NOW},v1={'0' * 64}"),
            (body + "x", f"t={NOW},v1={good}"),
            (body, f"t={NOW - 10**6},v1={good}"),
            ("distinctive_body_marker{", header_for("distinctive_body_marker{")),
            (b"\xff distinctive_body_marker", header_for(b"\xff distinctive_body_marker")),
        ]
        for scenario_body, header in scenarios:
            with self.subTest(header=header):
                with self.assertRaises(WebhookSignatureError) as caught:
                    webhooks.construct_event(scenario_body, header, SECRET, now=NOW)
                error = caught.exception
                shown = " ".join([str(error), repr(error), error.message, error.reason, repr(error.args)])
                for secret_text in (SECRET, "s3cr3t", good, "distinctive_body_marker", "0" * 64):
                    self.assertNotIn(secret_text, shown)
                # Nothing quoting the body is chained to the error either.
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_argument_errors_do_not_contain_the_secret(self):
        with self.assertRaises(TypeError) as caught:
            webhooks.construct_event({"id": SECRET}, "t=1,v1=a", SECRET)
        self.assertNotIn(SECRET, str(caught.exception))


class PublicSurfaceTests(unittest.TestCase):
    def test_exports(self):
        self.assertIs(gamecoin.construct_event, webhooks.construct_event)
        self.assertIs(gamecoin.WebhookEvent, webhooks.WebhookEvent)
        self.assertIn("webhooks", gamecoin.__all__)


if __name__ == "__main__":
    unittest.main()
