"""State-machine matrix tests — run with: python -m unittest discover tests"""
import unittest

from app.transitions import (ALL_STATUSES, TERMINAL_STATUSES, TransitionError,
                             VALID_TRANSITIONS, assert_valid, build_transition_sql,
                             is_valid)


class TestTransitionMatrix(unittest.TestCase):
    def test_happy_path_chain(self):
        chain = ["UPLOADED", "RESEARCH_PENDING", "RESEARCH_COMPLETE", "CONTACT_FOUND",
                 "DRAFT_READY", "SENT", "REPLY_RECEIVED", "BOOKING_DRAFTED"]
        for a, b in zip(chain, chain[1:]):
            self.assertTrue(is_valid(a, b), f"{a} -> {b} should be valid")

    def test_follow_up_path_terminates(self):
        self.assertTrue(is_valid("SENT", "FOLLOW_UP_SENT"))
        self.assertTrue(is_valid("FOLLOW_UP_SENT", "CLOSED_LOST"),
                        "FOLLOW_UP_SENT must have a terminal path (audit finding #5)")
        self.assertTrue(is_valid("FOLLOW_UP_SENT", "REPLY_RECEIVED"))

    def test_terminal_states_have_no_exits(self):
        for status in TERMINAL_STATUSES:
            self.assertNotIn(status, VALID_TRANSITIONS,
                             f"{status} is terminal and must have no outgoing transitions")

    def test_illegal_transitions_raise(self):
        with self.assertRaises(TransitionError):
            assert_valid("UPLOADED", "SENT")
        with self.assertRaises(TransitionError):
            assert_valid("CLOSED_LOST", "SENT")
        with self.assertRaises(TransitionError):
            assert_valid("SENT", "DRAFT_READY")

    def test_human_review_is_reachable_and_recoverable(self):
        reachable_from = [s for s, targets in VALID_TRANSITIONS.items()
                          if "HUMAN_REVIEW" in targets]
        self.assertGreaterEqual(len(reachable_from), 4)
        self.assertIn("DRAFT_READY", VALID_TRANSITIONS["HUMAN_REVIEW"])
        self.assertIn("CLOSED_LOST", VALID_TRANSITIONS["HUMAN_REVIEW"])

    def test_every_target_is_a_known_status(self):
        for targets in VALID_TRANSITIONS.values():
            for t in targets:
                self.assertIn(t, ALL_STATUSES)


class TestTransitionSql(unittest.TestCase):
    def test_sql_includes_cas_guard(self):
        sql, _ = build_transition_sql("UPLOADED", "RESEARCH_PENDING", [])
        self.assertIn("WHERE id = %(lead_id)s AND status = %(from_status)s", sql)
        self.assertIn("version = version + 1", sql)

    def test_unknown_column_rejected(self):
        with self.assertRaises(TransitionError):
            build_transition_sql("UPLOADED", "RESEARCH_PENDING", ["evil_column"])

    def test_illegal_pair_rejected_before_sql(self):
        with self.assertRaises(TransitionError):
            build_transition_sql("UPLOADED", "BOOKING_DRAFTED", [])


if __name__ == "__main__":
    unittest.main()
