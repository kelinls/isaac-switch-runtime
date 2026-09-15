import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NRO_PATH = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0"
    / "1"
    / ".nro"
    / "Repentance.nro"
)


class Stage15GameOwnerChainTests(unittest.TestCase):
    def test_owner_chain_ghidra_exporter_is_read_only(self):
        script = ROOT / "analysis" / "ghidra" / "scripts" / "ExportStage15GameOwnerChain.java"
        self.assertTrue(script.exists(), "Stage 15 owner-chain exporter must exist")
        source = script.read_text(encoding="utf-8")
        for required in (
            "getExecutableSHA256()",
            "getReferencesTo",
            "getReferencesFrom",
            "getFunctionContaining",
            "getMemory().getBlock",
            "owner_writes",
            "update_reads",
            "lifetime_observations",
            "Files.newBufferedWriter",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "createFunction(",
            "setBytes(",
            "createLabel(",
            "delete(",
            "runtime/",
            ".ips",
        ):
            self.assertNotIn(forbidden, source)

        ghidra = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")
        if not ghidra.exists():
            self.skipTest("Ghidra analyzeHeadless is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "owner-chain.json"
            environment = dict(os.environ)
            environment["GHIDRA_USER_HOME"] = str(Path(directory) / "ghidra-user-home")
            command = [
                str(ghidra),
                str(ROOT / "analysis" / "ghidra"),
                "isaac-switch",
                "-process",
                "Repentance.nro",
                "-noanalysis",
                "-scriptPath",
                str(script.parent),
                "-postScript",
                script.name,
                str(
                    ROOT
                    / "analysis"
                    / "stage15-game-owner-chain"
                    / "anchors-91C73FDD575061318D68886316AFEAC72388B2AB.json"
                ),
                str(output),
            ]
            result = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=environment,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("REPORT SCRIPT ERROR:", combined)
            self.assertEqual(result.returncode, 0, combined)
            self.assertTrue(output.exists())
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], "stage15-game-owner-chain-v1")
            self.assertEqual(document["build_id"], "91C73FDD575061318D68886316AFEAC72388B2AB")
            self.assertEqual(
                set(document),
                {
                    "schema_version",
                    "build_id",
                    "source_sha256",
                    "program",
                    "anchors",
                    "constructor_callers",
                    "destructor_callers",
                    "caller_parents",
                    "owner_writes",
                    "update_reads",
                    "lifetime_observations",
                },
            )

    def test_formal_owner_chain_lock_includes_manager_update(self):
        from tools.stage15_game_owner_chain import (
            OWNER_CHAIN_ANCHORS,
            verify_owner_chain_nro,
        )

        anchors = verify_owner_chain_nro(NRO_PATH)

        self.assertEqual(list(anchors), list(OWNER_CHAIN_ANCHORS))
        self.assertEqual(anchors["manager_update"]["file_offset"], 0x3F8DB8)
        self.assertEqual(
            anchors["manager_update"]["first_16_bytes"],
            "FF4301D1FD7B01A9FD430091F71300F9",
        )

    def test_owner_chain_formal_entry_rejects_unlocked_source(self):
        from tools.stage15_game_owner_chain import verify_owner_chain_nro

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Repentance.nro"
            path.write_bytes(b"not a supported game image")
            with self.assertRaisesRegex(ValueError, "source_sha256"):
                verify_owner_chain_nro(path)

    def test_merge_requires_one_consistent_owner_chain(self):
        from tools.merge_stage15_game_owner_chain import merge_owner_chain
        from tools.stage14_game_provenance import TARGET_BUILD_ID, TARGET_SOURCE_SHA256
        from tools.stage15_game_owner_chain import OWNER_CHAIN_ANCHORS

        anchors = {
            key: {
                "symbol": symbol,
                "entry_address": f"{offset:08x}",
                "first_16_bytes": guard,
            }
            for key, (symbol, offset, guard) in OWNER_CHAIN_ANCHORS.items()
        }

        def fixture(read_object="00900000", has_lifetime_pair=True):
            lifetime = []
            if has_lifetime_pair:
                lifetime = [
                    {
                        "event": "game_ctor",
                        "owner_slot": "00800000",
                        "object_address": "00900000",
                        "published": True,
                    },
                    {
                        "event": "manager_update_read",
                        "owner_slot": "00800000",
                        "object_address": "00900000",
                        "update_compatible": True,
                    },
                ]
            return {
                "schema_version": "stage15-game-owner-chain-v1",
                "build_id": TARGET_BUILD_ID,
                "source_sha256": TARGET_SOURCE_SHA256,
                "program": "Repentance.nro",
                "anchors": anchors,
                "constructor_callers": [],
                "destructor_callers": [],
                "caller_parents": [],
                "owner_writes": [
                    {
                        "owner_slot": "00800000",
                        "object_address": "00900000",
                        "published": True,
                        "memory_block_read": True,
                        "memory_block_execute": False,
                    }
                ],
                "update_reads": [
                    {
                        "owner_slot": "00800000",
                        "object_address": read_object,
                        "instruction_address": "003f9000",
                        "instruction": "003f9000: ldr x0,[x8, #0x20]",
                        "explicit_x0": True,
                        "x0_contiguous": True,
                        "memory_block_read": True,
                        "memory_block_execute": False,
                    }
                ],
                "lifetime_observations": lifetime,
            }

        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "raw.json"
            output_path = Path(directory) / "merged.json"
            raw_path.write_text(json.dumps(fixture()), encoding="utf-8")
            merged = merge_owner_chain(raw_path, output_path)
            self.assertEqual(merged["game_owner_chain"]["status"], "owner_chain_proven")
            self.assertFalse(merged["ready_for_runtime_binding"])
            self.assertIn("render_relay_unproven", merged["blocked_reasons"])

            raw_path.write_text(json.dumps(fixture(read_object="00900004")), encoding="utf-8")
            merged = merge_owner_chain(raw_path, output_path)
            self.assertEqual(merged["game_owner_chain"]["status"], "unproven")
            self.assertIn(
                "owner_update_object_mismatch",
                merged["game_owner_chain"]["blocking_links"],
            )

            raw_path.write_text(json.dumps(fixture(has_lifetime_pair=False)), encoding="utf-8")
            merged = merge_owner_chain(raw_path, output_path)
            self.assertEqual(merged["game_owner_chain"]["status"], "unproven")
            self.assertIn(
                "lifetime_pair_missing",
                merged["game_owner_chain"]["blocking_links"],
            )
