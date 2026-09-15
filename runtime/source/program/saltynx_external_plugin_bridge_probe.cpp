#include "saltynx_external_plugin_probe.hpp"

#include <cstdint>

extern "C" std::uintptr_t SaltySDCore_FindSymbol(const char*);

namespace {
using FindSymbol = std::uintptr_t (*)(const char*);
using RuntimeBridgeProbe = std::uint64_t (*)();

[[gnu::used]] volatile FindSymbol g_find_symbol = &SaltySDCore_FindSymbol;

constexpr char kBridgeSymbol[] = "IsaacModRuntime_SaltyBridgeProbe";
constexpr std::uint64_t kExpectedResult = 0x49534141435F4231ULL;
} // namespace

std::uint32_t RunSaltyNxExternalPluginProbe() {
    const auto address = g_find_symbol(kBridgeSymbol);
    if (address == 0) {
        return 0;
    }

    const auto probe = reinterpret_cast<RuntimeBridgeProbe>(address);
    return 1U | (probe() == kExpectedResult ? 2U : 0U);
}
