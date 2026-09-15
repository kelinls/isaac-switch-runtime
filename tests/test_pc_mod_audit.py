import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.pc_mod_audit import audit_mods


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class PcModAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.mods_root = Path(self.temp_dir.name) / "mods"

    def add_mod(self, directory: str, main_lua: str) -> Path:
        mod = self.mods_root / directory
        write_file(mod / "metadata.xml", f"<metadata><name>{directory}</name><id>1</id></metadata>")
        write_file(mod / "main.lua", main_lua)
        return mod

    def test_extracts_verified_dependencies_without_matching_comments_or_strings(self):
        alpha = self.add_mod(
            "Alpha",
            '''
                -- Game():IsPaused() MusicManager():Pause() require("comment")
                local ignored = "MusicManager():Pause()"
                local mod = RegisterMod("Alpha", 1)
                local game = Game()
                local music = MusicManager()
                require("src.helper")
                mod:AddCallback(ModCallbacks.MC_POST_RENDER, function()
                    if game:IsPaused() then music:Pause() end
                end)
            ''',
        )
        write_file(alpha / "src/helper.lua", "return true\n")

        result = audit_mods(self.mods_root)

        self.assertEqual([mod["directory"] for mod in result], ["Alpha"])
        self.assertEqual(result[0]["callbacks"], ["MC_POST_RENDER"])
        self.assertEqual(result[0]["requires"], ["src.helper"])
        self.assertEqual(result[0]["constructors"], ["Game", "MusicManager"])
        self.assertEqual(result[0]["methods"], ["Game:IsPaused", "Mod:AddCallback", "MusicManager:Pause"])
        self.assertEqual(result[0]["candidate_reasons"], [])
        self.assertTrue(result[0]["candidate"])

    def test_blocks_resource_repentogon_and_unverified_api_dependencies(self):
        beta = self.add_mod(
            "Beta",
            '''
                local mod = RegisterMod("Beta", 1)
                if REPENTOGON then SFXManager():Play(1) end
                mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function() end)
            ''',
        )
        write_file(beta / "resources/gfx/icon.png", "asset")

        result = audit_mods(self.mods_root)

        self.assertFalse(result[0]["candidate"])
        self.assertEqual(
            result[0]["candidate_reasons"],
            ["resource_directories", "repentogon", "unverified_api:SFXManager:Play"],
        )

    def test_treats_required_mod_handle_callback_as_verified_mod_api(self):
        gamma = self.add_mod(
            "Gamma",
            '''
                local mod = require("src.mod")
                mod:AddCallback(ModCallbacks.MC_POST_RENDER, function() end)
            ''',
        )
        write_file(gamma / "src/mod.lua", 'return RegisterMod("Gamma", 1)\n')

        result = audit_mods(self.mods_root)

        self.assertEqual(result[0]["constructors"], [])
        self.assertEqual(result[0]["methods"], ["Mod:AddCallback"])
        self.assertTrue(result[0]["candidate"])

    def test_extracts_isaac_dot_api_as_an_unverified_dependency(self):
        self.add_mod(
            "Delta",
            '''
                local ignored = "Isaac.GetCurseIdByName(\\\"string\\\")"
                local curse = Isaac.GetCurseIdByName("Curse of the Powerless")
            ''',
        )

        result = audit_mods(self.mods_root)

        self.assertEqual(result[0]["methods"], ["Isaac.GetCurseIdByName"])
        self.assertEqual(result[0]["candidate_reasons"], ["unverified_api:Isaac.GetCurseIdByName"])

    def test_extracts_rng_alias_and_each_method_in_a_chained_call(self):
        self.add_mod(
            "Epsilon",
            '''
                local rng = RNG()
                rng:SetSeed(1, 0)
                rng:Next()
                local item = Game():GetItemPool():GetCollectible(1, false, 1)
                local roomType = Game():GetRoom():GetType()
            ''',
        )

        result = audit_mods(self.mods_root)

        self.assertEqual(
            result[0]["methods"],
            [
                "Game:GetItemPool",
                "Game:GetRoom",
                "GetItemPool:GetCollectible",
                "GetRoom:GetType",
                "RNG:Next",
                "RNG:SetSeed",
            ],
        )


class PcModAuditCliTests(unittest.TestCase):
    def test_writes_deterministic_json_and_markdown_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mods_root = root / "mods"
            write_file(mods_root / "Alpha" / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
            write_file(
                mods_root / "Alpha" / "main.lua",
                'local mod = RegisterMod("Alpha", 1)\nmod:AddCallback(ModCallbacks.MC_POST_RENDER, function() end)\n',
            )
            json_path = root / "audit.json"
            markdown_path = root / "audit.md"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.audit_pc_mods",
                    str(mods_root),
                    "--json",
                    str(json_path),
                    "--markdown",
                    str(markdown_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["mods"][0]["candidate"], True)
            self.assertIn("| Alpha | 是 |", markdown_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
