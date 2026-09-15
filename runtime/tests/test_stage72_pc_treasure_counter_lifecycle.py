import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage72PcTreasureCounterLifecycle.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = Path("/tmp/isaac-pc-ghidra.DjKcoN")


class Stage72PcTreasureCounterLifecycleTests(unittest.TestCase):
    def test_exporter_recovers_pc_counter_serialization_map_without_switch_transfer(self):
        self.assertTrue(SCRIPT.is_file(), "Stage72 PC lifecycle exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_dir():
            self.skipTest("Ghidra analyzeHeadless or the managed PC project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pc-treasure-counter-lifecycle.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(PROJECT), "isaac-pc", "-process", "isaac-ng.exe", "-noanalysis",
                 "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage72-pc-treasure-counter-lifecycle-v1")
        self.assertEqual(document["source_sha256"], "04469d0c3d3581936fcf85bea5f9f4f3a65b2ccf96b36310456c9626bac36dc6")
        self.assertEqual(document["game_counter_offset"], "0x1c3178")
        self.assertEqual(document["serialized_state_offset"], "0x44")
        self.assertEqual(document["reset"]["entry"], "0x6c8a80")
        self.assertIn("1c3178", document["reset"]["instruction"].lower())
        self.assertEqual(document["restore"]["entry"], "0x6cba60")
        self.assertIn("[EBX + 0x44]", document["restore"]["source_instruction"])
        self.assertIn("1c3178", document["restore"]["destination_instruction"].lower())
        self.assertEqual(document["save"]["entry"], "0x6cc850")
        self.assertIn("1c3178", document["save"]["source_instruction"].lower())
        self.assertIn("[EDI + 0x44]", document["save"]["destination_instruction"])
        self.assertEqual(document["independent_reader"]["entry"], "0x71a850")
        self.assertFalse(document["switch_offset_transfer_authorized"])
        self.assertFalse(document["runtime_binding_authorized"])


if __name__ == "__main__":
    unittest.main()
