import hashlib
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def copy_tracked_workspace(destination):
    source_root = ROOT.parent
    project = Path(destination) / "project"
    tracked = subprocess.run(
        ["git", "-C", str(source_root), "ls-files", "-z"],
        text=False,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")

    for relative_bytes in tracked:
        if not relative_bytes:
            continue
        relative = Path(os.fsdecode(relative_bytes))
        source = source_root / relative
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # Make dry-runs recurse into these stage-specific build directories. Keep
    # the isolation fixture independent of untracked workspace build output.
    for stage in (12, 13):
        (project / "runtime" / f"build-diagnostic-stage{stage}").mkdir()

    return project, project / "runtime"


def copy_docker_integration_workspace(destination):
    project = Path(destination) / "project"
    copied_runtime = project / "runtime"
    shutil.copytree(
        ROOT,
        copied_runtime,
        ignore=shutil.ignore_patterns(
            ".DS_Store", "__pycache__", "build", "build-*", "deploy", "deploy-*", "runtime.elf"
        ),
    )
    shutil.copytree(ROOT.parent / "tools", project / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    evidence = ROOT.parent / "analysis/modmanager-lifecycle/91C73FDD575061318D68886316AFEAC72388B2AB.json"
    evidence_copy = project / evidence.relative_to(ROOT.parent)
    evidence_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(evidence, evidence_copy)

    for pattern in (
        "The Binding*/Program #0/0/main.npdm",
        "The Binding*/Program #0/1/.nro/Repentance.nro",
    ):
        source = next(ROOT.parent.glob(pattern), None)
        if source is None:
            raise AssertionError(f"missing integration build input: {pattern}")
        target = project / source.relative_to(ROOT.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    return project, copied_runtime


def first_preprocessor_gate(source):
    lines = source.splitlines()
    if not lines:
        raise AssertionError("runtime entry source is empty")

    first = lines[0]
    if not re.match(r"^[ \t]*#if\b", first):
        raise AssertionError("the first source line must be the runtime preprocessor gate")

    gate = first
    line_index = 0
    while gate.rstrip().endswith("\\"):
        line_index += 1
        if line_index == len(lines):
            raise AssertionError("runtime preprocessor gate has an unterminated continuation")
        gate = gate.rstrip()[:-1] + " " + lines[line_index].strip()
    return gate


def snapshot_path(path):
    if not path.exists():
        return None
    if path.is_file():
        stat = path.stat()
        return ("file", stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())

    entries = []
    for entry in sorted(path.rglob("*")):
        stat = entry.stat()
        relative = str(entry.relative_to(path))
        digest = hashlib.sha256(entry.read_bytes()).hexdigest() if entry.is_file() else None
        entries.append((relative, entry.is_dir(), stat.st_mtime_ns, digest))
    return ("directory", path.stat().st_mtime_ns, tuple(entries))


def defined_elf_symbols(binary):
    if len(binary) < 64 or binary[:4] != b"\x7fELF":
        raise AssertionError("Runtime artifact is not an ELF file")
    if binary[4] != 2 or binary[5] != 1:
        raise AssertionError("Runtime artifact must be little-endian ELF64")

    section_offset = struct.unpack_from("<Q", binary, 0x28)[0]
    section_entry_size = struct.unpack_from("<H", binary, 0x3A)[0]
    section_count = struct.unpack_from("<H", binary, 0x3C)[0]
    if section_entry_size != 64:
        raise AssertionError("Runtime artifact has an unexpected ELF64 section header size")

    symbols = set()
    for index in range(section_count):
        offset = section_offset + index * section_entry_size
        section_type = struct.unpack_from("<I", binary, offset + 4)[0]
        if section_type != 2:  # SHT_SYMTAB
            continue
        symbol_offset, symbol_size = struct.unpack_from("<QQ", binary, offset + 24)
        string_section_index = struct.unpack_from("<I", binary, offset + 40)[0]
        symbol_entry_size = struct.unpack_from("<Q", binary, offset + 56)[0]
        if symbol_entry_size != 24:
            raise AssertionError("Runtime artifact has an unexpected ELF64 symbol size")
        string_header = section_offset + string_section_index * section_entry_size
        string_offset, string_size = struct.unpack_from("<QQ", binary, string_header + 24)
        string_table = binary[string_offset:string_offset + string_size]
        for symbol_index in range(symbol_size // symbol_entry_size):
            entry = symbol_offset + symbol_index * symbol_entry_size
            name_offset, _, _, section_index, _, _ = struct.unpack_from("<IBBHQQ", binary, entry)
            if section_index == 0:
                continue
            name_end = string_table.find(b"\0", name_offset)
            symbols.add(string_table[name_offset:name_end])
    return symbols


def unittest_summary(output):
    ran = re.search(r"Ran (\d+) tests? in ", output)
    status = re.search(r"^(OK|FAILED)(?: \([^\n]+\))?$", output, re.M)
    if ran is None or status is None:
        raise AssertionError(f"cannot parse unittest summary:\n{output}")
    return int(ran.group(1)), status.group(0)


class GameFileReaderStage11Tests(unittest.TestCase):
    def test_non_integration_suite_ignores_workspace_elf_artifacts(self):
        if os.environ.get("ISAAC_ARTIFACT_ISOLATION_PROBE") == "1":
            return

        with tempfile.TemporaryDirectory(prefix="isaac-elf-isolation-") as temporary:
            project, copied_runtime = copy_tracked_workspace(temporary)
            environment = os.environ.copy()
            environment.pop("ISAAC_RUN_DOCKER_INTEGRATION", None)
            environment["ISAAC_ARTIFACT_ISOLATION_PROBE"] = "1"

            def run_suite():
                return subprocess.run(
                    [
                        os.sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "runtime/tests",
                        "-v",
                    ],
                    cwd=project,
                    env=environment,
                    text=True,
                    capture_output=True,
                )

            clean = run_suite()
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

            stale_markers = b"\n".join(
                (
                    b"_Z13luaL_newstatev",
                    b"_Z14svcQueryMemoryP10MemoryInfoPjm",
                    b"_ZN2nn2fs",
                    b"RuntimeFsLog RuntimeFsLogDiagnose smInitialize fsInitialize",
                    b"ReportStage6Failure kStage6FailureMagic",
                    b"ISAACMOD ISAACMFL ManagerLoadConfigsDiagnosticCallback",
                    b"ISAACRST ISAACRFL ManagerLoadConfigsResetDiagnosticCallback",
                    b"ISAACFOK ISAACFFL RunStage11RomfsSentinelDiagnostic rom:/isaac_mod_probe.lua",
                    b"_GLOBAL__sub_I_hook_impl.cpp _GLOBAL__sub_I_inline_impl.cpp",
                )
            )
            for path in (
                copied_runtime / "runtime.elf",
                copied_runtime / "build-diagnostic-stage3/runtime.elf",
                copied_runtime / "build-diagnostic-stage7/runtime.elf",
                copied_runtime / "build-diagnostic-stage11/runtime.elf",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(stale_markers)

            stale = run_suite()
            self.assertEqual(stale.returncode, 0, stale.stdout + stale.stderr)
            self.assertEqual(
                unittest_summary(stale.stdout + stale.stderr),
                unittest_summary(clean.stdout + clean.stderr),
            )

    def test_stage12_runtime_entry_top_level_gate_includes_stage12(self):
        source = (ROOT / "source/runtime_entry.cpp").read_text()
        gate = first_preprocessor_gate(source)
        self.assertRegex(gate, r"^[ \t]*#if\b")
        for stage in (6, 7, 8, 9, 11, 12):
            with self.subTest(stage=stage):
                self.assertRegex(
                    gate,
                    rf"\bEXL_DIAGNOSTIC_STAGE\s*==\s*{stage}\b",
                )

    def test_stage12_top_level_gate_helper_ignores_later_unrelated_conditions(self):
        old_gate_with_later_stage12 = "\n".join(
            (
                "#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 11",
                "#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12",
                "#endif",
            )
        )
        gate = first_preprocessor_gate(old_gate_with_later_stage12)
        self.assertNotRegex(gate, r"\bEXL_DIAGNOSTIC_STAGE\s*==\s*12\b")

        continued_gate = first_preprocessor_gate(
            "#if EXL_DIAGNOSTIC_STAGE == 11 || \\\n+EXL_DIAGNOSTIC_STAGE == 12\n"
        )
        self.assertRegex(continued_gate, r"\bEXL_DIAGNOSTIC_STAGE\s*==\s*12\b")

    def test_stage12_runtime_artifact_defines_entry_and_contains_protocol_markers(self):
        if os.environ.get("ISAAC_RUN_DOCKER_INTEGRATION") != "1":
            self.skipTest("set ISAAC_RUN_DOCKER_INTEGRATION=1 to run the clean Docker artifact test")

        docker = shutil.which("docker")
        if docker is None:
            self.fail("ISAAC_RUN_DOCKER_INTEGRATION=1 requires the Docker CLI")

        availability = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(
            availability.returncode,
            0,
            "ISAAC_RUN_DOCKER_INTEGRATION=1 requires a working Docker daemon:\n"
            + availability.stdout
            + availability.stderr,
        )

        protected_paths = (
            ROOT / "runtime.elf",
            ROOT / "build",
            ROOT / "deploy",
            ROOT / "deploy-diagnostic",
            *(ROOT / f"build-diagnostic-stage{stage}" for stage in range(13)),
        )
        workspace_before = {path: snapshot_path(path) for path in protected_paths}

        with tempfile.TemporaryDirectory(prefix="isaac-stage12-integration-") as temporary:
            project, copied_runtime = copy_docker_integration_workspace(temporary)
            build = subprocess.run(
                [
                    docker,
                    "run",
                    "--rm",
                    "-v",
                    f"{project}:/work",
                    "-w",
                    "/work",
                    "devkitpro/devkita64:latest",
                    "sh",
                    "-lc",
                    ". /opt/devkitpro/devkita64.sh && set -e; "
                    "for stage in $(seq 0 12); do "
                    "make -C runtime clean DIAGNOSTIC_STAGE=$stage; "
                    "make -C runtime DIAGNOSTIC_STAGE=$stage; "
                    "done; "
                    "make -C runtime clean; make -C runtime",
                ],
                text=True,
                capture_output=True,
            )

            workspace_after = {path: snapshot_path(path) for path in protected_paths}
            self.assertEqual(
                workspace_after,
                workspace_before,
                "isolated Docker builds modified real workspace artifacts",
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            stage12_elf = copied_runtime / "build-diagnostic-stage12/runtime.elf"
            default_elf = copied_runtime / "runtime.elf"
            self.assertTrue(stage12_elf.is_file(), "clean Stage 12 build did not produce runtime.elf")
            self.assertTrue(default_elf.is_file(), "clean default build did not produce runtime.elf")
            stage12_binary = stage12_elf.read_bytes()
            default_binary = default_elf.read_bytes()
            stage12_symbols = defined_elf_symbols(stage12_binary)
            default_symbols = defined_elf_symbols(default_binary)

            stage_binaries = {}
            for stage in range(13):
                elf = copied_runtime / f"build-diagnostic-stage{stage}/runtime.elf"
                self.assertTrue(elf.is_file(), f"clean Stage {stage} build did not produce runtime.elf")
                stage_binaries[stage] = elf.read_bytes()

                exefs = (
                    copied_runtime
                    / f"deploy-diagnostic/stage{stage}/atmosphere/contents/010021C000B6A000/exefs"
                )
                self.assertEqual(
                    sorted(path.name for path in exefs.iterdir() if not path.name.startswith(".")),
                    ["main.npdm", "subsdk9"],
                )

            default_exefs = copied_runtime / "deploy/atmosphere/contents/010021C000B6A000/exefs"
            self.assertEqual(
                sorted(path.name for path in default_exefs.iterdir() if not path.name.startswith(".")),
                ["main.npdm", "subsdk9"],
            )
            update_relay = (
                "atmosphere/nro_patches/isaac-repentance-manager-update-relay/"
                "91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"
            )
            loadconfigs_relay = (
                "atmosphere/nro_patches/isaac-repentance-manager-loadconfigs-relay/"
                "91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"
            )
            self.assertGreater((copied_runtime / "deploy" / update_relay).stat().st_size, 8)
            self.assertGreater(
                (copied_runtime / "deploy-diagnostic/stage6" / update_relay).stat().st_size,
                8,
            )
            self.assertGreater(
                (copied_runtime / "deploy-diagnostic/stage8" / loadconfigs_relay).stat().st_size,
                8,
            )

        for binary in (default_binary, stage_binaries[7]):
            self.assertNotIn(b"_Z13luaL_newstatev", binary)
        for binary in (default_binary, stage_binaries[11]):
            self.assertNotIn(b"_Z14svcQueryMemoryP10MemoryInfoPjm", binary)
        for marker in (
            b"_ZN2nn2fs",
            b"RuntimeFsLog",
            b"RuntimeFsLogDiagnose",
            b"smInitialize",
            b"fsInitialize",
            b"ReportStage6Failure",
            b"kStage6FailureMagic",
            b"ISAACMOD",
            b"ISAACMFL",
            b"ManagerLoadConfigsDiagnosticCallback",
            b"ISAACRST",
            b"ISAACRFL",
            b"ManagerLoadConfigsResetDiagnosticCallback",
            b"ISAACFOK",
            b"ISAACFFL",
            b"RunStage11RomfsSentinelDiagnostic",
            b"rom:/isaac_mod_probe.lua",
        ):
            with self.subTest(default_artifact_marker=marker):
                self.assertNotIn(marker, default_binary)
        self.assertNotIn(b"_GLOBAL__sub_I_hook_impl.cpp", stage_binaries[3])
        self.assertNotIn(b"_GLOBAL__sub_I_inline_impl.cpp", stage_binaries[3])

        for marker in (b"exl_main", b"RunStage12RomfsLuaDiagnostic", b"ReportStage12Failure"):
            with self.subTest(marker=marker):
                self.assertTrue(
                    any(marker in name for name in stage12_symbols),
                    f"stage 12 must define {marker.decode()}, not merely reference it",
                )
        for marker in (b"rom:/runtime_probe.lua",):
            with self.subTest(marker=marker):
                self.assertIn(marker, stage12_binary)

        # ISAACELF/P are BreakReason payload immediates, encoded as MOVZ/MOVK
        # instructions rather than printable ELF strings on AArch64.
        for protocol, instruction_words in {
            "ISAACELF": (0xD28988C1, 0xF2A868A1, 0xF2C82821, 0xF2E92A61),
            "ISAACELP": (0xD2898A01, 0xF2A868A1, 0xF2C82821, 0xF2E92A61),
        }.items():
            for instruction_word in instruction_words:
                with self.subTest(protocol=protocol, instruction=instruction_word):
                    self.assertIn(struct.pack("<I", instruction_word), stage12_binary)

        self.assertTrue(any(b"exl_main" in name for name in default_symbols))
        for marker in (
            b"RunStage12RomfsLuaDiagnostic",
            b"ReportStage12Failure",
            b"rom:/runtime_probe.lua",
            b"@rom:/runtime_probe.lua",
            b"GameFileReader",
        ):
            with self.subTest(default_marker=marker):
                self.assertFalse(any(marker in name for name in default_symbols))
                self.assertNotIn(marker, default_binary)

    def test_stage12_reader_uses_a_fixed_bounded_romfs_lua_path(self):
        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        reader = (ROOT / "source/game_file_reader.cpp").read_text()
        header = (ROOT / "source/game_file_reader.hpp").read_text()
        self.assertIn('kRomfsLuaProbePath[] = "rom:/runtime_probe.lua"', constants)
        self.assertIn("kRomfsLuaProbeMaximumLength = 4096", constants)
        self.assertIn("enum class ScriptReadResult", header)
        self.assertIn("ReadScript(const Bindings& bindings", header)
        self.assertIn("openRead(fileStorage.data(), kRomfsLuaProbePath)", reader)
        self.assertIn("length <= 0 || length > static_cast<int>(kRomfsLuaProbeMaximumLength)", reader)

    def test_stage13_reader_uses_caller_path_and_capacity(self):
        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        reader = (ROOT / "source/game_file_reader.cpp").read_text()
        header = (ROOT / "source/game_file_reader.hpp").read_text()
        shim = (ROOT / "source/program/game_file_reader.cpp").read_text()

        self.assertIn('kRomfsModManifestPath[] = "rom:/isaac_mods/manifest.json"', constants)
        # 2026-09-14：清单缓冲区 131072 -> 524288（真实贴图 Mod 的清单 309 KB）。缓冲区上限
        # 就是读取上限（`length > capacity` 直接判失败），两处定义必须一致。
        self.assertIn("kRomfsModManifestMaximumLength = 524288", constants)
        # 2026-09-12：脚本缓冲区 16384 -> 1048576。真机报告 `01789200504` 证明 16 KiB 装不下
        # EID 的 `main.lua`（87,328 字节）→ `LengthOutOfRange` → `EntryRead` 失败 → Mod 的 Lua
        # 一行都没跑（现象是"加载不报错、回调注册表为空、屏幕上什么都没有"）。
        self.assertIn("kRomfsModScriptMaximumLength = 1048576", constants)
        self.assertIn("enum class TextReadResult", header)
        self.assertIn("ReadTextFile(const Bindings& bindings, const char* path", header)
        self.assertIn("openRead(fileStorage.data(), path)", reader)
        self.assertIn("length <= 0 || length > static_cast<int>(capacity)", reader)
        self.assertLess(
            reader.index("openRead(fileStorage.data(), path)"),
            reader.index("close(fileStorage.data())", reader.index("openRead(fileStorage.data(), path)")),
        )
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 13", shim)

        function = reader[reader.index("TextReadResult ReadTextFile"):]
        validation = function[:function.index("construct(fileStorage.data())")]
        for guard in ("path == nullptr", "path[0] == '\\0'", "buffer == nullptr", "capacity == 0", "outputLength == nullptr"):
            with self.subTest(guard=guard):
                self.assertIn(guard, validation)
        open_failure = function[function.index("if (!openRead"):function.index("const int length")]
        self.assertIn("destroy(fileStorage.data())", open_failure)
        self.assertNotIn("close(fileStorage.data())", open_failure)
        for result in ("LengthOutOfRange", "ReadMismatch"):
            result_index = function.index(f"TextReadResult::{result}")
            close_index = function.rfind("close(fileStorage.data())", 0, result_index)
            destroy_index = function.rfind("destroy(fileStorage.data())", 0, result_index)
            self.assertGreaterEqual(close_index, 0)
            self.assertLess(close_index, destroy_index)
            self.assertLess(destroy_index, result_index)

    def test_stage13_text_reader_is_absent_from_stage11_and_stage12_preprocessed_header(self):
        translation_unit = '#include "game_file_reader.hpp"\n'

        for stage in (11, 12, 13):
            with self.subTest(stage=stage):
                preprocessed = subprocess.run(
                    [
                        "c++", "-std=c++17", "-E", "-P", f"-DEXL_DIAGNOSTIC_STAGE={stage}",
                        "-I", str(ROOT / "source"), "-x", "c++", "-",
                    ],
                    input=translation_unit,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(preprocessed.returncode, 0, preprocessed.stdout + preprocessed.stderr)
                if stage == 13:
                    self.assertIn("TextReadResult", preprocessed.stdout)
                    self.assertIn("ReadTextFile", preprocessed.stdout)
                else:
                    self.assertFalse(
                        "TextReadResult" in preprocessed.stdout,
                        f"Stage {stage} preprocessed header exposes TextReadResult",
                    )
                    self.assertFalse(
                        "ReadTextFile" in preprocessed.stdout,
                        f"Stage {stage} preprocessed header exposes ReadTextFile",
                    )

    def test_stage11_and_stage12_reader_have_a_stage_gated_program_build_shim(self):
        shim = ROOT / "source/program/game_file_reader.cpp"
        self.assertTrue(shim.is_file())
        source = shim.read_text()
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 11 || EXL_DIAGNOSTIC_STAGE == 12", source)
        self.assertIn('#include "../game_file_reader.cpp"', source)

    def test_reader_headers_are_self_contained_for_fixed_width_types(self):
        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        header = (ROOT / "source/game_file_reader.hpp").read_text()
        self.assertIn("#include <cstddef>", constants)
        self.assertIn("std::uint32_t", header)

    def test_stage11_constants_are_bound_to_the_public_file_members(self):
        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        expected = {
            "kGameFileObjectSize": "0x60",
            "kGameFileConstructorFileOffset": "0x4CCBC0",
            "kGameFileOpenReadFileOffset": "0x4CCD58",
            "kGameFileGetLengthFileOffset": "0x4CD104",
            "kGameFileReadFileOffset": "0x4CD184",
            "kGameFileCloseFileOffset": "0x4CD00C",
            "kGameFileDestructorFileOffset": "0x4CCBFC",
            "kRomfsSentinelPath": '"rom:/isaac_mod_probe.lua"',
            "kRomfsSentinelContents": '"ISAAC_ROMFS_PROBE\\n"',
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertRegex(constants, rf"{name}[^;]*{re.escape(value)}")
        self.assertRegex(constants, r"kRomfsSentinelLength\s*=\s*18")

    def test_public_read_guard_matches_the_supplied_target_nro(self):
        nro = next(ROOT.parent.glob("The Binding*/Program #0/1/.nro/Repentance.nro"), None)
        if nro is None:
            self.skipTest("supplied target NRO is not present")

        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        match = re.search(
            r"kGameFileReadExpectedBytes\s*=\s*\{(?P<bytes>.*?)\};",
            constants,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        configured = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-F]{2})", match.group("bytes")))
        self.assertEqual(len(configured), 16)
        with nro.open("rb") as file:
            file.seek(0x4CD184)
            observed = file.read(16)
        self.assertEqual(configured, observed)

    def test_reader_verifies_all_six_game_entry_guards_before_binding(self):
        source = (ROOT / "source/game_file_reader.cpp").read_text()
        for token in (
            "kGameFileConstructorExpectedBytes", "kGameFileOpenReadExpectedBytes",
            "kGameFileGetLengthExpectedBytes", "kGameFileReadExpectedBytes",
            "kGameFileCloseExpectedBytes", "kGameFileDestructorExpectedBytes",
            "IsMappedRxModuleCodeWindow", "module.Contains", "module.buildId != kTargetBuildId",
        ):
            self.assertIn(token, source)

    def test_reader_uses_game_file_lifecycle_and_never_imports_private_filesystem(self):
        source = (ROOT / "source/game_file_reader.cpp").read_text()
        for forbidden in ("fsInitialize", "smInitialize", "nn::fs", "FileHandle", "new ", "malloc"):
            self.assertNotIn(forbidden, source)
        construct = source.index("construct(fileStorage.data())")
        open_read = source.index("openRead(fileStorage.data(), kRomfsSentinelPath)")
        get_length = source.index("getLength(fileStorage.data())")
        read = source.index("read(fileStorage.data(), buffer.data(), 1, kRomfsSentinelLength)")
        self.assertLess(construct, open_read)
        self.assertLess(open_read, get_length)
        self.assertLess(get_length, read)
        self.assertIn("const int bytesRead", source)
        self.assertIn("bytesRead != static_cast<int>(kRomfsSentinelLength)", source)
        self.assertNotIn("readStreamData", source)
        self.assertNotIn("kGameFileStreamPositionOffset", source)
        self.assertNotIn("kGameFileReadStateOffset", source)

    def test_open_failure_destroys_without_close_and_all_later_failures_close_then_destroy(self):
        source = (ROOT / "source/game_file_reader.cpp").read_text()
        open_failure = source[source.index("if (!openRead"):source.index("const int length")]
        self.assertIn("destroy(fileStorage.data())", open_failure)
        self.assertNotIn("close(fileStorage.data())", open_failure)
        for result in ("LengthMismatch", "ReadMismatch"):
            marker = f"ReadResult::{result}"
            result_index = source.index(marker)
            block = source[result_index - 250:result_index]
            self.assertIn("close(fileStorage.data())", block)
            self.assertIn("destroy(fileStorage.data())", block)
        final_cleanup = source[source.index("const bool matches"):]
        self.assertLess(final_cleanup.index("close(fileStorage.data())"), final_cleanup.index("destroy(fileStorage.data())"))
        self.assertIn("return matches ? ReadResult::Success : ReadResult::ContentMismatch;", final_cleanup)

    def test_stage11_callback_runs_after_original_update_and_only_once(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        callback = source[source.index("static void Callback"):source.index("};", source.index("static void Callback"))]
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 11", callback)
        self.assertLess(callback.index("Orig(self)"), callback.index("RunStage11RomfsSentinelDiagnostic"))
        self.assertIn("compare_exchange_strong", callback)
        self.assertIn("std::memory_order_acq_rel", callback)

    def test_stage11_reports_only_fixed_protocol_and_does_not_initialize_lua(self):
        hook = (ROOT / "source/hook_manager.cpp").read_text()
        entry = (ROOT / "source/runtime_entry.cpp").read_text()
        self.assertIn("0x4953414143464F4BULL", hook)
        self.assertIn("0x495341414346464CULL", hook)
        self.assertIn("(11ULL << 32) | 18ULL", hook)
        for code in (10, 11, 12, 13, 14):
            with self.subTest(file_failure_code=code):
                self.assertIn(f"(11ULL << 32) | {code}ULL", hook)
        self.assertIn("ReportStage11Failure(1)", entry)
        lua_gate = entry[entry.index("#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 7"):
                         entry.index("#endif", entry.index("LuaRuntime::Initialize")) + len("#endif")]
        self.assertIn("LuaRuntime::Initialize", lua_gate)
        self.assertNotIn("EXL_DIAGNOSTIC_STAGE == 11", lua_gate)

    def test_stage11_install_failure_only_disarms_an_unclaimed_callback(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        failure = source[source.index("if (TryInstallManagerUpdateHook(module)"):source.index("return Stage11InstallResult::Success")]
        self.assertIn("Stage11State expected = Stage11State::Armed", failure)
        self.assertIn(
            "compare_exchange_strong(expected, Stage11State::Unarmed, std::memory_order_acq_rel)",
            failure,
        )
        self.assertNotIn("g_Stage11State.store(Stage11State::Unarmed", failure)

    def test_stage11_worker_start_failure_reports_fixed_failure_status(self):
        entry = (ROOT / "source/runtime_entry.cpp").read_text()
        start_failure = entry[entry.index("if (R_FAILED(startResult))"):
                              entry.index("return;", entry.index("if (R_FAILED(startResult))"))]
        self.assertIn("ReportStage11Failure(8)", start_failure)

    def test_stage11_failure_codes_identify_the_exact_startup_or_scan_boundary(self):
        entry = (ROOT / "source/runtime_entry.cpp").read_text()
        worker = entry[entry.index("void ModuleWorker"):entry.index('extern "C" void exl_main')]
        for code in (3, 4):
            with self.subTest(worker_code=code):
                self.assertIn(f"ReportStage11Failure({code})", worker)
        self.assertIn("ReportStage11Failure(stage11CandidateSeen ? 9 : 6)", worker)
        for code in (1, 2, 7, 8):
            with self.subTest(entry_code=code):
                self.assertIn(f"ReportStage11Failure({code})", entry)

    def test_stage11_retries_a_partially_loaded_target_until_it_is_ready(self):
        entry = (ROOT / "source/runtime_entry.cpp").read_text()
        worker = entry[entry.index("void ModuleWorker"):entry.index('extern "C" void exl_main')]
        found = worker[worker.index("if (scan.status == TargetModuleScanStatus::Found"):worker.index("if (attempt + 1")]
        self.assertIn("bool stage11CandidateSeen = false;", worker)
        self.assertIn("stage11CandidateSeen = true;", found)
        self.assertNotIn("ReportStage11Failure(5)", found)
        self.assertIn("ReportStage11Failure(stage11CandidateSeen ? 9 : 6)", worker)

    def test_stage11_make_contract_copies_only_the_fixed_romfs_sentinel(self):
        makefile = (ROOT / "Makefile").read_text()
        payload = ROOT / "diagnostic/stage11/isaac_mod_probe.lua"
        self.assertEqual(payload.read_bytes(), b"ISAAC_ROMFS_PROBE\n")
        self.assertIn("DIAGNOSTIC_STAGE)),)", makefile)
        self.assertIn("ROMFS_SENTINEL_SOURCE :=", makefile)
        self.assertIn("DEPLOY_ROMFS_SENTINEL", makefile)
        self.assertIn("stage11/isaac_mod_probe.lua", makefile)
        self.assertIn("deploy-diagnostic/stage$(DIAGNOSTIC_STAGE)", makefile)

    def test_default_runtime_source_excludes_stage12_reader_and_diagnostic_entry(self):
        entry_shim = (ROOT / "source/program/runtime_entry.cpp").read_text()
        reader_shim = (ROOT / "source/program/game_file_reader.cpp").read_text()
        hook_header = (ROOT / "source/hook_manager.hpp").read_text()
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 12", entry_shim)
        self.assertIn("#if defined(EXL_DIAGNOSTIC_STAGE)", reader_shim)
        for stage in (11, 12, 13):
            with self.subTest(reader_stage=stage):
                self.assertIn(f"EXL_DIAGNOSTIC_STAGE == {stage}", reader_shim)
        self.assertIn(
            "#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 12\n"
            "enum class Stage12InstallResult",
            hook_header,
        )

    def test_stage12_uses_the_bound_game_reader_before_publishing_the_update_callback(self):
        header = (ROOT / "source/hook_manager.hpp").read_text()
        source = (ROOT / "source/hook_manager.cpp").read_text()
        self.assertIn("enum class Stage12InstallResult", header)
        self.assertIn("TryInstallRomfsLuaDiagnostic", header)
        install = source[source.index("Stage12InstallResult TryInstallRomfsLuaDiagnostic"):
                         source.index("bool InstallManagerUpdateHook")]
        self.assertIn("GameFileReader::VerifyBindings(module, &bindings)", install)
        self.assertIn("TryInstallManagerUpdateHook(module)", install)
        self.assertLess(install.index("GameFileReader::VerifyBindings(module, &bindings)"),
                        install.index("TryInstallManagerUpdateHook(module)"))

    def test_stage12_maps_external_lua_boundaries_to_the_fixed_protocol(self):
        source = (ROOT / "source/hook_manager.cpp").read_text()
        callback = source[source.index("static void Callback"):source.index("};", source.index("static void Callback"))]
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 12", callback)
        self.assertLess(callback.index("Orig(self);"), callback.index("RunStage12RomfsLuaDiagnostic"))
        for result, status in (
            ("ScriptReadResult::OpenFailed", 10),
            ("ScriptReadResult::LengthOutOfRange", 11),
            ("ScriptReadResult::ReadMismatch", 12),
            ("LuaInitResult::StateCreateFailed", 13),
            ("LuaInitResult::RuntimePreparationMemoryFailed", 13),
            ("LuaInitResult::RuntimePreparationFailed", 13),
            ("LuaInitResult::ScriptLoadFailed", 14),
            ("LuaInitResult::ScriptRunFailed", 15),
            ("LuaInitResult::MissingPostUpdateCallback", 16),
        ):
            with self.subTest(result=result):
                self.assertIn(result, source)
                self.assertIn(f"ReportStage12Failure({status})", source)
        self.assertIn("LuaRuntime::TakeCallbackError()", callback)
        self.assertIn("ReportStage12Failure(17)", callback)
        self.assertIn("(12ULL << 32) | 120ULL", callback)
        self.assertIn("0x4953414143454C50ULL", source)
        self.assertIn("0x4953414143454C46ULL", source)

    def test_stage13_manifest_require_protocol(self):
        entry = (ROOT / "source/runtime_entry.cpp").read_text()
        selector = (ROOT / "source/program/runtime_entry.cpp").read_text()
        hook = (ROOT / "source/hook_manager.cpp").read_text()
        header = (ROOT / "source/hook_manager.hpp").read_text()
        callback = hook[hook.index("static void Callback"):hook.index("};", hook.index("static void Callback"))]

        self.assertIn("EXL_DIAGNOSTIC_STAGE == 13", entry)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 13", selector)
        self.assertIn("TryInstallManifestRequireDiagnostic", entry)
        self.assertIn("TryInstallManifestRequireDiagnostic", header)
        self.assertIn("kStage13SuccessMagic = 0x4953414143525150ULL", hook)
        self.assertIn("kStage13FailureMagic = 0x4953414143525146ULL", hook)
        self.assertIn("kStage13RequireErrorTailMagic = 0x4953414143525154ULL", hook)
        self.assertIn("(13ULL << 32) | 120ULL", hook)
        self.assertLess(callback.index("Orig(self);"), callback.index("RunStage13ManifestRequireDiagnostic"))

        for state in ("Unarmed", "Armed", "Running", "Ready", "Finished"):
            with self.subTest(state=state):
                self.assertIn(state, hook)
        self.assertIn("GameFileReader::VerifyBindings(module, &bindings)", hook)
        install = hook[hook.index("Stage13InstallResult TryInstallManifestRequireDiagnostic"):]
        self.assertLess(
            install.index("GameFileReader::VerifyBindings(module, &bindings)"),
            install.index("g_Stage13State.store(Stage13State::Armed"),
        )
        self.assertLess(
            install.index("g_Stage13State.store(Stage13State::Armed"),
            install.index("TryInstallManagerUpdateHook(module)"),
        )
        rollback = install[install.index("if (TryInstallManagerUpdateHook(module)"):
                           install.index("return Stage13InstallResult::Success")]
        self.assertIn(
            "compare_exchange_strong(expected, Stage13State::Unarmed, std::memory_order_acq_rel)",
            rollback,
        )
        self.assertNotIn("g_Stage13State.store(Stage13State::Unarmed", rollback)

        for status in (*range(10, 23), *range(23, 26)):
            with self.subTest(status=status):
                self.assertEqual(hook.count(f"ReportStage13Failure({status})"), 1)
        self.assertEqual(hook.count("ReportStage13Failure(26)"), 1)
        self.assertIn("LuaRuntime::RequireErrorTail()", hook)
        failure_report = hook[hook.index("NORETURN void ReportStage13Failure"):
                              hook.index("uintptr_t target_address")]
        self.assertEqual(hook.count("svcBreak(BreakReason_User, kStage13"), 3)
        self.assertLess(
            failure_report.index("g_Stage13State.store(Stage13State::Finished, std::memory_order_release)"),
            failure_report.index("svcBreak(BreakReason_User, kStage13FailureMagic"),
        )
        self.assertLess(
            callback.index("g_Stage13State.store(Stage13State::Finished, std::memory_order_release)"),
            callback.index("svcBreak(BreakReason_User, kStage13SuccessMagic"),
        )

    def test_stage13_source_is_absent_from_default_and_earlier_runtime_stages(self):
        inputs = []
        for path in (ROOT / "source/runtime_constants.hpp", ROOT / "source/hook_manager.cpp"):
            inputs.append(re.sub(r'^#include.*$', '', path.read_text(), flags=re.MULTILINE))
        translation_unit = "\n".join(inputs)
        for stage in (None, 7, 11, 12, 13):
            with self.subTest(stage=stage):
                command = ["c++", "-std=c++17", "-E", "-P", "-x", "c++", "-"]
                if stage is not None:
                    command.insert(2, f"-DEXL_DIAGNOSTIC_STAGE={stage}")
                result = subprocess.run(command, input=translation_unit, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if stage == 13:
                    self.assertIn("kStage13SuccessMagic", result.stdout)
                    self.assertIn("rom:/isaac_mods/manifest.json", result.stdout)
                elif stage is None:
                    self.assertNotIn("kStage13SuccessMagic", result.stdout)
                    self.assertIn("rom:/isaac_mods/manifest.json", result.stdout)
                else:
                    self.assertNotIn("kStage13SuccessMagic", result.stdout)
                    self.assertNotIn("rom:/isaac_mods/manifest.json", result.stdout)

    def test_stage13_clean_docker_artifact_is_isolated_from_default_runtime(self):
        if os.environ.get("ISAAC_RUN_DOCKER_INTEGRATION") != "1":
            self.skipTest("set ISAAC_RUN_DOCKER_INTEGRATION=1 to run the clean Docker artifact test")

        docker = shutil.which("docker")
        if docker is None:
            self.fail("ISAAC_RUN_DOCKER_INTEGRATION=1 requires the Docker CLI")
        availability = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(availability.returncode, 0, availability.stdout + availability.stderr)

        with tempfile.TemporaryDirectory(prefix="isaac-stage13-integration-") as temporary:
            project, copied_runtime = copy_docker_integration_workspace(temporary)
            build = subprocess.run(
                [
                    docker, "run", "--rm", "-v", f"{project}:/work", "-w", "/work",
                    "devkitpro/devkita64:latest", "sh", "-lc",
                    ". /opt/devkitpro/devkita64.sh && "
                    "make -C runtime clean DIAGNOSTIC_STAGE=13 && "
                    "make -C runtime DIAGNOSTIC_STAGE=13 && "
                    "make -C runtime clean && make -C runtime",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            stage13_elf = (copied_runtime / "build-diagnostic-stage13/runtime.elf").read_bytes()
            default_elf = (copied_runtime / "runtime.elf").read_bytes()
            stage13_symbols = defined_elf_symbols(stage13_elf)
            default_symbols = defined_elf_symbols(default_elf)
            for marker in (
                b"RunStage13ManifestRequireDiagnostic",
                b"TryInstallManifestRequireDiagnostic",
                b"ReportStage13Failure",
            ):
                with self.subTest(marker=marker):
                    self.assertTrue(any(marker in symbol for symbol in stage13_symbols))
                    self.assertFalse(any(marker in symbol for symbol in default_symbols))
                    self.assertNotIn(marker, default_elf)
            self.assertIn(b"rom:/isaac_mods/manifest.json", stage13_elf)
            self.assertNotIn(b"rom:/isaac_mods/manifest.json", default_elf)

            atmosphere = copied_runtime / "deploy-diagnostic/stage13/atmosphere"
            relative_files = sorted(
                str(path.relative_to(atmosphere)) for path in atmosphere.rglob("*") if path.is_file()
            )
            self.assertEqual(
                relative_files,
                [
                    "contents/010021C000B6A000/exefs/main.npdm",
                    "contents/010021C000B6A000/exefs/subsdk9",
                    "contents/010021C000B6A000/romfs/isaac_mods/manifest.json",
                    "contents/010021C000B6A000/romfs/isaac_mods/mods/00 Runtime Require Probe/main.lua",
                    "contents/010021C000B6A000/romfs/isaac_mods/mods/00 Runtime Require Probe/metadata.xml",
                    "contents/010021C000B6A000/romfs/isaac_mods/mods/00 Runtime Require Probe/src/metadata.lua",
                    "contents/010021C000B6A000/romfs/isaac_mods/mods/00 Runtime Require Probe/src/mod.lua",
                    "nro_patches/isaac-repentance-manager-update-relay/"
                    "91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
                ],
            )


if __name__ == "__main__":
    unittest.main()
