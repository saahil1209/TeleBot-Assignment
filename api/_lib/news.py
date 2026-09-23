"""Google News lookup over the public RSS endpoint. No key, no account."""

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
UA = {"User-Agent": "Mozilla/5.0 (compatible; SkinstinctPipeline/1.0)"}


def top_result(phrase):
    """Return {headline, source, date, url, summary} for the top hit, or None.

    Returns None rather than a weak match when nothing comes back. A post with
    no news angle is fine; an invented one is not.
    """
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
