#pragma once

#include "ports/hook_port.hpp"

#include <array>
#include <cstddef>

namespace isaac::runtime {

// M2b 后端：**只改数据、零代码字节** —— 把目标函数所用 PLT 桩读到的那条 GOT 槽，
// 改写成我们自己的拦截函数。目前只有 `ManagerPresent`（`Present` 调用点）走这条路。
//
// 与另两个后端的区别：它不改游戏指令，所以目标调用点的字节必须**保持原样**（那份 IPS 要先删）；
// 拦截函数自己按调用方过滤，保证只有原来那一处调用会派发 `MC_POST_RENDER`
// （这条 GOT 槽被 43 处调用共用，不过滤就不是等价迁移了）。
class GotSlotHookAdapter final : public IHookPort {
public:
    [[nodiscard]] Status Install(HookId id, const HookTarget& target) noexcept override;
    [[nodiscard]] bool IsInstalled(HookId id) const noexcept override;

private:
    std::array<bool, kHookIdCount> installed_{};
};

} // namespace isaac::runtime
