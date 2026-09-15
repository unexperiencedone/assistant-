"""Local FastAPI server for the live canvas.

    GET  /            the built React Flow app (assistant/ui/canvas_dist)
    WS   /ws          server -> client {"type": "snapshot", "data": ...} on every change
                      client -> server {"type": "say", "text": ...}
    GET  /api/state   current snapshot
    POST /api/say     {"text": ...}
    POST /api/show    bring the window forward (used by a second launch)

Runs uvicorn in a background thread and binds to 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..paths import resource_path
from ..state import AppState

SEND_INTERVAL = 0.05  # at most 20 snapshots per second per client

_MISSING_BUILD = """<!doctype html><title>Nova</title>
<body style="font-family:system-ui;padding:40px;max-width:60ch">
<h1>Canvas not built</h1>
<p>Run <code>npm install</code> and <code>npm run build</code> in the <code>canvas</code> folder, then restart Nova.</p>
</body>"""


class _Client:
    def __init__(self) -> None:
        self.latest: dict[str, Any] | None = None
        self.changed = asyncio.Event()


class DashboardServer:
    def __init__(
        self,
        state: AppState,
        port: int,
        on_text: Callable[[str], None],
        on_show: Callable[[], None] | None = None,
    ) -> None:
        self.state = state
        self.port = port
        self.on_text = on_text
        self.on_show = on_show
        self.dist = resource_path("assistant", "ui", "canvas_dist")
        self._clients: set[_Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: uvicorn.Server | None = None
        self.app = self._build_app()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # -- lifecycle ------------------------------------------------------------------
    def start(self) -> None:
        if not port_free(self.port):
            raise OSError(f"port {self.port} is already in use")
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                                access_log=False, lifespan="on")
        self._server = uvicorn.Server(config)
        threading.Thread(target=self._server.run, name="canvas-server", daemon=True).start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise OSError("canvas server did not start")
        self.state.add_listener(self._on_snapshot)

    def stop(self) -> None:
        self.state.remove_listener(self._on_snapshot)
        if self._server:
            self._server.should_exit = True

    # -- push -----------------------------------------------------------------------
    def _on_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Called on the event-bus thread; hand over to the server's event loop."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._fan_out, snapshot)

    def _fan_out(self, snapshot: dict[str, Any]) -> None:
        for client in self._clients:
            client.latest = snapshot  # only the newest snapshot matters
            client.changed.set()

    # -- app ------------------------------------------------------------------------
    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.on_event("startup")
        async def capture_loop() -> None:
            self._loop = asyncio.get_running_loop()

        @app.middleware("http")
        async def cache_headers(request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/assets/"):
                # Vite puts a content hash in these file names, so they never change.
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                # index.html names the current bundle: always revalidate it, or a rebuilt
                # canvas keeps showing the old UI (browsers and WebView2 cache it otherwise).
                response.headers["Cache-Control"] = "no-cache"
            return response

        @app.get("/api/state")
        async def api_state() -> JSONResponse:
            return JSONResponse(self.state.snapshot())

        @app.post("/api/say")
        async def api_say(body: dict[str, Any]) -> JSONResponse:
            text = str(body.get("text", "")).strip()
            if text:
                _in_background(self.on_text, text)
            return JSONResponse({"ok": bool(text)})

        @app.post("/api/show")
        async def api_show() -> JSONResponse:
            if self.on_show:
                _in_background(self.on_show)
            return JSONResponse({"ok": self.on_show is not None})

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await websocket.accept()
            client = _Client()
            client.latest = self.state.snapshot()
            client.changed.set()
            self._clients.add(client)
            sender = asyncio.create_task(self._send_loop(websocket, client))
            try:
                while True:
                    message = await websocket.receive_json()
                    if message.get("type") == "say" and str(message.get("text", "")).strip():
                        _in_background(self.on_text, str(message["text"]).strip())
            except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
                pass
            finally:
                sender.cancel()
                self._clients.discard(client)

        if (self.dist / "index.html").exists():
            app.mount("/", StaticFiles(directory=self.dist, html=True), name="canvas")
        else:
            @app.get("/")
            async def missing_build() -> HTMLResponse:
                return HTMLResponse(_MISSING_BUILD)

        return app

    @staticmethod
    async def _send_loop(websocket: WebSocket, client: _Client) -> None:
        try:
            while True:
                await client.changed.wait()
                client.changed.clear()
                await websocket.send_text(json.dumps({"type": "snapshot", "data": client.latest}))
                await asyncio.sleep(SEND_INTERVAL)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass


# Commands can block (e.g. "goodbye" waits for speech to finish), so they never run on
# the event loop. One worker keeps messages in the order they were typed.
_COMMANDS = ThreadPoolExecutor(max_workers=1, thread_name_prefix="canvas-command")


def _in_background(func: Callable[..., None], *args: Any) -> None:
    _COMMANDS.submit(func, *args)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
