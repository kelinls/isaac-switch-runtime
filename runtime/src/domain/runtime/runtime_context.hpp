#pragma once

#include "domain/runtime/capability_set.hpp"
#include "domain/runtime/runtime_state.hpp"
#include "domain/runtime/status.hpp"

#include <cstdint>

namespace isaac::runtime {

// Aggregates everything the Runtime knows about itself. It owns no platform
// handle and performs no work; services read it and the state machine writes
// the state.
struct RuntimeContext {
    RuntimeState state{RuntimeState::Cold};
    CapabilitySet capabilities{};
    Status lastFailure{};
    std::uint64_t buildId{0};
    std::uint32_t enteredStateCount{0};

    [[nodiscard]] bool running() const noexcept { return state == RuntimeState::Running; }
    [[nodiscard]] bool usable() const noexcept { return !IsTerminal(state); }
    [[nodiscard]] bool has(Capability capability) const noexcept {
        return capabilities.Has(capability);
    }
};

} // namespace isaac::runtime
