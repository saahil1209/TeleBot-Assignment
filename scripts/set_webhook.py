#!/usr/bin/env python3
"""Point Telegram at the deployed function. Usage: set_webhook.py <base-url>"""

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from _lib import config  # noqa: E402


def call(method, payload=None):
    token = config.get("TELEGRAM_BOT_TOKEN", required=True)
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    if len(sys.argv) < 2:
        info = call("getWebhookInfo")
        print(json.dumps(info.get("result", {}), indent=2))
        print("\nUsage: set_webhook.py https://your-project.vercel.app")
        return

    base = sys.argv[1].rstrip("/")
    payload = {
        "url": "%s/api/webhook" % base,
        "allowed_updates": ["message", "channel_post", "edited_channel_post"],
        "drop_pending_updates": True,
    }
    secret = config.get("TELEGRAM_WEBHOOK_SECRET")
    if secret:
        payload["secret_token"] = secret

    result = call("setWebhook", payload)
    print("setWebhook:", json.dumps(result))
    print("\ngetWebhookInfo:")
    print(json.dumps(call("getWebhookInfo").get("result", {}), indent=2))


if __name__ == "__main__":
    main()
