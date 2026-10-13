"""Signing in to LinkedIn once, and keeping the token fresh after that.

The Posts API needs a *member* access token: one that says "this person signed in and
let this app post on their feed". That is LinkedIn's three-legged OAuth, and it only
happens in a browser, so `sign_in()` does the whole dance on this machine:

    1. opens LinkedIn's consent page in your browser,
    2. listens on the redirect URL (localhost) for the code LinkedIn sends back,
    3. trades the code for a token and writes it to data/linkedin_token.json.

That file is git-ignored with the rest of data/, and it is the only place the token
lives. A hand-pasted LINKEDIN_ACCESS_TOKEN in .env still works as a fallback.

Expiry, said plainly because it is what will bite: an access token lasts 60 days.
LinkedIn only hands out refresh tokens to apps it has approved for them, so a
self-serve app usually gets none -- `refresh()` uses one when there is one, and
otherwise `status()` starts warning a week ahead so signing in again is one click
(you are already logged in to LinkedIn, so the consent page goes straight through).

What the app in the developer portal needs:
    * products: "Share on LinkedIn" (w_member_social) and "Sign In with LinkedIn using
      OpenID Connect" (openid, profile -- without these /v2/userinfo refuses, and that is
      how the post's author is found);
    * an authorized redirect URL exactly equal to LINKEDIN_REDIRECT_URI.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from ..paths import APP_DIR

AUTHORIZE = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SCOPES = "openid profile w_member_social"
DEFAULT_REDIRECT = "http://localhost:8766/linkedin/callback"  # 8765 is the canvas
CLIENT_ID_ENV = "LINKEDIN_CLIENT_ID"
CLIENT_SECRET_ENV = "LINKEDIN_CLIENT_SECRET"
REDIRECT_ENV = "LINKEDIN_REDIRECT_URI"
TOKEN_FILE = APP_DIR / "data" / "linkedin_token.json"
WARN_DAYS = 7
TIMEOUT = 30


def client() -> tuple[str, str, str]:
    return ((os.environ.get(CLIENT_ID_ENV) or "").strip(),
            (os.environ.get(CLIENT_SECRET_ENV) or "").strip(),
            (os.environ.get(REDIRECT_ENV) or DEFAULT_REDIRECT).strip())


def can_sign_in() -> bool:
    client_id, secret, _ = client()
    return bool(client_id and secret)


# -- the stored token ----------------------------------------------------------------
def load(path: Path = TOKEN_FILE) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any], path: Path = TOKEN_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _stored(response: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    data = dict(previous or {})
    data.update({
        "access_token": response["access_token"],
        "expires_at": now + int(response.get("expires_in") or 0),
        "scope": response.get("scope", data.get("scope", "")),
        "saved_at": now,
    })
    if response.get("refresh_token"):
        data["refresh_token"] = response["refresh_token"]
        data["refresh_expires_at"] = now + int(response.get("refresh_token_expires_in") or 0)
    return data


def current(path: Path = TOKEN_FILE) -> str:
    """The token to use right now: the stored one (refreshed if it can be), else .env."""
    data = load(path)
    if data.get("access_token"):
        if float(data.get("expires_at") or 0) > time.time() + 60:
            return data["access_token"]
        if data.get("refresh_token"):
            ok, _ = refresh(path)
            if ok:
                return load(path).get("access_token", "")
    return (os.environ.get("LINKEDIN_ACCESS_TOKEN") or "").strip()


def status(path: Path = TOKEN_FILE) -> tuple[bool, str]:
    """One honest sentence about the sign-in, with a warning before it lapses."""
    data = load(path)
    if not data.get("access_token"):
        if (os.environ.get("LINKEDIN_ACCESS_TOKEN") or "").strip():
            return True, "Using the LinkedIn token pasted into .env (its expiry is unknown)."
        if can_sign_in():
            return False, "Not signed in to LinkedIn yet. Run: python main.py linkedin auth"
        return False, (f"LinkedIn isn't set up: put {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} in .env, "
                       "then run: python main.py linkedin auth")
    left = (float(data.get("expires_at") or 0) - time.time()) / 86400
    if left <= 0:
        return False, "The LinkedIn sign-in has expired. Run: python main.py linkedin auth"
    if left < WARN_DAYS and not data.get("refresh_token"):
        return True, f"Signed in to LinkedIn, but it lapses in {left:.0f} days -- sign in again soon."
    return True, f"Signed in to LinkedIn for another {left:.0f} days."


# -- talking to LinkedIn's token endpoint --------------------------------------------
def _token_request(fields: dict[str, str]) -> tuple[bool, Any]:
    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return True, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return False, f"LinkedIn said {error.code}: {error.read().decode('utf-8', 'replace')[:300]}"
    except (OSError, ValueError) as error:
        return False, f"I couldn't reach LinkedIn: {error}"


def refresh(path: Path = TOKEN_FILE) -> tuple[bool, str]:
    data = load(path)
    client_id, secret, _ = client()
    if not data.get("refresh_token"):
        return False, "There is no refresh token; sign in again."
    if not (client_id and secret):
        return False, f"{CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} are needed to refresh."
    ok, response = _token_request({"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                                   "client_id": client_id, "client_secret": secret})
    if not ok:
        return False, response
    _save(_stored(response, data), path)
    return True, "Refreshed the LinkedIn token."


def authorize_url(state: str) -> str:
    client_id, _, redirect = client()
    return AUTHORIZE + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "state": state, "scope": SCOPES})


def sign_in(path: Path = TOKEN_FILE, wait: float = 300, open_browser: bool = True) -> tuple[bool, str]:
    """The whole browser round trip. Blocks until LinkedIn redirects back, or `wait` runs out."""
    client_id, secret, redirect = client()
    if not (client_id and secret):
        return False, f"Put {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} in .env first."
    target = urllib.parse.urlparse(redirect)
    if target.hostname not in ("localhost", "127.0.0.1") or not target.port:
        return False, f"{REDIRECT_ENV} must be a localhost URL with a port, like {DEFAULT_REDIRECT}"

    state = secrets.token_urlsafe(24)
    got: dict[str, str] = {}
    arrived = threading.Event()

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            url = urllib.parse.urlparse(self.path)
            if url.path != target.path:
                self.send_response(404)
                self.end_headers()
                return
            query = dict(urllib.parse.parse_qsl(url.query))
            got.update(query)
            good = query.get("state") == state and "code" in query
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = ("Signed in. You can close this tab and go back to Nova." if good else
                       f"LinkedIn didn't sign you in: {query.get('error_description') or query.get('error') or 'state mismatch'}")
            self.wfile.write(f"<p style='font:16px system-ui;margin:3em'>{message}</p>".encode("utf-8"))
            arrived.set()

        def log_message(self, *_args: Any) -> None:
            pass

    try:
        server = http.server.HTTPServer((target.hostname, target.port), Callback)
    except OSError as error:
        return False, f"Port {target.port} is busy, so I can't hear LinkedIn's reply: {error}"
    threading.Thread(target=server.serve_forever, name="linkedin-auth", daemon=True).start()
    url = authorize_url(state)
    try:
        if open_browser:
            webbrowser.open(url)
        print(f"If no browser opened, visit:\n{url}")
        if not arrived.wait(wait):
            return False, "Nobody finished signing in to LinkedIn in time."
    finally:
        server.shutdown()
        server.server_close()

    if got.get("state") != state:
        return False, "LinkedIn's reply didn't match this sign-in, so I ignored it."
    if "code" not in got:
        return False, f"LinkedIn said no: {got.get('error_description') or got.get('error') or 'no code'}"
    ok, response = _token_request({"grant_type": "authorization_code", "code": got["code"],
                                   "redirect_uri": redirect, "client_id": client_id, "client_secret": secret})
    if not ok:
        return False, response
    _save(_stored(response), path)
    return True, status(path)[1]
