#pragma once

#include "domain/runtime/status.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace isaac::runtime {

// Read-only view of a packaged content file (the RomFS Mod manifest and its
// Lua entry). Writing is deliberately not part of this port.
class IContentPort {
public:
    virtual ~IContentPort() = default;

    [[nodiscard]] virtual Status Size(std::string_view path, std::size_t* size) noexcept = 0;
    [[nodiscard]] virtual Status Read(std::string_view path, std::uint8_t* target,
                                     std::size_t capacity, std::size_t* readCount) noexcept = 0;
};

} // namespace isaac::runtime
