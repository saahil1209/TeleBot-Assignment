"""Peer-reviewed sources from PubMed. Free, no key, no account, no quota.

Gemini's grounded search needs a search quota this project does not have, so
evidence for notes with no first-party data comes from here instead. For
formulation chemistry and dermatology this is better than news anyway: the
results are papers, with journals and dates, and every one has a stable public
link she can open and check.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

ESEARCH = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
           "?db=pubmed&retmode=json&retmax={n}&sort=relevance&term={q}")
ESUMMARY = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            "?db=pubmed&retmode=json&id={ids}")
EFETCH = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
          "?db=pubmed&rettype=abstract&retmode=text&id={ids}")
ARTICLE = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
UA = {"User-Agent": "SkinstinctPipeline/1.0 (contact: founder@skinstinct)"}


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get_text(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def abstracts(pmids, limit_chars=1400):
    """Abstract text per pmid. Without these a draft can only say research
    exists on a topic, which is not evidence for anything."""
    if not pmids:
        return {}
    try:
        raw = _get_text(EFETCH.format(ids=",".join(pmids)))
    except Exception:
        return {}
    out, blocks = {}, [b.strip() for b in raw.split("\n\n\n") if b.strip()]
    for pmid, block in zip(pmids, blocks):
        out[pmid] = block[:limit_chars]
    return out


def search(query, limit=4):
    """Return up to `limit` papers: title, journal, date, pmid, uri.

    Returns [] on any failure. The caller distinguishes "no papers" from
    "lookup broke" by checking the exception it gets from `search_or_raise`.
    """
    try:
        return search_or_raise(query, limit)
    except Exception:
        return []


def search_or_raise(query, limit=4):
    if not query or not query.strip():
        return []
    ids_payload = _get(ESEARCH.format(n=limit, q=urllib.parse.quote(query.strip())))
    ids = (ids_payload.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []

    summary = _get(ESUMMARY.format(ids=",".join(ids)))
    result = summary.get("result") or {}
    papers = []
    for pmid in ids:
        item = result.get(pmid)
        if not item:
            continue
        authors = item.get("authors") or []
        first = authors[0].get("name") if authors else ""
        papers.append({
            "title": (item.get("title") or "").rstrip("."),
            "journal": item.get("fulljournalname") or item.get("source") or "",
            "date": item.get("pubdate") or "",
            "author": first + (" et al." if len(authors) > 1 else ""),
            "pmid": pmid,
            "uri": ARTICLE.format(pmid=pmid),
        })
    return papers
