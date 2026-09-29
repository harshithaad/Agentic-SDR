"""Our outreach ends with "To opt out, reply STOP." Mail clients quote it back,
so classifying a raw reply marks every quoted reply as an opt-out. These tests
pin the boundary between what the prospect wrote and what we wrote."""
import unittest

from app.stages.common import OPT_OUT_REGEX, sensitive_keywords_in, strip_quoted_reply

GMAIL_REPLY = """Yes, this is interesting - can we talk Tuesday afternoon?

On Mon, Aug 24, 2026 at 6:48 PM <hegwig.owlson@gmail.com> wrote:

> Hi Alex,
>
> Congrats on the recent Notion 3.0 updates.
>
> To opt out, reply STOP.
"""

OUTLOOK_REPLY = """Not interested right now.

-----Original Message-----
From: sender@example.com
Sent: Monday, August 24, 2026
To opt out, reply STOP.
"""

REAL_OPT_OUT = """STOP

On Mon, Aug 24, 2026 at 6:48 PM <hegwig.owlson@gmail.com> wrote:
> Hi Jordan, congrats on the launch.
> To opt out, reply STOP.
"""

SENSITIVE_REPLY = """What's your pricing, and do you have SOC 2 compliance?
Our legal team reviews every vendor.

On Mon, Aug 24, 2026 at 6:48 PM <hegwig.owlson@gmail.com> wrote:
> Hi Riley, congrats on the launch.
> To opt out, reply STOP.
"""


class TestStripQuotedReply(unittest.TestCase):
    def test_gmail_quote_removed(self):
        self.assertEqual(
            strip_quoted_reply(GMAIL_REPLY),
            "Yes, this is interesting - can we talk Tuesday afternoon?",
        )

    def test_outlook_quote_removed(self):
        self.assertEqual(strip_quoted_reply(OUTLOOK_REPLY), "Not interested right now.")

    def test_plain_reply_unchanged(self):
        self.assertEqual(strip_quoted_reply("Sounds good, call me."), "Sounds good, call me.")

    def test_empty_input(self):
        self.assertEqual(strip_quoted_reply(""), "")


class TestQuotingDoesNotFakeOptOut(unittest.TestCase):
    def test_interested_reply_is_not_an_opt_out(self):
        self.assertRegex(GMAIL_REPLY, OPT_OUT_REGEX)  # raw text falsely matches
        self.assertNotRegex(strip_quoted_reply(GMAIL_REPLY), OPT_OUT_REGEX)

    def test_genuine_opt_out_still_detected(self):
        self.assertRegex(strip_quoted_reply(REAL_OPT_OUT), OPT_OUT_REGEX)

    def test_sensitive_keywords_come_from_the_prospect_only(self):
        found = sensitive_keywords_in(strip_quoted_reply(SENSITIVE_REPLY))
        self.assertIn("pricing", found)
        self.assertIn("legal", found)
        self.assertNotRegex(strip_quoted_reply(SENSITIVE_REPLY), OPT_OUT_REGEX)


if __name__ == "__main__":
    unittest.main()
