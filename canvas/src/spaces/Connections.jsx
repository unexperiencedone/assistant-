import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "./api.js";

// Connections: the brains Nova thinks with and the keys it needs, set up by whoever
// installed it. Sign-ins (Claude, Antigravity) belong to their CLIs, so Nova opens the
// CLI's own sign-in; keys (Groq, OpenRouter, Fish Audio) are tried before they are kept,
// and are never shown again past their last four characters (assistant/connections.py).

function stampFor(p) {
  if (p.checking && p.connected == null) return { cls: " dash", text: "checking…" };
  if (p.connected) return { cls: " solid", text: "connected" };
  if (p.connected == null) return { cls: " dash", text: "unknown" };
  if (p.kind === "signin" && p.installed === false) return { cls: " dash", text: "not installed" };
  return { cls: " dash", text: "not connected" };
}

function KeyForm({ p, busy, onSave, onRemove }) {
  const [key, setKey] = useState("");
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="m-conn-acts">
      <form
        className="m-conn-key"
        onSubmit={(event) => {
          event.preventDefault();
          if (key.trim()) onSave(p, key).then((ok) => ok && setKey(""));
        }}
      >
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={p.connected ? `Replace the ${p.name} key` : `Paste your ${p.name} key`}
          aria-label={`${p.name} API key`}
          autoComplete="off"
          spellCheck={false}
        />
        <button type="submit" className={`m-btn${p.connected ? "" : " spot"}`} disabled={busy || !key.trim()}>
          {busy ? "Checking…" : p.connected ? "Replace" : "Connect"}
        </button>
      </form>
      <a className="m-btn" href={p.link} target="_blank" rel="noreferrer">
        Get a key
      </a>
      {p.connected ? (
        confirm ? (
          <button type="button" className="m-btn" disabled={busy} onClick={() => onRemove(p).then(() => setConfirm(false))}>
            Yes, remove it
          </button>
        ) : (
          <button type="button" className="m-btn" disabled={busy} onClick={() => setConfirm(true)}>
            Remove
          </button>
        )
      ) : null}
    </div>
  );
}

function SignIn({ p, busy, onSignIn, onCheck }) {
  if (p.installed === false) {
    return (
      <div className="m-conn-acts">
        <a className="m-btn spot" href={p.link} target="_blank" rel="noreferrer">
          Install {p.name}
        </a>
        <button type="button" className="m-btn" disabled={busy || p.checking} onClick={() => onCheck()}>
          I've installed it
        </button>
      </div>
    );
  }
  return (
    <div className="m-conn-acts">
      <button type="button" className={`m-btn${p.connected ? "" : " spot"}`} disabled={busy} onClick={() => onSignIn(p)}>
        {p.connected ? "Sign in again" : `Sign in to ${p.name}`}
      </button>
      <button type="button" className="m-btn" disabled={busy || p.checking} onClick={() => onCheck()}>
        {p.checking ? "Checking…" : "Check again"}
      </button>
    </div>
  );
}

export default function Connections() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [said, setSaid] = useState({});

  const load = useCallback((refresh = false) => {
    return getJSON(`/api/connections${refresh ? "?refresh=1" : ""}`)
      .then((d) => (setData(d), setError("")))
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load(true);
  }, [load]);

  // Sign-in checks run in the background on the PC: look again until they are back.
  const checking = (data?.providers ?? []).some((p) => p.checking);
  useEffect(() => {
    if (!checking) return undefined;
    const timer = setInterval(() => load(false), 2000);
    return () => clearInterval(timer);
  }, [checking, load]);

  const act = async (p, action, body = {}) => {
    setBusy(p.id);
    const result = await postJSON(`/api/connections/${action}`, { id: p.id, ...body }).catch((e) => ({ ok: false, said: String(e) }));
    if (result.providers) setData({ providers: result.providers, ready: result.ready });
    setSaid((prev) => ({ ...prev, [p.id]: result.said || (result.ok ? "Done." : "That didn't work.") }));
    setBusy("");
    return result.ok;
  };

  const providers = data?.providers ?? [];
  const brains = providers.filter((p) => p.brain);
  const extras = providers.filter((p) => !p.brain);

  const card = (p) => {
    const stamp = stampFor(p);
    return (
      <article key={p.id} className="m-panel m-conn" aria-labelledby={`conn-${p.id}`}>
        <div className="row">
          <h3 id={`conn-${p.id}`}>{p.name}</h3>
          <span className={`m-stamp${stamp.cls}`}>{stamp.text}</span>
        </div>
        <p className="role">{p.role}</p>
        <p className="detail">{p.detail}</p>
        {p.kind === "key" ? (
          <KeyForm p={p} busy={busy === p.id} onSave={(x, key) => act(x, "key", { key })} onRemove={(x) => act(x, "remove")} />
        ) : (
          <SignIn p={p} busy={busy === p.id} onSignIn={(x) => act(x, "signin")} onCheck={() => load(true)} />
        )}
        {said[p.id] ? <p className="m-said-back">{said[p.id]}</p> : null}
      </article>
    );
  };

  return (
    <div className="m-conns">
      <div className="m-head">
        <h2>Connections</h2>
        <p lang="hi">जुड़ाव</p>
      </div>
      {error ? <p className="m-empty">Couldn't read the connections: {error}</p> : null}
      {data && !data.ready ? (
        <div className="m-balloon m-conn-first">
          <p className="m-said">
            Nova needs at least one brain to think with. The quickest start is a free Groq key; sign in to Claude for the big jobs.
          </p>
        </div>
      ) : null}
      <h4 className="m-conn-group">Brains</h4>
      <div className="m-conn-grid">{brains.map(card)}</div>
      {extras.length ? (
        <>
          <h4 className="m-conn-group">Voice</h4>
          <div className="m-conn-grid">{extras.map(card)}</div>
        </>
      ) : null}
      <p className="m-conn-note">
        Keys are kept in the .env file next to config.toml on this PC and are never shown again. Sign-ins stay with Claude Code and
        Antigravity themselves.
      </p>
    </div>
  );
}
