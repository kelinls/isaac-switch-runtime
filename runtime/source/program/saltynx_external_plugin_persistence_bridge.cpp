#include "saltynx_external_plugin_persistence_bridge.hpp"

#include <cstdint>

extern "C" std::uintptr_t SaltySDCore_FindSymbol(const char*);

namespace {
using FindSymbol = std::uintptr_t (*)(const char*);
using RegisterFileApi = std::uint64_t (*)(std::uintptr_t, std::uintptr_t, std::uintptr_t,
                                          std::uintptr_t);

[[gnu::used]] volatile FindSymbol g_find_symbol = &SaltySDCore_FindSymbol;

constexpr char kOpenSymbol[] = "SaltySDCore_fopen";
constexpr char kReadSymbol[] = "SaltySDCore_fread";
constexpr char kWriteSymbol[] = "SaltySDCore_fwrite";
constexpr char kCloseSymbol[] = "SaltySDCore_fclose";
constexpr char kRegisterSymbol[] = "IsaacModRuntime_RegisterSaltyFileApi";
constexpr std::uint64_t kFileApiRegistered = 0x49534141435F4631ULL;
} // namespace

void RunSaltyNxPersistenceBridge() {
    const std::uintptr_t open = g_find_symbol(kOpenSymbol);
    const std::uintptr_t read = g_find_symbol(kReadSymbol);
    const std::uintptr_t write = g_find_symbol(kWriteSymbol);
    const std::uintptr_t close = g_find_symbol(kCloseSymbol);
    if (open == 0 || read == 0 || write == 0 || close == 0) {
        return;
    }
    const auto registerApi = reinterpret_cast<RegisterFileApi>(g_find_symbol(kRegisterSymbol));
    if (registerApi == nullptr) {
        return;
    }
    static_cast<void>(registerApi(open, read, write, close) == kFileApiRegistered);
}
