import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class TestRunObserverTests(unittest.TestCase):
    def test_snapshot_capture_never_accepts_mixed_state_and_detail(self):
        harness = textwrap.dedent(
            r"""
            #include "test_run_observer.hpp"
            #include <atomic>
            #include <cstdint>
            #include <thread>

            int main() {
                std::atomic<std::uint32_t> done{0};
                auto write = [&] {
                    for (std::uint32_t value = 1; value <= 200000; ++value) {
                        const std::uint32_t state = value % 10 + 1;
                        TestRunObserver::Mark(static_cast<TestRunObserver::State>(state), state);
                    }
                    done.fetch_add(1, std::memory_order_release);
                };
                std::thread writer1(write);
                std::thread writer2(write);
                std::uint32_t captures = 0;
                while (done.load(std::memory_order_acquire) != 2) {
                    TestRunSnapshot snapshot{};
                    if (IsaacModRuntime_GetTestRunSnapshot(&snapshot, sizeof(snapshot)) ==
                            kTestRunSnapshotMagic) {
                        ++captures;
                        if (snapshot.state != snapshot.detail) return 1;
                    }
                }
                writer1.join();
                writer2.join();
                return captures == 0 ? 2 : 0;
            }
            """
        )
        with tempfile.TemporaryDirectory(prefix="test-run-observer-") as temporary:
            executable = Path(temporary) / "observer-test"
            source = Path(temporary) / "observer-test.cpp"
            source.write_text(harness, encoding="utf-8")
            compile_result = subprocess.run(
                ["c++", "-std=c++20", "-pthread", "-DEXL_TEST_BUILD_ID=202609090801",
                 "-I", str(SOURCE), "-I", str(ROOT / "src"),
                 str(SOURCE / "test_run_observer.cpp"), str(source),
                 "-o", str(executable)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(compile_result.returncode, 0,
                             compile_result.stdout + compile_result.stderr)
            run_result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)

    def test_runtime_exports_a_nonterminating_versioned_snapshot(self):
        header = SOURCE / "test_run_observer.hpp"
        implementation = SOURCE / "test_run_observer.cpp"

        self.assertTrue(header.is_file(), "test-run observer header is missing")
        self.assertTrue(implementation.is_file(), "test-run observer implementation is missing")
        declaration = header.read_text(encoding="utf-8")
        source = implementation.read_text(encoding="utf-8")
        protocol = (SOURCE / "test_run_observer_protocol.hpp").read_text(encoding="utf-8")

        for token in (
            "ISAACTR1",
            "kTestRunSnapshotVersion = 1",
            "TEST_BUILD_ID",
            "enum class State",
            "ExlMainEntered",
            "ModuleWorkerEntered",
            "ModuleScanEntered",
            "ModuleScanReturned",
            "ManifestInstallEntered",
            "ManifestInstallReturned",
            "LuaInitializeEntered",
            "LuaInitializeReturned",
            "FinalReportEntered",
            "TestRunKind",
            "kind",
            "IsaacModRuntime_GetTestRunSnapshot",
            '__attribute__((visibility("default")))',
            "static_assert(sizeof(TestRunSnapshot) == 48)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, declaration + source + protocol)

        self.assertIn("std::memory_order_seq_cst", source)
        self.assertNotIn("std::memory_order_relaxed", source)
        self.assertIn("std::atomic<std::uint64_t> g_stateDetail", source)
        self.assertNotIn("g_state{", source)
        self.assertNotIn("g_detail{", source)
        self.assertNotIn("svcBreak", source)
        self.assertNotIn("fopen", source)
        self.assertNotIn("svcSleepThread", source)

    def test_stage8_marks_every_potentially_blocking_call_before_and_after(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hooks = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        worker = entry[
            entry.index("void ModuleWorker(void*)") : entry.index('extern "C" void exl_main')
        ]

        for before, call, after in (
            ("ModuleScanEntered", "ScanTargetModule()", "ModuleScanReturned"),
            ("ManifestInstallEntered", "TryInstallDefaultManifestMod(*scan.module)",
             "ManifestInstallReturned"),
        ):
            with self.subTest(call=call):
                self.assertLess(worker.index(before), worker.index(call))
                self.assertLess(worker.index(call), worker.index(after))
        self.assertLess(worker.index("ManifestInstallReturned"),
                        worker.index("AllowDefaultManifestInitialization();"))

        # `InitializeDefaultManifestMod` now delegates to the manifest service
        # path, so the marks and the Lua entry call are pinned there.
        manifest_initializer = hooks[
            hooks.index("bool LoadDefaultManifestModThroughService") :
            hooks.index("bool PrimeDefaultCallbacks")
        ]
        self.assertLess(manifest_initializer.index("isaac::runtime::ModLoadService service{"),
                        manifest_initializer.index("LuaInitializeEntered"))
        self.assertLess(manifest_initializer.index("LuaInitializeEntered"),
                        manifest_initializer.index("LuaInitializeReturned"))

        callback = hooks[
            hooks.index("if (DefaultManifestInitializationAllowed())") :
            hooks.index("if (LuaRuntime::IsReady())")
        ]
        self.assertLess(callback.index("DefaultManifestInitializationAllowed()"),
                        callback.index("DefaultManifestState expected"))
        self.assertLess(callback.index("ManifestInstallEntered"),
                        callback.index("InitializeDefaultManifestMod(&failureDetail)"))
        self.assertLess(callback.index("InitializeDefaultManifestMod(&failureDetail)"),
                        callback.index("ManifestInstallReturned"))

        main = entry[entry.index('extern "C" void exl_main') :]
        self.assertLess(main.index("TestRunObserver::State::ExlMainEntered"),
                        main.index("exl::hook::Initialize();"))
        self.assertLess(worker.index("TestRunObserver::State::ModuleWorkerEntered"),
                        worker.index("const StartupStatus startup"))
        self.assertIn("TestRunObserver::State::FinalReportEntered", entry)
