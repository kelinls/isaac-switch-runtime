#pragma once

#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/engine_memory_guard.hpp"

#include <cstdint>

namespace isaac::runtime {

// ============================================================================
// 帧时间归因探针（2026-09-13，"物品信息显示时卡顿"）
// ============================================================================
//
// 目的：把"卡顿"拆成可判读的数字，而不是靠手感。同一个会话内部做 A/B：
//
//   * 预热 `kWarmupFrames` 帧（进图、加载、EID 首次建描述），不计入任何一相；
//   * 之后每 `kPhaseFrames` 帧切换一次缓存（奇数次 = 缓存开，偶数次 = 缓存关），
//     共 `kPhaseCount` 相 → 两相各 `kPhaseCount / 2 × kPhaseFrames` 帧；
//   * 两相记录同样的量：帧间隔（总/最大/超 20 ms/超 33 ms）、派发耗时、
//     `EngineGuardProbe*` 的尝试数/系统调用数/系统调用 tick/缓存命中数、绘制调用数。
//
// **刻意不引用 libnx**：时间戳走 `engine_memory_guard.hpp` 的 `EngineGuardTick()`（`mrs cntvct_el0`），
// 频率走 `EngineGuardTicksPerSecond()`（`mrs cntfrq_el0`）。原来的 `svcSleepThread` 标定会把
// `lib/nx/nx.h` 拉进来，与同一编译单元里的 `<switch.h>` 冲突（`SplConfigItem` 重定义、
// `MemoryInfo` 字段顺序不同），所以这里改成直接读频率寄存器，不睡、不引平台头。
//
// **只在"真的派发过受管 POST_RENDER"的帧上计数**：`ShouldDispatchManagedCallbacks()` 已经
// 要求房间与玩家都成立，所以这条口径天然等于"在局内、EID 正在工作"，加载界面与主菜单
// 不会污染样本。
//
// 一相结束即触发一次 `svcBreak`（探针构建唯一允许的报错），把两相的数字装进寄存器；
// 解码器见 `tools/decode_engine_probe_payload.py`。
//
// 预算（按 60 fps）：预热 900 帧 = 15 s；8 相 × 450 帧 = 60 s；总计约 75 s。
constexpr std::uint32_t kEngineProbeWarmupFrames = 900;
constexpr std::uint32_t kEngineProbePhaseFrames = 450;
constexpr std::uint32_t kEngineProbePhaseCount = 8;
// 兜底：帧数没跑满（暂停、退出到菜单、卡死）时按时间结束会话，保证一轮真机一定有报告。
constexpr std::uint64_t kEngineProbeFallbackMs = 300ULL * 1000ULL;

#if defined(EXL_PROBE_BREAK)

struct EngineFramePhaseStats {
    std::uint64_t frames{0};
    std::uint64_t frameTicks{0};
    std::uint64_t maxFrameTicks{0};
    std::uint64_t framesOver20ms{0};
    std::uint64_t framesOver33ms{0};
    std::uint64_t dispatchTicks{0};
    std::uint64_t attempts{0};
    std::uint64_t syscalls{0};
    std::uint64_t syscallTicks{0};
    std::uint64_t cacheHits{0};
    std::uint64_t drawCalls{0};
};

struct EngineFrameProbeState {
    bool initialized{false};
    std::uint64_t ticksPerMs{19200};  // 先用 Tegra X1 的 19.2 MHz；初始化时按 cntfrq_el0 覆盖
    std::uint64_t firstTick{0};
    std::uint64_t lastFrameTick{0};
    std::uint32_t managedFrames{0};
    std::uint32_t phaseIndex{0};
    bool phaseOpen{false};
    bool pendingBreak{false};
    std::uint32_t triggerReason{0};
    EngineFramePhaseStats cacheOn{};
    EngineFramePhaseStats cacheOff{};
    std::uint64_t phaseBaseDispatchTicks{0};
    // 相基线（用于算增量）
    std::uint64_t baseAttempts{0};
    std::uint64_t baseSyscalls{0};
    std::uint64_t baseSyscallTicks{0};
    std::uint64_t baseCacheHits{0};
    std::uint32_t baseDrawCalls{0};
    // 当前帧的派发计时
    std::uint64_t dispatchStartedTick{0};
    bool dispatchOpen{false};
};

inline EngineFrameProbeState& EngineFrameProbe() noexcept {
    static EngineFrameProbeState state;
    return state;
}

inline void EngineFrameProbeCalibrate(EngineFrameProbeState& state) noexcept {
    // 直接读 `cntfrq_el0`（Hz），比"睡 1 ms 再数 tick"更准，也不需要 libnx。
    const std::uint64_t perSecond = EngineGuardTicksPerSecond();
    state.ticksPerMs = perSecond / 1000ULL != 0 ? perSecond / 1000ULL : 19200ULL;
}

inline void EngineFrameProbeBeginPhase(EngineFrameProbeState& state, std::uint32_t index) {
    state.phaseIndex = index;
    state.phaseOpen = true;
    EngineFramePhaseStats& stats =
        (index % 2U) == 0U ? state.cacheOn : state.cacheOff;
    stats = EngineFramePhaseStats{};
    // 奇数次相 = 缓存开，偶数次相 = 缓存关（0 号相是缓存开）。
    EngineGuardSetCacheEnabled((index % 2U) == 0U);
    state.baseAttempts = EngineGuardProbeAttempts().load(std::memory_order_relaxed);
    state.baseSyscalls = EngineGuardProbeSyscalls().load(std::memory_order_relaxed);
    state.baseSyscallTicks = EngineGuardProbeSyscallTicks().load(std::memory_order_relaxed);
    state.baseCacheHits = EngineGuardProbeCacheHits().load(std::memory_order_relaxed);
    state.baseDrawCalls = ApiSequenceProbeSnapshot().drawCalls;
}

inline void EngineFrameProbeEndPhase(EngineFrameProbeState& state) {
    EngineFramePhaseStats& stats =
        (state.phaseIndex % 2U) == 0U ? state.cacheOn : state.cacheOff;
    stats.attempts =
        EngineGuardProbeAttempts().load(std::memory_order_relaxed) - state.baseAttempts;
    stats.syscalls =
        EngineGuardProbeSyscalls().load(std::memory_order_relaxed) - state.baseSyscalls;
    stats.syscallTicks =
        EngineGuardProbeSyscallTicks().load(std::memory_order_relaxed) - state.baseSyscallTicks;
    stats.cacheHits =
        EngineGuardProbeCacheHits().load(std::memory_order_relaxed) - state.baseCacheHits;
    stats.drawCalls = ApiSequenceProbeSnapshot().drawCalls - state.baseDrawCalls;
    stats.dispatchTicks = state.phaseBaseDispatchTicks;
    state.phaseBaseDispatchTicks = 0;
    state.phaseOpen = false;
}

// 受管 `POST_RENDER` 派发的入口。**这就是帧边界**：只有它真的跑起来（房间与玩家都成立）
// 才累加样本，所以加载界面与主菜单不进统计。
inline void EngineFrameProbeOnManagedDispatch() noexcept {
    EngineFrameProbeState& state = EngineFrameProbe();
    const std::uint64_t now = EngineGuardTick();
    if (!state.initialized) {
        state.initialized = true;
        state.firstTick = now;
        EngineFrameProbeCalibrate(state);
        state.lastFrameTick = EngineGuardTick();
        return;
    }
    const std::uint64_t rawDelta = now - state.lastFrameTick;
    state.lastFrameTick = now;
    ++state.managedFrames;

    // 兜底时间：跑满 `kEngineProbeFallbackTicks` 仍未完成所有相，就带着现有读数结束会话。
    if (state.triggerReason == 0U &&
        now - state.firstTick > kEngineProbeFallbackMs * state.ticksPerMs) {
        state.triggerReason = 2U;
        state.pendingBreak = true;
        return;
    }
    if (state.managedFrames <= kEngineProbeWarmupFrames) {
        // 预热期始终保持"修复前"的行为，让两条口径从同一状态起跑。
        EngineGuardSetCacheEnabled(false);
        return;
    }
    if (!state.phaseOpen) {
        EngineFrameProbeBeginPhase(state, 0);
        return;
    }
    EngineFramePhaseStats& stats =
        (state.phaseIndex % 2U) == 0U ? state.cacheOn : state.cacheOff;
    ++stats.frames;
    stats.frameTicks += rawDelta;
    if (rawDelta > stats.maxFrameTicks) {
        stats.maxFrameTicks = rawDelta;
    }
    const std::uint64_t ms = state.ticksPerMs == 0 ? 0 : rawDelta / state.ticksPerMs;
    if (ms > 20ULL) {
        ++stats.framesOver20ms;
    }
    if (ms > 33ULL) {
        ++stats.framesOver33ms;
    }
    if (stats.frames >= kEngineProbePhaseFrames) {
        EngineFrameProbeEndPhase(state);
        if (state.phaseIndex + 1U >= kEngineProbePhaseCount) {
            state.triggerReason = 1U;
            state.pendingBreak = true;
            return;
        }
        EngineFrameProbeBeginPhase(state, state.phaseIndex + 1U);
    }
}

inline void EngineFrameProbeOnDispatchBegin() noexcept {
    EngineFrameProbeState& state = EngineFrameProbe();
    state.dispatchStartedTick = EngineGuardTick();
    state.dispatchOpen = true;
}

inline void EngineFrameProbeOnDispatchEnd() noexcept {
    EngineFrameProbeState& state = EngineFrameProbe();
    if (!state.dispatchOpen) {
        return;
    }
    state.dispatchOpen = false;
    const std::uint64_t elapsed = EngineGuardTick() - state.dispatchStartedTick;
    if (state.phaseOpen) {
        state.phaseBaseDispatchTicks += elapsed;
    }
}

inline bool EngineFrameProbeTakeBreak() noexcept {
    EngineFrameProbeState& state = EngineFrameProbe();
    if (!state.pendingBreak) {
        return false;
    }
    state.pendingBreak = false;
    return true;
}

// 崩溃报告负载快照（判定字段下标与解码器一一对应）。
struct EngineFrameProbeSnapshot {
    std::uint32_t triggerReason{0};
    std::uint32_t phasesDone{0};
    std::uint64_t ticksPerMs{19200};
    std::uint64_t managedFrames{0};
    EngineFramePhaseStats cacheOn{};
    EngineFramePhaseStats cacheOff{};
    std::uint32_t lastApiPrimary{0};
    std::uint32_t lastApiCalls{0};
    std::uint32_t filterMask{0};
    std::uint32_t postRenderCount{0};
    std::uint32_t postUpdateCount{0};
    // `EID.GameRenderCount` 的读取原因码与取值（原因码语义见
    // `LuaRuntime::ReadLuaTableNumber`：0 成功 / 1 全局不是表 / 2 没这个字段 / 3 不是 number）。
    std::uint32_t eidRenderReason{0};
    std::uint32_t eidRenderCount{0};
};

inline EngineFrameProbeSnapshot EngineFrameProbeCapture(std::uint32_t postRenderCount,
                                                        std::uint32_t postUpdateCount,
                                                        std::uint32_t eidRenderReason,
                                                        std::uint32_t eidRenderCount) noexcept {
    EngineFrameProbeSnapshot snapshot{};
    EngineFrameProbeState& state = EngineFrameProbe();
    snapshot.triggerReason = state.triggerReason;
    snapshot.phasesDone = state.phaseOpen ? state.phaseIndex : state.phaseIndex + 1U;
    snapshot.ticksPerMs = state.ticksPerMs;
    snapshot.managedFrames = state.managedFrames;
    snapshot.cacheOn = state.cacheOn;
    snapshot.cacheOff = state.cacheOff;
    const ApiSequenceProbe sequence = ApiSequenceProbeSnapshot();
    snapshot.lastApiPrimary = sequence.lastPrimary;
    snapshot.lastApiCalls = sequence.lastPrimaryCalls;
    snapshot.filterMask = static_cast<std::uint32_t>(sequence.filterMask & 0xFFFFU);
    snapshot.postRenderCount = postRenderCount;
    snapshot.postUpdateCount = postUpdateCount;
    snapshot.eidRenderReason = eidRenderReason;
    snapshot.eidRenderCount = eidRenderCount;
    return snapshot;
}

#else

// 生产构建：这些入口全部编译成空操作，连一次分支都不留。
struct EngineFrameProbeSnapshot {};
inline void EngineFrameProbeOnManagedDispatch() noexcept {}
inline void EngineFrameProbeOnDispatchBegin() noexcept {}
inline void EngineFrameProbeOnDispatchEnd() noexcept {}
inline bool EngineFrameProbeTakeBreak() noexcept { return false; }

#endif

} // namespace isaac::runtime
