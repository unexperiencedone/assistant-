import { useEffect, useMemo, useState } from "react";
import { getJSON } from "./api.js";
import NeedsYou from "./NeedsYou.jsx";

// Studio: everything that goes out under the owner's name. The week, the posted ledger,
// what's waiting, and whether the accounts are signed in. Read from /api/studio, which
// is built straight from the publish gate and the ledger.

const DAYS = [
  ["Mon", "सोम"],
  ["Tue", "मंगल"],
  ["Wed", "बुध"],
  ["Thu", "गुरु"],
  ["Fri", "शुक्र"],
  ["Sat", "शनि"],
  ["Sun", "रवि"],
];
const time = (s) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const date = (s) => new Date(s * 1000).toLocaleDateString([], { day: "numeric", month: "short" });

function weekOf(now) {
  const d = new Date(now);
  const monday = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7));
  return Array.from({ length: 7 }, (_, i) => new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i));
}

export default function Studio({ snapshot, active }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const inboxKey = JSON.stringify((snapshot?.inbox?.items ?? []).map((i) => i.id));

  useEffect(() => {
    let cancelled = false;
    getJSON("/api/studio")
      .then((d) => !cancelled && (setData(d), setError("")))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [inboxKey]);

  const week = useMemo(() => weekOf(Date.now()), []);
  const slots = useMemo(() => {
    const byDay = week.map(() => []);
    const put = (seconds, slot) => {
      const t = new Date(seconds * 1000);
      week.forEach((day, i) => {
        if (t.getFullYear() === day.getFullYear() && t.getMonth() === day.getMonth() && t.getDate() === day.getDate()) byDay[i].push(slot);
      });
    };
    for (const d of data?.scheduled ?? []) put(d.send_at, { cls: "sched", title: d.name || "Post", sub: `${time(d.send_at)} · ${d.media.length} image${d.media.length === 1 ? "" : "s"}` });
    for (const d of data?.recent ?? [])
      if (d.decided_at) put(d.decided_at, { cls: d.status === "sent" ? "posted" : "failed", title: d.name || "Post", sub: d.status === "sent" ? "posted" : "didn't go out" });
    return byDay;
  }, [data, week]);

  if (data && data.enabled === false) {
    return (
      <div className="m-space">
        <p className="m-empty">Publishing is turned off in the config.</p>
      </div>
    );
  }
  const today = new Date();
  const posts = (snapshot?.inbox?.items ?? []).filter((i) => i.kind === "draft" || i.kind === "scheduled");

  return (
    <div className="m-space">
      <div className="m-head">
        <h2>This week on LinkedIn</h2>
        <p lang="hi">इस हफ़्ते</p>
      </div>
      {error ? <p className="m-empty">Couldn't read the studio: {error}</p> : null}
      <div className="m-week">
        {week.map((day, i) => (
          <div key={day.toISOString()} className={`m-panel m-day${day.toDateString() === today.toDateString() ? " today" : ""}`}>
            <div className="d">
              {DAYS[i][0]} {day.getDate()}
              <small lang="hi">{DAYS[i][1]}</small>
            </div>
            {slots[i].map((s, j) => (
              <div key={j} className={`m-slot ${s.cls}`}>
                {s.title}
                <small>{s.sub}</small>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div className="m-grid2">
        <section className="m-panel" style={{ padding: "16px 18px" }}>
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
        <aside style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {posts.length ? <NeedsYou items={posts} active={active} /> : null}
          <div className="m-panel m-arm">
            <b>LinkedIn</b>
            <span className={`m-stamp${data?.linkedin?.ok ? " solid" : " dash"}`} title={data?.linkedin?.detail}>
              {data?.linkedin?.ok ? "Signed in" : "Not signed in"}
            </span>
          </div>
          {data?.linkedin?.detail ? <p style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>{data.linkedin.detail}</p> : null}
        </aside>
      </div>
    </div>
  );
}
