#pragma once

#include "domain/runtime/status.hpp"

#include <cstdint>

namespace isaac::runtime {

using ThreadEntry = void (*)(void* argument);

class IThreadPort {
public:
    virtual ~IThreadPort() = default;

    // One-shot worker creation. Kept here so bootstrap needs no Switch header.
    [[nodiscard]] virtual Status StartWorker(ThreadEntry entry, void* argument) noexcept = 0;
    [[nodiscard]] virtual std::uint64_t CurrentThreadId() const noexcept = 0;

    // Bounded sleep used only by the module-wait loop.
    virtual void SleepMilliseconds(std::uint32_t milliseconds) noexcept = 0;
};

} // namespace isaac::runtime
