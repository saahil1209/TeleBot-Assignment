"""Anthropic Messages API client. Standard library only.

The stack puts drafting on Claude from B1 onward - it holds a voice better
across a full post than Flash does. Scoring and keyword extraction stay on
Gemini Flash, where the work is mechanical and the cost matters more.
"""

import json
import time
import urllib.error
import urllib.request

from . import config

ENDPOINT = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"


class ClaudeError(RuntimeError):
    pass


def available():
    return bool(config.get("ANTHROPIC_API_KEY"))


def generate_json(prompt, system, schema, tool_name="emit", max_tokens=4096,
                  temperature=0.8):
    """Force a structured result via a single tool the model must call.

    Returns (parsed_dict, model). Claude has no response-schema parameter, so
    a forced tool call is the reliable way to get typed output back.
    """
    key = config.get("ANTHROPIC_API_KEY", required=True)
    model = config.get("CLAUDE_DRAFT_MODEL", DEFAULT_MODEL)

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{
            "name": tool_name,
            "description": "Return the finished post.",
            "input_schema": schema,
        }],
        "tool_choice": {"type": "tool", "name": tool_name},
    }

    last = None
    for attempt in range(3):
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                "x-api-key": key,
                "anthropic-version": VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                payload = json.load(r)
            for block in payload.get("content", []):
                if block.get("type") == "tool_use":
                    return block.get("input", {}), payload.get("model", model)
            raise ClaudeError("no tool_use block in response")
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                last = json.loads(raw).get("error", {}).get("message", raw)[:160]
            except ValueError:
                last = raw[:160]
            if e.code in (429, 500, 529) and attempt < 2:
                time.sleep(2 ** attempt * 2)
                continue
            raise ClaudeError("%s: %s" % (e.code, last))
        except urllib.error.URLError as e:
            last = str(e)
            if attempt < 2:
                time.sleep(2 ** attempt * 2)
                continue
            raise ClaudeError(last)

    raise ClaudeError(last or "unknown failure")
