# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-05

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
