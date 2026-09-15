#pragma once

#include <cstddef>
#include <cstdint>

// On-disk magic: ISAACTR1. Protocol version = 1.
inline constexpr std::uint64_t kTestRunSnapshotMagic = 0x3152544341415349ULL;
inline constexpr std::uint32_t kTestRunSnapshotVersion = 1;

enum class TestRunKind : std::uint32_t {
    Default = 0,
    StartupProbe = 1,
    Diagnostic = 2,
};

// What the diagnostics journal managed to do when it armed itself. Carried in the
// snapshot's `reserved` word, so the 48-byte layout (and every reader) stays
// unchanged: it was always zero before, and old readers simply ignore it.
enum class TestRunDiagnosticsAttach : std::uint32_t {
    NotAttempted = 0,
    Attached = 1,
    // No usable file port: the SaltyNX file table was empty or incomplete.
    NoPort = 2,
    // A port existed but the journal could not open its file.
    OpenFailed = 3,
    // The attach threw no error but the session reports no journal, i.e. the
    // entry point did not run.
    SessionMissing = 4,
};

struct TestRunSnapshot {
    std::uint64_t magic;
    std::uint32_t version;
    std::uint32_t state;
    std::uint32_t detail;
    std::uint32_t sequence;
    std::uint64_t buildId;
    std::uint32_t kind;
    std::uint32_t stage;
    std::uint32_t reserved;
    std::uint32_t checksum;
};

inline constexpr std::size_t kTestRunSnapshotSize = sizeof(TestRunSnapshot);
static_assert(sizeof(TestRunSnapshot) == 48);
