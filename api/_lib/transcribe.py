"""Turn a Telegram voice note into text with Gemini.

The case says Meera captures by voice. Before this, a voice note reaching the
bot produced nothing at all - no draft, no reply, no row. Gemini takes audio
directly, so no separate speech-to-text service is needed.
"""

import base64
import json
import urllib.error
import urllib.request

from . import config, gemini

FILE_API = "https://api.telegram.org/file/bot{token}/{path}"
GET_FILE = "https://api.telegram.org/bot{token}/getFile?file_id={fid}"

# Telegram voice notes are OGG/Opus; audio/video_note attachments vary.
MIME = {
    "voice": "audio/ogg",
    "audio": "audio/mpeg",
    "video_note": "video/mp4",
}

# Telegram's Bot API cannot download files larger than 20MB.
MAX_BYTES = 20 * 1024 * 1024

PROMPT = """Transcribe this recording verbatim.

It is a founder's private note to herself about skincare formulation, so expect
ingredient names, chemistry terms, pH values and percentages. Get those exactly
right - a wrong number is worse than a gap.

Write what she said and nothing else. Do not summarise, tidy the grammar,
or add anything she did not say. If a stretch is genuinely inaudible, write
[inaudible] rather than guessing at it."""


def _fetch(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def audio_part(msg):
    """Return (kind, file_id, duration) for the audio in a message, or None."""
    for kind in ("voice", "audio", "video_note"):
        part = msg.get(kind)
        if part and part.get("file_id"):
            return kind, part["file_id"], part.get("duration", 0)
    return None


def transcribe(msg):
    """Return (text, model). Raises RuntimeError with a user-safe message."""
    found = audio_part(msg)
    if not found:
        raise RuntimeError("no audio in that message")
    kind, file_id, _ = found

    token = config.get("TELEGRAM_BOT_TOKEN", required=True)
    try:
        info = json.loads(_fetch(GET_FILE.format(token=token, fid=file_id), 30))
    except urllib.error.HTTPError as e:
        raise RuntimeError("Telegram would not hand over the file (%s)" % e.code)
    if not info.get("ok"):
        raise RuntimeError(info.get("description", "getFile failed"))

    result = info["result"]
    if (result.get("file_size") or 0) > MAX_BYTES:
        raise RuntimeError("that recording is over Telegram's 20MB download limit")

    blob = _fetch(FILE_API.format(token=token, path=result["file_path"]), 120)
    encoded = base64.b64encode(blob).decode()

    text, model = gemini.generate(
        PROMPT, kind="fast", temperature=0.0,
        audio=(MIME.get(kind, "audio/ogg"), encoded),
    )
    if not text.strip():
        raise RuntimeError("the transcription came back empty")
    return text.strip(), model
