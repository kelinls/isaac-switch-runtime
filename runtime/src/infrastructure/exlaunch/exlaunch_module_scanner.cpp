#include "infrastructure/exlaunch/exlaunch_module_scanner.hpp"

#include "module_finder.hpp"

#include <algorithm>
#include <cstring>

namespace isaac::runtime {

Status ExlaunchModuleScanner::WaitForTarget(const TargetModuleSpec& spec, ModuleInfo* module) noexcept {
    if (module == nullptr || spec.name == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    *module = ModuleInfo{};

    const TargetModuleScanResult scan = ScanTargetModule();
    if (scan.status == TargetModuleScanStatus::NotFound || !scan.module.has_value()) {
        return Status{StatusCode::NotFound};
    }
    if (scan.status == TargetModuleScanStatus::BuildMismatch) {
        // Path matched but the Build ID did not: the caller keeps retrying.
        return Status{StatusCode::Rejected};
    }

    const TargetModule& found = *scan.module;
    module->base = found.base;
    module->textSize = found.textSize;
    module->imageSize = found.size;
    const std::size_t copySize = std::min(module->buildId.size(), found.buildId.size());
    std::memcpy(module->buildId.data(), found.buildId.data(), copySize);
    module->valid = found.base != 0 && found.textSize != 0;
    return module->valid ? Status::Ok() : Status{StatusCode::InvalidState};
}

} // namespace isaac::runtime
