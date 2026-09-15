#pragma once

#include "domain/runtime/status.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// The module header exposes a 32-byte build-ID field: 20 meaningful NRO bytes
// followed by zero padding. Hook verification compares the whole field, so the
// port carries it verbatim instead of truncating to the NRO value.
inline constexpr std::size_t kBuildIdSize = 32;
using ModuleBuildId = std::array<std::uint8_t, kBuildIdSize>;

// Identity of a located module. `base` and `textSize` describe the target
// module's code window; no pointer into game memory is stored here.
struct ModuleInfo {
    std::uintptr_t base{0};
    std::size_t textSize{0};
    std::size_t imageSize{0};
    ModuleBuildId buildId{};
    std::uint64_t moduleId{0};
    bool valid{false};
};

struct TargetModuleSpec {
    const char* name{nullptr};
    ModuleBuildId buildId{};
    std::uint32_t timeoutMilliseconds{0};
};

class IModuleScannerPort {
public:
    virtual ~IModuleScannerPort() = default;

    // Blocks only within the bounded wait window given by the spec. A missing
    // or mismatched module is a Status failure, never a silent success.
    [[nodiscard]] virtual Status WaitForTarget(const TargetModuleSpec& spec,
                                               ModuleInfo* module) noexcept = 0;
};

} // namespace isaac::runtime
