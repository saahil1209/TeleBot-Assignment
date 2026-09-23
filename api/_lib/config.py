"""Environment config. Works from a .env locally and from real env vars on Vercel."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_loaded = False


def load():
    global _loaded
    if _loaded:
        return
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _loaded = True


def get(name, default=None, required=False, strip=True):
    """Read an environment variable.

    Values are stripped by default. Pasting a key into a dashboard field very
    easily carries a trailing newline, and a URL built from one produces a
    confusing failure a long way from the cause.
    """
    load()
    val = os.environ.get(name, default)
    if strip and isinstance(val, str):
        val = val.strip()
    if required and not val:
        raise RuntimeError("Missing required environment variable: %s" % name)
    return val


SCORE_THRESHOLD = int(os.environ.get("SCORE_THRESHOLD", "6"))
