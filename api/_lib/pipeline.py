"""Score -> news angle -> draft. The judgment stays with Meera at the end."""

import json
import re
from email.utils import parsedate_to_datetime

from . import claude, config, gemini, news

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
5. Length follows the note. 400-700 words is the ceiling for a note carrying a
   full mechanism with evidence behind it; a thinner note makes a shorter post,
   200-350 words, and that is a correct outcome rather than a failure. Never
   pad to reach a length. Full paragraphs of 3-6 sentences throughout.
5a. Padding, specifically, means inventing narrative: how long something took,
   what happened next, what was decided afterwards, how it was resolved, or
   what other companies, brands or regulators do and do not do. None of that is
   in the note unless the note says it. If you reach the end of the note's
   substance, stop writing.
6. End on a question the reader can put to a brand, a manufacturer or customer
   service, or on a flat understated line.
7. Set used_news to true only if the news item actually appears in the post.
   If it does not fit naturally, ignore it and set used_news to false. Never
   force it."""

CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "unsupported": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["claim"],
            },
        }
    },
    "required": ["unsupported"],
}

CLAIM_SYSTEM = """You audit a draft post against the only two sources it was
allowed to use: a note, and possibly a news item.

Return every factual claim in the post that cannot be traced to one of those
sources. A factual claim is anything a reader could check and find wrong -
a number, a date, a mechanism, an industry norm, a statement about what other
companies or regulators do.

Include a claim even when it is probably true. Plausibility is not the test;
traceability is. A true claim the author cannot source is still a claim she
would be publishing on someone else's authority.

Do not include:
  - opinions, judgments or arguments
  - restatements of something in the note, even in different words
  - reasoning that follows from facts in the note
  - who the author is: her name, her company's name, that she founded it, that
    it makes skincare, that she has a formulation background. That is standing
    context, not a claim she needs a source for. "our formulations at
    Skinstinct" is not a finding.

Quote each claim in `claim` exactly as it appears in the post, trimmed to the
sentence. Put in `why` the reason it is not traceable, in one short clause.
Return an empty array when everything traces."""

FLAG_TEMPLATE = """

─────────────────────────────────
⚠ UNVERIFIED CLAIMS (%d)
Not traceable to your note%s:
%s
Cut them or confirm them before publishing.
─────────────────────────────────"""

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


def _backend():
    """Which model writes the post.

    The stack puts drafting on Claude from B1 onward. 'auto' uses Claude when a
    key is present and falls back to Gemini when it is not, so the pipeline
    keeps working either way rather than failing closed on a missing key.
    """
    choice = (config.get("DRAFT_BACKEND", "auto") or "auto").lower()
    if choice == "claude":
        return "claude"
    if choice == "gemini":
        return "gemini"
    return "claude" if claude.available() else "gemini"


def _clean(post):
    post = post.strip().replace("\u2014", " - ").replace("\u2013", " - ")
    return re.sub(r" {2,}", " ", post)


def draft(note_text, voice_skill, news_item=None, backend=None):
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
    prompt = "\n".join(prompt)
    system = DRAFT_SYSTEM % voice_skill

    backend = backend or _backend()
    if backend == "claude":
        data, model = claude.generate_json(prompt, system, DRAFT_SCHEMA)
    else:
        text, model = gemini.generate(prompt, kind="draft", system=system,
                                      schema=DRAFT_SCHEMA, temperature=0.8)
        data = json.loads(text)

    post = _clean(data.get("post", ""))
    used = news_item if (data.get("used_news") and news_item) else None
    if used:
        post += VERIFY_TEMPLATE % (used["headline"], used["source"],
                                   _pretty_date(used["date"]), used["url"])
    return post, model, used


def check_claims(post, note_text, news_item=None):
    """Return (unsupported_claims, model). Never raises - a failed audit must
    not lose a draft, but it also must not silently look like a clean one."""
    sources = ["NOTE:\n%s" % note_text]
    if news_item:
        sources.append("NEWS ITEM:\n  headline: %s\n  source: %s\n  summary: %s"
                       % (news_item["headline"], news_item["source"],
                          news_item.get("summary", "")))
    body = post.split("─────")[0].strip()
    try:
        text, model = gemini.generate(
            "%s\n\n---\n\nPOST:\n%s" % ("\n\n".join(sources), body),
            kind="fast", system=CLAIM_SYSTEM, schema=CLAIM_SCHEMA, temperature=0.1,
        )
        return json.loads(text).get("unsupported", []), model
    except Exception:
        return [], None


def flag_block(claims, has_news):
    if not claims:
        return ""
    lines = "\n".join("- \"%s\"" % c.get("claim", "").strip() for c in claims)
    return FLAG_TEMPLATE % (len(claims),
                            " or the news item" if has_news else "", lines)


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

    # The hard rules tell the drafting model not to invent facts. They are a
    # request, not a guarantee, so the draft is audited against its own sources
    # and anything untraceable is surfaced rather than left in fluent prose.
    claims, claim_model = check_claims(post, note_text, used)
    post += flag_block(claims, bool(used))

    return {"drafted": True, "score": value, "reason": reason,
            "score_model": score_model, "post": post, "model": model,
            "news": used, "claims": claims, "claim_model": claim_model}
