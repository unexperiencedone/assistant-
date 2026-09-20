"""Local FastAPI server for the live canvas.

    GET  /            the built React Flow app (assistant/ui/canvas_dist)
    WS   /ws          server -> client {"type": "snapshot", "data": ..., "limits": ...} once on connect,
                                then {"type": "patch", "data": {changed slices},
                                      "append": {list: [new rows]}} on every change
                      client -> server {"type": "say", "text": ..., "origin": ...} | {"type": "dismiss_denials"}
    GET  /api/state   current snapshot
    POST /api/say     {"text": ..., "origin": "desktop"|"phone"}
    POST /api/show    bring the window forward (used by a second launch)
    GET  /api/profile           profile + schema + what each agent would see
    PUT  /api/profile           save a profile (validated against the schema)
    POST /api/profile/preview   what agents would see for an unsaved profile
    GET  /api/history/sessions?q=&before=&limit=   work sessions, newest first
    GET  /api/history/sessions/<id>                one session: events, requests, files changed
    DELETE /api/history/sessions/<id>              delete a session
    GET  /api/history/runs/<id>                    a stored request's full graph
    GET  /api/ambient           the briefing cache (weather, markets, headlines, with sources)
    POST /api/ambient/refresh   ask the agent to read the pages again (one turn)
    GET  /api/awareness/state   is activity awareness on, and is it watching
    GET  /api/awareness/day     today's collapsed sessions
    POST /api/awareness/watch   {"on": true|false}
    POST /api/awareness/ask     {"question": ...} -> an answer written from the rows
    POST /api/awareness/forget  delete every recorded event and session
    GET  /api/phone/bridge      the Termux bridge script, so the phone can fetch it itself

Runs uvicorn in a background thread, bound to `[ui] host` (loopback by default).

**Every** request — page, API and WebSocket alike — must carry the shared secret from
`assistant/ui/auth.py`: an `X-Nova-Token` header, a `?token=` query, or the cookie a
successful `?token=` visit leaves behind. There is no loopback exemption; the desktop
canvas is opened with the token already in its URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..paths import resource_path
from ..state import LIST_LIMITS, AppState
from . import auth

SEND_INTERVAL = 0.05  # at most 20 updates per second per client

_MISSING_BUILD = """<!doctype html><title>Nova</title>
<body style="font-family:system-ui;padding:40px;max-width:60ch">
<h1>Canvas not built</h1>
<p>Run <code>npm install</code> and <code>npm run build</code> in the <code>canvas</code> folder, then restart Nova.</p>
</body>"""



def origin_of(client_host: str | None, claimed: str = "") -> str:
    """Where a request came from: what the client says, or where it connected from.

    The client knows best (a phone-shaped browser says so itself), but a request that
    arrives from anywhere other than this machine is remote whatever it claims, so the
    address is the fallback rather than a guess of "desktop".
    """
    if claimed in ("desktop", "phone"):
        return claimed
    host = (client_host or "").strip()
    return "desktop" if host in ("127.0.0.1", "::1", "localhost", "") else "phone"


class _Client:
    def __init__(self) -> None:
        self.versions: dict[str, int] = {}  # what this client has already been sent
        self.changed = asyncio.Event()


class DashboardServer:
    def __init__(
        self,
        state: AppState,
        port: int,
        on_text: Callable[..., None],
        on_show: Callable[[], None] | None = None,
        graph_tracker: Any = None,
        profile: Any = None,
        history: Any = None,
        ambient: Any = None,
        awareness: Any = None,
        host: str = "127.0.0.1",
        token: str = "",
    ) -> None:
        self.host = host or "127.0.0.1"
        self.token = token  # empty only when [ui] require_token is off
        self.profile = profile      # assistant.profile.ProfileService
        self.history = history      # assistant.history.HistoryRecorder
        self.ambient = ambient      # assistant.ambient.AmbientService
        self.awareness = awareness  # assistant.awareness.AwarenessService
        self.state = state
        self.port = port
        self.on_text = on_text
        self.on_show = on_show
        self.graph_tracker = graph_tracker  # serves full, untruncated run detail
        self.dist = resource_path("assistant", "ui", "canvas_dist")
        self.build = _build_id(self.dist)  # changes when the canvas is rebuilt
        self._clients: set[_Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: uvicorn.Server | None = None
        self.app = self._build_app()

    @property
    def url(self) -> str:
        """For showing and logging: never carries the token."""
        return f"http://127.0.0.1:{self.port}/"

    @property
    def open_url(self) -> str:
        """For opening a browser or the native window: carries the token once, which
        leaves a cookie behind, so nothing has to print the secret afterwards."""
        return f"{self.url}?{auth.QUERY_NAME}={self.token}" if self.token else self.url

    # -- lifecycle ------------------------------------------------------------------
    def start(self) -> None:
        if not port_free(self.port, self.host):
            raise OSError(f"port {self.port} is already in use")
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning",
                                access_log=False, lifespan="on")
        self._server = uvicorn.Server(config)
        threading.Thread(target=self._server.run, name="canvas-server", daemon=True).start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise OSError("canvas server did not start")
        self.state.add_listener(self._on_change)

    def stop(self) -> None:
        self.state.remove_listener(self._on_change)
        if self._server:
            self._server.should_exit = True

    # -- push -----------------------------------------------------------------------
    def _on_change(self) -> None:
        """Called on the event-bus thread; hand over to the server's event loop. Cheap: each
        client works out what changed for it when it next sends."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._fan_out)

    def _fan_out(self) -> None:
        for client in self._clients:
            client.changed.set()

    # -- app ------------------------------------------------------------------------
    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.on_event("startup")
        async def capture_loop() -> None:
            self._loop = asyncio.get_running_loop()

        @app.middleware("http")
        async def require_token(request, call_next):
            """Nothing is served without the shared secret — not the page, not the API.

            A request that proves itself with `?token=` gets a cookie back, so a device
            is paired by opening one link and never carries the token again.
            """
            supplied = auth.supplied_token(request.headers, request.query_params, request.cookies)
            if not auth.matches(self.token, supplied):
                return JSONResponse(
                    {"error": "Nova needs its token: add ?token=<token> once, or send an "
                              "X-Nova-Token header. It is in data/nova_token on the PC."},
                    status_code=401,
                )
            response = await call_next(request)
            if self.token and request.query_params.get(auth.QUERY_NAME):
                response.set_cookie(auth.COOKIE_NAME, self.token, max_age=auth.COOKIE_MAX_AGE,
                                    httponly=True, samesite="lax")
            return response

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
        async def api_say(request: Request, body: dict[str, Any]) -> JSONResponse:
            text = str(body.get("text", "")).strip()
            origin = origin_of(request.client.host if request.client else None, str(body.get("origin", "")))
            if text:
                _in_background(self.on_text, text, origin)
            return JSONResponse({"ok": bool(text), "origin": origin})

        @app.get("/api/run")
        @app.get("/api/run/{run}")
        async def api_run(run: int | None = None) -> JSONResponse:
            """One request's graph in full: every action, untruncated. Powers the inspector."""
            if self.graph_tracker is None:
                return JSONResponse({"error": "no graph"}, status_code=404)
            detail = self.graph_tracker.run_detail(run)
            if detail is None:
                return JSONResponse({"error": f"run {run} is no longer kept"}, status_code=404)
            return JSONResponse(detail)

        @app.post("/api/plan/move")
        async def api_plan_move(request: Request, body: dict[str, Any]) -> JSONResponse:
            """Drag-to-reorder from the canvas. Same path as saying "move step 2 down"."""
            try:
                number, to = int(body["from"]), int(body["to"])
            except (KeyError, TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "need 'from' and 'to' step numbers"}, status_code=400)
            origin = origin_of(request.client.host if request.client else None, str(body.get("origin", "")))
            _in_background(self.on_text, f"move step {number} to {to}", origin)
            return JSONResponse({"ok": True})

        @app.get("/api/profile")
        async def api_profile() -> JSONResponse:
            if self.profile is None:
                return JSONResponse({"error": "profiles are off"}, status_code=404)
            return JSONResponse(self.profile.document())

        @app.put("/api/profile")
        async def api_profile_save(body: dict[str, Any]) -> JSONResponse:
            if self.profile is None:
                return JSONResponse({"error": "profiles are off"}, status_code=404)
            # Saving restarts nothing mid-turn, but applying it can touch files: off the event loop.
            await asyncio.get_running_loop().run_in_executor(None, self.profile.update, body.get("profile"))
            return JSONResponse(self.profile.document())

        @app.post("/api/profile/preview")
        async def api_profile_preview(body: dict[str, Any]) -> JSONResponse:
            from ..profile.schema import normalize
            from ..profile.service import preview

            return JSONResponse(preview(normalize(body.get("profile"))))

        @app.get("/api/history/sessions")
        async def api_history_sessions(q: str = "", before: float | None = None, limit: int = 50) -> JSONResponse:
            if self.history is None:
                return JSONResponse({"sessions": [], "more": False, "enabled": False})
            sessions, more = await asyncio.to_thread(self.history.store.sessions, max(1, min(limit, 200)), before, q)
            return JSONResponse({"sessions": sessions, "more": more, "enabled": True})

        @app.get("/api/history/sessions/{session_id}")
        async def api_history_session(session_id: int) -> JSONResponse:
            detail = await asyncio.to_thread(self.history.store.session, session_id) if self.history else None
            if detail is None:
                return JSONResponse({"error": "no such session"}, status_code=404)
            return JSONResponse(detail)

        @app.delete("/api/history/sessions/{session_id}")
        async def api_history_delete(session_id: int) -> JSONResponse:
            if self.history is None or not await asyncio.to_thread(self.history.store.delete, session_id):
                return JSONResponse({"error": "no such session"}, status_code=404)
            self.history.notify()
            return JSONResponse({"ok": True})

        @app.get("/api/history/runs/{run_id}")
        async def api_history_run(run_id: int) -> JSONResponse:
            graph = await asyncio.to_thread(self.history.store.run, run_id) if self.history else None
            if graph is None:
                return JSONResponse({"error": "no such request"}, status_code=404)
            return JSONResponse(graph)

        @app.get("/api/ambient")
        async def api_ambient() -> JSONResponse:
            """The briefing exactly as it was last compiled. Never fetches anything."""
            if self.ambient is None:
                return JSONResponse({"status": "off", "markets": [], "headlines": []})
            return JSONResponse(await asyncio.to_thread(self.ambient.document))

        @app.post("/api/ambient/refresh")
        async def api_ambient_refresh() -> JSONResponse:
            if self.ambient is None:
                return JSONResponse({"ok": False, "error": "the briefing is off"}, status_code=404)
            return JSONResponse(self.ambient.refresh())

        @app.get("/api/awareness/state")
        async def api_awareness_state() -> JSONResponse:
            if self.awareness is None:
                return JSONResponse({"enabled": False, "watching": False,
                                     "retention": {"raw_days": 0, "session_days": 0}})
            return JSONResponse(self.awareness.state())

        @app.get("/api/awareness/day")
        async def api_awareness_day() -> JSONResponse:
            if self.awareness is None:
                return JSONResponse({"sessions": [], "tracked": 0, "switches": 0, "coding": None})
            return JSONResponse(await asyncio.to_thread(self.awareness.day))

        @app.post("/api/awareness/watch")
        async def api_awareness_watch(body: dict[str, Any]) -> JSONResponse:
            if self.awareness is None:
                return JSONResponse({"error": "activity awareness is off"}, status_code=404)
            return JSONResponse(self.awareness.watch(bool(body.get("on"))))

        @app.post("/api/awareness/ask")
        async def api_awareness_ask(body: dict[str, Any]) -> JSONResponse:
            """Answering reads rows and may ask a model, so it never runs on the event loop."""
            if self.awareness is None:
                return JSONResponse({"error": "activity awareness is off"}, status_code=404)
            question = str(body.get("question", "")).strip()
            if not question:
                return JSONResponse({"error": "ask something"}, status_code=400)
            return JSONResponse(await asyncio.to_thread(self.awareness.ask, question))

        @app.post("/api/awareness/forget")
        async def api_awareness_forget() -> JSONResponse:
            if self.awareness is None:
                return JSONResponse({"error": "activity awareness is off"}, status_code=404)
            return JSONResponse(await asyncio.to_thread(self.awareness.forget))

        @app.get("/api/phone/bridge")
        async def api_phone_bridge():
            """The Termux bridge script itself.

            Getting a file onto a phone otherwise means an SSH server, a cable or a
            cloud round trip. The phone can already reach this server and already has
            the token, so it can just fetch it:

                curl -H "X-Nova-Token: <token>" http://<pc>:8765/api/phone/bridge -o nova_bridge.py
            """
            path = resource_path("phone", "nova_bridge.py")
            try:
                return PlainTextResponse(path.read_text(encoding="utf-8"))
            except OSError:
                return JSONResponse({"error": "the bridge script isn't in this install"}, status_code=404)

        @app.post("/api/show")
        async def api_show() -> JSONResponse:
            if self.on_show:
                _in_background(self.on_show)
            return JSONResponse({"ok": self.on_show is not None})

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            # A browser cannot put a header on a WebSocket, so the cookie left by pairing
            # (or an explicit ?token=) is what proves this one.
            supplied = auth.supplied_token(websocket.headers, websocket.query_params, websocket.cookies)
            if not auth.matches(self.token, supplied):
                await websocket.close(code=1008)
                return
            await websocket.accept()
            client = _Client()
            self._clients.add(client)
            sender = asyncio.create_task(self._send_loop(websocket, client))
            try:
                while True:
                    message = await websocket.receive_json()
                    if message.get("type") == "say" and str(message.get("text", "")).strip():
                        origin = origin_of(websocket.client.host if websocket.client else None,
                                           str(message.get("origin", "")))
                        _in_background(self.on_text, str(message["text"]).strip(), origin)
                    elif message.get("type") == "dismiss_denials":
                        self.state.dismiss_denials()
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

    async def _send_loop(self, websocket: WebSocket, client: _Client) -> None:
        try:
            snapshot, client.versions = self.state.snapshot_with_versions()
            await websocket.send_text(json.dumps(
                {"type": "snapshot", "data": snapshot, "limits": LIST_LIMITS, "build": self.build}, default=str))
            while True:
                await client.changed.wait()
                client.changed.clear()
                replaced, appended, client.versions = self.state.changes_since(client.versions)
                if replaced or appended:
                    await websocket.send_text(json.dumps(
                        {"type": "patch", "data": replaced, "append": appended}, default=str))
                await asyncio.sleep(SEND_INTERVAL)  # changes during the pause go out together next time
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass


# Commands can block (e.g. "goodbye" waits for speech to finish), so they never run on
# the event loop. One worker keeps messages in the order they were typed.
_COMMANDS = ThreadPoolExecutor(max_workers=1, thread_name_prefix="canvas-command")


def _in_background(func: Callable[..., None], *args: Any) -> None:
    _COMMANDS.submit(func, *args)


def _build_id(dist: Path) -> str:
    """Identifies the built canvas. Sent with every update so a page left open from an
    older build reloads itself instead of showing stale UI from the browser cache."""
    try:
        files = sorted((p.stat().st_mtime_ns, p.stat().st_size, p.name) for p in dist.rglob("*") if p.is_file())
    except OSError:
        return "dev"
    return hashlib.sha1(repr(files).encode()).hexdigest()[:12]


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False
