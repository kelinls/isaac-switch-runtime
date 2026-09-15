#include "saltynx_external_plugin_probe.hpp"

#include <cstddef>
#include <cstdint>

extern "C" void* SaltySDCore_fopen(const char*, const char*);
extern "C" std::size_t SaltySDCore_fread(void*, std::size_t, std::size_t, void*);
extern "C" int SaltySDCore_fclose(void*);

namespace {
using Fopen = void* (*)(const char*, const char*);
using Fread = std::size_t (*)(void*, std::size_t, std::size_t, void*);
using Fclose = int (*)(void*);
[[gnu::used]] volatile Fopen g_Fopen = &SaltySDCore_fopen;
[[gnu::used]] volatile Fread g_Fread = &SaltySDCore_fread;
[[gnu::used]] volatile Fclose g_Fclose = &SaltySDCore_fclose;
constexpr char kPath[] = "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-write.bin";
constexpr std::uint8_t kExpected[] = "ISAAC_STAGE135!!";
}

std::uint32_t RunSaltyNxExternalPluginProbe() {
    std::uint32_t status = 0;
    void* file = g_Fopen(kPath, "rb");
    if (file == nullptr) return status;
    status |= 1U;
    std::uint8_t bytes[sizeof(kExpected) - 1]{};
    if (g_Fread(bytes, sizeof(bytes), 1, file) == 1) {
        status |= 2U;
        bool matches = true;
        for (std::size_t index = 0; index < sizeof(bytes); ++index) {
            if (bytes[index] != kExpected[index]) { matches = false; break; }
        }
        if (matches) status |= 4U;
    }
    if (g_Fclose(file) == 0) status |= 8U;
    return status;
}
