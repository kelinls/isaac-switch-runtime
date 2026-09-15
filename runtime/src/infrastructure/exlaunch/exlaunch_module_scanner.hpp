#pragma once

#include "ports/module_scanner_port.hpp"

namespace isaac::runtime {

// Bridges IModuleScannerPort onto the existing verified module scanner
// (module_finder.cpp). The bounded retry loop stays in ModuleScanService; this
// adapter performs exactly one scan.
class ExlaunchModuleScanner final : public IModuleScannerPort {
public:
    [[nodiscard]] Status WaitForTarget(const TargetModuleSpec& spec,
                                       ModuleInfo* module) noexcept override;
};

} // namespace isaac::runtime
