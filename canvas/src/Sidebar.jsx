import { useEffect, useRef, useState } from "react";

const QUICK = [
  ["Status", "status"],
  ["Run plan", "go ahead"],
  ["Read plan", "read the plan"],
  ["Cancel task", "cancel the task"],
  ["Use Claude", "use claude"],
  ["Use Antigravity", "use antigravity"],
];

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

export default function Sidebar({ snapshot, say }) {
  const [text, setText] = useState("");
  const conversation = snapshot?.conversation ?? [];
  const activity = snapshot?.activity ?? [];
  const convRef = useStickToBottom(conversation.length);
  const feedRef = useStickToBottom(activity.length);

  function submit(event) {
    event.preventDefault();
    say(text);
    setText("");
  }

  return (
    <aside className="sidebar">
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
          {QUICK.map(([label, command]) => (
            <button key={label} type="button" onClick={() => say(command)}>
              {label}
            </button>
          ))}
        </div>
        <form className="say" onSubmit={submit}>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type a request, e.g. plan a notes app"
            aria-label="Message"
          />
          <button type="submit">Send</button>
        </form>
      </section>

      <section className="panel activity">
        <h2>Activity</h2>
        <div className="feed" ref={feedRef}>
          {activity.length === 0 ? (
            <p className="muted">Idle.</p>
          ) : (
            activity.map((a, i) => (
              <div key={i} className="row">
                <span className="t">{time(a.ts)}</span>
                <span className={`k k-${a.kind}`}>{a.tool || a.kind}</span>
                <span className="b">{a.text}</span>
              </div>
            ))
          )}
        </div>
      </section>
    </aside>
  );
}
