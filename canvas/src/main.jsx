import React from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./styles.css";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// The service worker is what turns the canvas into an installed app on the phone.
// A worker only runs in a secure context, so over plain http on a Tailscale address
// this quietly does nothing and the canvas stays an ordinary page — put HTTPS in
// front of Nova with `tailscale serve` to get the full-screen app (docs/phone.md).
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* nothing to do: the canvas works the same, it just is not installable */
    });
  });
}
