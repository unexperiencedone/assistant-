"""The shared secret in front of the canvas server.

These run against the real FastAPI app, because the point of the token is that there
is no way in around it: page, API and WebSocket are all checked, and loopback gets no
special treatment.

The app is driven through ASGI directly rather than through starlette's TestClient,
which does not work with the installed httpx.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from assistant.events import EventBus
from assistant.state import AppState
from assistant.ui import auth
from assistant.ui.server import DashboardServer

TOKEN = "test-token-abc"


def make_app(token=TOKEN):
    bus = EventBus()
    state = AppState(bus, "Nova", "claude code", "C:/Assisstant")
    return DashboardServer(state, 8799, on_text=lambda text: None, token=token)


def call(server, method: str, path: str, **kwargs) -> httpx.Response:
    """One request against the app. httpx drives ASGI asynchronously only."""
    return session(server, [(method, path, kwargs)])[0]


def session(server, calls: list) -> list:
    """Several requests on one client, so a cookie set by the first is sent by the next."""
    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://nova.test") as client:
            return [await client.request(method, path, **kwargs) for method, path, kwargs in calls]

    return asyncio.run(run())


def websocket_open(app, query: str = "", cookie: str = "") -> dict:
    """Connect to /ws through ASGI and return the server's first message.

    That message is the whole answer: `websocket.accept` means the guard let it in,
    `websocket.close` means it did not.
    """
    scope = {
        "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "scheme": "ws", "path": "/ws", "raw_path": b"/ws", "root_path": "",
        "query_string": query.encode(), "headers": [(b"cookie", cookie.encode())] if cookie else [],
        "client": ("127.0.0.1", 51234), "server": ("127.0.0.1", 8799), "subprotocols": [], "state": {},
    }
    sent: list[dict] = []
    incoming = [{"type": "websocket.connect"}, {"type": "websocket.disconnect", "code": 1000}]

    async def receive():
        return incoming.pop(0) if incoming else {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        sent.append(message)

    async def run():
        await asyncio.wait_for(app(scope, receive, send), timeout=5)

    asyncio.run(run())
    return sent[0] if sent else {}


class TestTokenFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "nova_token"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_token_is_generated_once_and_then_reused(self):
        first = auth.load_or_create(self.path)
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(auth.load_or_create(self.path), first)
        self.assertEqual(auth.read(self.path), first)

    def test_rotating_issues_a_different_token(self):
        first = auth.load_or_create(self.path)
        second = auth.rotate(self.path)
        self.assertNotEqual(first, second)
        self.assertEqual(auth.read(self.path), second)

    def test_reading_a_missing_file_is_empty_not_an_error(self):
        self.assertEqual(auth.read(Path(self.dir.name) / "nothing"), "")


class TestRequestsAreChecked(unittest.TestCase):
    def setUp(self):
        self.server = make_app()

    def test_the_api_refuses_a_request_with_no_token(self):
        response = call(self.server, "GET", "/api/state")
        self.assertEqual(response.status_code, 401)
        self.assertIn("token", response.json()["error"].lower())

    def test_a_wrong_token_is_refused(self):
        response = call(self.server, "GET", "/api/state", headers={"X-Nova-Token": "nope"})
        self.assertEqual(response.status_code, 401)

    def test_the_header_lets_a_request_through(self):
        response = call(self.server, "GET", "/api/state", headers={"X-Nova-Token": TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Nova")

    def test_pairing_with_a_query_token_leaves_a_cookie_behind(self):
        paired, after = session(self.server, [
            ("GET", f"/api/state?token={TOKEN}", {}),
            ("GET", "/api/state", {}),   # the cookie alone, no token in sight
        ])
        self.assertEqual(paired.status_code, 200)
        self.assertEqual(paired.cookies.get(auth.COOKIE_NAME), TOKEN)
        self.assertEqual(after.status_code, 200)

    def test_posting_a_command_without_the_token_is_refused(self):
        self.assertEqual(call(self.server, "POST", "/api/say", json={"text": "hello"}).status_code, 401)

    def test_the_page_itself_is_behind_the_token_too(self):
        self.assertEqual(call(self.server, "GET", "/").status_code, 401)


class TestWebSocketIsChecked(unittest.TestCase):
    def test_the_socket_is_closed_without_a_token(self):
        first = websocket_open(make_app().app)
        self.assertEqual(first.get("type"), "websocket.close")
        self.assertEqual(first.get("code"), 1008)

    def test_a_wrong_token_is_closed_too(self):
        first = websocket_open(make_app().app, query="token=nope")
        self.assertEqual(first.get("type"), "websocket.close")

    def test_the_socket_opens_with_the_token(self):
        self.assertEqual(websocket_open(make_app().app, query=f"token={TOKEN}").get("type"), "websocket.accept")

    def test_the_pairing_cookie_opens_it_as_well(self):
        first = websocket_open(make_app().app, cookie=f"{auth.COOKIE_NAME}={TOKEN}")
        self.assertEqual(first.get("type"), "websocket.accept")


class TestTokenCanBeTurnedOff(unittest.TestCase):
    def test_no_token_configured_means_no_check(self):
        self.assertEqual(call(make_app(token=""), "GET", "/api/state").status_code, 200)


class TestOpenUrl(unittest.TestCase):
    def test_the_url_shown_never_carries_the_secret(self):
        server = make_app()
        self.assertNotIn(TOKEN, server.url)
        self.assertIn(TOKEN, server.open_url)


if __name__ == "__main__":
    unittest.main()


class TestTheBridgeScriptIsServed(unittest.TestCase):
    """Getting the bridge onto the phone without ssh, a cable or a cloud round trip."""

    def setUp(self):
        self.server = make_app()

    def test_it_needs_the_token_like_everything_else(self):
        self.assertEqual(call(self.server, "GET", "/api/phone/bridge").status_code, 401)

    def test_it_serves_the_script(self):
        response = call(self.server, "GET", "/api/phone/bridge", headers={"X-Nova-Token": TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nova bridge", response.text)
        self.assertIn("termux-torch", response.text)
