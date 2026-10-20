# Apilow GameCoin: Python SDK

**The bank of your game.** GameCoin keeps the virtual currency, the inventory and the sales of your web or mobile
game behind one API. This is the **server** SDK: it runs in your game's backend, holds a **secret key**, and is the
only place that can credit coins. (The browser SDK uses a publishable key and can never credit anything.)

- Pure standard library: **no dependency**, Python 3.9 and later.
- Typed (`py.typed`): frozen dataclasses for every object, timezone-aware `datetime` fields.
- Webhook signature verification built in: [`webhooks.construct_event`](#webhooks), no client needed.
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

Packs can have a sale window and a limited stock. `pack.availability` is `None` for an ordinary pack (always on sale);
otherwise it is a `PackAvailability` with `starts_at` and `ends_at` (aware `datetime` or `None`), `remaining` (the stock
left, `None` when unlimited) and `founder`. The catalog also lists packs that are scheduled, ended or sold out: your shop
decides what to show. While `catalog.prelaunch` is `True`, show pre-orders with the date `catalog.launch_at`.

```python
catalog = gamecoin.catalog.get()
for pack in catalog.packs:
    window = pack.availability
    if window is not None and window.remaining == 0:
        print(pack.sku, "is sold out")
```

## Manage the catalog

`currencies`, `items` and `packs` create, change and archive the catalog, the way a payment platform manages its products and
prices. Each has `create`, `get`, `update`, `archive` and `list`.

> The catalog is shared by the `test` and `live` environments of the game: a pack created with a test key exists for live keys
> too. Create a new pack as a draft (the default) and turn it on with `status="active"` when it is ready.

```python
gamecoin.currencies.create(code="rubies", name="Rubies")
gamecoin.items.create(sku="elixir", name="Elixir", type="consumable", price={"currency": "gems", "amount": 15})

pack = gamecoin.packs.create(
    sku="mega",
    name="Mega pack",
    price_cents=999,
    grants=[{"currency": "gems", "amount": 1200, "bonus": 100}],
    items=[{"sku": "elixir", "quantity": 2}],
)
print(pack.status)  # "draft"

gamecoin.packs.update(
    "mega",
    price_cents=1099,
    availability={"starts_at": datetime(2026, 11, 1, tzinfo=timezone.utc), "stock": 500, "founder": True},  # replaces the whole availability
    status="active",
)
gamecoin.items.update("elixir", price=None)  # None clears a description, a price or a limit
gamecoin.packs.archive("mega")  # nothing is deleted
everything = gamecoin.packs.list(include_archived=True)
```

An argument you leave out of `update` is unchanged. The identifiers (`code`, `sku`) and the `type` of an item are final, so
`update` has no argument for them. A taken identifier is `CONFLICT` with `details["fieldErrors"]["sku"] == ["SKU_TAKEN"]`
(`["CODE_TAKEN"]` for a currency); an invalid pack is `VALIDATION_FAILED` with the faulty lines in `details["fieldErrors"]`;
archiving something that an active pack still uses is `CONFLICT` with `details["blockedBy"]`. A create has no idempotency key
(the identifier is its natural key), so it is retried on `429` only: after a network error, a timeout or a `5xx`, read the
resource back with `get` before you try again.

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
    locale="nl",                                         # language of the payment page and e-mails: "fr", "en" or "nl" (optional)
    creator_code="ALICE10",                              # a creator's code typed by the buyer (optional)
)
print(checkout.order.status, checkout.checkout_url)      # "pending", then the page to open

order = gamecoin.orders.get(checkout.order.id)
print(order.status, order.amount_cents, order.paid_at)   # follow the order: pending -> paid -> fulfilled
```

`locale` (`"fr"`, `"en"` or `"nl"`) is the language of the hosted payment page, the receipt and the e-mails of the order.
It is not sent when you leave it out, the API ignores any other value, and a pending order that is reused takes the
language of the latest request.

`creator_code` is a **creator code** typed by the buyer: the order keeps it (`order.creator_code` is `None` or an
`OrderCreatorCode` with `code` and `creator_name`, never the creator's rates), the buyer gets a bonus of free coins
when the order is delivered and the creator earns a commission. A refused code (unknown, disabled, not valid for the
pack, or the creator's own player) is a `VALIDATION_FAILED` error:

```python
try:
    gamecoin.checkouts.create(player, pack_sku="gems-500", creator_code=typed_by_the_player)
except GameCoinError as error:
    if error.code == ErrorCode.VALIDATION_FAILED and "creatorCode" in error.details.get("fieldErrors", {}):
        print("This creator code is not valid.")             # fieldErrors["creatorCode"] == ["CODE_INVALID"]
    elif error.code == ErrorCode.PACK_NOT_AVAILABLE:
        print(error.details["startsAt"], error.details["endsAt"])   # ISO 8601 text or None
    elif error.code == ErrorCode.PACK_SOLD_OUT:
        print("No unit left of", error.details["packSku"])
    else:
        raise
```

A pack outside its sale window is refused with `PACK_NOT_AVAILABLE` and a pack without a unit left with
`PACK_SOLD_OUT` (both HTTP 409, never retried). The window is checked when the order is created: an order created in
time stays payable.

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

## Codes

A **gift code** gives a player free coins (the `bonus` part of a balance) and items that your studio chose: limited
uses, one use per player, optional expiry. Redeem it for a player from your backend:

```python
from gamecoin import ErrorCode, GameCoinError

try:
    redemption = gamecoin.codes.redeem(player, typed_by_the_player)      # pass the code as typed
except GameCoinError as error:
    if error.code == ErrorCode.VALIDATION_FAILED:                      # fieldErrors["code"] == ["CODE_INVALID"]
        print("This code is not valid.")
    else:
        raise
else:
    for grant in redemption.grants:
        print(f"+{grant.amount} {grant.currency}, balance: {grant.balance.total}")
    for item in redemption.items:
        print(f"+{item.quantity} x {item.sku}")
    for item in redemption.skipped:                                      # the player already owns the maximum
        print(f"{item.sku} skipped")
```

The server ignores case, spaces and dashes, so the SDK sends the code exactly as typed. Every refusal (unknown,
expired, used up, disabled, already used by this player, or a creator code) is the same `VALIDATION_FAILED` error with
`details["fieldErrors"]["code"] == ["CODE_INVALID"]`: nothing tells a guess apart from a real code, and it is never
retried. `PLAYER_BLOCKED`, `LIMIT_REACHED` (the player owns the maximum of every item of the code), `CONFLICT` and
`RATE_LIMITED` (attempts are limited) come through the usual [error mapping](#errors). `codes.redeem` carries an
idempotency key like every mutation: the same `idempotency_key` never credits twice, and `redemption.replayed` tells.

Creator codes are a different thing: they are given to `checkouts.create` (see [Checkout](#checkout-orders-and-refunds)).

## Webhooks

GameCoin can call **your server** when an order is delivered, refunded or disputed (declare an endpoint in the
dashboard; it shows a signing secret, `whsec_gc_...`, once). Every call is signed. `webhooks.construct_event` checks the
signature and the age of the call, and returns the event: it is a plain function, so it needs no client and makes no
request.

> **Pass the raw body.** The signature covers the body byte for byte. Give `construct_event` the request body exactly as
> received (`bytes` or `str`), **before** any JSON parsing: a body that was parsed and serialized again can differ by one
> character, and then no signature matches. A parsed object is refused with a `TypeError`. In Flask that is
> `request.get_data()`, not `request.json`.

```python
import os

from flask import Flask, request
from gamecoin import WebhookEventType, WebhookSignatureError, webhooks

app = Flask(__name__)
WEBHOOK_SECRET = os.environ["GAMECOIN_WEBHOOK_SECRET"]       # whsec_gc_..., kept on your server only

@app.post("/gamecoin/webhook")
def gamecoin_webhook():
    try:
        event = webhooks.construct_event(
            request.get_data(),                              # the raw body
            request.headers.get("GameCoin-Signature"),
            WEBHOOK_SECRET,
        )
    except WebhookSignatureError as error:
        print("refused:", error.reason)                      # e.g. "no_matching_signature"
        return "", 400

    if already_processed(event.id):                          # at-least-once delivery: the same id can come twice
        return "", 200
    if event.type == WebhookEventType.ORDER_FULFILLED:
        unlock_purchase(event.data.player_id, event.data.pack.sku)   # event.data is an Order
    mark_processed(event.id)
    return "", 200                                           # answer 2xx within 10 seconds
```

A runnable version lives in [`examples/webhook_flask.py`](examples/webhook_flask.py) (`pip install flask` first).

`construct_event(raw_body, signature_header, secret, *, tolerance_seconds=300, now=None)`:

- `signature_header` is the `GameCoin-Signature` header, `t=<unix seconds>,v1=<hex>[,v1=<hex>...]`; pass `None` when it
  is absent. The signature is the HMAC-SHA256 of `"<t>." + raw_body` keyed with the secret; **every** `v1` is compared, in
  constant time, and unknown keys of the header are ignored.
- `tolerance_seconds` is how far `t` may be from your clock, in the past or the future. `0` turns the check off. `now`
  replaces the system clock (Unix seconds, an aware `datetime`, or a callable returning one): handy in tests.
- The secret is trimmed. It never appears in an error or a `repr`.
- Wrong arguments (a parsed body, an empty secret) raise `TypeError` or `ValueError`: that is a bug in your code.
- A call that must not be trusted raises `WebhookSignatureError`, which does **not** extend `GameCoinError`. Its
  `reason` is one of the `WebhookSignatureReason` constants: `missing_header`, `malformed_header`,
  `no_matching_signature`, `timestamp_out_of_tolerance`, `invalid_payload` (the body is not JSON or not an event). Its
  message never contains the secret, the body or a signature. Answer `400` and do nothing with the body.

The result is a `WebhookEvent` with `id`, `type`, `created_at` (aware `datetime`), `env` (`"test"` or `"live"`) and `data`:

| `type` (constants in `WebhookEventType`) | `data` |
|---|---|
| `order.fulfilled`, `order.refunded`, `order.chargeback` | an `Order`, as returned by `orders.get` |
| `wallet.credited` | `WalletCredited`: `player_id`, `order_id`, `credits` (tuple of `WalletCredit`: `currency`, `paid`, `bonus`) |
| `webhook.test` | `WebhookTest`: `message` |
| any other type | the decoded JSON (a `dict`), so a newer API never breaks an older SDK |

Use `event.id` to process an event **once**: GameCoin delivers at least once, so the same event can arrive twice. The
wallet and the inventory are already updated when you are notified: never grant coins because an event says so without
having verified its signature.

## Idempotency and retries

Every call that changes something (`wallet.grant`, `wallet.spend`, `inventory.grant`, `inventory.consume`,
`purchases.create`, `checkouts.create`, `codes.redeem`) sends an `Idempotency-Key`. Without your help the SDK generates a UUID v4
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
`ENV_NOT_ENABLED`, `PACK_NOT_AVAILABLE`, `PACK_SOLD_OUT`, `INTERNAL`. The API reference describes each one.

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

**Pre-release (0.4.0).** The API runs in test mode with simulated payments; real payments are not available yet.
This SDK is synchronous only.

## Licence

[MIT](LICENSE) © 2026 Apilow
