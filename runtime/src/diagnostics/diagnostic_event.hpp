#pragma once

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Stable event identity. Values are wire format: the offline parser reads them
// numerically and must never depend on a display string, so new events append
// (never renumber) and each subsystem owns a contiguous block.
enum class DiagnosticSubsystem : std::uint16_t {
    None = 0,
    Bootstrap = 1,
    Scanner = 2,
    Hook = 3,
    Manifest = 4,
    Lua = 5,
    Api = 6,
    Persistence = 7,
    Diagnostics = 8,
};

// Which execution context produced the record. The Runtime module and the
// SaltyNX host plugin write separate files with the same schema, so this tells
// the parser where a record came from.
enum class DiagnosticOrigin : std::uint32_t {
    Unknown = 0,
    RuntimeModule = 1,
    HostPlugin = 2,
};

enum class DiagnosticSeverity : std::uint8_t {
    Trace = 0,
    Info = 1,
    Warning = 2,
    Error = 3,
};

// Which half of a paired operation the record describes. Only
// `Entered`/`Returned`/`Failed` participate in pairing; `None` is a one-shot
// observation such as "file port registered".
enum class DiagnosticPhase : std::uint8_t {
    None = 0,
    Entered = 1,
    Returned = 2,
    Failed = 3,
};

// The result carried by a `Returned`/`Failed` record. `domain` stays a plain
// integer so the diagnostics layer never depends on the error model of the
// subsystem that produced the event.
struct DiagnosticResult {
    std::uint32_t domain{0};
    std::uint32_t code{0};
};

// Encoded little-endian first, so the eight leading file bytes read "ISAACDV1".
inline constexpr std::uint64_t kDiagnosticEventMagic = 0x3156444341415349ULL;
inline constexpr std::uint16_t kDiagnosticEventSchemaVersion = 1;
inline constexpr std::uint32_t kDiagnosticThreadTagUnknown = 0xFFFFFFFFU;

// One event. Fixed size on purpose: the codec writes it field by field instead
// of memcpy-ing the struct, so layout and endianness cannot leak into the file
// format and a host test can check every byte.
struct DiagnosticEvent {
    DiagnosticOrigin origin{DiagnosticOrigin::Unknown};
    std::uint64_t buildId{0};
    std::uint32_t sequence{0};
    DiagnosticSubsystem subsystem{DiagnosticSubsystem::None};
    std::uint16_t event{0};
    DiagnosticSeverity severity{DiagnosticSeverity::Info};
    DiagnosticPhase phase{DiagnosticPhase::None};
    std::uint16_t flags{0};
    std::uint32_t threadTag{kDiagnosticThreadTagUnknown};
    DiagnosticResult result{};
    std::uint64_t detail{0};
};

// Two records pair up when both halves share the identity quintuple
// (origin, buildId, subsystem, event, thread) and the first half entered. Events
// that merely share an id but not that identity must not pair, otherwise the
// parser would report a duration for two unrelated operations.
[[nodiscard]] constexpr bool IsPairedWith(const DiagnosticEvent& entered,
                                          const DiagnosticEvent& returned) noexcept {
    return entered.subsystem == returned.subsystem && entered.event == returned.event &&
           entered.threadTag == returned.threadTag &&
           entered.origin == returned.origin && entered.buildId == returned.buildId &&
           entered.phase == DiagnosticPhase::Entered &&
           (returned.phase == DiagnosticPhase::Returned ||
            returned.phase == DiagnosticPhase::Failed);
}

} // namespace isaac::runtime
