#pragma once

#include <cstdint>

namespace isaac::runtime {

// Capabilities are published all-or-nothing: a platform adapter either proves
// a complete callable table or publishes nothing. 64 bits is deliberately
// fixed so the value stays trivially copyable and allocation-free.
enum class Capability : std::uint8_t {
    FileApi = 0,
    ModuleScanner,
    HookBackend,
    GameMemory,
    LuaEngine,
    Content,
    DiagnosticSink,
    Count,
};

class CapabilitySet {
public:
    constexpr CapabilitySet() noexcept = default;
    constexpr explicit CapabilitySet(std::uint64_t bits) noexcept : bits_(bits) {}

    static constexpr CapabilitySet None() noexcept { return CapabilitySet{}; }

    [[nodiscard]] constexpr bool Has(Capability capability) const noexcept {
        return (bits_ & Mask(capability)) != 0;
    }

    // Returns false when the bit is already present, so callers can detect a
    // double publication instead of silently overwriting state.
    constexpr bool Add(Capability capability) noexcept {
        const std::uint64_t mask = Mask(capability);
        if ((bits_ & mask) != 0) {
            return false;
        }
        bits_ |= mask;
        return true;
    }

    constexpr void Remove(Capability capability) noexcept { bits_ &= ~Mask(capability); }

    [[nodiscard]] constexpr std::uint64_t bits() const noexcept { return bits_; }

private:
    static constexpr std::uint64_t Mask(Capability capability) noexcept {
        return 1ULL << static_cast<std::uint8_t>(capability);
    }

    std::uint64_t bits_{0};
};

} // namespace isaac::runtime
