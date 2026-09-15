import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage65TransitionCounterFieldTriad.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage65TransitionCounterFieldTriadTests(unittest.TestCase):
    def test_exporter_reports_transition_writer_triad_or_specific_blocker(self):
        self.assertTrue(SCRIPT.is_file(), "Stage65 transition field triad exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transition-field-triad.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                    "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                    "-postScript", SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage65-transition-counter-field-triad-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["transition_game_receiver"], {
            "game_change_room_entry": "00353fe0",
            "level_change_room_entry": "003d9aac",
            "receiver_relation": "Game entry x0 is the embedded Level receiver",
        })
        self.assertTrue(document["transition_scalar_writers"])
        for field in document["transition_scalar_writers"]:
            self.assertTrue(field["offset"])
            self.assertTrue(field["writer_context"])
            self.assertIn(field["triad_verdict"], {
                "authorized_counter_triad", "blocked_missing_reset", "blocked_missing_independent_reader",
                "blocked_missing_reset_and_independent_reader", "blocked_not_increment_writer",
            })
        self.assertIn(document["status"], {"authorized_candidate", "blocked"})
        if document["status"] == "blocked":
            self.assertEqual(document["blocked_reason"], "blocked_no_transition_counter_field_triad")
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
