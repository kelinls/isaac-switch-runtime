#include "host_plugin/saltynx_symbol_resolver.hpp"

// The plugin's only import from the loader. Everything else it needs (file
// functions, the Runtime's registration entry) is resolved through the address
// this symbol returns, so the plugin's undefined-symbol set stays exactly one entry.
extern "C" std::uintptr_t SaltySDCore_FindSymbol(const char* name);

namespace {

using FindSymbol = std::uintptr_t (*)(const char*);

// Bind the import through a global function pointer instead of calling the
// undefined symbol directly.
//
// This is not stylistic. A direct call to an undefined symbol makes the linker
// emit a `.plt` stub plus an `R_AARCH64_JUMP_SLOT` relocation; taking the symbol's
// address instead keeps the single reference inside `.data` as one
// `R_AARCH64_ABS64`, which is the exact relocation shape every SaltyNX plugin in
// this project has already proven on hardware
// (`runtime/source/program/saltynx_external_plugin_persistence_bridge_trace.cpp`
// uses the same idiom). Keeping the plugin's relocation set to that one entry is
// what makes a plugin loadable by `SaltySDCore_DynamicLinkModule`.
[[gnu::used]] volatile FindSymbol g_find_symbol = &SaltySDCore_FindSymbol;

} // namespace

namespace isaac::runtime::host_plugin {

std::uintptr_t SaltyNxCoreResolver::Find(const char* name) noexcept {
    if (name == nullptr) {
        return 0;
    }
    return g_find_symbol(name);
}

std::uintptr_t SaltyNxCoreResolver::LookupAddress() const noexcept {
    return reinterpret_cast<std::uintptr_t>(g_find_symbol);
}

} // namespace isaac::runtime::host_plugin
