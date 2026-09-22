# Publishing: the arms, and the gate in front of them

Nova can draft a post, a release or a commit. It cannot send one. Everything outward-facing
is staged as a **draft**, said back to you in one line, and goes out only when you approve
that specific draft.

Checked September 2026. Platform rules move; re-check before trusting this.

## The gate (`assistant/publish/gate.py`)

```
Nova drafts  ->  "Drafted a LinkedIn post on your feed: '...'  Nothing has gone out."
you          ->  "post it"   (or "drop it", or nothing at all)
Nova sends   ->  "Posted to LinkedIn."    or    "It didn't go out. LinkedIn said 401: ..."
```

| Property | Why it is there |
|---|---|
| Drafts are SQLite rows, not memory | "I'll approve that in the morning" survives a restart |
| Approval is per draft, never a mode | there is no switch that eventually posts the wrong thing |
| A refusal is recorded as `failed`, with what the platform said | a draft is never called sent because the request was made |
| A sender that throws is a failure, not a send | an exception must not look like success |

Voice: **"what's waiting to go out"**, **"post it"**, **"drop it"**. With no id, those act on
the oldest thing waiting.

## GitHub

Through the `gh` CLI, which carries its own login — no token stored here. Not installed on
this machine yet:

```powershell
winget install --id GitHub.cli -e
gh auth login
```

| Call | Gate? |
|---|---|
| `repos()`, `recent_activity()` | no — reading is free |
| `release(repo, tag, notes)` | yes (`github_release`; a draft with no tag is refused) |
| `commit_and_push(folder, message)` | yes (`github_push`) |
| `update_profile_readme(text, folder)` | yes — writes `README.md` in your `<user>/<user>` checkout, then commits and pushes |

## LinkedIn

**No partner approval needed to post to your own profile.** This is the part most guides get
wrong and it is why LinkedIn is the easier of the two.

1. Create an app in the LinkedIn developer portal.
2. Add the self-serve **Share on LinkedIn** product. The `w_member_social` scope arrives
   without a review conversation — it means "act as the member who signed in, on their own
   feed", which covers posting, commenting and liking.
3. Put the member access token in `.env`:

   ```
   LINKEDIN_ACCESS_TOKEN=...
   ```

Details the API is strict about: posts go to `POST /rest/posts`, with a `LinkedIn-Version`
header in `YYYYMM` form (a version they still support — they cut one monthly and support each
for at least a year) and `X-Restli-Protocol-Version: 2.0.0`. The author is
`urn:li:person:<sub>`, where `sub` comes from `/v2/userinfo`.

Ceilings, not approvals, are what will bite: roughly **100 calls per day per member**, access
tokens expire after **60 days** and refresh tokens after **365**. `linkedin.check()` exists so
an expired token reads as "sign in again" rather than as a mystery.

Text posts only so far. Images and video need the upload endpoints, which are not wired up.

## Instagram

The constraint is the **account**, not the review.

1. The account must be **professional** — Business or Creator. Reels publishing needs
   Business specifically. A personal account cannot publish through the API at all, and no
   amount of code works around that.
2. Create an app in the Meta developer portal. To publish to **your own** account you do not
   need App Review: add your account as an **Instagram Tester** and work in development mode.
   Review (`instagram_business_basic`, `instagram_business_content_publish`, a screencast,
   two to four weeks) only matters if other people ever connect their accounts.
3. Put both values in `.env`:

   ```
   INSTAGRAM_ACCESS_TOKEN=...
   INSTAGRAM_USER_ID=...
   ```

The newer "Instagram API with Instagram Login" drops the old Facebook Page requirement.
Rate limit is 200 calls per user per hour, so the container poll is patient (5 seconds,
up to two minutes) rather than tight.

**The open problem.** Publishing is two calls — create a container pointing at a URL, then
publish that container — and neither takes a local file. **Instagram fetches the media over
the public internet.** A render sitting in the inbox on this laptop cannot be posted until
something has put it somewhere with a URL. That is a hosting decision and it is still open;
until it is made, `instagram.sender` says so plainly rather than pretending to try.

## Files

| File | Does |
|---|---|
| `assistant/publish/gate.py` | drafts, approval, honest recording of what happened |
| `assistant/publish/service.py` | owns the gate, registers the senders, reports which arms work |
| `assistant/publish/github.py` | the `gh` CLI wrapper, plus `git` commit-and-push |
| `assistant/publish/linkedin.py` | Posts API, own feed, text |
| `assistant/publish/instagram.py` | container-then-publish, professional accounts |
| `tests/test_publish.py` | that staging sends nothing, and a failure is never a send |
