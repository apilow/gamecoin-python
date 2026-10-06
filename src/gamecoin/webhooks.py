"""Verification of the webhooks that GameCoin sends to your server.

    from gamecoin import webhooks

    event = webhooks.construct_event(raw_body, request.headers.get("GameCoin-Signature"), os.environ["GAMECOIN_WEBHOOK_SECRET"])
    if event.type == webhooks.WebhookEventType.ORDER_FULFILLED:
        print(event.data.player_id)

:func:`construct_event` is a pure function: it needs no client and makes no request. Pass it the **raw** request body
(``bytes`` or ``str``, exactly as received, never a parsed object): a body that was parsed and serialized again can
differ by one character, and then no signature matches.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from .errors import WebhookSignatureError, WebhookSignatureReason
from .models import Order, parse_datetime

SIGNATURE_HEADER = "GameCoin-Signature"
"""Name of the HTTP header that carries the signature."""

DEFAULT_TOLERANCE_SECONDS = 300
"""How far the timestamp of a call may be from your clock, in seconds."""


class WebhookEventType:
    """Values of :attr:`WebhookEvent.type`. A type this version does not know about is delivered as is."""

    ORDER_FULFILLED = "order.fulfilled"
    ORDER_REFUNDED = "order.refunded"
    ORDER_CHARGEBACK = "order.chargeback"
    WALLET_CREDITED = "wallet.credited"
    WEBHOOK_TEST = "webhook.test"


# --- Event data ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WalletCredit:
    """One currency credited by a delivery: ``paid`` coins bought with real money, ``bonus`` coins given on top."""

    currency: str
    paid: int
    bonus: int


@dataclass(frozen=True)
class WalletCredited:
    """``data`` of a ``wallet.credited`` event: which player received what, for which order."""

    player_id: str
    order_id: str
    credits: Tuple[WalletCredit, ...]


@dataclass(frozen=True)
class WebhookTest:
    """``data`` of a ``webhook.test`` event."""

    message: str


WebhookData = Union[Order, WalletCredited, WebhookTest, Dict[str, Any]]


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook event.

    ``data`` is an :class:`~gamecoin.models.Order` for ``order.*`` events, a :class:`WalletCredited` for
    ``wallet.credited``, a :class:`WebhookTest` for ``webhook.test``, and the decoded JSON (a ``dict``) for a type this
    version does not know about. Use ``id`` to process an event once: GameCoin delivers at least once.
    """

    id: str
    type: str
    created_at: datetime
    env: str
    data: WebhookData


def _wallet_credited(data: Mapping[str, Any]) -> WalletCredited:
    credits: List[WalletCredit] = []
    for item in data["credits"]:
        paid, bonus = item["paid"], item["bonus"]
        if isinstance(paid, bool) or not isinstance(paid, int) or isinstance(bonus, bool) or not isinstance(bonus, int):
            raise ValueError("credit amounts must be integers")
        credits.append(WalletCredit(currency=_text(item["currency"]), paid=paid, bonus=bonus))
    return WalletCredited(player_id=_text(data["playerId"]), order_id=_text(data["orderId"]), credits=tuple(credits))


def _webhook_test(data: Mapping[str, Any]) -> WebhookTest:
    message = data.get("message", "")
    return WebhookTest(message=_text(message))


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value


_DATA_PARSERS: Dict[str, Callable[[Mapping[str, Any]], WebhookData]] = {
    WebhookEventType.ORDER_FULFILLED: Order.from_api,
    WebhookEventType.ORDER_REFUNDED: Order.from_api,
    WebhookEventType.ORDER_CHARGEBACK: Order.from_api,
    WebhookEventType.WALLET_CREDITED: _wallet_credited,
    WebhookEventType.WEBHOOK_TEST: _webhook_test,
}


def _parse_event(payload: Any) -> Optional[WebhookEvent]:
    """The event of a decoded body, or ``None`` when it is not an event envelope."""
    try:
        if not isinstance(payload, dict):
            return None
        event_type = _text(payload["type"])
        data = payload["data"]
        parser = _DATA_PARSERS.get(event_type)
        if parser is not None:
            if not isinstance(data, dict):
                return None
            data = parser(data)
        return WebhookEvent(
            id=_text(payload["id"]),
            type=event_type,
            created_at=parse_datetime(payload["createdAt"]),
            env=_text(payload["env"]),
            data=data,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# --- Signature -------------------------------------------------------------------------------------------------

_UNIX_SECONDS = re.compile(r"[0-9]{1,15}")


def _parse_header(header: str) -> Optional[Tuple[int, List[str]]]:
    """``(t, [v1, ...])`` from ``t=<seconds>,v1=<hex>[,v1=<hex>...]``, or ``None`` when it is malformed.

    Keys other than ``t`` and ``v1`` are ignored, so that a later version can add some.
    """
    timestamps = set()
    signatures: List[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            if _UNIX_SECONDS.fullmatch(value) is None:
                return None
            timestamps.add(int(value))
        elif key == "v1" and value:
            signatures.append(value)
    if len(timestamps) != 1 or not signatures:
        return None
    return timestamps.pop(), signatures


def _clock(now: Any) -> float:
    if callable(now):
        now = now()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError("now must be a timezone-aware datetime")
        return now.timestamp()
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise TypeError("now must be a number of Unix seconds, a datetime, or a callable returning one")
    return float(now)


def construct_event(
    raw_body: Union[bytes, bytearray, memoryview, str],
    signature_header: Optional[str],
    secret: str,
    *,
    tolerance_seconds: Union[int, float] = DEFAULT_TOLERANCE_SECONDS,
    now: Union[None, int, float, datetime, Callable[[], Union[int, float, datetime]]] = None,
) -> WebhookEvent:
    """Checks the signature of a webhook call and returns its event. Raises :class:`WebhookSignatureError` otherwise.

    Args:
        raw_body: the request body exactly as received, as ``bytes`` or ``str``. Never a parsed object.
        signature_header: the value of the ``GameCoin-Signature`` header, ``t=<unix seconds>,v1=<hex>[,v1=<hex>...]``;
            ``None`` when the request has no such header.
        secret: the signing secret of the endpoint (``whsec_gc_...``). Whitespace around it is trimmed.
        tolerance_seconds: how far the timestamp may be from the clock, in the past or the future (default 300).
            ``0`` turns the timestamp check off.
        now: the clock, for tests: Unix seconds, an aware ``datetime``, or a callable returning one of them.
            Defaults to the system clock.

    The signature is the lowercase hexadecimal HMAC-SHA256 of ``"<t>." + raw_body`` keyed with the secret; every
    ``v1`` of the header is compared, in constant time. ``TypeError`` or ``ValueError`` (not a
    :class:`WebhookSignatureError`) is raised for arguments that are wrong on their face, such as a parsed body or an
    empty secret: that is a bug in your code, not a forged call.
    """
    if isinstance(raw_body, str):
        body = raw_body.encode("utf-8")
    elif isinstance(raw_body, (bytes, bytearray, memoryview)):
        body = bytes(raw_body)
    else:
        raise TypeError(
            f"raw_body must be the raw request body as bytes or str, not {type(raw_body).__name__}: "
            "pass it before any JSON parsing"
        )
    if signature_header is not None and not isinstance(signature_header, str):
        raise TypeError(f"signature_header must be a str or None, not {type(signature_header).__name__}")
    if not isinstance(secret, str):
        raise TypeError(f"secret must be a str, not {type(secret).__name__}")
    key = secret.strip()
    if not key:
        raise ValueError("secret must not be empty")
    if isinstance(tolerance_seconds, bool) or not isinstance(tolerance_seconds, (int, float)):
        raise TypeError(f"tolerance_seconds must be a number, not {type(tolerance_seconds).__name__}")
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds must not be negative")

    reason = WebhookSignatureReason
    if signature_header is None or not signature_header.strip():
        raise WebhookSignatureError(reason.MISSING_HEADER, f"the {SIGNATURE_HEADER} header is missing")
    parsed = _parse_header(signature_header)
    if parsed is None:
        raise WebhookSignatureError(
            reason.MALFORMED_HEADER, f"the {SIGNATURE_HEADER} header must look like t=<unix seconds>,v1=<hex signature>"
        )
    timestamp, candidates = parsed

    signed = str(timestamp).encode("ascii") + b"." + body
    expected = hmac.new(key.encode("utf-8"), signed, hashlib.sha256).hexdigest().encode("ascii")
    # Every candidate is compared, whatever the earlier ones gave: no early exit on the first match.
    matches = [hmac.compare_digest(expected, candidate.encode("utf-8")) for candidate in candidates]
    if not any(matches):
        raise WebhookSignatureError(reason.NO_MATCHING_SIGNATURE, "no signature matches the body and the signing secret")

    if tolerance_seconds > 0:
        clock = time.time() if now is None else _clock(now)
        if abs(clock - timestamp) > tolerance_seconds:
            raise WebhookSignatureError(
                reason.TIMESTAMP_OUT_OF_TOLERANCE,
                f"the timestamp of the call is more than {tolerance_seconds:g} seconds away from the clock",
            )

    # The decoding errors are left out of the raised error on purpose: they quote the body.
    try:
        payload: Any = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        payload = None
    event = _parse_event(payload)
    if event is None:
        raise WebhookSignatureError(reason.INVALID_PAYLOAD, "the body is not a GameCoin event")
    return event
