#pragma once

#include <cstdint>

namespace isaac::runtime {

// Stable, pointer-free Mod identity. Lua userdata stores only this handle, so
// a cached userdata can never keep a native object alive across callbacks.
struct ModHandle {
    std::uint16_t index{0};
    std::uint16_t generation{0};

    [[nodiscard]] constexpr bool valid() const noexcept { return generation != 0; }

    friend constexpr bool operator==(ModHandle left, ModHandle right) noexcept {
        return left.index == right.index && left.generation == right.generation;
    }
    friend constexpr bool operator!=(ModHandle left, ModHandle right) noexcept {
        return !(left == right);
    }
};

} // namespace isaac::runtime
