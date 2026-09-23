"""Telegram Bot API - just the two calls this pipeline makes."""

import json
import urllib.error
import urllib.request

from . import config

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096


def _call(method, payload):
    token = config.get("TELEGRAM_BOT_TOKEN", required=True)
    req = urllib.request.Request(
        API.format(token=token, method=method),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"ok": False, "description": e.read().decode(errors="replace")[:300]}


def send(chat_id, text, reply_to=None):
    """Send text, splitting on Telegram's 4096-character limit."""
    results = []
    chunks = _split(text)
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if reply_to and i == 0:
            payload["reply_to_message_id"] = reply_to
        results.append(_call("sendMessage", payload))
    return results


def _split(text):
    if len(text) <= LIMIT:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > LIMIT:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current += ("\n\n" if current else "") + para
    if current:
        chunks.append(current.strip())
    return chunks
