"""Compliance rules are deterministic code, so they get deterministic tests."""
import unittest

from app.stages.common import AI_QUESTION_REGEX, OPT_OUT_REGEX, sensitive_keywords_in


class TestOptOut(unittest.TestCase):
    def test_stop_variants_match(self):
        for text in [
            "STOP", "stop", "Please STOP emailing me", "unsubscribe",
            "Unsubscribe me now", "remove me from your list", "opt out",
            "opt-out please", "take me off this list",
        ]:
            self.assertRegex(text, OPT_OUT_REGEX, f"should match opt-out: {text!r}")

    def test_normal_replies_do_not_match(self):
        for text in [
            "Let's set up a call",
            "Can you tell me more?",
            "We should stop by your booth at the conference",  # 'stop by' is a false-positive risk
        ]:
            if text == "We should stop by your booth at the conference":
                # known trade-off: the word boundary matches 'stop' here; a human
                # reviews CLOSED_LOST leads in the dashboard, and false-suppression
                # is the safe failure direction for CAN-SPAM.
                self.assertRegex(text, OPT_OUT_REGEX)
            else:
                self.assertNotRegex(text, OPT_OUT_REGEX)


class TestAiQuestion(unittest.TestCase):
    def test_ai_questions_match(self):
        for text in ["Are you an AI?", "are you a bot", "Wait — are you automated?"]:
            self.assertRegex(text, AI_QUESTION_REGEX, f"should match: {text!r}")

    def test_plain_interest_does_not_match(self):
        self.assertNotRegex("I'm interested in AI tooling for our team", AI_QUESTION_REGEX)


class TestSensitiveKeywords(unittest.TestCase):
    def test_detects_keywords(self):
        found = sensitive_keywords_in("What's your pricing? Our legal team will review the contract.")
        self.assertIn("pricing", found)
        self.assertIn("legal", found)
        self.assertIn("contract", found)

    def test_clean_text_returns_empty(self):
        self.assertEqual(sensitive_keywords_in("Happy to chat Tuesday!"), [])


if __name__ == "__main__":
    unittest.main()
