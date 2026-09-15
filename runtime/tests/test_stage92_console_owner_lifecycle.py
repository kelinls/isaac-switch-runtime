import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage92ConsoleOwnerLifecycle.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra"


class Stage92ConsoleOwnerLifecycleTests(unittest.TestCase):
    def test_a_missing_manager_console_lifecycle_keeps_the_command_api_blocked(self):
        self.assertTrue(SCRIPT.is_file(), "Stage92 Console owner lifecycle exporter must exist")
        if not GHIDRA.is_file() or not (PROJECT / "isaac-switch.gpr").is_file():
            self.skipTest("managed Switch Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "console-owner-lifecycle.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(PROJECT), "isaac-switch", "-process", "Repentance.nro", "-noanalysis",
                 "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage92-console-owner-lifecycle-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["manager_constructor"]["entry"], "003f3b00")
        self.assertEqual(document["manager_destructor"]["entry"], "003f3f48")
        self.assertEqual(document["console_constructor"]["entry"], "0003bc34")
        self.assertEqual(document["console_destructor"]["entry"], "0003bc64")
        self.assertTrue(document["manager_constructor_context"])
        self.assertTrue(document["manager_destructor_context"])
        self.assertEqual(document["suspected_constructor_thunk"]["entry"], "00679b00")
        self.assertEqual(document["suspected_destructor_thunk"]["entry"], "00679cb0")
        self.assertEqual(document["construction_sites"], [])
        self.assertEqual(document["destruction_sites"], [])
        self.assertEqual(document["manager_console_offset"], "")
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(set(document["blocked_reasons"]), {
            "manager_is_not_a_proven_console_owner",
            "console_owner_chain_not_yet_proven",
            "switch_string_output_lifetime_not_proven",
            "pc_lua_argument_and_result_marshalling_not_proven",
        })


if __name__ == "__main__":
    unittest.main()
