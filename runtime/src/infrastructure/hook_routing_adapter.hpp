#pragma once

#include "domain/runtime/hook_catalog.hpp"
#include "infrastructure/exlaunch/exlaunch_hook_adapter.hpp"
#include "infrastructure/gotslot/got_slot_hook_adapter.hpp"
#include "infrastructure/relay/entry_relay_hook_adapter.hpp"
#include "infrastructure/relayslot/relay_slot_hook_adapter.hpp"
#include "ports/hook_port.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// 每个挂点用哪个后端：**来自 domain 的登记表**（`kHookCatalog`），这里只是给它一个
// 基础设施层习惯的名字，并保持既有调用点继续写 `kHookBackends[index]`。
// 顺序必须与 `HookId` 一致；"哪个点用哪个后端"这件事只在登记表里定义一次。
inline constexpr std::array<HookBackend, kHookIdCount> kHookBackends = CatalogBackends();

// 三个后端并存：按 kHookBackends 路由。上层（HookInstallService）看不到差异。
class HookRoutingAdapter final : public IHookPort {
public:
    [[nodiscard]] Status Install(HookId id, const HookTarget& target) noexcept override;
    [[nodiscard]] bool IsInstalled(HookId id) const noexcept override;

    // 自有诊断接口（不进端口）：转发入口中继最近一次失败码，0 表示成功。
    // 注意：这是本适配器自己的方法，**不是** IHookPort 的虚函数
    // （端口只有 Install / IsInstalled 两个纯虚函数，刻意不为诊断开口子）。
    [[nodiscard]] std::uint32_t LastFailureCode() const noexcept;

private:
    ExlaunchHookAdapter legacy_{};
    EntryRelayHookAdapter relay_{};
    GotSlotHookAdapter gotSlot_{};
    RelaySlotHookAdapter relaySlot_{};
};

// 回读入口中继失败码（供 Task 5 安装报告）。转发给内部 relay_ 适配器。
[[nodiscard]] std::uint32_t HookRoutingFailureCode(const HookRoutingAdapter& adapter) noexcept;

} // namespace isaac::runtime
