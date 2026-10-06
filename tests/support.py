"""Test helpers: a scripted fake transport and builders for API payloads."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from gamecoin import GameCoin, HttpRequest, HttpResponse

KEY = "gc_sk_test_" + "k" * 43
BASE = "https://gamecoin.test"
API = BASE + "/api/v1"
PLAYER_ID = "0123456789abcdef01234567"
ORDER_ID = "abcdef0123456789abcdef01"

Scripted = Union[HttpResponse, BaseException, Callable[[HttpRequest], HttpResponse]]


class FakeTransport:
    """Answers requests from a script, in order, and records them. An empty script is a test failure."""

    def __init__(self, *script: Scripted) -> None:
        self.script: List[Scripted] = list(script)
        self.requests: List[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(request)
        return step

    @property
    def last(self) -> HttpRequest:
        return self.requests[-1]


def make_client(*script: Scripted, **options: Any) -> Tuple[GameCoin, FakeTransport, List[float]]:
    """A client wired to a fake transport; the third item collects the delays the client asked to sleep."""
    transport = FakeTransport(*script)
    sleeps: List[float] = []
    client = GameCoin(KEY, base_url=BASE, transport=transport, sleep=sleeps.append, **options)
    return client, transport, sleeps


def ok(payload: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> HttpResponse:
    return HttpResponse(status, headers or {}, json.dumps(payload).encode("utf-8"))


def replayed(payload: Any) -> HttpResponse:
    return ok(payload, headers={"Idempotent-Replayed": "true"})


def api_error(
    status: int,
    code: str,
    message: str = "Something went wrong",
    details: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> HttpResponse:
    error: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return ok({"error": error}, status, headers)


def body_of(request: HttpRequest) -> Any:
    assert request.body is not None, "the request has no body"
    return json.loads(request.body.decode("utf-8"))


# --- API payloads (shapes of docs/tech/API.md § Objects) --------------------------------------------------------


def player_json(**over: Any) -> Dict[str, Any]:
    data = {
        "id": PLAYER_ID,
        "externalId": "user-42",
        "kind": "external",
        "displayName": "Alice",
        "country": "BE",
        "blocked": False,
        "createdAt": "2026-10-05T12:00:00.000Z",
    }
    data.update(over)
    return data


def balance_json(**over: Any) -> Dict[str, Any]:
    data = {"currency": "gems", "paid": 0, "bonus": 100, "total": 100}
    data.update(over)
    return data


def entry_json(**over: Any) -> Dict[str, Any]:
    data = {
        "id": "ffffffffffffffffffffff01",
        "type": "grant",
        "currency": "gems",
        "paidDelta": 0,
        "bonusDelta": 100,
        "balanceAfter": {"paid": 0, "bonus": 100, "total": 100},
        "reason": "level_up",
        "metadata": {"level": "7"},
        "ref": {},
        "createdAt": "2026-10-05T12:00:01.250Z",
    }
    data.update(over)
    return data


def movement_json() -> Dict[str, Any]:
    return {"entry": entry_json(), "balance": balance_json()}


def item_json(**over: Any) -> Dict[str, Any]:
    data = {"sku": "potion", "quantity": 3, "updatedAt": "2026-10-05T12:00:02.000Z"}
    data.update(over)
    return data


def order_json(**over: Any) -> Dict[str, Any]:
    data = {
        "id": ORDER_ID,
        "playerId": PLAYER_ID,
        "status": "pending",
        "pack": {"sku": "gems-500", "name": "500 gems"},
        "amountCents": 499,
        "currency": "EUR",
        "country": "BE",
        "vatRateBp": 2100,
        "vatCents": 87,
        "netCents": 412,
        "createdAt": "2026-10-05T12:00:03.000Z",
        "paidAt": None,
        "fulfilledAt": None,
        "refundedAt": None,
    }
    data.update(over)
    return data


def catalog_json() -> Dict[str, Any]:
    return {
        "currencies": [
            {"code": "gems", "name": "Gems", "purchasable": True},
            {"code": "gold", "name": "Gold", "purchasable": False},
        ],
        "items": [
            {
                "sku": "potion",
                "name": "Potion",
                "description": "Heals",
                "type": "consumable",
                "price": {"currency": "gems", "amount": 20},
                "maxOwned": None,
                "clientPurchasable": True,
                "metadata": {"heal": "50"},
            },
            {
                "sku": "fire-sword",
                "name": "Fire sword",
                "description": None,
                "type": "durable",
                "price": None,
                "maxOwned": 1,
                "clientPurchasable": False,
                "metadata": {},
            },
        ],
        "packs": [
            {
                "sku": "gems-500",
                "name": "500 gems",
                "description": None,
                "priceCents": 499,
                "currency": "EUR",
                "grants": [{"currency": "gems", "amount": 500, "bonus": 50}],
                "items": [{"sku": "potion", "quantity": 2}],
                "badge": "best value",
                "maxPerPlayer": None,
            }
        ],
    }


def redemption_json(**over: Any) -> Dict[str, Any]:
    data = {
        "grants": [{"currency": "gems", "amount": 50, "balance": balance_json(bonus=150, total=150)}],
        "items": [{"sku": "potion", "quantity": 2}],
        "skipped": [{"sku": "fire-sword", "quantity": 1}],
    }
    data.update(over)
    return data
