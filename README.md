# Apilow GameCoin: Python SDK

**The bank of your game.** GameCoin keeps the virtual currency, the inventory and the sales of your web or mobile
game behind one API. This is the **server** SDK: it runs in your game's backend, holds a **secret key**, and is the
only place that can credit coins. (The browser SDK uses a publishable key and can never credit anything.)

- Pure standard library: **no dependency**, Python 3.9 and later.
- Typed (`py.typed`): frozen dataclasses for every object, timezone-aware `datetime` fields.
- Safe to retry: every mutation carries an idempotency key and the SDK retries network errors, `5xx` and `429` for you.
- Synchronous only in this version (no `asyncio`); see [Configuration](#configuration) for async apps.

## Install

```bash
pip install apilow-gamecoin
```

## Quickstart

```python
import os
from gamecoin import GameCoin, ext

gamecoin = GameCoin(os.environ["GAMECOIN_SECRET_KEY"])  # gc_sk_test_... while you develop
player = ext("user-42")                                  # your own id for the player

gamecoin.wallet.grant(player, currency="gems", amount=100, reason="welcome_gift")
movement = gamecoin.wallet.spend(player, currency="gems", amount=30, reason="shop")
print(movement.balance.total)                            # 70 for a brand new player

checkout = gamecoin.checkouts.create(player, pack_sku="gems-500")
print(checkout.checkout_url)                             # send the player to this page to pay
```

A runnable version of this tour lives in [`examples/quickstart.py`](examples/quickstart.py).

## Configuration

```python
import os
from gamecoin import GameCoin

gamecoin = GameCoin(
    os.environ["GAMECOIN_SECRET_KEY"],
    base_url="https://gamecoin.apilow.com",  # the default; use your sandbox or proxy address if you have one
    timeout=30,                              # seconds, applied to each socket operation
    max_retries=2,                           # extra attempts after a network error, timeout, 5xx or 429
)
print(gamecoin.environment)                  # "test" or "live", read from the key prefix
print(gamecoin)                              # GameCoin(environment='test', base_url='https://gamecoin.apilow.com', secret_key='gc_sk_test_…')
```

| Option | Default | |
|---|---|---|
| `base_url` | `https://gamecoin.apilow.com` | Origin of the service; the SDK adds `/api/v1` itself. A path prefix is kept, trailing slashes are dropped. |
| `timeout` | `30` | Seconds, per socket operation (connect, send, read). |
| `max_retries` | `2` | Extra attempts, so up to three in all; from `0` to `10`. `0` turns retries off. |
| `transport` | standard library (`urllib`) | A callable that sends one HTTP request: see [Testing your code](#testing-your-code). |

The constructor refuses anything that is not a secret key (`gc_sk_test_...` or `gc_sk_live_...`); whitespace around it,
such as the newline of a key read from a file, is trimmed. A publishable key (`gc_pk_...`) gets a message pointing to
the browser SDK. The key never appears in an error message or in a `repr`, which shows its prefix only
(`gc_sk_test_…`). Keep it in an environment variable or a secret store, never in your repository.

The client is **synchronous and thread-safe**: create one and share it. In an `asyncio` application, hand the calls
to a worker thread:

```python
import asyncio
import os
from gamecoin import GameCoin, ext

gamecoin = GameCoin(os.environ["GAMECOIN_SECRET_KEY"])

async def welcome_gift(user_id: str):
    return await asyncio.to_thread(gamecoin.wallet.grant, ext(user_id), currency="gems", amount=10, reason="welcome_gift")

print(asyncio.run(welcome_gift("user-42")).balance.total)
```

## Players and the `ext` helper

A player is a GameCoin player id, or **your own id** wrapped by `ext()`: `ext("user-42")` is `"ext:user-42"`. With a
secret key, the first write for an unknown `ext` player creates it, so there is no registration step.

```python
from gamecoin import ext

player = ext("user-42")

profile = gamecoin.players.upsert(player, display_name="Alice", country="BE")
print(profile.id, profile.external_id, profile.kind)       # GameCoin id, "user-42", "external"

gamecoin.players.upsert(player, display_name=None)         # None erases a field; a field you leave out is kept
print(gamecoin.players.get(player).display_name)           # None

token = gamecoin.players.create_token(player)              # short-lived token for the browser SDK
print(token.expires_at)
```

Players are read with `players.get`. Reading an unknown `ext` player is a `NOT_FOUND` error; any write creates it.
Ids may hold anything (spaces, slashes, `%`, accents): the SDK percent-encodes them exactly once.

## Wallet and ledger

```python
movement = gamecoin.wallet.grant(player, currency="gems", amount=100, reason="level_up", metadata={"level": "7"})
print(movement.balance.total, movement.entry.id, movement.replayed)

gamecoin.wallet.spend(player, currency="gems", amount=30, reason="shop")

wallet = gamecoin.wallet.get(player)                       # one balance per currency, zeros included
for balance in wallet.balances:
    print(balance.currency, balance.paid, balance.bonus, balance.total)
print(wallet.balance_of("gems").total)
```

`grant` credits **free** coins (the `bonus` part of a balance); `paid` coins come from packs bought with real money.
`amount` is a positive integer. `reason` (a short text) and `metadata` (a `dict` of `str` to `str`) are stored on the
ledger entry.

The ledger is the immutable history of every movement, newest first (`limit` is the page size, from 1 to 100):

```python
page = gamecoin.ledger.list(player, currency="gems", limit=20)
for entry in page.entries:
    print(entry.created_at, entry.type, entry.paid_delta + entry.bonus_delta, entry.balance_after.total)
if page.next_cursor:
    older = gamecoin.ledger.list(player, currency="gems", limit=20, cursor=page.next_cursor)

# Or let the SDK turn the pages. Each page is fetched only when the loop reaches it.
for entry in gamecoin.ledger.iterate(player, currency="gems"):
    print(entry.id, entry.reason)
```

## Inventory and purchases

Items are what players own: `consumable` items are used up, `durable` ones are kept (some have a maximum).

```python
gamecoin.inventory.grant(player, sku="potion", quantity=3)
gamecoin.inventory.consume(player, sku="potion")           # quantity defaults to 1
for item in gamecoin.inventory.list(player):               # owned items only
    print(item.sku, item.quantity)

purchase = gamecoin.purchases.create(player, sku="potion") # buys with the item's price in virtual currency
print(purchase.balance.total, purchase.item.quantity)
```

```python
from gamecoin import ErrorCode, GameCoinError

gamecoin.inventory.grant(player, sku="fire-sword")
try:
    gamecoin.inventory.grant(player, sku="fire-sword")     # this item is limited to one per player
except GameCoinError as error:
    print(error.code == ErrorCode.LIMIT_REACHED)           # True
```

`catalog.get()` lists the active currencies, items (with their price) and packs of your game.

## Checkout, orders and refunds

A **pack** is sold for real money. `checkouts.create` opens an order and returns the hosted payment page; send the
player there. In the test environment the page offers "Pay (simulated)" and "Decline".

```python
checkout = gamecoin.checkouts.create(
    player,
    pack_sku="gems-500",
    success_url="https://mygame.example/shop/thanks",   # where the player lands after paying (optional)
    cancel_url="https://mygame.example/shop",           # optional
    country="BE",                                        # tax country, ISO 3166-1 alpha-2 (optional)
)
print(checkout.order.status, checkout.checkout_url)      # "pending", then the page to open

order = gamecoin.orders.get(checkout.order.id)
print(order.status, order.amount_cents, order.paid_at)   # follow the order: pending -> paid -> fulfilled
```

Coins and items are credited when the order is paid. Poll `orders.get` (or wait for the player to come back to your
`success_url`) before telling them it worked. Statuses are in `gamecoin.OrderStatus`.

A refund takes back the coins and items of a **paid** order. Refusing to refund an unpaid one is a `CONFLICT`:

```python
try:
    gamecoin.orders.refund(checkout.order.id, reason="support request")
except GameCoinError as error:
    print(error.code == ErrorCode.CONFLICT)                # True: nobody paid this order
```

A refund has no idempotency key, so the SDK retries it on `429` only. After a network error or a `5xx` it raises, and
you check the order with `orders.get` before trying again.

## Idempotency and retries

Every call that changes something (`wallet.grant`, `wallet.spend`, `inventory.grant`, `inventory.consume`,
`purchases.create`, `checkouts.create`) sends an `Idempotency-Key`. Without your help the SDK generates a UUID v4
**once per call** and reuses it for each retry of that call, so a retried `grant` never credits twice.

Give your own key when the same action may reach your code twice (a webhook delivered again, a job restarted):

```python
key = "quest-17:user-42"                                     # 1 to 100 printable ASCII characters
first = gamecoin.wallet.grant(player, currency="gems", amount=50, idempotency_key=key)
again = gamecoin.wallet.grant(player, currency="gems", amount=50, idempotency_key=key)
print(first.replayed, again.replayed)                      # False on the first run, True for the repeat
print(first.entry.id == again.entry.id)                    # True: the original result comes back
```

- `replayed` is `True` when the API recognised the key and returned the original result; nothing happened twice.
- The same key with a **different** request is an `IDEMPOTENCY_CONFLICT` error. Keys are kept for 30 days.
- A replayed `checkouts.create` shows the order in its **current** state, not the one it had at creation.

Automatic retries: at most `max_retries` (default 2), on a network error, a timeout, a `5xx` or a `429`. The delay is
`0.5 s x 2^attempt` times a random factor from 0.75 to 1.25; a `Retry-After` header (in seconds or as an HTTP date)
wins, up to 30 seconds included, on a `5xx` as on a `429`. A longer wait is not worth blocking your request for: the
error is raised and its `retry_after` tells you how long to wait. `4xx` errors other than `429` are never retried.
Reads, `players.upsert` and `players.create_token` are retried like the mutations; only `orders.refund` is limited to
`429`.

## Errors

Every failure of a call is a `GameCoinError`:

```python
from gamecoin import ErrorCode, GameCoinError

try:
    gamecoin.wallet.spend(player, currency="gems", amount=5000)
except GameCoinError as error:
    if error.code == ErrorCode.INSUFFICIENT_FUNDS:
        print(f"needs {error.details['required']}, has {error.details['available']}")
    elif error.code == ErrorCode.RATE_LIMITED:
        print(f"try again in {error.retry_after} seconds")
    else:
        raise
```

| Attribute | Content |
|---|---|
| `code` | API code (`INSUFFICIENT_FUNDS`, `NOT_FOUND`...), or `NETWORK_ERROR` / `TIMEOUT` (status 0), or `INVALID_RESPONSE` when the body is not the expected JSON. Constants in `ErrorCode`. |
| `status` | HTTP status, `0` when no response was received. |
| `message` | Message of the API, or a short description. For developers, not for your players. |
| `details` | `details` of the API (`required` and `available` for `INSUFFICIENT_FUNDS`, `fieldErrors` for `VALIDATION_FAILED`...), `{}` when absent. |
| `retry_after` | Seconds, when the API sent `Retry-After`. |

Codes you will meet: `VALIDATION_FAILED`, `UNAUTHENTICATED`, `FORBIDDEN`, `PLAYER_BLOCKED`, `NOT_FOUND`,
`INSUFFICIENT_FUNDS`, `LIMIT_REACHED`, `IDEMPOTENCY_CONFLICT`, `CONFLICT`, `RATE_LIMITED`, `PAYMENT_FAILED`,
`ENV_NOT_ENABLED`, `INTERNAL`. The API reference describes each one.

Arguments that are wrong on their face (an empty player, `amount=-5`, `quantity=1.5`) never reach the network: they
raise `ValueError` or `TypeError` at once.

## Testing your code

The HTTP layer is a plain callable, so your own tests need no network and no mock library:

```python
from gamecoin import GameCoin, HttpResponse

def fake_transport(request):                                # request.method, request.url, request.body...
    return HttpResponse(200, {}, b'{"playerId": "p1", "balances": []}')

test_client = GameCoin("gc_sk_test_" + "x" * 43, transport=fake_transport)
print(test_client.wallet.get("ext:user-42").balances)       # ()
```

A transport returns an `HttpResponse` for **every** status (4xx and 5xx included), raises `TimeoutError` on a timeout
and `OSError` (for instance `ConnectionError`) on any other network failure.

## Running the tests

```bash
python -m unittest        # unit tests: no network, no key needed
```

The integration tests run against a sandbox game and are skipped unless both variables are set:

```bash
GAMECOIN_SECRET_KEY=gc_sk_test_... GAMECOIN_BASE_URL=http://localhost:3400 python -m unittest tests.test_integration -v
```

## Status

**Pre-release (0.1.0).** The API runs in test mode with simulated payments; real payments are not available yet.
This SDK is synchronous only.

## Licence

[MIT](LICENSE) © 2026 Apilow
