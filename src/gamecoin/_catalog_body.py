"""Bodies of the catalog management calls (``currencies``, ``items``, ``packs``), checked before any request.

Only the shape is checked here (types, integers, unknown keys of a mapping): the rules of the catalog (format of a code, an
existing currency, the content of a pack...) are the server's, which answers ``VALIDATION_FAILED`` with
``details["fieldErrors"]``. Every message names the argument, never its value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import _validate as v
from .models import ManagedPackAvailability, PackGrant, PackItem, Price, parse_datetime

BADGES = ("popular", "best_value")
PACK_STATUSES = ("draft", "active", "archived")


def boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, not {type(value).__name__}")
    return value


def integer(value: Any, name: str, minimum: int) -> int:
    # ``bool`` is an ``int`` subclass: ``price_cents=True`` is a bug, not a request for 1 cent.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def nullable_integer(value: Any, name: str, minimum: int) -> Optional[int]:
    return None if value is None else integer(value, name, minimum)


def text(value: Any, name: str) -> str:
    return v.require_str(value, name)


def nullable_text(value: Any, name: str) -> Optional[str]:
    """A description: any ``str`` (an empty one means none), or ``None``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str or None, not {type(value).__name__}")
    return value


def choice(value: Any, name: str, allowed: Sequence[str]) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    if value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(allowed)}")
    return value


def nullable_choice(value: Any, name: str, allowed: Sequence[str]) -> Optional[str]:
    return None if value is None else choice(value, name, allowed)


def string_map(value: Any, name: str) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping of str to str, not {type(value).__name__}")
    for key, entry in value.items():
        if not isinstance(key, str) or not isinstance(entry, str):
            raise TypeError(f"{name} keys and values must all be str")
    return dict(value)


def _fields(value: Any, name: str, allowed: Sequence[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping, not {type(value).__name__}")
    unknown = [key for key in value if key not in allowed]
    if unknown:
        raise ValueError(f"{name} has an unknown key {unknown[0]!r} (allowed: {', '.join(allowed)})")
    return value


def price(value: Any, name: str) -> Optional[Dict[str, Any]]:
    """A :class:`~gamecoin.models.Price` or a ``{"currency": ..., "amount": ...}`` mapping; ``None`` for no price."""
    if value is None:
        return None
    if isinstance(value, Price):
        currency: Any = value.currency
        amount: Any = value.amount
    else:
        fields = _fields(value, name, ("currency", "amount"))
        currency, amount = fields.get("currency"), fields.get("amount")
    return {"currency": v.require_str(currency, f"{name}.currency"), "amount": integer(amount, f"{name}.amount", 1)}


def _lines(value: Any, name: str, kind: type, keys: Sequence[str], minimums: Mapping[str, int]) -> List[Dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a list, not {type(value).__name__}")
    lines: List[Dict[str, Any]] = []
    for index, line in enumerate(value):
        where = f"{name}[{index}]"
        if isinstance(line, kind):
            source: Mapping[str, Any] = {key: getattr(line, key) for key in keys}
        else:
            source = _fields(line, where, keys)
        entry: Dict[str, Any] = {}
        for key in keys:
            if key in minimums:
                entry[key] = integer(source.get(key), f"{where}.{key}", minimums[key])
            else:
                entry[key] = v.require_str(source.get(key), f"{where}.{key}")
        lines.append(entry)
    return lines


def grants(value: Any, name: str) -> List[Dict[str, Any]]:
    """The currencies a pack credits: :class:`~gamecoin.models.PackGrant` objects or mappings with the same keys."""
    return _lines(value, name, PackGrant, ("currency", "amount", "bonus"), {"amount": 0, "bonus": 0})


def pack_items(value: Any, name: str) -> List[Dict[str, Any]]:
    """The items a pack grants: :class:`~gamecoin.models.PackItem` objects or mappings with the same keys."""
    return _lines(value, name, PackItem, ("sku", "quantity"), {"quantity": 1})


def _instant(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be a timezone-aware datetime")
        return value.isoformat()
    if isinstance(value, str):
        try:
            parse_datetime(value)
        except ValueError:
            raise ValueError(f"{name} must be a datetime or an ISO 8601 date-time with a time zone") from None
        return value
    raise TypeError(f"{name} must be a datetime, an ISO 8601 str or None, not {type(value).__name__}")


def availability(value: Any, name: str) -> Optional[Dict[str, Any]]:
    """The whole sale setting of a pack, as a mapping (``starts_at``, ``ends_at``, ``stock``, ``founder``) or the
    :class:`~gamecoin.models.ManagedPackAvailability` read back from a pack. A key left out is ``None`` (``founder``:
    ``False``): the setting replaces the previous one, it is not merged with it."""
    if value is None:
        return None
    if isinstance(value, ManagedPackAvailability):
        fields: Mapping[str, Any] = {
            "starts_at": value.starts_at,
            "ends_at": value.ends_at,
            "stock": value.stock,
            "founder": value.founder,
        }
    else:
        fields = _fields(value, name, ("starts_at", "ends_at", "stock", "founder"))
    founder = fields.get("founder")
    return {
        "startsAt": _instant(fields.get("starts_at"), f"{name}.starts_at"),
        "endsAt": _instant(fields.get("ends_at"), f"{name}.ends_at"),
        "stock": nullable_integer(fields.get("stock"), f"{name}.stock", 1),
        "founder": False if founder is None else boolean(founder, f"{name}.founder"),
    }


def include_archived(value: Any) -> Dict[str, Any]:
    """The query of a ``list`` call: ``includeArchived=true`` only when asked, so that the default URL stays plain."""
    return {"includeArchived": "true"} if boolean(value, "include_archived") else {}

