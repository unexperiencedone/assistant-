# Nova v1: publisher notes

Notes for the first public release. **Not started yet.** These are the decisions and the work list to pick up when the release begins.

## Identity and credit

| | |
|---|---|
| Product name | **Nova** (tagline: "Personal Butler") |
| Publisher, the product's owner | **Kaiketsu Tech**, funded by the freelance work it is built on |
| Developer | **Aakshant Kumar**, co-founder and VP Tech of Kaiketsu Tech |
| Where it is listed | Kaiketsu Tech's website |

- **Publisher field.** Kaiketsu Tech goes everywhere Windows or a store shows a publisher: the exe's file details (CompanyName "Kaiketsu Tech", ProductName "Nova", version 1.0.0), the installer, and the Windows app ID (`Kaiketsu.Nova`).
- **Developer credit.** Aakshant Kumar is credited as the developer, with his role: an About screen ("Nova, a product by Kaiketsu Tech. Developed by Aakshant Kumar, co-founder and VP Tech"), the docs and the listing.
- **Logo.** Use the owner's own design, in `docs/brand/`. Regenerate the variants with `python packaging/make_brand.py`.

## Before listing

1. **Compiled build.** `packaging/build.ps1` produces `Nova.exe`. Nothing the user sees may say Python. The Store Python is a packaged app, so the taskbar shows "Python 3.11" whatever app ID is set; only the compiled exe fixes it. Add version info with Kaiketsu as publisher to `packaging/nova.spec`. The taskbar identity code is in `assistant/ui/identity.py`: the app ID `Kaiketsu.Nova` and a Start Menu shortcut carrying it, set before any window. It is already correct for the exe; under the Store Python it can't override the package identity.
2. **Themes.** Several themes users can choose from, not only Manga × HUD. The tokens already sit at the top of `canvas/src/manga.css`, so a theme is a token set plus a picker (You, or the first-run setup).
3. **User docs.** Install, first run (Connections: sign in to Claude or Antigravity, add a Groq key), the hotkeys (Ctrl+Alt+N for the window, Ctrl+Alt+H to ask about the screen), voice and wake word, and what each space does.
4. **Privacy policy.** A policy for Nova itself, separate from the LinkedIn app's PRIVACY.md. It must cover:
   - **On this PC:** what stays local (memory, activity awareness, voice print, screen reads from the overlay).
   - **Sent to cloud brains:** what goes to Claude, Antigravity, Groq, OpenRouter and Fish Audio, and when.
   - **Keys:** they live in `.env` on the user's PC.
   - **Defaults:** private apps are never read (password managers, banking, incognito).
   - **Opting out:** how to turn things off and delete data.
5. **Tray icon pinned.** Windows 11 hides every new tray icon behind the ^ overflow, so a user who autostarts Nova (it starts hidden, `--tray`) sees nothing at login. The pin is per executable (`HKCU\Control Panel\NotifyIconSettings\<id>\IsPromoted = 1`), so `Nova.exe` gets a new entry. The installer or first run should set it, or the user docs must say how.
6. **Publisher docs.** This file, plus a release checklist: build, smoke test on a clean Windows account, a signed installer if possible, then the listing copy for Kaiketsu's site.

## Already done that the release builds on

- **Logo:** the owner's design traced into SVG variants, app and tray icons, and the masthead (2026-10-14).
- **Connections screen:** You · Connections, for sign-ins and keys, with a first-run prompt when no brain is connected.
- **Ctrl+Alt+H overlay:** fixed and verified on both monitors.
