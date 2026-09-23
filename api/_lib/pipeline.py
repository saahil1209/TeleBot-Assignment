"""Score -> news angle -> draft. The judgment stays with Meera at the end."""

import json
import re
from email.utils import parsedate_to_datetime

from . import config, gemini, news

THRESHOLD = int(config.get("SCORE_THRESHOLD", "6"))

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
}

SCORE_SYSTEM = """You score raw notes from a skincare founder on whether they can
become a LinkedIn post. You are strict. Most captured fragments are not posts.

Score 0-10 against four things. A note scores well only when it has all four:

1. A mechanism - something that happens for a reason she can explain.
2. An assumption it knocks down - the reader ends up believing something
   different.
3. Evidence she owns, or evidence she can name honestly.
4. An exit - a question the reader could put to a brand or a manufacturer.

Guidance on the range:
  0-2  a logistics reminder, a task, a fragment with no claim in it
  3-5  a real thought, but missing a mechanism or any evidence
  6-7  has all four, thin in one of them
  8-10 a mechanism with evidence behind it and a clear assumption to break

Do not inflate. A note that would produce a generic post scores below 6, however
interesting it sounds. The reason is one line, and it names what is missing
rather than praising what is there."""

KEYWORD_SCHEMA = {
    "type": "object",
    "properties": {
        "phrase": {"type": "string"},
        "terms": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["phrase"],
}

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "used_news": {"type": "boolean"},
        "post": {"type": "string"},
    },
    "required": ["used_news", "post"],
}

DRAFT_SYSTEM = """You draft LinkedIn posts as Meera, founder of Skinstinct.

Her writing style is below. Follow it as mechanics. It contains no facts you may
use - every example in it illustrates a shape, never a claim about her.

%s

--- HARD RULES. These outrank everything above. ---

1. Every fact, number, date, measurement and anecdote comes from the note you
   are given, or from the news item if one is supplied. You invent nothing. If
   the note is thin, the post is short.
1a. This includes how her business operates. Do not state who manufactures,
   who supplies, what she sells, what she tests or how her team works unless
   the note says so. If the note mentions a supplier, she has a supplier - do
   not write that she makes it in-house. Inferring an operational detail is
   inventing it.
1b. Do not characterise the news item's age. Refer to it by what it is, not as
   "recent" or "just published" - you are given its date, and it may be months
   old.
2. Never reuse a figure, date or story from the style guide. They are not hers.
3. No greeting, no sign-off, no hashtags, no emoji, no exclamation marks, no
   closing engagement question.
4. Spaced hyphens ( - ) as dashes, never em dashes. British spelling.
5. 400-700 words in full paragraphs of 3-6 sentences.
6. End on a question the reader can put to a brand, a manufacturer or customer
   service, or on a flat understated line.
7. Set used_news to true only if the news item actually appears in the post.
   If it does not fit naturally, ignore it and set used_news to false. Never
   force it."""

VERIFY_TEMPLATE = """

─────────────────────────────────
NEWS SOURCE: %s
FROM: %s · %s
LINK: %s
⚠ Check this before publishing — you are the author of this claim
─────────────────────────────────"""


def score(note_text):
    text, model = gemini.generate(
        "Score this note:\n\n---\n%s\n---" % note_text,
        kind="fast", system=SCORE_SYSTEM, schema=SCORE_SCHEMA, temperature=0.1,
    )
    data = json.loads(text)
    value = max(0, min(10, int(data.get("score", 0))))
    return value, data.get("reason", "").strip(), model


def search_phrase(note_text):
    try:
        text, _ = gemini.generate(
            "Pull 3-5 search terms from this note and combine them into one short "
            "news search phrase about the underlying subject, not about the "
            "founder.\n\n---\n%s\n---" % note_text,
            kind="fast", schema=KEYWORD_SCHEMA, temperature=0.2,
        )
        return json.loads(text).get("phrase", "").strip()
    except Exception:
        return ""


def _pretty_date(raw):
    if not raw:
        return "date unknown"
    try:
        return parsedate_to_datetime(raw).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return raw


def draft(note_text, voice_skill, news_item=None):
    """Return (post_text, model, news_item_or_None_if_unused)."""
    prompt = ["This note is your only source of facts about her, her company and "
              "her products:\n\n---\n%s\n---" % note_text]
    if news_item:
        prompt.append(
            "\nA current news item was found. If it is genuinely relevant, use it to "
            "make the post timely. If it does not fit naturally, ignore it "
            "entirely.\n\n  headline: %s\n  source: %s\n  date: %s\n  summary: %s"
            % (news_item["headline"], news_item["source"],
               _pretty_date(news_item["date"]), news_item.get("summary", ""))
        )
    prompt.append("\nWrite the post.")

    text, model = gemini.generate(
        "\n".join(prompt), kind="draft",
        system=DRAFT_SYSTEM % voice_skill, schema=DRAFT_SCHEMA, temperature=0.8,
    )
    data = json.loads(text)
    post = data.get("post", "").strip()
    post = post.replace("—", " - ").replace("–", " - ")
    post = re.sub(r" {2,}", " ", post)

    used = news_item if (data.get("used_news") and news_item) else None
    if used:
        post += VERIFY_TEMPLATE % (used["headline"], used["source"],
                                   _pretty_date(used["date"]), used["url"])
    return post, model, used


def run(note_text):
    """Full pass. Returns a dict describing what happened."""
    value, reason, score_model = score(note_text)
    if value < THRESHOLD:
        return {"drafted": False, "score": value, "reason": reason,
                "score_model": score_model}

    from . import store
    voice = store.active_voice_skill()
    if not voice:
        raise RuntimeError("No active voice skill in Supabase. Run scripts/seed_voice.py")

    item = news.top_result(search_phrase(note_text))
    post, model, used = draft(note_text, voice, item)
    return {"drafted": True, "score": value, "reason": reason,
            "score_model": score_model, "post": post, "model": model, "news": used}
