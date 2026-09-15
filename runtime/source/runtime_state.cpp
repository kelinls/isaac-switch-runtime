#include "runtime_state.hpp"

RuntimeState Step(RuntimeState state, const RuntimeContext& context) {
    if (state == RuntimeState::Disabled || state == RuntimeState::Ready) {
        return state;
    }
    if (context.fatalFailure || context.buildMismatch ||
        (context.titleChecked && !context.titleOk)) {
        return RuntimeState::Disabled;
    }
    if (state == RuntimeState::Cold) {
        return context.titleChecked ? RuntimeState::WaitingForModule : RuntimeState::Cold;
    }
    if (!context.moduleFound) {
        return RuntimeState::WaitingForModule;
    }
    if (!context.hookAttempted || !context.hookSucceeded) {
        return RuntimeState::Disabled;
    }
    return RuntimeState::Ready;
}
