import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/stage105_lifecycle_call_boundary_audit.py"
NRO = next(ROOT.glob("The Binding of Isaac*/**/Repentance.nro"), None)


class Stage105LifecycleCallBoundaryAuditTests(unittest.TestCase):
    def test_raw_nro_audit_excludes_manager_lifecycle_entries_as_game_start_relays(self):
        self.assertTrue(SCRIPT.is_file(), "Stage105 raw-NRO lifecycle auditor must exist")
        self.assertIsNotNone(NRO, "user-provided fixed Switch NRO is required")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lifecycle-call-boundary.json"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(NRO), str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage105-lifecycle-call-boundary-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(set(document["game_lifecycle_entries"]), {"new_game", "saved_game"})
        self.assertEqual(set(document["manager_lifecycle_entries"]), {"new_game", "saved_game", "restart"})
        self.assertEqual(document["manager_to_game_lifecycle_calls"], [])
        self.assertEqual(document["runtime_hook_authorized"], False)
        self.assertEqual(document["next_step"], "audit_game_entry_receivers_and_safe_post_event_boundaries")


if __name__ == "__main__":
    unittest.main()
