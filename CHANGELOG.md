# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

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
