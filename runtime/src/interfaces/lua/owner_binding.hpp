#pragma once

#include "interfaces/lua/api_catalog.hpp"
#include "interfaces/lua/field_api.hpp"

#include <cstddef>
#include <cstdint>

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// One row of a family's `descriptor id -> handler` table. Families own their
// table next to the handlers they expose, so adding a method to a family means
// adding one catalog descriptor and one row here.
struct LuaHandlerBinding {
    std::uint32_t id;
    lua_CFunction handler;
};

// Attaches every catalog descriptor whose owner matches `owner` and whose id has
// a binding. Ids without a binding are skipped, so a catalog entry can never
// publish a null function, and the loop itself lives in one place instead of
// being copied into every family translation unit.
//
// `fieldRows`/`rowCount` 可以省略；给了就把整张**字段读取型 API 数据行**表一并挂上：
// 每个 API 用共享处理器 `FieldApiHandler` + 该行作为闭包上值注册。
// 为什么直接吃 `FieldApiRow`（而不是再造一张 `id → (handler, row)` 表）：id 已经在行里，
// 再抄一份等于每个 API 多花 8–16 字节只读数据 —— 而这套机制存在的意义就是把每个 API 压到
// 一行数据。行表与手写绑定表 `id` 重复时**以手写为准**（便于逐条迁移）。
//
// Returns the number of attached methods.
[[nodiscard]] std::size_t AttachOwnerMethods(lua_State* state, const char* owner,
                                             const LuaHandlerBinding* bindings,
                                             std::size_t count,
                                             const FieldApiRow* fieldRows = nullptr,
                                             std::size_t rowCount = 0) noexcept;

} // namespace isaac::runtime
