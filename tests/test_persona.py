"""Nova's character, and the identity it keeps.

Three layers are tested here, because each catches what the one before it missed: the
instant intent (so no model is ever asked), the seed every backend is given, and the
guard on the way out.

The most important test in this file is the one that checks the guard leaves an honest
answer alone. Answering as Nova is branding; denying what it runs on would be a lie, and
the character's first value is not lying.
"""

import unittest

from assistant import intents, persona
from assistant.persona import guard


class TestNobodyElseAnswersWhoAreYou(unittest.TestCase):
    def test_the_question_never_reaches_a_model(self):
        for said in ("who are you", "what are you", "who r u", "whats your name",
                     "what's your name", "what is your name", "introduce yourself",
                     "are you an ai", "are you a bot", "are you human", "are you claude"):
            with self.subTest(said=said):
                found = intents.match_intent(said)
                self.assertIsNotNone(found, said)
                self.assertEqual(found.name, "identity", said)

    def test_the_same_words_inside_a_message_are_not_the_question(self):
        """"text Priya saying who are you" is an SMS, not an introduction."""
        found = intents.match_intent("text priya saying who are you")
        self.assertEqual(found.name, "phone_sms_name")
        self.assertEqual(found.args["body"], "who are you")

    def test_a_question_about_somebody_else_is_left_alone(self):
        for said in ("who are the directors of this company", "who is the president",
                     "what are the options"):
            found = intents.match_intent(said)
            self.assertNotEqual(getattr(found, "name", ""), "identity", said)


class TestWhatNovaSaysItIs(unittest.TestCase):
    def setUp(self):
        persona.configure("Nova", "Kaiketsu Tech")

    def test_it_names_itself_and_its_maker_not_a_model(self):
        line = persona.identity_line(register=persona.CUSTOMER)
        self.assertIn("Nova", line)
        self.assertIn("Kaiketsu Tech", line)
        for leaked in ("Claude", "Groq", "OpenRouter", "Llama", "language model"):
            self.assertNotIn(leaked.lower(), line.lower())

    @staticmethod
    def flat(text):
        """The seed is wrapped prose; assertions are about words, not line breaks."""
        return " ".join(text.lower().split())

    def test_the_seed_forbids_introducing_itself_as_a_model(self):
        seed = self.flat(persona.seed())
        self.assertIn("never introduce yourself as any model", seed)
        self.assertIn("never blame a provider", seed)

    def test_the_seed_also_forbids_denying_it(self):
        """The line between branding and lying, stated in the prompt itself."""
        self.assertIn("never deny it", self.flat(persona.seed()))

    def test_both_registers_are_the_same_character(self):
        owner = self.flat(persona.character(register=persona.OWNER))
        customer = self.flat(persona.character(register=persona.CUSTOMER))
        for value in ("telling the truth about what happened", "no flattery"
                      if "no flattery" in owner else "not a flatterer"):
            self.assertIn(value, owner)
            self.assertIn(value, customer)
        self.assertIn("never commit to a price", customer)
        self.assertNotIn("never commit to a price", owner)


class TestTheGuard(unittest.TestCase):
    IDENTITY = "I'm Nova, an assistant made by Kaiketsu Tech."

    def scrub(self, text):
        return guard.scrub(text, self.IDENTITY)

    def test_a_backend_introducing_itself_is_replaced(self):
        for said in ("I'm Claude Code, an AI assistant.",
                     "I am Claude, made by Anthropic.",
                     "I'm a large language model trained to help.",
                     "I am an AI assistant created by Google.",
                     "I'm Gemini, how can I help?"):
            with self.subTest(said=said):
                reply, tripped = self.scrub(said)
                self.assertEqual(tripped, "identity", said)
                self.assertEqual(reply, self.IDENTITY)

    def test_blaming_the_plumbing_is_removed_but_the_rest_survives(self):
        reply, tripped = self.scrub("The API returned an error. I'll try again in a moment.")
        self.assertEqual(tripped, "blame")
        self.assertEqual(reply, "I'll try again in a moment.")

    def test_a_reply_that_was_only_blame_becomes_an_owned_failure(self):
        reply, tripped = self.scrub("The provider failed.")
        self.assertEqual(tripped, "blame")
        self.assertEqual(reply, guard.OWNED_FAILURE)

    def test_an_honest_answer_about_what_it_runs_on_is_left_alone(self):
        """Branding is fine. Lying is not -- and scrubbing this would be lying."""
        honest = "Kaiketsu Tech builds me on models from a few providers."
        reply, tripped = self.scrub(honest)
        self.assertEqual(tripped, "")
        self.assertEqual(reply, honest)

    def test_merely_mentioning_a_model_is_not_a_leak(self):
        for said in ("Claude wrote that file for you.",
                     "I asked Claude to finish it.",
                     "I couldn't finish that."):
            with self.subTest(said=said):
                self.assertEqual(self.scrub(said)[1], "", said)

    def test_an_empty_reply_is_not_mangled(self):
        self.assertEqual(self.scrub(""), ("", ""))


class TestAnElaboratedQuestion(unittest.TestCase):
    """"Who are you, who built you and how do you work" is three questions.

    A one-line canned answer to it is its own kind of wrong -- it ignores two thirds of
    what was asked, which is exactly the moment someone is paying attention to the
    character. And any phrasing the intent misses reaches a backend that answers as
    itself.
    """

    def setUp(self):
        persona.configure("Nova", "Kaiketsu Tech")

    def test_the_wider_phrasings_still_never_reach_a_model(self):
        for said in ("who built you", "who made you", "how do you work",
                     "how were you made", "tell me about yourself", "describe yourself",
                     "what ai do you use", "what model are you",
                     "are you built on gpt", "what technology do you use",
                     "who are you and who built you and how do you work"):
            with self.subTest(said=said):
                found = intents.match_intent(said)
                self.assertIsNotNone(found, said)
                self.assertEqual(found.name, "identity", said)

    def test_a_question_about_the_world_is_not_about_nova(self):
        for said in ("who built the taj mahal", "how do solar panels work",
                     "who made this pull request"):
            found = intents.match_intent(said)
            self.assertNotEqual(getattr(found, "name", ""), "identity", said)

    def test_each_part_asked_gets_answered(self):
        answer = persona.about("who are you and who built you and how do you work")
        self.assertIn("Nova", answer)
        self.assertIn("Kaiketsu Tech built me", answer)
        self.assertIn("fraction of a second", answer)

    def test_only_what_was_asked_is_answered(self):
        short = persona.about("who are you")
        self.assertNotIn("fraction of a second", short)
        self.assertEqual(short, persona.identity_line())

    def test_asked_what_it_runs_on_it_tells_the_truth(self):
        answer = persona.about("what ai do you use")
        self.assertIn("models from a few providers", answer)
        for denial in ("I do not use", "I don't use", "no outside", "not Claude"):
            self.assertNotIn(denial.lower(), answer.lower())

    def test_no_answer_it_composes_would_trip_its_own_guard(self):
        """The local answers must survive the net that catches the backends."""
        for said in ("who are you", "who built you", "how do you work", "what ai do you use",
                     "who are you and who built you and how do you work"):
            for register in (persona.OWNER, persona.CUSTOMER):
                answer = persona.about(said, register=register)
                self.assertEqual(guard.scrub(answer, "x")[1], "", f"{said} / {register}")


class TestTheQuieterLeak(unittest.TestCase):
    ID = "I'm Nova, made by Kaiketsu Tech."

    def test_naming_a_provider_as_what_it_is(self):
        for said in ("I'm powered by Claude.", "I run on Claude 3.5.",
                     "My underlying model is GPT-4.", "I am based on Llama."):
            with self.subTest(said=said):
                self.assertEqual(guard.scrub(said, self.ID)[1], "identity", said)

    def test_the_honest_general_answer_names_no_provider_and_survives(self):
        honest = "Kaiketsu Tech builds me on AI models from a few providers."
        self.assertEqual(guard.scrub(honest, self.ID), (honest, ""))


class TestAnsweringOnlyWhatWasAsked(unittest.TestCase):
    """Padding is the opposite of this character's second value: say it and stop."""

    def setUp(self):
        persona.configure("Nova", "Kaiketsu Tech")

    def test_who_built_you_gets_an_answer_not_an_introduction(self):
        answer = persona.about("who built you")
        self.assertTrue(answer.startswith("Kaiketsu Tech built me"), answer)
        self.assertNotIn("I live on this laptop", answer)

    def test_what_it_runs_on_gets_an_answer_not_an_introduction(self):
        answer = persona.about("are you built on gpt")
        self.assertTrue(answer.startswith("Kaiketsu Tech builds me on AI models"), answer)
        self.assertNotIn("I live on this laptop", answer)

    def test_how_do_you_work_does_not_introduce_itself_first(self):
        self.assertNotIn("I live on this laptop", persona.about("how do you work"))

    def test_asking_what_it_is_does_lead_with_that(self):
        for said in ("who are you", "tell me about yourself",
                     "so what exactly am I talking to here"):
            with self.subTest(said=said):
                self.assertTrue(persona.about(said).startswith("I'm Nova"), said)

    def test_a_three_part_question_still_gets_three_parts(self):
        answer = persona.about("who are you, who built you and how do you work")
        self.assertIn("I'm Nova", answer)
        self.assertIn("Kaiketsu Tech built me", answer)
        self.assertIn("fraction of a second", answer)

    def test_the_maker_is_not_named_twice_to_a_customer(self):
        answer = persona.about("who are you and who built you", register=persona.CUSTOMER)
        self.assertEqual(answer.count("Kaiketsu Tech"), 1, answer)

    def test_with_no_maker_configured_it_says_nothing_that_reads_like_a_placeholder(self):
        persona.configure("Nova", "")
        for said in ("who are you", "who built you", "what ai do you use"):
            answer = persona.about(said).lower()
            for placeholder in ("{maker}", "its maker", "your maker", "my maker"):
                self.assertNotIn(placeholder, answer, f"{said}: {answer}")
        persona.configure("Nova", "Kaiketsu Tech")


if __name__ == "__main__":
    unittest.main()
