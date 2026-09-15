#pragma once

#include "host_plugin/runtime_host_api_client.hpp"
#include "host_plugin/saltynx_symbol_resolver.hpp"

#include <cstdint>

// Everything in the plugin is hidden: the shared object exports nothing, so
// the dynamic symbol table stays minimal and the loader's mapping span does not
// grow with the C++ surface (the frozen plugin span is 0x3000).
#pragma GCC visibility push(hidden)

namespace isaac::runtime::host_plugin {

// What one plugin run did, as a bit set. Values are wire format: the plugin
// writes them into `isaac-runtime-bridge.bin` and the offline reader names them.
enum class HostPluginFlags : std::uint32_t {
    None = 0,
    FileTableResolved = 1U << 0,
    ReadResolved = 1U << 1,
    RegistrationEntryResolved = 1U << 2,
    FileTableRegistered = 1U << 3,
    ObserverStateResolved = 1U << 4,
    SnapshotResolved = 1U << 5,
    ObserverStateRead = 1U << 6,
    HookDiagnosticsResolved = 1U << 7,
    HookDiagnosticsRead = 1U << 8,
    DiagnosticsStateResolved = 1U << 9,
    DiagnosticsStateRead = 1U << 10,
    RuntimeIdentityResolved = 1U << 11,
    RuntimeCopyConfirmed = 1U << 13,
    FileTableRegisteredInRunningCopy = 1U << 12,
    // The Runtime's self-journal: whether the copy this plugin talks to has tried to
    // write its own record through the `fs` service, and whether that call answered.
    SelfJournalStateResolved = 1U << 14,
    SelfJournalStateRead = 1U << 15,
    // Which copies of the Runtime module exist in this process, and what happened to the
    // one that runs: the byte scan that finds them, the copy whose published `exl_main`
    // address is non-zero, the store that hands it the file table, and the two state reads
    // taken from it (recorded with readIndex 3).
    ModuleCopyScanDone = 1U << 16,
    RunningCopyFound = 1U << 17,
    FileTablePlantedInRunningCopy = 1U << 18,
    RunningCopyStateRead = 1U << 19,
    RunningCopyJournalRead = 1U << 20,
    // The wait for the Runtime's entry, and the hand-over that follows it. Without this
    // the table is registered into an image whose `.bss` the Runtime's own startup clears.
    // The thread that waits for the Runtime's entry and hands the table over afterwards.
    // It cannot write a record itself -- a thread the game did not create may not use the
    // game's libc -- so this bit is set by the run that started it, and its findings travel
    // through `IsaacModRuntime_SetPluginNote` into the probe break's crash report.
    HandoverThreadStarted = 1U << 21,
    // The plugin's own thread created its probe file: the control for the comparison with
    // the Runtime's game-thread probe.
    PluginThreadFileWriteOk = 1U << 22,
};

// The minimal host plugin (Task 6b): resolve the file table, hand it to the
// Runtime, and read back the Runtime-owned snapshots. It deliberately does **no**
// hooking, no Lua work and no domain logic (design §13), and it never performs
// file I/O on the SaltyNX registration thread: that is what crashed on hardware
// before and is what the Runtime's own update callback is for.
//
// The resolver is held by concrete type, not through a base-class reference: a
// polymorphic member would make `resolver_.Find(...)` a virtual call, which the
// SaltyNX loader cannot support (see `saltynx_symbol_resolver.hpp`).
class SaltyNxHostPlugin {
public:
    explicit SaltyNxHostPlugin(SaltyNxCoreResolver& resolver) noexcept : resolver_(resolver) {}

    // Runs the whole plugin sequence once. Returns the observed flag set.
    //
    // When `report` is true the plugin also appends one status record to
    // `isaac-runtime-bridge.bin`, which is the only file write it performs and the
    // one thing a plugin is allowed to do itself: this happens on the SaltyNX
    // thread, so it must never touch the Runtime and must stay a single append.
    [[nodiscard]] std::uint32_t Run(bool report) noexcept;

private:
    SaltyNxCoreResolver& resolver_;
};

} // namespace isaac::runtime::host_plugin

#pragma GCC visibility pop
