// The masthead: Nova's name, the four spaces (keys 1–4), the Needs-you count, and what
// Nova is doing right now, said as an SFX word from the real status slice.

import Logo from "../Logo.jsx";

export const SPACES = [
  { id: "now", key: "1", en: "Now", hi: "अभी" },
  { id: "work", key: "2", en: "Work", hi: "काम" },
  { id: "studio", key: "3", en: "Studio", hi: "स्टूडियो" },
  { id: "you", key: "4", en: "You", hi: "आप" },
];

const STATE_WORD = {
  idle: "ready",
  listening: "listening…",
  transcribing: "hearing…",
  thinking: "thinking…",
  speaking: "speaking…",
  working: "working…",
  offline: "offline",
};

function LiveLine({ live }) {
  const job = live?.running?.[0];
  const what = job ? job.phase || job.command?.tool?.replace(/_/g, " ") : live?.macro;
  if (!what) return null;
  const target = job && !job.phase ? job.command?.target : "";
  const more = (live?.running?.length ?? 0) - 1;
  return (
    <span className="live-line" title={target ? `${what} on ${target}` : what}>
      {what}
      {target ? <em>{target}</em> : null}
      {more > 0 ? <em>+{more}</em> : null}
    </span>
  );
}

export default function Masthead({ space, onSpace, status, working, count, live, talk, onBriefing }) {
  const state = working && status === "idle" ? "working" : status;
  const busy = state !== "idle" && state !== "offline";
  return (
    <header className="m-mast">
      <span className="m-brand">
        <Logo kind="wordmark" height={40} />
      </span>
      <nav className="m-spaces" aria-label="Spaces">
        {SPACES.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-current={space === s.id ? "page" : undefined}
            onClick={() => onSpace(s.id)}
            title={`${s.en} (press ${s.key})`}
          >
            <span className="m-key" aria-hidden="true">
              {s.key}
            </span>
            <span className="l">
              <b>
                {s.en}
                {s.id === "now" && count > 0 ? (
                  <span className="m-count" aria-label={`${count} waiting on you`}>
                    {count}
                  </span>
                ) : null}
              </b>
              <small lang="hi">{s.hi}</small>
            </span>
          </button>
        ))}
      </nav>
      <span className="grow" />
      {talk?.kind ? (
        <span className="m-stamp dash" title='Say "back to work" to stop'>
          {talk.label}
        </span>
      ) : null}
      <button type="button" className="m-btn" onClick={onBriefing}>
        Briefing
      </button>
      <span className="m-state" aria-live="polite">
        <LiveLine live={live} />
        <span className="m-sfx">{STATE_WORD[state] ?? state}</span>
        <span className={`m-orb${busy ? " live" : ""}`} style={{ width: 36, height: 36 }} aria-hidden="true" />
      </span>
    </header>
  );
}
