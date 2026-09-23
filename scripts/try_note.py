#!/usr/bin/env python3
"""Run one note through score -> news -> draft locally. No Telegram, no Vercel."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from _lib import pipeline  # noqa: E402

note = " ".join(sys.argv[1:]).strip() or sys.stdin.read().strip()
if not note:
    sys.exit("Give me a note: try_note.py \"...\"  (or pipe one in)")

result = pipeline.run(note)
print("score: %d/10  [%s]" % (result["score"], result["score_model"]))
print("reason: %s" % result["reason"])
if not result["drafted"]:
    print("\nBelow threshold (%d). No draft." % pipeline.THRESHOLD)
    sys.exit(0)

print("model: %s | words: %d | news used: %s"
      % (result["model"], len(result["post"].split()), bool(result.get("news"))))
print("=" * 70)
print(result["post"])
