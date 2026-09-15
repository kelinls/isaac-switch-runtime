"""扫描、Hook 与回调应用服务的集成测试。

Task 4 把三件事从 hook_manager.cpp / runtime_entry.cpp 抽到应用层：
有界的模块扫描重试、Hook 安装策略（Update 必需、Render 与 Collectible 可选）、
以及可多 Mod 注册并按顺序派发且互相隔离的回调管线。这里用假的 Port 验证策略，
并检查应用层没有反向依赖 exlaunch 或旧头文件。
"""

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
SRC = RUNTIME / "src"

DRIVER = textwrap.dedent(
    r"""
    #include "application/callback/callback_dispatcher.hpp"
    #include "application/callback/callback_registry.hpp"
    #include "application/runtime/hook_install_service.hpp"
    #include "application/runtime/module_scan_service.hpp"
    #include "domain/callback/callback_descriptor.hpp"
    #include "ports/hook_port.hpp"
    #include "ports/module_scanner_port.hpp"
    #include "ports/thread_port.hpp"

    #include <cstdio>
    #include <vector>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;

    void Check(bool condition, const char* what) {
        if (!condition) {
            std::printf("FAILED_CHECK %s\n", what);
            ++failures;
        }
    }

    ModHandle Handle(std::uint16_t index) {
        ModHandle handle{};
        handle.index = index;
        handle.generation = 1;
        return handle;
    }

    struct RecordingInvoker final : ICallbackInvoker {
        std::vector<int> called;
        int failingReference = -1;
        Status Invoke(const CallbackDescriptor& descriptor) noexcept override {
            called.push_back(descriptor.luaReference);
            if (descriptor.luaReference == failingReference) {
                return Status{StatusCode::Rejected};
            }
            return Status::Ok();
        }
    };

    struct CountingThreads final : IThreadPort {
        std::uint32_t sleeps = 0;
        Status StartWorker(ThreadEntry, void*) noexcept override { return Status::Ok(); }
        std::uint64_t CurrentThreadId() const noexcept override { return 1; }
        void SleepMilliseconds(std::uint32_t) noexcept override { ++sleeps; }
    };

    struct ScriptedScanner final : IModuleScannerPort {
        std::vector<StatusCode> script;
        std::size_t position = 0;
        std::uint32_t calls = 0;
        Status WaitForTarget(const TargetModuleSpec&, ModuleInfo* module) noexcept override {
            ++calls;
            const StatusCode code = position < script.size() ? script[position] : StatusCode::NotFound;
            ++position;
            if (code == StatusCode::Ok) {
                module->base = 0x7100000000ULL;
                module->textSize = 0x1000;
                module->valid = true;
            }
            return Status{code};
        }
    };

    struct FailingValidScanner final : IModuleScannerPort {
        Status WaitForTarget(const TargetModuleSpec&, ModuleInfo* module) noexcept override {
            module->valid = false;
            return Status::Ok();
        }
    };

    struct ScriptedHooks final : IHookPort {
        Status update{StatusCode::Ok};
        Status render{StatusCode::Ok};
        Status collectible{StatusCode::Ok};
        Status present{StatusCode::Ok};
        Status rebuild{StatusCode::Ok};
        Status gameStart{StatusCode::Ok};
        std::uint32_t updateCalls = 0;
        std::uint32_t renderCalls = 0;
        std::uint32_t collectibleCalls = 0;
        std::uint32_t presentCalls = 0;
        std::uint32_t rebuildCalls = 0;
        std::uint32_t gameStartCalls = 0;

        Status Install(HookId id, const HookTarget&) noexcept override {
            switch (id) {
                case HookId::ManagerUpdate: ++updateCalls; return update;
                case HookId::ManagerRender: ++renderCalls; return render;
                case HookId::PreGetCollectible: ++collectibleCalls; return collectible;
                case HookId::ManagerPresent: ++presentCalls; return present;
                case HookId::RebuildMountPoints: ++rebuildCalls; return rebuild;
                // `GameStart` 走的是 `RelaySlot` 后端（只往引擎侧回调槽里写我们的指针，
                // 见 `domain/runtime/hook_catalog.hpp`）。夹具里必须为它留一支，
                // 否则 `-Werror,-Wswitch` 直接判编译失败。
                case HookId::GameStart: ++gameStartCalls; return gameStart;
                case HookId::Count: break;
            }
            return Status{StatusCode::InvalidArgument};
        }
        bool IsInstalled(HookId) const noexcept override { return false; }
    };

    struct CapturingHooks final : IHookPort {
        HookTarget last{};
        std::uint32_t calls = 0;
        Status Install(HookId, const HookTarget& target) noexcept override {
            last = target;
            ++calls;
            return Status::Ok();
        }
        bool IsInstalled(HookId) const noexcept override { return false; }
    };

    ModuleInfo ValidModule() {
        ModuleInfo module{};
        module.base = 0x7100000000ULL;
        module.textSize = 0x1000;
        module.valid = true;
        return module;
    }

    CallbackDescriptor Descriptor(CallbackId id, ModHandle owner, int reference,
                                  ThreadAffinity affinity = ThreadAffinity::ManagedCallback) {
        CallbackDescriptor descriptor{};
        descriptor.id = id;
        descriptor.owner = owner;
        descriptor.luaReference = reference;
        descriptor.affinity = affinity;
        return descriptor;
    }

    void TestRegistry() {
        CallbackRegistry registry{};
        Check(registry.Register(Descriptor(kCallbackPostUpdate, Handle(1), 11)).ok(),
              "register_first_mod");
        Check(registry.Register(Descriptor(kCallbackPostUpdate, Handle(2), 22)).ok(),
              "register_second_mod");
        Check(registry.Count() == 2, "two_registrations");
        Check(registry.CountOf(kCallbackPostUpdate) == 2, "two_callbacks_for_id");

        Check(registry.Register(Descriptor(kCallbackPostUpdate, Handle(1), 99)).ok(),
              "reregistration_allowed");
        // 同一 (id, owner) 再登记一次 = **追加**，不是替换。PC 的 `Mod:AddCallback` 每次都新增一条、
        // 派发时按登记顺序全部调用（EID 对同一 id 登记 5 条 `MC_POST_NEW_ROOM` 就靠这个），
        // 见 `callback_registry.cpp` 里 2026-09-12 那段注释。
        Check(registry.Count() == 3, "reregistration_appends");
        Check(registry.CountOf(kCallbackPostUpdate) == 3, "three_callbacks_for_id");
        const CallbackDescriptor* first = registry.At(kCallbackPostUpdate, 0);
        const CallbackDescriptor* appended = registry.At(kCallbackPostUpdate, 2);
        Check(first != nullptr && first->luaReference == 11, "first_registration_kept");
        Check(appended != nullptr && appended->luaReference == 99, "appended_registration_is_last");

        CallbackDescriptor invalid = Descriptor(kCallbackPostUpdate, ModHandle{}, 11);
        Check(registry.Register(invalid).code() == StatusCode::InvalidArgument,
              "ownerless_callback_rejected");

        // 容量上限：一直登记到超出（容量 256），必须报 CapacityExceeded，且总数停在容量上
        // ——既不静默丢弃，也不覆盖旧条目。
        std::uint32_t index = 0;
        Status capacity = Status::Ok();
        while (capacity.ok() && index < 400) {
            capacity = registry.Register(
                Descriptor(1000 + index, Handle(static_cast<std::uint16_t>(100 + index)),
                           static_cast<int>(200 + index)));
            ++index;
        }
        Check(capacity.code() == StatusCode::CapacityExceeded, "capacity_is_bounded");
        Check(registry.Count() == CallbackRegistry::kCapacity, "capacity_is_exact");

        Check(registry.RemoveOwner(Handle(2)) == 1, "remove_owner_removes_one");
        // 只剩 owner 1 的两条（ref 11 与 ref 99）：`Remove`/`RemoveOwner` 只按 (id, owner) 删，不误伤别人的条目。
        Check(registry.CountOf(kCallbackPostUpdate) == 2, "removed_callback_gone");
    }

    void TestDispatcher() {
        CallbackRegistry registry{};
        RecordingInvoker invoker{};
        CallbackDispatcher dispatcher{registry, invoker};

        Check(registry.Register(Descriptor(kCallbackPostRender, Handle(1), 11)).ok(), "d_reg_1");
        Check(registry.Register(Descriptor(kCallbackPostRender, Handle(2), 22)).ok(), "d_reg_2");
        Check(registry.Register(Descriptor(kCallbackPostRender, Handle(3), 33,
                                           ThreadAffinity::MainUpdate)).ok(), "d_reg_3");

        DispatchReport report = dispatcher.Dispatch(kCallbackPostRender, ThreadAffinity::ManagedCallback);
        Check(report.invoked == 2 && report.failed == 0, "two_invoked");
        Check(report.skippedAffinity == 1, "main_update_callback_skipped_on_render_thread");
        Check(invoker.called.size() == 2 && invoker.called[0] == 11 && invoker.called[1] == 22,
              "registration_order_preserved");

        invoker.called.clear();
        invoker.failingReference = 11;
        report = dispatcher.Dispatch(kCallbackPostRender, ThreadAffinity::ManagedCallback);
        Check(report.invoked == 1 && report.failed == 1, "failure_isolated_per_callback");
        Check(invoker.called.size() == 2, "second_callback_still_runs");

        invoker.called.clear();
        invoker.failingReference = -1;
        report = dispatcher.DispatchOwner(Handle(2), kCallbackPostRender,
                                          ThreadAffinity::ManagedCallback);
        Check(report.invoked == 1 && invoker.called.size() == 1 && invoker.called[0] == 22,
              "owner_dispatch_filters");

        invoker.called.clear();
        report = dispatcher.Dispatch(kCallbackPostRender, ThreadAffinity::Any);
        Check(report.skippedAffinity == 0 && report.invoked == 3, "unknown_context_does_not_skip");
    }

    void TestScanService() {
        ScriptedScanner scanner{};
        CountingThreads threads{};
        ModuleScanService service{scanner, &threads};
        TargetModuleSpec spec{};
        spec.name = "Repentance.nrs";

        scanner.script = {StatusCode::NotFound, StatusCode::NotFound, StatusCode::Ok};
        Result<ModuleInfo> found = service.ScanWithRetry(spec, ScanPolicy{5, 100});
        Check(found.ok() && found.value().base == 0x7100000000ULL, "retry_finds_module");
        Check(service.attempts() == 3, "three_attempts");
        Check(threads.sleeps == 2, "sleeps_between_attempts");

        ScriptedScanner mismatch{};
        CountingThreads mismatchThreads{};
        ModuleScanService mismatchService{mismatch, &mismatchThreads};
        mismatch.script = {StatusCode::Rejected, StatusCode::Rejected};
        Result<ModuleInfo> rejected = mismatchService.ScanWithRetry(spec, ScanPolicy{2, 100});
        Check(rejected.code() == StatusCode::Rejected, "build_mismatch_is_last_failure");
        Check(mismatchService.attempts() == 2, "mismatch_keeps_retrying");

        ScriptedScanner mixed{};
        CountingThreads mixedThreads{};
        ModuleScanService mixedService{mixed, &mixedThreads};
        mixed.script = {StatusCode::NotFound, StatusCode::Rejected};
        Check(mixedService.ScanWithRetry(spec, ScanPolicy{2, 100}).code() == StatusCode::Rejected,
              "mismatch_preferred_over_not_found");

        ScriptedScanner invalid{};
        CountingThreads invalidThreads{};
        ModuleScanService invalidService{invalid, &invalidThreads};
        invalid.script = {StatusCode::InvalidArgument};
        Check(invalidService.ScanWithRetry(spec, ScanPolicy{5, 100}).code() ==
                  StatusCode::InvalidArgument,
              "invalid_spec_fails_immediately");
        Check(invalidService.attempts() == 1 && invalidThreads.sleeps == 0,
              "no_retry_on_invalid_spec");
        Check(invalidService.ScanWithRetry(spec, ScanPolicy{0, 100}).code() ==
                  StatusCode::InvalidArgument,
              "zero_attempt_policy_rejected");

        FailingValidScanner lying{};
        ModuleScanService lyingService{lying, nullptr};
        Check(lyingService.ScanOnce(spec).code() == StatusCode::InvalidState,
              "success_without_module_is_contract_violation");

        ScriptedScanner offline{};
        ModuleScanService offlineService{offline, nullptr};
        offline.script = {StatusCode::NotFound, StatusCode::NotFound};
        Check(offlineService.ScanWithRetry(spec, ScanPolicy{2, 100}).code() == StatusCode::NotFound,
              "exhausted_attempts_report_not_found");
    }

    void TestHookInstallService() {
        ScriptedHooks hooks{};
        HookInstallService service{hooks};
        HookInstallReport report{};

        Check(service.InstallProductionHooks(ValidModule(), &report).ok(), "install_success");
        Check(report.OutcomeOf(HookId::ManagerUpdate) == HookOutcome::Installed &&
                  report.OutcomeOf(HookId::ManagerRender) == HookOutcome::Installed &&
                  report.OutcomeOf(HookId::PreGetCollectible) == HookOutcome::Installed &&
                  report.OutcomeOf(HookId::ManagerPresent) == HookOutcome::Installed &&
                  report.OutcomeOf(HookId::RebuildMountPoints) == HookOutcome::Installed,
              "all_hooks_installed");
        Check(report.productionReady(), "report_ready");
        Check(report.InstalledCount() == static_cast<std::uint32_t>(kHookIdCount),
              "installed_count_counts_every_hook");
        Check(report.FirstFailureSlot() == 0, "no_failure_slot_when_every_hook_is_installed");
        Check(hooks.updateCalls == 1 && hooks.renderCalls == 1 && hooks.collectibleCalls == 1 &&
                  hooks.presentCalls == 1 && hooks.rebuildCalls == 1 && hooks.gameStartCalls == 1,
              "each_hook_installed_once");

        // 所有**可选**挂点都被端口拒绝：`GameStart` 同样是可选点（`kHookCatalog` 里
        // `required == false`），所以这里也把它拒掉 —— 否则 `InstalledCount()` 会变成 2，
        // 而这条检查要证明的正是"被跳过的不计入已安装数"。
        ScriptedHooks optionalMissing{};
        optionalMissing.render = Status{StatusCode::Rejected};
        optionalMissing.collectible = Status{StatusCode::Rejected};
        optionalMissing.present = Status{StatusCode::Rejected};
        optionalMissing.rebuild = Status{StatusCode::Rejected};
        optionalMissing.gameStart = Status{StatusCode::Rejected};
        HookInstallService optionalService{optionalMissing};
        HookInstallReport optionalReport{};
        Check(optionalService.InstallProductionHooks(ValidModule(), &optionalReport).ok(),
              "optional_hook_failure_is_not_fatal");
        Check(optionalReport.OutcomeOf(HookId::ManagerUpdate) == HookOutcome::Installed &&
                  optionalReport.OutcomeOf(HookId::ManagerRender) == HookOutcome::Skipped &&
                  optionalReport.OutcomeOf(HookId::PreGetCollectible) == HookOutcome::Skipped &&
                  optionalReport.OutcomeOf(HookId::ManagerPresent) == HookOutcome::Skipped &&
                  optionalReport.OutcomeOf(HookId::RebuildMountPoints) == HookOutcome::Skipped &&
                  optionalReport.OutcomeOf(HookId::GameStart) == HookOutcome::Skipped,
              "optional_hooks_recorded_as_skipped");
        Check(optionalReport.productionReady(), "optional_failure_still_ready");
        Check(optionalReport.InstalledCount() == 1, "skipped_hooks_are_not_counted");
        // `Skipped` 不是失败：可选点被端口拒绝时，报告里没有任何 `Failed` 槽。
        Check(optionalReport.FirstFailureSlot() == 0, "skipped_hooks_are_not_failures");

        ScriptedHooks requiredMissing{};
        requiredMissing.update = Status{StatusCode::Rejected};
        HookInstallService requiredService{requiredMissing};
        HookInstallReport requiredReport{};
        Check(requiredService.InstallProductionHooks(ValidModule(), &requiredReport).code() ==
                  StatusCode::Rejected,
              "required_hook_failure_is_fatal");
        Check(requiredReport.OutcomeOf(HookId::ManagerUpdate) == HookOutcome::Failed,
              "required_hook_reported_failed");
        Check(requiredReport.FirstFailureSlot() ==
                  static_cast<std::uint32_t>(HookId::ManagerUpdate) + 1U,
              "first_failure_slot_points_at_update");
        Check(!requiredReport.productionReady(), "required_failure_is_not_ready");
        Check(requiredMissing.renderCalls == 0 && requiredMissing.collectibleCalls == 0 &&
                  requiredMissing.presentCalls == 0 && requiredMissing.rebuildCalls == 0,
              "optional_hooks_not_attempted_after_required_failure");
        Check(requiredReport.OutcomeOf(HookId::ManagerRender) == HookOutcome::Skipped &&
                  requiredReport.OutcomeOf(HookId::RebuildMountPoints) == HookOutcome::Skipped,
              "unattempted_optional_hooks_are_skipped_not_pending");

        Check(service.InstallProductionHooks(ValidModule(), nullptr).code() ==
                  StatusCode::InvalidArgument,
              "null_report_rejected");
        ModuleInfo invalidModule{};
        HookInstallReport invalidReport{};
        Check(service.InstallProductionHooks(invalidModule, &invalidReport).code() ==
                  StatusCode::InvalidArgument,
              "invalid_module_rejected");
        // 早退路径（报告先清零再返回）留下的 `Pending` **不是**失败：必须配合
        // `InstalledCount()==0` 才能被设备侧读成"从未填充"，见 hpp 的回读口径注释。
        Check(invalidReport.InstalledCount() == 0 && invalidReport.FirstFailureSlot() == 0 &&
                  invalidReport.OutcomeOf(HookId::ManagerUpdate) == HookOutcome::Pending &&
                  !invalidReport.productionReady(),
              "early_return_leaves_the_report_unfilled_not_failed");

        // 契约：`FirstFailureSlot` 只看 `Failed`，`Pending` 不算失败。
        HookInstallReport fresh{};
        Check(fresh.InstalledCount() == 0 && fresh.FirstFailureSlot() == 0,
              "unfilled_report_reads_as_no_failure_and_no_install");
        Check(fresh.OutcomeOf(HookId::ManagerUpdate) == HookOutcome::Pending &&
                  fresh.OutcomeOf(HookId::ManagerRender) == HookOutcome::Pending &&
                  fresh.OutcomeOf(HookId::PreGetCollectible) == HookOutcome::Pending &&
                  fresh.OutcomeOf(HookId::ManagerPresent) == HookOutcome::Pending &&
                  fresh.OutcomeOf(HookId::RebuildMountPoints) == HookOutcome::Pending,
              "unfilled_report_keeps_every_slot_pending");
        Check(fresh.OutcomeOf(HookId::Count) == HookOutcome::Failed,
              "out_of_range_hook_id_reads_as_failed");
    }

    void TestHookTargetCarriesModuleIdentity() {
        // Hook verification compares the module build-ID field, so the service
        // must pass base, code window, image size and the full build ID through
        // the port; dropping any of them would silently disable every hook.
        CapturingHooks hooks{};
        HookInstallService service{hooks};
        HookInstallReport report{};
        ModuleInfo module = ValidModule();
        module.imageSize = 0x200000;
        module.buildId[0] = 0x91;
        module.buildId[19] = 0xAB;
        Check(service.InstallProductionHooks(module, &report).ok(), "capture_install");
        // 每个挂钩点各装一次：数量由 HookId 决定，不再写死某个数字。
        Check(hooks.calls == static_cast<std::uint32_t>(kHookIdCount),
              "every_interception_point_installed_once");
        Check(hooks.last.base == module.base, "target_base_forwarded");
        Check(hooks.last.codeSize == module.textSize, "target_code_window_forwarded");
        Check(hooks.last.imageSize == module.imageSize, "target_image_size_forwarded");
        Check(hooks.last.buildId == module.buildId, "target_build_id_forwarded");
        Check(kBuildIdSize == 32, "build_id_field_is_32_bytes");
    }

    } // namespace

    int main() {
        TestRegistry();
        TestDispatcher();
        TestScanService();
        TestHookInstallService();
        TestHookTargetCarriesModuleIdentity();
        if (failures != 0) {
            std::printf("PIPELINE_CHECKS_FAILED %d\n", failures);
            return 1;
        }
        std::printf("PIPELINE_CHECKS_PASSED\n");
        return 0;
    }
    """
)


def host_compiler() -> str | None:
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return found
    return None


class HookCallbackPipelineTests(unittest.TestCase):
    def test_application_layer_has_no_platform_dependency(self):
        offenders = []
        for directory in (SRC / "application", SRC / "domain", SRC / "ports"):
            for path in sorted(directory.rglob("*")):
                if path.suffix not in (".hpp", ".cpp"):
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("#include"):
                        continue
                    if any(token in stripped for token in
                           ("exlaunch", "hook_manager", "module_finder", "infrastructure/", "nn/")):
                        offenders.append(f"{path.relative_to(ROOT)}: {stripped}")
        self.assertEqual(offenders, [], "应用/领域/端口层不得依赖平台或旧实现头文件")

    def test_exlaunch_adapter_maps_every_hook_to_a_verified_installer(self):
        adapter = (SRC / "infrastructure" / "exlaunch" / "exlaunch_hook_adapter.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("TryInstallManagerUpdateHook", adapter)
        self.assertIn("TryInstallManagerRenderHook", adapter)
        self.assertIn("TryInstallPreGetCollectibleRelay", adapter)
        self.assertIn("TryInstallManagerPresentRelay", adapter)
        self.assertIn("TryInstallRebuildMountPointsRelay", adapter)
        self.assertIn("std::memcpy(module.buildId.data()", adapter)
        # 挂点清单与"每个点用哪种后端"登记在 domain 的登记表里（唯一真值源）；
        # 端口只引用它，所以要查挂点名得读那张表。
        catalog = (SRC / "domain" / "runtime" / "hook_catalog.hpp").read_text(encoding="utf-8")
        for hook in ("ManagerUpdate", "ManagerRender", "PreGetCollectible",
                     "ManagerPresent", "RebuildMountPoints"):
            self.assertIn(hook, catalog)
        header = (SRC / "ports" / "hook_port.hpp").read_text(encoding="utf-8")
        self.assertIn("hook_catalog.hpp", header, "端口必须引用登记表而不是自己列一遍挂点")
        self.assertIn("ModuleBuildId buildId", header)

    def test_callback_ids_match_the_pc_modcallbacks_table(self):
        header = (SRC / "domain" / "callback" / "callback_descriptor.hpp").read_text(encoding="utf-8")
        # 2026-09-12：`MC_POST_UPDATE` 从内部值 0 改成 PC 契约的 1（0 在 PC 表里是
        # `MC_NPC_UPDATE`）。Lua 侧的 `ModCallbacks` 表由 `pc_lua_enum_data.cpp` 生成，
        # 两边必须一致。
        expected = {
            "kCallbackPostUpdate": 1,
            "kCallbackPostRender": 2,
            "kCallbackInputAction": 13,
            "kCallbackPreGetCollectible": 62,
        }
        for name, value in expected.items():
            match = re.search(rf"{name} = (\d+);", header)
            self.assertIsNotNone(match, f"{name} 必须显式定义")
            self.assertEqual(int(match.group(1)), value)

    def test_pipeline_behaviour_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-hook-pipeline-") as temporary:
            directory = Path(temporary)
            source = directory / "pipeline.cpp"
            binary = directory / "pipeline"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(SRC),
                    str(source),
                    str(SRC / "application" / "callback" / "callback_registry.cpp"),
                    str(SRC / "application" / "callback" / "callback_dispatcher.cpp"),
                    str(SRC / "application" / "runtime" / "module_scan_service.cpp"),
                    str(SRC / "application" / "runtime" / "hook_install_service.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("PIPELINE_CHECKS_PASSED", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
