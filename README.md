# Skinstinct TeleBot

Meera drops a note into Telegram. The pipeline scores it, finds a news angle,
drafts a LinkedIn post in her voice, and sends it back to the same chat for her
to approve. Nothing is published.

## Getting a draft into LinkedIn

`COPY` replies with the post body on its own - annotations stripped - so she can
copy it in one gesture and paste it into LinkedIn.

It cannot be saved straight into LinkedIn's drafts folder, and no tool can do
that. The Posts API states that `PUBLISHED` is the only `lifecycleState`
accepted during creation; `DRAFT` can be read back but never created. The
drafts in LinkedIn's composer are a client-side feature the API does not
expose. Anything claiming to save drafts to LinkedIn is either posting live or
storing the draft somewhere else and calling it a draft.

The `drafts` table is the real drafts folder here, and it keeps the verify
block, the claim flags and the source note, all of which LinkedIn's composer
would discard.

## Voice notes

She captures by voice. A voice note goes to Telegram `getFile`, downloads, and
is transcribed by Gemini directly - no separate speech-to-text service. The
transcript then enters the pipeline as an ordinary note, and `notes.source`
records whether it arrived as voice or text.

The transcription prompt asks for a verbatim transcript and tells the model to
write `[inaudible]` rather than guess, because a wrong pH value or percentage is
worse than a gap.

## The cut

Of the nine checks, check 07 - Judgment Protected - is the one that fails, and
it fails deliberately. She passed on two consultants who built end-to-end tools.
She wants to remain the author of everything published under her name.

So nothing publishes on its own. There is no scheduler and no auto-post, and
approval alone sends nothing anywhere.

Publishing to LinkedIn exists as an option, off by default, and it takes two
separate decisions from her: APPROVE records that the draft is good, and a
later explicit PUBLISH sends it. She reads the draft before either. That keeps
check 07 intact - the judgment is still hers, per post - while removing the
copy-and-paste step she never objected to.

What would break the cut is publishing as a side effect of approval, or on a
schedule. Neither is built, and neither should be.

Publishing also strips the verify block and the claim flags first. Those are
written for her, not for her readers.

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
                        ├─ draft (Claude, voice skill as system instruction)
                        ├─ audit every claim back to the note / news item
                        ├─ append verify block if the news item was used
                        ├─ append unverified-claims flag if any claim is loose
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

## Claim audit

The drafting prompt forbids inventing facts. That is a request, not a
guarantee: the first live draft produced two claims found nowhere in the note -
an industry pH norm and a statement about cutaneous vasodilation. Both were
plausible, which is what made them dangerous. Fluent prose hides an unsourced
claim well.

So after drafting, a Flash call audits the post against its own two permitted
sources and returns anything untraceable. Plausibility is explicitly not the
test - traceability is, because a true claim she cannot source is still one she
would be publishing on someone else's authority. Anything flagged is appended
to the draft:

```
─────────────────────────────────
⚠ UNVERIFIED CLAIMS (2)
Not traceable to your note:
- "..."
Cut them or confirm them before publishing.
─────────────────────────────────
```

The flags are stored per draft in `drafts.unsupported_claims`, so the rate is
measurable over time rather than surfacing once in Telegram and vanishing.

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

## Which model does what

| task | model | why |
|---|---|---|
| scoring notes | Gemini Flash | mechanical, no judgment needed |
| keyword extraction | Gemini Flash | mechanical |
| fetching news | Google News RSS | free, no key, no account |
| writing drafts | **Gemini** (see below) | Claude in the stack; no key available |

**A documented deviation.** The stack puts drafting on Claude from B1 onward,
because it holds a voice better across a full post. There is no Anthropic key
for this build, so drafting runs on Gemini and `DRAFT_BACKEND` is set to
`gemini` explicitly rather than left on `auto` - a deliberate choice recorded in
config beats a silent fallback that looks like the spec was met.

The Claude backend is built and wired (`api/_lib/claude.py`). Adding
`ANTHROPIC_API_KEY` and setting `DRAFT_BACKEND=claude` (or `auto`) switches it
with no code change, and makes `scripts/compare_models.py` able to run the
side-by-side comparison the stack asks for.

```bash
python3 scripts/compare_models.py "a note"
```

runs the same note through both and prints them side by side.

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
| `api/_lib/gemini.py` | Gemini client for scoring and keywords, per-model quota fallback |
| `api/_lib/claude.py` | Anthropic client for drafting, forced-tool structured output |
| `api/_lib/news.py` | Google News RSS, no key needed |
| `api/_lib/store.py` | Supabase REST |
| `api/_lib/telegram.py` | sendMessage, with 4096-character splitting |
| `voice-skill.txt` | the voice instruction sent to Gemini |
| `TRIAGE.md` | the reasoning behind the scoring criteria |
