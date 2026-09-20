import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// The profile editor. Its form is drawn from the schema the server sends
// (assistant/profile/schema.py), so a field added there shows up here without UI work.

const PREVIEW_TABS = [
  { key: "trusted", label: "Claude & Antigravity" },
  { key: "all", label: "Free cloud models" },
  { key: "speech", label: "Speech words" },
];

async function api(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

const post = (path, body, method = "POST") =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function defaultFor(field) {
  if (field.type === "tags" || field.type === "list") return [];
  if (field.type === "bool") return field.default ?? false;
  if (field.type === "select") return field.default ?? field.options[0];
  return "";
}

function emptyItem(fields) {
  return Object.fromEntries(fields.map((f) => [f.key, defaultFor(f)]));
}

/** How much of a section is filled in, for the navigation. */
function filled(section, value) {
  if (!value) return 0;
  return section.fields.reduce((count, field) => {
    const v = value[field.key];
    if (field.type === "list") return count + v.length;
    if (field.type === "bool") return count;
    return count + (Array.isArray(v) ? (v.length ? 1 : 0) : v ? 1 : 0);
  }, 0);
}

/* ---------------------------------------------------------------- fields */

function TagsInput({ value, onChange, placeholder, id }) {
  const [text, setText] = useState("");
  const add = (raw) => {
    const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
    const known = new Set(value.map((v) => v.toLowerCase()));
    const fresh = parts.filter((p) => !known.has(p.toLowerCase()));
    if (fresh.length) onChange([...value, ...fresh]);
    setText("");
  };
  return (
    <div className="tags">
      {value.map((tag, i) => (
        <span key={`${tag}-${i}`} className="tag">
          {tag}
          <button type="button" aria-label={`Remove ${tag}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
            ×
          </button>
        </span>
      ))}
      <input
        id={id}
        value={text}
        placeholder={value.length ? "" : placeholder || "Type and press Enter"}
        onChange={(e) => (e.target.value.includes(",") ? add(e.target.value) : setText(e.target.value))}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (text.trim()) add(text);
          } else if (e.key === "Backspace" && !text && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => text.trim() && add(text)}
      />
    </div>
  );
}

function Field({ field, value, onChange, idPrefix }) {
  const id = `${idPrefix}-${field.key}`;
  let control;
  switch (field.type) {
    case "textarea":
      control = (
        <textarea id={id} rows={3} value={value} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />
      );
      break;
    case "select":
      control = (
        <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
          {field.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      );
      break;
    case "bool":
      return (
        <label className="field field-bool" htmlFor={id}>
          <input id={id} type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
          <span>
            {field.label}
            {field.help ? <small>{field.help}</small> : null}
          </span>
        </label>
      );
    case "tags":
      control = <TagsInput id={id} value={value} onChange={onChange} placeholder={field.placeholder} />;
      break;
    case "list":
      return <ListField field={field} value={value} onChange={onChange} idPrefix={id} />;
    default:
      control = <input id={id} value={value} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <div className="field">
      <label htmlFor={id}>{field.label}</label>
      {control}
      {field.help ? <small>{field.help}</small> : null}
    </div>
  );
}

function itemSubtitle(item) {
  return [item.status, item.level, item.relation, item.context].filter(Boolean).join(" · ");
}

function ListField({ field, value, onChange, idPrefix }) {
  const [open, setOpen] = useState(null);
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const shown = value
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => !needle || JSON.stringify(item).toLowerCase().includes(needle));

  const update = (index, key, v) => onChange(value.map((item, i) => (i === index ? { ...item, [key]: v } : item)));
  const remove = (index) => {
    onChange(value.filter((_, i) => i !== index));
    setOpen(null);
  };
  const move = (index, delta) => {
    const to = index + delta;
    if (to < 0 || to >= value.length) return;
    const next = [...value];
    [next[index], next[to]] = [next[to], next[index]];
    onChange(next);
    setOpen(to);
  };
  const add = () => {
    onChange([...value, emptyItem(field.fields)]);
    setOpen(value.length);
    setQuery("");
  };

  return (
    <div className="list-field">
      {value.length > 6 ? (
        <input
          className="list-search"
          type="search"
          value={query}
          placeholder={`Search ${value.length} ${field.item_label}s`}
          onChange={(e) => setQuery(e.target.value)}
        />
      ) : null}
      {shown.map(({ item, index }) => {
        const expanded = open === index;
        const missingName = field.fields.some((f) => f.required && !item[f.key]);
        return (
          <div key={index} className={`item${expanded ? " open" : ""}${missingName ? " incomplete" : ""}`}>
            <button type="button" className="item-head" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : index)}>
              <span className="item-name">{item.name || `New ${field.item_label}`}</span>
              <span className="item-sub">{itemSubtitle(item)}</span>
              <span className="item-caret">{expanded ? "−" : "+"}</span>
            </button>
            {expanded ? (
              <div className="item-body">
                {field.fields.map((sub) => (
                  <Field
                    key={sub.key}
                    field={sub}
                    value={item[sub.key]}
                    idPrefix={`${idPrefix}-${index}`}
                    onChange={(v) => update(index, sub.key, v)}
                  />
                ))}
                {missingName ? <p className="warn">Needs a name, or it won't be saved.</p> : null}
                <div className="item-actions">
                  <button type="button" onClick={() => move(index, -1)} disabled={index === 0}>
                    Move up
                  </button>
                  <button type="button" onClick={() => move(index, 1)} disabled={index === value.length - 1}>
                    Move down
                  </button>
                  <button type="button" className="danger" onClick={() => remove(index)}>
                    Remove {field.item_label}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        );
      })}
      {needle && !shown.length ? <p className="muted">Nothing matches "{query}".</p> : null}
      <button type="button" className="add" onClick={add}>
        + Add {field.item_label}
      </button>
    </div>
  );
}

function Visibility({ options, value, onChange, section }) {
  const current = options.find((o) => o.value === value);
  return (
    <div className="visibility">
      <span className="visibility-label">Who can see this</span>
      <div className="segmented" role="radiogroup" aria-label={`Who can see ${section}`}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={value === o.value}
            className={value === o.value ? "on" : ""}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {current ? <small>{current.help}</small> : null}
    </div>
  );
}

/* ---------------------------------------------------------------- editor */

export default function Profile({ onClose, revision }) {
  const [doc, setDoc] = useState(null);            // {schema, profile, preview} as last loaded or saved
  const [draft, setDraft] = useState(null);
  const [preview, setPreview] = useState(null);
  const [active, setActive] = useState("identity");
  const [tab, setTab] = useState("trusted");
  const [state, setState] = useState("loading");   // loading | ready | saving | saved | error
  const [error, setError] = useState("");
  const [confirmClose, setConfirmClose] = useState(false);
  const formRef = useRef(null);

  const dirty = useMemo(
    () => Boolean(doc && draft && JSON.stringify(doc.profile) !== JSON.stringify(draft)),
    [doc, draft],
  );

  const load = useCallback(async () => {
    try {
      const loaded = await api("/api/profile");
      setDoc(loaded);
      setDraft(loaded.profile);
      setPreview(loaded.preview);
      setState("ready");
    } catch (e) {
      setError(`Couldn't load the profile: ${e.message}`);
      setState("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Saved somewhere else (another window): pick it up, unless you're mid-edit.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    if (doc && revision != null && revision !== doc.revision && !dirtyRef.current) load();
  }, [revision, doc, load]);

  // What agents would be told, recomputed as you type.
  useEffect(() => {
    if (!draft || !dirty) {
      if (doc) setPreview(doc.preview);
      return undefined;
    }
    const id = setTimeout(() => {
      post("/api/profile/preview", { profile: draft }).then(setPreview).catch(() => {});
    }, 350);
    return () => clearTimeout(id);
  }, [draft, dirty, doc]);

  const save = useCallback(async () => {
    if (!draft || state === "saving") return;
    setState("saving");
    try {
      const saved = await post("/api/profile", { profile: draft }, "PUT");
      setDoc(saved);
      setDraft(saved.profile);
      setPreview(saved.preview);
      setState("saved");
      setError("");
    } catch (e) {
      setError(`Couldn't save: ${e.message}`);
      setState("error");
    }
  }, [draft, state]);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (dirty) save();
      } else if (e.key === "Escape" && !dirty) {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, save, onClose]);

  useEffect(() => {
    if (state !== "saved") return undefined;
    const id = setTimeout(() => setState("ready"), 1800);
    return () => clearTimeout(id);
  }, [state]);

  const close = () => (dirty ? setConfirmClose(true) : onClose());

  if (!doc || !draft) {
    return (
      <div className="profile">
        <div className="profile-loading">{error || "Loading your profile…"}</div>
      </div>
    );
  }

  const sections = doc.schema.sections;
  const section = sections.find((s) => s.key === active) ?? sections[0];
  const setField = (key, value) => setDraft((d) => ({ ...d, [section.key]: { ...d[section.key], [key]: value } }));
  const previewText = preview?.[tab] ?? "";

  return (
    <div className="profile">
      <header className="profile-bar">
        <div>
          <h2>Profile</h2>
          <p className="muted">
            Saved on this PC only (data/profile.json). Each section decides which agents may read it.
          </p>
        </div>
        <div className="profile-bar-actions">
          <span className={`save-state save-${state}`} role="status">
            {state === "saving" ? "Saving…" : state === "saved" ? "Saved" : dirty ? "Unsaved changes" : ""}
          </span>
          {confirmClose ? (
            <>
              <span className="muted">Discard your changes?</span>
              <button type="button" onClick={() => setConfirmClose(false)}>
                Keep editing
              </button>
              <button type="button" className="danger" onClick={onClose}>
                Discard
              </button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => setDraft(doc.profile)} disabled={!dirty}>
                Undo changes
              </button>
              <button type="button" className="primary" onClick={save} disabled={!dirty || state === "saving"}>
                Save
              </button>
              <button type="button" className="close" onClick={close} aria-label="Close profile">
                ×
              </button>
            </>
          )}
        </div>
      </header>
      {error ? <div className="banner banner-denied">{error}</div> : null}

      <div className="profile-body">
        <nav className="profile-nav" aria-label="Profile sections">
          {sections.map((s) => {
            const count = filled(s, draft[s.key]);
            return (
              <button
                key={s.key}
                type="button"
                className={s.key === section.key ? "on" : ""}
                onClick={() => {
                  setActive(s.key);
                  formRef.current?.scrollTo({ top: 0 });
                }}
              >
                <span>{s.label}</span>
                {count ? <b>{count}</b> : null}
                {s.visibility ? <i className={`vis vis-${draft.visibility[s.key]}`} title={draft.visibility[s.key]} /> : null}
              </button>
            );
          })}
          <div className="vis-legend" aria-hidden="true">
            {doc.schema.visibility.map((v) => (
              <span key={v.value}>
                <i className={`vis vis-${v.value}`} />
                {v.label}
              </span>
            ))}
          </div>
        </nav>

        <section className="profile-form" ref={formRef} aria-labelledby="profile-section-title">
          <h3 id="profile-section-title">{section.label}</h3>
          {section.help ? <p className="muted section-help">{section.help}</p> : null}
          {section.visibility ? (
            <Visibility
              section={section.label}
              options={doc.schema.visibility}
              value={draft.visibility[section.key]}
              onChange={(v) => setDraft((d) => ({ ...d, visibility: { ...d.visibility, [section.key]: v } }))}
            />
          ) : null}
          {section.fields.map((field) => (
            <Field
              key={`${section.key}-${field.key}`}
              field={field}
              value={draft[section.key][field.key]}
              idPrefix={section.key}
              onChange={(v) => setField(field.key, v)}
            />
          ))}
        </section>

        <aside className="profile-preview" aria-label="What agents see">
          <h3>What agents see</h3>
          <div className="preview-tabs" role="tablist">
            {PREVIEW_TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                className={tab === t.key ? "on" : ""}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <p className="muted">
            {tab === "speech"
              ? "Given to speech recognition as hints. Never leaves this PC."
              : `Added to every session${dirty ? " (preview of your unsaved changes)" : ""}. Projects, people and past work you mention in a request add their full details to that request.`}
          </p>
          <pre>{previewText || (tab === "speech" ? "No names yet." : "Nothing: this agent sees no part of your profile.")}</pre>
          {tab !== "speech" ? <small className="muted">{previewText.length} characters</small> : null}
        </aside>
      </div>
    </div>
  );
}
