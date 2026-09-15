#pragma once

#include "domain/runtime/runtime_state.hpp"
#include "domain/runtime/status.hpp"

namespace isaac::runtime {

// The only place a RuntimeState may change. Every transition is explicit so an
// event can never move the Runtime backwards or resurrect a terminal state.
class RuntimeStateMachine {
public:
    [[nodiscard]] static Result<RuntimeState> Transition(RuntimeState from, RuntimeEvent event) noexcept;

    // Applies Transition to the context. On failure the context is untouched
    // and the failure is recorded for diagnostics.
    [[nodiscard]] static Status Apply(struct RuntimeContext& context, RuntimeEvent event) noexcept;
};

} // namespace isaac::runtime
