#include "infrastructure/exlaunch/exlaunch_hook_adapter.hpp"

#include "hook_manager.hpp"

#include <algorithm>
#include <cstring>

namespace isaac::runtime {
namespace {

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

Status ExlaunchHookAdapter::Install(HookId id, const HookTarget& target) noexcept {
    const std::size_t index = static_cast<std::size_t>(id);
    if (index >= kHookCount || target.base == 0 || target.codeSize == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    if (installed_[index]) {
        return Status{StatusCode::InvalidState};
    }

    const TargetModule module = ModuleOf(target);
    bool ok = false;
    switch (id) {
        case HookId::ManagerUpdate:
            ok = TryInstallManagerUpdateHook(module) == HookInstallResult::Success;
            break;
        case HookId::ManagerRender:
            ok = TryInstallManagerRenderHook(module) == RenderHookInstallResult::Success;
            break;
        case HookId::PreGetCollectible:
            ok = TryInstallPreGetCollectibleRelay(module) ==
                 PreGetCollectibleRelayInstallResult::Success;
            break;
        case HookId::ManagerPresent:
            ok = TryInstallManagerPresentRelay(module) == RenderPresentRelayInstallResult::Success;
            break;
        case HookId::RebuildMountPoints:
            // 这个安装器直接返回布尔值（真=已验证并发布）。
            ok = TryInstallRebuildMountPointsRelay(module);
            break;
        case HookId::GameStart:
            // 开局走"代码洞中继 + 回调槽"（`RelaySlot` 后端），旧 IPS 蹦床这边不接。
            return Status{StatusCode::InvalidArgument};
        case HookId::Count:
            return Status{StatusCode::InvalidArgument};
    }
    if (!ok) {
        return Status{StatusCode::Rejected};
    }
    installed_[index] = true;
    return Status::Ok();
}

bool ExlaunchHookAdapter::IsInstalled(HookId id) const noexcept {
    const std::size_t index = static_cast<std::size_t>(id);
    if (index >= kHookCount) {
        return false;
    }
    return installed_[index];
}

} // namespace isaac::runtime
