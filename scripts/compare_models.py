#!/usr/bin/env python3
"""Run one note through both drafting backends and print them side by side.

The stack separates scoring (Gemini Flash, fast and cheap, no judgment needed)
from drafting (Claude, better at holding a voice across a full post). This is
the script that checks whether that claim holds on Meera's actual notes.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from _lib import claude, news, pipeline, store  # noqa: E402

note = " ".join(sys.argv[1:]).strip() or sys.stdin.read().strip()
if not note:
    sys.exit('Usage: compare_models.py "the note text"')

score, reason, score_model, own_evidence, mechanism = pipeline.score(note)
print("score %d/10 [%s] - %s\nown evidence: %s\n" % (score, score_model, reason, own_evidence))
if score < pipeline.THRESHOLD:
    sys.exit("Below threshold, nothing to compare.")

voice = store.active_voice_skill()
items = news.search(pipeline.search_phrase(note), limit=3)
print("news candidates: %d\n" % len(items))

backends = ["gemini"] + (["claude"] if claude.available() else [])
if len(backends) == 1:
    print("ANTHROPIC_API_KEY not set - showing Gemini only.\n")

for backend in backends:
    post, model, used = pipeline.draft(note, voice, items, backend=backend)
    body = post.split("─────")[0].strip()
    print("=" * 72)
    print("%s  [%s]  %d words  news used: %s"
          % (backend.upper(), model, len(body.split()), bool(used)))
    print("=" * 72)
    print(post)
    print()

print("Read them side by side and write down in one sentence what changed.")
