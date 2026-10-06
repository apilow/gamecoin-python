"""The :class:`GameCoin` client."""

from __future__ import annotations

import re
import time
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from ._core import Core
from .resources import (
    CatalogResource,
    CheckoutsResource,
    CodesResource,
    InventoryResource,
    LedgerResource,
    OrdersResource,
    PlayersResource,
    PurchasesResource,
    WalletResource,
)
from .transport import Transport, UrllibTransport

DEFAULT_BASE_URL = "https://gamecoin.apilow.com"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRIES_LIMIT = 10

_SECRET_KEY = re.compile(r"gc_sk_(test|live)_[A-Za-z0-9_-]+")


class GameCoin:
    """Client of the GameCoin server API. Use it from your game's backend only: it holds a **secret** key.

    Args:
        secret_key: ``gc_sk_test_...`` or ``gc_sk_live_...`` (surrounding whitespace is trimmed). Read it from the
            environment or a secret store.
        base_url: Origin of the service, ``https://gamecoin.apilow.com`` by default; the SDK adds ``/api/v1``. Point it
            at your sandbox or a proxy; a path prefix is kept, trailing slashes are dropped.
        timeout: Seconds, applied to each socket operation of a request. Default 30.
        max_retries: Extra attempts after a network error, timeout, 5xx or 429, from 0 to 10. Default 2 (three attempts in all).
        transport: Replaces the HTTP layer (tests, custom proxies): see :mod:`gamecoin.transport`.
        sleep: Replaces :func:`time.sleep` between retries. A hook for tests.

    The client is synchronous and thread-safe: share one instance. Resources: ``catalog``, ``players``, ``wallet``,
    ``ledger``, ``inventory``, ``purchases``, ``checkouts``, ``codes``, ``orders``.

    Raises:
        TypeError: ``secret_key`` is not a string.
        ValueError: ``secret_key`` is not a secret key (a publishable key gets a message pointing to the browser
            SDK), or an option is out of range.
    """

    catalog: CatalogResource
    players: PlayersResource
    wallet: WalletResource
    ledger: LedgerResource
    inventory: InventoryResource
    purchases: PurchasesResource
    checkouts: CheckoutsResource
    codes: CodesResource
    orders: OrdersResource

    def __init__(
        self,
        secret_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Optional[Transport] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        key, environment = _check_secret_key(secret_key)
        self._environment: str = environment
        self._masked_key = f"gc_sk_{environment}_…"  # the prefix only, for display
        self._base_url = _normalize_base_url(base_url)

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not timeout > 0:
            raise ValueError("timeout must be a positive number of seconds")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= MAX_RETRIES_LIMIT:
            raise ValueError(f"max_retries must be an integer from 0 to {MAX_RETRIES_LIMIT}")

        core = Core(
            key,
            self._base_url,
            float(timeout),
            max_retries,
            transport if transport is not None else UrllibTransport(),
            sleep if sleep is not None else time.sleep,
        )
        self.catalog = CatalogResource(core)
        self.players = PlayersResource(core)
        self.wallet = WalletResource(core)
        self.ledger = LedgerResource(core)
        self.inventory = InventoryResource(core)
        self.purchases = PurchasesResource(core)
        self.checkouts = CheckoutsResource(core)
        self.codes = CodesResource(core)
        self.orders = OrdersResource(core)

    @property
    def environment(self) -> str:
        """``"test"`` or ``"live"``, read from the key prefix."""
        return self._environment

    @property
    def base_url(self) -> str:
        return self._base_url

    def __repr__(self) -> str:
        # The key prefix only: a client ends up in logs, tracebacks and debugger panes.
        return (
            f"GameCoin(environment={self._environment!r}, base_url={self._base_url!r}, "
            f"secret_key={self._masked_key!r})"
        )


def _check_secret_key(secret_key: object) -> Tuple[str, str]:
    """Returns the trimmed key and its environment. The messages never contain the key itself."""
    if not isinstance(secret_key, str):
        raise TypeError(f"secret_key must be a str, not {type(secret_key).__name__}")
    secret_key = secret_key.strip()  # a key read from a file or a variable often carries a trailing newline
    if secret_key.startswith("gc_pk_"):
        raise ValueError(
            "This is a publishable key (gc_pk_...). The server SDK needs a secret key (gc_sk_test_... or "
            "gc_sk_live_...): publishable keys belong in the browser SDK, which cannot credit coins."
        )
    if secret_key.startswith("gc_pt_"):
        raise ValueError(
            "This is a player token (gc_pt_...). The server SDK needs a secret key (gc_sk_test_... or gc_sk_live_...)."
        )
    match = _SECRET_KEY.fullmatch(secret_key)
    if match is None:
        raise ValueError("secret_key is not a GameCoin secret key: expected gc_sk_test_... or gc_sk_live_...")
    return secret_key, match.group(1)


def _normalize_base_url(base_url: object) -> str:
    """The origin of the service, plus an optional path prefix, without trailing slashes. The SDK adds /api/v1."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty string such as https://gamecoin.apilow.com")
    text = base_url.strip()
    try:
        parts = urlsplit(text)
        has_credentials = parts.username is not None or parts.password is not None
    except ValueError:
        parts = None
    if (
        parts is None
        or parts.scheme.lower() not in ("http", "https")
        or not parts.netloc
        or re.search(r"\s", text)
    ):
        raise ValueError("base_url must be an absolute http(s) URL, e.g. https://gamecoin.apilow.com")
    if has_credentials or parts.query or parts.fragment:
        raise ValueError("base_url must be an http(s) URL without credentials, query or fragment")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"
