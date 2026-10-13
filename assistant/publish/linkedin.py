"""Posting to your own LinkedIn feed, through LinkedIn's own API.

No partner programme and no review conversation: an app in the developer portal with
the self-serve **Share on LinkedIn** product attached gives the `w_member_social` scope,
which is exactly "act as the member who signed in, on their own feed". That is the whole
capability and it is all this needs.

Set up once: LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env, then
`python main.py linkedin auth` (linkedin_auth.py) signs in through the browser and keeps
the token in data/. A hand-pasted LINKEDIN_ACCESS_TOKEN in .env still works as a fallback.

The token is a credential, so it never goes in config.toml. It expires after 60 days,
which is why `check()` exists and why an expired token has to read as "sign in again",
not as a mysterious failure.

Two details the API is strict about and everyone gets wrong once: the `LinkedIn-Version`
header is a YYYYMM string that must be a version LinkedIn still supports, and
`X-Restli-Protocol-Version` is always 2.0.0. A third that only shows up on a real post:
the commentary is "little text", where ( ) [ ] # @ and friends are markup and must be
escaped, or LinkedIn quietly cuts the post at the first one.

Images go up through the Images API first (initializeUpload, then a PUT of the bytes),
and the post refers to the image URNs that come back.

Not yet run against a live account from here. The shapes below follow the current
Posts and Images API documentation; treat the first real post as the test.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import linkedin_auth

API = "https://api.linkedin.com"
# YYYYMM; LinkedIn cuts a new one monthly and supports each for about a year, so this
# has to move forward now and then. LINKEDIN_VERSION in .env overrides it without an edit.
VERSION = "202604"
TOKEN_ENV = "LINKEDIN_ACCESS_TOKEN"
TIMEOUT = 30
MAX_IMAGES = 9          # the API takes up to 20; past a handful a post reads as a dump
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TEXT = 3000         # LinkedIn's limit on commentary
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif")

_urn_cache: dict[str, str] = {}


def token() -> str:
    return linkedin_auth.current()


def configured() -> bool:
    return bool(token())


def version() -> str:
    return (os.environ.get("LINKEDIN_VERSION") or VERSION).strip()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "LinkedIn-Version": version(),
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _call(path: str, payload: dict[str, Any] | None = None, method: str = "") -> tuple[bool, Any]:
    if not configured():
        return False, linkedin_auth.status()[1]
    request = urllib.request.Request(
        f"{API}{path}", headers=_headers(), method=method or ("POST" if payload is not None else "GET"),
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
                           f"(they last 60 days). Sign in again: python main.py linkedin auth. {detail}")
        if error.code == 426:
            return False, (f"LinkedIn no longer supports API version {version()}. Put a newer "
                           f"LINKEDIN_VERSION (YYYYMM) in .env. {detail}")
        return False, f"LinkedIn said {error.code}: {detail}"
    except OSError as error:
        return False, f"I couldn't reach LinkedIn: {error}"


def member_urn() -> tuple[bool, str]:
    """Who the token belongs to. `sub` from the OpenID userinfo endpoint is the member id."""
    key = token()
    if key in _urn_cache:
        return True, _urn_cache[key]
    ok, data = _call("/v2/userinfo")
    if not ok:
        return False, data
    sub = (data or {}).get("sub") if isinstance(data, dict) else ""
    if not sub:
        return False, "LinkedIn didn't say who the token belongs to (is the OpenID Connect product added?)."
    _urn_cache[key] = f"urn:li:person:{sub}"
    return True, _urn_cache[key]


def check() -> tuple[bool, str]:
    """Whether a post would work right now, without posting anything."""
    if not configured():
        return False, linkedin_auth.status()[1]
    ok, detail = member_urn()
    return (True, linkedin_auth.status()[1]) if ok else (False, detail)


# -- little text ---------------------------------------------------------------------
_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~#])")
_HASHTAG = re.compile(r"(?<![\w#])#([A-Za-z]\w{1,60})")


def little_text(text: str) -> str:
    """Escape what the Posts API treats as markup, keeping real hashtags as hashtags."""
    out: list[str] = []
    last = 0
    for match in _HASHTAG.finditer(text):
        out.append(_RESERVED.sub(r"\\\1", text[last:match.start()]))
        out.append("{hashtag|\\#|" + match.group(1) + "}")
        last = match.end()
    out.append(_RESERVED.sub(r"\\\1", text[last:]))
    return "".join(out)


# -- images --------------------------------------------------------------------------
def upload_image(path: str | Path, owner: str) -> tuple[bool, str]:
    """Put one local image on LinkedIn and return its urn:li:image:... id."""
    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError as error:
        return False, f"I couldn't read {file.name}: {error}"
    if len(data) > MAX_IMAGE_BYTES:
        return False, f"{file.name} is over {MAX_IMAGE_BYTES // (1024 * 1024)} MB."
    ok, answer = _call("/rest/images?action=initializeUpload",
                       {"initializeUploadRequest": {"owner": owner}})
    if not ok:
        return False, answer
    value = (answer or {}).get("value") or {}
    upload_url, image = value.get("uploadUrl"), value.get("image")
    if not (upload_url and image):
        return False, "LinkedIn didn't give me somewhere to upload the image."
    kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    request = urllib.request.Request(upload_url, data=data, method="PUT", headers={
        "Authorization": f"Bearer {token()}", "Content-Type": kind})
    try:
        with urllib.request.urlopen(request, timeout=120):
            pass
    except urllib.error.HTTPError as error:
        return False, f"Uploading {file.name} failed ({error.code}): {error.read().decode('utf-8', 'replace')[:200]}"
    except OSError as error:
        return False, f"Uploading {file.name} failed: {error}"
    return True, image


# -- posting -------------------------------------------------------------------------
def post(text: str, visibility: str = "PUBLIC", images: list[str] | None = None,
         alt: str = "") -> tuple[bool, str]:
    """One post on your own feed, text plus optional images. Called only by the gate."""
    ok, author = member_urn()
    if not ok:
        return False, author
    if len(text) > MAX_TEXT:
        return False, f"That post is {len(text)} characters; LinkedIn's limit is {MAX_TEXT}."
    media: list[dict[str, str]] = []
    for path in (images or [])[:MAX_IMAGES]:
        ok, urn = upload_image(path, author)
        if not ok:
            return False, urn    # half a post is worse than none: stop before publishing
        media.append({"id": urn, "altText": (alt or Path(path).stem.replace("_", " "))[:120]})
    payload: dict[str, Any] = {
        "author": author,
        "commentary": little_text(text),
        "visibility": visibility,
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if len(media) == 1:
        payload["content"] = {"media": media[0]}
    elif media:
        payload["content"] = {"multiImage": {"images": media}}
    ok, data = _call("/rest/posts", payload)
    if not ok:
        return False, data
    posted = (data or {}).get("id") if isinstance(data, dict) else ""
    pictures = f" with {len(media)} image{'s' if len(media) != 1 else ''}" if media else ""
    return True, f"Posted to LinkedIn{pictures}{f' ({posted})' if posted else ''}."


def delete_post(urn: str) -> tuple[bool, str]:
    """Remove one of your own posts (w_member_social covers deleting what you posted)."""
    if not urn.startswith("urn:li:"):
        return False, f"That isn't a LinkedIn post id: {urn}"
    ok, detail = _call(f"/rest/posts/{urllib.parse.quote(urn, safe='')}", method="DELETE")
    return (True, f"Deleted the old post ({urn}).") if ok else (False, detail)


def sender(draft: dict[str, Any]) -> tuple[bool, str]:
    """The gate's entry point for a `linkedin_post` draft."""
    media = [str(m) for m in draft.get("media") or []]
    other = [m for m in media if not m.lower().endswith(IMAGE_TYPES)]
    if other:
        return False, ("I can only attach images to LinkedIn so far; video needs the videos "
                       f"upload endpoint, which isn't wired up. Not sent: {Path(other[0]).name}")
    return post(draft["text"], images=media, alt=(draft.get("extra") or {}).get("alt", ""))
