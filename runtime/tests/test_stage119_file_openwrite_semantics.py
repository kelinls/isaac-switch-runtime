import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage119FileOpenWriteSemantics.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage119FileOpenWriteSemanticsTests(unittest.TestCase):
    def test_exporter_recovers_file_open_abort_boundary_and_original_callers(self):
        self.assertTrue(SCRIPT.is_file(), "Stage119 File::OpenWrite semantics exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "file-openwrite-semantics.json"
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

        self.assertEqual(document["schema_version"], "stage119-file-openwrite-semantics-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["functions"]["open_file_handle"]["entry"], "0x4cd2d4")
        self.assertEqual(document["functions"]["open_write"]["entry"], "0x4cce38")
        self.assertEqual(document["open_file_handle"]["abort_result"], "0x002ee802")
        self.assertIn("CreateCleanPath", document["open_file_handle"]["path_flow"])
        self.assertIn("not statically identified", document["open_file_handle"]["abort_boundary"])
        self.assertEqual(document["original_openwrite_callers"], [])
        self.assertTrue(document["open_handle_thunk_callers"])
        self.assertTrue(any("File::OpenWrite" in value for value in document["open_handle_thunk_callers"]))
        self.assertIn("runtime_binding_authorized", document)


if __name__ == "__main__":
    unittest.main()
