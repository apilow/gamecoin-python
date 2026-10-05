"""HTTP transport: a plain callable, so that tests and proxies can replace it.

A transport is any ``Callable[[HttpRequest], HttpResponse]``. It must

* return an :class:`HttpResponse` for **every** HTTP status, including 4xx and 5xx (it never raises for a status);
* raise :class:`TimeoutError` when the request timed out;
* raise an :class:`OSError` (for instance :class:`ConnectionError`) for any other network failure.

The SDK maps the last two to ``GameCoinError`` codes ``TIMEOUT`` and ``NETWORK_ERROR`` and may retry them.
"""

from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.message import Message
from typing import Callable, Dict, Mapping, Optional


@dataclass(frozen=True)
class HttpRequest:
    """One HTTP request, as handed to the transport. ``headers`` carries the secret key: it is kept out of ``repr``."""

    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: Optional[bytes] = None
    timeout: float = 30.0


@dataclass(frozen=True)
class HttpResponse:
    """One HTTP response. Header names are lower-cased on construction."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", {str(k).lower(): str(v) for k, v in dict(self.headers).items()})

    def header(self, name: str) -> Optional[str]:
        """Value of a header, whatever the case of ``name``."""
        return self.headers.get(name.lower())


Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The API never redirects. Following a redirect would replay the Authorization header elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _is_timeout(exc: BaseException) -> bool:
    # ``socket.timeout`` is a distinct ``OSError`` subclass before Python 3.10, an alias of ``TimeoutError`` after.
    return isinstance(exc, (TimeoutError, socket.timeout))


class UrllibTransport:
    """Default transport, built on :mod:`urllib.request`. Honours the standard ``HTTP(S)_PROXY`` variables.

    ``timeout`` is applied to each socket operation (connect, send, read), not to the whole exchange.
    """

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            try:
                with self._opener.open(req, timeout=request.timeout) as response:
                    return HttpResponse(response.status, _flatten(response.headers), response.read())
            except urllib.error.HTTPError as error:
                # A status >= 300 is raised as an exception by urllib; here it is an ordinary response.
                with error:
                    return HttpResponse(error.code, _flatten(error.headers), error.read())
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, BaseException) and _is_timeout(reason):
                raise TimeoutError(f"request timed out after {request.timeout:g}s") from error
            raise ConnectionError(str(reason)) from error
        except (socket.timeout, TimeoutError) as error:
            raise TimeoutError(f"request timed out after {request.timeout:g}s") from error
        except (OSError, http.client.HTTPException) as error:
            raise ConnectionError(str(error) or error.__class__.__name__) from error


def _flatten(headers: Optional[Message]) -> Dict[str, str]:
    return {name: value for name, value in headers.items()} if headers is not None else {}
