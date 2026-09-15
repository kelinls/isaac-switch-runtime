#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// **API 序列观测**：记录"本次会话里哪些 Lua API 被调用过"。
//
// 为什么需要它（2026-09-12 定案）：判断"Mod 走到了哪一支代码"有两种办法 —— 反查 Mod 的 Lua
// 状态（读它的全局/字段），或者看它**调用过我们哪些 API**。前者先后两次被证伪：
//   * `ReadLuaGlobalNumber("EID.GameRenderCount")` 答"没有"，而同一份报告里 update/render 回调
//     计数是 900/878；
//   * 换成 `lua_getglobal("EID") + lua_getfield`，答"EID 不是表"，而屏幕上已经出现过 EID 画的图标。
// 后者（本文件）只依赖**我们自己的**入口调用计数 —— 与诊断字里那批计数器同源，已被反复验证可靠。
//
// 一次真机因此可以同时回答"它走到哪了"：
//   * 调过 `Game.GetLevel` 但没调过 `Isaac.FindInRadius` ⇒ 卡在"选描述"之前；
//   * 调过 `Font.DrawString*` ⇒ 描述**构建成功**，问题在绘制/坐标；
//   * 一个都没调 ⇒ `OnRender` 没进到那些分支。
//
// **实现刻意全部放在头文件里**（`inline`）：宿主 harness 按固定清单链接运行时源码，新增一个
// `.cpp` 就要同步改多处清单；而这个探针只是"记位 + 读快照"，没有单独成 TU 的价值。
//
// 位表（低 64 位 = 渲染/查询主干；高 64 位 = 其余可能在提前 return 路径上的调用）：
//   bits 0..15  `Game`：0 `IsPaused`、1 `IsGreedMode`、2 `GetLevel`、3 `GetItemPool`、
//               4 `GetRoom`、5 `GetNumPlayers`、6 `GetFrameCount`、7 `GetSeeds`、8 `GetVictoryLap`
//   bits 16..31 `Isaac`：16 `GetPlayer`、17 `GetItemConfig`、18 `FindInRadius`、19 `FindByType`、
//               20 `CountEnemies`、21 `CountBosses`
//   bits 32..47 `Font`：32 `DrawString`、33 `DrawStringScaled`、34 `DrawStringUTF8`、
//               35 `DrawStringScaledUTF8`
//   bits 48..63 `Sprite`：48 `Render`、49 `RenderLayer`、50 `GetTexel`
//   —— 高位（secondary）——
//   bits 0..15  `EntityPlayer`：0 `HasCollectible`、1 `GetPlayerType`、2 `GetData`
//   bits 16..31 `Room`/`Level`/`Seeds`：16 `Room.GetType`、17 `Level.GetStage`、
//               18 `Seeds.IsCustomRun`
//   bits 32..47 `Input`：32 `IsActionPressed`
//   filterMask（第三张掩码，2026-09-12 第八轮，EID 的"实体要不要进描述表"过滤器链）：
//     0 `Entity.GetData`、1 `Entity.GetSprite`、2 `Entity:ToPickup`、
//     3 `ItemConfig.GetCollectible`、4 `EntityPickup.IsShopItem`、5 `Global.GetPtrHash`
struct ApiSequenceProbe {
    std::uint64_t mainMask{0};
    std::uint64_t secondaryMask{0};
    std::uint32_t drawCalls{0};
    std::uint32_t drawBytes{0};
    // `ItemConfig:*` 入口的命中/未命中次数（EID 的 hasDescription 依赖它）。
    std::uint32_t itemConfigFound{0};
    std::uint32_t itemConfigMissing{0};
    // 过滤器链掩码（位表见上）。
    std::uint64_t filterMask{0};
    // "**最后**调用的主掩码位"与调用总次数（2026-09-12 第七轮）。掩码只能回答"调用过没有"，
    // 回答不了"报错那一刻走到哪个 API" —— 而后者正是现在缺的那一块。
    std::uint32_t lastPrimary{0xFFFFFFFFu};
    std::uint32_t lastPrimaryCalls{0};
};

namespace detail {

struct ApiSequenceState {
    std::atomic<std::uint64_t> mainMask{0};
    std::atomic<std::uint64_t> secondaryMask{0};
    std::atomic<std::uint32_t> drawCalls{0};
    std::atomic<std::uint32_t> drawBytes{0};
    std::atomic<std::uint32_t> itemConfigFound{0};
    std::atomic<std::uint32_t> itemConfigMissing{0};
    std::atomic<std::uint64_t> filterMask{0};
    std::atomic<std::uint32_t> lastPrimary{0xFFFFFFFFu};
    std::atomic<std::uint32_t> lastPrimaryCalls{0};
};

// 函数内 `static`：C++11 起初始化线程安全，且整个模块只有一个实例。
inline ApiSequenceState& ApiSequence() noexcept {
    static ApiSequenceState state;
    return state;
}

} // namespace detail

// 低 64 位的位号（家族组号 × 16 + 组内序号）。
inline void RecordApiSequence(std::uint32_t bit) noexcept {
    if (bit >= 64) {
        return;
    }
    detail::ApiSequence().mainMask.fetch_or(1ULL << bit, std::memory_order_relaxed);
    // 顺带记"最后调用的主掩码位"（只花一次 relaxed 存储，不需要新的负载字段）。
    detail::ApiSequence().lastPrimary.store(bit, std::memory_order_relaxed);
    detail::ApiSequence().lastPrimaryCalls.fetch_add(1, std::memory_order_relaxed);
}

// 高 64 位的位号。
inline void RecordApiSequenceSecondary(std::uint32_t bit) noexcept {
    if (bit >= 64) {
        return;
    }
    detail::ApiSequence().secondaryMask.fetch_or(1ULL << bit, std::memory_order_relaxed);
}

// `Font:DrawString*` 的次数与累计文本字节数（与位掩码互证）。
inline void RecordApiSequenceDraw(std::size_t textBytes) noexcept {
    detail::ApiSequence().drawCalls.fetch_add(1, std::memory_order_relaxed);
    detail::ApiSequence().drawBytes.fetch_add(static_cast<std::uint32_t>(textBytes),
                                              std::memory_order_relaxed);
}

// `ItemConfig:GetCollectible` 一类入口的**命中/未命中计数**：EID 的 `main.lua:1473`
// 用 `EID:hasDescription(entity)`（内部走它）决定实体要不要进描述表；恒未命中的话
// `descriptionsToPrint` 永远为空、`renderString` 永远不会被调用。
inline void RecordItemConfigResult(bool found) noexcept {
    if (found) {
        detail::ApiSequence().itemConfigFound.fetch_add(1, std::memory_order_relaxed);
    } else {
        detail::ApiSequence().itemConfigMissing.fetch_add(1, std::memory_order_relaxed);
    }
}

// 过滤器链的位号（0..5，见上方位表）。
inline void RecordFilterChain(std::uint32_t bit) noexcept {
    if (bit >= 64) {
        return;
    }
    detail::ApiSequence().filterMask.fetch_or(1ULL << bit, std::memory_order_relaxed);
}

[[nodiscard]] inline ApiSequenceProbe ApiSequenceProbeSnapshot() noexcept {
    detail::ApiSequenceState& state = detail::ApiSequence();
    ApiSequenceProbe probe{};
    probe.mainMask = state.mainMask.load(std::memory_order_acquire);
    probe.secondaryMask = state.secondaryMask.load(std::memory_order_acquire);
    probe.drawCalls = state.drawCalls.load(std::memory_order_acquire);
    probe.drawBytes = state.drawBytes.load(std::memory_order_acquire);
    probe.itemConfigFound = state.itemConfigFound.load(std::memory_order_acquire);
    probe.itemConfigMissing = state.itemConfigMissing.load(std::memory_order_acquire);
    probe.filterMask = state.filterMask.load(std::memory_order_acquire);
    probe.lastPrimary = state.lastPrimary.load(std::memory_order_acquire);
    probe.lastPrimaryCalls = state.lastPrimaryCalls.load(std::memory_order_acquire);
    return probe;
}

} // namespace isaac::runtime
