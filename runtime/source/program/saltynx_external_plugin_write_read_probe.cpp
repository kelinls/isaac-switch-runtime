#include "saltynx_external_plugin_probe.hpp"

#include <cstddef>
#include <cstdint>

extern "C" void* SaltySDCore_fopen(const char* filename, const char* mode);
extern "C" std::size_t SaltySDCore_fwrite(const void* ptr, std::size_t size, std::size_t count, void* stream);
extern "C" std::size_t SaltySDCore_fread(void* ptr, std::size_t size, std::size_t count, void* stream);
extern "C" int SaltySDCore_fclose(void* stream);

namespace {
using SaltyFopen = void* (*)(const char*, const char*);
using SaltyFwrite = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using SaltyFread = std::size_t (*)(void*, std::size_t, std::size_t, void*);
using SaltyFclose = int (*)(void*);

[[gnu::used]] volatile SaltyFopen g_SaltyFopen = &SaltySDCore_fopen;
[[gnu::used]] volatile SaltyFwrite g_SaltyFwrite = &SaltySDCore_fwrite;
[[gnu::used]] volatile SaltyFread g_SaltyFread = &SaltySDCore_fread;
[[gnu::used]] volatile SaltyFclose g_SaltyFclose = &SaltySDCore_fclose;

constexpr char kWritePath[] = "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-write.bin";
constexpr std::uint8_t kExpected[] = "ISAAC_STAGE135!!";
} // namespace

std::uint32_t RunSaltyNxExternalPluginProbe() {
    std::uint32_t status = 0;
    void* write_file = g_SaltyFopen(kWritePath, "wb");
    if (write_file == nullptr) {
        return status;
    }
    status |= 1U;

    if (g_SaltyFwrite(kExpected, sizeof(kExpected) - 1, 1, write_file) != 1) {
        g_SaltyFclose(write_file);
        return status;
    }
    status |= 2U;

    if (g_SaltyFclose(write_file) != 0) {
        return status;
    }
    status |= 4U;

    void* read_file = g_SaltyFopen(kWritePath, "rb");
    if (read_file == nullptr) {
        return status;
    }
    status |= 8U;

    std::uint8_t bytes[sizeof(kExpected) - 1]{};
    if (g_SaltyFread(bytes, sizeof(bytes), 1, read_file) == 1) {
        status |= 16U;
        bool matches = true;
        for (std::size_t index = 0; index < sizeof(bytes); ++index) {
            if (bytes[index] != kExpected[index]) {
                matches = false;
                break;
            }
        }
        if (matches) {
            status |= 32U;
        }
    }
    if (g_SaltyFclose(read_file) == 0) {
        status |= 64U;
    }
    return status;
}
