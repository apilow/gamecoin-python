# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.4.0] - Unreleased

0.4.0 — catalog management: create, change and archive currencies, items and packs.

### Added

- Three resources, `currencies`, `items` and `packs`, each with `create`, `get`, `update`, `archive` and `list`: the catalog
  management API (`/currencies`, `/items`, `/packs`). Twelve resources and thirty-two calls in all.
- Results `ManagedCurrency`, `ManagedItem` and `ManagedPack` (the catalog objects plus `status`, `created_at` and
  `updated_at`; a pack also has `sort_order` and `availability` as `ManagedPackAvailability(starts_at, ends_at, stock,
  founder, sold)`), and the `CatalogStatus` and `PackStatus` string values.
- `update` follows the `players.upsert` convention: an argument left out is unchanged (`UNSET`), `None` clears a
  description, a price or a limit. `grants`, `items`, `metadata` and `availability` replace the old value as a whole.
- `archive` returns the archived resource; `list` leaves archived ones out unless `include_archived=True`.
- `availability` takes a mapping (`starts_at`, `ends_at`, `stock`, `founder`; timezone-aware datetimes or ISO 8601 strings)
  or the `ManagedPackAvailability` read back from a pack; a key left out is `None` (`founder`: `False`).

### Notes

- The catalog is shared by the `test` and `live` environments of the game: a resource created with a test key exists for
  live keys too.
- A create has no idempotency key (the code or sku is its natural key; a taken one is `CONFLICT` with `SKU_TAKEN` or
  `CODE_TAKEN`), so it is retried on `429` only, like `orders.refund`. Updates, archives and reads follow the usual rules.

## [0.3.0] - Unreleased

0.3.0 — gift codes; creator code on checkouts; pack availability.

### Added

- `codes.redeem(player, code, *, idempotency_key=None)`: redeems a gift code for a player and returns a `Redemption`
  (`grants`, `items`, `skipped`, `replayed`). The code is sent as typed: the API ignores case, spaces and dashes. A code
  that is unknown, expired, used up, disabled or already used is always `VALIDATION_FAILED` with
  `details["fieldErrors"]["code"] == ["CODE_INVALID"]`, and is never retried. New objects `Redemption`, `RedeemedGrant`
  and `RedeemedItem`.
- `creator_code` argument on `checkouts.create` (sent as `creatorCode`, not sent when absent). A refused code is
  `VALIDATION_FAILED` with `details["fieldErrors"]["creatorCode"] == ["CODE_INVALID"]`.
- `Order.creator_code` (`None` or `OrderCreatorCode(code, creator_name)`), also on the `order.*` webhook events.
- `Pack.availability` (`None` or `PackAvailability(starts_at, ends_at, remaining, founder)`), `Catalog.prelaunch` and
  `Catalog.launch_at`.
- `ErrorCode.PACK_NOT_AVAILABLE` (`details["startsAt"]` and `details["endsAt"]`, ISO 8601 text or `None`) and
  `ErrorCode.PACK_SOLD_OUT` (`details["packSku"]`), both HTTP 409 on `checkouts.create`.

## [0.2.0] - Unreleased

0.2.0 — `locale` on checkouts; webhook signature verification.

### Added

- `locale` argument on `checkouts.create` (`"fr"`, `"en"` or `"nl"`): the language of the hosted payment page, the
  receipt and the e-mails of the order. It is not sent when absent.
- `gamecoin.webhooks.construct_event(raw_body, signature_header, secret, *, tolerance_seconds=300, now=None)`: checks the
  `GameCoin-Signature` header of a webhook call (HMAC-SHA256, every `v1` compared in constant time, timestamp
  tolerance) and returns a typed `WebhookEvent`. It needs no client and makes no request.
- `WebhookSignatureError` with a `reason` (`missing_header`, `malformed_header`, `no_matching_signature`,
  `timestamp_out_of_tolerance`, `invalid_payload`; constants in `WebhookSignatureReason`). It does not extend
  `GameCoinError`.
- `WebhookEvent`, `WalletCredited`, `WebhookTest` and the `WebhookEventType` constants (`order.fulfilled`,
  `order.refunded`, `order.chargeback`, `wallet.credited`, `webhook.test`); an unknown event type is passed through
  with its `data` as decoded JSON.
- `examples/webhook_flask.py`.

## [0.1.0] - Unreleased

First release (pre-release: the API runs in test mode with simulated payments).

### Added

- `GameCoin` client for the server API: `catalog`, `players`, `wallet`, `ledger`, `inventory`, `purchases`,
  `checkouts` and `orders`.
- `ext()` helper to designate a player by your own id.
- Frozen dataclasses for every API object, timezone-aware `datetime` fields, `py.typed`.
- Idempotency keys on every mutation (generated once per call, reused by retries, overridable) and the `replayed`
  flag on their results.
- Automatic retries with backoff (network error, timeout, 5xx, 429 with `Retry-After`); refunds are retried on 429
  only.
- `GameCoinError` with `code`, `status`, `message`, `details`, `retry_after`, and the `ErrorCode` constants.
- `ledger.iterate()`, a lazy iterator over every page of a player's ledger.
- Injectable transport; no runtime dependency (standard library only).
