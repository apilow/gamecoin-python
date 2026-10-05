"""Request engine: headers, serialisation, retries, error mapping. Private: use :class:`gamecoin.GameCoin`."""

from __future__ import annotations

import email.utils
import http.client
import json
import math
import platform
import random
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, TypeVar
from urllib.parse import urlencode

from ._version import __version__
from .errors import ErrorCode, GameCoinError
from .transport import HttpRequest, HttpResponse, Transport

T = TypeVar("T")

API_PREFIX = "/api/v1"
BASE_RETRY_DELAY = 0.5
MAX_RETRY_AFTER = 30.0
USER_AGENT = f"gamecoin-python/{__version__} (python/{platform.python_version()})"

# Retry policies. ``ALL``: network error, timeout, 5xx and 429. ``RATE_LIMIT_ONLY``: 429 alone, for the calls that
# cannot be replayed safely (a refund has no idempotency key).
RETRY_ALL = "all"
RETRY_RATE_LIMIT_ONLY = "rate_limit_only"


def backoff_delay(attempt: int, jitter: Callable[[], float] = random.random) -> float:
    """``0.5 s * 2**attempt * [0.75, 1.25]``: the jitter keeps clients that failed together from retrying together."""
    return BASE_RETRY_DELAY * (2.0 ** attempt) * (0.75 + 0.5 * jitter())


def retry_delay(attempt: int, retry_after: Optional[float] = None) -> Optional[float]:
    """Seconds to wait before retry number ``attempt + 1``; ``None`` means "do not retry".

    A ``Retry-After`` wins over the backoff, up to :data:`MAX_RETRY_AFTER` seconds included, whatever the status. A
    longer wait is not worth blocking the caller for: the error is raised and carries ``retry_after`` instead.
    """
    if retry_after is not None:
        return None if retry_after > MAX_RETRY_AFTER else max(0.0, float(retry_after))
    return backoff_delay(attempt)


def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """``Retry-After`` in seconds: a number of seconds, or an HTTP date (rounded up to the next second)."""
    if value is None:
        return None
    text = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        seconds = float(text)
        return int(seconds) if seconds.is_integer() else seconds
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now if now is not None else datetime.now(timezone.utc)
    return max(0, math.ceil((when - current).total_seconds()))


class Core:
    def __init__(
        self,
        secret_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
        transport: Transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._secret_key = secret_key
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport
        self._sleep = sleep

    # --- public to the resources ------------------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        parse: Callable[[Dict[str, Any], bool], T],
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotent: bool = False,
        idempotency_key: Optional[str] = None,
        retry: str = RETRY_ALL,
    ) -> T:
        """Sends one logical call (possibly several HTTP attempts) and parses the answer with ``parse(data, replayed)``."""
        headers = {
            "Authorization": f"Bearer {self._secret_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if idempotent:
            # One key per call, reused by every retry of that call: this is what makes a retry safe.
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        data: Optional[bytes] = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = HttpRequest(method, self._url(path, query), headers, data, self._timeout)

        attempt = 0
        while True:
            try:
                response = self._transport(request)
            except (OSError, http.client.HTTPException) as exc:
                if retry == RETRY_ALL and attempt < self._max_retries:
                    self._sleep(backoff_delay(attempt))
                    attempt += 1
                    continue
                raise self._transport_error(exc) from exc

            if 200 <= response.status < 300:
                return self._parse_success(response, parse)

            error = self._response_error(response)
            if self._retryable(response.status, retry) and attempt < self._max_retries:
                delay = retry_delay(attempt, error.retry_after)
                if delay is not None:
                    self._sleep(delay)
                    attempt += 1
                    continue
            raise error

    # --- internals --------------------------------------------------------------------------------------------

    def _url(self, path: str, query: Optional[Mapping[str, Any]]) -> str:
        url = f"{self._base_url}{API_PREFIX}{path}"
        pairs = [(k, str(v)) for k, v in (query or {}).items() if v is not None]
        return f"{url}?{urlencode(pairs)}" if pairs else url

    @staticmethod
    def _retryable(status: int, retry: str) -> bool:
        if status == 429:
            return True
        return retry == RETRY_ALL and 500 <= status <= 599

    def _redact(self, text: str) -> str:
        return text.replace(self._secret_key, "gc_sk_***") if self._secret_key in text else text

    def _transport_error(self, exc: BaseException) -> GameCoinError:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return GameCoinError(ErrorCode.TIMEOUT, 0, self._redact(str(exc) or f"request timed out after {self._timeout:g}s"))
        return GameCoinError(ErrorCode.NETWORK_ERROR, 0, self._redact(str(exc) or exc.__class__.__name__))

    def _response_error(self, response: HttpResponse) -> GameCoinError:
        retry_after = parse_retry_after(response.header("retry-after"))
        try:
            error = json.loads(response.body.decode("utf-8"))["error"]
            code, message = error["code"], error["message"]
            if not isinstance(code, str) or not isinstance(message, str):
                raise TypeError("code and message must be strings")
            details = error.get("details")
            return GameCoinError(
                code,
                response.status,
                self._redact(message),
                details if isinstance(details, dict) else None,
                retry_after,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            # Not the API's error envelope: a proxy page, an HTML 502, an empty body...
            return GameCoinError(
                ErrorCode.INVALID_RESPONSE,
                response.status,
                f"Unexpected response (HTTP {response.status}) that is not a GameCoin error",
                None,
                retry_after,
            )

    def _parse_success(self, response: HttpResponse, parse: Callable[[Dict[str, Any], bool], T]) -> T:
        replayed = (response.header("idempotent-replayed") or "").strip().lower() == "true"
        try:
            data = json.loads(response.body.decode("utf-8"))
            if not isinstance(data, dict):
                raise TypeError("expected a JSON object")
            return parse(data, replayed)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GameCoinError(
                ErrorCode.INVALID_RESPONSE,
                response.status,
                f"The response is not the expected JSON ({exc.__class__.__name__})",
            ) from exc
