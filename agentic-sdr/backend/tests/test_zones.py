"""Two-zone architecture contracts: materialization targets, payload chaining,
and reaper re-drive edges."""
import unittest

from app import events
from app.transitions import (HOT_PATH_STATUSES, MATERIALIZE_TARGETS,
                             VALID_TRANSITIONS, is_valid)


class TestZoneBoundary(unittest.TestCase):
    def test_materialize_targets_are_the_stream_exits(self):
        self.assertEqual(
            MATERIALIZE_TARGETS,
            {"RESEARCH_FAILED", "NO_CONTACT_FOUND", "HUMAN_REVIEW", "DRAFT_READY"},
        )

    def test_hot_path_statuses(self):
        self.assertEqual(
            HOT_PATH_STATUSES,
            {"RESEARCH_PENDING", "RESEARCH_COMPLETE", "CONTACT_FOUND"},
        )

    def test_every_materialize_target_is_reachable_via_legal_chain(self):
        # composite writes must correspond to a legal path through the matrix
        reachable = set()
        frontier = {"RESEARCH_PENDING"}
        seen = set()
        while frontier:
            s = frontier.pop()
            seen.add(s)
            for t in VALID_TRANSITIONS.get(s, set()):
                reachable.add(t)
                if t not in seen and t in HOT_PATH_STATUSES:
                    frontier.add(t)
        for target in MATERIALIZE_TARGETS:
            self.assertIn(target, reachable,
                          f"{target} not reachable from the hot path via legal edges")

    def test_reaper_can_re_drive_hot_path(self):
        self.assertTrue(is_valid("RESEARCH_COMPLETE", "RESEARCH_PENDING"))
        self.assertTrue(is_valid("CONTACT_FOUND", "RESEARCH_PENDING"))


class TestPayloadChain(unittest.TestCase):
    def test_field_groups_are_disjoint(self):
        self.assertFalse(set(events.SEED_FIELDS) & set(events.RESEARCH_FIELDS))
        self.assertFalse(set(events.RESEARCH_FIELDS) & set(events.CONTACT_FIELDS))

    def test_carried_payload_roundtrip(self):
        payload = {"company_name": "Acme", "website": "https://a.example",
                   "company_summary": "s", "industry": "i",
                   "employee_size_estimate": "10-50", "pain_points": ["p"],
                   "recent_news": [], "research_confidence": 0.8}
        msg = events.make_message(events.CMD_FIND_CONTACT, "abc", payload)
        back = events.deserialize(events.serialize(msg))
        for f in events.SEED_FIELDS + events.RESEARCH_FIELDS:
            self.assertIn(f, back["data"], f"carried field lost: {f}")


if __name__ == "__main__":
    unittest.main()
