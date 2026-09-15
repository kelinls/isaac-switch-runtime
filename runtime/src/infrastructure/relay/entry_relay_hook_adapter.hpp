#pragma once

#include "infrastructure/relay/entry_relay_binding.hpp"
#include "ports/hook_port.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// 零占洞后端：中继体写进本模块自己的竞技场（`source/relay/entry_relay.*`），
// 只改写目标函数入口 16 字节，**不占用游戏映像的任何字节**，因此不再需要
// Atmosphere 的 IPS 静态中继，也不再与金手指抢地址。
class EntryRelayHookAdapter final : public IHookPort {
public:
    [[nodiscard]] Status Install(HookId id, const HookTarget& target) noexcept override;
    [[nodiscard]] bool IsInstalled(HookId id) const noexcept override;

    // 自有诊断接口（不进端口）：最近一次 entry_relay 失败码，0 表示成功。
    [[nodiscard]] std::uint32_t LastFailureCode() const noexcept;

private:
    std::array<bool, kHookIdCount> installed_{};
    std::uint32_t lastFailure_{0};
};

// 安装前把已实现的回调接进入口中继绑定表（Task 4）。定义在同模块的另一 TU，
// 这里给外部链接声明；调用点在 hook_manager.cpp（安装前调用一次）。
void RegisterEntryRelayCallbacks() noexcept;

} // namespace isaac::runtime
