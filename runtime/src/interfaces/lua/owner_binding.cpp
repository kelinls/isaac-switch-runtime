#include "interfaces/lua/owner_binding.hpp"

#include <cstring>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {

std::size_t AttachOwnerMethods(lua_State* state, const char* owner,
                               const LuaHandlerBinding* bindings,
                               std::size_t count) noexcept {
    if (state == nullptr || owner == nullptr) {
        return 0;
    }
    const ApiCatalog& catalog = ApiCatalog::Default();
    std::size_t attached = 0;
    for (std::size_t index = 0; index < catalog.Count(); ++index) {
        const LuaApiDescriptor* descriptor = catalog.At(index);
        if (descriptor == nullptr || std::strcmp(descriptor->owner, owner) != 0) {
            continue;
        }
        lua_CFunction handler = nullptr;
        for (std::size_t row = 0; row < count; ++row) {
            if (bindings[row].id == descriptor->id) {
                handler = bindings[row].handler;
                break;
            }
        }
        if (handler == nullptr) {
            continue;
        }
        lua_pushcfunction(state, handler);
        lua_setfield(state, -2, descriptor->name);
        ++attached;
    }
    return attached;
}

} // namespace isaac::runtime
