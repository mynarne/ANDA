import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from anda.notion_readback import Stop, await_note
from anda.connectors import DeliveryError

PAGE = "00000000-0000-4000-8000-000000000001"
AUTHOR = "00000000-0000-4000-8000-000000000002"
BLOCK = "00000000-0000-4000-8000-000000000003"
OTHER = "00000000-0000-4000-8000-000000000004"
MARKER = "ANDA-NOTE-synthetic"
PROOF = {"author": AUTHOR, "bot": OTHER, "block": OTHER}

def block():
    return {"id": BLOCK, "type": "paragraph", "parent": {"type": "page_id", "page_id": PAGE},
            "created_by": {"id": AUTHOR}, "last_edited_by": {"id": AUTHOR},
            "paragraph": {"rich_text": [{"type": "text", "plain_text": MARKER}]}}

class NotionResumeTests(unittest.TestCase):
    def probe(self, read, baseline=(), delay=None):
        return await_note(read, MARKER, set(baseline), PAGE, PROOF, sleep=delay or Mock())

    def test_late_sync_and_bounded_absence(self):
        delay = Mock()
        read = Mock(side_effect=[[], [], [block()]])
        self.assertEqual(self.probe(read, delay=delay)["id"], BLOCK)
        self.assertEqual(read.call_count, 3)
        read = Mock(return_value=[])
        with self.assertRaisesRegex(Stop, "^note_not_visible$"):
            self.probe(read)
        self.assertEqual(read.call_count, 5)

    def test_two_matches_and_stale_scope(self):
        with self.assertRaisesRegex(Stop, "^note_ambiguous$"):
            self.probe(lambda: [block(), {**block(), "id": OTHER}])
        with self.assertRaisesRegex(Stop, "^note_scope_mismatch$"):
            self.probe(lambda: [block()], (BLOCK,))
        with self.assertRaisesRegex(Stop, "^note_scope_mismatch$"):
            self.probe(lambda: [{**block(), "parent": {"type": "page_id", "page_id": OTHER}}])

    def test_other_editor_and_transient_errors(self):
        with self.assertRaisesRegex(Stop, "^note_author_mismatch$"):
            self.probe(lambda: [{**block(), "last_edited_by": {"id": OTHER}}])
        delay = Mock()
        flaky = Mock(side_effect=[DeliveryError("pending", "synthetic transient", 3), [block()]])
        self.assertEqual(self.probe(flaky, delay=delay)["id"], BLOCK)
        delay.assert_called_once_with(3)
        blocked = Mock(side_effect=DeliveryError("blocked", "synthetic forbidden"))
        with self.assertRaisesRegex(Stop, "^notion_access_denied$"):
            self.probe(blocked)
        blocked.assert_called_once()
