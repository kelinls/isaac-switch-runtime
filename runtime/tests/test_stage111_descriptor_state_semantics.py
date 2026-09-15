import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportStage111DescriptorStateSemantics.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = ROOT / "analysis/ghidra/isaac-switch.gpr"


class Stage111DescriptorStateSemanticsTests(unittest.TestCase):
    def test_exporter_proves_only_serialized_descriptor_slots_not_pc_qualification_equivalence(self):
        self.assertTrue(SCRIPT.is_file(), "Stage111 descriptor-state exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_file():
            self.skipTest("Ghidra analyzeHeadless or its managed Switch project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "descriptor-state-semantics.json"
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

        self.assertEqual(document["schema_version"], "stage111-descriptor-state-semantics-v1")
        self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(document["source_sha256"], "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a")
        self.assertEqual(document["functions"]["level_get_current_room_desc"]["entry"], "0x3dc684")
        self.assertEqual(document["functions"]["level_change_room"]["entry"], "0x3d9aac")
        self.assertEqual(document["functions"]["gamestate_write_room"]["entry"], "0x368688")
        self.assertEqual(document["functions"]["gamestate_read_room"]["entry"], "0x36fca4")
        lifecycle = document["descriptor_save_restore"]
        self.assertTrue(lifecycle["write_source_offsets"])
        self.assertTrue(lifecycle["read_destination_offsets"])
        self.assertTrue(lifecycle["bidirectionally_serialized_offsets"])
        self.assertEqual(document["pc_qualification_equivalence"], "not_authenticated")
        self.assertEqual(document["runtime_binding_authorized"], False)
        self.assertIn("descriptor_qualification_fields_not_authenticated", document["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
