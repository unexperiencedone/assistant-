import { useEffect, useMemo, useRef, useState } from "react";
import PhoneVoice, { usePhoneSpeech } from "./PhoneVoice.jsx";
import RunDetail from "./RunDetail.jsx";
import { useNarrow, usePhone } from "./device.js";

function time(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

function useStickToBottom(dep) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [dep]);
  return ref;
}

/**
 * What was said, and what was done.
 *
 * The lower panel has two views of the same work: Activity is the live stream of
 * events, Detail is the current request written out in full — how many commands ran
 * and exactly what ran. Collapsing it gives the whole column back to Conversation;
 * nothing is thrown away, and History still keeps all of it after the fact.
 */
export default function Sidebar({ snapshot, say, graph, viewRun, leftEdge, mode }) {
  const [text, setText] = useState("");
  const [heard, setHeard] = useState("");   // what the phone's own microphone is picking up
  const phone = usePhone();      // speaks replies out loud
  const narrow = useNarrow();    // shows the microphone
  const [filter, setFilter] = useState("");
  const [full, setFull] = useState(false);
  const [tab, setTab] = useState(mode === "simple" ? "detail" : "activity");
  const [collapsed, setCollapsed] = useState(false);
  const conversation = snapshot?.conversation ?? [];
  const activity = snapshot?.activity ?? [];
  const quick = snapshot?.quick_actions ?? [];

  const rows = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return activity;
    return activity.filter((a) => `${a.tool ?? ""} ${a.kind} ${a.text ?? ""}`.toLowerCase().includes(needle));
  }, [activity, filter]);

  // Simple task hides the plan graph, so the per-command detail becomes the main way
  // to see what ran; Plan mode goes back to the live stream.
  useEffect(() => {
    setTab(mode === "simple" ? "detail" : "activity");
  }, [mode]);

  // Replies meant for this phone are read out here, because the PC stays quiet for them.
  usePhoneSpeech(conversation, phone);

  const convRef = useStickToBottom(conversation.length);
  const feedRef = useStickToBottom(rows.length);

  function submit(event) {
    event.preventDefault();
    say(text);
    setText("");
  }

  return (
    <aside className={`sidebar${collapsed ? " activity-collapsed" : ""}`}>
      {leftEdge}
      <section className="panel conversation">
        <h2>Conversation</h2>
        <div className="messages" ref={convRef}>
          {conversation.length === 0 ? (
            <p className="muted">Speak, or type below.</p>
          ) : (
            conversation.map((m, i) => (
              <div key={i} className={`msg msg-${m.role}`}>
                {m.text}
              </div>
            ))
          )}
        </div>
        <div className="quick">
          {quick.map((item) => (
            <button key={item.command} type="button" title={item.command} onClick={() => say(item.command)}>
              {item.label.length > 28 ? `${item.label.slice(0, 27)}…` : item.label}
            </button>
          ))}
        </div>
        <form className="say" onSubmit={submit}>
          <input
            value={heard || text}
            onChange={(e) => setText(e.target.value)}
            placeholder={narrow ? "Type, or tap the mic" : "Type a request, e.g. plan a notes app"}
            aria-label="Message"
            readOnly={Boolean(heard)}
          />
          {narrow ? <PhoneVoice say={say} onInterim={setHeard} /> : null}
          <button type="submit">Send</button>
        </form>
      </section>

      <section className="panel activity">
        <h2>
          {collapsed ? (
            <span>{tab === "detail" ? "Detail" : "Activity"}</span>
          ) : (
            <span className="panel-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === "activity"}
                className={tab === "activity" ? "on" : ""}
                onClick={() => setTab("activity")}
              >
                Activity
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "detail"}
                className={tab === "detail" ? "on" : ""}
                onClick={() => setTab("detail")}
                title="Every command this request ran, in full"
              >
                Detail
              </button>
            </span>
          )}
          <span className="feed-tools">
            {!collapsed && tab === "activity" ? (
              <>
                <input
                  className="feed-filter"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="filter"
                  aria-label="Filter activity"
                />
                <button type="button" className={full ? "on" : ""} onClick={() => setFull((v) => !v)} title="Show full text">
                  {full ? "full" : "short"}
                </button>
              </>
            ) : null}
            <button
              type="button"
              className="collapse-btn"
              onClick={() => setCollapsed((v) => !v)}
              aria-expanded={!collapsed}
            >
              {collapsed ? "expand" : "collapse"}
            </button>
          </span>
        </h2>

        {collapsed ? null : tab === "detail" ? (
          <RunDetail graph={graph} run={viewRun} />
        ) : (
          <div className={`feed${full ? " feed-full" : ""}`} ref={feedRef}>
            {rows.length === 0 ? (
              <p className="muted">{filter ? `Nothing matches "${filter}".` : "Idle."}</p>
            ) : (
              rows.map((a, i) => (
                <div key={i} className="row">
                  <span className="t">{time(a.ts)}</span>
                  <span className={`k k-${a.kind}`}>{a.tool || a.kind}</span>
                  <span className="b">{a.text}</span>
                </div>
              ))
            )}
          </div>
        )}
      </section>
    </aside>
  );
}
