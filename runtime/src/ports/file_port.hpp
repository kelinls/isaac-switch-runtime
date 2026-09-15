#pragma once

#include "domain/runtime/status.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace isaac::runtime {

enum class FileMode : std::uint8_t {
    Read,
    Write,
};

// Runtime file capability (SaltyNX Core on the Switch). The handle is opaque
// so application code can never dereference a native FILE*.
class IFilePort {
public:
    virtual ~IFilePort() = default;

    [[nodiscard]] virtual Status Open(std::string_view path, FileMode mode, void** handle) noexcept = 0;
    [[nodiscard]] virtual Status Read(void* handle, std::uint8_t* target, std::size_t capacity,
                                      std::size_t* readCount) noexcept = 0;
    [[nodiscard]] virtual Status Write(void* handle, const std::uint8_t* bytes, std::size_t count,
                                       std::size_t* writtenCount) noexcept = 0;
    [[nodiscard]] virtual Status Close(void* handle) noexcept = 0;
};

} // namespace isaac::runtime
