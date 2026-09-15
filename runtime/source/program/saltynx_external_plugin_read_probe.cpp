#include "saltynx_external_plugin_probe.hpp"

#include <cstddef>
#include <cstdint>

extern "C" void* SaltySDCore_fopen(const char* filename, const char* mode);
extern "C" std::size_t SaltySDCore_fread(void* ptr, std::size_t size, std::size_t count, void* stream);
extern "C" int SaltySDCore_fclose(void* stream);

namespace {
using SaltyFopen = void* (*)(const char*, const char*);
using SaltyFread = std::size_t (*)(void*, std::size_t, std::size_t, void*);
using SaltyFclose = int (*)(void*);

[[gnu::used]] volatile SaltyFopen g_SaltyFopen = &SaltySDCore_fopen;
[[gnu::used]] volatile SaltyFread g_SaltyFread = &SaltySDCore_fread;
[[gnu::used]] volatile SaltyFclose g_SaltyFclose = &SaltySDCore_fclose;

constexpr char kReadPath[] = "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-read.bin";
constexpr char kReadMode[] = "rb";
constexpr std::uint8_t kExpected[] = "ISAAC_STAGE134!!";
} // namespace

std::uint32_t RunSaltyNxExternalPluginProbe() {
    std::uint32_t status = 0;
    void* file = g_SaltyFopen(kReadPath, kReadMode);
    if (file == nullptr) {
        return status;
    }

    status |= 1U;
    std::uint8_t bytes[sizeof(kExpected) - 1]{};
    if (g_SaltyFread(bytes, sizeof(bytes), 1, file) == 1) {
        status |= 2U;
        bool matches = true;
        for (std::size_t index = 0; index < sizeof(bytes); ++index) {
            if (bytes[index] != kExpected[index]) {
                matches = false;
                break;
            }
        }
        if (matches) {
            status |= 4U;
        }
    }
    if (g_SaltyFclose(file) == 0) {
        status |= 8U;
    }
    return status;
}
