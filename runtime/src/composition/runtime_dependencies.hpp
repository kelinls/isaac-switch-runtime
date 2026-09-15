#pragma once

#include "ports/event_sink.hpp"
#include "ports/module_scanner_port.hpp"
#include "ports/thread_port.hpp"

#include <cstdint>

namespace isaac::runtime {

// Everything the Runtime needs from the platform, injected at composition
// time. Pointers may be null: the desktop bootstrap runs before the module
// scanner and thread port exist, and each service checks its own port.
struct RuntimeDependencies {
    IThreadPort* threads{nullptr};
    IModuleScannerPort* scanner{nullptr};
    IEventSink* events{nullptr};
    std::uint64_t buildId{0};
};

} // namespace isaac::runtime
