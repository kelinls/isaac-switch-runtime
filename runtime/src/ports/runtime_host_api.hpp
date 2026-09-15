#pragma once

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Function types exported by SaltyNX Core. They are declared here (and nowhere
// else) so the host plugin and the Runtime agree on one ABI.
using HostFileOpenFn = void* (*)(const char* path, const char* mode);
using HostFileReadFn = std::size_t (*)(void* target, std::size_t size, std::size_t count, void* file);
using HostFileWriteFn = std::size_t (*)(const void* source, std::size_t size, std::size_t count, void* file);
using HostFileCloseFn = int (*)(void* file);

inline constexpr std::uint32_t kHostApiVersion1 = 1;
// Fixed struct sizes so both binaries agree before either one is loaded, and
// so `structSize` can be set without referring to the type being defined.
inline constexpr std::uint32_t kHostFileApiSizeV1 = 40;      // 2 * u32 + 4 * pointer
inline constexpr std::uint32_t kRuntimeHostApiSizeV1 = 40;   // 2 * u32 + 2 * u64 + 2 * pointer

// Capabilities the plugin may consume. The Runtime publishes exactly the ones
// it actually implements, so a plugin can never call a missing function.
inline constexpr std::uint64_t kHostCapabilityFilePort = 1ULL << 0;
inline constexpr std::uint64_t kHostCapabilityHostEvent = 1ULL << 1;

enum class HostRegisterResult : std::uint32_t {
    Accepted = 0,
    RejectedVersion,
    RejectedIncomplete,
    RejectedBuildMismatch,
    RejectedCapacity,
};

struct HostFileApiV1 {
    std::uint32_t abiVersion{kHostApiVersion1};
    std::uint32_t structSize{kHostFileApiSizeV1};
    HostFileOpenFn open{nullptr};
    HostFileReadFn read{nullptr};
    HostFileWriteFn write{nullptr};
    HostFileCloseFn close{nullptr};

    [[nodiscard]] bool complete() const noexcept {
        return open != nullptr && read != nullptr && write != nullptr && close != nullptr;
    }
};

// The single versioned entry point shared by the Runtime and the host plugin.
// The plugin may register capabilities and publish host events; it must never
// call into Manifest, Lua, hooks or persistence services.
struct RuntimeHostApiV1 {
    std::uint32_t abiVersion{kHostApiVersion1};
    std::uint32_t structSize{kRuntimeHostApiSizeV1};
    std::uint64_t buildId{0};
    std::uint64_t capabilities{0};
    HostRegisterResult (*RegisterFilePort)(const HostFileApiV1*) noexcept{nullptr};
    HostRegisterResult (*PublishHostEvent)(const void* payload, std::uint32_t size) noexcept{nullptr};
};

class IRuntimeHostApi {
public:
    virtual ~IRuntimeHostApi() = default;

    [[nodiscard]] virtual HostRegisterResult RegisterFilePort(const HostFileApiV1& api) noexcept = 0;
    [[nodiscard]] virtual HostRegisterResult PublishHostEvent(const void* payload,
                                                              std::uint32_t size) noexcept = 0;
    // Copies the file table once a complete registration has been published;
    // false while no host has registered a usable table.
    [[nodiscard]] virtual bool PublishedFileApi(HostFileApiV1* out) const noexcept = 0;
    [[nodiscard]] virtual const RuntimeHostApiV1& Abi() const noexcept = 0;
};

} // namespace isaac::runtime
