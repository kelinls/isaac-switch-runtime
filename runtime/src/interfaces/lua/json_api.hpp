#pragma once

extern "C" {
#include <lua.h>
}

namespace isaac::runtime {

// The PC-Lua `json` module that Mods obtain with `local json = require("json")`.
//
// EID calls `require("json")` unconditionally while it loads, so the Runtime has to publish
// `encode` / `decode` before any Mod chunk runs. `json` is not an owner-based API family and is
// deliberately absent from `api_catalog.*`: it is one global table with two plain `lua_CFunction`
// members, so there is nothing for the descriptor table to drive.
//
// Published shape (PC-compatible):
//
//   json.encode(value) -> string   nil/boolean/number/string/table, nested. A table whose keys are
//                                  exactly `1..n` becomes a JSON array, every other table becomes
//                                  an object (so `{}` encodes as `{}`, not `[]`). Functions,
//                                  userdata, threads, non-finite numbers and tables that contain
//                                  themselves raise a Lua error instead of crashing or looping.
//   json.decode(text)  -> value    Standard JSON. `null` becomes Lua `nil`, which means a `null`
//                                  object member is not stored at all; `[]` and `{}` both become
//                                  tables. Malformed input raises a Lua error and never returns a
//                                  partially built value.
//
// The implementation is self-contained: it needs the Lua C API only, allocates through the Lua
// allocator (no static scratch buffer, nothing added to the read-only segment per call), uses no
// C++ exceptions/RTTI and no third-party dependency.
void RegisterJsonModule(lua_State* state);

} // namespace isaac::runtime
