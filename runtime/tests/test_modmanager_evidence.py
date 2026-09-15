import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "analysis/modmanager-lifecycle/91C73FDD575061318D68886316AFEAC72388B2AB.json"
TARGET_BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"


class ModManagerEvidenceTests(unittest.TestCase):
    def test_target_lifecycle_evidence_is_complete(self):
        evidence = json.loads(EVIDENCE.read_text())

        self.assertEqual(evidence["build_id"], TARGET_BUILD_ID)
        for key in (
            "managerInit",
            "managerLoadConfigs",
            "modManagerReset",
            "modManagerListMods",
            "modManagerTryRedirectPath",
        ):
            with self.subTest(function=key):
                function = evidence["functions"][key]
                self.assertEqual(function["file_offset"] % 4, 0)
                self.assertEqual(len(bytes.fromhex(function["first_16_bytes"])), 16)

    def test_unproven_lifecycle_entry_blocks_runtime_bridge(self):
        evidence = json.loads(EVIDENCE.read_text())
        status = evidence["analysis_status"]

        self.assertIn(status, {"safe_entry_verified", "blocked_no_safe_entry"})
        if status == "safe_entry_verified":
            entry = evidence["safe_lifecycle_entry"]
            self.assertIsNotNone(entry)
            self.assertTrue(entry["before_first_load_configs"])
            self.assertGreaterEqual(entry["file_offset"], 0)
        else:
            self.assertIsNone(evidence["safe_lifecycle_entry"])
            self.assertIsNone(evidence["manager_to_modmanager_offset"])


if __name__ == "__main__":
    unittest.main()
