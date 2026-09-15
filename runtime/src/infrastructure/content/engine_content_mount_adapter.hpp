#pragma once

#include "ports/content_mount_port.hpp"

namespace isaac::runtime {

// Bridges IContentMountPort onto the engine's KAGE content manager.
//
// Every offset is guarded: the target module's bytes are compared against the
// 16-byte guards recorded in `runtime_constants.hpp` before any call is made, and
// a mismatch fails closed instead of calling into an unverified address. The
// guards and the call sequence were confirmed on hardware (crash report
// `01789131431`), where the console's own symbolizer named every callee at the
// audited offset.
class EngineContentMountAdapter final : public IContentMountPort {
public:
    [[nodiscard]] Status MountModDirectory(std::string_view modDirectory,
                                           std::string_view leaf) noexcept override;
};

} // namespace isaac::runtime
