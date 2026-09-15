#include "application/callback/callback_dispatcher.hpp"

namespace isaac::runtime {
namespace {

// 单线程派发路径上的普通全局：只有游戏线程会调用 `Dispatch`，所以既不需要锁，也不能用
// `thread_local`（本项目在真机上已被 `thread_local` 坑过一次，见 `lua_runtime.cpp` 的注释）。
std::uint64_t g_PostUpdateDispatchCount = 0;

} // namespace

void ManagedFrameClock::MarkPostUpdateDispatch() noexcept {
    ++g_PostUpdateDispatchCount;
}

void ManagedFrameClock::Reset() noexcept {
    g_PostUpdateDispatchCount = 0;
}

std::uint64_t ManagedFrameClock::Count() noexcept {
    return g_PostUpdateDispatchCount;
}

DispatchReport CallbackDispatcher::Dispatch(CallbackId id, ThreadAffinity current) noexcept {
    DispatchReport report{};
    if (id == kCallbackPostUpdate) {
        // 每个 update 恰好一次：这是 `Isaac.GetFrameCount()` 的唯一累加点。
        ManagedFrameClock::MarkPostUpdateDispatch();
    }
    const std::size_t total = registry_.CountOf(id);
    for (std::size_t index = 0; index < total; ++index) {
        const CallbackDescriptor* descriptor = registry_.At(id, index);
        if (descriptor == nullptr) {
            break;
        }
        if (!AffinityMatches(descriptor->affinity, current)) {
            ++report.skippedAffinity;
            continue;
        }
        if (invoker_.Invoke(*descriptor).ok()) {
            ++report.invoked;
        } else {
            ++report.failed;
        }
    }
    return report;
}

DispatchReport CallbackDispatcher::DispatchOwner(ModHandle owner, CallbackId id,
                                                 ThreadAffinity current) noexcept {
    DispatchReport report{};
    for (std::size_t index = 0; index < registry_.Count(); ++index) {
        const CallbackDescriptor* descriptor = registry_.AtIndex(index);
        if (descriptor == nullptr || descriptor->id != id || descriptor->owner != owner) {
            continue;
        }
        if (!AffinityMatches(descriptor->affinity, current)) {
            ++report.skippedAffinity;
            continue;
        }
        if (invoker_.Invoke(*descriptor).ok()) {
            ++report.invoked;
        } else {
            ++report.failed;
        }
    }
    return report;
}

} // namespace isaac::runtime
