#include "saltynx_external_plugin_probe.hpp"
#include "saltynx_state_container.hpp"

#include <cstddef>

extern "C" void* SaltySDCore_fopen(const char*, const char*);
extern "C" std::size_t SaltySDCore_fwrite(const void*, std::size_t, std::size_t, void*);
extern "C" std::size_t SaltySDCore_fread(void*, std::size_t, std::size_t, void*);
extern "C" int SaltySDCore_fclose(void*);

namespace {
using Fopen = void* (*)(const char*, const char*);
using Fwrite = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using Fread = std::size_t (*)(void*, std::size_t, std::size_t, void*);
using Fclose = int (*)(void*);

[[gnu::used]] volatile Fopen g_open = &SaltySDCore_fopen;
[[gnu::used]] volatile Fwrite g_write = &SaltySDCore_fwrite;
[[gnu::used]] volatile Fread g_read = &SaltySDCore_fread;
[[gnu::used]] volatile Fclose g_close = &SaltySDCore_fclose;

constexpr char kPath[] = "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-state.bin";
constexpr saltynx_state::StateRecord kRecords[] = {
    {0x47414D455F535441ULL, 0x5452454153555245ULL, 7},
    {0x52554E54494D455FULL, 0x434F4D5041545F31ULL, 42},
};
} // namespace

std::uint32_t RunSaltyNxExternalPluginProbe() {
    std::uint32_t status = 0;
    std::array<std::uint8_t, saltynx_state::kContainerSize> written{};
    saltynx_state::BuildStateContainer(written, 1, kRecords);

    void* write_file = g_open(kPath, "wb");
    if (!write_file) {
        return status;
    }
    status |= 1;
    if (g_write(written.data(), written.size(), 1, write_file) != 1) {
        g_close(write_file);
        return status;
    }
    status |= 2;
    if (g_close(write_file) != 0) {
        return status;
    }
    status |= 4;

    void* read_file = g_open(kPath, "rb");
    if (!read_file) {
        return status;
    }
    status |= 8;
    std::array<std::uint8_t, saltynx_state::kContainerSize> read{};
    if (g_read(read.data(), read.size(), 1, read_file) == 1) {
        status |= 16;
        if (saltynx_state::ValidateContainerEnvelope(read)) {
            status |= 32;
        }
        if (saltynx_state::ValidateContainerRecords(read, kRecords)) {
            status |= 64;
        }
    }
    if (g_close(read_file) == 0) {
        status |= 128;
    }
    return status;
}
