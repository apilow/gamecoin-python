#!/usr/bin/env python3
"""A short tour of the GameCoin server SDK, meant to be run against your sandbox (a test key).

    GAMECOIN_SECRET_KEY=gc_sk_test_... python examples/quickstart.py

Set GAMECOIN_BASE_URL too when the API is not at the default address (a local sandbox, a proxy). The script creates
a brand new player on every run, so it can be repeated. It assumes the sandbox catalog: currency `gems`, item
`potion` (20 gems) and pack `gems-500`.
"""

import os
import sys
import uuid

from gamecoin import DEFAULT_BASE_URL, ErrorCode, GameCoin, GameCoinError, ext


def main() -> int:
    secret_key = os.environ.get("GAMECOIN_SECRET_KEY")
    if not secret_key:
        print("Set GAMECOIN_SECRET_KEY to a test secret key (gc_sk_test_...).", file=sys.stderr)
        return 2

    gamecoin = GameCoin(secret_key, base_url=os.environ.get("GAMECOIN_BASE_URL", DEFAULT_BASE_URL))
    player = ext("demo-" + uuid.uuid4().hex[:8])  # your own id for the player

    # 1. Give a welcome gift. The call is idempotent: a retry never credits twice.
    gift = gamecoin.wallet.grant(player, currency="gems", amount=100, reason="welcome_gift")
    print(f"granted 100 gems, balance: {gift.balance.total}")

    # 2. Spend some of it.
    spent = gamecoin.wallet.spend(player, currency="gems", amount=30, reason="shop")
    print(f"spent 30 gems, balance: {spent.balance.total}")

    # 3. Spending more than the player owns is an error you can handle.
    try:
        gamecoin.wallet.spend(player, currency="gems", amount=1000)
    except GameCoinError as error:
        if error.code != ErrorCode.INSUFFICIENT_FUNDS:
            raise
        print(f"cannot spend {error.details['required']}: only {error.details['available']} available")

    # 4. Buy an item with gems.
    bought = gamecoin.purchases.create(player, sku="potion")
    print(f"bought a potion, balance: {bought.balance.total}, potions owned: {bought.item.quantity}")

    # 5. Read the ledger, newest entry first.
    for entry in gamecoin.ledger.iterate(player):
        print(f"  {entry.created_at:%H:%M:%S}  {entry.type:<8} {entry.currency}  balance after: {entry.balance_after.total}")

    # 6. Sell a pack for real money: send the player to the checkout page.
    checkout = gamecoin.checkouts.create(player, pack_sku="gems-500")
    print(f"order {checkout.order.id} is {checkout.order.status}; pay at {checkout.checkout_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
