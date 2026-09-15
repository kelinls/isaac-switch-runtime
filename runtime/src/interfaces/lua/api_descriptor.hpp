#pragma once

#include "domain/runtime/thread_affinity.hpp"

#include <cstdint>

namespace isaac::runtime {

// Stable API identity. `id` is permanent: a value is never reused after an API
// is removed, so an old log or manifest can always be resolved.
//
//   id = (domain << 24) | (ownerGroup << 16) | sequence
//
// The domain byte never changes for an existing API; changing an owner's group
// or a sequence number would break that promise.
enum class ApiDomain : std::uint8_t {
    Global = 0,
    Mod = 1,
    Game = 2,
    Level = 3,
    Room = 4,
    ItemPool = 5,
    Music = 6,
    Rng = 7,
    Persistence = 8,
    Input = 9,
    Diagnostic = 10,
    Font = 11,
    Vector = 12,
    Sprite = 13,
    // `Isaac` 是引擎门面（`Isaac.GetItemConfig()` 这类全局入口），不是某一种对象家族，
    // 所以它有独立的 domain 字节。
    Isaac = 14,
    // `Seeds`（`Game:GetSeeds()` 的返回值）是独立对象家族，与 `Game` 分开计域。
    Seed = 15,
};

// How much evidence exists for an API. Persistence stays Experimental until the
// Stage145 investigation closes; nothing may be promoted without hardware proof.
enum class ApiMaturity : std::uint8_t {
    Experimental = 0,
    HostVerified,
    HardwareVerified,
    Disabled,
};

struct ApiVersion {
    std::uint16_t major{1};
    std::uint16_t minor{0};
};

// One registered Lua API, described without any Lua or platform type: the
// catalog stays host-testable and cannot smuggle in a lua_State or a raw game
// pointer. Binding to an implementation happens in the Lua adapter layer.
struct LuaApiDescriptor {
    std::uint32_t id{0};
    ApiDomain domain{ApiDomain::Global};
    const char* owner{nullptr};
    const char* name{nullptr};
    ApiVersion version{};
    std::uint64_t requiredCapabilities{0};
    ThreadAffinity affinity{ThreadAffinity::Any};
    ApiMaturity maturity{ApiMaturity::Experimental};

    [[nodiscard]] constexpr std::uint8_t DomainByte() const noexcept {
        return static_cast<std::uint8_t>(domain);
    }
};

[[nodiscard]] const char* ToString(ApiDomain domain) noexcept;
[[nodiscard]] const char* ToString(ApiMaturity maturity) noexcept;

} // namespace isaac::runtime
