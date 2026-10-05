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
