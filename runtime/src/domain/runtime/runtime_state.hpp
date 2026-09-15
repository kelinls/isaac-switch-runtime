#pragma once

#include <cstdint>

namespace isaac::runtime {

// Full startup lifecycle from the Runtime architecture design (section 7.1).
// The legacy four-state RuntimeState stays in runtime/source until the
// compatibility layer is removed; this enum is the single target model.
enum class RuntimeState : std::uint8_t {
    Cold = 0,
    ModuleEntered,
    HeapReady,
    PlatformReady,
    ConstructorsReady,
    RuntimeEntered,
    WorkerStarting,
    Scanning,
    HooksInstalling,
    RuntimeReady,
    ModLoading,
    ModReady,
    Running,
    Disabled,
    ModDisabled,
    Count,
};

// Events are the only way a state may change. Logging must never drive state.
// Every event below corresponds one-to-one with an edge in the approved
// startup diagram; an event that has no edge is rejected by the state machine.
enum class RuntimeEvent : std::uint8_t {
    ModuleEntered = 0,
    HeapReady,
    PlatformReady,
    ConstructorsReady,
    RuntimeEntered,
    WorkerStarting,
    Scanning,
    HooksInstalling,
    RuntimeReady,
    ModLoadStarted,
    ModLoaded,
    ModRunStarted,
    FatalPrerequisiteFailed,
    InitializationFailed,
    ModuleMismatch,
    RequiredHookFailed,
    ModLoadFailed,
    ModRuntimeFailed,
    Count,
};

[[nodiscard]] const char* ToString(RuntimeState state) noexcept;
[[nodiscard]] const char* ToString(RuntimeEvent event) noexcept;

// Disabled and ModDisabled are terminal: the Mod may not be re-enabled and the
// Runtime does not attempt a second startup in the same process.
[[nodiscard]] constexpr bool IsTerminal(RuntimeState state) noexcept {
    return state == RuntimeState::Disabled || state == RuntimeState::ModDisabled;
}

// A surviving game process is the normal outcome of a Mod-level failure; only
// framework-level memory-safety failures may disable the whole Runtime.
[[nodiscard]] constexpr bool AllowsGameToContinue(RuntimeState state) noexcept {
    return state != RuntimeState::Disabled;
}

} // namespace isaac::runtime
