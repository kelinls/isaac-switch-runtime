#include "application/runtime/module_scan_service.hpp"

namespace isaac::runtime {

Result<ModuleInfo> ModuleScanService::ScanOnce(const TargetModuleSpec& spec) noexcept {
    ModuleInfo module{};
    const Status status = scanner_.WaitForTarget(spec, &module);
    if (!status.ok()) {
        return status;
    }
    if (!module.valid) {
        // A port that reports success without a usable module is a contract
        // violation, not a successful scan.
        return Status{StatusCode::InvalidState};
    }
    return module;
}

Result<ModuleInfo> ModuleScanService::ScanWithRetry(const TargetModuleSpec& spec,
                                                    const ScanPolicy& policy) noexcept {
    attempts_ = 0;
    Status lastFailure{StatusCode::NotFound};
    if (policy.maxAttempts == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    for (std::uint32_t attempt = 0; attempt < policy.maxAttempts; ++attempt) {
        ++attempts_;
        const Result<ModuleInfo> result = ScanOnce(spec);
        if (result.ok()) {
            return result;
        }
        const StatusCode code = result.code();
        if (code == StatusCode::Rejected) {
            // Seen a matching path with a different Build ID; keep looking but
            // remember the strongest evidence if nothing better shows up.
            lastFailure = result.status();
        } else if (code == StatusCode::NotFound) {
            if (lastFailure.code() != StatusCode::Rejected) {
                lastFailure = result.status();
            }
        } else {
            return result;
        }
        const bool lastAttempt = attempt + 1 == policy.maxAttempts;
        if (lastAttempt) {
            break;
        }
        if (threads_ != nullptr && policy.intervalMilliseconds != 0) {
            threads_->SleepMilliseconds(policy.intervalMilliseconds);
        }
    }
    return lastFailure;
}

} // namespace isaac::runtime
