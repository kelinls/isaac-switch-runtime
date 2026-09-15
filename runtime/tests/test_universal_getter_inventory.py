import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage57UniversalGetterInventory.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class UniversalGetterInventoryTests(unittest.TestCase):
    def test_exporter_has_a_single_explicit_authorization_decision_for_each_getter(self):
        self.assertTrue(SCRIPT.is_file(), "Stage57 universal getter exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed analysis project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "universal-getters.json"
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

        self.assertEqual(document["schema_version"], "stage57-universal-getter-inventory-v1")
        self.assertEqual(document["getters"]["game_is_greed_mode"]["status"], "authorized")
        self.assertEqual(document["getters"]["game_get_level"]["status"], "authorized")
        self.assertEqual(document["getters"]["level_get_stage"]["status"], "authorized")
        self.assertEqual(document["getters"]["game_get_item_pool"]["status"], "blocked")
        self.assertEqual(document["getters"]["game_get_room"]["status"], "blocked")
        self.assertEqual(document["getters"]["room_get_type"]["status"], "blocked")
        self.assertEqual(document["getters"]["game_get_treasure_room_visit_count"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
