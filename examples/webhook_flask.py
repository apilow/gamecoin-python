#!/usr/bin/env python3
"""Receives GameCoin webhooks with Flask and checks the signature of every call.

    pip install flask
    GAMECOIN_WEBHOOK_SECRET=whsec_gc_... python examples/webhook_flask.py

Declare http://localhost:5000/gamecoin/webhook as an endpoint of your test environment in the dashboard (plain http
is accepted towards localhost in test), copy its signing secret into GAMECOIN_WEBHOOK_SECRET, then use "send a test"
there. The signature is computed on the RAW body: read it with request.get_data(), never with request.json.
"""

import os

from flask import Flask, request

from gamecoin import WebhookEventType, WebhookSignatureError, webhooks

app = Flask(__name__)
WEBHOOK_SECRET = os.environ.get("GAMECOIN_WEBHOOK_SECRET", "")

processed = set()  # use your database in a real game: GameCoin delivers at least once, so the same id can come twice


@app.post("/gamecoin/webhook")
def gamecoin_webhook():
    try:
        event = webhooks.construct_event(
            request.get_data(),                                  # the raw body, exactly as received
            request.headers.get("GameCoin-Signature"),
            WEBHOOK_SECRET,
        )
    except WebhookSignatureError as error:
        app.logger.warning("webhook refused: %s", error.reason)  # the reason is safe to log; the body is not trusted
        return "", 400

    if event.id in processed:
        return "", 200                                           # a repeated event: nothing to do
    processed.add(event.id)

    if event.type == WebhookEventType.ORDER_FULFILLED:
        print(f"order {event.data.id} fulfilled for player {event.data.player_id} ({event.data.pack.sku})")
    elif event.type == WebhookEventType.WALLET_CREDITED:
        for credit in event.data.credits:
            print(f"player {event.data.player_id} received {credit.paid} + {credit.bonus} {credit.currency}")
    elif event.type in (WebhookEventType.ORDER_REFUNDED, WebhookEventType.ORDER_CHARGEBACK):
        print(f"order {event.data.id} was taken back ({event.type})")
    elif event.type == WebhookEventType.WEBHOOK_TEST:
        print(f"test event received: {event.data.message}")
    return "", 200                                               # a 2xx within 10 seconds tells GameCoin it is done


if __name__ == "__main__":
    if not WEBHOOK_SECRET:
        raise SystemExit("Set GAMECOIN_WEBHOOK_SECRET to the signing secret of your endpoint (whsec_gc_...).")
    app.run(port=5000)
