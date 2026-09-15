#pragma once

#include "application/callback/callback_invoker.hpp"
#include "application/callback/callback_registry.hpp"
#include "domain/callback/callback_descriptor.hpp"
#include "domain/runtime/thread_affinity.hpp"

#include <cstdint>

namespace isaac::runtime {

struct DispatchReport {
    std::uint32_t invoked{0};
    std::uint32_t failed{0};
    std::uint32_t skippedAffinity{0};
};

// `Isaac.GetFrameCount()` 的帧计数真源。
//
// 生产路径里 `LuaRuntime::DispatchPostUpdate()` 每个游戏 update 调用一次
// `CallbackDispatcher::Dispatch(kCallbackPostUpdate, ...)`，所以"派发过多少次
// MC_POST_UPDATE"就是"自本次加载以来过了多少帧"。计数器放在派发层而不是 Lua 层，是因为
// 只有一个地方派发该阶段：把累加放在派发入口，就等于每个 update 恰好加一次，不需要新线程、
// 不需要任何时钟；宿主测试也可以直接调用 `LuaRuntime::DispatchPostUpdate()` 观察它递增。
//
// 这是**近似**：它数的是 Runtime 的 update 派发次数，不是引擎自己的游戏内计时器
// （引擎时钟偏移尚未定位，见 `isaac_api.cpp` 的 `Isaac.GetTime`）。
class ManagedFrameClock {
public:
    // 每次派发 MC_POST_UPDATE 时调用一次（dispatch 单线程路径，无锁）。
    static void MarkPostUpdateDispatch() noexcept;

    // 新的 Lua 会话开始时归零，保证计数是"自本次加载以来"的帧数。
    static void Reset() noexcept;

    [[nodiscard]] static std::uint64_t Count() noexcept;
};

// Synchronous dispatch for one callback id. One Mod's failure never stops the
// others, and a callback registered for another thread is skipped rather than
// run in the wrong context.
class CallbackDispatcher {
public:
    CallbackDispatcher(const CallbackRegistry& registry, ICallbackInvoker& invoker) noexcept
        : registry_(registry), invoker_(invoker) {}

    // Dispatching `kCallbackPostUpdate` advances `ManagedFrameClock` by one, so
    // this entry point is also the frame tick the `Isaac` facade reads.
    [[nodiscard]] DispatchReport Dispatch(CallbackId id, ThreadAffinity current) noexcept;
    [[nodiscard]] DispatchReport DispatchOwner(ModHandle owner, CallbackId id,
                                               ThreadAffinity current) noexcept;

private:
    [[nodiscard]] static bool AffinityMatches(ThreadAffinity declared,
                                              ThreadAffinity current) noexcept {
        // `current == Any` means the caller cannot name its context, so no
        // restriction is applied; otherwise the declared affinity must match.
        return current == ThreadAffinity::Any || declared == ThreadAffinity::Any ||
               declared == current;
    }

    const CallbackRegistry& registry_;
    ICallbackInvoker& invoker_;
};

} // namespace isaac::runtime
