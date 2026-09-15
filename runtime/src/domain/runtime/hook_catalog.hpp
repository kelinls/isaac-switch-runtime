#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace isaac::runtime {

// Runtime 认识的全部"侵入点"（挂点）。这张表是**唯一真值源**：ID、显示名、用哪种侵入方式、
// 以及"它是不是加载 Mod 的前置条件"都写在一处，于是
//   * 应用层（`HookInstallService`）按它决定安装顺序与必需/可选策略；
//   * 基础设施（路由适配器）按它选后端；
//   * 读数/日志按它取名字。
//
// **加一个挂点 = 这里加一行**，外加该挂点自己的目标常量（`runtime_constants.hpp`）与回调体。
// 表放在 domain：它只描述事实，不依赖端口、也不依赖基础设施，谁都可以引用它。
enum class HookId : std::uint8_t {
    ManagerUpdate = 0,
    ManagerRender,
    PreGetCollectible,
    ManagerPresent,
    RebuildMountPoints,
    // 游戏开局：引擎在 `Game::Start` / `Game::StartFromSavedState` **返回之后**通知我们。
    // 模组的 `MC_POST_GAME_STARTED` 依赖它（没有它，EID 的开局初始化永远不执行）。
    GameStart,
    Count,
};

inline constexpr std::size_t kHookIdCount = static_cast<std::size_t>(HookId::Count);

// 侵入方式（策略标签）。具体怎么做由基础设施里的适配器实现：
//   LegacyIps  —— Atmosphere 静态中继 IPS + exlaunch 蹦床（历史路径）
//   EntryRelay —— 改写目标函数入口的 16 字节，跳到我们模块里的中继（零占洞）
//   GotSlot    —— 只改写该函数所用 PLT 桩读的那条 GOT 槽（零代码字节）
//   RelaySlot  —— 引擎侧的中继代码（由 IPS 放进游戏模块的代码洞）在"原调用返回之后"
//                 从一个**回调槽**里取出函数指针来调用；本后端只负责把我们的回调指针
//                 写进那个槽（同样零代码字节）。目前只有 `GameStart` 走这条路。
enum class HookBackend : std::uint8_t { LegacyIps, EntryRelay, GotSlot, RelaySlot };

struct HookDescriptor {
    HookId id;
    std::string_view name;   // 读数与日志用；不参与行为判定
    HookBackend backend;
    bool required;           // true = 装不上就不能加载 Mod；其余点失败只记 Skipped
};

// 顺序必须与 `HookId` 一致（测试会钉住这一点）。
inline constexpr std::array<HookDescriptor, kHookIdCount> kHookCatalog = {{
    {HookId::ManagerUpdate, "ManagerUpdate", HookBackend::EntryRelay, true},
    {HookId::ManagerRender, "ManagerRender", HookBackend::EntryRelay, false},
    {HookId::PreGetCollectible, "PreGetCollectible", HookBackend::EntryRelay, false},
    {HookId::ManagerPresent, "ManagerPresent", HookBackend::GotSlot, false},
    {HookId::RebuildMountPoints, "RebuildMountPoints", HookBackend::EntryRelay, false},
    {HookId::GameStart, "GameStart", HookBackend::RelaySlot, false},
}};

// 取登记项。传入 `HookId::Count` 或越界值属于调用方错误，这里退化成第一个登记项
// （不抛异常 —— 这条路在游戏进程里跑，宁可返回保守值也不引入新的失败模式）。
[[nodiscard]] constexpr const HookDescriptor& DescriptorOf(HookId id) noexcept {
    const std::size_t index = static_cast<std::size_t>(id);
    return kHookCatalog[index < kHookIdCount ? index : 0];
}

[[nodiscard]] constexpr HookBackend BackendOf(HookId id) noexcept {
    return DescriptorOf(id).backend;
}

[[nodiscard]] constexpr bool IsRequired(HookId id) noexcept {
    return DescriptorOf(id).required;
}

[[nodiscard]] constexpr std::string_view NameOf(HookId id) noexcept {
    return DescriptorOf(id).name;
}

// 从表派生的后端数组（顺序 = HookId 顺序）。路由适配器用它，避免第二处定义。
[[nodiscard]] constexpr std::array<HookBackend, kHookIdCount> CatalogBackends() noexcept {
    std::array<HookBackend, kHookIdCount> backends{};
    for (std::size_t index = 0; index < kHookIdCount; ++index) {
        backends[index] = kHookCatalog[index].backend;
    }
    return backends;
}

} // namespace isaac::runtime
