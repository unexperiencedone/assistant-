import { useCallback, useEffect, useState } from "react";
import { postJSON, typing } from "./api.js";

// One quest per decision waiting on the owner (assistant/inbox.py). The primary action
// of the first three is on F1–F3, the HUD half of the grammar; every button calls the
// same server function the voice path does.

function Quest({ item, index, onAct, busy, said }) {
  const fkey = index < 3 ? `F${index + 1}` : "";
  const primary = item.actions.find((a) => a.primary);
  return (
    <article className="m-panel m-quest" aria-labelledby={`q-${item.id}`}>
      <div className="row">
        <h3 id={`q-${item.id}`}>{item.title}</h3>
        <span className={`m-stamp${item.kind === "scheduled" || item.kind === "automation" ? " dash" : ""}`}>{item.tag}</span>
      </div>
      <div className="ctx">{[item.at, item.detail].filter(Boolean).join(" · ")}</div>
      {item.text ? (
        <details>
          <summary>Read it{item.media?.length ? ` · ${item.media.length} image${item.media.length > 1 ? "s" : ""}` : ""}</summary>
          <p>{item.text}</p>
        </details>
      ) : null}
      {item.actions.length ? (
        <div className="acts">
          {item.actions.map((action) => (
            <button
              key={action.id}
              type="button"
              className={`m-btn${action.primary ? " spot" : ""}`}
              disabled={!action.enabled || busy}
              onClick={() => onAct(item, action.id)}
            >
              {action === primary && fkey ? <span className="m-key">{fkey}</span> : null}
              {action.label}
            </button>
          ))}
        </div>
      ) : null}
      {said ? <p className="m-said-back">{said}</p> : null}
    </article>
  );
}

export default function NeedsYou({ items, active }) {
  const [busy, setBusy] = useState("");
  const [said, setSaid] = useState({});

  const act = useCallback(async (item, action) => {
    setBusy(item.id);
    const result = await postJSON("/api/inbox/act", { id: item.id, action }).catch((error) => ({ ok: false, said: String(error) }));
    setSaid((prev) => ({ ...prev, [item.id]: result.said || (result.ok ? "Done." : "That didn't work.") }));
    setBusy("");
  }, []);

  useEffect(() => {
    if (!active) return undefined;
    const onKey = (event) => {
      const match = /^F([1-3])$/.exec(event.key);
      if (!match || typing(event)) return;
      const item = items[Number(match[1]) - 1];
      const primary = item?.actions.find((a) => a.primary && a.enabled);
      if (!primary) return;
      event.preventDefault();
      act(item, primary.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, items, act]);

  return (
    <aside className="m-rail" aria-label="Needs you">
      <div className="m-head">
        <h2>Needs you{items.length ? ` · ${items.length}` : ""}</h2>
        <p lang="hi">आपकी बारी</p>
      </div>
      {items.length === 0 ? (
        <div className="m-panel" style={{ padding: "16px 18px" }}>
          <p className="m-empty" style={{ margin: 0 }}>
            Nothing is waiting on you. Drafts, scheduled posts and proposed automations land here.
          </p>
        </div>
      ) : (
        items.map((item, i) => <Quest key={item.id} item={item} index={i} onAct={act} busy={busy === item.id} said={said[item.id]} />)
      )}
    </aside>
  );
}
