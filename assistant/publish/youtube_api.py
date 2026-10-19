"""YouTube through the Data API v3: sign-in, upload, and setting a video's details exactly.

Sign-in is Google's installed-app OAuth flow: `python main.py youtube auth` opens the
consent page, a one-shot server on localhost catches the code, and the refresh token is
kept in data/youtube_token.json (git-ignored). The client comes from .env:
GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET. Plain HTTP through `requests`, no Google SDK.

Publishing never trusts a write: after the title, description and tags are set, the video
is read back and compared, and anything that doesn't match exactly fails the send with the
difference spelled out, before the video is made public.

One limit is Google's, not ours: videos *uploaded* through an API project that hasn't been
through Google's audit stay private. A video already on the channel (uploaded in Studio) is
found and finished in place instead of uploaded again, which is also why a failed Studio
upload never turns into a duplicate.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any

import requests

from ..paths import APP_DIR

TOKEN_FILE = APP_DIR / "data" / "youtube_token.json"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = "https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.upload"
REDIRECT_PORT = 8767           # 8765 is the canvas, 8766 LinkedIn's sign-in
TIMEOUT = 60


class YouTubeError(RuntimeError):
    pass


# -- sign-in ------------------------------------------------------------------------------
def client() -> tuple[str, str]:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip(), os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def configured() -> bool:
    return all(client())


def load(path: Path = TOKEN_FILE) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any], path: Path = TOKEN_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def signed_in(path: Path = TOKEN_FILE) -> bool:
    return bool(load(path).get("refresh_token"))


def sign_in(path: Path = TOKEN_FILE, wait: float = 300, open_browser: bool = True) -> tuple[bool, str]:
    """The consent page in the browser, the code caught on localhost, the tokens stored."""
    cid, secret = client()
    if not (cid and secret):
        return False, "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET need to be in .env."
    redirect = f"http://127.0.0.1:{REDIRECT_PORT}/"
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    caught: dict[str, str] = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if query.get("state") == state:
                caught.update(query)
            ok = "code" in query and query.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<p>Nova is signed in to YouTube. You can close this tab.</p>" if ok else
                              "<p>That didn't work; go back to Nova and try again.</p>").encode())

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Callback)
    server.timeout = 1
    if open_browser:
        webbrowser.open(url)
    else:
        print(url)
    deadline = time.time() + wait
    while time.time() < deadline and not caught:
        server.handle_request()
    server.server_close()
    if "code" not in caught:
        return False, caught.get("error") or "No answer came back from Google within five minutes."
    response = requests.post(TOKEN_URL, data={
        "code": caught["code"], "client_id": cid, "client_secret": secret, "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier}, timeout=TIMEOUT)
    data = response.json()
    if response.status_code != 200 or "refresh_token" not in data:
        return False, f"Google refused the sign-in: {data.get('error_description') or data.get('error') or response.status_code}"
    _save({"refresh_token": data["refresh_token"], "access_token": data.get("access_token", ""),
           "expires_at": time.time() + float(data.get("expires_in", 0)) - 60}, path)
    return True, "Signed in to YouTube."


def access_token(path: Path = TOKEN_FILE) -> str:
    data = load(path)
    if not data.get("refresh_token"):
        raise YouTubeError("YouTube isn't signed in: run python main.py youtube auth")
    if data.get("access_token") and float(data.get("expires_at", 0)) > time.time():
        return data["access_token"]
    cid, secret = client()
    response = requests.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret,
                                              "refresh_token": data["refresh_token"], "grant_type": "refresh_token"},
                             timeout=TIMEOUT)
    fresh = response.json()
    if response.status_code != 200:
        raise YouTubeError(f"YouTube sign-in has lapsed ({fresh.get('error')}): run python main.py youtube auth")
    data.update(access_token=fresh["access_token"], expires_at=time.time() + float(fresh.get("expires_in", 0)) - 60)
    _save(data, path)
    return data["access_token"]


def _call(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token()}", **kwargs.pop("headers", {})}
    response = requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", TIMEOUT), **kwargs)
    if response.status_code >= 400:
        try:
            error = response.json().get("error", {})
            why = error.get("message") or error.get("errors", [{}])[0].get("reason") or response.text[:200]
        except ValueError:
            why = response.text[:200]
        raise YouTubeError(f"YouTube said {response.status_code}: {why}")
    return response.json() if response.content else {}


# -- the channel ------------------------------------------------------------------------
def my_uploads(limit: int = 50) -> list[dict[str, Any]]:
    """The channel's videos, newest first, private and unlisted included."""
    channel = _call("GET", f"{API}/channels", params={"part": "contentDetails", "mine": "true"})
    items = channel.get("items") or []
    if not items:
        raise YouTubeError("This Google account has no YouTube channel.")
    playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    listed = _call("GET", f"{API}/playlistItems", params={"part": "contentDetails", "playlistId": playlist,
                                                          "maxResults": min(50, limit)})
    ids = [i["contentDetails"]["videoId"] for i in listed.get("items", [])]
    return get_videos(ids) if ids else []


def get_videos(ids: list[str]) -> list[dict[str, Any]]:
    found = _call("GET", f"{API}/videos", params={"part": "snippet,status", "id": ",".join(ids)})
    return found.get("items", [])


def _plain(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").lower().split())


def find_existing(package: dict[str, Any]) -> dict[str, Any] | None:
    """A video already on the channel for this package: named by its file (what Studio calls
    an upload it hasn't been given a title for) or already carrying the package's title."""
    if package.get("video_id"):
        found = get_videos([package["video_id"]])
        return found[0] if found else None
    names = {_plain(Path(package["video"]).stem), _plain(package["title"])}
    for video in my_uploads():
        if _plain(video["snippet"].get("title", "")) in names:
            return video
    return None


# -- writing ----------------------------------------------------------------------------
def upload(package: dict[str, Any], description: str) -> str:
    """A resumable upload of the file; returns the new video's id. (Private until audited.)"""
    path = Path(package["video"])
    meta = {"snippet": {"title": package["title"], "description": description, "tags": package["tags"],
                        "categoryId": "28"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    start = requests.post(f"{UPLOAD}/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                          headers={"Authorization": f"Bearer {access_token()}",
                                   "X-Upload-Content-Type": "video/mp4",
                                   "X-Upload-Content-Length": str(path.stat().st_size)},
                          json=meta, timeout=TIMEOUT)
    if start.status_code >= 400 or "Location" not in start.headers:
        raise YouTubeError(f"YouTube wouldn't start the upload ({start.status_code}): {start.text[:200]}")
    with path.open("rb") as fh:
        done = requests.put(start.headers["Location"], data=fh, headers={"Content-Type": "video/mp4"},
                            timeout=60 * 60)
    if done.status_code >= 400:
        raise YouTubeError(f"The upload failed ({done.status_code}): {done.text[:200]}")
    return done.json()["id"]


def set_details(video: dict[str, Any], package: dict[str, Any], description: str) -> None:
    snippet = dict(video["snippet"])
    snippet.update(title=package["title"], description=description, tags=package["tags"],
                   categoryId=snippet.get("categoryId") or "28")
    for key in ("thumbnails", "localized", "channelTitle", "publishedAt", "channelId", "liveBroadcastContent"):
        snippet.pop(key, None)
    _call("PUT", f"{API}/videos", params={"part": "snippet"}, json={"id": video["id"], "snippet": snippet})


def set_thumbnail(video_id: str, image: str) -> None:
    data = Path(image).read_bytes()
    kind = "image/png" if image.lower().endswith(".png") else "image/jpeg"
    _call("POST", f"{UPLOAD}/thumbnails/set", params={"videoId": video_id}, data=data,
          headers={"Content-Type": kind})


def set_visibility(video: dict[str, Any], privacy: str) -> None:
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
              "embeddable": video.get("status", {}).get("embeddable", True)}
    _call("PUT", f"{API}/videos", params={"part": "status"}, json={"id": video["id"], "status": status})


def differences(video: dict[str, Any], package: dict[str, Any], description: str) -> list[str]:
    """What the video says that the package doesn't. Empty means it's exactly right."""
    snip = video["snippet"]
    wrong = []
    if snip.get("title", "") != package["title"]:
        wrong.append(f"title is {snip.get('title')!r}")
    if " ".join(snip.get("description", "").split()) != " ".join(description.split()):
        wrong.append("the description doesn't match")
    # YouTube hands tags back sorted, so the set is what has to match, not the order.
    have, want = set(snip.get("tags", [])), set(package["tags"])
    if have != want:
        missing, extra = sorted(want - have), sorted(have - want)
        wrong.append("tags differ" + (f" (missing: {', '.join(missing)})" if missing else "")
                     + (f" (unexpected: {', '.join(extra)})" if extra else ""))
    return wrong


def publish(package: dict[str, Any], description: str) -> tuple[bool, str]:
    """Finish the video on the channel (or upload it), set every detail, check them, then set
    its visibility. Returns (ok, what happened)."""
    video = find_existing(package)
    fresh = video is None
    if fresh:
        video = get_videos([upload(package, description)])[0]
    set_details(video, package, description)
    if package.get("thumbnail"):
        set_thumbnail(video["id"], package["thumbnail"])
    video = get_videos([video["id"]])[0]
    wrong = differences(video, package, description)
    if wrong:
        return False, f"The details didn't come back right, so it stays {video['status']['privacyStatus']}: {'; '.join(wrong)}."
    want = package.get("visibility", "public")
    try:
        set_visibility(video, want)
    except YouTubeError as error:
        if fresh:
            return False, (f"Uploaded with every detail right, but YouTube keeps it private until the Google "
                           f"project is audited ({error}). Make it public in Studio.")
        raise
    video = get_videos([video["id"]])[0]
    now = video["status"]["privacyStatus"]
    link = f"https://youtu.be/{video['id']}"
    if now != want:
        return False, f"Every detail is right, but YouTube left it {now} instead of {want}: {link}"
    return True, f"On YouTube as {now}, title, description, chapters and {len(package['tags'])} tags checked: {link}"
