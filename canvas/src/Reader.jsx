/* The reader: a long answer, laid out to be read rather than heard.
 *
 * Speech takes the first three sentences and drops the rest, which is right for "the
 * torch is on" and destructive for a piece of research. When an answer is a document
 * (assistant/reader.py decides) the whole thing arrives here, in a small always-on-top
 * window, while the voice says a short true summary of it.
 *
 * Markdown is rendered by hand rather than with a parser, for one reason: everything is
 * HTML-escaped *first*, and only tags this file writes are inserted afterwards. A
 * document can contain text Nova read off a web page (`read_page`), so the path from a
 * hostile page through the model to innerHTML is real, and escaping first closes it
 * without trusting a sanitiser to be configured correctly.
 *
 * Mermaid is the exception and is loaded lazily -- it is a large library and the canvas
 * should not carry it on every page load -- with securityLevel "strict", so a diagram
 * definition cannot smuggle markup through either.
 */
import { useEffect, useMemo, useRef, useState } from "react";

const escapeHtml = (text) =>
  text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Inline marks, applied to already-escaped text. Code spans first, so that ** inside
   `code` is left alone. */
function inline(text) {
  let out = text;
  out = out.replace(/`([^`]+)`/g, (_m, code) => `<code>${code}</code>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\(((?:https?:)?\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  return out;
}

/* A caption per docs/document_standards.md: "Figure 3: Title — the takeaway." Marked up
   so the takeaway can be styled apart from the label, since the standard exists to make
   a visual understandable without the body text. */
const CAPTION = /^((?:figure|fig\.?|table)\s*\d+)\s*[:.]\s*(.+)$/i;

function blocks(markdown) {
  const lines = (markdown || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  let para = [];

  const flushPara = () => {
    if (!para.length) return;
    const text = para.join(" ").trim();
    const caption = CAPTION.exec(text);
    if (caption) {
      out.push({ kind: "caption", label: caption[1], text: caption[2] });
    } else if (text) {
      out.push({ kind: "p", text });
    }
    para = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced block: mermaid gets its own node, anything else is code
    const fence = /^```\s*(\w+)?\s*$/.exec(line);
    if (fence) {
      const lang = (fence[1] || "").toLowerCase();
      const body = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i += 1; // closing fence
      flushPara();
      out.push(lang === "mermaid" ? { kind: "mermaid", code: body.join("\n") } : { kind: "code", lang, code: body.join("\n") });
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushPara();
      out.push({ kind: "h", level: heading[1].length, text: heading[2].trim() });
      i += 1;
      continue;
    }

    if (/^\s*(?:[-*_]\s*){3,}$/.test(line)) {
      flushPara();
      out.push({ kind: "hr" });
      i += 1;
      continue;
    }

    // table: a header row, a divider, then rows
    if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      flushPara();
      out.push({ kind: "table", head, rows });
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      flushPara();
      out.push({ kind: "quote", text: quote.join(" ") });
      continue;
    }

    const bullet = /^\s*(?:[-*+]|(\d+)\.)\s+(.*)$/.exec(line);
    if (bullet) {
      const ordered = Boolean(bullet[1]);
      const items = [];
      while (i < lines.length) {
        const next = /^\s*(?:[-*+]|(\d+)\.)\s+(.*)$/.exec(lines[i]);
        if (!next || Boolean(next[1]) !== ordered) break;
        items.push(next[2]);
        i += 1;
      }
      flushPara();
      out.push({ kind: "list", ordered, items });
      continue;
    }

    if (!line.trim()) {
      flushPara();
      i += 1;
      continue;
    }
    para.push(line.trim());
    i += 1;
  }
  flushPara();
  return out;
}

function Mermaid({ code, index }) {
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "dark",
          fontFamily: "IBM Plex Mono, ui-monospace, monospace",
          themeVariables: { background: "#12101a", primaryColor: "#17131f", primaryTextColor: "#f2ece2", lineColor: "#6b6577" },
        });
        const { svg: rendered } = await mermaid.render(`reader-d${index}-${Date.now()}`, code);
        if (live) setSvg(rendered);
      } catch {
        if (live) setFailed(true);
      }
    })();
    return () => {
      live = false;
    };
  }, [code, index]);

  // A diagram that will not draw shows its own source rather than a blank space: the
  // definition is still information, and an empty box looks like a bug in Nova.
  if (failed) return <pre className="reader-code" data-lang="mermaid">{code}</pre>;
  if (!svg) return <div className="reader-diagram is-loading">drawing…</div>;
  return <figure className="reader-diagram" dangerouslySetInnerHTML={{ __html: svg }} />;
}

export default function Reader({ document: doc }) {
  const parsed = useMemo(() => blocks(doc?.markdown || ""), [doc?.markdown]);
  const top = useRef(null);

  // A new document starts at the top: it is a different thing to read, not more of the
  // last one, and being left halfway down someone else's report is disorienting.
  useEffect(() => {
    if (top.current) top.current.scrollTop = 0;
  }, [doc?.revision]);

  if (!doc?.markdown) {
    return (
      <div className="reader is-empty">
        <p className="reader-hint">
          Nothing to read yet. When an answer is long enough to be a document, it appears here in full
          and Nova reads you a summary of it.
        </p>
      </div>
    );
  }

  let figure = 0;
  return (
    <div className="reader" ref={top}>
      {doc.title ? <h1 className="reader-title">{doc.title}</h1> : null}
      {parsed.map((block, n) => {
        switch (block.kind) {
          case "h": {
            const Tag = `h${Math.min(block.level + 1, 6)}`;
            return <Tag key={n} dangerouslySetInnerHTML={{ __html: inline(escapeHtml(block.text)) }} />;
          }
          case "p":
            return <p key={n} dangerouslySetInnerHTML={{ __html: inline(escapeHtml(block.text)) }} />;
          case "caption":
            return (
              <p className="reader-caption" key={n}>
                <span className="reader-caption-label">{block.label}</span>
                <span dangerouslySetInnerHTML={{ __html: inline(escapeHtml(block.text)) }} />
              </p>
            );
          case "quote":
            return <blockquote key={n} dangerouslySetInnerHTML={{ __html: inline(escapeHtml(block.text)) }} />;
          case "hr":
            return <hr key={n} />;
          case "list":
            return block.ordered ? (
              <ol key={n}>
                {block.items.map((item, m) => (
                  <li key={m} dangerouslySetInnerHTML={{ __html: inline(escapeHtml(item)) }} />
                ))}
              </ol>
            ) : (
              <ul key={n}>
                {block.items.map((item, m) => (
                  <li key={m} dangerouslySetInnerHTML={{ __html: inline(escapeHtml(item)) }} />
                ))}
              </ul>
            );
          case "table":
            return (
              <div className="reader-table-wrap" key={n}>
                <table className="reader-table">
                  <thead>
                    <tr>
                      {block.head.map((cell, m) => (
                        <th key={m} dangerouslySetInnerHTML={{ __html: inline(escapeHtml(cell)) }} />
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, m) => (
                      <tr key={m}>
                        {row.map((cell, k) => (
                          <td key={k} dangerouslySetInnerHTML={{ __html: inline(escapeHtml(cell)) }} />
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "code":
            return (
              <pre className="reader-code" key={n} data-lang={block.lang || undefined}>
                {block.code}
              </pre>
            );
          case "mermaid":
            return <Mermaid key={n} code={block.code} index={figure++} />;
          default:
            return null;
        }
      })}
    </div>
  );
}
