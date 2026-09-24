"""Google News lookup over the public RSS endpoint. No key, no account."""

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
UA = {"User-Agent": "Mozilla/5.0 (compatible; SkinstinctPipeline/1.0)"}


# Aggregators that republish market-research boilerplate. They rank well on
# product keywords and are worthless as a hook - none of them report anything.
JUNK_SOURCES = {
    "indexbox", "openpr", "marketwatch press release", "globenewswire",
    "prnewswire", "einpresswire", "researchandmarkets", "digital journal",
    "market research", "openpr.com", "wicz", "benzinga", "newswire",
    "accesswire", "ad hoc news", "sponsored",
}
JUNK_TITLE_BITS = (
    "market analysis", "market size", "market share", "market report",
    "forecast", "cagr", "industry outlook", "market outlook",
    "market research report", "trends and insights",
    # Supplement-affiliate spam, which ranks well on any product keyword.
    "reviews 2026", "reviews 2025", "does it work", "before you buy",
    "scam or legit", "customer reviews", "shocking", "buyer beware",
)
MAX_AGE_DAYS = 120


def _is_junk(item):
    src = (item.get("source") or "").lower()
    title = (item.get("headline") or "").lower()
    if any(j in src for j in JUNK_SOURCES):
        return True
    return any(bit in title for bit in JUNK_TITLE_BITS)


def _age_days(item):
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(item.get("date") or "")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def search(phrase, limit=5):
    """Several candidates, junk and stale items removed.

    One top result was the old behaviour and it kept returning listicles and
    market-research spam. Handing the drafting model a few real candidates and
    letting it take none is better than forcing it to work with the first hit.
    """
    items = _all_results(phrase)
    out = []
    for item in items:
        if _is_junk(item):
            continue
        age = _age_days(item)
        if age is not None and age > MAX_AGE_DAYS:
            continue
        item["age_days"] = age
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _all_results(phrase):
    """Every item in the feed, parsed. [] on any failure."""
    if not phrase or not phrase.strip():
        return []
    url = RSS.format(q=urllib.parse.quote(phrase.strip()))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            xml = r.read()
        root = ET.fromstring(xml)
    except Exception:
        return []

    out = []
    for item in root.findall("./channel/item"):
        def text(tag, it=item):
            el = it.find(tag)
            return (el.text or "").strip() if el is not None else ""

        headline = text("title")
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if " - " in headline:
            head, tail = headline.rsplit(" - ", 1)
            if not source:
                headline, source = head, tail
            elif tail.strip().lower() == source.lower():
                headline = head
        summary = re.sub(r"<[^>]+>", " ", text("description"))
        summary = re.sub(r"\s+", " ", summary).strip()[:300]
        out.append({"headline": headline.strip(),
                    "source": source.strip() or "Google News",
                    "date": text("pubDate"), "url": text("link"),
                    "summary": summary})
    return out


def top_result(phrase):
    """Best single hit after filtering, or None.

    Returns None rather than a weak match. A post with no news angle is fine;
    an invented or irrelevant one is not.
    """
    results = search(phrase, limit=1)
    return results[0] if results else None


def _legacy_top_result(phrase):
    if not phrase or not phrase.strip():
        return None
    url = RSS.format(q=urllib.parse.quote(phrase.strip()))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            xml = r.read()
    except Exception:
        return None

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    item = root.find("./channel/item")
    if item is None:
        return None

    def text(tag):
        el = item.find(tag)
        return (el.text or "").strip() if el is not None else ""

    headline = text("title")
    # Google appends " - Publication" to the headline.
    source_el = item.find("source")
    source = (source_el.text or "").strip() if source_el is not None else ""
    if " - " in headline:
        head, tail = headline.rsplit(" - ", 1)
        if not source:
            headline, source = head, tail
        elif tail.strip().lower() == source.lower():
            headline = head

    summary = re.sub(r"<[^>]+>", " ", text("description"))
    summary = re.sub(r"\s+", " ", summary).strip()[:300]

    return {
        "headline": headline.strip(),
        "source": source.strip() or "Google News",
        "date": text("pubDate"),
        "url": text("link"),
        "summary": summary,
    }
