import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage74LevelCandidateTriad.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage74LevelCandidateTriadTests(unittest.TestCase):
    def test_exporter_keeps_level_candidate_blocked_without_all_three_independent_roles(self):
        self.assertTrue(SCRIPT.is_file(), "Stage74 Level-candidate triad exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "level-candidate-triad.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                 "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent),
                 "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage74-level-candidate-triad-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["level_field_offset"], "0xc")
        self.assertEqual(document["serialized_state_offset"], "0x1a8")
        self.assertEqual(document["save_anchor"], "0x3e5818")
        self.assertEqual(document["restore_anchor"], "0x3e4da8")
        self.assertGreater(document["level_function_count"], 0)
        self.assertEqual(document["status"], "rejected_level_curse_flags")
        self.assertEqual([anchor["function"] for anchor in document["curse_semantic_anchors"]],
                         ["IsaacRepentance::Level::GetCurses"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertIn("candidate_is_level_curse_flags", document["blocked_reasons"])
        self.assertIn("treasure_room_event_writer_not_authenticated", document["blocked_reasons"])
        for access in document["independent_readers"]:
            self.assertNotIn("StoreGameState", access["function"])
            self.assertNotIn("RestoreGameState", access["function"])


if __name__ == "__main__":
    unittest.main()
