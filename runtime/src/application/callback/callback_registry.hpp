#pragma once

#include "domain/callback/callback_descriptor.hpp"
#include "domain/runtime/status.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Registered callbacks per Mod. The legacy Runtime kept one global slot per
// callback id, which made multi-Mod registration impossible; this registry is
// fixed-capacity and ordered so dispatch order is deterministic.
class CallbackRegistry {
public:
    // 2026-09-12：64 → 256。PC 允许同一 Mod 对同一 id 登记多个回调**并存**，而 EID 一家就要
    // 几十条（5 个 `MC_POST_NEW_ROOM`、10 个 `MC_PRE_USE_ITEM`、各 feature 各自的登记）。
    // 每条描述符只有几十字节，256 条的代价可以忽略。
    static constexpr std::size_t kCapacity = 256;

    [[nodiscard]] Status Register(const CallbackDescriptor& descriptor) noexcept;

    [[nodiscard]] const CallbackDescriptor* Find(CallbackId id, ModHandle owner) const noexcept;

    // Removes one (id, owner) registration. When `removed` is provided the
    // caller receives the entry so it can release its Lua references.
    std::uint32_t Remove(CallbackId id, ModHandle owner,
                         CallbackDescriptor* removed = nullptr) noexcept;

    // Removes every callback owned by one Mod, for example on reload.
    std::uint32_t RemoveOwner(ModHandle owner) noexcept;

    [[nodiscard]] std::size_t Count() const noexcept { return count_; }
    [[nodiscard]] std::size_t CountOf(CallbackId id) const noexcept;

    // Returns the index-th registration of `id` in registration order, or
    // nullptr when there is no such entry.
    [[nodiscard]] const CallbackDescriptor* At(CallbackId id, std::size_t index) const noexcept;
    [[nodiscard]] const CallbackDescriptor* AtIndex(std::size_t index) const noexcept;

    void Reset() noexcept;

private:
    std::array<CallbackDescriptor, kCapacity> entries_{};
    std::size_t count_{0};
};

} // namespace isaac::runtime
