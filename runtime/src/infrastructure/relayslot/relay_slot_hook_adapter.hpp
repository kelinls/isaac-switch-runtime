#pragma once

#include "ports/hook_port.hpp"

#include <array>
#include <cstddef>

namespace isaac::runtime {

// 后端：**引擎侧的中继代码已经放好，我们只往它的回调槽里写一个函数指针**（零代码字节）。
//
// 与另两个后端的分工：
//   * 代码洞里的中继（由 `atmosphere/nro_patches/isaac-repentance-lifecycle-relay` 的 IPS 写入）
//     负责"在合适的时机调用槽里的指针"，时机是**原函数调用返回之后**；
//   * 本适配器负责"把指针写进去"，并在写之前校验调用点与中继代码的字节形态，
//     确认放代码洞的那份补丁确实是我们要的那一份（形态不符就拒绝安装，宁可少一个事件，
//     也不跳进一段来路不明的代码）。
//
// 目前只有 `GameStart` 走这条路。
class RelaySlotHookAdapter final : public IHookPort {
public:
    [[nodiscard]] Status Install(HookId id, const HookTarget& target) noexcept override;
    [[nodiscard]] bool IsInstalled(HookId id) const noexcept override;

private:
    std::array<bool, kHookIdCount> installed_{};
};

} // namespace isaac::runtime
