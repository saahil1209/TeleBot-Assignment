"""Gemini client. Standard library only.

The key in use is on the free tier: 20 generateContent requests per day, per
model. Quota is tracked per model, so a 429 rolls to the next model in the
chain instead of failing the request.
"""

import json
import os
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


# The whole request must finish inside the serverless function's maxDuration.
# Four models, two attempts each, at a 120s socket timeout is up to 16 minutes -
# far past it - and a hard timeout kills the process without raising, so the
# note vanishes with no error recorded. Every call is bounded instead.
REQUEST_TIMEOUT = 45
TOTAL_BUDGET = int(os.environ.get("GEMINI_TOTAL_BUDGET", "150"))


def _chain(kind):
    base = FAST_CHAIN if kind == "fast" else DRAFT_CHAIN
    override = config.get("GEMINI_FAST_MODEL" if kind == "fast" else "GEMINI_DRAFT_MODEL")
    return [override] + [m for m in base if m != override] if override else base


def generate(prompt, kind="draft", system=None, schema=None, temperature=0.7,
             audio=None, search=False):
    """Return (text, model_used) or (text, model_used, sources) when search=True.

    Raises GeminiError when every model fails.

    `audio` is an optional (mime_type, base64_data) pair sent alongside the
    prompt - used to transcribe Telegram voice notes without a separate
    speech-to-text service.
    """
    key = config.get("GEMINI_API_KEY", required=True)

    parts = [{"text": prompt}]
    if audio:
        mime, data = audio
        parts.append({"inline_data": {"mime_type": mime, "data": data}})

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": temperature},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if schema and not search:
        # Grounded search and a forced response schema cannot be combined.
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema
    if search:
        body["tools"] = [{"google_search": {}}]

    problems = []
    deadline = time.time() + TOTAL_BUDGET
    for model in _chain(kind):
        if time.time() > deadline:
            problems.append("%s: skipped, time budget spent" % model)
            break
        for attempt in range(2):
            req = urllib.request.Request(
                ENDPOINT.format(model=model),
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
            )
            try:
                remaining = max(5, min(REQUEST_TIMEOUT, deadline - time.time()))
                with urllib.request.urlopen(req, timeout=remaining) as r:
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
                if not search:
                    return text.strip(), model
                sources, seen = [], set()
                grounding = cands[0].get("groundingMetadata") or {}
                for chunk in grounding.get("groundingChunks") or []:
                    web = chunk.get("web") or {}
                    uri = web.get("uri")
                    if uri and uri not in seen:
                        seen.add(uri)
                        sources.append({"title": web.get("title", ""), "uri": uri})
                return text.strip(), model, sources
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
                if e.code in (500, 503) and attempt == 0 and time.time() < deadline - 10:
                    time.sleep(2)
                    continue
                problems.append("%s: %s %s" % (model, e.code, msg))
                break
            except Exception as e:  # network, timeout
                if attempt == 0 and time.time() < deadline - 10:
                    time.sleep(2)
                    continue
                problems.append("%s: %s" % (model, e))
                break

    raise GeminiError("every model failed - " + "; ".join(problems))
