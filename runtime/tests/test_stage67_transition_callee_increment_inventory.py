import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage67TransitionCalleeIncrementInventory.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage67TransitionCalleeIncrementInventoryTests(unittest.TestCase):
    def test_exporter_reports_all_receiver_relative_increment_candidates(self):
        self.assertTrue(SCRIPT.is_file(), "Stage67 transition-callee increment exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transition-callee-increments.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run([str(GHIDRA), str(ROOT / "analysis/ghidra"), "isaac-switch", "-process",
                "Repentance.nro", "-noanalysis", "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name,
                str(output)], cwd=ROOT, text=True, capture_output=True, env=environment)
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "stage67-transition-callee-increment-inventory-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(len(document["selected_level_callees"]), 6)
        self.assertIn("increment_candidates", document)
        for item in document["increment_candidates"]:
            self.assertTrue(item["function_entry"])
            self.assertTrue(item["instruction_context"])
            self.assertTrue(item["receiver_offset"])
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
