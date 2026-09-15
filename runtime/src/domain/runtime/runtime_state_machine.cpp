#include "domain/runtime/runtime_state_machine.hpp"

#include "domain/runtime/runtime_context.hpp"

namespace isaac::runtime {
namespace {

struct Rule {
    RuntimeState from;
    RuntimeEvent event;
    RuntimeState to;
};

// Mirrors the startup diagram in the architecture design one-to-one. Adding a
// state without a rule here makes it unreachable, which the unit test enforces;
// any additional failure edge requires a design update first.
constexpr Rule kRules[] = {
    {RuntimeState::Cold, RuntimeEvent::ModuleEntered, RuntimeState::ModuleEntered},
    {RuntimeState::ModuleEntered, RuntimeEvent::HeapReady, RuntimeState::HeapReady},
    {RuntimeState::HeapReady, RuntimeEvent::PlatformReady, RuntimeState::PlatformReady},
    {RuntimeState::PlatformReady, RuntimeEvent::ConstructorsReady, RuntimeState::ConstructorsReady},
    {RuntimeState::ConstructorsReady, RuntimeEvent::RuntimeEntered, RuntimeState::RuntimeEntered},
    {RuntimeState::RuntimeEntered, RuntimeEvent::WorkerStarting, RuntimeState::WorkerStarting},
    {RuntimeState::WorkerStarting, RuntimeEvent::Scanning, RuntimeState::Scanning},
    {RuntimeState::Scanning, RuntimeEvent::HooksInstalling, RuntimeState::HooksInstalling},
    {RuntimeState::HooksInstalling, RuntimeEvent::RuntimeReady, RuntimeState::RuntimeReady},
    {RuntimeState::RuntimeReady, RuntimeEvent::ModLoadStarted, RuntimeState::ModLoading},
    {RuntimeState::ModLoading, RuntimeEvent::ModLoaded, RuntimeState::ModReady},
    {RuntimeState::ModReady, RuntimeEvent::ModRunStarted, RuntimeState::Running},

    {RuntimeState::Cold, RuntimeEvent::FatalPrerequisiteFailed, RuntimeState::Disabled},
    {RuntimeState::ModuleEntered, RuntimeEvent::InitializationFailed, RuntimeState::Disabled},
    {RuntimeState::Scanning, RuntimeEvent::ModuleMismatch, RuntimeState::Disabled},
    {RuntimeState::HooksInstalling, RuntimeEvent::RequiredHookFailed, RuntimeState::Disabled},
    {RuntimeState::ModLoading, RuntimeEvent::ModLoadFailed, RuntimeState::ModDisabled},
    {RuntimeState::Running, RuntimeEvent::ModRuntimeFailed, RuntimeState::ModDisabled},
};

} // namespace

Result<RuntimeState> RuntimeStateMachine::Transition(RuntimeState from, RuntimeEvent event) noexcept {
    if (IsTerminal(from)) {
        return Status{StatusCode::InvalidState};
    }
    for (const Rule& rule : kRules) {
        if (rule.from == from && rule.event == event) {
            return rule.to;
        }
    }
    return Status{StatusCode::InvalidState};
}

Status RuntimeStateMachine::Apply(RuntimeContext& context, RuntimeEvent event) noexcept {
    const Result<RuntimeState> next = Transition(context.state, event);
    if (!next.ok()) {
        context.lastFailure = next.status();
        return next.status();
    }
    context.state = next.value();
    ++context.enteredStateCount;
    return Status::Ok();
}

const char* ToString(RuntimeState state) noexcept {
    switch (state) {
        case RuntimeState::Cold: return "Cold";
        case RuntimeState::ModuleEntered: return "ModuleEntered";
        case RuntimeState::HeapReady: return "HeapReady";
        case RuntimeState::PlatformReady: return "PlatformReady";
        case RuntimeState::ConstructorsReady: return "ConstructorsReady";
        case RuntimeState::RuntimeEntered: return "RuntimeEntered";
        case RuntimeState::WorkerStarting: return "WorkerStarting";
        case RuntimeState::Scanning: return "Scanning";
        case RuntimeState::HooksInstalling: return "HooksInstalling";
        case RuntimeState::RuntimeReady: return "RuntimeReady";
        case RuntimeState::ModLoading: return "ModLoading";
        case RuntimeState::ModReady: return "ModReady";
        case RuntimeState::Running: return "Running";
        case RuntimeState::Disabled: return "Disabled";
        case RuntimeState::ModDisabled: return "ModDisabled";
        case RuntimeState::Count: break;
    }
    return "Unknown";
}

const char* ToString(RuntimeEvent event) noexcept {
    switch (event) {
        case RuntimeEvent::ModuleEntered: return "ModuleEntered";
        case RuntimeEvent::HeapReady: return "HeapReady";
        case RuntimeEvent::PlatformReady: return "PlatformReady";
        case RuntimeEvent::ConstructorsReady: return "ConstructorsReady";
        case RuntimeEvent::RuntimeEntered: return "RuntimeEntered";
        case RuntimeEvent::WorkerStarting: return "WorkerStarting";
        case RuntimeEvent::Scanning: return "Scanning";
        case RuntimeEvent::HooksInstalling: return "HooksInstalling";
        case RuntimeEvent::RuntimeReady: return "RuntimeReady";
        case RuntimeEvent::ModLoadStarted: return "ModLoadStarted";
        case RuntimeEvent::ModLoaded: return "ModLoaded";
        case RuntimeEvent::ModRunStarted: return "ModRunStarted";
        case RuntimeEvent::FatalPrerequisiteFailed: return "FatalPrerequisiteFailed";
        case RuntimeEvent::InitializationFailed: return "InitializationFailed";
        case RuntimeEvent::ModuleMismatch: return "ModuleMismatch";
        case RuntimeEvent::RequiredHookFailed: return "RequiredHookFailed";
        case RuntimeEvent::ModLoadFailed: return "ModLoadFailed";
        case RuntimeEvent::ModRuntimeFailed: return "ModRuntimeFailed";
        case RuntimeEvent::Count: break;
    }
    return "Unknown";
}

} // namespace isaac::runtime
