#pragma once

#include "diagnostics/diagnostic_event.hpp"

#include <cstdint>

namespace isaac::runtime {

// Stable event ids per subsystem. These numbers are wire format: the offline
// parser maps them to names, so an id is never renumbered or reused. Each
// subsystem owns its own contiguous block.
//
// Only the events the pipeline currently emits are listed; new ones are appended
// together with their parser mapping in `tools/read_persistence_event_log.py`.

// DiagnosticSubsystem::Bootstrap
inline constexpr std::uint16_t kBootstrapEventModuleEntered = 1;
inline constexpr std::uint16_t kBootstrapEventPlatformInitialized = 2;
inline constexpr std::uint16_t kBootstrapEventWorkerStarted = 3;
inline constexpr std::uint16_t kBootstrapEventRuntimeEntered = 4;

// DiagnosticSubsystem::Manifest
inline constexpr std::uint16_t kManifestEventLoadEntered = 1;
inline constexpr std::uint16_t kManifestEventLoadReturned = 2;

// DiagnosticSubsystem::Lua
inline constexpr std::uint16_t kLuaEventStateCreated = 1;
inline constexpr std::uint16_t kLuaEventManifestModEntered = 2;
inline constexpr std::uint16_t kLuaEventManifestModReturned = 3;

// DiagnosticSubsystem::Diagnostics
inline constexpr std::uint16_t kDiagnosticsEventFilePortRegistered = 1;
inline constexpr std::uint16_t kDiagnosticsEventHealthChanged = 2;
inline constexpr std::uint16_t kDiagnosticsEventJournalFlushed = 3;
inline constexpr std::uint16_t kDiagnosticsEventAttachFailed = 4;

// `detail` of an AttachFailed event. These numbers are wire format: they are how
// an offline reader tells "no file port was ever registered" from "the file could
// not be opened", which are different problems with different fixes.
inline constexpr std::uint32_t kDiagnosticsAttachFailureNoPort = 1;
inline constexpr std::uint32_t kDiagnosticsAttachFailureOpenFailed = 2;

} // namespace isaac::runtime
