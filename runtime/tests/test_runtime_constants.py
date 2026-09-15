import re
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONSTANTS = (ROOT / "source/runtime_constants.hpp").read_text()
CONFIG_MK = (ROOT / "config.mk").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()


def heartbeat_frames(start: int, stop: int, interval: int):
    return [frame for frame in range(start, stop) if frame % interval == 0]


def format_event(event: str, fields: dict[str, str] | None = None) -> str:
    line = f"event={event}"
    if fields:
        line += " " + " ".join(f"{key}={value}" for key, value in fields.items())
    return line


def _make_assignment(text: str, name: str) -> str:
    match = re.search(rf"^\s*{re.escape(name)}\s*:=\s*([^#\n]+)", text, re.M)
    if match is None:
        raise AssertionError(f"missing make assignment: {name}")
    return match.group(1).strip()


class RuntimeConstantsTest(unittest.TestCase):
    def test_level_isascent_guard_matches_the_fixed_nro_semantic_anchor(self):
        self.assertEqual(
            int(re.search(r"kLevelIsAscentOffset = 0x([0-9A-Fa-f]+)", CONSTANTS).group(1), 16),
            0x3E1D98,
        )
        section = re.search(r"kLevelIsAscentExpectedBytes = \{(.*?)\};", CONSTANTS, re.S).group(1)
        actual = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", section))
        self.assertEqual(actual, bytes.fromhex("080040b9080500511f15007128010054"))

    def test_target_constants_and_deploy_path(self):
        self.assertEqual(
            int(re.search(r"kTargetTitleId = 0x([0-9A-Fa-f]+)", CONSTANTS).group(1), 16),
            0x010021C000B6A000,
        )
        build_section = re.search(r"kTargetBuildId = \{(.*?)\};", CONSTANTS, re.S).group(1)
        build_id = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", build_section))
        self.assertEqual(build_id[:20].hex().upper(), "91C73FDD575061318D68886316AFEAC72388B2AB")
        self.assertEqual(
            int(re.search(r"kManagerUpdateFileOffset = 0x([0-9A-Fa-f]+)", CONSTANTS).group(1), 16),
            0x3F8DB8,
        )
        self.assertEqual(
            re.search(r'kLogPath\[\] = "([^"]+)"', CONSTANTS).group(1),
            "/atmosphere/logs/isaac-runtime-probe.log",
        )
        program_id = _make_assignment(CONFIG_MK, "PROGRAM_ID")
        load_kind = _make_assignment(CONFIG_MK, "LOAD_KIND")
        self.assertEqual(program_id, "010021C000B6A000")
        self.assertEqual(load_kind, "Module")

        module_branch = re.search(
            r"ifeq \(\$\(LOAD_KIND\),\s*Module\)(.*?)(?=^else ifeq|^else|^endif)",
            MAKEFILE,
            re.M | re.S,
        )
        self.assertIsNotNone(module_branch)
        binary_name = _make_assignment(module_branch.group(1), "BINARY_NAME")
        self.assertEqual(binary_name, "subsdk9")

        deploy_path = f"atmosphere/contents/{program_id}/exefs/{binary_name}"
        self.assertEqual(
            deploy_path,
            (ROOT / "deploy/atmosphere/contents" / program_id / "exefs" / binary_name)
            .relative_to(ROOT / "deploy")
            .as_posix(),
        )


class RuntimeBuildTests(unittest.TestCase):
    def test_runtime_fake_heap_reserves_lua_mod_headroom(self):
        settings = (ROOT / "source/program/setting.hpp").read_text()
        heap_size = int(
            re.search(r"constexpr size_t HeapSize = 0x([0-9A-Fa-f]+)", settings).group(1),
            16,
        )
        self.assertGreaterEqual(heap_size, 0x40000)
        # 2026-09-12：真机 `LUA_ERRMEM`（EID 的语言包把 2 MiB 的模块堆撑爆）之后提到 32 MiB。
        # 这里钉一个 16 MiB 的下界：它必须在"能装下真实 PC Mod"的量级上，而不是"够装几个
        # Font"的量级上。
        self.assertGreaterEqual(heap_size, 0x1000000)
        # The heap also backs every Runtime-owned `KAGE::Graphics::Font` (0x20050 bytes, see
        # runtime_constants.hpp). Assert the headroom in Fonts rather than in a bare number, so
        # lowering the heap below what the Font API needs fails here instead of on hardware.
        constants = (ROOT / "source/runtime_constants.hpp").read_text()
        font_object_size = int(
            re.search(r"kFontObjectSize = 0x([0-9A-Fa-f]+)", constants).group(1), 16
        )
        self.assertGreaterEqual(
            heap_size // font_object_size, 8,
            "fake heap 至少要为 Lua 自身之外留出 8 个 Font 对象",
        )

    def test_lua_runtime_declares_the_c_api_with_c_linkage(self):
        wrapper = (ROOT / "source/lua_runtime.cpp").read_text()
        self.assertRegex(
            wrapper,
            r'extern "C" \{\s*#include <lauxlib\.h>\s*#include <lualib\.h>\s*\}',
        )

    def test_runtime_svc_header_is_reached_through_the_c_linkage_wrapper(self):
        common = (ROOT / "source/common.hpp").read_text()
        nx = (ROOT / "source/lib/nx/nx.h").read_text()
        self.assertIn('#include "lib/nx/nx.h"', common)
        self.assertRegex(
            nx,
            r'(?s)#ifdef __cplusplus\s*extern "C" \{\s*#endif.*'
            r'#include "kernel/svc\.h".*#ifdef __cplusplus\s*\}\s*#endif',
        )

    def test_default_runtime_source_does_not_use_game_nn_filesystem(self):
        for path in (ROOT / "source/runtime_entry.cpp", ROOT / "source/hook_manager.cpp"):
            with self.subTest(path=path):
                source = path.read_text()
                self.assertNotIn("nn::fs", source)
                self.assertNotIn('"nn/fs/', source)

    def test_default_runtime_make_excludes_runtime_filesystem_diagnostics(self):
        rules = (ROOT / "misc/mk/common.mk").read_text()
        self.assertIn("ifeq ($(filter 4 5,$(DIAGNOSTIC_STAGE)),)", rules)
        self.assertIn("CFILES := $(filter-out fs_ipc.c,$(CFILES))", rules)

    def test_default_runtime_source_stage_gates_diagnostic_implementations(self):
        runtime_entry = (ROOT / "source/runtime_entry.cpp").read_text()
        hook = (ROOT / "source/hook_manager.cpp").read_text()
        contracts = (
            (runtime_entry, 6, "ReportStage6Failure"),
            (hook, 8, "ManagerLoadConfigsDiagnosticCallback"),
            (hook, 9, "ManagerLoadConfigsResetDiagnosticCallback"),
            (hook, 11, "RunStage11RomfsSentinelDiagnostic"),
        )
        for source, stage, marker in contracts:
            with self.subTest(stage=stage, marker=marker):
                self.assertRegex(
                    source,
                    rf"(?s)#if defined\(EXL_DIAGNOSTIC_STAGE\) && EXL_DIAGNOSTIC_STAGE == {stage}"
                    rf"(?:(?!#endif).)*{marker}(?:(?!#endif).)*#endif",
                )

    def test_deploy_contract_includes_runtime_and_npdm_overlay(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("patch_npdm.py", MAKEFILE)
        self.assertIn("DEPLOY_RELAY_IPS", MAKEFILE)
        self.assertIn("DEPLOY_GAME_OBSERVER_RELAY_IPS", MAKEFILE)
        self.assertIn("nro_patches/isaac-repentance-game-observer-relay", MAKEFILE)
        self.assertIn("--kind game-observer-relay", MAKEFILE)
        self.assertIn("DEPLOY_GAME_UPDATE_OBSERVER_RELAY_IPS", MAKEFILE)
        self.assertIn("nro_patches/isaac-repentance-game-update-observer-relay", MAKEFILE)
        self.assertIn("--kind game-update-observer-relay", MAKEFILE)
        self.assertIn("DEPLOY_GAME_STATE2_OBSERVER_RELAY_IPS", MAKEFILE)
        self.assertIn("nro_patches/isaac-repentance-game-state2-observer-relay", MAKEFILE)
        self.assertIn("--kind game-state2-observer-relay", MAKEFILE)
        self.assertIn("nro_patches/isaac-repentance-manager-update-relay", MAKEFILE)
        self.assertIn("--kind relay", MAKEFILE)
        self.assertIn("ORIGINAL_NRO", MAKEFILE)
        self.assertIn("exefs/main.npdm", MAKEFILE)
        self.assertIn("exefs/subsdk9", MAKEFILE)
        self.assertIn("ORIGINAL_NPDM", MAKEFILE)
        self.assertNotIn("ORIGINAL_NPDM_PREREQ", MAKEFILE)
        self.assertIn("删除整个", readme)

    def test_runtime_links_libnx_for_its_private_filesystem_service(self):
        module_rules = (ROOT / "misc/mk/common.mk").read_text()
        self.assertIn("LIBS\t:=\t-lnx", module_rules)
        self.assertIn("LIBDIRS\t:=\t$(LIBNX)", module_rules)

    def test_make_parses_from_a_project_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="isaac runtime ") as temporary:
            temporary_root = Path(temporary)
            copied_runtime = temporary_root / "project with spaces" / "runtime"
            shutil.copytree(ROOT, copied_runtime, ignore=shutil.ignore_patterns("build", "deploy"))
            (copied_runtime / "build").mkdir()

            with tempfile.TemporaryDirectory(prefix="fake-devkitpro-") as fake_devkit:
                fake_devkitpro = Path(fake_devkit)
                switch_rules = fake_devkitpro / "libnx" / "switch_rules"
                switch_rules.parent.mkdir(parents=True)
                switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")

                environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}
                result = subprocess.run(
                    ["make", "-C", str(copied_runtime), "-n", "clean"],
                    cwd=temporary_root,
                    env=environment,
                    text=True,
                    capture_output=True,
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_make_dry_run_builds_from_a_project_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="isaac runtime ") as temporary:
            temporary_root = Path(temporary)
            copied_runtime = temporary_root / "project with spaces" / "runtime"
            shutil.copytree(ROOT, copied_runtime, ignore=shutil.ignore_patterns("build", "deploy"))
            (copied_runtime / "build").mkdir()

            with tempfile.TemporaryDirectory(prefix="fake-devkitpro-") as fake_devkit:
                fake_devkitpro = Path(fake_devkit)
                switch_rules = fake_devkitpro / "libnx" / "switch_rules"
                switch_rules.parent.mkdir(parents=True)
                switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")

                environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}
                result = subprocess.run(
                    ["make", "-C", str(copied_runtime), "-n"],
                    cwd=temporary_root,
                    env=environment,
                    text=True,
                    capture_output=True,
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_default_deploy_manifest_contains_the_npdm_and_runtime(self):
        self.assertIn("SD_OUT := atmosphere/contents/$(PROGRAM_ID)/exefs", MAKEFILE)
        self.assertIn("DEPLOY_BINARY = $(OUT)/$(SD_OUT)/$(BINARY_NAME)", MAKEFILE)
        self.assertIn("DEPLOY_NPDM := $(OUT)/atmosphere/contents/$(PROGRAM_ID)/exefs/main.npdm", MAKEFILE)

    def test_makefile_generates_the_manager_update_relay(self):
        self.assertIn("all: $(DEPLOY_NPDM)\nifneq ($(filter 102 104 108 109 110 112 127 128,$(DIAGNOSTIC_STAGE)),)\nelse\nall: $(DEPLOY_RELAY_IPS)\nendif", MAKEFILE)
        self.assertIn("$(DEPLOY_RELAY_IPS): FORCE", MAKEFILE)
        self.assertIn("--kind relay", MAKEFILE)
        for target, patch_kind in (
            ("$(DEPLOY_GAME_OBSERVER_RELAY_IPS): FORCE", "--kind game-observer-relay"),
            ("$(DEPLOY_GAME_UPDATE_OBSERVER_RELAY_IPS): FORCE", "--kind game-update-observer-relay"),
            ("$(DEPLOY_GAME_STATE2_OBSERVER_RELAY_IPS): FORCE", "--kind game-state2-observer-relay"),
            ("$(DEPLOY_GAME_ISPAUSED_RENDER_OBSERVER_RELAY_IPS): FORCE", "--kind game-ispaused-render-observer-relay"),
        ):
            with self.subTest(target=target):
                self.assertIn(target, MAKEFILE)
                self.assertIn(patch_kind, MAKEFILE)


class RuntimeDiagnosticTests(unittest.TestCase):
    def test_default_runtime_has_no_filesystem_logging_path(self):
        entry = (ROOT / "source/runtime_entry.cpp").read_text()
        hook = (ROOT / "source/hook_manager.cpp").read_text()

        for source in (entry, hook):
            self.assertNotIn("ProbeLogger", source)
            self.assertNotIn("RuntimeFsLog", source)
            self.assertNotIn("smInitialize", source)
            self.assertNotIn("fsInitialize", source)
            self.assertNotIn("nn::fs", source)

    def test_ipc_backend_owns_file_handle_before_reading_its_size(self):
        source = (ROOT / "source/fs_ipc.c").read_text()
        open_call = source.index("fsFsOpenFile")
        ownership = source.index("g_FileOpen = true", open_call)
        size_read = source.index("fsFileGetSize(&g_File, writeOffset)", open_call)
        self.assertLess(ownership, size_read)

    def test_ipc_backend_has_a_self_contained_filesystem_diagnostic(self):
        source = (ROOT / "source/fs_ipc.c").read_text()
        header = (ROOT / "source/fs_ipc.h").read_text()

        self.assertIn("RuntimeFsDiagnosticResult RuntimeFsLogDiagnose", source)
        self.assertIn("switch/services/sm.h", source)
        self.assertIn("RuntimeFsDiagnosticStage_ServiceManager", source)
        self.assertIn("RuntimeFsDiagnosticStage_FsInitialize", source)
        self.assertIn("RuntimeFsDiagnosticStage_OpenSdCardFileSystem", source)
        self.assertIn("RuntimeFsDiagnosticStage_OpenFile", source)
        self.assertIn("RuntimeFsDiagnosticStage_GetFileSize", source)
        self.assertIn("RuntimeFsDiagnosticStage_WriteFile", source)
        self.assertIn("RuntimeFsDiagnosticStage_Success", source)
        self.assertIn("RuntimeFsDiagnosticResult", header)

    def test_ipc_backend_initializes_service_manager_before_filesystem(self):
        source = (ROOT / "source/fs_ipc.c").read_text()

        self.assertIn("smInitialize()", source)
        self.assertIn("smExit()", source)
        sm_initialize = source.index("smInitialize()")
        fs_initialize = source.index("fsInitialize()")
        fs_exit = source.index("fsExit()")
        sm_exit = source.index("smExit()")
        self.assertLess(sm_initialize, fs_initialize)
        self.assertLess(fs_exit, sm_exit)

    def test_runtime_scans_without_recording_startup_events(self):
        source = (ROOT / "source/runtime_entry.cpp").read_text()
        self.assertIn("ScanTargetModule()", source)
        self.assertNotIn("runtime_init", source)
        self.assertNotIn("module_wait", source)


if __name__ == "__main__":
    unittest.main()
