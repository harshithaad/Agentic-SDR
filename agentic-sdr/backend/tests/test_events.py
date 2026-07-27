import unittest
import uuid

from app import events


class TestEventEnvelope(unittest.TestCase):
    def test_roundtrip(self):
        msg = events.make_message(events.CMD_SEND_EMAIL, str(uuid.uuid4()), {"kind": "follow_up"})
        raw = events.serialize(msg)
        back = events.deserialize(raw)
        self.assertEqual(back["type"], events.CMD_SEND_EMAIL)
        self.assertEqual(back["data"]["kind"], "follow_up")
        self.assertEqual(back["event_id"], msg["event_id"])

    def test_malformed_rejected(self):
        with self.assertRaises(ValueError):
            events.deserialize(b'{"type": "X"}')  # missing event_id / lead_id

    def test_every_stage_has_a_topic(self):
        self.assertEqual(
            set(events.CMD_TOPICS.keys()),
            {"research", "contact", "draft", "send", "classify"},
        )

    def test_transition_event_shape(self):
        evt = events.transition_event("abc", "SENT", "REPLY_RECEIVED", by="scheduler")
        self.assertEqual(evt["type"], events.EVT_LEAD_TRANSITIONED)
        self.assertEqual(evt["data"]["from"], "SENT")
        self.assertEqual(evt["data"]["to"], "REPLY_RECEIVED")


if __name__ == "__main__":
    unittest.main()
