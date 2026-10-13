import Awareness from "../Awareness.jsx";
import Memory from "../Memory.jsx";
import Profile from "../Profile.jsx";
import Usage from "../Usage.jsx";

// You: what Nova knows about you. The existing screens, one at a time, under one space.

export const YOU_TABS = [
  { id: "memory", en: "Memory", hi: "याद" },
  { id: "awareness", en: "Activity", hi: "गतिविधि" },
  { id: "profile", en: "Profile", hi: "परिचय" },
  { id: "usage", en: "Usage", hi: "खर्च" },
];

export default function You({ tab, onTab, snapshot, onClose }) {
  return (
    <div className="m-space">
      <div className="m-subtabs" role="toolbar" aria-label="You">
        {YOU_TABS.map((t) => (
          <button key={t.id} type="button" aria-pressed={tab === t.id} onClick={() => onTab(t.id)}>
            {t.en} · <span lang="hi">{t.hi}</span>
          </button>
        ))}
      </div>
      {tab === "profile" ? (
        <Profile onClose={onClose} revision={snapshot?.profile?.revision} />
      ) : tab === "awareness" ? (
        <Awareness onClose={onClose} revision={snapshot?.awareness?.revision} />
      ) : tab === "usage" ? (
        <Usage onClose={onClose} revision={snapshot?.usage?.revision} />
      ) : (
        <Memory onClose={onClose} revision={snapshot?.afterthought?.revision} />
      )}
    </div>
  );
}
