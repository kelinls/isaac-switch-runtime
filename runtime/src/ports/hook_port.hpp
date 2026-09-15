#pragma once

#include "domain/runtime/hook_catalog.hpp"
#include "domain/runtime/status.hpp"
#include "ports/module_scanner_port.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// 挂点清单（`HookId` / `kHookIdCount`）与"每个点用哪种侵入方式、是不是必需"登记在
// `domain/runtime/hook_catalog.hpp` —— 那里是唯一真值源，本端口只引用它。
//
// 端口本身只说一句：**应用层按 `HookId` 要一个能力，具体挂哪里、用什么指令守卫都由适配器决定**。
// （`ManagerPresent` 与 `RebuildMountPoints` 原先在 hook_manager.cpp 里被直接安装、绕过本端口与
// `HookInstallReport`，设备侧看不到它们装没装上；现在一律经端口安装。）

// Code window of the verified target module.
struct HookTarget {
    std::uintptr_t base{0};
    std::size_t codeSize{0};
    std::size_t imageSize{0};
    ModuleBuildId buildId{};
};

class IHookPort {
public:
    virtual ~IHookPort() = default;

    // Installs one interception point. Verification failures are reported as a
    // Status failure; a partially installed hook must never report success.
    [[nodiscard]] virtual Status Install(HookId id, const HookTarget& target) noexcept = 0;
    [[nodiscard]] virtual bool IsInstalled(HookId id) const noexcept = 0;
};

} // namespace isaac::runtime
