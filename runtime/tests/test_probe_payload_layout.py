import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "runtime" / "source" / "probe" / "content_mount_point_probe.cpp"


class ProbePayloadLayoutTests(unittest.TestCase):
    def test_replace_spritesheet_fields_do_not_overlap_status_bits(self):
        source = PROBE.read_text(encoding="utf-8")
        payload_zero = source[source.index("payload[0] = inputs->status"):
                             source.index("payload[1] = inputs->errorLength")]

        self.assertIn("inputs->status & 0xFFFULL", payload_zero)
        self.assertIn("replaceNameLength", payload_zero)
        self.assertIn("replaceNameHead", payload_zero)


if __name__ == "__main__":
    unittest.main()
