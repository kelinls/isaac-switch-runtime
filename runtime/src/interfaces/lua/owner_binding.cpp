#include "interfaces/lua/owner_binding.hpp"

#include <cstring>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {

std::size_t AttachOwnerMethods(lua_State* state, const char* owner,
                               const LuaHandlerBinding* bindings,
                               std::size_t count,
                               const FieldApiRow* fieldRows,
                               std::size_t rowCount) noexcept {
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
        if (handler != nullptr) {
            lua_pushcfunction(state, handler);
            lua_setfield(state, -2, descriptor->name);
            ++attached;
            continue;
        }
        // 字段读取型：共享处理器 + 一行数据。行作为**闭包上值**带进去，
        // 于是同一个 `lua_CFunction` 能服务成百上千个 API，每个只占一行只读数据。
        for (std::size_t row = 0; row < rowCount; ++row) {
            if (fieldRows[row].id != descriptor->id) {
                continue;
            }
            lua_pushlightuserdata(state, const_cast<FieldApiRow*>(&fieldRows[row]));
            lua_pushcclosure(state, &FieldApiHandler, 1);
            lua_setfield(state, -2, descriptor->name);
            ++attached;
            break;
        }
    }
    return attached;
}

} // namespace isaac::runtime
