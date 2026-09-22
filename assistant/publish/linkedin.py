"""Posting to your own LinkedIn feed, through LinkedIn's own API.

No partner programme and no review conversation: an app in the developer portal with
the self-serve **Share on LinkedIn** product attached gives the `w_member_social` scope,
which is exactly "act as the member who signed in, on their own feed". That is the whole
capability and it is all this needs.

Set up once, in .env next to config.toml:

    LINKEDIN_ACCESS_TOKEN=<the member access token>

The token is a credential, so it never goes in config.toml. It expires after 60 days
(the refresh token after 365), which is why `check()` exists and why an expired token
has to read as "sign in again", not as a mysterious failure.

Two details the API is strict about and everyone gets wrong once: the `LinkedIn-Version`
header is a YYYYMM string that must be a version LinkedIn still supports, and
`X-Restli-Protocol-Version` is always 2.0.0.

Not yet run against a live account from here -- there is no token on this machine. The
shapes below follow the current Posts API documentation; treat the first real post as
the test.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

API = "https://api.linkedin.com"
VERSION = "202604"  # YYYYMM; LinkedIn cuts a new one monthly and supports each for a year
TOKEN_ENV = "LINKEDIN_ACCESS_TOKEN"
TIMEOUT = 30


def token() -> str:
    return (os.environ.get(TOKEN_ENV) or "").strip()


def configured() -> bool:
    return bool(token())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "LinkedIn-Version": VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _call(path: str, payload: dict[str, Any] | None = None) -> tuple[bool, Any]:
    if not configured():
        return False, f"No LinkedIn token: put {TOKEN_ENV} in your .env file."
    request = urllib.request.Request(
        f"{API}{path}", headers=_headers(), method="POST" if payload is not None else "GET",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace")
            created = response.headers.get("x-restli-id") or response.headers.get("x-linkedin-id") or ""
            try:
                return True, {"id": created, **(json.loads(body) if body.strip() else {})}
            except ValueError:
                return True, {"id": created, "body": body[:500]}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        if error.code in (401, 403):
            return False, ("LinkedIn refused the token -- it has most likely expired "
                           f"(they last 60 days). Sign in again and update {TOKEN_ENV}. {detail}")
        return False, f"LinkedIn said {error.code}: {detail}"
    except OSError as error:
        return False, f"I couldn't reach LinkedIn: {error}"


def member_urn() -> tuple[bool, str]:
    """Who the token belongs to. `sub` from the OpenID userinfo endpoint is the member id."""
    ok, data = _call("/v2/userinfo")
    if not ok:
        return False, data
    sub = (data or {}).get("sub") if isinstance(data, dict) else ""
    return (True, f"urn:li:person:{sub}") if sub else (False, "LinkedIn didn't say who the token belongs to.")


def check() -> tuple[bool, str]:
    """Whether a post would work right now, without posting anything."""
    if not configured():
        return False, f"No LinkedIn token: put {TOKEN_ENV} in your .env file."
    ok, detail = member_urn()
    return (True, "LinkedIn is ready.") if ok else (False, detail)


def post(text: str, visibility: str = "PUBLIC") -> tuple[bool, str]:
    """One text post on your own feed. Called only by the gate, never by a request."""
    ok, author = member_urn()
    if not ok:
        return False, author
    payload = {
        "author": author,
        "commentary": text,
        "visibility": visibility,
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    ok, data = _call("/rest/posts", payload)
    if not ok:
        return False, data
    posted = (data or {}).get("id") if isinstance(data, dict) else ""
    return True, f"Posted to LinkedIn{f' ({posted})' if posted else ''}."


def sender(draft: dict[str, Any]) -> tuple[bool, str]:
    """The gate's entry point for a `linkedin_post` draft."""
    if draft.get("media"):
        return False, ("I can only post text to LinkedIn so far. Images and video need the "
                       "images/videos upload endpoints, which aren't wired up yet.")
    return post(draft["text"])
