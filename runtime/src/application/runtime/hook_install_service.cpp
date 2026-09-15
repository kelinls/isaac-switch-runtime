#include "application/runtime/hook_install_service.hpp"

namespace isaac::runtime {
namespace {

HookTarget TargetOf(const ModuleInfo& module) noexcept {
    HookTarget target{};
    target.base = module.base;
    target.codeSize = module.textSize;
    target.imageSize = module.imageSize;
    target.buildId = module.buildId;
    return target;
}

} // namespace

std::uint32_t HookInstallReport::InstalledCount() const noexcept {
    std::uint32_t count = 0;
    for (const HookOutcome outcome : outcomes) {
        if (outcome == HookOutcome::Installed) {
            ++count;
        }
    }
    return count;
}

std::uint32_t HookInstallReport::FirstFailureSlot() const noexcept {
    for (std::size_t index = 0; index < outcomes.size(); ++index) {
        if (outcomes[index] == HookOutcome::Failed) {
            return static_cast<std::uint32_t>(index) + 1U;
        }
    }
    return 0U;
}

Status HookInstallService::InstallProductionHooks(const ModuleInfo& module,
                                                 HookInstallReport* report) noexcept {
    if (report == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    *report = HookInstallReport{};
    if (!module.valid) {
        return Status{StatusCode::InvalidArgument};
    }

    const HookTarget target = TargetOf(module);

    // 必需点先装（登记表里 required 的那些；当前只有 ManagerUpdate）。
    // 必需点失败即整体失败：其余点根本没被尝试，一律记 Skipped，
    // 于是报告里不会留下 Pending（回读口径"FirstFailureSlot 只看 Failed"依赖这一点）。
    for (const HookDescriptor& point : kHookCatalog) {
        if (!point.required) {
            continue;
        }
        const Status status = hooks_.Install(point.id, target);
        if (!status.ok()) {
            report->Set(point.id, HookOutcome::Failed);
            for (const HookDescriptor& other : kHookCatalog) {
                if (other.id != point.id) {
                    report->Set(other.id, HookOutcome::Skipped);
                }
            }
            return status;
        }
        report->Set(point.id, HookOutcome::Installed);
    }

    // 可选点：各判各的，任何一个装不上都只记 Skipped，
    // 既不影响 Mod 加载，也不影响其它挂点。顺序即回读槽位顺序。
    for (const HookDescriptor& point : kHookCatalog) {
        if (point.required) {
            continue;
        }
        report->Set(point.id, hooks_.Install(point.id, target).ok() ? HookOutcome::Installed
                                                                    : HookOutcome::Skipped);
    }
    return Status::Ok();
}

} // namespace isaac::runtime
