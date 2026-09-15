#pragma once

#include <cstdint>

namespace isaac::runtime {

// Which thread an entry point is allowed to run on. The Facade validates this
// before dispatching, so a Lua API can never run on an unexpected thread.
enum class ThreadAffinity : std::uint8_t {
    Any = 0,
    RuntimeWorker,
    MainUpdate,
    MainRender,
    ManagedCallback,
};

[[nodiscard]] constexpr const char* ToString(ThreadAffinity affinity) noexcept {
    switch (affinity) {
        case ThreadAffinity::Any: return "Any";
        case ThreadAffinity::RuntimeWorker: return "RuntimeWorker";
        case ThreadAffinity::MainUpdate: return "MainUpdate";
        case ThreadAffinity::MainRender: return "MainRender";
        case ThreadAffinity::ManagedCallback: return "ManagedCallback";
    }
    return "Unknown";
}

} // namespace isaac::runtime
