"""Gemini client. Standard library only.

The key in use is on the free tier: 20 generateContent requests per day, per
model. Quota is tracked per model, so a 429 rolls to the next model in the
chain instead of failing the request.
"""

import json
import time
import urllib.error
import urllib.request

from . import config

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Scoring and keyword extraction are mechanical - flash is the right tool.
FAST_CHAIN = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3-flash-preview", "gemini-3.1-flash-lite"]
# Drafting has to hold a voice across 500 words.
DRAFT_CHAIN = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.1-pro-preview"]


class GeminiError(RuntimeError):
    pass


def _chain(kind):
    base = FAST_CHAIN if kind == "fast" else DRAFT_CHAIN
    override = config.get("GEMINI_FAST_MODEL" if kind == "fast" else "GEMINI_DRAFT_MODEL")
    return [override] + [m for m in base if m != override] if override else base


def generate(prompt, kind="draft", system=None, schema=None, temperature=0.7):
    """Return (text, model_used). Raises GeminiError when every model fails."""
    key = config.get("GEMINI_API_KEY", required=True)

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema

    problems = []
    for model in _chain(kind):
        for attempt in range(2):
            req = urllib.request.Request(
                ENDPOINT.format(model=model),
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    payload = json.load(r)
                cands = payload.get("candidates") or []
                if not cands:
                    problems.append("%s: no candidates" % model)
                    break
                text = "".join(
                    p.get("text", "")
                    for p in cands[0].get("content", {}).get("parts", [])
                )
                if not text.strip():
                    problems.append("%s: empty response" % model)
                    break
                return text.strip(), model
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                try:
                    err = json.loads(raw).get("error", {})
                    msg = err.get("message", "")[:120]
                except ValueError:
                    msg = raw[:120]
                if e.code == 429:
                    problems.append("%s: out of quota" % model)
                    break
                if e.code in (500, 503) and attempt == 0:
                    time.sleep(2)
                    continue
                problems.append("%s: %s %s" % (model, e.code, msg))
                break
            except Exception as e:  # network, timeout
                if attempt == 0:
                    time.sleep(2)
                    continue
                problems.append("%s: %s" % (model, e))
                break

    raise GeminiError("every model failed - " + "; ".join(problems))
