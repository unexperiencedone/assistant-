"""The profile: validated against its schema, shown to each agent only as allowed, applied everywhere."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from assistant.agents.antigravity_cli import AntigravityCliAgent
from assistant.agents.chat_api import GroqAgent
from assistant.agents.claude_cli import ClaudeCliAgent
from assistant.config import AntigravityAgentSettings, ClaudeAgentSettings, GroqAgentSettings
from assistant.events import EventBus
from assistant.profile import ProfileService, audience_for
from assistant.profile.render import relevant_context, speech_words, standing_context
from assistant.profile.schema import normalize


def sample(**overrides):
    profile = {
        "identity": {"name": "Asha Rao", "preferred_name": "Asha", "headline": "ML student",
                     "education": "B.Tech, CGPA 9.1.", "languages": ["Hindi", "English"]},
        "goals": {"career": ["Game developer"]},
        "skills": {"items": [{"name": "PyTorch", "level": "strong"}, {"name": "Hardware", "level": "learning"}],
                   "avoid": ["Hardware-dependent problems"]},
        "work_style": {"code": "Inline code with file placement."},
        "devices": {"items": [{"name": "Laptop", "gpu": "none"}]},
        "projects": {"items": [{"name": "Drishtikon", "aliases": ["SIH"], "team": ["Ravi"], "summary": "Satellite VLM."},
                               {"name": "Old thing", "status": "done"}]},
        "people": {"items": [{"name": "Ravi", "relation": "teammate"}]},
        "vocabulary": {"words": ["Kaiketsu"]},
    }
    profile.update(overrides)
    return normalize(profile)


class Schema(unittest.TestCase):
    def test_bad_input_becomes_a_valid_profile(self):
        p = normalize({"identity": {"name": 42, "languages": "Hindi, English, hindi", "bogus": 1},
                       "skills": {"items": [{"name": "Go", "level": "godlike"}, {"level": "strong"}]},
                       "visibility": {"identity": "everyone"}, "unknown_section": {}})
        self.assertEqual("", p["identity"]["name"])
        self.assertEqual(["Hindi", "English"], p["identity"]["languages"])
        self.assertNotIn("bogus", p["identity"])
        self.assertEqual([{"name": "Go", "level": "comfortable", "notes": ""}], p["skills"]["items"])  # nameless dropped
        self.assertEqual("trusted", p["visibility"]["identity"])  # invalid choice -> section default
        self.assertNotIn("unknown_section", p)

    def test_empty_profile_says_nothing(self):
        self.assertEqual("", standing_context(normalize({}), "trusted"))


class Visibility(unittest.TestCase):
    def test_free_models_never_see_trusted_sections(self):
        p = sample()
        trusted, everyone = standing_context(p, "trusted"), standing_context(p, "all")
        self.assertIn("Asha Rao", trusted)
        self.assertNotIn("Asha Rao", everyone)      # identity is Claude & Antigravity only by default
        self.assertNotIn("Drishtikon", everyone)
        self.assertIn("PyTorch", everyone)          # skills are shared with every agent
        self.assertEqual("", relevant_context(p, "work on drishtikon", "all"))

    def test_local_sections_never_reach_a_prompt(self):
        p = sample()
        p["visibility"]["skills"] = "local"
        self.assertNotIn("PyTorch", standing_context(p, "trusted"))
        self.assertNotIn("Kaiketsu", standing_context(p, "trusted"))  # vocabulary is local
        self.assertIn("Kaiketsu", speech_words(p))                    # ...but still helps speech recognition

    def test_personalization_off_sends_nothing(self):
        p = sample(privacy={"personalize": False})
        self.assertEqual("", standing_context(p, "trusted"))
        self.assertEqual("", relevant_context(p, "drishtikon", "trusted"))

    def test_backends_map_to_audiences(self):
        self.assertEqual("trusted", audience_for("claude"))
        self.assertEqual("trusted", audience_for("antigravity"))
        self.assertEqual("all", audience_for("groq"))
        self.assertEqual("all", audience_for("openrouter"))


class Context(unittest.TestCase):
    def test_mentions_pull_in_details_by_name_or_alias(self):
        p = sample()
        text = relevant_context(p, "push the SIH readme and tell ravi", "trusted")
        self.assertIn("Satellite VLM", text)
        self.assertIn("Person Ravi", text)
        self.assertEqual("", relevant_context(p, "open sihx and ravioli", "trusted"))  # whole words only

    def test_standing_lists_current_projects_only(self):
        text = standing_context(sample(), "trusted")
        self.assertIn("Drishtikon", text)
        self.assertNotIn("Old thing", text)
        self.assertNotIn("9.1..", text)


class Service(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.bus = EventBus()
        self.events = []
        self.bus.subscribe("profile", lambda e: self.events.append(e.data))
        self.service = ProfileService(self.folder / "profile.json", self.bus)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_update_saves_publishes_and_notifies(self):
        seen = []
        self.service.on_change(lambda s: seen.append(s.revision))
        self.service.update(sample())
        self.assertEqual("Asha Rao", json.loads((self.folder / "profile.json").read_text(encoding="utf-8"))["identity"]["name"])
        self.assertEqual([{"revision": 1, "name": "Asha"}], self.events)
        self.assertEqual([1], seen)
        self.assertEqual("Asha", ProfileService(self.folder / "profile.json").preferred_name)  # survives a restart

    def test_prompt_context_depends_on_the_agent(self):
        self.service.update(sample())
        self.assertTrue(self.service.with_context("P", "fix drishtikon", "claude").endswith("\n\nP"))
        self.assertEqual("P", self.service.with_context("P", "fix drishtikon", "groq"))

    def test_project_folder_opens_by_name(self):
        project = self.folder / "drishtikon"
        project.mkdir()
        self.service.update(sample(projects={"items": [{"name": "Drishtikon", "aliases": ["SIH"], "folder": str(project)}]}))
        self.assertEqual(str(project), self.service.folder_for("SIH"))
        self.assertEqual(str(project), self.service.folder_for("drishtikon project"))
        self.assertIsNone(self.service.folder_for("something else"))

    def test_hotwords_lead_with_the_wake_word_once(self):
        self.service.update(sample(projects={"items": [{"name": "Nova"}, {"name": "Drishtikon"}]}))
        words = self.service.hotwords("nova").split(", ")
        self.assertEqual("Nova", words[0])
        self.assertEqual(1, sum(w.lower() == "nova" for w in words))


class Agents(unittest.TestCase):
    def test_claude_restarts_between_turns_to_pick_up_a_change(self):
        agent = ClaudeCliAgent(ClaudeAgentSettings(), Path("."), True, "RULES")
        agent.set_profile("About the user: X")
        self.assertEqual("RULES\n\nAbout the user: X", agent.instructions)
        self.assertFalse(agent._restart_for_instructions)  # no process yet: nothing to restart
        agent._proc = object()
        agent.set_profile("About the user: Y")
        self.assertTrue(agent._restart_for_instructions)
        self.assertEqual(agent.instructions, agent.spawn().instructions)

    def test_antigravity_is_told_once_mid_conversation(self):
        agent = AntigravityCliAgent(AntigravityAgentSettings(), Path("."), True, "RULES")
        agent.session_id = "conv-1"
        agent.set_profile("About the user: X")
        first = agent.build_command("agy", "task one")
        second = agent.build_command("agy", "task two")
        self.assertIn("About the user: X", first[2])
        self.assertNotIn("About the user", second[2])

    def test_chat_models_get_it_in_the_system_prompt(self):
        agent = GroqAgent(GroqAgentSettings(), Path("."), True, "Nova")
        agent.set_profile("About the user: X")
        self.assertTrue(agent._system_prompt().endswith("About the user: X"))
        self.assertEqual("About the user: X", agent.spawn().profile)


if __name__ == "__main__":
    unittest.main()
