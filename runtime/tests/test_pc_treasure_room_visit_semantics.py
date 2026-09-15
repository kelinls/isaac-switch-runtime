import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "analysis/ghidra/scripts/ExportPcTreasureRoomVisitSemantics.java"
# 走缓存包装器（`tools/ghidra_cached.py`）：同一份「脚本 + 工程 + 参数」第二次直接复用上次的
# 导出结果，不再重跑一次无头分析。35 个模块每次都重跑 = 整轮门禁 164 秒的墙钟下限，
# 而它们做的事完全一样（打开同一个工程、跑一个导出脚本、写一个 JSON）。
GHIDRA = Path(__file__).resolve().parents[2] / "tools" / "ghidra_cached.py"
PROJECT = Path("/tmp/isaac-pc-ghidra.DjKcoN")


class PcTreasureRoomVisitSemanticsTests(unittest.TestCase):
    def test_exporter_recovers_shared_counter_and_increment_writer(self):
        self.assertTrue(SCRIPT.is_file(), "PC treasure-room semantic exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_dir():
            self.skipTest("Ghidra analyzeHeadless or the managed PC project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pc-treasure-room-semantics.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(PROJECT), "isaac-pc", "-process", "isaac-ng.exe", "-noanalysis",
                    "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], "pc-treasure-room-visit-semantics-v1")
        self.assertEqual(document["getter"]["name"], "GetTreasureRoomVisitCount")
        self.assertEqual(document["increment_api"]["name"], "AddTreasureRoomsVisited")
        self.assertEqual(document["getter"]["field_displacement"], document["increment_api"]["field_displacement"])
        self.assertTrue(document["increment_writers"])
        self.assertEqual(document["event_timing_status"], "proven")
        self.assertEqual(document["original_increment_writers"][0]["instruction_address"], "007b398d")
        for access in document["field_accesses"]:
            self.assertTrue(access["function_entry"])
            self.assertTrue(access["instruction_context"])

    def test_exporter_distinguishes_proven_operations_from_unproven_event_timing(self):
        self.assertTrue(SCRIPT.is_file(), "PC treasure-room semantic exporter must exist")
        if not GHIDRA.is_file() or not os.access(GHIDRA, os.X_OK) or not PROJECT.is_dir():
            self.skipTest("Ghidra analyzeHeadless or the managed PC project is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pc-treasure-room-semantics.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            result = subprocess.run(
                [
                    str(GHIDRA), str(PROJECT), "isaac-pc", "-process", "isaac-ng.exe", "-noanalysis",
                    "-scriptPath", str(SCRIPT.parent), "-postScript", SCRIPT.name, str(output),
                ],
                cwd=ROOT, text=True, capture_output=True, env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            document = json.loads(output.read_text(encoding="utf-8"))

        kinds = {access["kind"] for access in document["field_accesses"]}
        self.assertIn("increment", kinds)
        self.assertIn("reset", kinds)
        self.assertIn(document["event_timing_status"], {"proven", "blocked_no_original_event_caller"})
        if document["event_timing_status"] != "proven":
            self.assertEqual(document["event_timing_blocked_reason"], "blocked_no_original_event_caller")

    def test_checked_in_evidence_records_the_original_treasure_room_entry_writer_and_guards(self):
        evidence = ROOT / (
            "analysis/starterr-native-evidence/"
            "pc-treasure-room-visit-semantics-"
            "04469d0c3d3581936fcf85bea5f9f4f3a65b2ccf96b36310456c9626bac36dc6.json"
        )
        document = json.loads(evidence.read_text(encoding="utf-8"))

        writers = document["original_increment_writers"]
        self.assertEqual(len(writers), 1)
        writer = writers[0]
        self.assertEqual(writer["instruction_address"], "007b398d")
        self.assertEqual(writer["room_type"], 4)
        self.assertEqual(writer["required_descriptor_fields_zero"], ["0x40", "0x0c"])
        self.assertEqual(writer["semantic"], "eligible_treasure_room_entry")


if __name__ == "__main__":
    unittest.main()
