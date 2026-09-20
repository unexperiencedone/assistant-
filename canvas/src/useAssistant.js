import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { clientOrigin } from "./device.js";

// The server sends one full snapshot on connect, then patches: replaced slices in
// `data`, new rows for the long lists in `append`. Slices a patch doesn't mention
// keep their object identity, so a mic level update doesn't make the canvas lay
// out its graph again.
function reduce(state, message) {
  if (message.type === "snapshot") return { ...message.data, limits: message.limits ?? {} };
  if (message.type !== "patch" || !state) return state;
  const next = { ...state, ...message.data };
  for (const [key, rows] of Object.entries(message.append ?? {})) {
    if (!rows.length) continue;
    const max = state.limits?.[key] ?? 100;
    next[key] = [...(state[key] ?? []), ...rows].slice(-max);
  }
  return next;
}

export function useAssistant() {
  const [snapshot, dispatch] = useReducer(reduce, null);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef(null);
  const buildRef = useRef(null);

  useEffect(() => {
    let closed = false;
    let retry;

    function connect() {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${scheme}://${location.host}/ws`);
      socketRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        // The canvas was rebuilt since this page loaded: reload so it isn't stale.
        if (message.type === "snapshot" && message.build) {
          if (buildRef.current && buildRef.current !== message.build) {
            location.reload();
            return;
          }
          buildRef.current = message.build;
        }
        dispatch(message);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      socketRef.current?.close();
    };
  }, []);

  const send = useCallback((message) => {
    const ws = socketRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
  }, []);

  // Origin travels with the request: the reply, and any confirmation it needs, goes
  // back to the device that asked rather than out of the PC's speaker.
  const say = useCallback((text) => {
    if (text.trim()) send({ type: "say", text, origin: clientOrigin() });
  }, [send]);

  const dismissDenials = useCallback(() => send({ type: "dismiss_denials" }), [send]);

  return { snapshot, connected, say, dismissDenials };
}
