#pragma once

#include <cstdint>

// Marker ids and entry point for the self-journal.
//
// The self-journal is the one write path in this module that does not go through
// the file table the SaltyNX host plugin registers: the copy of the module that is
// actually executing appends a record to `sdmc:/SaltySD/plugins/<title>/isaac-runtime-self.bin`
// through the `fs` service itself. See `saltynx_runtime_bridge.cpp` for why that
// path exists.
//
// The marker ids are wire format; the offline reader names them. The declaration is
// hidden because every call site sits in another translation unit of this module: a
// default-visibility declaration would make the linker route the call through a
// `.plt` stub whose GOT slot only works if the module loader resolves it, which is
// exactly the dependency this journal must not have.
namespace isaac::runtime {
constexpr std::uint32_t kSelfJournalUpdateMarker = 1;
constexpr std::uint32_t kSelfJournalRenderMarker = 2;
constexpr std::uint32_t kSelfJournalExlMainMarker = 3;
}  // namespace isaac::runtime

extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_WriteSelfJournal(std::uint32_t marker);

// Gives the calling thread a libc thread pointer when it does not have one.
//
// Called first thing in `exl_main`, before anything can call into the host plugin's
// file functions: SaltyNX's own code is built with devkitA64 too, and devkitA64 reads
// the thread pointer through `[tpidrro_el0 + 0x1f8]` and dereferences it. A game
// thread has that slot zeroed, so the slot is filled before the first such call.
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_EnsureThreadTls();
