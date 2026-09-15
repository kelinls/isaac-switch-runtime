#pragma once

#include <cstdint>

// Everything in the plugin is hidden: the shared object exports nothing, so
// the dynamic symbol table stays minimal and the loader's mapping span does not
// grow with the C++ surface (the frozen plugin span is 0x3000).
#pragma GCC visibility push(hidden)

namespace isaac::runtime::host_plugin {

// The SaltyNX file table, as the Runtime's Host API expects it. Kept as a plain
// value so the plugin never shares a pointer into its own image with the module.
struct HostFileApi {
    std::uintptr_t open{0};
    std::uintptr_t read{0};
    std::uintptr_t write{0};
    std::uintptr_t close{0};

    [[nodiscard]] constexpr bool complete() const noexcept {
        return open != 0 && write != 0 && close != 0;
    }
    [[nodiscard]] constexpr bool fullyResolved() const noexcept {
        return open != 0 && read != 0 && write != 0 && close != 0;
    }
};

// Calls into the Runtime module by symbol name. This is the only direction of
// control flow from the plugin into the Runtime, and it is deliberately narrow:
// registering the file table and reading back snapshots the Runtime owns.
class RuntimeHostApiClient {
public:
    using RegisterFileApiFn = std::uint64_t (*)(std::uintptr_t, std::uintptr_t,
                                                std::uintptr_t, std::uintptr_t);

    explicit RuntimeHostApiClient(RegisterFileApiFn registerFileApi) noexcept
        : registerFileApi_(registerFileApi) {}

    // Returns the Runtime's registration acknowledgement, or 0 when the Runtime
    // rejected the table (incomplete table, or the module is not the expected one).
    [[nodiscard]] std::uint64_t RegisterFileApi(const HostFileApi& api) const noexcept;

private:
    RegisterFileApiFn registerFileApi_{nullptr};
};

} // namespace isaac::runtime::host_plugin

#pragma GCC visibility pop
