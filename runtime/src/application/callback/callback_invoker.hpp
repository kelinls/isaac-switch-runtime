#pragma once

#include "domain/callback/callback_descriptor.hpp"
#include "domain/runtime/status.hpp"

namespace isaac::runtime {

// Calls one registered callback. The Lua adapter implements this interface, so
// the dispatcher never sees a lua_State and never links against Lua.
class ICallbackInvoker {
public:
    virtual ~ICallbackInvoker() = default;

    // A failure is isolated to this callback; the dispatcher keeps going.
    [[nodiscard]] virtual Status Invoke(const CallbackDescriptor& descriptor) noexcept = 0;
};

} // namespace isaac::runtime
