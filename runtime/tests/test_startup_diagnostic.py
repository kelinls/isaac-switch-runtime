import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class StartupDiagnosticBuildTest(unittest.TestCase):
    def test_makefile_validates_stage_and_uses_isolated_output(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("DIAGNOSTIC_STAGE", makefile)
        self.assertIn("0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17", makefile)
        self.assertIn("deploy-diagnostic/stage$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("-DEXL_DIAGNOSTIC_STAGE=$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_ARTIFACT_DIR := $(EXL_BUILD_DIR)", makefile)
        self.assertIn("EXL_ARTIFACT_DIR := .", makefile)

        with tempfile.TemporaryDirectory(prefix="fake-devkitpro-") as temporary:
            fake_devkitpro = Path(temporary)
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")
            environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}

            valid = subprocess.run(
                ["make", "-n", "clean-runtime-output", "DIAGNOSTIC_STAGE=9"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            valid14 = subprocess.run(
                ["make", "-n", "clean-runtime-output", "DIAGNOSTIC_STAGE=14"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            valid16 = subprocess.run(
                ["make", "-n", "clean-runtime-output", "DIAGNOSTIC_STAGE=16"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            valid17 = subprocess.run(
                ["make", "-n", "clean-runtime-output", "DIAGNOSTIC_STAGE=17"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            invalid = {
                stage: subprocess.run(
                    ["make", "-n", f"DIAGNOSTIC_STAGE={stage}"],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                for stage in ("18", "0 1", "2 7")
            }

        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertIn("deploy-diagnostic/stage9", valid.stdout)
        self.assertEqual(valid14.returncode, 0, valid14.stdout + valid14.stderr)
        self.assertIn("deploy-diagnostic/stage14", valid14.stdout)
        self.assertEqual(valid16.returncode, 0, valid16.stdout + valid16.stderr)
        self.assertIn("deploy-diagnostic/stage16", valid16.stdout)
        self.assertEqual(valid17.returncode, 0, valid17.stdout + valid17.stderr)
        self.assertIn("deploy-diagnostic/stage17", valid17.stdout)
        for stage, result in invalid.items():
            with self.subTest(stage=stage):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DIAGNOSTIC_STAGE", result.stdout + result.stderr)

    def test_stage14_uses_runtime_entry_and_keeps_three_file_deployment_contract(self):
        selector = (SOURCE / "program/runtime_entry.cpp").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("EXL_DIAGNOSTIC_STAGE != 14", selector)
        self.assertIn("all: $(DEPLOY_NPDM)\nifneq ($(filter 102 104 108 109 110 112 127 128,$(DIAGNOSTIC_STAGE)),)\nelse\nall: $(DEPLOY_RELAY_IPS)\nendif", makefile)
        self.assertNotIn("filter 14,$(DIAGNOSTIC_STAGE)", makefile)

    def test_default_build_publishes_mod_payload_and_stage14_keeps_three_files(self):
        with tempfile.TemporaryDirectory(prefix="isaac-stage14-payload-") as temporary:
            project = Path(temporary) / "project"
            copied_runtime = project / "runtime"
            shutil.copytree(
                ROOT,
                copied_runtime,
                ignore=shutil.ignore_patterns("build*", "deploy", "deploy-diagnostic"),
            )
            shutil.copytree(ROOT.parent / "tools", project / "tools")
            (copied_runtime / "tools/patch_npdm.py").write_text(
                "from pathlib import Path\nimport argparse\n"
                "p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--output'); a=p.parse_args()\n"
                "o=Path(a.output); o.parent.mkdir(parents=True,exist_ok=True); o.write_bytes(b'npdm')\n"
            )
            (project / "tools/build_patches.py").write_text(
                "from pathlib import Path\nimport argparse\n"
                "p=argparse.ArgumentParser(); p.add_argument('--kind'); p.add_argument('--nro'); p.add_argument('--output'); a=p.parse_args()\n"
                "o=Path(a.output); o.parent.mkdir(parents=True,exist_ok=True); o.write_bytes(b'ips')\n"
            )
            fake_devkitpro = Path(temporary) / "devkitpro"
            switch_rules = fake_devkitpro / "libnx/switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text(
                "%.o:\n\t@touch $@\n%.elf:\n\t@touch $@\n"
                "%.nso: %.elf\n\t@touch $@\n%.npdm:\n\t@touch $@\n"
            )
            environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}
            for stage, output in ((None, "deploy"), (14, "deploy-diagnostic/stage14")):
                command = ["make", "-j2"]
                if stage is not None:
                    command.append(f"DIAGNOSTIC_STAGE={stage}")
                result = subprocess.run(
                    command,
                    cwd=copied_runtime,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                atmosphere = copied_runtime / output / "atmosphere"
                files = sorted(
                    path.relative_to(atmosphere).as_posix()
                    for path in atmosphere.rglob("*") if path.is_file()
                )
                expected = [
                    "contents/010021C000B6A000/exefs/main.npdm",
                    "contents/010021C000B6A000/exefs/subsdk9",
                    "nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
                    # 游戏开局（`Game::Start` / `StartFromSavedState` 的后置边界）是**常驻能力**：
                    # `make` 里那一行 `all: $(DEPLOY_LIFECYCLE_RELAY_IPS)` 在**所有**诊断阶段之外，
                    # 所以默认包与 stage14 包**都**带这条中继（模组的 `MC_POST_GAME_STARTED`
                    # 依赖它；EID 的开局初始化——套装计数就是其中之一——全挂在上面）。
                    # 旧断言只在默认包那一支里列它，是 2026-09-14 提升为常驻之前的写法。
                    "nro_patches/isaac-repentance-lifecycle-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
                ]
                if stage is None:
                    expected.extend([
                        "contents/010021C000B6A000/romfs/isaac_mods/manifest.json",
                        "contents/010021C000B6A000/romfs/isaac_mods/mods/MuteOnPause/main.lua",
                        "contents/010021C000B6A000/romfs/isaac_mods/mods/MuteOnPause/metadata.xml",
                        "contents/010021C000B6A000/romfs/isaac_mods/mods/MuteOnPause/src/metadata.lua",
                        "contents/010021C000B6A000/romfs/isaac_mods/mods/MuteOnPause/src/mod.lua",
                        "nro_patches/isaac-repentance-manager-render-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
                        "nro_patches/isaac-repentance-pre-get-collectible-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
                        # Mod content mount points must be restored after the engine rebuilds
                        # its mount point table, so the default package ships this relay too.
                        "nro_patches/isaac-repentance-rebuild-mount-points-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
                        # `MC_POST_RENDER` 的派发点在 `Manager::Render` 体内最后一次 `Present`
                        # 调用之前；这条 IPS 就是那个调用点上的中继，缺了它回调只能退回入口中继
                        # （下一帧、被实体与 HUD 盖住）。
                        "nro_patches/isaac-repentance-render-present-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
                        # Mod 贴图 PNG 桥（`isaac-repentance-image-path-relay`）**已删除**：
                        # 真机两轮证明"把请求改回 `.png`"不足以让引擎用上它（Load 成功、画面空白），
                        # 改由打包阶段给 `gfx/` 下的 PNG 产出同名 `.pcx`（见 `tools/pc_mod_manifest.py`
                        # 与 `runtime/source/content/image_path_relay.cpp` 的删除记录）。
                    ])
                self.assertEqual(files, sorted(expected))

    def test_stage13_make_contract(self):
        with tempfile.TemporaryDirectory(prefix="fake-devkitpro-") as temporary:
            fake_devkitpro = Path(temporary)
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")
            environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}
            stage13_build = ROOT / "build-diagnostic-stage13"
            stage13_build_existed = stage13_build.exists()
            stage13_build.mkdir(exist_ok=True)

            try:
                stage13 = subprocess.run(
                    ["make", "-n", "all", "DIAGNOSTIC_STAGE=13"],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                stage13_parallel = subprocess.run(
                    ["make", "-n", "-j8", "all", "DIAGNOSTIC_STAGE=13"],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                stage12 = subprocess.run(
                    ["make", "-n", "all", "DIAGNOSTIC_STAGE=12"],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
            finally:
                if not stage13_build_existed:
                    stage13_build.rmdir()

        self.assertEqual(stage13.returncode, 0, stage13.stdout + stage13.stderr)
        self.assertEqual(stage13_parallel.returncode, 0, stage13_parallel.stdout + stage13_parallel.stderr)
        self.assertEqual(stage12.returncode, 0, stage12.stdout + stage12.stderr)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("cd .. && $(PYTHON) -m tools.inspect_pc_mod", makefile)
        self.assertIn("--mods-root runtime/diagnostic/stage13/pc-mods", makefile)
        self.assertIn("--romfs-output runtime/$(OUT)", makefile)
        for result in (stage13, stage13_parallel):
            self.assertIn("tools.inspect_pc_mod", result.stdout)
            self.assertIn("runtime/diagnostic/stage13/pc-mods", result.stdout)
            self.assertIn("tools/patch_npdm.py", result.stdout)
            self.assertIn("tools/build_patches.py --kind relay", result.stdout)
            self.assertLess(
                result.stdout.index("tools.inspect_pc_mod"),
                result.stdout.index("tools/patch_npdm.py"),
            )
            self.assertLess(
                result.stdout.index("tools.inspect_pc_mod"),
                result.stdout.index("tools/build_patches.py --kind relay"),
            )
        self.assertNotIn("tools.inspect_pc_mod", stage12.stdout)

    def test_stage13_parallel_build_from_clean_copy_publishes_romfs_before_deploy_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="isaac-stage13-clean-make-") as temporary:
            project = Path(temporary) / "project"
            copied_runtime = project / "runtime"
            shutil.copytree(
                ROOT,
                copied_runtime,
                ignore=shutil.ignore_patterns("build*", "deploy", "deploy-diagnostic"),
            )
            shutil.copytree(ROOT.parent / "tools", project / "tools")

            # These publishers are outside the dependency-order contract under test.
            (copied_runtime / "tools" / "patch_npdm.py").write_text(
                "from pathlib import Path\nimport argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\nparser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "output = Path(args.output)\noutput.parent.mkdir(parents=True, exist_ok=True)\noutput.write_bytes(b'npdm')\n"
            )
            (project / "tools" / "build_patches.py").write_text(
                "from pathlib import Path\nimport argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--kind')\nparser.add_argument('--nro')\nparser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "output = Path(args.output)\noutput.parent.mkdir(parents=True, exist_ok=True)\noutput.write_bytes(b'ips')\n"
            )

            fake_devkitpro = Path(temporary) / "devkitpro"
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text(
                "%.o:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
                "%.elf:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
                "%.nso:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
                "%.npdm:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
            )
            environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}

            clean = subprocess.run(
                ["make", "clean", "DIAGNOSTIC_STAGE=13"],
                cwd=copied_runtime,
                env=environment,
                text=True,
                capture_output=True,
            )
            build = subprocess.run(
                ["make", "-j2", "DIAGNOSTIC_STAGE=13"],
                cwd=copied_runtime,
                env=environment,
                text=True,
                capture_output=True,
            )

            output = copied_runtime / "deploy-diagnostic" / "stage13"
            manifest = output / "atmosphere/contents/010021C000B6A000/romfs/isaac_mods/manifest.json"
            npdm = output / "atmosphere/contents/010021C000B6A000/exefs/main.npdm"
            relay = output / "atmosphere/nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"

            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertTrue(manifest.is_file())
            self.assertTrue(npdm.is_file())
            self.assertTrue(relay.is_file())

    def test_stage13_generator_failure_does_not_publish_npdm_or_relay(self):
        with tempfile.TemporaryDirectory(prefix="isaac-stage13-generator-failure-") as temporary:
            project = Path(temporary) / "project"
            copied_runtime = project / "runtime"
            shutil.copytree(
                ROOT,
                copied_runtime,
                ignore=shutil.ignore_patterns("build*", "deploy", "deploy-diagnostic"),
            )
            shutil.copytree(ROOT.parent / "tools", project / "tools")
            (copied_runtime / "diagnostic/stage13/pc-mods/00 Runtime Require Probe/metadata.xml").unlink()

            fake_devkitpro = Path(temporary) / "devkitpro"
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text(
                "%.o:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
                "%.elf:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
                "%.nso:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
                "%.npdm:\n\t@mkdir -p $(dir $@)\n\t@touch $@\n"
            )
            environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}
            build = subprocess.run(
                ["make", "-j2", "DIAGNOSTIC_STAGE=13"],
                cwd=copied_runtime,
                env=environment,
                text=True,
                capture_output=True,
            )

            output = copied_runtime / "deploy-diagnostic" / "stage13"
            npdm = output / "atmosphere/contents/010021C000B6A000/exefs/main.npdm"
            relay = output / "atmosphere/nro_patches/isaac-repentance-manager-update-relay/91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips"
            self.assertNotEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertIn("metadata.xml is required", build.stdout + build.stderr)
            self.assertFalse(npdm.exists())
            self.assertFalse(relay.exists())

    def test_sequential_stage_dry_runs_use_distinct_build_state(self):
        with tempfile.TemporaryDirectory(prefix="isaac-diagnostic-build-") as temporary:
            copied_runtime = Path(temporary) / "runtime"
            shutil.copytree(
                ROOT,
                copied_runtime,
                ignore=shutil.ignore_patterns("build*", "deploy", "deploy-diagnostic"),
            )
            for stage in (0, 1):
                (copied_runtime / f"build-diagnostic-stage{stage}").mkdir()

            fake_devkitpro = Path(temporary) / "devkitpro"
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")
            environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}

            outputs = []
            for stage in (0, 1):
                result = subprocess.run(
                    ["make", "-n", f"DIAGNOSTIC_STAGE={stage}"],
                    cwd=copied_runtime,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                outputs.append(result.stdout)

        self.assertIn("build-diagnostic-stage0", outputs[0])
        self.assertNotIn("build-diagnostic-stage1", outputs[0])
        self.assertIn("build-diagnostic-stage1", outputs[1])
        self.assertNotIn("build-diagnostic-stage0", outputs[1])

        common_mk = (ROOT / "misc" / "mk" / "common.mk").read_text()
        post_build = (ROOT / "misc" / "scripts" / "post-build.sh").read_text()
        self.assertIn("BUILD\t\t:=\t$(EXL_BUILD_DIR)", common_mk)
        self.assertIn("$(TOPDIR)/$(EXL_ARTIFACT_DIR)/$(TARGET)", common_mk)
        self.assertIn('${EXL_ARTIFACT_DIR}/${NAME}.nso', post_build)

    def test_readme_documents_stage7_embedded_lua_hardware_validation(self):
        readme = (ROOT / "README.md").read_text()
        for token in (
            "runtime_test.lua",
            "不从 SD 卡读取",
            "ISAAC_LP",
            "0x0000000700000078",
            "ISAAC_LF",
            "runtime/deploy-diagnostic/stage7/atmosphere/",
            "runtime/deploy/atmosphere/",
        ):
            with self.subTest(token=token):
                self.assertIn(token, readme)


class StartupDiagnosticEntryTest(unittest.TestCase):
    def test_diagnostic_entry_has_exact_boundaries(self):
        source = (SOURCE / "diagnostic_entry.cpp").read_text()

        self.assertIn("#if defined(EXL_DIAGNOSTIC_STAGE)", source)
        self.assertIn("svcBreak(BreakReason_User", source)
        self.assertIn("svcExitProcess()", source)
        self.assertIn("InitMemLayout()", source)
        self.assertIn("virtmemSetup()", source)
        self.assertIn("exl::hook::Initialize()", source)
        self.assertIn("#if EXL_DIAGNOSTIC_STAGE >= 1", source)
        self.assertIn("#if EXL_DIAGNOSTIC_STAGE >= 2", source)
        self.assertIn("#if EXL_DIAGNOSTIC_STAGE >= 3", source)
        self.assertIn('extern "C" void exl_main(void*, void*)', source)
        self.assertIn('extern "C" NORETURN void exl_exception_entry()', source)
        self.assertNotIn("ModuleWorker", source)
        self.assertNotIn("ProbeLogger", source)
        self.assertNotIn("ScanTargetModule", source)
        self.assertNotIn("TryInstallManagerUpdateHook", source)

    def test_stage4_reports_filesystem_stage_and_raw_result(self):
        source = (SOURCE / "diagnostic_entry.cpp").read_text()
        header = (SOURCE / "fs_ipc.h").read_text()

        self.assertIn("RuntimeFsLogDiagnose", header)
        self.assertIn("RuntimeFsDiagnosticResult", header)
        self.assertIn("kDiagnosticFsMagic", source)
        self.assertIn("RuntimeFsLogDiagnose(kLogDirectory, kLogPath)", source)
        self.assertIn("diagnostic.stage", source)
        self.assertIn("diagnostic.result", source)
        self.assertIn("static_cast<u64>(diagnostic.stage) << 32", source)
        self.assertIn("#if EXL_DIAGNOSTIC_STAGE == 4", source)

    def test_stage5_runs_filesystem_diagnostic_from_a_delayed_worker(self):
        source = (SOURCE / "diagnostic_entry.cpp").read_text()

        self.assertIn("#if EXL_DIAGNOSTIC_STAGE == 5", source)
        self.assertIn("FsDiagnosticWorker", source)
        self.assertIn("svcCreateThread", source)
        self.assertIn("svcStartThread", source)
        self.assertIn("svcSleepThread(12'000'000'000)", source)
        self.assertIn("RuntimeFsLogDiagnose(kLogDirectory, kLogPath)", source)
        self.assertIn("svcCloseHandle", source)

    def test_stage6_through_stage12_use_runtime_entry_instead_of_minimal_diagnostic_entry(self):
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text()
        runtime = (SOURCE / "runtime_entry.cpp").read_text()
        self.assertIn(
            "EXL_DIAGNOSTIC_STAGE != 6 && EXL_DIAGNOSTIC_STAGE != 7 && EXL_DIAGNOSTIC_STAGE != 8 && EXL_DIAGNOSTIC_STAGE != 9 && EXL_DIAGNOSTIC_STAGE != 11 && EXL_DIAGNOSTIC_STAGE != 12",
            selector,
        )
        self.assertIn("../runtime_entry.cpp", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 6", runtime)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 7", runtime)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 8", runtime)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 9", runtime)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 11", runtime)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 12", runtime)
        self.assertIn("InitMemLayout();", runtime)
        self.assertIn("virtmemSetup();", runtime)

    def test_stage6_callback_reports_after_original_manager_update(self):
        source = (SOURCE / "hook_manager.cpp").read_text()
        callback = source[source.index("static void Callback"):source.index("};", source.index("static void Callback"))]
        stage6 = callback[
            callback.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6"):
            callback.index("#endif", callback.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6"))
        ]
        self.assertIn("ReportStage6IsPausedSuccess", callback)
        self.assertIn("GameIsPausedObservation::PausedFalse", callback)
        self.assertIn("GameIsPausedObservation::PausedTrue", callback)
        self.assertIn("svcBreak(BreakReason_User", callback)
        self.assertIn("svcExitProcess()", callback)
        self.assertLess(callback.index("Orig(self)"), callback.index("svcBreak(BreakReason_User"))
        for status in (39, 40, 41, 42):
            with self.subTest(current_callback_status=status):
                self.assertIn(f"ReportStage6Failure({status});", stage6)
        for status in range(19, 37):
            with self.subTest(historical_callback_status=status):
                self.assertNotIn(f"ReportStage6Failure({status});", stage6)

    def test_stage6_reports_each_controlled_hook_setup_failure(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        worker = source[source.index("void ModuleWorker"):source.index('extern "C" void exl_main')]
        entry_start = source.index('extern "C" void exl_main')
        entry = source[entry_start:source.index('extern "C" NORETURN', entry_start)]

        self.assertIn("kStage6FailureMagic = 0x49534141435F4946ULL", source)
        self.assertIn("NORETURN void ReportStage6Failure(u32 status)", source)
        self.assertIn("(6ULL << 32) | status", source)
        self.assertIn("svcBreak(BreakReason_User, kStage6FailureMagic", source)
        self.assertIn("svcExitProcess()", source)
        startup_failure = worker[worker.index("if (startup != StartupStatus::TitleOk)"):worker.index("for (u32 attempt")]
        build_mismatch = worker[worker.index("if (scan.status == TargetModuleScanStatus::BuildMismatch"):worker.index("if (scan.status == TargetModuleScanStatus::Found")]
        hook_failure = worker[worker.index("if (scan.status == TargetModuleScanStatus::Found"):worker.index("if (attempt + 1 < kTargetModuleScanAttemptLimit)")]
        timeout = worker[worker.rindex("SetRuntimeState({.fatalFailure = true});"):]
        title_failure = entry[entry.index("if (R_FAILED(svcGetInfo"):entry.index("const Result createResult")]
        create_failure = entry[entry.index("if (R_FAILED(createResult))"):entry.index("const Result startResult")]
        start_failure = entry[entry.index("if (R_FAILED(startResult))"):]

        self.assertIn("ReportStage6Failure(1);", startup_failure)
        self.assertIn("ReportStage6Failure(3);", build_mismatch)
        self.assertRegex(hook_failure, r"if \(install == HookInstallResult::InstructionMismatch\)\s*\{\s*ReportStage6Failure\(4\);")
        self.assertIn("ReportStage6Failure(2);", timeout)
        self.assertEqual(title_failure.count("ReportStage6Failure(1);"), 2)
        self.assertIn("ReportStage6Failure(7);", create_failure)
        self.assertIn("ReportStage6Failure(7);", start_failure)

    def test_stage6_reports_relay_install_failures(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text()
        manager = (SOURCE / "hook_manager.cpp").read_text()
        header = (SOURCE / "hook_manager.hpp").read_text()
        worker = entry[entry.index("void ModuleWorker"):entry.index('extern "C" void exl_main')]
        hook_failure = worker[worker.index("if (scan.status == TargetModuleScanStatus::Found"):worker.index("if (attempt + 1 < kTargetModuleScanAttemptLimit)")]

        mapping = (
            ("RelayPatchMismatch", "15"),
            ("RelaySlotNotEmpty", "16"),
            ("RelayPublishFailed", "17"),
        )
        for result, status in mapping:
            with self.subTest(result=result):
                self.assertIn(result, header)
                self.assertIn(f"HookInstallResult::{result}", manager)
                self.assertRegex(hook_failure, rf"case HookInstallResult::{result}:\s*ReportStage6Failure\({status}\);")
        for result, status in (
            ("GameOwnerSlotMismatch", "37"),
            ("GameOwnerSlotPublishFailed", "38"),
        ):
            with self.subTest(result=result):
                self.assertIn(result, header)
                self.assertIn(f"HookInstallResult::{result}", manager)
                self.assertRegex(hook_failure, rf"case HookInstallResult::{result}:\s*ReportStage6Failure\({status}\);")
        for status in range(19, 37):
            self.assertNotIn(f"ReportStage6Failure({status});", hook_failure)
        self.assertNotIn("ReportStage6Failure(9);", hook_failure)

    def test_exception_reports_are_distinct_from_success_reports(self):
        source = (SOURCE / "diagnostic_entry.cpp").read_text()
        success_magic = re.search(
            r"kDiagnosticSuccessMagic\s*=\s*(0x[0-9A-Fa-f]+)", source
        )
        exception_magic = re.search(
            r"kDiagnosticExceptionMagic\s*=\s*(0x[0-9A-Fa-f]+)", source
        )
        self.assertIsNotNone(success_magic)
        self.assertIsNotNone(exception_magic)
        self.assertNotEqual(success_magic.group(1), exception_magic.group(1))

        exception_body = re.search(
            r'extern "C" NORETURN void exl_exception_entry\(\)\s*\{(?P<body>.*?)\n\}',
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(exception_body)
        self.assertIn("kDiagnosticExceptionMagic", exception_body.group("body"))
        self.assertNotIn("kDiagnosticSuccessMagic", exception_body.group("body"))
        self.assertNotIn("FinishStage", exception_body.group("body"))

    def test_runtime_and_default_init_are_excluded_from_diagnostic_build(self):
        runtime_entry = (SOURCE / "runtime_entry.cpp").read_text()
        entry_selector = (SOURCE / "program" / "runtime_entry.cpp").read_text()
        init = (SOURCE / "lib" / "init" / "init.cpp").read_text()

        self.assertIn("#if !defined(EXL_DIAGNOSTIC_STAGE)", runtime_entry)
        self.assertIn('extern "C" void exl_main(void*, void*)', runtime_entry)
        self.assertIn("ModuleWorker", runtime_entry)
        self.assertIn("#if defined(EXL_DIAGNOSTIC_STAGE)", entry_selector)
        self.assertIn('"../diagnostic_entry.cpp"', entry_selector)
        self.assertIn('"../runtime_entry.cpp"', entry_selector)
        self.assertIn("#if !defined(EXL_DIAGNOSTIC_STAGE)", init)
        self.assertIn("InitPatcherImpl()", init)
        self.assertIn("exl::reloc::impl::Initialize()", init)
        self.assertIn("extern \"C\" void exl_init()", init)

    def test_diagnostic_startup_skips_all_initializers_before_exl_main(self):
        init = (SOURCE / "lib" / "init" / "init.cpp").read_text()
        for function in ("exl_module_init", "exl_entrypoint_init"):
            match = re.search(
                rf"void {function}\([^)]*\)\s*\{{(?P<body>.*?)\n    \}}",
                init,
                re.DOTALL,
            )
            self.assertIsNotNone(match)
            body = match.group("body")
            guard = "#if !defined(EXL_DIAGNOSTIC_STAGE)"
            self.assertIn(guard, body)
            self.assertLess(body.index(guard), body.index("__init_array"))
            self.assertLess(body.index("__init_array"), body.rindex("#endif"))
            self.assertGreater(body.index("exl_main"), body.rindex("#endif"))

    def test_stage3_initializes_only_the_fake_heap_before_exl_main(self):
        init = (SOURCE / "lib" / "init" / "init.cpp").read_text()
        for function in ("exl_module_init", "exl_entrypoint_init"):
            with self.subTest(function=function):
                match = re.search(
                    rf"void {function}\([^)]*\)\s*\{{(?P<body>.*?)\n    \}}",
                    init,
                    re.DOTALL,
                )
                self.assertIsNotNone(match)
                body = match.group("body")
                heap_guard = "#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE >= 3"
                default_guard = "#if !defined(EXL_DIAGNOSTIC_STAGE)\n        exl_init();"
                self.assertIn(heap_guard, body)
                self.assertIn("__init_heap();", body)
                self.assertLess(body.index(heap_guard), body.index("__init_heap();"))
                self.assertLess(body.index("__init_heap();"), body.index(default_guard))
                self.assertLess(body.index(default_guard), body.index("exl_init();"))
                self.assertLess(body.index("exl_init();"), body.index("__init_array();"))

    def test_stage3_explicitly_initializes_only_the_required_jit_objects(self):
        jit = (SOURCE / "lib" / "util" / "sys" / "jit.hpp").read_text()
        hook = (SOURCE / "lib" / "hook" / "nx64" / "hook_impl.cpp").read_text()
        inline = (SOURCE / "lib" / "hook" / "nx64" / "inline_impl.cpp").read_text()

        self.assertIn("constinit exl::util::Jit<size> name", jit)
        self.assertIn("void Initialize(std::span<const u8, Size> rx)", jit)
        self.assertIn("s_HookJit.Initialize(std::span {impl::s_HookJit::s_Area})", hook)
        self.assertIn(
            "s_InlineHookJit.Initialize(std::span {impl::s_InlineHookJit::s_Area})",
            inline,
        )


class StartupDiagnosticDocumentationTest(unittest.TestCase):
    def test_readme_documents_stage35_game_owner_chain_protocol(self):
        readme = (ROOT / "README.md").read_text()
        for token in (
            "0xAAC698", "global -> owner -> Game*", "状态 37", "状态 38",
            "状态 39", "状态 40", "状态 41", "状态 42",
            "不调用 `Game::IsPaused()`", "不保存", "Manager Update relay IPS",
        ):
            self.assertIn(token, readme)

        historical_relays = (
            "game-observer-relay",
            "game-update-observer-relay",
            "game-state2-observer-relay",
            "game-ispaused-render-observer-relay",
        )
        stage6 = readme[readme.index("## Stage 6"):readme.index("## Stage 7")]
        current_payload = readme[
            readme.index("随后只从当前阶段"):readme.index("按阶段 0、1、2、3")
        ]
        historical_cleanup = readme[
            readme.index("下列删除命令仅可用于部署旧历史诊断阶段"):
            readme.index("随后只从当前阶段")
        ]
        for historical_relay in historical_relays:
            self.assertNotIn(historical_relay, stage6)
            self.assertNotIn(historical_relay, current_payload)
            self.assertIn(
                f"rm -rf atmosphere/nro_patches/isaac-repentance-{historical_relay}",
                historical_cleanup,
            )

    def test_stage35_status40_documents_any_unsafe_owner_chain_read(self):
        expected_semantics = (
            "owner 链无法安全读取（包括运行期 owner 槽地址无效或不可读，以及已观察到非空 owner 后"
            "地址未对齐或字段映射不可读）"
        )
        documents = (
            ROOT / "README.md",
            ROOT.parent / "docs" / "superpowers" / "specs" /
            "2026-08-26-stage35-game-owner-chain-observer-design.md",
            ROOT.parent / "docs" / "superpowers" / "plans" /
            "2026-08-26-stage35-game-owner-chain-observer-implementation.md",
            ROOT.parent / "docs" / "问题与解决记录.md",
            ROOT.parent / "docs" / "会话交接-2026-08-26.md",
        )
        for document in documents:
            with self.subTest(document=document):
                self.assertIn(expected_semantics, document.read_text(encoding="utf-8"))
        issues = (ROOT.parent / "docs" / "问题与解决记录.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m unittest discover -s tests -q`：118 项", issues)
        self.assertIn("python3 -m unittest discover -s runtime/tests -q`：213 项，跳过 2 项", issues)

    def test_stage35_hardware_and_default_smoke_success_are_documented(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        issues = (ROOT.parent / "docs" / "问题与解决记录.md").read_text(encoding="utf-8")
        handoff = (ROOT.parent / "docs" / "会话交接-2026-08-26.md").read_text(encoding="utf-8")
        plan = (
            ROOT.parent / "docs" / "superpowers" / "plans" /
            "2026-08-26-stage35-game-owner-chain-observer-implementation.md"
        ).read_text(encoding="utf-8")
        for document in (readme, issues, handoff, plan):
            self.assertIn("01787760316_010021c000b6a000.log", document)
            self.assertIn("A6D03340A1D7B205CC14B4FAE7315DE7A7CB5F64", document)
            self.assertIn("91C73FDD575061318D68886316AFEAC72388B2AB", document)
            self.assertIn("ISAAC_HP", document)
            self.assertIn("0x0000000600000001", document)
            self.assertIn("MainThread", document)
            self.assertIn("默认包 smoke test 通过（用户确认游戏正常进入并持续运行，无报错）", document)
        self.assertIn("Stage 6：Game owner 链观察（真机验证通过）", readme)
        self.assertIn("Stage 35：只观察 owner 链（真机验证通过）", handoff)
        self.assertIn("Stage 36：同步读取 IsPaused（真机验证通过）", handoff)

    def test_stage36_native_ispaused_protocol_is_documented(self):
        documents = (
            ROOT / "README.md",
            ROOT.parent / "docs" / "问题与解决记录.md",
            ROOT.parent / "docs" / "会话交接-2026-08-26.md",
        )
        for document_path in documents:
            document = document_path.read_text(encoding="utf-8")
            for token in (
                "Stage 36",
                "0x671100",
                "ISAAC_IP",
                "ISAAC_IF",
                "状态 43",
                "状态 44",
                "(6,1)",
                "(6,2)",
                "不缓存",
                "不接入 Lua",
                "Manager Update relay IPS",
            ):
                with self.subTest(document=document_path, token=token):
                    self.assertIn(token, document)

        handoff = documents[2].read_text(encoding="utf-8")
        problem_log = documents[1].read_text(encoding="utf-8")
        self.assertIn("d1411f6 docs: 记录 Stage 36 本地验收", handoff)
        self.assertIn("当前下一步是恢复默认三文件树并执行正常启动 smoke test", handoff)
        self.assertNotIn("下一步完成 Stage 36 全量本地验收", handoff)
        self.assertIn("Stage 36 交接状态与本地验收记录不一致", problem_log)

    def test_stage36_hardware_and_default_smoke_success_are_documented(self):
        documents = (
            ROOT / "README.md",
            ROOT.parent / "docs" / "问题与解决记录.md",
            ROOT.parent / "docs" / "会话交接-2026-08-26.md",
            ROOT.parent / "docs" / "superpowers" / "plans" /
            "2026-08-27-stage36-game-ispaused-native-call-implementation.md",
        )
        for document_path in documents:
            document = document_path.read_text(encoding="utf-8")
            for token in (
                "01787765092_010021c000b6a000.log",
                "3264FDDF5191F7E0B1BCBA8F39F8C0BE1E4E583A",
                "91C73FDD575061318D68886316AFEAC72388B2AB",
                "MainThread",
                "ISAAC_IP",
                "0x0000000600000002",
                "返回 true",
                "Stage 36 默认包 smoke test 通过（用户确认游戏正常进入并持续运行，无报错）",
            ):
                with self.subTest(document=document_path, token=token):
                    self.assertIn(token, document)

        readme = documents[0].read_text(encoding="utf-8")
        handoff = documents[2].read_text(encoding="utf-8")
        self.assertIn("Stage 36：Game::IsPaused 原生同步调用（真机验证通过）", readme)
        self.assertIn("Stage 36：同步读取 IsPaused（真机验证通过）", handoff)
        self.assertNotIn("Stage 36：Game::IsPaused 原生同步调用（本地实现完成，待真机验证）", readme)
        self.assertNotIn("Stage 36：同步读取 IsPaused（本地实现完成，待真机验证）", handoff)
        for document_path in documents:
            self.assertNotIn(
                "默认包 smoke test 待确认",
                document_path.read_text(encoding="utf-8"),
            )

    def test_readme_documents_stage13_manifest_require_validation(self):
        readme = (ROOT / "README.md").read_text()
        stage13 = readme[readme.index("## Stage 13"):readme.index("## 默认运行与诊断")]
        for token in (
            "Stage 13",
            "DIAGNOSTIC_STAGE=13",
            "runtime/deploy-diagnostic/stage13/atmosphere/",
            "ISAACRQP",
            "0x0000000D00000078",
            "ISAACRQF",
            "Atmosphere 报告",
            "runtime/deploy/atmosphere/",
            "第三方 Mod",
            "游戏 API",
            "isaac-stage13-backup-",
            'rm -rf "$stage13_romfs"',
            'ditto runtime/deploy-diagnostic/stage13/atmosphere "$sd_root/atmosphere"',
            'ditto "$sd_root/atmosphere/crash_reports" "$report_dir"',
            'ditto runtime/deploy/atmosphere "$sd_root/atmosphere"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, stage13)
        self.assertNotIn('rm -rf "$title_root"', stage13)
        self.assertNotIn('rm -rf "$sd_root/atmosphere"', stage13)

    def test_readme_documents_stage12_romfs_external_lua_hardware_validation(self):
        readme = (ROOT / "README.md").read_text()
        for token in (
            "DIAGNOSTIC_STAGE=12", "runtime/deploy-diagnostic/stage12/atmosphere/",
            "rom:/runtime_probe.lua", "4096", "ISAACELP", "0x0000000C00000078",
            "ISAACELF", "runtime/deploy/atmosphere/", "isaac-stage12-backup-",
            "backup_root", "mkdir -p \"$backup_root/contents\"", "ditto",
            "runtime_probe.lua",
        ):
            with self.subTest(token=token):
                self.assertIn(token, readme)

        self.assertNotIn(
            "Stage 12：受限 RomFS 外部 Lua 验证（待真机验证）\n\n"
            "完全退出游戏后，删除旧的 Title 覆盖",
            readme,
        )

    def test_readme_marks_stage7_as_hardware_validated(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("内嵌 Lua Stage 7 均已完成真机验证", readme)
        self.assertNotIn("内嵌 Lua stage 7 尚待真机验证", readme)

    def test_readme_documents_stage11_romfs_sentinel_hardware_validation(self):
        readme = (ROOT / "README.md").read_text()
        for token in (
            "DIAGNOSTIC_STAGE=11", "isaac_mod_probe.lua", "ISAACFOK",
            "ISAACFFL", "0x0000000B00000012", "romfs",
            "不执行 Lua", "恢复默认", "91C73FDD575061318D68886316AFEAC72388B2AB",
            "eddd22720685263ccf13839ecb77a8b737560553335119374a91cb9bfcf0832f",
        ):
            with self.subTest(token=token):
                self.assertIn(token, readme)
        self.assertRegex(readme, r"\b[0-9a-f]{64}  exefs/subsdk9\b")

    def test_readme_documents_staged_hardware_test(self):
        readme = (ROOT / "README.md").read_text()
        for stage, initializer in (
            (0, "svcBreak"),
            (1, "InitMemLayout()"),
            (2, "virtmemSetup()"),
            (3, "exl::hook::Initialize()"),
            (4, "RuntimeFsLogDiagnose"),
            (5, "12 秒"),
            (6, "Manager::Update"),
        ):
            with self.subTest(stage=stage):
                self.assertIn(f"DIAGNOSTIC_STAGE={stage}", readme)
                self.assertIn(f"deploy-diagnostic/stage{stage}", readme)
                self.assertIn(initializer, readme)

        self.assertIn("for stage in 0 1 2 3 4 5 6", readme)
        self.assertIn("docker run --rm", readme)
        self.assertIn("rm -rf atmosphere/contents/010021C000B6A000", readme)
        self.assertIn("只从当前阶段", readme)
        self.assertIn("main.npdm", readme)
        self.assertIn("subsdk9", readme)
        self.assertIn("应用（subsdk9）报告成功", readme)
        self.assertIn("然后推进下一阶段", readme)
        self.assertIn("整机冻结", readme)
        self.assertIn("不要继续下一阶段", readme)
        self.assertIn("0x49534141435F4449", readme)
        self.assertIn("0x49534141435F4558", readme)
        self.assertIn("正常到达阶段边界", readme)
        self.assertIn("初始化异常", readme)
        self.assertIn("0x49534141435F4850", readme)
        self.assertIn("0x0000000600000001", readme)
        self.assertIn("原函数已返回", readme)
        self.assertIn("DIAGNOSTIC_STAGE=8", readme)
        self.assertIn("deploy-diagnostic/stage8", readme)
        self.assertIn("Manager::LoadConfigs", readme)
        self.assertIn("0x49534141434D4F44", readme)
        self.assertIn("0x0000000800036800", readme)
        self.assertIn("isaac-repentance-manager-loadconfigs-relay", readme)


class StartupDiagnosticArtifactTest(unittest.TestCase):
    def assert_exact_payload(self, exefs):
        self.assertEqual(
            sorted(path.name for path in exefs.iterdir() if not path.name.startswith(".")),
            ["main.npdm", "subsdk9"],
        )

    def test_payload_ignores_hidden_metadata_but_rejects_extra_regular_files(self):
        with tempfile.TemporaryDirectory(prefix="diagnostic-payload-") as temporary:
            exefs = Path(temporary)
            for name in ("main.npdm", "subsdk9", ".DS_Store", "._subsdk9"):
                (exefs / name).touch()
            self.assert_exact_payload(exefs)

            (exefs / "unexpected.bin").touch()
            with self.assertRaises(AssertionError):
                self.assert_exact_payload(exefs)

    def test_stage3_jit_objects_require_explicit_initialization(self):
        jit = (SOURCE / "lib" / "util" / "sys" / "jit.hpp").read_text()
        hook = (SOURCE / "lib" / "hook" / "nx64" / "hook_impl.cpp").read_text()
        inline = (SOURCE / "lib" / "hook" / "nx64" / "inline_impl.cpp").read_text()
        self.assertIn("constinit exl::util::Jit<size> name", jit)
        self.assertIn("s_HookJit.Initialize", hook)
        self.assertIn("s_InlineHookJit.Initialize", inline)

    def test_makefile_defines_isolated_diagnostic_payload_contracts(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("OUT := deploy-diagnostic/stage$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("SD_OUT := atmosphere/contents/$(PROGRAM_ID)/exefs", makefile)
        self.assertIn("DEPLOY_BINARY = $(OUT)/$(SD_OUT)/$(BINARY_NAME)", makefile)
        self.assertIn("all: $(DEPLOY_NPDM)\nifneq ($(filter 102 104 108 109 110 112 127 128,$(DIAGNOSTIC_STAGE)),)\nelse\nall: $(DEPLOY_RELAY_IPS)\nendif", makefile)
        self.assertIn("ifneq ($(filter 8 9,$(DIAGNOSTIC_STAGE)),)", makefile)
        self.assertIn("all: $(DEPLOY_LOAD_CONFIGS_RELAY_IPS)", makefile)

    def test_makefile_adds_loadconfigs_relay_for_stage8_and_stage9(self):
        makefile = (ROOT / "Makefile").read_text()

        self.assertIn("DEPLOY_LOAD_CONFIGS_RELAY_IPS", makefile)
        self.assertIn("isaac-repentance-manager-loadconfigs-relay", makefile)
        self.assertIn("--kind loadconfigs-relay", makefile)
        self.assertIn("ifneq ($(filter 8 9,$(DIAGNOSTIC_STAGE)),)", makefile)


if __name__ == "__main__":
    unittest.main()
