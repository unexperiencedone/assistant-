"""Nova's control of the phone: what it will ask for, and what the phone will do.

The bridge is tested against a real HTTP server on loopback rather than a mock, because
the parts worth checking are the wire: the token, the confirmation gate, and the fact
that only named actions exist.
"""

import importlib.util
import json
import os
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

from assistant.intents import match_intent
from assistant.phone import PhoneBridge, PhoneError

BRIDGE_PATH = Path(__file__).resolve().parent.parent / "phone" / "nova_bridge.py"


def load_phone_side():
    """The script that runs inside Termux, imported here as a module."""
    spec = importlib.util.spec_from_file_location("nova_bridge", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phone_side = load_phone_side()
TOKEN = "phone-test-token"


class TestWhatThePhoneWillRun(unittest.TestCase):
    """`build` is the whole vocabulary: no shell string ever reaches the phone."""

    def test_a_fixed_action_maps_to_its_command(self):
        self.assertEqual(phone_side.build("torch_on", {})[0], ["termux-torch", "on"])

    def test_an_unknown_action_builds_nothing(self):
        command, why = phone_side.build("format_the_phone", {})
        self.assertIsNone(command)
        self.assertIn("do not know how", why)

    def test_a_known_app_opens_by_its_own_scheme(self):
        self.assertEqual(phone_side.build("open", {"target": "spotify"})[0],
                         ["termux-open-url", "spotify://"])

    def test_an_unknown_app_is_refused_rather_than_guessed(self):
        command, why = phone_side.build("open", {"target": "frobnicator"})
        self.assertIsNone(command)
        self.assertIn("do not know how to open", why)

    def test_a_target_with_shell_characters_never_gets_through(self):
        for nasty in ["spotify; rm -rf ~", "$(whoami)", "`id`", "a|b"]:
            command, why = phone_side.build("open", {"target": nasty})
            self.assertIsNone(command, nasty)
            self.assertIn("will not pass on", why)

    def test_a_message_needs_a_plausible_number(self):
        self.assertIsNone(phone_side.build("sms_send", {"number": "nope", "text": "hi"})[0])
        self.assertEqual(phone_side.build("sms_send", {"number": "+91 98765 43210", "text": "late"})[0],
                         ["termux-sms-send", "-s", "0", "-n", "+91 98765 43210", "late"])

    def test_a_message_goes_out_on_the_asked_for_sim(self):
        self.assertEqual(phone_side.build("sms_send", {"number": "+91 1", "text": "hi", "sim": 1})[0][:3],
                         ["termux-sms-send", "-s", "1"])
        # A nonsense slot falls back to SIM 1 rather than failing the send.
        self.assertEqual(phone_side.build("sms_send", {"number": "+91 1", "text": "hi", "sim": "left"})[0][2], "0")

    def test_the_volume_level_stays_in_range(self):
        self.assertEqual(phone_side.build("volume", {"stream": "music", "level": 99})[0][-1], "15")
        self.assertIsNone(phone_side.build("volume", {"stream": "rocket", "level": 3})[0])


class PhoneServer:
    """The real handler, on loopback, with the commands stubbed out."""

    def __init__(self):
        phone_side.Handler.token = TOKEN
        self.ran: list[list[str]] = []
        self.original = phone_side.run
        phone_side.run = self.fake_run
        self.server = HTTPServer(("127.0.0.1", 0), phone_side.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def fake_run(self, command):
        self.ran.append(command)
        return True, "stub output"

    def stop(self):
        phone_side.run = self.original
        self.server.shutdown()
        self.server.server_close()


class TestTheWire(unittest.TestCase):
    def setUp(self):
        self.phone = PhoneServer()
        os.environ["NOVA_PHONE_TEST_TOKEN"] = TOKEN
        self.settings = SimpleNamespace(enabled=True, host="127.0.0.1", port=self.phone.port,
                                        token_env="NOVA_PHONE_TEST_TOKEN", timeout=5,
                                        confirm_destructive=True)
        self.bridge = PhoneBridge(self.settings)

    def tearDown(self):
        self.phone.stop()
        os.environ.pop("NOVA_PHONE_TEST_TOKEN", None)

    def test_an_action_reaches_the_phone(self):
        result = self.bridge.call("torch_on")
        self.assertTrue(result["ok"])
        self.assertEqual(self.phone.ran, [["termux-torch", "on"]])

    def test_the_wrong_token_gets_nowhere(self):
        os.environ["NOVA_PHONE_TEST_TOKEN"] = "not-the-token"
        with self.assertRaises(PhoneError) as caught:
            self.bridge.call("torch_on")
        self.assertIn("token", str(caught.exception).lower())
        self.assertEqual(self.phone.ran, [], "nothing ran on the phone")

    def test_a_message_is_refused_until_it_is_confirmed(self):
        with self.assertRaises(PhoneError) as caught:
            self.bridge.call("sms_send", {"number": "+91 98765 43210", "text": "late"})
        self.assertIn("confirm", str(caught.exception).lower())
        self.assertEqual(self.phone.ran, [], "an unconfirmed message never reaches the phone")

    def test_a_confirmed_message_goes(self):
        result = self.bridge.call("sms_send", {"number": "+91 98765 43210", "text": "late"}, confirmed=True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.phone.ran[0][:4], ["termux-sms-send", "-s", "0", "-n"])

    def test_health_says_the_phone_is_there(self):
        self.assertTrue(self.bridge.reachable())

    def test_an_action_nova_does_not_know_never_leaves_the_pc(self):
        with self.assertRaises(PhoneError):
            self.bridge.call("wipe_everything")
        self.assertEqual(self.phone.ran, [])


class TestNotSetUp(unittest.TestCase):
    def test_it_says_where_to_look(self):
        bridge = PhoneBridge(SimpleNamespace(enabled=True, host="", token_env="NOTHING", port=1))
        with self.assertRaises(PhoneError) as caught:
            bridge.call("torch_on")
        self.assertIn("README", str(caught.exception))


class TestThePhrases(unittest.TestCase):
    def matched(self, phrase):
        intent = match_intent(phrase)
        return (intent.name, intent.args) if intent else (None, {})

    def test_the_phone_commands_are_recognised(self):
        self.assertEqual(self.matched("turn on the torch")[0], "phone_torch")
        self.assertEqual(self.matched("torch off")[0], "phone_torch")
        self.assertEqual(self.matched("what's my phone battery")[0], "phone_battery")
        self.assertEqual(self.matched("find my phone")[0], "phone_find")
        self.assertEqual(self.matched("is my phone there")[0], "phone_status")

    def test_open_and_notify_carry_their_argument(self):
        self.assertEqual(self.matched("open spotify on my phone"), ("phone_open", {"target": "spotify"}))
        self.assertEqual(self.matched("notify my phone saying dinner is ready"),
                         ("phone_notify", {"body": "dinner is ready"}))

    def test_a_text_message_carries_both(self):
        name, args = self.matched("text +91 98765 43210 saying running late")
        self.assertEqual(name, "phone_sms")
        self.assertEqual(args["number"], "+91 98765 43210")
        self.assertEqual(args["body"], "running late")

    def test_ordinary_requests_still_go_to_the_agent(self):
        self.assertIsNone(match_intent("play a song"))
        self.assertIsNone(match_intent("summarise what changed in the repo"))


if __name__ == "__main__":
    unittest.main()


class TestCallsAreAskedAboutToo(unittest.TestCase):
    """Dialling is louder than a text and just as final."""

    def test_the_phone_refuses_an_unconfirmed_call(self):
        server = PhoneServer()
        os.environ["NOVA_PHONE_TEST_TOKEN"] = TOKEN
        bridge = PhoneBridge(SimpleNamespace(enabled=True, host="127.0.0.1", port=server.port,
                                             token_env="NOVA_PHONE_TEST_TOKEN", timeout=5))
        try:
            with self.assertRaises(PhoneError) as caught:
                bridge.call("call_dial", {"number": "+91 98765 43210"})
            self.assertIn("confirm", str(caught.exception).lower())
            self.assertEqual(server.ran, [], "nothing was dialled")
            result = bridge.call("call_dial", {"number": "+91 98765 43210"}, confirmed=True)
            self.assertTrue(result["ok"])
            self.assertEqual(server.ran[0], ["termux-telephony-call", "+91 98765 43210"])
        finally:
            server.stop()
            os.environ.pop("NOVA_PHONE_TEST_TOKEN", None)

    def test_only_a_number_can_be_dialled(self):
        self.assertIsNone(phone_side.build("call_dial", {"number": "mum"})[0])
        self.assertIsNone(phone_side.build("call_dial", {"number": "112; rm -rf ~"})[0])

    def test_a_number_or_a_name_matches_but_an_idiom_does_not(self):
        self.assertEqual(match_intent("call +91 98765 43210").name, "phone_call")
        self.assertEqual(match_intent("call mum").name, "phone_call_name")
        for idiom in ("call it a day", "call me back", "call an ambulance", "call the meeting off",
                      "call back later", "call you later"):
            found = match_intent(idiom)
            self.assertNotIn(getattr(found, "name", ""), ("phone_call", "phone_call_name"), idiom)


class TestFindingAContact(unittest.TestCase):
    """A name only becomes a number when there is exactly one sensible match."""

    CONTACTS = ('[{"name":"Mum","number":"+91 90000 11111"},'
                '{"name":"Sam Ali","number":"+91 90000 22222"},'
                '{"name":"Sam Roy","number":"+91 90000 33333"},'
                '{"name":"Karan","number":"+91 90000 44444"}]')

    def find(self, name):
        from assistant.phone.bridge import find_number
        return find_number(self.CONTACTS, name)

    def test_an_exact_name(self):
        self.assertEqual(self.find("mum"), ("Mum", "+91 90000 11111"))

    def test_a_near_spelling(self):
        self.assertEqual(self.find("kar an"), ("Karan", "+91 90000 44444"))

    def test_two_equally_good_matches_resolve_to_nothing(self):
        self.assertEqual(self.find("sam"), ("", ""), "two Sams: ask, never guess")

    def test_a_fuller_name_picks_the_right_one(self):
        self.assertEqual(self.find("sam roy"), ("Sam Roy", "+91 90000 33333"))

    def test_a_stranger_is_not_invented(self):
        self.assertEqual(self.find("nobody"), ("", ""))

    def test_rubbish_input_is_survivable(self):
        from assistant.phone.bridge import find_number
        self.assertEqual(find_number("not json", "mum"), ("", ""))
        self.assertEqual(find_number("[]", ""), ("", ""))


class TestReadingIsGatedByTheProfile(unittest.TestCase):
    """Doing things is always allowed; reading is not."""

    def setUp(self):
        from assistant.profile.schema import normalize
        self.profile = normalize({})

    def allowed(self, action, profile=None):
        from assistant.phone.bridge import sensing_allowed
        return sensing_allowed(self.profile if profile is None else profile, action)

    def test_doing_something_is_never_gated(self):
        for action in ("torch_on", "notify", "call_dial", "open"):
            self.assertTrue(self.allowed(action), action)

    def test_messages_and_logs_are_off_by_default(self):
        self.assertFalse(self.allowed("sms_list"))
        self.assertFalse(self.allowed("call_log"))
        self.assertFalse(self.allowed("notifications"))

    def test_contacts_are_on_so_that_call_mum_works(self):
        self.assertTrue(self.allowed("contacts"))

    def test_the_master_switch_turns_everything_off(self):
        self.profile["phone"]["messages"] = True
        self.profile["phone"]["sensing"] = False
        self.assertFalse(self.allowed("contacts"))
        self.assertFalse(self.allowed("sms_list"))
        self.assertTrue(self.allowed("torch_on"), "doing is still doing")

    def test_no_profile_means_no_reading(self):
        self.assertFalse(self.allowed("contacts", profile={}))


class FakeBridge:
    configured = True

    def __init__(self):
        self.calls = []

    def call(self, action, args=None, confirmed=False):
        self.calls.append((action, args, confirmed))
        return {"ok": True, "output": "done"}


class TestPhoneStepsInAutomations(unittest.TestCase):
    """An automation drives the phone with no model in the loop — which is the point —
    but must not be able to fire the things that need a human."""

    def runner(self):
        from assistant.automation.macros import MacroRunner
        from assistant.events import EventBus
        self.phone = FakeBridge()
        return MacroRunner(EventBus(), open_app=lambda name: True, say=lambda text: None, phone=self.phone)

    def test_a_plain_action_runs(self):
        runner = self.runner()
        detail = runner._execute({"do": "phone", "action": "torch_on"}, {}, None)
        self.assertEqual(self.phone.calls, [("torch_on", {}, False)])
        self.assertEqual(detail, "done")

    def test_arguments_are_passed_through(self):
        runner = self.runner()
        runner._execute({"do": "phone", "action": "notify", "title": "Nova", "text": "tea"}, {}, None)
        self.assertEqual(self.phone.calls[0][1], {"title": "Nova", "text": "tea"})

    def test_a_text_message_cannot_be_automated(self):
        from assistant.automation.macros import MacroError
        runner = self.runner()
        with self.assertRaises(MacroError) as caught:
            runner._execute({"do": "phone", "action": "sms_send", "number": "+91 1", "text": "hi"}, {}, None)
        self.assertIn("confirm", str(caught.exception))
        self.assertEqual(self.phone.calls, [], "nothing reached the phone")

    def test_a_call_cannot_be_automated_either(self):
        from assistant.automation.macros import MacroError
        runner = self.runner()
        with self.assertRaises(MacroError):
            runner._execute({"do": "phone", "action": "call_dial", "number": "+91 1"}, {}, None)
        self.assertEqual(self.phone.calls, [])

    def test_an_unconfigured_bridge_says_where_to_look(self):
        from assistant.automation.macros import MacroError, MacroRunner
        from assistant.events import EventBus
        runner = MacroRunner(EventBus(), open_app=lambda n: True, say=lambda t: None, phone=None)
        with self.assertRaises(MacroError) as caught:
            runner._execute({"do": "phone", "action": "torch_on"}, {}, None)
        self.assertIn("README", str(caught.exception))
