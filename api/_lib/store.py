"""Supabase persistence over the REST API. Service role key, server side only."""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config


def _headers():
    key = config.get("SUPABASE_SERVICE_ROLE_KEY", required=True)
    return {
        "apikey": key,
        "Authorization": "Bearer %s" % key,
        "Content-Type": "application/json",
    }


def _request(method, path, payload=None, prefer=None):
    base = config.get("SUPABASE_URL", required=True).rstrip("/")
    headers = _headers()
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(
        "%s/rest/v1/%s" % (base, path),
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else []
    except urllib.error.HTTPError as e:
        raise RuntimeError("Supabase %s %s: %s"
                           % (method, path, e.read().decode(errors="replace")[:300]))


def active_voice_skill():
    """Active voice skill from the table, falling back to the bundled file.

    The table is the source of truth so the voice can be updated without a
    redeploy. The file fallback means a fresh install still drafts correctly
    before anyone has run scripts/seed_voice.py.
    """
    try:
        rows = _request("GET", "voice_skill?active=eq.true&order=version.desc&limit=1")
        if rows:
            return rows[0]["content"]
    except Exception:
        pass
    bundled = config.ROOT / "voice-skill.txt"
    return bundled.read_text() if bundled.exists() else None


def note_exists(chat_id, message_id):
    """Has this exact Telegram message already been handled?

    Telegram retries a webhook it considers failed, so the same update arrives
    more than once. This is checked before any model call, so a retry costs
    nothing against the free tier's daily quota.
    """
    if message_id is None:
        return False
    rows = _request(
        "GET", "notes?chat_id=eq.%d&telegram_message_id=eq.%d&select=id,status"
        % (chat_id, message_id))
    return rows[0] if rows else False


def save_note(chat_id, message_id, content, score, reason, status,
              source="text", transcript_model=None):
    rows = _request(
        "POST", "notes?on_conflict=chat_id,telegram_message_id",
        [{
            "chat_id": chat_id, "telegram_message_id": message_id,
            "content": content, "score": score,
            "score_reason": reason, "status": status,
            "source": source, "transcript_model": transcript_model,
        }],
        prefer="return=representation,resolution=merge-duplicates",
    )
    return rows[0] if rows else None


def save_draft(note_id, chat_id, content, model, news=None, claims=None,
               claim_model=None, sources=None):
    row = {
        "note_id": note_id, "chat_id": chat_id, "content": content,
        "model": model, "status": "pending",
        "unsupported_claims": claims or [],
        "claim_check_model": claim_model,
        "research_sources": sources or [],
    }
    if news:
        row.update({
            "news_headline": news.get("headline"), "news_source": news.get("source"),
            "news_date": news.get("date"), "news_url": news.get("url"),
        })
    rows = _request("POST", "drafts", [row], prefer="return=representation")
    return rows[0] if rows else None


def latest_pending_draft(chat_id):
    rows = _request(
        "GET",
        "drafts?chat_id=eq.%d&status=eq.pending&order=created_at.desc&limit=1" % chat_id,
    )
    return rows[0] if rows else None


def latest_approved_draft(chat_id):
    """Most recent approved draft that has not been published."""
    rows = _request(
        "GET", "drafts?chat_id=eq.%d&status=eq.approved&published_at=is.null"
        "&order=decided_at.desc&limit=1" % chat_id)
    return rows[0] if rows else None


def mark_published(draft_id, post_id):
    return _request(
        "PATCH", "drafts?id=eq.%d" % draft_id,
        {"status": "published", "published_at": "now()", "linkedin_post_id": post_id},
        prefer="return=representation")


def decide_draft(draft_id, status):
    """Approve or reject. Rejected rows are kept - they show what needs improving."""
    return _request(
        "PATCH", "drafts?id=eq.%d" % draft_id,
        {"status": status, "decided_at": "now()"},
        prefer="return=representation",
    )
