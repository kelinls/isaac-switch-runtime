import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import pc_mod_manifest
from tools.pc_mod_manifest import discover_mods, inspect_mod, write_romfs_manifest


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class PcModManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "Example_42"
        self.root.mkdir()

    def test_inspect_mod_keeps_pc_layout_and_detects_standard_markers(self):
        write_file(self.root / "metadata.xml", "<metadata><name>Example</name><id>42</id></metadata>")
        write_file(self.root / "main.lua", "return RegisterMod('Example', 1)")
        write_file(self.root / "resources/gfx/example.png", "asset")
        write_file(self.root / "content/items.xml", "<items/>")
        write_file(self.root / "disable.it", "")

        manifest = inspect_mod(self.root)

        self.assertEqual(manifest["source_directory"], "Example_42")
        self.assertEqual(manifest["metadata"]["name"], "Example")
        self.assertEqual(manifest["metadata"]["id"], "42")
        self.assertEqual(manifest["entry_script"], "main.lua")
        self.assertEqual(manifest["markers"], {"disable.it": True, "update.it": False})
        self.assertEqual(manifest["resource_directories"], ["resources", "content"])
        self.assertIn("resources/gfx/example.png", manifest["files"])
        self.assertNotIn(str(self.root), manifest["files"])
        self.assertNotIn("metadata_xml", manifest)

    def test_inspect_mod_preserves_repeated_metadata_elements_and_attributes(self):
        write_file(
            self.root / "metadata.xml",
            '<metadata><name>Example</name><id>42</id><tag id="Lua"/><tag id="Items"/></metadata>',
        )

        manifest = inspect_mod(self.root)

        self.assertEqual(manifest["metadata"]["tag"], [
            {"attributes": {"id": "Lua"}, "text": ""},
            {"attributes": {"id": "Items"}, "text": ""},
        ])

    def test_discover_mods_preserves_crlf_xml_and_accepts_unknown_nested_metadata(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        alpha = mods_root / "alpha"
        metadata_bytes = (
            b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\r\n"
            b"<metadata><name>Alpha</name><id>1</id>"
            b"<unknown><nested value=\"kept\"/></unknown>"
            b"<tag id=\"Lua\"/></metadata>\r\n"
        )
        write_bytes(alpha / "metadata.xml", metadata_bytes)
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")

        mod = discover_mods(mods_root)[0]

        self.assertEqual(mod["metadata_xml"].encode("utf-8"), metadata_bytes)
        self.assertNotIn("unknown", mod["metadata"])
        self.assertEqual(mod["metadata"]["tag"], {"attributes": {"id": "Lua"}, "text": ""})
        self.assertIsNone(mod["version"])

        manifest_path = write_romfs_manifest(mods_root, Path(self.temp_dir.name) / "output")
        manifest_mod = json.loads(manifest_path.read_text(encoding="utf-8"))["mods"][0]
        self.assertEqual(manifest_mod["metadata_xml"].encode("utf-8"), metadata_bytes)

    def test_discover_mods_requires_strict_utf8_and_reports_direct_mod_directory(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        alpha = mods_root / "alpha"
        write_bytes(alpha / "metadata.xml", b"<metadata><name>\xff</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")

        with self.assertRaises(ValueError) as raised:
            discover_mods(mods_root)

        self.assertIn(str(alpha.resolve()), str(raised.exception))
        self.assertIn("UTF-8", str(raised.exception))

    def test_inspect_mod_rejects_missing_or_malformed_metadata(self):
        with self.assertRaisesRegex(ValueError, "metadata.xml"):
            inspect_mod(self.root)

        # `<name>` 是唯一必需元素。<id>（Steam Workshop id）与 <directory> 可以缺：
        # 真实 PC Mod 里就有不写 <id> 的（qualityonsprites），而运行时根本不读 metadata.xml。
        write_file(self.root / "metadata.xml", "<metadata><directory>x</directory></metadata>")
        with self.assertRaisesRegex(ValueError, "non-empty <name>"):
            inspect_mod(self.root)

    def test_inspect_mod_accepts_metadata_without_id(self):
        write_file(
            self.root / "metadata.xml",
            "<metadata><name>QualityOnSprite</name><directory>qualityonsprite</directory>"
            "<version>1.0</version></metadata>",
        )
        write_bytes(self.root / "resources/gfx/a.png", b"\x89PNG\r\n\x1a\n")

        manifest = inspect_mod(self.root)

        self.assertEqual(manifest["metadata"]["name"], "QualityOnSprite")
        self.assertNotIn("id", manifest["metadata"])
        # metadata 里的 <directory> 与真实目录名不一致时以真实目录名为准（清单/落盘/加载
        # 三者都用它），metadata 原样保留给索引读者。
        self.assertEqual(manifest["source_directory"], "Example_42")
        self.assertEqual(manifest["metadata"]["directory"], "qualityonsprite")

    def test_inspect_mod_rejects_symlink_escaping_mod_root(self):
        write_file(self.root / "metadata.xml", "<metadata><name>Example</name><id>42</id></metadata>")
        outside = Path(self.temp_dir.name) / "outside.lua"
        outside.write_text("outside", encoding="utf-8")
        link = self.root / "resources" / "outside.lua"
        link.parent.mkdir()
        try:
            os.symlink(outside, link)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "symlink"):
            inspect_mod(self.root)

    def test_discover_mods_orders_and_hashes_mods(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        alpha = mods_root / "alpha"
        zeta = mods_root / "zeta"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        write_file(alpha / "disable.it", "")
        write_file(zeta / "metadata.xml", "<metadata><name>Zeta</name><id>2</id><version>2.0</version></metadata>")
        write_file(zeta / "main.lua", "return RegisterMod('Zeta', 2)")
        write_file(zeta / "resources/gfx/example.png", "asset")
        write_file(zeta / "src/loader.lua", "return true")

        mods = discover_mods(mods_root)

        self.assertEqual([mod["directory"] for mod in mods], ["alpha", "zeta"])
        self.assertFalse(mods[0]["enabled"])
        self.assertEqual(mods[0]["entry"], "main.lua")
        self.assertIsNone(mods[0]["version"])
        self.assertEqual(mods[1]["version"], "2.0")
        self.assertEqual(
            [file["path"] for file in mods[1]["files"]],
            ["main.lua", "metadata.xml", "resources/gfx/example.png", "src/loader.lua"],
        )
        self.assertEqual(mods[1]["files"][0]["sha256"], "c051edcaf7bc3b4f8ee6e9ed75fb01a249d3fb72e5418ddead35d31e6cd62407")

    def test_discover_mods_rejects_top_level_symlinked_directory(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        outside = Path(self.temp_dir.name) / "outside"
        write_file(outside / "metadata.xml", "<metadata><name>Outside</name><id>4</id></metadata>")
        write_file(outside / "main.lua", "return RegisterMod('Outside', 4)")
        link = mods_root / "linked"
        mods_root.mkdir()
        try:
            os.symlink(outside, link)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "linked"):
            discover_mods(mods_root)

    def test_discover_mods_requires_a_script_or_something_to_mount(self):
        # 没有 `main.lua` 现在是**合法形态**（资源型 Mod，"资源型（无脚本）"写在索引里）。
        # 只有"既没有脚本、也没有 resources/ 或 content/"才是坏 Mod：没有任何东西可挂。
        mods_root = Path(self.temp_dir.name) / "mods"
        write_file(mods_root / "empty-mod" / "metadata.xml", "<metadata><name>Broken</name><id>3</id></metadata>")

        with self.assertRaises(ValueError) as raised:
            discover_mods(mods_root)
        self.assertIn("must ship resources/", str(raised.exception))
        self.assertIn(str((mods_root / "empty-mod").resolve()), str(raised.exception))
        with self.assertRaisesRegex(ValueError, "mods root"):
            discover_mods(Path(self.temp_dir.name) / "missing-mods")

    def test_resource_only_mod_has_no_entry_and_is_marked_content_only(self):
        mods_root = Path(self.temp_dir.name) / "resource-mods"
        textures = mods_root / "qualityonsprites"
        write_file(textures / "metadata.xml", "<metadata><name>QualityOnSprite</name><id>7</id></metadata>")
        write_bytes(textures / "resources/gfx/items/collectibles/a.png", b"\x89PNG\r\n\x1a\n")

        mods = discover_mods(mods_root)

        self.assertEqual(len(mods), 1)
        self.assertIsNone(mods[0]["entry"])
        self.assertTrue(mods[0]["content_only"])
        self.assertEqual(mods[0]["directory"], "qualityonsprites")
        self.assertTrue(mods[0]["enabled"])

    def test_write_romfs_manifest_omits_entry_for_resource_only_mod(self):
        mods_root = Path(self.temp_dir.name) / "resource-mods"
        output_root = Path(self.temp_dir.name) / "output"
        textures = mods_root / "qualityonsprites"
        write_file(textures / "metadata.xml", "<metadata><name>QualityOnSprite</name><id>7</id></metadata>")
        write_bytes(textures / "resources/gfx/items/collectibles/a.png", b"\x89PNG\r\n\x1a\n")

        manifest_path = write_romfs_manifest(mods_root, output_root)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        packed = payload["mods"][0]

        # 清单里**不写** `entry`（而不是写 null）：设备端解析器只在值是字符串时接受这个键。
        self.assertNotIn("entry", packed)
        self.assertTrue(packed["content_only"])
        self.assertTrue(packed["enabled"])
        self.assertEqual(packed["directory"], "qualityonsprites")
        self.assertEqual(
            [record["path"] for record in packed["files"]],
            ["mods/qualityonsprites/metadata.xml", "mods/qualityonsprites/resources/gfx/items/collectibles/a.png"],
        )
        self.assertEqual(
            (manifest_path.parent / "mods/qualityonsprites/resources/gfx/items/collectibles/a.png").read_bytes(),
            b"\x89PNG\r\n\x1a\n",
        )

    def test_scripted_mod_still_reports_entry_and_content_only_false(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        output_root = Path(self.temp_dir.name) / "output"

        manifest_path = write_romfs_manifest(mods_root, output_root)
        packed = json.loads(manifest_path.read_text(encoding="utf-8"))["mods"][0]

        self.assertEqual(packed["entry"], "mods/alpha/main.lua")
        self.assertFalse(packed["content_only"])

    def test_cli_syncs_mod_root_to_romfs(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        zeta = mods_root / "zeta"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        write_file(zeta / "metadata.xml", "<metadata><name>Zeta</name><id>2</id></metadata>")
        write_file(zeta / "main.lua", "return RegisterMod('Zeta', 2)")

        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.inspect_pc_mod",
                "--mods-root",
                str(mods_root),
                "--romfs-output",
                str(output_root),
            ],
            check=True,
        )

        manifest_path = output_root / "atmosphere/contents/010021C000B6A000/romfs/isaac_mods/manifest.json"
        self.assertTrue(manifest_path.is_file())
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual([mod["directory"] for mod in payload["mods"]], ["alpha", "zeta"])

    def test_stage13_probe_fixture_generates_manifest(self):
        project_root = Path(__file__).resolve().parents[1]
        mods_root = project_root / "runtime/diagnostic/stage13/pc-mods"

        with tempfile.TemporaryDirectory(prefix="isaac-stage13-probe-") as temporary:
            output_root = Path(temporary)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.inspect_pc_mod",
                    "--mods-root",
                    str(mods_root),
                    "--romfs-output",
                    str(output_root),
                ],
                cwd=project_root,
                check=True,
            )

            romfs = output_root / "atmosphere/contents/010021C000B6A000/romfs/isaac_mods"
            payload = json.loads((romfs / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(len(payload["mods"]), 1)
            probe = payload["mods"][0]
            self.assertEqual(probe["directory"], "00 Runtime Require Probe")
            self.assertTrue(probe["enabled"])
            self.assertEqual(probe["entry"], "mods/00 Runtime Require Probe/main.lua")
            for path in (
                probe["entry"],
                "mods/00 Runtime Require Probe/src/mod.lua",
                "mods/00 Runtime Require Probe/src/metadata.lua",
            ):
                self.assertTrue((romfs / path).is_file())

    def test_stage13_probe_uses_nested_require_table_before_returning_mod(self):
        project_root = Path(__file__).resolve().parents[1]
        source = (
            project_root
            / "runtime/diagnostic/stage13/pc-mods/00 Runtime Require Probe/src/mod.lua"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            source,
            'local metadata = require("src.metadata")\n'
            'return RegisterMod(metadata.name, 1)\n',
        )

        metadata = (
            project_root
            / "runtime/diagnostic/stage13/pc-mods/00 Runtime Require Probe/src/metadata.lua"
        ).read_text(encoding="utf-8")
        self.assertEqual(metadata, 'return { name = "Runtime Require Probe" }\n')

        entry = (
            project_root
            / "runtime/diagnostic/stage13/pc-mods/00 Runtime Require Probe/main.lua"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            entry,
            'local mod = require("src.mod")\n'
            'assert(require("src.mod") == mod)\n'
            'mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function(self)\n'
            '    assert(self == mod)\n'
            '    RuntimeTest.MarkPostUpdate()\n'
            'end)\n',
        )
        self.assertNotIn('RegisterMod("Runtime Require Probe", 1)', entry)
        self.assertIn("mod:AddCallback", entry)
        self.assertNotIn("RuntimeRequireProbeMod", entry)

    def test_cli_rejects_mixed_single_mod_and_mods_root_modes_without_output(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        single_mod = mods_root / "single"
        write_file(single_mod / "metadata.xml", "<metadata><name>Single</name><id>1</id></metadata>")
        write_file(single_mod / "main.lua", "return RegisterMod('Single', 1)")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.inspect_pc_mod",
                str(single_mod),
                "--mods-root",
                str(mods_root),
                "--romfs-output",
                str(Path(self.temp_dir.name) / "output"),
            ],
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--output", result.stderr)

    def test_cli_keeps_single_mod_output_mode(self):
        output_path = Path(self.temp_dir.name) / "single-mod.json"
        write_file(self.root / "metadata.xml", "<metadata><name>Example</name><id>42</id></metadata>")
        write_file(self.root / "main.lua", "return RegisterMod('Example', 1)")

        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.inspect_pc_mod",
                str(self.root),
                "--output",
                str(output_path),
            ],
            check=True,
        )

        self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["source_directory"], "Example_42")

    def test_cli_reports_expected_value_and_os_errors_without_traceback(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        broken_mod = mods_root / "broken"
        write_file(broken_mod / "metadata.xml", "<metadata><name>Broken</name>")
        write_file(broken_mod / "main.lua", "return RegisterMod('Broken', 1)")
        blocked_parent = Path(self.temp_dir.name) / "blocked"
        write_file(blocked_parent, "not a directory")

        commands = [
            [
                sys.executable,
                "-m",
                "tools.inspect_pc_mod",
                "--mods-root",
                str(mods_root),
                "--romfs-output",
                str(Path(self.temp_dir.name) / "output"),
            ],
            [
                sys.executable,
                "-m",
                "tools.inspect_pc_mod",
                str(self.root),
                "--output",
                str(blocked_parent / "manifest.json"),
            ],
        ]
        write_file(self.root / "metadata.xml", "<metadata><name>Example</name><id>42</id></metadata>")

        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_real_sample_manifest_records_eid_and_mute_on_pause_when_pc_root_exists(self):
        pc_mods_root = Path("The Binding of Isaac Rebirth pc/mods")
        manifest_path = Path("analysis/pc-mod-contract/mods-manifest.json")
        if not pc_mods_root.is_dir():
            self.skipTest("real PC mods root is unavailable")
        self.assertTrue(manifest_path.is_file())

        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        mods = {mod["directory"]: mod for mod in payload["mods"]}
        eid = mods["external item descriptions_836319872"]
        mute_on_pause = mods["MuteOnPause"]

        self.assertEqual(eid["metadata"]["name"], "!!~External item descriptions")
        self.assertEqual(eid["metadata"]["id"], "836319872")
        self.assertEqual(eid["metadata"]["version"], "5.21")
        self.assertEqual(eid["version"], "5.21")
        self.assertEqual(eid["entry"], "mods/external item descriptions_836319872/main.lua")
        self.assertFalse(eid["enabled"])
        self.assertTrue(eid["markers"]["disable.it"])
        self.assertEqual(mute_on_pause["metadata"]["name"], "Mute on Pause")
        self.assertEqual(mute_on_pause["metadata"]["id"], "3452119167")
        self.assertEqual(mute_on_pause["metadata"]["version"], "1.0")
        self.assertEqual(mute_on_pause["version"], "1.0")
        self.assertEqual(mute_on_pause["entry"], "mods/MuteOnPause/main.lua")
        self.assertTrue(mute_on_pause["enabled"])
        self.assertFalse(mute_on_pause["markers"]["disable.it"])
        self.assertIn(
            "mods/MuteOnPause/src/mod.lua",
            [file["path"] for file in mute_on_pause["files"]],
        )

    def test_write_romfs_manifest_copies_files_without_touching_other_overlays(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        zeta = mods_root / "zeta"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        write_file(alpha / "disable.it", "")
        write_file(zeta / "metadata.xml", "<metadata><name>Zeta</name><id>2</id></metadata>")
        write_file(zeta / "main.lua", "return RegisterMod('Zeta', 2)")
        write_file(zeta / "resources/gfx/example.png", "asset")
        title_root = output_root / "atmosphere/contents/010021C000B6A000"
        write_file(title_root / "romfs/runtime_probe.lua", "return true")
        write_file(title_root / "exefs/subsdk9", "runtime overlay")

        manifest_path = write_romfs_manifest(mods_root, output_root)
        romfs_root = manifest_path.parent
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["mods"][0]["entry"], "mods/alpha/main.lua")
        self.assertFalse(payload["mods"][0]["enabled"])
        self.assertEqual(
            (romfs_root / "mods/alpha/main.lua").read_bytes(),
            (mods_root / "alpha/main.lua").read_bytes(),
        )
        self.assertEqual((title_root / "romfs/runtime_probe.lua").read_text(encoding="utf-8"), "return true")
        self.assertEqual((title_root / "exefs/subsdk9").read_text(encoding="utf-8"), "runtime overlay")
        self.assertEqual(manifest_path.read_bytes(), write_romfs_manifest(mods_root, output_root).read_bytes())

    def test_write_romfs_manifest_rejects_fixed_target_inside_mods_root_before_creating_output(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")

        with self.assertRaisesRegex(ValueError, "overlap"):
            write_romfs_manifest(mods_root, mods_root)

        self.assertFalse((mods_root / "atmosphere").exists())

    def test_write_romfs_manifest_rejects_mods_root_at_or_inside_fixed_target(self):
        for relationship in ("same", "descendant"):
            with self.subTest(relationship=relationship):
                base = Path(self.temp_dir.name) / relationship
                output_root = base / "output"
                fixed_target = output_root / pc_mod_manifest.ROMFS_MANIFEST_RELATIVE
                mods_root = fixed_target if relationship == "same" else fixed_target / "source-mods"
                alpha = mods_root / "alpha"
                write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
                write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")

                with self.assertRaisesRegex(ValueError, "overlap"):
                    write_romfs_manifest(mods_root, output_root)

                self.assertEqual(list(fixed_target.parent.glob(".isaac_mods.staging.*")), [])

    def _assert_input_mutation_preserves_old_target(self, mutate_input):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Original', 1)")
        manifest_path = write_romfs_manifest(mods_root, output_root)
        old_manifest = manifest_path.read_bytes()
        old_main = (manifest_path.parent / "mods/alpha/main.lua").read_bytes()
        original_copy = pc_mod_manifest._copy_file
        mutated = False

        def copy_then_mutate(source_path, destination_path):
            nonlocal mutated
            original_copy(source_path, destination_path)
            if not mutated:
                mutated = True
                mutate_input(mods_root)

        with patch("tools.pc_mod_manifest._copy_file", side_effect=copy_then_mutate):
            with self.assertRaisesRegex(ValueError, "changed"):
                write_romfs_manifest(mods_root, output_root)

        self.assertEqual(manifest_path.read_bytes(), old_manifest)
        self.assertEqual((manifest_path.parent / "mods/alpha/main.lua").read_bytes(), old_main)

    def test_write_romfs_manifest_rejects_new_file_during_sync(self):
        self._assert_input_mutation_preserves_old_target(
            lambda mods_root: write_file(mods_root / "alpha/new.lua", "return true")
        )

    def test_write_romfs_manifest_rejects_new_mod_during_sync(self):
        def add_mod(mods_root):
            write_file(mods_root / "beta/metadata.xml", "<metadata><name>Beta</name><id>2</id></metadata>")
            write_file(mods_root / "beta/main.lua", "return RegisterMod('Beta', 1)")

        self._assert_input_mutation_preserves_old_target(add_mod)

    def test_write_romfs_manifest_rejects_content_change_during_sync(self):
        self._assert_input_mutation_preserves_old_target(
            lambda mods_root: write_file(mods_root / "alpha/main.lua", "return RegisterMod('Changed', 1)")
        )

    def test_write_romfs_manifest_validates_three_snapshots_without_three_public_discoveries(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")

        with (
            patch("tools.pc_mod_manifest.discover_mods", wraps=discover_mods) as discover,
            patch(
                "tools.pc_mod_manifest._revalidate_input_snapshot",
                wraps=pc_mod_manifest._revalidate_input_snapshot,
            ) as revalidate,
        ):
            write_romfs_manifest(mods_root, output_root)

        self.assertEqual(discover.call_count, 1)
        self.assertEqual(revalidate.call_count, 2)

    def test_write_romfs_manifest_copies_disabled_mod_markers_and_nested_files(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        disabled = mods_root / "disabled"
        write_file(disabled / "metadata.xml", "<metadata><name>Disabled</name><id>1</id></metadata>")
        write_file(disabled / "main.lua", "return RegisterMod('Disabled', 1)")
        write_file(disabled / "disable.it", "")
        write_file(disabled / "src/nested/mod.lua", "return true")

        manifest_path = write_romfs_manifest(mods_root, output_root)

        self.assertEqual((manifest_path.parent / "mods/disabled/disable.it").read_bytes(), b"")
        self.assertEqual(
            (manifest_path.parent / "mods/disabled/src/nested/mod.lua").read_bytes(),
            b"return true",
        )

    def test_write_romfs_manifest_writes_utf8_with_lf_only(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha 测试</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        original_write_text = Path.write_text

        def windows_translating_write_text(path, data, *args, **kwargs):
            if path.name == "manifest.json":
                return path.write_bytes(data.replace("\n", "\r\n").encode("utf-8"))
            return original_write_text(path, data, *args, **kwargs)

        with patch.object(Path, "write_text", new=windows_translating_write_text):
            manifest_path = write_romfs_manifest(mods_root, output_root)

        manifest_bytes = manifest_path.read_bytes()
        self.assertNotIn(b"\r", manifest_bytes)
        self.assertIn("测试", manifest_bytes.decode("utf-8"))

    def test_write_romfs_manifest_does_not_publish_when_copied_file_hash_mismatches(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")

        with patch("tools.pc_mod_manifest._sha256_file_at", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "sha256"):
                write_romfs_manifest(mods_root, output_root)

        romfs_root = output_root / "atmosphere/contents/010021C000B6A000/romfs"
        self.assertFalse((romfs_root / "isaac_mods").exists())

    def test_write_romfs_manifest_preserves_existing_output_when_hashing_fails(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        manifest_path = write_romfs_manifest(mods_root, output_root)
        original_tree = {
            path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
            for path in manifest_path.parent.rglob("*")
            if path.is_file()
        }

        with patch("tools.pc_mod_manifest._sha256_file_at", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "sha256"):
                write_romfs_manifest(mods_root, output_root)

        self.assertEqual(
            {
                path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
                for path in manifest_path.parent.rglob("*")
                if path.is_file()
            },
            original_tree,
        )

    def test_write_romfs_manifest_keeps_every_historical_backup(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Original', 1)")
        manifest_path = write_romfs_manifest(mods_root, output_root)
        romfs_parent = manifest_path.parent.parent
        user_owned_lookalike = romfs_parent / ".isaac_mods.backup.0123456789abcdef"
        user_owned_file = romfs_parent / ".isaac_mods.backup.fedcba9876543210"
        write_file(user_owned_lookalike / "keep.txt", "directory")
        write_file(user_owned_file, "file")

        for publication in range(1, 4):
            write_file(alpha / "main.lua", f"return RegisterMod('Published {publication}', 1)")
            self.assertEqual(write_romfs_manifest(mods_root, output_root), manifest_path)

        backups = sorted(
            path
            for path in romfs_parent.iterdir()
            if path.is_dir() and path.name.startswith(".isaac_mods.backup.")
        )
        self.assertEqual(len(backups), 4)
        self.assertEqual((user_owned_lookalike / "keep.txt").read_text(encoding="utf-8"), "directory")
        self.assertEqual(user_owned_file.read_text(encoding="utf-8"), "file")

    def test_write_romfs_manifest_second_sync_leaves_old_target_as_one_backup(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Original', 1)")
        manifest_path = write_romfs_manifest(mods_root, output_root)
        old_manifest = manifest_path.read_bytes()
        write_file(alpha / "main.lua", "return RegisterMod('Changed', 1)")

        write_romfs_manifest(mods_root, output_root)

        backups = [
            path
            for path in manifest_path.parent.parent.iterdir()
            if path.is_dir() and path.name.startswith(".isaac_mods.backup.")
        ]
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "manifest.json").read_bytes(), old_manifest)

    def test_write_romfs_manifest_restores_existing_output_when_publish_replace_fails(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        manifest_path = write_romfs_manifest(mods_root, output_root)
        old_manifest = manifest_path.read_bytes()
        old_main = (manifest_path.parent / "mods/alpha/main.lua").read_bytes()
        write_file(alpha / "main.lua", "return RegisterMod('Changed', 1)")
        original_replace = os.replace
        failed = False

        def fail_first_target_replace(source, destination, *args, **kwargs):
            nonlocal failed
            if Path(destination).name == "isaac_mods" and not failed:
                failed = True
                raise OSError("simulated publish failure")
            return original_replace(source, destination, *args, **kwargs)

        with patch("tools.pc_mod_manifest.os.replace", side_effect=fail_first_target_replace):
            with self.assertRaisesRegex(OSError, "simulated publish failure"):
                write_romfs_manifest(mods_root, output_root)

        self.assertEqual(manifest_path.read_bytes(), old_manifest)
        self.assertEqual((manifest_path.parent / "mods/alpha/main.lua").read_bytes(), old_main)

    def test_write_romfs_manifest_rejects_existing_target_symlink_without_touching_external_tree(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        outside_root = Path(self.temp_dir.name) / "outside"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        romfs_root = output_root / "atmosphere/contents/010021C000B6A000/romfs"
        romfs_root.mkdir(parents=True)
        write_file(outside_root / "keep.txt", "keep")
        try:
            os.symlink(outside_root, romfs_root / "isaac_mods")
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "symlink"):
            write_romfs_manifest(mods_root, output_root)

        self.assertEqual((outside_root / "keep.txt").read_text(encoding="utf-8"), "keep")
        self.assertFalse((outside_root / "manifest.json").exists())

    def test_write_romfs_manifest_rejects_symlinked_output_parent(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        outside_root = Path(self.temp_dir.name) / "outside"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")
        output_root.mkdir()
        outside_root.mkdir()
        try:
            os.symlink(outside_root, output_root / "atmosphere")
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "symlink"):
            write_romfs_manifest(mods_root, output_root)

        self.assertFalse((outside_root / "contents").exists())

    def test_write_romfs_manifest_does_not_require_posix_directory_fds(self):
        mods_root = Path(self.temp_dir.name) / "mods"
        output_root = Path(self.temp_dir.name) / "output"
        alpha = mods_root / "alpha"
        write_file(alpha / "metadata.xml", "<metadata><name>Alpha</name><id>1</id></metadata>")
        write_file(alpha / "main.lua", "return RegisterMod('Alpha', 1)")

        with patch.object(os, "supports_dir_fd", set()):
            manifest_path = write_romfs_manifest(mods_root, output_root)

        self.assertTrue(manifest_path.is_file())


if __name__ == "__main__":
    unittest.main()
