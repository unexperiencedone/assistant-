import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes straight into the Python package, which serves it.
// `npm run dev` proxies the WebSocket and API to a running `python main.py`.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../assistant/ui/canvas_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
      "/api": "http://127.0.0.1:8765",
    },
  },
});
