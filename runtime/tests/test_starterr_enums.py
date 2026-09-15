import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "generate_starterr_enums.py"
PC_ROOT = ROOT / "The Binding of Isaac Rebirth pc"
ENUM_SOURCE = PC_ROOT / "resources" / "scripts" / "enums.lua"
STARTERR_SOURCE = PC_ROOT / "mods" / "Starterr_3208060487" / "main.lua"
RUNTIME_SOURCE = ROOT / "runtime" / "source" / "lua_runtime.cpp"


class StarterrEnumTests(unittest.TestCase):
    def test_generator_emits_all_and_only_starterr_enum_dependencies(self):
        if not ENUM_SOURCE.is_file() or not STARTERR_SOURCE.is_file():
            self.skipTest("PC enum and Starterr source inputs are unavailable in the tracked-file isolation fixture")
        with tempfile.TemporaryDirectory(prefix="isaac-starterr-enums-") as directory:
            output = Path(directory) / "starterr_enum_data.hpp"
            result = subprocess.run(
                [
                    "python3", str(GENERATOR), "--enum-source", str(ENUM_SOURCE),
                    "--mod-source", str(STARTERR_SOURCE), "--output", str(output),
                ],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            generated = output.read_text(encoding="utf-8")

        self.assertIn(
            hashlib.sha256(ENUM_SOURCE.read_bytes()).hexdigest(), generated,
        )
        self.assertIn('"CollectibleType", "COLLECTIBLE_SATANIC_BIBLE", 292', generated)
        self.assertIn('"ItemPoolType", "POOL_TREASURE", 0', generated)
        self.assertIn('"ItemPoolType", "POOL_GREED_TREASURE", 16', generated)
        self.assertIn('"RoomType", "ROOM_TREASURE", 4', generated)
        self.assertIn('"LevelStage", "STAGE1_GREED", 1', generated)
        self.assertEqual(generated.count('{"CollectibleType", '), 157)
        self.assertEqual(generated.count('{"ItemPoolType", '), 2)
        self.assertEqual(generated.count('{"RoomType", '), 1)
        self.assertEqual(generated.count('{"LevelStage", '), 1)

    def test_generated_header_is_not_linked_before_starterr_is_authorized(self):
        runtime = RUNTIME_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn('#include "program/starterr_enum_data.hpp"', runtime)
        self.assertNotIn("RegisterStarterrEnumTables", runtime)
        self.assertNotIn("EXL_ENABLE_STARTERR_RNG", runtime)
        default_mod_root = ROOT / "runtime" / "pc-mods"
        self.assertTrue((default_mod_root / "MuteOnPause").is_dir())
        self.assertFalse((default_mod_root / "Starterr_3208060487").exists())


if __name__ == "__main__":
    unittest.main()
