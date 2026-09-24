"""Optional LinkedIn publishing. Off unless explicitly enabled.

This is the one place in the pipeline that does something irreversible, so it
is deliberately hard to reach: disabled by default, and reachable only by an
explicit PUBLISH command on a draft that is already approved. Nothing here runs
on a schedule and nothing publishes as a side effect of approval.
"""

import json
import re
import urllib.error
import urllib.request

from . import config

POSTS_API = "https://api.linkedin.com/rest/posts"
API_VERSION = "202506"

# Everything from the first box-drawing rule onward is internal annotation -
# the source verify block and the unverified-claim flags. It is written for
# Meera, not for her readers, and must never be posted.
ANNOTATION = re.compile(r"\n*─{5,}.*\Z", re.S)


def enabled():
    return (config.get("ENABLE_LINKEDIN_PUBLISH", "false") or "").lower() in (
        "1", "true", "yes", "on")


def configured():
    return bool(config.get("LINKEDIN_ACCESS_TOKEN") and
                config.get("LINKEDIN_PERSON_URN"))


def post_body(draft_content):
    """Strip internal annotations, leaving only what she actually wrote."""
    return ANNOTATION.sub("", draft_content).strip()


def publish(draft_content):
    """Publish to LinkedIn. Returns the post id. Raises RuntimeError on failure."""
    if not enabled():
        raise RuntimeError("publishing is switched off (ENABLE_LINKEDIN_PUBLISH)")
    token = config.get("LINKEDIN_ACCESS_TOKEN", required=True)
    urn = config.get("LINKEDIN_PERSON_URN", required=True)
    if not urn.startswith("urn:li:"):
        urn = "urn:li:person:%s" % urn

    body = post_body(draft_content)
    if not body:
        raise RuntimeError("nothing left to post once annotations were stripped")

    payload = {
        "author": urn,
        "commentary": body,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    req = urllib.request.Request(
        POSTS_API,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
            "LinkedIn-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            post_id = r.headers.get("x-restli-id") or r.headers.get("X-RestLi-Id")
            return post_id or "published"
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise RuntimeError("LinkedIn rejected the token - it has expired or "
                               "lacks the w_member_social scope")
        raise RuntimeError("LinkedIn returned %s: %s" % (e.code, detail))
