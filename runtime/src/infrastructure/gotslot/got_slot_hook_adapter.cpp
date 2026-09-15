#include "infrastructure/gotslot/got_slot_hook_adapter.hpp"

#include "hook_manager.hpp"

#include <algorithm>
#include <cstring>

namespace isaac::runtime {
namespace {

// 端口类型 → 安装器要的模块描述。与 `exlaunch_hook_adapter.cpp` 里那份换算是同一套口径
// （`HookTarget` 与 `TargetModule` 字段一一对应），这里为了不动那个已冻结的适配器而各自持一份。
TargetModule ModuleOf(const HookTarget& target) noexcept {
    TargetModule module{};
    module.base = target.base;
    module.textSize = target.codeSize;
    module.size = target.imageSize != 0 ? target.imageSize : target.codeSize;
    const std::size_t buildIdBytes = std::min(module.buildId.size(), target.buildId.size());
    std::memcpy(module.buildId.data(), target.buildId.data(), buildIdBytes);
    return module;
}

} // namespace

Status GotSlotHookAdapter::Install(HookId id, const HookTarget& target) noexcept {
    const std::size_t index = static_cast<std::size_t>(id);
    if (index >= kHookIdCount || target.base == 0 || target.codeSize == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    if (installed_[index]) {
        return Status{StatusCode::InvalidState};
    }
    // 本后端只实现 `Present` 那一处；别的挂点由路由表交给各自的适配器。
    // 走到这里的其它 `HookId` 是配置错误，**响亮失败**而不是静默成功。
    if (id != HookId::ManagerPresent) {
        return Status{StatusCode::Rejected};
    }
    if (TryInstallManagerPresentGotSlot(ModuleOf(target)) !=
        RenderPresentRelayInstallResult::Success) {
        return Status{StatusCode::Rejected};
    }
    installed_[index] = true;
    return Status::Ok();
}

bool GotSlotHookAdapter::IsInstalled(HookId id) const noexcept {
    const std::size_t index = static_cast<std::size_t>(id);
    return index < kHookIdCount && installed_[index];
}

} // namespace isaac::runtime
