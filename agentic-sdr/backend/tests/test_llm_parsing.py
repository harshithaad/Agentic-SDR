"""JSON extraction must survive how models actually emit output: markdown
fences, leading prose, and truncation at the token cap."""
import unittest

from app.llm import LLMOutputInvalid, extract_json


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_leading_prose(self):
        self.assertEqual(extract_json('Here you go:\n{"a": 1}'), {"a": 1})

    def test_truncated_mid_string(self):
        raw = '```json\n{"company_summary": "Anthropic is an AI safety comp'
        self.assertEqual(
            extract_json(raw), {"company_summary": "Anthropic is an AI safety comp"}
        )

    def test_truncated_after_comma(self):
        raw = '{"industry": "SaaS", "pain_points": ["slow onboarding"],'
        self.assertEqual(
            extract_json(raw), {"industry": "SaaS", "pain_points": ["slow onboarding"]}
        )

    def test_truncated_dangling_key(self):
        raw = '{"industry": "SaaS", "company_summary":'
        self.assertEqual(extract_json(raw), {"industry": "SaaS"})

    def test_truncated_nested_array(self):
        raw = '{"a": 1, "pain_points": ["one", "two'
        self.assertEqual(extract_json(raw), {"a": 1, "pain_points": ["one", "two"]})

    def test_illegal_apostrophe_escape(self):
        # models emit \' (valid in Python/JS, illegal in JSON)
        raw = '{"subject_line": "Render\\\'s pipeline"}'
        self.assertEqual(extract_json(raw), {"subject_line": "Render's pipeline"})

    def test_truncated_mid_escape_sequence(self):
        # cut off right after a backslash: the dangling escape must not swallow
        # the closing quote the repair adds
        raw = '{"email_body": "Hi Anurag,\\n\\'
        self.assertEqual(extract_json(raw), {"email_body": "Hi Anurag,\n"})

    def test_no_json_at_all_raises(self):
        with self.assertRaises(LLMOutputInvalid):
            extract_json("I cannot help with that request.")


if __name__ == "__main__":
    unittest.main()
