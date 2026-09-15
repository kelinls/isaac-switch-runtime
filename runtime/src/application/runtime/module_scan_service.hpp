#pragma once

#include "domain/runtime/status.hpp"
#include "ports/module_scanner_port.hpp"
#include "ports/thread_port.hpp"

#include <cstdint>

namespace isaac::runtime {

// Bounded module discovery policy. The retry loop belongs here rather than in
// the worker thread so the timing rule has one owner and is host-testable.
struct ScanPolicy {
    std::uint32_t maxAttempts{300};
    std::uint32_t intervalMilliseconds{100};
};

class ModuleScanService {
public:
    ModuleScanService(IModuleScannerPort& scanner, IThreadPort* threads) noexcept
        : scanner_(scanner), threads_(threads) {}

    [[nodiscard]] Result<ModuleInfo> ScanOnce(const TargetModuleSpec& spec) noexcept;

    // Retries only on "not found yet" and "found but wrong build": a module that
    // matches the path but not the Build ID must not stop the retry loop, while
    // an invalid specification must fail immediately instead of spinning.
    [[nodiscard]] Result<ModuleInfo> ScanWithRetry(const TargetModuleSpec& spec,
                                                   const ScanPolicy& policy) noexcept;

    [[nodiscard]] std::uint32_t attempts() const noexcept { return attempts_; }

private:
    IModuleScannerPort& scanner_;
    IThreadPort* threads_;
    std::uint32_t attempts_{0};
};

} // namespace isaac::runtime
