import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage93InputAbiForensics.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra"


class Stage93InputAbiForensicsTests(unittest.TestCase):
    def test_exporter_records_input_query_abi_without_authorizing_lua_dispatch(self):
        self.assertTrue(SCRIPT.is_file(), "Stage93 input ABI exporter must exist")
        if not GHIDRA.is_file() or not (PROJECT / "isaac-switch.gpr").is_file():
            self.skipTest("managed Switch Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "input-abi-forensics.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [str(GHIDRA), str(PROJECT), "isaac-switch", "-process", "Repentance.nro", "-noanalysis",
                 "-readOnly", "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output)],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            self.assertTrue(output.is_file(), combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "stage93-input-abi-forensics-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["targets"]["manager_is_action_triggered"]["entry"], "003f9b7c")
        self.assertEqual(document["targets"]["manager_is_action_pressed"]["entry"], "003f9b6c")
        self.assertEqual(document["targets"]["manager_get_action_value"]["entry"], "003f9b8c")
        self.assertEqual(document["targets"]["kage_get_action_value"]["entry"], "004f9bcc")
        self.assertEqual(document["targets"]["kage_get_action_value"]["return_abi"], "float_v0")
        self.assertEqual(len(document["process_input_call_sites"]), 4)
        self.assertTrue(all(site["args"] == {"x0": "global_input_manager", "x3": 0}
                            and site["controller"] == 4294967295
                            and site["return_use"] == "tbz_w0_bit0"
                            and isinstance(site["action"], int)
                            and isinstance(site["context"], list)
                            for site in document["process_input_call_sites"]))
        self.assertEqual({site["action"] for site in document["process_input_call_sites"]}, {8, 10, 11, 13})
        self.assertFalse(document["pc_callback"]["writeback_proven"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(set(document["blocked_reasons"]), {
            "mc_input_action_dispatch_abi_unproven",
            "pc_callback_writeback_unproven",
            "safe_pre_input_hook_phase_unproven",
        })


if __name__ == "__main__":
    unittest.main()
