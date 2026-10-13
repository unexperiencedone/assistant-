"""The Chrome extension's bridge: requests out, answers back, and what a model is allowed.

The extension itself is JavaScript and runs in Chrome; what is tested here is everything
on Nova's side of the socket, with a fake extension answering on a real event loop.
"""

import asyncio
import json
import threading
import unittest

from assistant.agents import tools
from assistant.automation import extension
from assistant.automation.extension import ExtensionBridge, ExtensionBrowser, ExtensionError, ref_number
from test_server_auth import TOKEN, call, make_app


class FakeExtension:
    """A WebSocket whose other end answers each request with `answer(method, params)`."""

    def __init__(self, answer):
        self.answer = answer
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []

    async def receive_text(self):
        message = await self.inbox.get()
        if message is None:
            raise ConnectionError("closed")
        return message

    async def send_text(self, text):
        request = json.loads(text)
        self.sent.append(request)
        try:
            reply = {"id": request["id"], "ok": True, "result": self.answer(request["method"], request["params"])}
        except Exception as exc:
            reply = {"id": request["id"], "ok": False, "error": str(exc)}
        await self.inbox.put(json.dumps(reply))

    async def close(self, code=1000):
        await self.inbox.put(None)


class Connected:
    """A bridge served on its own loop thread, as the canvas server would."""

    def __init__(self, answer):
        self.bridge = ExtensionBridge()
        self.loop = asyncio.new_event_loop()
        self.fake = None
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.fake = FakeExtension(answer)
            self.loop.call_soon(ready.set)
            self.loop.run_until_complete(self.bridge.serve(self.fake))

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(2)
        while not self.bridge.connected:
            pass

    def disconnect(self):
        asyncio.run_coroutine_threadsafe(self.fake.inbox.put(None), self.loop).result(2)
        self.thread.join(2)


class TestBridge(unittest.TestCase):
    def test_a_call_gets_the_extensions_answer(self):
        conn = Connected(lambda method, params: {"page": f"{method}:{params['url']}"})
        try:
            self.assertEqual(conn.bridge.call("open", {"url": "https://a.test"}), {"page": "open:https://a.test"})
        finally:
            conn.disconnect()
        self.assertFalse(conn.bridge.connected)

    def test_an_error_in_chrome_is_raised_here(self):
        def answer(method, params):
            raise ValueError("[9] is no longer on the page")

        conn = Connected(answer)
        try:
            with self.assertRaisesRegex(ExtensionError, "no longer on the page"):
                conn.bridge.call("click", {"target": {"ref": 9}})
        finally:
            conn.disconnect()

    def test_calling_without_the_extension_says_so(self):
        with self.assertRaisesRegex(ExtensionError, "isn't connected"):
            ExtensionBridge().call("snapshot")

    def test_the_driver_setting_can_rule_the_extension_out(self):
        conn = Connected(lambda m, p: {})
        try:
            conn.bridge.driver = "playwright"
            self.assertFalse(conn.bridge.usable())
            conn.bridge.driver = "auto"
            self.assertTrue(conn.bridge.usable())
        finally:
            conn.disconnect()


class TestRefs(unittest.TestCase):
    def test_every_way_a_model_writes_a_ref_is_the_same_element(self):
        for written in (7, "7", "[7]", "ref 7", " [7] "):
            self.assertEqual(ref_number(written), 7)
        self.assertIsNone(ref_number(None))
        self.assertIsNone(ref_number(""))

    def test_form_fields_are_sent_as_numbers(self):
        conn = Connected(lambda m, p: {"page": "Filled 2 fields."})
        try:
            ExtensionBrowser(conn.bridge).act_form([{"ref": "[3]", "value": "Ada"}, {"ref": 4, "value": 1}], submit_ref="12")
            params = conn.fake.sent[-1]["params"]
            self.assertEqual(params["fields"], [{"ref": 3, "value": "Ada"}, {"ref": 4, "value": "1"}])
            self.assertEqual(params["submit_ref"], 12)
            self.assertTrue(params["guard"])
        finally:
            conn.disconnect()


class TestModelGuard(unittest.TestCase):
    """A model clicking 'Place order' in the user's own, signed-in Chrome must ask first."""

    def setUp(self):
        self.conn = Connected(self.answer)
        self.saved = extension.BRIDGE
        extension.BRIDGE = self.conn.bridge
        self.ctx = tools.ToolContext(browser=True)

    def tearDown(self):
        extension.BRIDGE = self.saved
        self.conn.disconnect()

    @staticmethod
    def answer(method, params):
        if method == "click" and params.get("guard"):
            return {"risky": True, "page": '[12] button "Place order"'}
        return {"page": "Clicked [12]."}

    def test_an_unconfirmed_risky_click_is_refused(self):
        reply = tools.browse_click(self.ctx, ref=12)
        self.assertIn("Ask the user first", reply)
        self.assertIn("Place order", reply)

    def test_a_confirmed_click_goes_through(self):
        self.assertEqual(tools.browse_click(self.ctx, ref=12, confirmed=True), "Clicked [12].")

    def test_the_new_tools_are_offered_when_the_browser_is(self):
        names = {schema["function"]["name"] if "function" in schema else schema["name"] for schema in tools.schemas(self.ctx)}
        self.assertTrue({"browse_form", "browse_find"} <= names)


class TestVisibility(unittest.TestCase):
    """You can tell the extension did the work: every call reaches the activity feed."""

    def test_each_call_is_reported_in_a_line(self):
        conn = Connected(lambda m, p: {"page": "ok"})
        seen = []
        conn.bridge.on_call = lambda line, ok: seen.append((line, ok))
        try:
            conn.bridge.call("click", {"target": {"ref": 7}})
            conn.bridge.call("open", {"url": "https://a.test"})
        finally:
            conn.disconnect()
        self.assertTrue(seen[0][0].startswith("click [7] ("))
        self.assertTrue(seen[1][0].startswith("open https://a.test"))
        self.assertTrue(all(ok for _, ok in seen))
        self.assertEqual(conn.bridge.calls, 2)


class TestAntigravity(unittest.TestCase):
    """Antigravity has its own browser built in and gets the rules only on a
    conversation's first turn, so a running conversation is reminded each turn."""

    def setUp(self):
        from pathlib import Path

        from assistant.agents.antigravity_cli import AntigravityCliAgent
        from assistant.config import AntigravityAgentSettings

        self.conn = Connected(lambda m, p: {})
        self.saved = extension.BRIDGE
        extension.BRIDGE = self.conn.bridge
        self.agent = AntigravityCliAgent(AntigravityAgentSettings(), Path("."), True, "RULES")
        self.agent.session_id = "conv-1"  # a conversation already under way

    def tearDown(self):
        extension.BRIDGE = self.saved
        self.conn.disconnect()

    def test_a_later_turn_is_told_to_use_the_extension(self):
        task = self.agent.build_command("agy", "book the table")[2]
        self.assertIn("web open", task)
        self.assertIn("browser_subagent", task)
        self.assertTrue(task.endswith("book the table"))

    def test_no_reminder_when_the_extension_is_not_there(self):
        self.conn.bridge.driver = "playwright"
        self.assertEqual(self.agent.build_command("agy", "book the table")[2], "book the table")

    def test_using_its_own_browser_is_called_out(self):
        step = {"step_type": "tool", "state": "RUNNING", "step_index": 3,
                "tool_name": "open_browser_url", "tool_info": {"parameters": {"Url": "https://a.test"}}}
        with self.assertLogs("nova.agents.antigravity", "WARNING") as logs:
            list(self.agent._parse_step(step))
        self.assertIn("instead of the Nova extension", logs.output[0])


class TestServer(unittest.TestCase):
    def test_the_extension_socket_needs_the_token(self):
        server = make_app()
        sent: list = []
        scope = {
            "type": "websocket", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "ws",
            "path": "/ext", "raw_path": b"/ext", "root_path": "", "query_string": b"token=wrong",
            "headers": [], "client": ("127.0.0.1", 5), "server": ("127.0.0.1", 8799), "subprotocols": [], "state": {},
        }
        incoming = [{"type": "websocket.connect"}]

        async def receive():
            return incoming.pop(0) if incoming else {"type": "websocket.disconnect", "code": 1000}

        async def send(message):
            sent.append(message)

        asyncio.run(asyncio.wait_for(server.app(scope, receive, send), timeout=5))
        self.assertEqual(sent[0]["type"], "websocket.close")
        self.assertEqual(sent[0]["code"], 1008)

    def test_calling_through_the_api_without_chrome_is_a_clear_503(self):
        response = call(make_app(), "POST", "/api/ext", headers={"x-nova-token": TOKEN},
                        json={"method": "snapshot", "params": {}})
        self.assertEqual(response.status_code, 503)
        self.assertIn("isn't connected", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
