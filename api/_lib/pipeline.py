"""Score -> news angle -> draft. The judgment stays with Meera at the end."""

import json
import re
from email.utils import parsedate_to_datetime

from . import claude, config, gemini, news, pubmed

THRESHOLD = int(config.get("SCORE_THRESHOLD", "6"))

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "reason": {"type": "string"},
        "has_own_evidence": {"type": "boolean"},
        "mechanism": {"type": "string"},
    },
    "required": ["score", "reason", "has_own_evidence"],
}

SCORE_SYSTEM = """You score raw notes from a skincare founder on whether they can
become a LinkedIn post.

The gate is one thing: **a mechanism**. Something that happens for a reason she
can explain - not that a thing happened, but why it happens. A mechanism cannot
be added later without inventing it, so a note without one is not a post.

Everything else is framing, and the drafting step supplies it: the argument, the
assumption being overturned, the closing question. Never mark a note down for
missing those.

Set `has_own_evidence` true only when the note contains her own measurements -
stability pulls, batch data, CoAs, returns, percentages she has tested, a
supplier document she has read. A customer anecdote is not owned evidence. A
remembered fact about how skin works is not owned evidence. This flag decides
whether the post is built on her data or on researched sources, so be strict
about it - but it does not affect the score.

The range:
  0-3   no mechanism: a greeting, a task, a reminder, a bare opinion, a fragment
  4-5   a mechanism only half-formed, or a topic with no explanation attached
  6-7   a clear mechanism she can explain, no data of her own behind it
  8-10  a clear mechanism with her own measurements behind it

A conceptual explanation with no numbers in it is a 6 or 7, not a 4. It is a
publishable post that will be built on researched sources.

The reason is one line naming the mechanism you found, or saying what is missing
when there is none."""

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
        # 1-based index of the candidate actually used; 0 for none.
        "used_news_index": {"type": "integer"},
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
5. Put a blank line between paragraphs. The `post` value is a single string and
   paragraphs are separated inside it by two newline characters. A post returned
   as one unbroken block is unusable and will be rejected, however good the
   writing is. Three to six sentences per paragraph.
5b. Length follows the note. 400-700 words is the ceiling for a note carrying a
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
   force it.
8. When a sentence needs a supporting fact you do not have, delete the
   sentence. Do not supply the fact. This is the single rule most often broken,
   because the missing fact is usually easy to guess and the guess is usually
   right - and a right guess she cannot source is still a claim published on
   someone else's authority.
9. Before you return the post, read it back one sentence at a time and ask of
   each: which line of the note is this? If you cannot point at one, cut the
   sentence. A shorter post that survives that check is the correct output.
   Every claim is audited against the note afterwards, so anything invented
   will be found and shown to her.
10. The note is raw material, not an outline. It will often be a plain
   technical dump with no argument in it. Finding the argument is your job:
   the assumption a reader holds that these facts overturn, and the question
   they should ask because of it. Build both out of what is in the note - never
   by adding facts to make an argument work.
11. When the note covers several subjects, choose the one carrying the most
   evidence and write only about that. One mechanism explained properly beats
   three listed. The rest stay in the note for another post."""

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

SOURCES_TEMPLATE = """

─────────────────────────────────
PEER-REVIEWED SOURCES CONSULTED (%d)
From PubMed. Your note had no data of its own, so the post draws on these.
Not all of them necessarily appear in the text:
%s
⚠ Check these before publishing — you are the author of these claims
─────────────────────────────────"""


UNSOURCED_TEMPLATE = """

─────────────────────────────────
⚠ NO SOURCES BEHIND THIS POST
Your note had no measurements of its own, and %s.
Every supporting fact here is the model's, not yours and not a
published source's. Treat the whole post as unverified.
─────────────────────────────────"""


def sources_block(sources):
    if not sources:
        return ""
    lines = "\n".join("%d. %s\n   %s" % (i, s.get("title") or "untitled", s["uri"])
                       for i, s in enumerate(sources[:8], 1))
    return SOURCES_TEMPLATE % (len(sources[:8]), lines)


def score(note_text):
    text, model = gemini.generate(
        "Score this note:\n\n---\n%s\n---" % note_text,
        kind="fast", system=SCORE_SYSTEM, schema=SCORE_SCHEMA, temperature=0.1,
    )
    data = json.loads(text)
    value = max(0, min(10, int(data.get("score", 0))))
    return (value, data.get("reason", "").strip(), model,
            bool(data.get("has_own_evidence")), data.get("mechanism", ""))


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


RESEARCH_SYSTEM = """You find published evidence for a skincare founder's post.

She has a mechanism she wants to write about but no data of her own for it. Find
real, citable support: peer-reviewed findings, regulatory positions, industry
standards, published figures.

Rules:
  - Report only what the search results actually say. If the results do not
    support the mechanism, say so plainly - that is a useful answer.
  - Give figures with their units and context, and say what kind of evidence
    each is: a controlled study, an in-vitro result, a regulatory limit, an
    industry convention.
  - Say when evidence is thin, contested or absent. Do not smooth it over.
  - Never state a figure the results did not give you.

Write 6-10 short factual lines. No prose, no conclusions, no advice."""


QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}

QUERY_SYSTEM = """You turn a founder's note into PubMed search queries.

Two or three queries, each 3-6 words, using the terms a paper would use rather
than the terms a customer would. "stratum corneum lipid barrier", not "why skin
feels tight". Drop brand names, drop her own product names, drop anything
conversational.

Short beats specific. A query with too many qualifiers returns nothing at
all, which is worse than a broad query returning four papers."""


def research(note_text, mechanism=""):
    """Find published evidence for a note with no first-party data.

    Sources come from PubMed, not from Gemini's grounded search: grounding needs
    a search quota this project does not have, and for formulation chemistry
    papers are better evidence than news anyway.

    Returns (findings, sources, model, error). `error` is never swallowed - a
    failed lookup and "no evidence exists" are different facts, and a post built
    on the first has to say so.
    """
    try:
        raw, model = gemini.generate(
            "Note:\n%s\n\nMechanism: %s" % (note_text, mechanism or "see note"),
            kind="fast", system=QUERY_SYSTEM, schema=QUERY_SCHEMA, temperature=0.2)
        queries = json.loads(raw).get("queries", [])[:3]
    except gemini.GeminiError as e:
        return "", [], None, "could not build search queries (%s)" % e

    papers, seen = [], set()
    for q in queries:
        for paper in pubmed.search(q, limit=3):
            if paper["pmid"] not in seen:
                seen.add(paper["pmid"])
                papers.append(paper)
    if not papers:
        return "", [], model, "no papers found on PubMed for %s" % (
            ", ".join(repr(q) for q in queries) or "this note")

    papers = papers[:5]
    texts = pubmed.abstracts([p["pmid"] for p in papers])
    findings = "\n\n".join(
        "[%d] %s (%s, %s)\n%s" % (i, p["title"], p["journal"], p["date"],
                                  texts.get(p["pmid"], "abstract unavailable"))
        for i, p in enumerate(papers, 1))

    sources = [{"title": "%s (%s, %s)" % (p["title"], p["journal"], p["date"]),
                "uri": p["uri"]} for p in papers]
    return findings, sources, model, None


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


def _reparagraph(post, per=4):
    """Last-resort paragraphing when the model returns one block.

    Splits on sentence ends every `per` sentences. Crude, and only ever reached
    when the model ignored the formatting rule - but a readable approximation
    beats a wall of text she has to reformat by hand.
    """
    parts = re.split(r"(?<=[.?!])\s+", post.strip())
    out = ["".join(" ".join(parts[i:i + per])) for i in range(0, len(parts), per)]
    return "\n\n".join(p.strip() for p in out if p.strip())


def _clean(post):
    post = post.strip().replace("\u2014", " - ").replace("\u2013", " - ")
    return re.sub(r" {2,}", " ", post)


def draft(note_text, voice_skill, news_items=None, backend=None, findings=""):
    """Return (post_text, model, news_item_or_None_if_unused)."""
    prompt = ["This note is your only source of facts about her, her company and "
              "her products:\n\n---\n%s\n---" % note_text]
    if findings:
        prompt.append(
            "\nShe has no measurements of her own for this one, so the supporting "
            "facts come from published research below. You may use these, and only "
            "these, for anything the note does not cover. Attribute them as "
            "published findings rather than as hers - she did not run these "
            "studies and must not appear to claim she did.\n\n---\n%s\n---"
            % findings)
    if news_item:
        prompt.append(
            "\nA current news item was found. Use it only if it genuinely sharpens "
            "the argument. A passing mention that an article 'highlighted' "
            "something adds nothing and should be left out - when research "
            "findings are also supplied, prefer those and drop the news item. "
            "If it does not fit naturally, ignore it entirely.\n\n  headline: %s\n  source: %s\n  date: %s\n  summary: %s"
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

    idx = int(data.get("used_news_index") or 0)
    post = _clean(data.get("post", ""))
    if "\n\n" not in post and len(post.split()) > 150:
        # One unbroken block. Her guide is explicit that she never writes this
        # way, so rebuild the paragraphs rather than send it as it stands.
        post = _reparagraph(post)
    used = None
    if data.get("used_news") and 1 <= idx <= len(news_items):
        used = news_items[idx - 1]
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
    value, reason, score_model, own_evidence, mechanism = score(note_text)
    if value < THRESHOLD:
        return {"drafted": False, "score": value, "reason": reason,
                "score_model": score_model}

    from . import store
    voice = store.active_voice_skill()
    if not voice:
        raise RuntimeError("No active voice skill in Supabase. Run scripts/seed_voice.py")

    # With her own measurements the post stands on them. Without, it is built on
    # published research that gets cited, rather than on whatever the drafting
    # model happens to believe.
    findings, sources, research_error = "", [], None
    if not own_evidence:
        findings, sources, _, research_error = research(note_text, mechanism)

    items = news.search(search_phrase(note_text), limit=3)
    post, model, used = draft(note_text, voice, items, findings=findings)

    permitted = findings + ("\n" + json.dumps(used) if used else "")
    claims, claim_model = check_claims(post, note_text + "\n" + permitted, used)
    post += flag_block(claims, bool(used))
    if sources:
        post += sources_block(sources)
    elif not own_evidence:
        post += UNSOURCED_TEMPLATE % (research_error or "no sources found")

    return {"drafted": True, "score": value, "reason": reason,
            "score_model": score_model, "post": post, "model": model,
            "news": used, "claims": claims, "claim_model": claim_model,
            "sources": sources, "own_evidence": own_evidence,
            "research_error": research_error}
