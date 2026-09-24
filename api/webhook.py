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

from _lib import (config, linkedin, pipeline, store, telegram,  # noqa: E402
                  transcribe)

HELP = (
    "Send a note and I'll score it. Anything at or above %d gets drafted and "
    "comes back here.\n\n"
    "Send text or a voice note - voice gets transcribed first.\n"
    "Reply APPROVE or REJECT to a draft to record your decision.\n"
    "PUBLISH sends an already-approved draft to LinkedIn, when that is enabled."
    % pipeline.THRESHOLD
)


def _publish(chat_id):
    """PUBLISH - the only irreversible action, and the only one that needs two
    separate decisions from her: APPROVE first, then this."""
    if not linkedin.enabled():
        telegram.send(chat_id, "Publishing is switched off. The draft is yours "
                               "to post.")
        return
    if not linkedin.configured():
        telegram.send(chat_id, "Publishing is on but LinkedIn is not configured "
                               "(LINKEDIN_ACCESS_TOKEN / LINKEDIN_PERSON_URN).")
        return

    row = store.latest_approved_draft(chat_id)
    if not row:
        telegram.send(chat_id, "Nothing approved and waiting. Reply APPROVE to a "
                               "draft first - publishing needs both steps.")
        return

    try:
        post_id = linkedin.publish(row["content"])
    except Exception as e:
        telegram.send(chat_id, "Did not publish: %s\n\nThe draft is untouched "
                               "and still approved." % e)
        return

    store.mark_published(row["id"], post_id)
    telegram.send(chat_id, "Published to LinkedIn (%s). The verify block and any "
                           "claim flags were stripped - only the post body went "
                           "out." % post_id)


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
    if not chat_id:
        return

    source, transcript_model = "text", None
    if not text and transcribe.audio_part(msg):
        if store.note_exists(chat_id, message_id):
            return
        try:
            text, transcript_model = transcribe.transcribe(msg)
            source = "voice"
        except Exception as e:
            telegram.send(chat_id, "Could not transcribe that: %s" % e,
                          reply_to=message_id)
            return
        telegram.send(chat_id, "Transcribed (%d words). Scoring it now."
                      % len(text.split()), reply_to=message_id)

    if not text:
        return

    command = text.upper().lstrip("/")
    if command in ("APPROVE", "REJECT"):
        _decision(chat_id, "approved" if command == "APPROVE" else "rejected")
        return
    if command == "PUBLISH":
        _publish(chat_id)
        return
    if command in ("START", "HELP"):
        telegram.send(chat_id, HELP)
        return

    if store.note_exists(chat_id, message_id):
        return  # a Telegram retry of something already handled

    try:
        result = pipeline.run(text)
    except Exception as e:
        telegram.send(chat_id, "Could not process that note: %s" % e)
        raise

    if not result["drafted"]:
        store.save_note(chat_id, message_id, text, result["score"],
                        result["reason"], "rejected", source, transcript_model)
        telegram.send(
            chat_id,
            "No draft for this one.\n\nScore %d/10 - %s\n\nIt stays saved. Two "
            "thin notes on the same mechanism often make one good post later."
            % (result["score"], result["reason"]),
            reply_to=message_id,
        )
        return

    note = store.save_note(chat_id, message_id, text, result["score"],
                           result["reason"], "drafted", source, transcript_model)
    store.save_draft(note["id"] if note else None, chat_id, result["post"],
                     result["model"], result.get("news"),
                     result.get("claims"), result.get("claim_model"))
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
                "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN",
            )},
            "publishing": {"enabled": linkedin.enabled(),
                           "configured": linkedin.configured()},
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

        # Answer Telegram first. A full pass takes two Gemini calls, a news
        # fetch and a draft - far longer than Telegram waits before declaring
        # the webhook failed and redelivering it. The invocation stays alive
        # after the response is flushed, so the work continues below.
        self._respond(200, {"ok": True})
        try:
            self.wfile.flush()
        except Exception:
            pass

        try:
            process(update)
        except Exception:
            traceback.print_exc()
