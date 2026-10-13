// Small fetch helpers for the spaces. Same-origin: the token cookie rides along.
export async function getJSON(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

export async function postJSON(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok && data.ok !== false, ...data };
}

/** Is the user typing somewhere? Then single keys belong to the field, not to Nova. */
export function typing(event) {
  const target = event.target;
  return Boolean(target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName ?? ""));
}
