#pragma once

#include "composition/runtime_dependencies.hpp"
#include "domain/runtime/runtime_context.hpp"
#include "domain/runtime/status.hpp"

#include <cstdint>

namespace isaac::runtime {

// Single composition root. It owns the Runtime state and the injected ports and
// is the only place that wires them together; services receive references
// instead of reaching for globals.
class RuntimeKernel {
public:
    [[nodiscard]] static RuntimeKernel& Instance() noexcept;

    // Second initialisation is rejected: the Runtime starts once per process.
    [[nodiscard]] Status Initialize(const RuntimeDependencies& dependencies) noexcept;

    [[nodiscard]] bool initialized() const noexcept { return initialized_; }
    [[nodiscard]] RuntimeContext& context() noexcept { return context_; }
    [[nodiscard]] const RuntimeContext& context() const noexcept { return context_; }
    [[nodiscard]] const RuntimeDependencies& dependencies() const noexcept { return dependencies_; }

    // Early event accounting. Diagnostics takes this over in a later stage, so
    // the kernel only forwards to the sink and counts what it published.
    void PublishEvent(std::uint32_t eventId) noexcept;
    [[nodiscard]] std::uint32_t publishedEventCount() const noexcept { return publishedEventCount_; }

private:
    RuntimeKernel() = default;
    RuntimeKernel(const RuntimeKernel&) = delete;
    RuntimeKernel& operator=(const RuntimeKernel&) = delete;

    RuntimeContext context_{};
    RuntimeDependencies dependencies_{};
    std::uint32_t publishedEventCount_{0};
    bool initialized_{false};
};

} // namespace isaac::runtime
