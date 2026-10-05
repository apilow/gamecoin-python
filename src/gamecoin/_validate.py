"""Local argument checks: a bad call fails here, before any request, with ``ValueError`` or ``TypeError``."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional, Union
from urllib.parse import quote

from .models import Player

PlayerLike = Union[str, Player]

MAX_PAGE_LIMIT = 100
_IDEMPOTENCY_KEY = re.compile(r"[\x20-\x7e]{1,100}")


def require_str(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def optional_str(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str or None, not {type(value).__name__}")
    return value


def positive_int(value: Any, name: str) -> int:
    # ``bool`` is an ``int`` subclass: ``amount=True`` is a bug, not a request for 1 gem.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, not {type(value).__name__}")
    if value < 1:
        # The message names the argument, never its value.
        raise ValueError(f"{name} must be a positive integer")
    return value


def page_limit(value: Any, name: str) -> Optional[int]:
    """A page size: ``None`` (the API default) or an integer from 1 to 100."""
    if value is None:
        return None
    number = positive_int(value, name)
    if number > MAX_PAGE_LIMIT:
        raise ValueError(f"{name} must be an integer from 1 to {MAX_PAGE_LIMIT}")
    return number


def nullable_str(value: Any, name: str) -> Optional[str]:
    """A profile field: a non-empty ``str``, or ``None`` to erase it."""
    return None if value is None else require_str(value, name)


def player_ref(value: Any) -> str:
    """A GameCoin player id, the ``ext(...)`` form, or a :class:`~gamecoin.models.Player` returned by the SDK."""
    if isinstance(value, Player):
        return value.id
    return require_str(value, "player")


def metadata(value: Any) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"metadata must be a mapping of str to str, not {type(value).__name__}")
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError("metadata keys and values must all be str")
    return dict(value)


def idempotency_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"idempotency_key must be a str, not {type(value).__name__}")
    if _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise ValueError("idempotency_key must be 1 to 100 printable ASCII characters")
    return value


def segment(value: str) -> str:
    """Percent-encodes one path segment, exactly once (``/``, ``:``, ``%`` and spaces included)."""
    return quote(value, safe="")
