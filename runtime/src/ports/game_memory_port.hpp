#pragma once

#include "domain/runtime/status.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Arguments for one verified thunk call. Kept to fixed-width scalars so the
// port stays independent of any game or platform header.
struct ThunkCall {
    std::uintptr_t thunk{0};
    std::uint64_t arguments[4]{0, 0, 0, 0};
    std::uint8_t argumentCount{0};
};

class IGameMemoryPort {
public:
    virtual ~IGameMemoryPort() = default;

    [[nodiscard]] virtual Status Read(std::uintptr_t address, void* target,
                                      std::size_t size) noexcept = 0;
    [[nodiscard]] virtual Status ReadU64(std::uintptr_t address, std::uint64_t* value) noexcept = 0;

    // Resolves one pointer in a validated object chain. Every link is checked
    // against the module's mapped ranges before the next hop.
    [[nodiscard]] virtual Status ResolvePointer(std::uintptr_t address,
                                                std::uintptr_t* value) noexcept = 0;

    // Calls a thunk that was already validated as executable game code.
    [[nodiscard]] virtual Status Call(const ThunkCall& call, std::uint64_t* result) noexcept = 0;
};

} // namespace isaac::runtime
