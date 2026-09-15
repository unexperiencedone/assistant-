import { useCallback, useEffect, useRef, useState } from "react";

// Live connection to the Python app: the server pushes full snapshots over a
// WebSocket; we send typed commands back on the same socket.
export function useAssistant() {
  const [snapshot, setSnapshot] = useState(null);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef(null);

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
        if (message.type === "snapshot") setSnapshot(message.data);
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

  const say = useCallback((text) => {
    const ws = socketRef.current;
    if (text.trim() && ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "say", text }));
    }
  }, []);

  return { snapshot, connected, say };
}
