#include "infrastructure/hook_routing_adapter.hpp"

namespace isaac::runtime {

Status HookRoutingAdapter::Install(HookId id, const HookTarget& target) noexcept {
    const auto index = static_cast<std::size_t>(id);
    if (index >= kHookIdCount) {
        return Status{StatusCode::InvalidArgument};
    }
    switch (kHookBackends[index]) {
        case HookBackend::EntryRelay:
            return relay_.Install(id, target);
        case HookBackend::GotSlot:
            return gotSlot_.Install(id, target);
        case HookBackend::RelaySlot:
            return relaySlot_.Install(id, target);
        case HookBackend::LegacyIps:
            break;
    }
    return legacy_.Install(id, target);
}

bool HookRoutingAdapter::IsInstalled(HookId id) const noexcept {
    const auto index = static_cast<std::size_t>(id);
    if (index >= kHookIdCount) {
        return false;
    }
    switch (kHookBackends[index]) {
        case HookBackend::EntryRelay:
            return relay_.IsInstalled(id);
        case HookBackend::GotSlot:
            return gotSlot_.IsInstalled(id);
        case HookBackend::RelaySlot:
            return relaySlot_.IsInstalled(id);
        case HookBackend::LegacyIps:
            break;
    }
    return legacy_.IsInstalled(id);
}

std::uint32_t HookRoutingAdapter::LastFailureCode() const noexcept {
    // 路由适配器本身不安装挂点，失败码来自内部的入口中继后端。
    return relay_.LastFailureCode();
}

std::uint32_t HookRoutingFailureCode(const HookRoutingAdapter& adapter) noexcept {
    return adapter.LastFailureCode();
}

}  // namespace isaac::runtime
