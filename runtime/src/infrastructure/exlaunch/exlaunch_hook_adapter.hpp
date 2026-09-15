#pragma once

#include "ports/hook_port.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Bridges IHookPort onto the verified legacy installers in hook_manager.cpp.
// Each installer performs its own instruction and relay-slot verification, so a
// hook is only reported as installed after it is actually reachable.
class ExlaunchHookAdapter final : public IHookPort {
public:
    [[nodiscard]] Status Install(HookId id, const HookTarget& target) noexcept override;
    [[nodiscard]] bool IsInstalled(HookId id) const noexcept override;

private:
    static constexpr std::size_t kHookCount = static_cast<std::size_t>(HookId::Count);
    std::array<bool, kHookCount> installed_{};
};

} // namespace isaac::runtime
