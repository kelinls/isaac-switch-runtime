import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NRO_PATH = ROOT / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]" / "Program #0" / "1" / ".nro" / "Repentance.nro"


class Stage16Tests(unittest.TestCase):
    def test_exporter_is_read_only(self):
        script = ROOT / "analysis" / "ghidra" / "scripts" / "ExportStage16GamePltDataflow.java"
        self.assertTrue(script.exists(), "Stage 16 PLT exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in (
            "getExecutableSHA256()",
            "getReferencesTo",
            "getFunctionContaining",
            "indirect_calls",
            "manager_x0_members",
            "Files.newBufferedWriter",
        ):
            self.assertIn(required, source)
        for forbidden in ("createFunction(", "setBytes(", "createLabel(", "delete(", "runtime/", ".ips"):
            self.assertNotIn(forbidden, source)

        ghidra = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
        if not ghidra.exists():
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plt-dataflow.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            command = [
                str(ghidra), str(ROOT / "analysis" / "ghidra"), "isaac-switch",
                "-process", "Repentance.nro", "-noanalysis",
                "-scriptPath", str(script.parent), "-postScript", script.name,
                str(ROOT / "analysis" / "stage16-game-plt-dataflow" / "plt-targets-91C73FDD575061318D68886316AFEAC72388B2AB.json"),
                str(output),
            ]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env=environment)
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, combined)
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            document = json.loads(output.read_text(encoding="utf-8"))
            calls = {
                (item["target"], item["caller"], item["call_address"])
                for item in document["indirect_calls"]
            }
            self.assertIn(("game_ctor", "Init@003f5898", "003f5a30"), calls)
            self.assertIn(("game_init", "Init@003f5898", "003f5a44"), calls)

    def test_plt_targets_are_version_locked_and_include_game_update(self):
        from tools.stage16_game_plt_dataflow import write_plt_targets

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plt.json"
            document = write_plt_targets(NRO_PATH, output)
            self.assertEqual(document["targets"]["game_update"]["got_slot"], 0xAA4458)
            self.assertEqual(document["targets"]["game_is_paused"]["got_slot"], 0xA9E7E8)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), document)
