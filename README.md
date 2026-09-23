# Skinstinct TeleBot

Meera drops a note into Telegram. The pipeline scores it, finds a news angle,
drafts a LinkedIn post in her voice, and sends it back to the same chat for her
to approve. Nothing is published.

## The cut

Of the nine checks, check 07 - Judgment Protected - is the one that fails, and
it fails deliberately. She passed on two consultants who built end-to-end tools.
She wants to remain the author of everything published under her name.

So the pipeline stops at the draft. No scheduler, no auto-post, no LinkedIn
credentials anywhere in this repo. The worst failure available is a draft she
rejects, never a post that goes live unchecked. Anything added later that
publishes on her behalf breaks the one property that makes this worth running.

## Flow

```
Telegram  ──▶  /api/webhook
                   │
                   ├─ APPROVE / REJECT ──▶ update draft status in Supabase ──▶ reply
                   │
                   └─ a note
                        │
                        ├─ score 0-10 (Gemini Flash)
                        │     └─ below 6 ──▶ reply with score + reason, save, stop
                        │
                        ├─ keywords ──▶ Google News RSS ──▶ top result
                        ├─ draft (Gemini, voice skill as system instruction)
                        ├─ append verify block if the news item was used
                        ├─ save note + draft to Supabase (status: pending)
                        └─ send the draft back to Telegram
```

Scoring runs before drafting so logistics reminders and abandoned half-sentences
never reach the drafting model. If everything passes, the scoring prompt is too
lenient.

## Verify block

Any draft that uses a news item carries this, and it is not optional:

```
─────────────────────────────────
NEWS SOURCE: [headline]
FROM: [publication] · [date]
LINK: [url]
⚠ Check this before publishing — you are the author of this claim
─────────────────────────────────
```

A fact published in her name that she has not checked is the exact failure this
pipeline exists to prevent. The model is told to set `used_news` to false and
ignore the item when it does not fit naturally, so the block only appears when
the news actually made it into the post.

## Voice

`voice-skill.txt` is what Gemini receives as its system instruction. It is
mechanics only - how she opens, sentence rhythm, verdict pairs, how she presents
numbers, the two kinds of certainty, emotional leaks and containment, what she
never says, how she ends.

It deliberately contains **no facts** about her or Skinstinct. An earlier version
mixed style rules with specific figures and a founding story, and a drafting
model reading that will eventually place one of those numbers in a post as if it
were hers. `VOICE.md` keeps the original for reference, marked not-source-data.

The live copy is the active row in the `voice_skill` table, so the voice can be
changed without a redeploy. `voice-skill.txt` is the fallback if the table is
empty.

## Tables

| table | holds |
|---|---|
| `notes` | every note received, with score and reason, status `scored` / `rejected` / `drafted` |
| `drafts` | every draft, status `pending` / `approved` / `rejected`, plus the news fields |
| `voice_skill` | versioned voice instructions, one row active |

Rejected notes and rejected drafts are kept, never deleted. They are the record
of what the scoring and the voice still get wrong.

RLS is on with no policies, so the anon key reads nothing. The function uses the
service role key.

## Deploy

1. Push to GitHub.
2. Import the repo in Vercel.
3. Add environment variables before deploying - `TELEGRAM_BOT_TOKEN`,
   `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and
   `TELEGRAM_WEBHOOK_SECRET` if you want one.
4. Deploy, then point Telegram at it:

```bash
python3 scripts/set_webhook.py https://your-project.vercel.app
```

5. Seed the voice skill into Supabase once:

```bash
python3 scripts/seed_voice.py
```

`GET /api/webhook` returns a health check, so you can confirm the deployment
before wiring Telegram to it.

## Local

```bash
cp .env.example .env   # fill it in
python3 scripts/try_note.py "paste a note here"
```

`try_note.py` runs score, news and draft without Telegram or Vercel, which is
the cheap way to tune the scoring threshold.

## Cost ceiling

The Gemini key is on the free tier: 20 requests per day, per model. One note
costs three calls - score, keywords, draft. Quota is per model, so
`api/_lib/gemini.py` falls back down a chain of models on a 429 rather than
failing the request. At three posts a week this is comfortable; a bulk backfill
of sixty notes is not, and would need billing enabled.

## Files

| path | role |
|---|---|
| `api/webhook.py` | the Vercel function - routing, APPROVE/REJECT, error handling |
| `api/_lib/pipeline.py` | score, keyword extraction, draft, verify block |
| `api/_lib/gemini.py` | Gemini client, per-model quota fallback |
| `api/_lib/news.py` | Google News RSS, no key needed |
| `api/_lib/store.py` | Supabase REST |
| `api/_lib/telegram.py` | sendMessage, with 4096-character splitting |
| `voice-skill.txt` | the voice instruction sent to Gemini |
| `TRIAGE.md` | the reasoning behind the scoring criteria |
