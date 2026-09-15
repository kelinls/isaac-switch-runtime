#include "saltynx_external_plugin_probe.hpp"

#include <cstdint>

extern "C" std::uintptr_t SaltySDCore_FindSymbol(const char*);

namespace {
using FindSymbol = std::uintptr_t (*)(const char*);
using RegisterFileApi = std::uint64_t (*)(std::uintptr_t, std::uintptr_t, std::uintptr_t,
                                          std::uintptr_t);
using FileApiStatus = std::uint64_t (*)();
using RuntimeIoProbe = std::uint64_t (*)();

[[gnu::used]] volatile FindSymbol g_find_symbol = &SaltySDCore_FindSymbol;

constexpr char kOpenSymbol[] = "SaltySDCore_fopen";
constexpr char kReadSymbol[] = "SaltySDCore_fread";
constexpr char kWriteSymbol[] = "SaltySDCore_fwrite";
constexpr char kCloseSymbol[] = "SaltySDCore_fclose";
constexpr char kRegisterSymbol[] = "IsaacModRuntime_RegisterSaltyFileApi";
constexpr char kStatusSymbol[] = "IsaacModRuntime_SaltyFileApiStatus";
constexpr char kRuntimeIoProbeSymbol[] = "IsaacModRuntime_SaltyFileIoProbe";
constexpr std::uint64_t kFileApiRegistered = 0x49534141435F4631ULL;
constexpr std::uint64_t kAllFileApiBits = 0xFULL;
} // namespace

std::uint32_t RunSaltyNxExternalPluginProbe() {
    const std::uintptr_t open = g_find_symbol(kOpenSymbol);
    const std::uintptr_t read = g_find_symbol(kReadSymbol);
    const std::uintptr_t write = g_find_symbol(kWriteSymbol);
    const std::uintptr_t close = g_find_symbol(kCloseSymbol);
    std::uint32_t registration = (open != 0 ? 1U : 0U) | (read != 0 ? 2U : 0U) |
                                 (write != 0 ? 4U : 0U) | (close != 0 ? 8U : 0U);

    const auto register_api = reinterpret_cast<RegisterFileApi>(g_find_symbol(kRegisterSymbol));
    if (register_api == nullptr) {
        return registration;
    }
    registration |= 0x10U;
    if (register_api(open, read, write, close) != kFileApiRegistered) {
        return registration;
    }
    registration |= 0x20U;

    const auto file_api_status = reinterpret_cast<FileApiStatus>(g_find_symbol(kStatusSymbol));
    if (file_api_status == nullptr) {
        return registration;
    }
    registration |= 0x40U;
    if (file_api_status() != kAllFileApiBits) {
        return registration;
    }
    registration |= 0x80U;

    const auto runtime_io_probe =
        reinterpret_cast<RuntimeIoProbe>(g_find_symbol(kRuntimeIoProbeSymbol));
    if (runtime_io_probe == nullptr) {
        return registration;
    }
    return registration | (static_cast<std::uint32_t>(runtime_io_probe()) << 8);
}
