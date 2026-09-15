import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage93ConsoleOwnerStringForensics.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra"


class Stage93ConsoleOwnerStringForensicsTests(unittest.TestCase):
    def test_exporter_rejects_unproven_console_owner_and_string_lifetimes(self):
        self.assertTrue(SCRIPT.is_file(), "Stage93 Console owner/string exporter must exist")
        if not GHIDRA.is_file() or not (PROJECT / "isaac-switch.gpr").is_file():
            self.skipTest("managed Switch Ghidra project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "console-owner-string-forensics.json"
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

        self.assertEqual(document["schema_version"], "stage93-console-owner-string-forensics-v1")
        self.assertEqual(document["anchors"]["run_command"]["entry"], "0003d740")
        self.assertEqual(document["anchors"]["run_command"]["entry_guard"],
                         "E80F19FCFD7B01A9FD430091FC6F02A9")
        self.assertEqual(document["submit_input_call"]["call_site"], "0003c810")
        self.assertEqual(document["submit_input_call"]["receiver"], "x0=x19")
        self.assertEqual(document["submit_input_call"]["input"], "x1=sp")
        self.assertEqual(document["submit_input_call"]["output"], "x2=0")
        self.assertEqual(document["submit_input_call"]["player"], "x3=0")
        self.assertEqual(document["submit_input_call"]["owner_status"], "unproven")
        self.assertEqual(document["plt_resolutions"], [
            {"thunk": "00679b00", "got_slot": "00aa2ce8", "dynamic_symbol": "GameState::C1",
             "definition_entry": "003650d0", "status": "resolved_not_console"},
            {"thunk": "00679cb0", "got_slot": "00aa2dc0", "dynamic_symbol": "GameState::D1",
             "definition_entry": "00366bf4", "status": "resolved_not_console"},
        ])
        self.assertFalse(document["string_lifetimes"]["input"]["constructed"])
        self.assertFalse(document["string_lifetimes"]["output"]["callee_write_proven"])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertEqual(set(document["blocked_reasons"]), {
            "console_owner_chain_unproven",
            "console_ctor_dtor_callers_unproven",
            "std_string_input_layout_and_lifetime_unproven",
            "std_string_output_abi_and_lifetime_unproven",
            "pc_lua_stack_and_return_marshalling_unproven",
        })


if __name__ == "__main__":
    unittest.main()
