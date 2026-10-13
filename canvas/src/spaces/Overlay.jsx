import { useEffect, useMemo, useRef, useState } from "react";
import { postJSON, typing } from "./api.js";

// Nova over another app. The still of the monitor the owner was on, ink brackets locked
// onto what Nova read, and a panel that asks, answers and offers numbered choices.
// Esc puts it away; 1–3 sends a choice; the answer is the next thing Nova says.

function union(rects) {
  if (!rects?.length) return null;
  const left = Math.min(...rects.map((r) => r[0]));
  const top = Math.min(...rects.map((r) => r[1]));
  const right = Math.max(...rects.map((r) => r[0] + r[2]));
  const bottom = Math.max(...rects.map((r) => r[1] + r[3]));
  return [left, top, right - left, bottom - top];
}

function appName(process) {
  const name = (process || "the app").replace(/\.exe$/i, "").replace(/^Code$/i, "VS Code").replace(/^WindowsTerminal$/i, "Terminal");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

const MIN_LOCK = 44; // px: a one-line target still gets four distinct corners

function Tracker({ items, left }) {
  if (!items.length) return null;
  return (
    <aside className={`m-panel m-ov-tracker${left ? " left" : ""}`} aria-label="Needs you">
      <h3>
        Needs you <small lang="hi">आपकी बारी</small>
      </h3>
      {items.slice(0, 3).map((item, i) => (
        <div key={item.id} className="m-obj">
          <i className={`m-mark${i === 0 ? " live" : ""}`} />
          <div>
            {item.title}
            <small>{[item.tag, item.at].filter(Boolean).join(" · ")}</small>
          </div>
        </div>
      ))}
    </aside>
  );
}

export default function Overlay({ snapshot }) {
  const overlay = snapshot?.overlay ?? {};
  const context = overlay.context ?? {};
  const [text, setText] = useState("");
  const inputRef = useRef(null);
  const [mx, my, mw, mh] = context.monitor ?? [0, 0, window.innerWidth, window.innerHeight];

  const lock = useMemo(() => {
    const box = union(context.rects) ?? context.focus_rect;
    if (!box || !mw || !mh) return null;
    let [x, y, w, h] = box;
    // Screen pixels per CSS pixel here, so a short target can be padded in real terms.
    const scale = mw / window.innerWidth;
    const min = MIN_LOCK * scale;
    if (h < min) [y, h] = [y - (min - h) / 2, min];
    if (w < min) [x, w] = [x - (min - w) / 2, min];
    const arm = Math.max(10, Math.min(30, (0.35 * Math.min(w, h)) / scale));
    return { left: ((x - mx) / mw) * 100, top: ((y - my) / mh) * 100, width: (w / mw) * 100, height: (h / mh) * 100, arm };
  }, [context.rects, context.focus_rect, mx, my, mw, mh]);
  const items = snapshot?.inbox?.items ?? [];

  useEffect(() => {
    setText("");
    inputRef.current?.focus();
  }, [overlay.rev]);

  const close = () => postJSON("/api/overlay/close", {});
  const ask = (question) => {
    if (!question.trim()) return;
    postJSON("/api/overlay/ask", { text: question });
    setText("");
  };

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (typing(event) || !overlay.answer) return;
      const n = Number(event.key);
      const choice = overlay.choices?.[n - 1];
      if (choice) {
        event.preventDefault();
        ask(choice);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [overlay.answer, overlay.choices]);

  const waiting = overlay.question && !overlay.answer;
  const sfx = overlay.answer ? "GOT IT!" : waiting ? "thinking…" : "looking…";
  const panelAtTop = lock && lock.top + lock.height > 58;
  const where = `${appName(context.process)}${context.title ? ` · ${context.title}` : ""}`;

  return (
    <div className="m-ov" key={overlay.rev}>
      <img className="m-ov-frame" src={`/api/overlay/frame?rev=${overlay.rev ?? 0}`} alt="" aria-hidden="true" />
      <div className="m-ov-dim" aria-hidden="true" onClick={close} />
      {lock ? (
        <div
          className="m-ov-lock"
          style={{ left: `${lock.left}%`, top: `${lock.top}%`, width: `${lock.width}%`, height: `${lock.height}%`, "--arm": `${lock.arm}px` }}
          aria-hidden="true"
        >
          <i />
          <i />
          <i />
          <i />
          <span className="m-caption m-ov-locktab">
            <span className="m-tab">
              Nova is reading · <span lang="hi">पढ़ा जा रहा</span>
            </span>
            {context.has_selection ? "your selection" : appName(context.process)}
          </span>
        </div>
      ) : null}
      <Tracker items={items} left={Boolean(lock && lock.left + lock.width > 70 && lock.top < 35)} />

      <section className={`m-panel splash m-ov-panel${panelAtTop ? " top" : ""}`} role="dialog" aria-label="Nova">
        <div className="m-ov-portrait">
          <span className={`m-orb${waiting ? " live" : ""}`} style={{ width: 70, height: 70 }} aria-hidden="true" />
          <span className="m-sfx">{sfx}</span>
          <span className="m-ov-name">NOVA</span>
        </div>
        <div className="m-ov-body">
          <div className="m-caption">
            <span className="m-tab">
              {context.private ? "Private window · " : context.has_selection ? "Nova read your selection · " : "Looking at · "}
              <span lang="hi">नज़र में</span>
            </span>
            <b>{where.length > 90 ? `${where.slice(0, 89)}…` : where}</b>
            {context.has_selection ? <span className="m-ov-sel">“{context.selection.slice(0, 140)}{context.selection.length > 140 ? "…" : ""}”</span> : null}
            {context.private ? <span className="m-ov-sel">Nothing was read from this window.</span> : null}
          </div>

          {overlay.question ? (
            <div className="m-caption" style={{ marginTop: 22 }}>
              <span className="m-tab">
                You asked · <span lang="hi">आपका सवाल</span>
              </span>
              <b>“{overlay.question}”</b>
            </div>
          ) : null}

          {overlay.answer ? (
            <div className="m-balloon" style={{ marginTop: 14 }}>
              <p className="m-said">{overlay.answer}</p>
            </div>
          ) : null}

          {overlay.answer && overlay.choices?.length ? (
            <div className="m-ov-choices">
              {overlay.choices.map((choice, i) => (
                <button key={choice} type="button" className={`m-ov-choice${i === 0 ? " first" : ""}`} onClick={() => ask(choice)}>
                  <span className="m-key">{i + 1}</span>
                  {choice}
                  {i === 0 ? <span className="hint">say “one”</span> : null}
                </button>
              ))}
            </div>
          ) : null}

          {!waiting ? (
            <form
              className="m-ov-ask"
              onSubmit={(event) => {
                event.preventDefault();
                ask(text);
              }}
            >
              <input
                ref={inputRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={overlay.answer ? "Ask a follow-up…" : "Ask about what's on your screen…"}
                aria-label="Ask Nova"
                autoFocus
              />
              <span className="m-key">Esc</span>
            </form>
          ) : null}
        </div>
      </section>
    </div>
  );
}
