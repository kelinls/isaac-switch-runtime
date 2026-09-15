import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage58BlockedGetterEvidence.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage58BlockedGetterEvidenceTests(unittest.TestCase):
    def test_inventory_requires_complete_candidate_or_precise_blocker(self):
        self.assertTrue(SCRIPT.is_file(), "Stage58 getter evidence exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed analysis project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blocked-getters.json"
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

        self.assertEqual(document["schema_version"], "stage58-blocked-getter-evidence-v1")
        getters = document["getters"]
        self.assertEqual(
            set(getters),
            {"game_get_item_pool", "game_get_room", "room_get_type", "game_get_treasure_room_visit_count"},
        )
        for key, getter in getters.items():
            self.assertIn(getter["status"], {"authorized_candidate", "blocked"}, key)
            self.assertIn("pc_semantics", getter, key)
            self.assertIn("community_semantics_scope", getter, key)
            self.assertIn("receiver_provenance", getter, key)
            self.assertIn("lifecycle_status", getter, key)
            if getter["status"] == "authorized_candidate":
                self.assertTrue(getter["writer"], key)
                self.assertTrue(getter["independent_reader"], key)
                self.assertNotIn("pc_offset", getter, key)
            else:
                self.assertTrue(getter["blocked_reason"], key)


if __name__ == "__main__":
    unittest.main()
