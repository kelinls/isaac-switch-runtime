import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage69DynamicPreconditions.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage69DynamicPreconditionsTests(unittest.TestCase):
    def test_exporter_proves_bounded_dynamic_read_keys_without_runtime_authorization(self):
        self.assertTrue(SCRIPT.is_file(), "Stage69 precondition exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dynamic-preconditions.json"
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

        self.assertEqual(document["schema_version"], "stage69-dynamic-preconditions-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["game_allocation"]["status"], "observed")
        self.assertTrue(document["game_allocation"]["constructor_entry"])
        self.assertTrue(document["game_allocation"]["constructor_guard"])
        self.assertEqual(document["game_allocation"]["allocation_detail"], "observed_operator_new_size_argument")
        self.assertEqual(document["game_allocation"]["allocation_size"], "0x347d50")
        self.assertEqual(document["game_allocation"]["allocator_symbol"], "_Znwm")
        self.assertEqual(document["game_allocation"]["allocator_got_slot"], "0xa9dd00")
        self.assertTrue(document["room_descriptor"]["room_descriptor_offset"])
        self.assertEqual(document["room_descriptor"]["room_descriptor_offset"], "0x8")
        self.assertTrue(document["room_descriptor"]["room_type_offset"])
        self.assertEqual(document["room_descriptor"]["room_type_offset"], "0x10")
        self.assertEqual(document["dynamic_read_keys"], [
            "game_ptr", "room_descriptor_ptr", "room_type",
        ])
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertTrue(document["blocked_reasons"])
        self.assertNotIn("game_allocation_size_not_proven", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
