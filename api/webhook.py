"""Telegram webhook. One note in, one draft back, nothing published.

Meera sends a note to the bot. The pipeline scores it, finds a news angle,
drafts in her voice and sends the draft back to the same chat with a verify
block. She replies APPROVE or REJECT. Nothing reaches LinkedIn from here, and
nothing should be added that does - that is the whole point of the design.
"""

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _lib import config, pipeline, store, telegram  # noqa: E402

HELP = (
    "Send a note and I'll score it. Anything at or above %d gets drafted and "
    "comes back here.\n\n"
    "Reply APPROVE or REJECT to the draft to record your decision."
    % pipeline.THRESHOLD
)


def _decision(chat_id, verdict):
    row = store.latest_pending_draft(chat_id)
    if not row:
        telegram.send(chat_id, "No draft is waiting on a decision.")
        return
    store.decide_draft(row["id"], verdict)
    if verdict == "approved":
        telegram.send(chat_id, "Marked approved. It is yours to post - nothing "
                               "goes out from here.")
    else:
        telegram.send(chat_id, "Marked rejected. Keeping it - rejected drafts "
                               "show what needs improving.")


def process(update):
    msg = (update.get("message") or update.get("channel_post")
           or update.get("edited_message") or update.get("edited_channel_post"))
    if not msg:
        return

    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    message_id = msg.get("message_id")
    if not chat_id or not text:
        return

    command = text.upper().lstrip("/")
    if command in ("APPROVE", "REJECT"):
        _decision(chat_id, "approved" if command == "APPROVE" else "rejected")
        return
    if command in ("START", "HELP"):
        telegram.send(chat_id, HELP)
        return

    try:
        result = pipeline.run(text)
    except Exception as e:
        telegram.send(chat_id, "Could not process that note: %s" % e)
        raise

    if not result["drafted"]:
        store.save_note(chat_id, message_id, text, result["score"],
                        result["reason"], "rejected")
        telegram.send(
            chat_id,
            "No draft for this one.\n\nScore %d/10 - %s\n\nIt stays saved. Two "
            "thin notes on the same mechanism often make one good post later."
            % (result["score"], result["reason"]),
            reply_to=message_id,
        )
        return

    note = store.save_note(chat_id, message_id, text, result["score"],
                           result["reason"], "drafted")
    store.save_draft(note["id"] if note else None, chat_id, result["post"],
                     result["model"], result.get("news"))
    telegram.send(chat_id, "Score %d/10 - %s" % (result["score"], result["reason"]),
                  reply_to=message_id)
    telegram.send(chat_id, result["post"])
    telegram.send(chat_id, "Reply APPROVE or REJECT.")


class handler(BaseHTTPRequestHandler):
    def _respond(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        # Booleans and lengths only - never the values. Enough to tell a missing
        # variable from a malformed one (a trailing newline on a pasted token is
        # the classic cause of a bot that authenticates locally but not here).
        def probe(name):
            raw = config.get(name)
            if not raw:
                return False
            return {"set": True, "len": len(raw), "clean": raw == raw.strip()}

        self._respond(200, {
            "ok": True,
            "service": "skinstinct-telebot",
            "threshold": pipeline.THRESHOLD,
            "backend": pipeline._backend(),
            "env": {n: probe(n) for n in (
                "TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY",
                "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "ANTHROPIC_API_KEY", "TELEGRAM_WEBHOOK_SECRET",
            )},
        })

    def do_POST(self):
        secret = config.get("TELEGRAM_WEBHOOK_SECRET")
        if secret and self.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            self._respond(401, {"ok": False, "error": "bad secret token"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            update = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._respond(200, {"ok": True, "note": "unparseable body ignored"})
            return

        try:
            process(update)
        except Exception:
            traceback.print_exc()
            # Always 200: a non-200 makes Telegram retry the same update forever.
            self._respond(200, {"ok": False, "error": "handled"})
            return

        self._respond(200, {"ok": True})
