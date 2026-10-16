import { useCallback, useEffect, useMemo, useState } from "react";
import { getJSON, postJSON, typing } from "./api.js";

// Studio: everything that goes out under the owner's name. Every post Nova holds, at any
// date, in one queue; the one picked shows as a proof of exactly what will go out (the
// whole text, the real images, when), with what can still be done to it. The week above
// pages forward, so a post next Monday is never somewhere he can't see. Read from
// /api/studio, built straight from the publish gate and the ledger.

const DAYS = [
  ["Mon", "सोम"],
  ["Tue", "मंगल"],
  ["Wed", "बुध"],
  ["Thu", "गुरु"],
  ["Fri", "शुक्र"],
  ["Sat", "शनि"],
  ["Sun", "रवि"],
];
const GROUPS = [
  { key: "scheduled", title: "Going out", hi: "जाने वाले", stamp: "Scheduled" },
  { key: "pending", title: "Waiting on you", hi: "आपकी बारी", stamp: "Draft" },
  { key: "sent", title: "Went out", hi: "गए", stamp: "Posted" },
  { key: "failed", title: "Didn't go out", hi: "नहीं गए", stamp: "Failed" },
];
// Roughly where LinkedIn's feed cuts a post to "…more" on desktop: the hook has to land before it.
const FOLD = 210;

const time = (s) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const date = (s) => new Date(s * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
const weekday = (s) => DAYS[(new Date(s * 1000).getDay() + 6) % 7][0];
const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

function mondayOf(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7));
}

function weekFrom(monday) {
  return Array.from({ length: 7 }, (_, i) => new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i));
}

function relative(seconds) {
  const days = Math.round((dayStart(seconds) - dayStart(Date.now() / 1000)) / 86400);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

function dayStart(seconds) {
  const d = new Date(seconds * 1000);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000;
}

function localInput(seconds) {
  const d = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function tomorrowAtTen() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, 10, 0).getTime() / 1000;
}

// When a post went out or is due: the time that places it on the week and in the queue.
const placed = (p) => (p.status === "scheduled" ? p.send_at : p.decided_at || p.created_at);

function images(n) {
  return n ? `${n} image${n === 1 ? "" : "s"}` : "text only";
}

const PLATFORM = { linkedin_post: "LinkedIn", youtube_video: "YouTube", instagram_post: "Instagram" };
const isVideo = (p) => p.kind === "youtube_video";
const carries = (p) => (isVideo(p) ? "video" : images(p.media.length));

function fold(text) {
  if (text.length <= FOLD + 40) return [text, ""];
  const cut = text.lastIndexOf(" ", FOLD);
  const at = cut > FOLD * 0.6 ? cut : FOLD;
  return [text.slice(0, at), text.slice(at)];
}

function QueueRow({ post, selected, next, onPick }) {
  const at = placed(post);
  const mark = post.status === "sent" ? " done" : next ? " live" : "";
  return (
    <button type="button" className="m-qrow" aria-pressed={selected} onClick={() => onPick(post.id)}>
      <span className="when">
        {at ? (
          <>
            <b>
              {weekday(at)} {new Date(at * 1000).getDate()}
            </b>
            <small>{post.status === "scheduled" ? time(at) : post.status === "pending" ? "drafted" : date(at)}</small>
          </>
        ) : (
          <b>—</b>
        )}
      </span>
      <span className="what">
        <i className={`m-mark${mark}`} aria-hidden="true" />
        <span>
          {post.name || "Untitled post"}
          <small>
            {post.status === "pending" ? "no time yet" : relative(at)} · {carries(post)}
          </small>
        </span>
      </span>
    </button>
  );
}

function Proof({ post, onDone }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const [when, setWhen] = useState("");
  const [moving, setMoving] = useState(false);

  useEffect(() => {
    setSaid("");
    setMoving(false);
    setWhen(localInput(post?.send_at || tomorrowAtTen()));
  }, [post?.id, post?.send_at]);

  if (!post) {
    return (
      <section className="m-panel splash m-proof">
        <p className="m-empty">Nothing here yet. When Nova drafts a post, it shows up in this space to read before it goes anywhere.</p>
      </section>
    );
  }

  const act = async (action) => {
    setBusy(true);
    const result = await postJSON("/api/inbox/act", { id: `draft:${post.id}`, action }).catch((e) => ({ ok: false, said: String(e) }));
    setSaid(result.said || (result.ok ? "Done." : "That didn't work."));
    setBusy(false);
    onDone();
  };
  const schedule = async (event) => {
    event.preventDefault();
    const at = new Date(when).getTime() / 1000;
    if (!at) return;
    setBusy(true);
    const result = await postJSON("/api/studio/schedule", { id: post.id, at }).catch((e) => ({ ok: false, said: String(e) }));
    setSaid(result.said || (result.ok ? "Scheduled." : "That didn't work."));
    setBusy(false);
    if (result.ok) setMoving(false);
    onDone();
  };

  const group = GROUPS.find((g) => g.key === post.status) ?? GROUPS[1];
  // The fold is LinkedIn's feed; a YouTube description is read on the watch page.
  const [head, rest] = isVideo(post) ? [post.text || "", ""] : fold(post.text || "");
  const at = placed(post);
  const line =
    post.status === "scheduled"
      ? `Goes out ${weekday(at)} ${date(at)} · ${time(at)} · ${relative(at)}`
      : post.status === "pending"
        ? `Drafted ${date(post.created_at)} · not scheduled`
        : post.status === "sent"
          ? `Went out ${weekday(at)} ${date(at)} · ${time(at)}`
          : `Tried ${date(at)} · ${time(at)}`;
  const canSchedule = post.status === "pending" || (post.status === "scheduled" && moving);

  return (
    <section className="m-panel splash m-proof" aria-labelledby="proof-title">
      <div className="top">
        <h3 id="proof-title">{post.name || "Untitled post"}</h3>
        <span className={`m-stamp${post.status === "scheduled" || post.status === "pending" ? " dash" : post.status === "sent" ? " solid" : ""}`}>
          {group.stamp}
        </span>
      </div>
      <div className="ctx">
        {line} · {PLATFORM[post.kind] || "LinkedIn"} · {carries(post)}
      </div>

      {isVideo(post) ? (
        <div className="m-proof-video">
          <video controls preload="metadata" src={`/api/studio/media/${post.id}/0`} poster={post.media[1] ? `/api/studio/media/${post.id}/1` : undefined} />
          {post.youtube?.chapters?.length ? (
            <ol className="m-chapters" aria-label="Chapters">
              {post.youtube.chapters.map(([at, name]) => (
                <li key={at + name}>
                  <span>{at}</span>
                  {name}
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}

      <article className="m-sheet" aria-label="The post as it goes out">
        <p>
          {head}
          {rest ? (
            <>
              <span className="m-fold" title="About where LinkedIn's feed cuts the post to “…more”: the hook has to land before it.">
                feed folds here · …more
              </span>
              {rest}
            </>
          ) : null}
        </p>
      </article>

      {isVideo(post) && post.youtube?.tags?.length ? (
        <p className="m-tags">
          {post.youtube.tags.map((t) => (
            <span key={t}>{t}</span>
          ))}
        </p>
      ) : null}

      {post.media.length && !isVideo(post) ? (
        <div className={`m-proof-media n${Math.min(post.media.length, 4)}`}>
          {post.media.map((name, i) => (
            <a key={name + i} href={`/api/studio/media/${post.id}/${i}`} target="_blank" rel="noreferrer" title={name}>
              <img src={`/api/studio/media/${post.id}/${i}`} alt={post.alt ? `${post.alt} ${i + 1}` : name} loading="lazy" />
            </a>
          ))}
        </div>
      ) : null}

      {post.status === "failed" && post.result ? <p className="m-proof-why">{post.result}</p> : null}

      <div className="acts">
        {post.status === "sent" && post.post ? (
          <a className="m-btn" href={`https://www.linkedin.com/feed/update/${post.post}/`} target="_blank" rel="noreferrer">
            Open on LinkedIn
          </a>
        ) : null}
        {post.status === "scheduled" ? (
          <>
            <button type="button" className="m-btn" disabled={busy} onClick={() => setMoving((m) => !m)} aria-expanded={moving}>
              Move it
            </button>
            <button type="button" className="m-btn" disabled={busy} onClick={() => act("approve")}>
              Post now
            </button>
            <button type="button" className="m-btn" disabled={busy} onClick={() => act("unschedule")}>
              Unschedule
            </button>
          </>
        ) : null}
        {post.status === "pending" ? (
          <>
            <button type="button" className="m-btn" disabled={busy} onClick={() => act("approve")}>
              Post now
            </button>
            <button type="button" className="m-btn danger" disabled={busy} onClick={() => act("drop")}>
              Drop it
            </button>
          </>
        ) : null}
      </div>

      {canSchedule ? (
        <form className="m-when" onSubmit={schedule}>
          <label>
            <span>{post.status === "pending" ? "Send it on" : "Move it to"}</span>
            <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} required />
          </label>
          <button type="submit" className="m-btn spot" disabled={busy}>
            {post.status === "pending" ? "Schedule" : "Move"}
          </button>
        </form>
      ) : null}

      {said ? <p className="m-said-back">{said}</p> : null}
    </section>
  );
}

export default function Studio({ snapshot, active }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [picked, setPicked] = useState(null);
  const [monday, setMonday] = useState(() => mondayOf(new Date()));
  const inboxKey = JSON.stringify((snapshot?.inbox?.items ?? []).map((i) => i.id));

  useEffect(() => {
    let cancelled = false;
    getJSON("/api/studio")
      .then((d) => !cancelled && (setData(d), setError("")))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [inboxKey, tick]);

  const groups = useMemo(() => {
    const scheduled = [...(data?.scheduled ?? [])].sort((a, b) => a.send_at - b.send_at);
    const pending = [...(data?.pending ?? [])].sort((a, b) => b.created_at - a.created_at);
    const recent = [...(data?.recent ?? [])].sort((a, b) => (b.decided_at || 0) - (a.decided_at || 0));
    return {
      scheduled,
      pending,
      sent: recent.filter((p) => p.status === "sent").slice(0, 8),
      failed: recent.filter((p) => p.status === "failed").slice(0, 4),
    };
  }, [data]);
  const order = useMemo(() => GROUPS.flatMap((g) => groups[g.key]), [groups]);
  const post = order.find((p) => p.id === picked) ?? order[0] ?? null;
  const nextId = groups.scheduled[0]?.id;

  const pick = useCallback(
    (id) => {
      setPicked(id);
      const found = order.find((p) => p.id === id);
      const at = found && placed(found);
      if (at) setMonday(mondayOf(new Date(at * 1000)));
    },
    [order],
  );

  // ↑ ↓ walk the queue, the same way 1–4 walk the spaces.
  useEffect(() => {
    if (!active) return undefined;
    const onKey = (event) => {
      if ((event.key !== "ArrowDown" && event.key !== "ArrowUp") || typing(event) || !order.length) return;
      event.preventDefault();
      const i = Math.max(0, order.findIndex((p) => p.id === post?.id));
      const j = Math.min(order.length - 1, Math.max(0, i + (event.key === "ArrowDown" ? 1 : -1)));
      pick(order[j].id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, order, post?.id, pick]);

  const week = useMemo(() => weekFrom(monday), [monday]);
  const slots = useMemo(() => week.map((day) => order.filter((p) => placed(p) && sameDay(new Date(placed(p) * 1000), day))), [week, order]);
  // Scheduled posts the week on screen doesn't reach: named under it, one press away.
  const elsewhere = groups.scheduled.filter((p) => {
    const t = new Date(p.send_at * 1000);
    return t < week[0] || t >= new Date(week[6].getFullYear(), week[6].getMonth(), week[6].getDate() + 1);
  });

  if (data && data.enabled === false) {
    return (
      <div className="m-space">
        <p className="m-empty">Publishing is turned off in the config.</p>
      </div>
    );
  }
  const today = new Date();
  const thisWeek = sameDay(monday, mondayOf(today));
  const shift = (weeks) => setMonday(new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 7 * weeks));
  const counts = [
    groups.scheduled.length && `${groups.scheduled.length} going out`,
    groups.pending.length && `${groups.pending.length} waiting on you`,
  ].filter(Boolean);

  return (
    <div className="m-space m-studio">
      <div className="m-studio-head">
        <div className="m-head">
          <h2>Posts</h2>
          <p lang="hi">पोस्ट</p>
        </div>
        {counts.length ? <span className="m-note">{counts.join(" · ")}</span> : null}
        <div className="m-arm">
          <b>LinkedIn</b>
          <span className={`m-stamp${data?.linkedin?.ok ? " solid" : " dash"}`} title={data?.linkedin?.detail}>
            {data?.linkedin?.ok ? "Signed in" : "Not signed in"}
          </span>
        </div>
      </div>
      {error ? <p className="m-empty">Couldn't read the studio: {error}</p> : null}

      <div className="m-weekbar">
        <h3>
          {thisWeek ? "This week" : `Week of ${date(monday.getTime() / 1000)}`}
          <small lang="hi">{thisWeek ? "इस हफ़्ते" : "हफ़्ता"}</small>
        </h3>
        <div className="nav">
          <button type="button" className="m-btn" onClick={() => shift(-1)} aria-label="Previous week">
            ‹
          </button>
          <button type="button" className="m-btn" onClick={() => setMonday(mondayOf(today))} disabled={thisWeek}>
            Today
          </button>
          <button type="button" className="m-btn" onClick={() => shift(1)} aria-label="Next week">
            ›
          </button>
        </div>
      </div>
      <div className="m-week">
        {week.map((day, i) => (
          <div key={day.toISOString()} className={`m-panel m-day${sameDay(day, today) ? " today" : ""}`}>
            <div className="d">
              {DAYS[i][0]} {day.getDate()}
              <small lang="hi">{DAYS[i][1]}</small>
            </div>
            {slots[i].map((p) => (
              <button
                key={p.id}
                type="button"
                className={`m-slot ${p.status === "scheduled" ? "sched" : p.status === "sent" ? "posted" : p.status === "failed" ? "failed" : ""}`}
                aria-pressed={post?.id === p.id}
                onClick={() => pick(p.id)}
              >
                {p.name || "Post"}
                <small>{p.status === "scheduled" ? `${time(p.send_at)} · ${carries(p)}` : p.status === "sent" ? "posted" : p.status === "pending" ? "draft" : "didn't go out"}</small>
              </button>
            ))}
          </div>
        ))}
      </div>
      {elsewhere.length ? (
        <div className="m-elsewhere">
          <span>Also going out</span>
          {elsewhere.map((p) => (
            <button key={p.id} type="button" className="m-btn" onClick={() => pick(p.id)}>
              {weekday(p.send_at)} {date(p.send_at)} · {p.name || "Post"}
            </button>
          ))}
        </div>
      ) : null}

      <div className="m-posts">
        <nav className="m-panel m-queue" aria-label="Every post">
          <div className="qhead">
            <b>Every post</b>
            <span>
              <span className="m-key">↑</span>
              <span className="m-key">↓</span>
            </span>
          </div>
          {GROUPS.map((g) =>
            groups[g.key].length ? (
              <section key={g.key} aria-label={g.title}>
                <h4>
                  {g.title} <small lang="hi">{g.hi}</small>
                </h4>
                {groups[g.key].map((p) => (
                  <QueueRow key={p.id} post={p} selected={post?.id === p.id} next={p.id === nextId} onPick={pick} />
                ))}
              </section>
            ) : null,
          )}
          {!order.length && data ? <p className="m-empty">No posts yet.</p> : null}
        </nav>
        <Proof post={post} onDone={() => setTick((t) => t + 1)} />
      </div>

      <section className="m-panel m-ledger-panel">
        <div className="m-head">
          <h2>Posted projects</h2>
          <p lang="hi">परियोजनाएँ</p>
        </div>
        {(data?.ledger ?? []).length === 0 ? (
          <p className="m-empty">Nothing recorded yet. Run “python main.py linkedin seed” with your LinkedIn export, or post a project.</p>
        ) : (
          <table className="m-ledger">
            <thead>
              <tr>
                <th>Project</th>
                <th>Status</th>
                <th>When</th>
                <th>Post</th>
              </tr>
            </thead>
            <tbody>
              {data.ledger.map((e) => (
                <tr key={e.key}>
                  <td>
                    <b>{e.name}</b>
                    <small>{e.path}</small>
                  </td>
                  <td>
                    <span className={`m-stamp${e.status === "posted" ? " solid" : ""}`}>{e.status}</span>
                  </td>
                  <td>{e.at ? date(e.at) : "—"}</td>
                  <td>
                    {e.post ? (
                      <a href={`https://www.linkedin.com/feed/update/${e.post}/`} target="_blank" rel="noreferrer">
                        open
                      </a>
                    ) : (
                      <small>{e.source}</small>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
