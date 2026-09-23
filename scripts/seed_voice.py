#!/usr/bin/env python3
"""Load voice-skill.txt into the Supabase voice_skill table as the active version."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from _lib import store  # noqa: E402

content = (ROOT / "voice-skill.txt").read_text()
existing = store._request("GET", "voice_skill?select=version&order=version.desc&limit=1")
version = (existing[0]["version"] + 1) if existing else 1

store._request("PATCH", "voice_skill?active=eq.true", {"active": False})
row = store._request("POST", "voice_skill",
                     [{"name": "meera-skinstinct-voice", "content": content,
                       "version": version, "active": True}],
                     prefer="return=representation")
print("seeded voice skill v%d (%d chars)" % (version, len(content)))
