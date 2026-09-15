import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage70RoomDescriptorLifecycle.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage70RoomDescriptorLifecycleTests(unittest.TestCase):
    def test_exporter_reports_descriptor_lifecycle_without_authorizing_getter(self):
        self.assertTrue(SCRIPT.is_file(), "Stage70 lifecycle exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "room-descriptor-lifecycle.json"
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

        self.assertEqual(document["schema_version"], "stage70-room-descriptor-lifecycle-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        functions = document["functions"]
        for key, entry in {
            "descriptor_reset": "0x35e440",
            "descriptor_destructor": "0x360348",
            "descriptor_copy_constructor": "0x3e7698",
            "level_try_display_room": "0x3d9004",
            "level_get_current_room_desc": "0x3dc684",
            "gamestate_write_room": "0x368688",
            "gamestate_read_room": "0x36fca4",
        }.items():
            self.assertEqual(functions[key]["entry"].lower(), entry)
            self.assertTrue(functions[key]["instructions"])
        self.assertIn(document["identity_assessment"], {"blocked", "candidate", "authorized"})
        self.assertEqual(document["identity_assessment"], "blocked")
        self.assertTrue(document["descriptor_model"]["copy_creates_distinct_storage"])
        self.assertEqual(document["descriptor_model"]["pointer_identity"], "not_stable_by_static_proof")
        self.assertFalse(document["runtime_binding_authorized"])
        self.assertTrue(document["blocked_reasons"])
        self.assertIn("stable_room_session_key_not_proven", document["blocked_reasons"])
        self.assertIn("read_restore_identity_not_proven", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
