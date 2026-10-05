"""Apilow GameCoin: server SDK for Python.

    from gamecoin import GameCoin, GameCoinError, ext

    gamecoin = GameCoin(os.environ["GAMECOIN_SECRET_KEY"])
    movement = gamecoin.wallet.grant(ext("user-42"), currency="gems", amount=100, reason="level_up")
    print(movement.balance.total)
"""

from ._version import __version__
from .client import DEFAULT_BASE_URL, GameCoin
from .errors import ErrorCode, GameCoinError
from .models import (
    Balance,
    BalanceAfter,
    Catalog,
    Checkout,
    Currency,
    EntryRef,
    InventoryItem,
    Item,
    ItemType,
    LedgerEntry,
    LedgerEntryType,
    LedgerPage,
    Movement,
    Order,
    OrderPack,
    OrderStatus,
    Pack,
    PackGrant,
    PackItem,
    Player,
    PlayerKind,
    PlayerToken,
    Price,
    Purchase,
    Wallet,
)
from .resources import UNSET, ext
from .transport import HttpRequest, HttpResponse, Transport, UrllibTransport

__all__ = [
    "__version__",
    # client
    "GameCoin",
    "DEFAULT_BASE_URL",
    "ext",
    "UNSET",
    # errors
    "GameCoinError",
    "ErrorCode",
    # objects
    "Balance",
    "BalanceAfter",
    "Catalog",
    "Checkout",
    "Currency",
    "EntryRef",
    "InventoryItem",
    "Item",
    "LedgerEntry",
    "LedgerPage",
    "Movement",
    "Order",
    "OrderPack",
    "Pack",
    "PackGrant",
    "PackItem",
    "Player",
    "PlayerToken",
    "Price",
    "Purchase",
    "Wallet",
    # string values of the object fields
    "ItemType",
    "LedgerEntryType",
    "OrderStatus",
    "PlayerKind",
    # transport
    "HttpRequest",
    "HttpResponse",
    "Transport",
    "UrllibTransport",
]
