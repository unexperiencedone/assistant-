// The same believable workspace behind every overlay variant: VS Code on projects.py,
// the _walk_files function selected, mid-afternoon. Injected into #backdrop so all four
// directions are judged against an identical screen.
(function () {
  const code = [
    ["kw", "def"], ["fn", " _walk_files"], ["", "(folder: Path, limit: int = 4000):"], null,
    ["", "    count = 0"], null,
    ["", "    stack = [folder]"], null,
    ["kw", "    while"], ["", " stack "], ["kw", "and"], ["", " count < limit:"], null,
    ["", "        current = stack.pop()"], null,
    ["kw", "        try"], ["", ":"], null,
    ["", "            entries = "], ["fn", "list"], ["", "(current.iterdir())"], null,
    ["kw", "        except"], ["cls", " OSError"], ["", ":"], null,
    ["kw", "            continue"], null,
    ["kw", "        for"], ["", " entry "], ["kw", "in"], ["", " entries:"], null,
    ["kw", "            if"], ["", " entry.is_dir():"], null,
    ["", "                stack.append(entry)"], null,
    ["kw", "            else"], ["", ":"], null,
    ["", "                count += 1"], null,
    ["kw", "                yield"], ["", " entry"], null,
    ["", ""], null,
    ["kw", "def"], ["fn", " _languages_and_todos"], ["", "(folder: Path):"], null,
    ["kw", "    for"], ["", " file "], ["kw", "in"], ["fn", " _walk_files"], ["", "(folder):"], null,
    ["", "        newest = "], ["fn", "max"], ["", "(newest, file.stat().st_mtime)"], null,
    ["", "        text = file."], ["fn", "read_text"], ["", "(encoding="], ["str", "\"utf-8\""], ["", ")"], null,
    ["kw", "        for"], ["", " match "], ["kw", "in"], ["", " TODO.finditer(text):"], null,
    ["", "            todos.append(match.group(2))"], null,
  ];
  const lines = [[]];
  for (const part of code) part === null ? lines.push([]) : lines[lines.length - 1].push(part);
  lines.pop();
  const sel = new Set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
  const rows = lines
    .map((parts, i) => {
      const html = parts.map(([k, t]) => `<span class="${k}">${t.replace(/</g, "&lt;")}</span>`).join("");
      return `<div class="ln${sel.has(i) ? " sel" : ""}"><b>${233 + i}</b>${html || "&nbsp;"}</div>`;
    })
    .join("");
  const files = ["__init__.py", "__main__.py", "capture.py", "gate.py", "ledger.py", "linkedin.py", "linkedin_auth.py", "projects.py", "service.py", "showcase.py"];
  document.getElementById("backdrop").innerHTML = `
  <div class="vsc">
    <div class="vsc-title"><span>●  ●  ●</span>projects.py — Assisstant — Visual Studio Code</div>
    <div class="vsc-body">
      <div class="vsc-act"><i></i><i></i><i></i><i></i></div>
      <div class="vsc-side"><p>EXPLORER</p><p class="dir">▾ assistant / publish</p>${files
        .map((f) => `<p class="${f === "projects.py" ? "on" : ""}">${f}</p>`)
        .join("")}</div>
      <div class="vsc-ed"><div class="vsc-tabs"><span class="on">projects.py</span><span>showcase.py</span><span>capture.py</span></div><div class="vsc-code">${rows}</div></div>
    </div>
    <div class="vsc-status"><span>⎇ main</span><span>Python 3.11.9</span><span>Ln 233, Col 1 (14 lines selected)</span></div>
  </div>`;
})();
