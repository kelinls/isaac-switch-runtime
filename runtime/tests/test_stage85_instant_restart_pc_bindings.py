import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage85InstantRestartPcBindings.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT_DIRECTORY_FILE = Path("/tmp/isaac-stage84-pc-dir.txt")


class Stage85InstantRestartPcBindingsTests(unittest.TestCase):
    def test_exporter_recovers_target_lua_registration_handlers(self):
        self.assertTrue(SCRIPT.is_file(), "Stage85 PC binding exporter must exist")
        if not GHIDRA.is_file() or not PROJECT_DIRECTORY_FILE.is_file():
            self.skipTest("temporary analyzed PC Ghidra project is unavailable")
        project = Path(PROJECT_DIRECTORY_FILE.read_text(encoding="utf-8").strip())
        if not project.is_dir():
            self.skipTest("temporary analyzed PC Ghidra project was removed")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "instant-restart-pc-bindings.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(project), "isaac-pc", "-process", "isaac-ng.exe", "-noanalysis",
                 "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage85-instant-restart-pc-bindings-v1")
        self.assertEqual(document["source_sha256"], "04469d0c3d3581936fcf85bea5f9f4f3a65b2ccf96b36310456c9626bac36dc6")
        self.assertEqual(document["callbacks"]["mc_input_action"], 13)
        for name in ("IsActionTriggered", "ExecuteCommand", "IsAscent"):
            self.assertGreaterEqual(len(document["apis"][name]["registrations"]), 1, name)


if __name__ == "__main__":
    unittest.main()
