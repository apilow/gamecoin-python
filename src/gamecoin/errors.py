"""The single error type raised by the SDK, and the constants for its codes."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


class ErrorCode:
    """Values of :attr:`GameCoinError.code`.

    The first group comes from the API (``docs/tech/API.md`` § Errors); the last three are raised by the SDK itself
    when no usable response was received. ``code`` is a plain ``str``: a code this version does not know about is
    still delivered as is.
    """

    # Returned by the API.
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    FORBIDDEN = "FORBIDDEN"
    PLAYER_BLOCKED = "PLAYER_BLOCKED"
    ENV_NOT_ENABLED = "ENV_NOT_ENABLED"
    NOT_FOUND = "NOT_FOUND"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    LIMIT_REACHED = "LIMIT_REACHED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    READ_ONLY = "READ_ONLY"
    INTERNAL = "INTERNAL"

    # Raised by the SDK (``status`` is 0 for the first two).
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class GameCoinError(Exception):
    """Any failure of a GameCoin call: an error answered by the API, or a transport failure.

    Attributes:
        code: API code (``"INSUFFICIENT_FUNDS"``, ``"NOT_FOUND"``...), or ``"NETWORK_ERROR"`` / ``"TIMEOUT"``
            (``status`` 0), or ``"INVALID_RESPONSE"`` when the body is not the expected JSON. See :class:`ErrorCode`.
        status: HTTP status, 0 when no response was received.
        message: message of the API, or a short description.
        details: ``details`` object of the API (``required``, ``available``, ``fieldErrors``...), empty when absent.
        retry_after: seconds to wait, when the API sent ``Retry-After``.

    Arguments that are invalid on their face (an empty player, a negative amount...) never reach this class: they
    raise ``ValueError`` or ``TypeError`` before any request is made.
    """

    code: str
    status: int
    message: str
    details: Dict[str, Any]
    retry_after: Optional[float]

    def __init__(
        self,
        code: str,
        status: int,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        # All five values go to Exception.args, so that the error survives pickling (multiprocessing, task queues).
        super().__init__(code, status, message, details, retry_after)
        self.code = code
        self.status = status
        self.message = message
        self.details = dict(details) if details else {}
        self.retry_after = retry_after

    def __str__(self) -> str:
        where = f" (HTTP {self.status})" if self.status else ""
        return f"{self.code}{where}: {self.message}"

    def __repr__(self) -> str:
        return (
            f"GameCoinError(code={self.code!r}, status={self.status!r}, message={self.message!r}, "
            f"details={self.details!r}, retry_after={self.retry_after!r})"
        )


class WebhookSignatureReason:
    """Values of :attr:`WebhookSignatureError.reason`."""

    MISSING_HEADER = "missing_header"
    MALFORMED_HEADER = "malformed_header"
    NO_MATCHING_SIGNATURE = "no_matching_signature"
    TIMESTAMP_OUT_OF_TOLERANCE = "timestamp_out_of_tolerance"
    INVALID_PAYLOAD = "invalid_payload"


class WebhookSignatureError(Exception):
    """A webhook call that must not be trusted: see :func:`gamecoin.webhooks.construct_event`.

    It does **not** extend :class:`GameCoinError`, which describes a failed API call. Answer ``400`` and do nothing
    with the body.

    Attributes:
        reason: one of the :class:`WebhookSignatureReason` values: ``"missing_header"``, ``"malformed_header"``,
            ``"no_matching_signature"``, ``"timestamp_out_of_tolerance"`` or ``"invalid_payload"`` (the body is not
            JSON, or not an event envelope).
        message: a short description. It never contains the signing secret, the body or a signature.
    """

    reason: str
    message: str

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(reason, message)  # both values in args: the error survives pickling
        self.reason = reason
        self.message = message

    def __str__(self) -> str:
        return f"{self.reason}: {self.message}"

    def __repr__(self) -> str:
        return f"WebhookSignatureError(reason={self.reason!r}, message={self.message!r})"
