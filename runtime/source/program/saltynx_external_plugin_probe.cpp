#include "saltynx_external_plugin_probe.hpp"

#include <cstdint>

extern "C" void SaltySDCore_printf(const char* format, ...);

namespace {
using SaltyPrintf = void (*)(const char*, ...);

// The Core linker must fill this strong-import slot before it invokes our entrypoint.
[[gnu::used]] volatile SaltyPrintf g_SaltyPrintf = &SaltySDCore_printf;
} // namespace

std::uint32_t RunSaltyNxExternalPluginProbe() {
    return g_SaltyPrintf != nullptr ? 1U : 0U;
}
