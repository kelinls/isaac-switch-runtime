import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class JsonObject(list):
    pass


def _object_pairs(pairs):
    return JsonObject(pairs)


def _reject_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _check_depth(value, depth=1):
    if depth > 16:
        raise ValueError("JSON nesting exceeds 16")
    if isinstance(value, JsonObject):
        for _, child in value:
            _check_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_depth(child, depth + 1)


def _required_members(value, required, optional=frozenset()):
    if not isinstance(value, JsonObject):
        raise ValueError("object required")
    found = {}
    for key, child in value:
        if key in required or key in optional:
            if key in found:
                raise ValueError(f"duplicate required key: {key}")
            found[key] = child
    if not required <= found.keys():
        raise ValueError("missing required key")
    return found


def _validate_directory(directory):
    if not isinstance(directory, str):
        raise ValueError("directory must be a string")
    if len(directory.encode("utf-8")) >= 256:
        raise ValueError("directory exceeds owned array")
    if not directory or directory in (".", "..") or "/" in directory or "\\" in directory or "\0" in directory:
        raise ValueError("unsafe directory")


def _validate_entry(directory, entry):
    # `entry` is optional: a pure-resource Mod has no `main.lua`, so the manifest
    # omits the key and the loader mounts its content without initializing Lua.
    # When the key *is* present it must still be a path inside this Mod.
    if entry is None:
        return ""
    if not isinstance(entry, str):
        raise ValueError("entry must be a string")
    if len(entry.encode("utf-8")) >= 1024:
        raise ValueError("entry exceeds owned array")
    prefix = f"mods/{directory}/"
    if not entry.startswith(prefix) or not entry.endswith(".lua") or "\\" in entry or "\0" in entry:
        raise ValueError("unsafe entry")
    segments = entry.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError("unsafe entry segment")
    return entry


def select_first_enabled_mirror(document):
    if isinstance(document, str):
        document = document.encode("utf-8")
    try:
        decoded = document.decode("utf-8", errors="strict")
        root = json.loads(
            decoded,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON") from error
    _check_depth(root)
    members = _required_members(root, {"schema_version", "mods"})
    schema = members["schema_version"]
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != 1:
        raise ValueError("unsupported schema")
    mods = members["mods"]
    if not isinstance(mods, list) or isinstance(mods, JsonObject):
        raise ValueError("mods must be an array")

    selected = None
    for mod in mods:
        fields = _required_members(mod, {"directory", "enabled"}, {"entry"})
        if not isinstance(fields["enabled"], bool):
            raise ValueError("enabled must be boolean")
        _validate_directory(fields["directory"])
        if "entry" in fields and not isinstance(fields["entry"], str):
            # Present but `null`/non-string: the parser rejects it. Only an
            # *absent* key means "resource-only Mod".
            raise ValueError("entry must be a string")
        entry = _validate_entry(fields["directory"], fields.get("entry"))
        if fields["enabled"] and selected is None:
            selected = (fields["directory"], entry)
    if selected is None:
        raise ValueError("no enabled Mod")
    return selected


class ModManifestStage13Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-manifest-stage13-")
        temporary = Path(cls.temporary.name)
        harness = temporary / "manifest_harness.cpp"
        harness.write_text(
            """
#include "mod_manifest.hpp"

#include <iostream>
#include <iterator>
#include <string>

int main() {
    const std::string input(std::istreambuf_iterator<char>(std::cin), {});
    ModManifest::SelectedMod selected{};
    selected.directory[0] = 'X';
    selected.entry[0] = 'Y';
    const auto result = ModManifest::SelectFirstEnabled(input.data(), input.size(), &selected);
    std::cout << static_cast<unsigned>(result) << "\\n";
    std::cout << selected.directory.data() << "\\n" << selected.entry.data() << "\\n";
}
""".lstrip()
        )
        cls.executable = temporary / "manifest_harness"
        build = subprocess.run(
            [
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-DEXL_DIAGNOSTIC_STAGE=13", "-I", str(SOURCE),
                str(harness), str(SOURCE / "mod_manifest.cpp"), "-o", str(cls.executable),
            ],
            text=True,
            capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_parser(self, document):
        if isinstance(document, str):
            document = document.encode("utf-8")
        result = subprocess.run([str(self.executable)], input=document, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
        lines = result.stdout.decode("utf-8").splitlines()
        return int(lines[0]), tuple(lines[1:])

    def assert_rejected(self, document):
        with self.assertRaises(ValueError):
            select_first_enabled_mirror(document)
        status, output = self.run_parser(document)
        self.assertNotEqual(status, 0)
        self.assertEqual(output, ("X", "Y"))

    def test_selects_first_enabled_mod_after_disabled_mod(self):
        document = json.dumps(
            {
                "schema_version": 1,
                "unknown": {"nested": [None, {"ignored": True}]},
                "mods": [
                    {"entry": "mods/Off/main.lua", "enabled": False, "directory": "Off"},
                    {"enabled": True, "directory": "Probe", "entry": "mods/Probe/src/main.lua"},
                    {"enabled": True, "directory": "Later", "entry": "mods/Later/main.lua"},
                ],
            },
            ensure_ascii=False,
        )
        self.assertEqual(select_first_enabled_mirror(document), ("Probe", "mods/Probe/src/main.lua"))
        self.assertEqual(self.run_parser(document), (0, ("Probe", "mods/Probe/src/main.lua")))

    def test_accepts_escaped_unicode_paths_and_whitespace(self):
        document = ' \n {"mods":[{"enabled":true,"entry":"mods/\\u6d4b\\u8bd5/main.lua","directory":"\\u6d4b\\u8bd5"}],"schema_version":1}\t'
        self.assertEqual(select_first_enabled_mirror(document), ("测试", "mods/测试/main.lua"))
        self.assertEqual(self.run_parser(document), (0, ("测试", "mods/测试/main.lua")))

    def test_accepts_manifest_generated_by_existing_pc_tool(self):
        with tempfile.TemporaryDirectory(prefix="isaac-stage13-manifest-tool-") as temporary:
            output = Path(temporary) / "romfs-output"
            generated = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.inspect_pc_mod",
                    "--mods-root",
                    "runtime/diagnostic/stage13/pc-mods",
                    "--romfs-output",
                    str(output),
                ],
                cwd=ROOT.parent,
                text=True,
                capture_output=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            manifest = output / "atmosphere/contents/010021C000B6A000/romfs/isaac_mods/manifest.json"
            expected = ("00 Runtime Require Probe", "mods/00 Runtime Require Probe/main.lua")
            self.assertEqual(select_first_enabled_mirror(manifest.read_bytes()), expected)
            self.assertEqual(self.run_parser(manifest.read_bytes()), (0, expected))

    def test_rejects_bad_schema_and_root_shape(self):
        for document in (
            '[]',
            '{"schema_version":2,"mods":[]}',
            '{"schema_version":true,"mods":[]}',
            '{"mods":[]}',
            '{"schema_version":1}',
            '{"schema_version":1,"mods":{}}',
        ):
            with self.subTest(document=document):
                self.assert_rejected(document)

    def test_rejects_trailing_bytes_and_incomplete_json(self):
        for document in (
            '{"schema_version":1,"mods":[]} trailing',
            '{"schema_version":1,"mods":[',
            b'{"schema_version":1,"mods":[]}\x00',
        ):
            with self.subTest(document=document):
                self.assert_rejected(document)

    def test_rejects_duplicate_required_keys_even_when_escaped(self):
        for document in (
            '{"schema_version":1,"schema_version":1,"mods":[]}',
            '{"schema_version":1,"schema\\u005fversion":1,"mods":[]}',
            '{"schema_version":1,"mods":[],"mods":[]}',
            '{"schema_version":1,"mods":[{"directory":"A","directory":"A","entry":"mods/A/main.lua","enabled":true}]}',
            '{"schema_version":1,"mods":[{"directory":"A","entry":"mods/A/main.lua","enabled":true,"en\\u0061bled":true}]}',
        ):
            with self.subTest(document=document):
                self.assert_rejected(document)

    def test_rejects_missing_or_nonboolean_enabled(self):
        for enabled_member in ('', ',"enabled":1', ',"enabled":"true"', ',"enabled":null'):
            document = '{"schema_version":1,"mods":[{"directory":"A","entry":"mods/A/main.lua"' + enabled_member + '}]} '
            with self.subTest(enabled_member=enabled_member):
                self.assert_rejected(document)

    def test_rejects_unsafe_directory_and_entry_paths(self):
        cases = (
            ("..", "mods/../main.lua"),
            ("A/B", "mods/A/B/main.lua"),
            ("A\\B", "mods/A\\\\B/main.lua"),
            ("A", "mods/A/../main.lua"),
            ("A", "mods/A/src\\\\main.lua"),
            ("A", "/mods/A/main.lua"),
            ("A", "mods/A//main.lua"),
            ("A", "mods/A/main.luac"),
            ("A", "mods/B/main.lua"),
        )
        for directory, entry in cases:
            document = json.dumps({"schema_version": 1, "mods": [{"directory": directory, "entry": entry, "enabled": True}]})
            with self.subTest(directory=directory, entry=entry):
                self.assert_rejected(document)

    def test_rejects_paths_that_do_not_fit_owned_arrays(self):
        long_directory = "A" * 256
        documents = (
            json.dumps({"schema_version": 1, "mods": [{"directory": long_directory, "entry": f"mods/{long_directory}/main.lua", "enabled": True}]}),
            json.dumps({"schema_version": 1, "mods": [{"directory": "A", "entry": "mods/A/" + ("b" * 1013) + ".lua", "enabled": True}]}),
        )
        for document in documents:
            with self.subTest(length=len(document)):
                self.assert_rejected(document)

    def test_rejects_invalid_strings_and_invalid_utf8(self):
        for document in (
            b'{"schema_version":1,"mods":[{"directory":"\xff","entry":"mods/A/main.lua","enabled":true}]}',
            '{"schema_version":1,"mods":[{"directory":"\\u0000","entry":"mods/\\u0000/main.lua","enabled":true}]}',
            '{"schema_version":1,"mods":[{"directory":"\\uD800","entry":"mods/A/main.lua","enabled":true}]}',
            b'{"schema_version":1,"mods":[],"bad":"line\nfeed"}',
        ):
            with self.subTest(document=document):
                self.assert_rejected(document)

    def test_rejects_missing_required_mod_keys_and_wrong_types(self):
        mods = (
            {"entry": "mods/A/main.lua", "enabled": True},
            {"directory": 1, "entry": "mods/A/main.lua", "enabled": True},
            # `entry: null` stays invalid on purpose: the packer *omits* the key for
            # a resource-only Mod instead of writing `null`, and the parser only
            # accepts the key with a string value.
            {"directory": "A", "entry": None, "enabled": True},
            ["not", "an", "object"],
        )
        for mod in mods:
            with self.subTest(mod=mod):
                self.assert_rejected(json.dumps({"schema_version": 1, "mods": [mod]}))

    def test_accepts_enabled_mod_without_entry_as_resource_only(self):
        # A pure-resource Mod ships no `main.lua`, so the manifest has no `entry`.
        # It is a well-formed, selectable Mod: the loader mounts its content and
        # skips Lua initialization.
        document = json.dumps(
            {
                "schema_version": 1,
                "mods": [
                    {"directory": "Off", "entry": "mods/Off/main.lua", "enabled": False},
                    {"directory": "Textures", "enabled": True},
                ],
            }
        )
        self.assertEqual(select_first_enabled_mirror(document), ("Textures", ""))
        self.assertEqual(self.run_parser(document), (0, ("Textures", "")))

    def test_accepts_only_mod_without_entry_when_it_is_the_enabled_one(self):
        document = '{"schema_version":1,"mods":[{"directory":"Textures","enabled":true}]}'
        self.assertEqual(select_first_enabled_mirror(document), ("Textures", ""))
        self.assertEqual(self.run_parser(document), (0, ("Textures", "")))
        # An enabled Mod without `entry` is still skipped over by nothing: the
        # first enabled Mod wins, whether or not it has a script.
        document = json.dumps(
            {
                "schema_version": 1,
                "mods": [
                    {"directory": "Textures", "enabled": True},
                    {"directory": "Later", "entry": "mods/Later/main.lua", "enabled": True},
                ],
            }
        )
        self.assertEqual(select_first_enabled_mirror(document), ("Textures", ""))
        self.assertEqual(self.run_parser(document), (0, ("Textures", "")))

    def test_rejects_unsafe_directory_with_or_without_entry(self):
        # The directory rules are unchanged; only the entry is optional.
        for entry in ({}, {"entry": "mods/A/main.lua"}):
            document = json.dumps(
                {"schema_version": 1, "mods": [{"directory": "A/B", "enabled": True, **entry}]}
            )
            with self.subTest(entry=entry):
                self.assert_rejected(document)

    def test_rejects_documents_deeper_than_sixteen_levels(self):
        accepted_unknown = "0"
        for _ in range(14):
            accepted_unknown = "[" + accepted_unknown + "]"
        accepted = '{"schema_version":1,"mods":[{"directory":"A","entry":"mods/A/main.lua","enabled":true}],"unknown":' + accepted_unknown + '}'
        self.assertEqual(select_first_enabled_mirror(accepted), ("A", "mods/A/main.lua"))
        self.assertEqual(self.run_parser(accepted), (0, ("A", "mods/A/main.lua")))

        rejected_unknown = "[" + accepted_unknown + "]"
        rejected = '{"schema_version":1,"mods":[{"directory":"A","entry":"mods/A/main.lua","enabled":true}],"unknown":' + rejected_unknown + '}'
        self.assert_rejected(rejected)

    def test_source_contract_is_stage13_only_and_bounded(self):
        header = (SOURCE / "mod_manifest.hpp").read_text()
        parser = (SOURCE / "mod_manifest.cpp").read_text()
        shim = (SOURCE / "program/mod_manifest.cpp").read_text()
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 13", parser)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 13", shim)
        self.assertIn('#include "../mod_manifest.cpp"', shim)
        self.assertIn("kMaximumNesting = 16", parser)
        self.assertIn("SelectFirstEnabled(const char* json, std::size_t length", header)
        self.assertIn("std::array<char", header)
        for forbidden in ("nn::fs", "fsInitialize", "smInitialize", "ModManager", "opendir", "readdir", "malloc", "new "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, parser)


if __name__ == "__main__":
    unittest.main()
