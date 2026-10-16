"""YouTube: a finished explainer video, staged for approval, then uploaded through
YouTube Studio in the owner's own Chrome (the Nova extension).

Why Studio and not the Data API: Google locks every video uploaded through an
unaudited API project to private, so an API upload could never go public. Studio is
the owner's real, signed-in channel, the same page he would use by hand.

A video job (publish/video.py) leaves a package next to the render:

    out/<slug>/youtube.json
    {"project": "AirGated", "video": "...mp4", "thumbnail": "...png",
     "title": "...", "description": "...", "chapters": [["00:00", "The problem"], ...],
     "tags": ["..."], "pinned_comment": "...", "visibility": "public"}

`stage()` turns that into a `youtube_video` draft; nothing is uploaded until the draft
is approved, like every other arm.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

UPLOAD_URL = "https://www.youtube.com/upload"
VISIBILITY = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}
TITLE_MAX, DESCRIPTION_MAX = 100, 5000


class PackageError(ValueError):
    pass


def load(path: str | Path) -> dict[str, Any]:
    """The package, checked: the files exist and YouTube's limits hold."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackageError(f"couldn't read {path}: {error}") from error
    video = Path(str(data.get("video", "")))
    if not video.is_absolute():
        video = path.parent / video
    if not video.is_file():
        raise PackageError(f"the video {video} isn't there")
    thumb = Path(str(data.get("thumbnail") or ""))
    if data.get("thumbnail") and not thumb.is_absolute():
        thumb = path.parent / thumb
    title = " ".join(str(data.get("title", "")).split())
    if not title:
        raise PackageError("the package has no title")
    if len(title) > TITLE_MAX:
        raise PackageError(f"the title is {len(title)} characters; YouTube allows {TITLE_MAX}")
    for bad in "<>":
        if bad in title:
            raise PackageError(f"YouTube refuses '{bad}' in a title")
    visibility = str(data.get("visibility", "public")).lower()
    if visibility not in VISIBILITY:
        raise PackageError(f"visibility must be one of {', '.join(VISIBILITY)}")
    package = {
        "project": str(data.get("project", "")),
        "video": str(video),
        "thumbnail": str(thumb) if data.get("thumbnail") and thumb.is_file() else "",
        "title": title,
        "description": str(data.get("description", "")).strip(),
        "chapters": [[str(a), str(b)] for a, b in (data.get("chapters") or [])],
        "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
        "pinned_comment": str(data.get("pinned_comment", "")).strip(),
        "visibility": visibility,
        "package": str(path),
    }
    if len(full_description(package)) > DESCRIPTION_MAX:
        raise PackageError(f"the description with chapters is over {DESCRIPTION_MAX} characters")
    return package


def full_description(package: dict[str, Any]) -> str:
    """The description as uploaded: chapters go in it, since that is how YouTube finds them."""
    text = package.get("description", "")
    chapters = package.get("chapters") or []
    if chapters and chapters[0][0] not in ("00:00", "0:00"):
        chapters = [["00:00", "Intro"], *chapters]   # YouTube ignores chapters that don't start at zero
    if chapters:
        text += "\n\nChapters\n" + "\n".join(f"{at} {name}" for at, name in chapters)
    return text.strip()


def stage(gate: Any, path: str | Path) -> dict[str, Any]:
    package = load(path)
    media = [package["video"]] + ([package["thumbnail"]] if package["thumbnail"] else [])
    text = f"{package['title']}\n\n{full_description(package)}"
    extra = {**package, "name": package["project"] or package["title"], "alt": "Thumbnail"}
    return gate.stage("youtube_video", text, target="your YouTube channel", media=media, extra=extra)


def sender(draft: dict[str, Any]) -> tuple[bool, str]:
    """Upload through YouTube Studio in the owner's Chrome. Returns (ok, what happened)."""
    from ..automation.extension import BRIDGE, ExtensionBrowser, ExtensionError

    package = draft.get("extra") or {}
    if not package.get("video") or not Path(package["video"]).is_file():
        return False, "The video file for this draft is gone."
    if not BRIDGE.usable():
        return False, "YouTube goes up through your Chrome, and the Nova extension isn't connected."
    browser = ExtensionBrowser()
    step = "opening YouTube Studio"
    try:
        browser.goto(UPLOAD_URL)
        step = "attaching the video"
        browser.upload([package["video"]], selector="input[type=file][name=Filedata], input[type=file]", timeout=60)
        step = "waiting for the details page"
        browser.wait_for(timeout=60, selector="#title-textarea #textbox")
        step = "writing the title"
        browser.fill(package["title"], selector="#title-textarea #textbox")
        step = "writing the description"
        browser.fill(full_description(package), selector="#description-textarea #textbox")
        if package.get("thumbnail"):
            step = "adding the thumbnail"
            browser.upload([package["thumbnail"]], selector="#file-loader, input[type=file][accept*=image]")
        step = "saying it isn't made for kids"
        browser.click(selector="tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]")
        if package.get("tags"):
            step = "adding tags"
            browser.click(selector="#toggle-button")   # "Show more"
            browser.fill(",".join(package["tags"]) + ",", selector="#tags-container input#text-input, ytcp-free-text-chip-bar input")
        for _ in range(3):   # Details -> Video elements -> Checks -> Visibility
            step = "moving to the next page"
            browser.click(selector="#next-button")
            time.sleep(1.5)
        step = f"choosing {package.get('visibility', 'public')}"
        browser.click(selector=f"tp-yt-paper-radio-button[name={VISIBILITY[package.get('visibility', 'public')]}]")
        step = "waiting for the upload to finish"
        _wait_uploaded(browser)
        step = "publishing"
        browser.click(selector="#done-button")
        step = "reading the link"
        browser.wait_for(timeout=60, selector="ytcp-video-info a, a.ytcp-video-info, #share-url")
        link = browser.read(selector="ytcp-video-info a, a.ytcp-video-info, #share-url")
    except ExtensionError as error:
        return False, f"YouTube Studio stopped while {step}: {error}"
    except Exception as error:   # a timeout from the bridge, a page that changed shape
        return False, f"YouTube Studio stopped while {step}: {error}"
    return True, f"Uploaded to YouTube as {package.get('visibility', 'public')}: {package['title']} ({link.strip()})."


def _wait_uploaded(browser: Any, limit: float = 1800) -> None:
    """Studio only publishes once the file is fully up; a long render takes a while."""
    started = time.time()
    while time.time() - started < limit:
        try:
            status = browser.read(selector="ytcp-video-upload-progress .progress-label, .progress-label")
        except Exception:
            status = ""
        low = status.lower()
        if "complete" in low or "processing" in low or "checks" in low or "finished" in low:
            return
        time.sleep(5)
    raise TimeoutError("the upload didn't finish in 30 minutes")
