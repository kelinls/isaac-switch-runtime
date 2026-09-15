import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class StartupGateProbeTests(unittest.TestCase):
    def test_makefile_accepts_isolated_startup_probe_stages_1_through_8(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("STARTUP_PROBE_STAGE ?=", makefile)
        self.assertIn("STARTUP_PROBE_LAYOUT ?=", makefile)
        self.assertIn("deploy-startup-probe/stage$(STARTUP_PROBE_STAGE)", makefile)
        self.assertIn("build-startup-probe-stage$(STARTUP_PROBE_STAGE)", makefile)
        self.assertIn("-DEXL_STARTUP_PROBE_STAGE=$(STARTUP_PROBE_STAGE)", makefile)

        with tempfile.TemporaryDirectory(prefix="fake-devkitpro-") as temporary:
            fake_devkitpro = Path(temporary)
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")
            environment = os.environ | {"DEVKITPRO": str(fake_devkitpro)}

            for stage in range(1, 9):
                result = subprocess.run(
                    ["make", "-n", "clean-runtime-output", f"STARTUP_PROBE_STAGE={stage}"],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"deploy-startup-probe/stage{stage}", result.stdout)
                if stage == 6:
                    self.assertIn("deploy-startup-probe/stage6-compact", result.stdout)

            for layout in ("compat", "compact"):
                result = subprocess.run(
                    ["make", "-n", "clean-runtime-output", "STARTUP_PROBE_STAGE=6",
                     f"STARTUP_PROBE_LAYOUT={layout}"],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"deploy-startup-probe/stage6-{layout}", result.stdout)

            for arguments in (
                ("STARTUP_PROBE_STAGE=0",),
                ("STARTUP_PROBE_STAGE=9",),
                ("STARTUP_PROBE_STAGE=6", "STARTUP_PROBE_LAYOUT=invalid"),
                ("STARTUP_PROBE_STAGE=5", "STARTUP_PROBE_LAYOUT=compact"),
                ("STARTUP_PROBE_STAGE=1", "DIAGNOSTIC_STAGE=2"),
            ):
                result = subprocess.run(
                    ["make", "-n", *arguments],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("STARTUP_PROBE", result.stdout + result.stderr)

    def test_probe_reports_each_module_initialization_boundary_before_runtime_work(self):
        init = (SOURCE / "lib/init/init.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")

        self.assertIn("kStartupProbeMagic", init)
        self.assertIn("ReportStartupProbe(1)", init)
        self.assertIn("ReportStartupProbe(2)", init)
        self.assertIn("ReportStartupProbe(3)", init)
        self.assertIn("ReportStartupProbe(4)", init)
        self.assertIn("RunStartupProbeExlMainGate()", entry)
        self.assertIn("ReportStartupProbe(5)", init)

    def test_stage6_reports_worker_dispatch_after_exl_main_preconditions(self):
        init = (SOURCE / "lib/init/init.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")

        self.assertIn("ReportStartupProbeWorkerGate", init)
        self.assertIn("kStartupProbeStage6RuntimeStateRecorded", entry)
        self.assertIn("kStartupProbeStage6HookInitialized", entry)
        self.assertIn("kStartupProbeStage6TitleOk", entry)
        self.assertIn("kStartupProbeStage6ThreadCreated", entry)
        self.assertIn("kStartupProbeStage6WorkerDispatched", entry)
        self.assertLess(entry.index("kStartupProbeStage6RuntimeStateRecorded"),
                        entry.index("exl::hook::Initialize();"))
        self.assertLess(entry.index("kStartupProbeStage6HookInitialized"),
                        entry.index("svcGetInfo"))
        self.assertLess(entry.index("kStartupProbeStage6TitleOk"),
                        entry.index("svcCreateThread"))
        self.assertIn("ReportStartupProbeWorkerGate(", entry)
        self.assertIn("void StartupProbeWorker(void*)", entry)
        self.assertIn("&StartupProbeWorker", entry)
        self.assertIn(
            "#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 6\n"
            "    void* workerEntry = reinterpret_cast<void*>(&StartupProbeWorker);\n"
            "#else\n"
            "    void* workerEntry = reinterpret_cast<void*>(&ModuleWorker);\n"
            "#endif",
            entry,
        )
        self.assertIn(
            "#if !defined(EXL_STARTUP_PROBE_STAGE) || EXL_STARTUP_PROBE_STAGE != 6\n"
            "void ModuleWorker(void*)",
            entry,
        )

    def test_stage6_compat_layout_keeps_the_verified_segment_boundaries(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        linker_script = (ROOT / "misc" / "link.ld").read_text(encoding="utf-8")
        compat_specs = (ROOT / "misc" / "specs" / "module_startup_probe_compat.specs")
        compat_linker_script = (ROOT / "misc" / "link_startup_probe_compat.ld")

        self.assertIn("module_startup_probe_compat.specs", makefile)
        self.assertIn("EXL_STARTUP_PROBE_COMPAT_LAYOUT", linker_script)
        self.assertIn("0x55000", linker_script)
        self.assertIn("0x76000", linker_script)
        self.assertTrue(compat_specs.is_file())
        self.assertTrue(compat_linker_script.is_file())
        self.assertIn("link_startup_probe_compat.ld", compat_specs.read_text(encoding="utf-8"))
        self.assertIn("EXL_STARTUP_PROBE_COMPAT_LAYOUT = 1", compat_linker_script.read_text(encoding="utf-8"))

    def test_stage7_reports_full_module_worker_dispatch_after_runtime_preconditions(self):
        init = (SOURCE / "lib/init/init.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")

        for token in (
            "kStartupProbeStage7RuntimeStateRecorded",
            "kStartupProbeStage7HookInitialized",
            "kStartupProbeStage7TitleOk",
            "kStartupProbeStage7ThreadCreated",
            "kStartupProbeStage7WorkerDispatched",
            "RunStartupProbeStage7WorkerGate",
            "ReportStartupProbeWorkerGate(",
        ):
            self.assertIn(token, entry)

        worker = entry[
            entry.index("void ModuleWorker(void*)") :
            entry.index('extern "C" void exl_main')
        ]
        self.assertLess(worker.index("kStartupProbeStage7WorkerDispatched"),
                        worker.index("ManagerUpdateHookAudit::WorkerState::Started"))

        main = entry[entry.index('extern "C" void exl_main') :]
        self.assertLess(main.index("kStartupProbeStage7RuntimeStateRecorded"),
                        main.index("exl::hook::Initialize();"))
        self.assertLess(main.index("kStartupProbeStage7HookInitialized"),
                        main.index("svcGetInfo"))
        self.assertLess(main.index("kStartupProbeStage7TitleOk"),
                        main.index("svcCreateThread"))
        self.assertLess(main.index("kStartupProbeStage7ThreadCreated"),
                        main.index("svcStartThread"))

        self.assertIn("#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 7", entry)
        self.assertIn("g_StartupProbeStage7Gates", entry)
        self.assertIn("RunStartupProbeStage7WorkerGate(", worker)
        self.assertIn("g_StartupProbeStage7Gates.load(std::memory_order_acquire)", worker)
        self.assertLess(worker.index("kStartupProbeStage7WorkerDispatched"),
                        worker.index("RunStartupProbeStage7WorkerGate("))
        self.assertLess(worker.index("RunStartupProbeStage7WorkerGate("),
                        worker.index("ManagerUpdateHookAudit::WorkerState::Started"))
        self.assertIn("RunStartupProbeStage7WorkerGate", init)
        self.assertIn("EXL_STARTUP_PROBE_STAGE == 7", init)

    def test_stage8_reports_complete_default_worker_boundaries_without_dispatch(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        init = (SOURCE / "lib/init/init.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hooks = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        self.assertIn("1 2 3 4 5 6 7 8", makefile)
        self.assertIn("EXL_STARTUP_PROBE_STAGE == 8", init)
        self.assertIn("RunStartupProbeStage8WorkerGate", init)

        for token in (
            "kStartupProbeStage8WorkerEntered",
            "kStartupProbeStage8TitleOk",
            "kStartupProbeStage8ScanStarted",
            "kStartupProbeStage8ModuleFound",
            "kStartupProbeStage8InstallSucceeded",
            "kStartupProbeStage8LuaInitialized",
            "ReportStartupProbeStage8Gates",
        ):
            self.assertIn(token, entry)

        worker = entry[
            entry.index("void ModuleWorker(void*)") :
            entry.index('extern "C" void exl_main')
        ]
        self.assertLess(worker.index("kStartupProbeStage8WorkerEntered"),
                        worker.index("ManagerUpdateHookAudit::WorkerState::Started"))
        self.assertLess(worker.index("kStartupProbeStage8ScanStarted"),
                        worker.index("for (u32 attempt"))
        self.assertLess(worker.index("kStartupProbeStage8ModuleFound"),
                        worker.index("TryInstallDefaultManifestMod(*scan.module)"))
        self.assertLess(worker.index("kStartupProbeStage8InstallSucceeded"),
                        worker.index("AllowDefaultManifestInitialization();"))
        self.assertIn("ReportStartupProbeStage8ManifestResult", entry)
        self.assertIn(
            "ReportStartupProbeStage8ManifestResult(initialized, failureDetail);",
            hooks,
        )
        reporter_start = entry.index(
            'extern "C" NORETURN void ReportStartupProbeStage8ManifestResult'
        )
        reporter = entry[
            reporter_start :
            entry.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6",
                        reporter_start)
        ]
        self.assertLess(reporter.index("kStartupProbeStage8InstallSucceeded"),
                        reporter.index("if (initialized)"))

    def test_stage8_keeps_the_title_success_block_outside_preprocessor_guards(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")

        title_success = entry.index(
            "MarkStartupProbeStage6Gate(kStartupProbeStage6TitleOk);"
        )
        worker_entry = entry.index(
            "void* workerEntry = reinterpret_cast<void*>(&StartupProbeWorker);"
        )
        title_tail = entry[title_success:worker_entry]
        self.assertIn(
            "#endif\n}\n#if defined(EXL_STARTUP_PROBE_STAGE)",
            title_tail,
        )

    def test_stage8_closes_the_scan_exhaustion_terminal_branch(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        worker = entry[
            entry.index("void ModuleWorker(void*)") :
            entry.index('extern "C" void exl_main')
        ]

        scan_exhaustion = worker[worker.index("SetRuntimeState({.fatalFailure = true});"):]
        self.assertIn(
            "#if defined(EXL_STARTUP_PROBE_STAGE) && EXL_STARTUP_PROBE_STAGE == 8\n"
            "    ReportStartupProbeStage8Gates();\n"
            "#else",
            scan_exhaustion,
        )
        self.assertIn("#endif\n#endif\n    svcExitThread();", scan_exhaustion)

    def test_startup_probe_package_includes_the_observer_plugin_and_build_manifest(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        for token in (
            "ifndef TEST_BUILD_ID",
            "TEST_BUILD_ID := $(shell date -u +%Y%m%d%H%M%S)",
            "EXL_TEST_BUILD_ID=$(TEST_BUILD_ID)",
            "deploy-saltynx/stage145/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf",
            "$(OUT)/SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf",
            "test-build-manifest.json",
        ):
            with self.subTest(token=token):
                self.assertIn(token, makefile)

    def test_test_package_manifest_records_build_stage_and_file_hashes(self):
        script = ROOT.parent / "tools" / "write_test_package_manifest.py"
        self.assertTrue(script.is_file(), "test package manifest writer is missing")
        with tempfile.TemporaryDirectory(prefix="test-package-") as temporary:
            package = Path(temporary)
            artifact = package / "atmosphere" / "subsdk9"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"runtime")
            result = subprocess.run(
                ["python3", str(script), "--root", str(package), "--build-id", "202609090801",
                 "--stage", "8"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((package / "test-build-manifest.json").read_text())
            self.assertEqual(manifest["protocol"], 2)
            self.assertEqual(manifest["mode"], "startup-probe")
            self.assertEqual(manifest["build_id"], 202609090801)
            self.assertEqual(manifest["stage"], 8)
            self.assertEqual(manifest["files"][0]["path"], "atmosphere/subsdk9")
            self.assertEqual(manifest["files"][0]["size"], len(b"runtime"))
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)

            for invalid_build_id in ("0", str(1 << 64)):
                invalid = subprocess.run(
                    ["python3", str(script), "--root", str(package),
                     "--build-id", invalid_build_id, "--stage", "8"],
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(invalid.returncode, 0)
