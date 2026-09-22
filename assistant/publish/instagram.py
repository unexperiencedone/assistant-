"""Publishing to your own Instagram account, through the Content Publishing API.

What this actually needs, stated plainly because the internet overstates it:

- A **professional** account. Business or Creator; Reels publishing needs Business
  specifically. A personal account cannot publish through the API at all -- that is a
  setting on the account, not something code can work around.
- An app in the Meta developer portal. To publish to **your own** account you do not
  need App Review: add your account as an Instagram Tester and work in development mode.
  Review (`instagram_business_basic`, `instagram_business_content_publish`, a screencast,
  two to four weeks) only matters if other people ever connect their accounts.
- Two values in .env next to config.toml:

      INSTAGRAM_ACCESS_TOKEN=<long-lived token>
      INSTAGRAM_USER_ID=<the Instagram user id the token is for>

The awkward part, and it shapes the whole capture-and-edit pipeline: **the media has to
be fetchable over the public internet.** Publishing is two calls -- create a container
pointing at a URL, then publish that container -- and neither one takes a local file.
A video sitting in the inbox on this laptop cannot be posted until something has put it
somewhere with a URL. That is a hosting decision, not a coding one, and it is still open.

Rate limit is 200 calls per user per hour, so polling a container's status should be
patient rather than tight.

Not yet run against a live account from here -- there is no token on this machine.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://graph.instagram.com/v23.0"
TOKEN_ENV = "INSTAGRAM_ACCESS_TOKEN"
USER_ENV = "INSTAGRAM_USER_ID"
TIMEOUT = 30
POLL_SECONDS = 5
POLL_TRIES = 24  # video containers are usually ready inside two minutes


def token() -> str:
    return (os.environ.get(TOKEN_ENV) or "").strip()


def user_id() -> str:
    return (os.environ.get(USER_ENV) or "").strip()


def configured() -> bool:
    return bool(token() and user_id())


def _call(path: str, params: dict[str, str], method: str = "POST") -> tuple[bool, Any]:
    if not configured():
        return False, f"Instagram isn't set up: {TOKEN_ENV} and {USER_ENV} need to be in your .env file."
    data = urllib.parse.urlencode({**params, "access_token": token()}).encode("utf-8")
    url = f"{API}{path}"
    request = (urllib.request.Request(url, data=data, method="POST") if method == "POST"
               else urllib.request.Request(f"{url}?{data.decode()}", method="GET"))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return True, json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        if error.code in (400, 401, 403) and "access token" in detail.lower():
            return False, f"Instagram refused the token. It may have expired: {detail}"
        return False, f"Instagram said {error.code}: {detail}"
    except (OSError, ValueError) as error:
        return False, f"I couldn't reach Instagram: {error}"


def check() -> tuple[bool, str]:
    """Whether a post would work right now, without posting anything."""
    if not configured():
        return False, f"Instagram isn't set up: {TOKEN_ENV} and {USER_ENV} need to be in your .env file."
    ok, data = _call(f"/{user_id()}", {"fields": "username,account_type"}, method="GET")
    if not ok:
        return False, data
    kind = (data or {}).get("account_type", "")
    if kind and kind.upper() not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        return False, (f"That account is {kind}. Publishing needs a professional account -- "
                       "switch it to Business in the Instagram app first.")
    return True, f"Instagram is ready ({(data or {}).get('username', 'account')})."


def _container(params: dict[str, str]) -> tuple[bool, str]:
    ok, data = _call(f"/{user_id()}/media", params)
    if not ok:
        return False, data
    creation = (data or {}).get("id", "")
    return (True, creation) if creation else (False, "Instagram didn't return a container id.")


def _ready(creation_id: str) -> tuple[bool, str]:
    """Wait for a video container to finish processing. Images are ready immediately."""
    for _ in range(POLL_TRIES):
        ok, data = _call(f"/{creation_id}", {"fields": "status_code,status"}, method="GET")
        if not ok:
            return False, data
        status = (data or {}).get("status_code", "")
        if status == "FINISHED":
            return True, "ready"
        if status == "ERROR":
            return False, f"Instagram couldn't process the media: {(data or {}).get('status', '')}"
        time.sleep(POLL_SECONDS)
    return False, "The media was still processing after two minutes, so I stopped waiting."


def post(media_url: str, caption: str = "", kind: str = "image") -> tuple[bool, str]:
    """Create a container for a publicly reachable file, then publish it."""
    params = {"caption": caption}
    if kind == "reel":
        params.update({"media_type": "REELS", "video_url": media_url})
    elif kind == "video":
        params.update({"media_type": "VIDEO", "video_url": media_url})
    else:
        params["image_url"] = media_url
    ok, creation = _container(params)
    if not ok:
        return False, creation
    if kind != "image":
        ok, detail = _ready(creation)
        if not ok:
            return False, detail
    ok, data = _call(f"/{user_id()}/media_publish", {"creation_id": creation})
    if not ok:
        return False, data
    return True, f"Posted to Instagram ({(data or {}).get('id', creation)})."


def sender(draft: dict[str, Any]) -> tuple[bool, str]:
    """The gate's entry point for an `instagram_post` draft.

    `extra["media_url"]` must already be a public URL. A local path in `media` is not
    something this can post, and saying so is better than pretending to try.
    """
    url = (draft.get("extra") or {}).get("media_url", "")
    if not url:
        return False, ("Instagram only accepts media it can fetch over the internet, and this "
                       "draft only has a local file. It needs hosting somewhere public first.")
    return post(url, draft["text"], (draft.get("extra") or {}).get("kind", "image"))
