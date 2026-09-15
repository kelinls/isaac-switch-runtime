import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class ModPersistenceProbeTraceTests(unittest.TestCase):
    def test_stage145_event_diagnostic_has_versioned_registration_records_and_build_target(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        plugin = (SOURCE / "program" / "saltynx_external_plugin_persistence_bridge_trace.cpp").read_text(encoding="utf-8")

        for token in (
            "mod-persistence-event-diagnostic-deploy",
            "PERSISTENCE_EVENT_DIAGNOSTIC=1",
            "TEST_BUILD_ID=${ISAAC_TEST_BUILD_ID}",
            "isaac-runtime-registration-events.bin",
            "ISAACRG1",
            "SymbolsResolved",
            "RegisterReturned",
            'open(kRegistrationPath, "ab")',
        ):
            with self.subTest(token=token):
                self.assertIn(token, makefile + plugin)
        target_start = makefile.index("mod-persistence-event-diagnostic-deploy")
        target_end = makefile.find("\n\n", target_start)
        self.assertNotIn("STARTUP_PROBE_STAGE", makefile[target_start:target_end])
        diagnostic = plugin[plugin.index("#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)"):plugin.index("#else", plugin.index("#if defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)"))]
        self.assertNotIn("svcSleepThread", diagnostic)
        self.assertNotIn("svcBreak", diagnostic)
        self.assertNotIn("svcExitProcess", diagnostic)

    def test_registration_log_keeps_missing_write_api_ambiguous(self):
        plugin = (SOURCE / "program" / "saltynx_external_plugin_persistence_bridge_trace.cpp").read_text(encoding="utf-8")
        self.assertIn("saltysd.log", plugin)
        self.assertIn("cannot distinguish", plugin.lower())

    def test_event_journal_callback_manifest_gate_dispatch_order_is_explicit(self):
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")

        registration = bridge[bridge.index("IsaacModRuntime_RegisterSaltyFileApi"):]
        self.assertLess(registration.index("PersistenceEventJournal::ConfigureFileApi"),
                        registration.index("PersistenceEventJournal::Mark"))
        self.assertIn("Event::FileApiAccepted", registration)
        self.assertIn("PersistenceEventJournal::DrainPendingBounded()", registration)

        callback = hook[hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)"):]
        for token in (
            "Event::ManagerCallbackEntered",
            "Event::OriginalReturned",
            "Event::ManifestEntered",
            "Event::ManifestReturned",
            "Event::GateEvaluated",
            "Event::DispatchEntered",
            "Event::DispatchReturned",
            "Event::CallbackFailed",
            "PersistenceEventJournal::FlushBounded",
        ):
            self.assertIn(token, callback)
        self.assertLess(callback.index("Event::ManagerCallbackEntered"), callback.index("Orig(self);"))
        self.assertLess(callback.index("Orig(self);"), callback.index("Event::OriginalReturned"))
        self.assertLess(callback.index("Event::ManifestEntered"), callback.index("InitializeDefaultManifestMod"))
        self.assertLess(callback.index("InitializeDefaultManifestMod"), callback.index("Event::ManifestReturned"))
        self.assertLess(callback.index("Event::DispatchEntered"), callback.index("DispatchPostUpdate"))
        self.assertLess(callback.index("DispatchPostUpdate"), callback.index("Event::DispatchReturned"))
        self.assertIn("PersistenceEventJournal::MarkOnce", callback)
        self.assertIn("PersistenceEventJournal::MarkIfChanged", callback)
        self.assertIn("PersistenceEventJournal::HasPending", callback)
        self.assertIn("PersistenceEventJournal::DrainPendingBounded(1)", callback)
        self.assertIn("LuaRuntime::CallbackFailureDetail()", callback)
        self.assertIn("static_cast<u32>(install)", entry[entry.index("Event::CallbackFailed"):])
        self.assertIn("PersistenceEventJournal::Event::RuntimeEntered", entry)
        self.assertIn("PersistenceEventJournal::Event::WorkerEntered", entry)
        failure_event = entry[entry.index("Event::CallbackFailed"):]
        self.assertIn("PersistenceEventJournal::FlushBounded()", failure_event)
        self.assertNotIn("svcSleepThread(1'000'000)", failure_event)
        self.assertNotIn("for (u32 wait", failure_event)
        self.assertNotIn("FileApiConfigured()", failure_event)

        failure_branch = entry[entry.index("if (install == DefaultManifestInstallResult::Success)"):]
        self.assertIn("EXL_PERSISTENCE_EVENT_DIAGNOSTIC", failure_branch)
        self.assertIn("Event::CallbackFailed", failure_branch)
        self.assertIn("#if !defined(EXL_PERSISTENCE_EVENT_DIAGNOSTIC)\n                ReportTraceDefaultInstallFailure(install);", failure_branch)

    def test_event_journal_has_runtime_owned_pending_drain_contract(self):
        header = (SOURCE / "persistence_event_journal.hpp").read_text(encoding="utf-8")
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        journal = (SOURCE / "persistence_event_journal.cpp").read_text(encoding="utf-8")
        self.assertIn("bool FileApiConfigured()", header)
        self.assertIn("FlushResult DrainPendingBounded", header)
        self.assertIn("g_fileApiReady", journal)
        self.assertIn("g_pendingDrainBudget", journal)
        self.assertIn("PersistenceEventJournal::Mark(PersistenceEventJournal::Event::FileApiAccepted)", bridge)

    def test_stage145_trace_plugin_is_isolated_from_default_and_stage144_builds(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        common = (RUNTIME / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")

        self.assertIn("133 134 135 136 137 138 139 140 141 142 143 144 145", makefile)
        self.assertIn("SALTYNX_STAGE145_CPPFILES", makefile)
        self.assertIn(
            "runtime_entry.cpp saltynx_external_plugin_persistence_bridge_trace.cpp", makefile
        )
        self.assertIn("mod-persistence-probe-trace-deploy", makefile)
        self.assertIn("deploy-saltynx/mod-persistence-probe-trace", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 144 || EXL_DIAGNOSTIC_STAGE == 145", entry)
        self.assertIn("saltynx_external_plugin_persistence_bridge_trace.cpp", common)

    def test_stage145_plugin_writes_bridge_marker_without_diagnostic_exit(self):
        bridge = SOURCE / "program" / "saltynx_external_plugin_persistence_bridge_trace.cpp"

        self.assertTrue(bridge.is_file(), "Stage145 trace bridge is missing")
        source = bridge.read_text(encoding="utf-8")
        for token in (
            "RunSaltyNxPersistenceBridge",
            "IsaacModRuntime_RegisterSaltyFileApi",
            "isaac-runtime-bridge.bin",
            "ISAACBR1",
            "registerFlags",
        ):
            self.assertIn(token, source)
        self.assertNotIn("svc #0x26", source)
        self.assertNotIn("svcExitProcess", source)

    def test_stage145_plugin_persists_the_runtime_test_run_snapshot_without_breaking_the_game(self):
        plugin = SOURCE / "program" / "saltynx_external_plugin_persistence_bridge_trace.cpp"
        source = plugin.read_text(encoding="utf-8")

        for token in (
            "IsaacModRuntime_GetTestRunSnapshot",
            "isaac-runtime-run.bin",
            "WriteTestRunSnapshot",
            "WriteTestRunSnapshot(open, write, close,",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertIn('open(kTestRunPath, "ab")', source)
        self.assertNotIn('open(kTestRunPath, "wb")', source)
        self.assertNotIn("svc #0x26", source)

    def test_stage145_plugin_exports_synchronous_delayed_runtime_observer_without_runtime_file_io(self):
        plugin = SOURCE / "program" / "saltynx_external_plugin_persistence_bridge_trace.cpp"
        runtime_bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        source = plugin.read_text(encoding="utf-8")

        for token in (
            "IsaacModRuntime_GetObserverState",
            "isaac-runtime-observer.bin",
            "ISAACOB1",
            "svcSleepThread",
            "kObserverDelayNanoseconds",
            "WriteObserver(open, write, close, state, observerFlags)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

        for token in (
            "svcCreateThread",
            "svcStartThread",
            "svcCloseHandle",
            "ObserverWorker",
            "g_observerStack",
        ):
            with self.subTest(absent_token=token):
                self.assertNotIn(token, source)

        self.assertIn("IsaacModRuntime_GetObserverState", runtime_bridge)
        export_body = runtime_bridge[runtime_bridge.index("IsaacModRuntime_GetObserverState") :]
        self.assertIn("CaptureObserverSnapshot", export_body)
        self.assertNotIn("ConfigureFileApi", export_body)
        self.assertNotIn("PersistenceTrace::Flush", export_body)

    def test_runtime_trace_configures_nondestructive_stage_file(self):
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        trace = (SOURCE / "persistence_trace.cpp").read_text(encoding="utf-8")
        trace_header = (SOURCE / "persistence_trace.hpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        self.assertIn("PersistenceTrace::ConfigureFileApi", bridge)
        self.assertIn("EXL_PERSISTENCE_TRACE", trace)
        self.assertIn("isaac-runtime-trace.bin", trace_header)
        self.assertNotIn("isaac-runtime-registered.bin", trace)
        self.assertIn("EXL_PERSISTENCE_TRACE", hook)
        self.assertIn('#include "persistence_trace.hpp"', hook)
        self.assertNotIn("kTraceManifestFailedMagic", hook)
        self.assertNotIn("kTraceCallbackErrorMagic", hook)

    def test_file_api_registration_uses_only_bounded_journal_io(self):
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        trace = (SOURCE / "persistence_trace.cpp").read_text(encoding="utf-8")
        audit = (SOURCE / "manager_update_hook_audit.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        register_body = bridge[
            bridge.index("IsaacModRuntime_RegisterSaltyFileApi"):
            bridge.index("#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)",
                         bridge.index("IsaacModRuntime_RegisterSaltyFileApi"))
        ]
        self.assertNotIn("(open)(", register_body)
        self.assertNotIn("traceFile", bridge)
        self.assertIn("void Flush() { TryFlush(); }", trace)
        self.assertIn("void Flush() { TryFlush(); }", audit)
        self.assertIn("PersistenceTrace::Flush();", hook)
        self.assertIn("ManagerUpdateHookAudit::Flush();", hook)

    def test_runtime_trace_implementation_is_linked_and_checked_after_build(self):
        common = (RUNTIME / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        post_build = (RUNTIME / "misc" / "scripts" / "post-build.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("CPPFILES += persistence_trace.cpp", common)
        self.assertNotIn("ifeq ($(PERSISTENCE_TRACE),1)\nCXXFLAGS += -Oz\nendif", common)
        for object_file in (
            "runtime_entry.o",
            "hook_manager.o",
            "manager_update_hook_audit.o",
            "persistence_trace.o",
            "saltynx_runtime_bridge.o",
        ):
            with self.subTest(object_file=object_file):
                self.assertIn(f"{object_file}: CXXFLAGS += -Oz", common)
        self.assertIn("lapi.o: CFLAGS += -Oz", common)
        self.assertIn('if [ "${PERSISTENCE_TRACE:-0}" = "1" ]; then', post_build)
        self.assertIn("PersistenceTrace::", post_build)
        self.assertIn("contains unresolved PersistenceTrace symbols", post_build)

    def test_runtime_trace_audits_worker_and_manager_hook_after_install(self):
        common = (RUNTIME / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        post_build = (RUNTIME / "misc" / "scripts" / "post-build.sh").read_text(
            encoding="utf-8"
        )
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")

        self.assertIn("CPPFILES += manager_update_hook_audit.cpp", common)
        self.assertIn("manager_update_hook_audit.o: CXXFLAGS += -Oz", common)
        self.assertIn("persistence_trace.o: CXXFLAGS += -Oz", common)
        self.assertIn("saltynx_runtime_bridge.o: CXXFLAGS += -Oz", common)
        self.assertIn("runtime_entry.o: CXXFLAGS += -Oz", common)
        self.assertIn("ManagerUpdateHookAudit::", post_build)
        for token in (
            "RecordWorkerState(\n        ManagerUpdateHookAudit::WorkerState::RuntimeEntered",
            "WorkerState::ThreadCreateFailed",
            "WorkerState::ThreadStartFailed",
            "WorkerState::Started",
            "RecordScanAttempt(attempt + 1)",
            "WorkerState::Found",
            "WorkerState::InstallFailure",
            "WorkerState::TimedOut",
            "kManagerHookAuditSampleLimit",
            "ManagerUpdateHookAudit::Sample(finalSample)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, entry)
        self.assertIn("ManagerUpdateHookAudit::RecordInstall", hook)
        self.assertIn("ManagerUpdateHookAudit::RecordCallbackEntry();", hook)
        self.assertIn("ManagerUpdateHookAudit::ConfigureFileApi", bridge)

    def test_runtime_trace_records_the_precise_default_manifest_failure(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        for token in (
            "DefaultManifestFailureDetail",
            "ManifestRead = 1",
            "ManifestParse = 2",
            "PathBuild = 3",
            "EntryRead = 4",
            # LuaInit 步的失败细节按 `0x10 + detail` 编码（`MapModLoadFailure`）；
            # 该函数已从"取局部 result"改成"读 `ModLoadFailure` 结构"，断言跟着改。
            "0x10U + failure.detail",
            "g_DefaultManifestFailureDetail",
            "PersistenceTrace::Phase::ManifestCompleted, traceDetail",
        ):
            with self.subTest(token=token):
                self.assertIn(token, hook)

    def test_runtime_trace_encodes_lua_preparation_result_and_step(self):
        runtime = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        for token, source in (
            ("g_PreparationStep.store(7, std::memory_order_relaxed);", runtime),
            ("g_PreparationStep.store(8, std::memory_order_relaxed);", runtime),
            ("LuaRuntime::PreparationFailureDetail()", hook),
            ("(static_cast<u64>(result) << 32) | preparationDetail", hook),
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_runtime_trace_encodes_callback_status_and_persistence_operation(self):
        runtime = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        mod_api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "mod_api.cpp"
        ).read_text(encoding="utf-8")
        header = (SOURCE / "lua_runtime.hpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        for token, source in (
            ("CallbackFailureDetail()", header),
            ("g_CallbackOperation", runtime),
            ("CallbackOperation::HasData", mod_api),
            ("callbackStatus", runtime),
            ("LuaRuntime::CallbackFailureDetail()", hook),
            ("PersistenceTrace::Phase::CallbackFailed, callbackDetail", hook),
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_runtime_trace_reports_install_failure_and_records_ready_persistence_gate(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        for token in (
            "kTraceDefaultInstallFailureMagic",
            "ReportTraceDefaultInstallFailure",
            "static_cast<u32>(result)",
            "ISAAC_PI",
        ):
            with self.subTest(token=token):
                self.assertIn(token, entry)

        for token in (
            "PersistenceTrace::Phase::PersistenceGateReady",
            "kDefaultPersistenceRequired",
            "ModPersistence::IsFileApiReady()",
        ):
            with self.subTest(token=token):
                self.assertIn(token, hook)

    def test_runtime_trace_records_callback_boundaries_without_exiting_before_dispatch(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        update = hook[
            hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)"):
            hook.index("HOOK_DEFINE_TRAMPOLINE(MusicReplayMusicPlayHook)")
        ]

        entered = "PersistenceTrace::RecordCallbackEntry();"
        returned = "PersistenceTrace::Mark(PersistenceTrace::Phase::OriginalReturned);"
        started = "PersistenceTrace::Mark(PersistenceTrace::Phase::DispatchStarted);"
        dispatched = "LuaRuntime::DispatchPostUpdate();"
        completed = "PersistenceTrace::Mark(PersistenceTrace::Phase::DispatchReturned);"
        failed = "PersistenceTrace::Mark(PersistenceTrace::Phase::CallbackFailed, callbackDetail);"
        for token in (entered, returned, started, completed, failed):
            self.assertIn(token, update)
        self.assertLess(update.index(entered), update.index("Orig(self);"))
        self.assertLess(update.index("Orig(self);"), update.index(returned))
        self.assertLess(update.index(started), update.index(dispatched))
        self.assertLess(update.index(dispatched), update.index(completed))
        self.assertLess(update.index(completed), update.index(failed))
        self.assertNotIn("kTracePersistenceGateMagic", update)

if __name__ == "__main__":
    unittest.main()
