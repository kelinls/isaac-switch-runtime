#pragma once

#include <cstdint>

// Everything in the plugin is hidden: the shared object exports nothing, so
// the dynamic symbol table stays minimal and the loader's mapping span does not
// grow with the C++ surface (the frozen plugin span is 0x3000).
#pragma GCC visibility push(hidden)

namespace isaac::runtime::host_plugin {

// The plugin's only import from SaltyNX: `SaltySDCore_FindSymbol`, called directly
// through the GOT.
//
// This class is deliberately NOT polymorphic, and it must stay that way. A virtual
// function moves a vtable into `.data`, and every vtable slot then needs an
// `R_AARCH64_RELATIVE` relocation. SaltyNX's loader (`SaltySDCore_DynamicLinkModule`)
// applies the symbol relocations but leaves RELATIVE entries as raw file offsets, so
// the first virtual call branches to a near-null address. That is exactly how the
// Task 6b package `20260910670000` died on hardware: Instruction Abort at PC=0x60 --
// the unrelocated vtable slot of `Find` -- reported as Result 0x2A8 (2168-0001) in
// `crash_reports/01789060016_010021c000b6a000.log`.
//
// A virtual *destructor* is worse still: its deleting variant drags in
// `operator delete` -> `free` -> newlib's `__malloc_av_`, `_impure_data` and
// `_sbrk_r`, growing `.data` by 2704 bytes, `.bss` by 4096, and adding a `.plt` plus
// a `JUMP_SLOT` relocation. Keep the plugin virtual-free, allocation-free and
// exception-free; the build-time gate in `runtime/tests/unit/test_host_plugin_elf.py`
// fails the build if a RELATIVE relocation reappears.
class SaltyNxCoreResolver final {
public:
    [[nodiscard]] std::uintptr_t Find(const char* name) noexcept;

    // The address of the loader's lookup itself, so the Runtime can resolve names on its
    // own. Without this the Runtime could only use the file functions the plugin hands it,
    // and those are measured not to work on the game's threads.
    [[nodiscard]] std::uintptr_t LookupAddress() const noexcept;
};

} // namespace isaac::runtime::host_plugin

#pragma GCC visibility pop
