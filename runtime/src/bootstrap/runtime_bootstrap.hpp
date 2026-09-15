#pragma once

#include "composition/runtime_dependencies.hpp"
#include "composition/runtime_kernel.hpp"
#include "domain/runtime/status.hpp"

namespace isaac::runtime {

// Early event identifiers published by bootstrap. They are stable so a later
// release can keep parsing logs written by this one.
inline constexpr std::uint32_t kEventModuleEntered = 0x01000001;

// The layered Runtime entry. `Start()` uses the compile-time build identity and
// the kernel's own early sink; `Start(dependencies)` is the injectable form
// used by host tests and by the composition root once real ports exist.
//
// This runs at the top of exl_main and only records the entry boundary: module
// scanning, hooks, Lua and persistence keep their existing control flow.
class RuntimeBootstrap {
public:
    [[nodiscard]] static Status Start() noexcept;
    [[nodiscard]] static Status Start(const RuntimeDependencies& dependencies) noexcept;
};

} // namespace isaac::runtime
