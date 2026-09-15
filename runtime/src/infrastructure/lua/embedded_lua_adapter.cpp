#include "infrastructure/lua/embedded_lua_adapter.hpp"

#include "lua_runtime.hpp"

namespace isaac::runtime {
namespace {

Status MapLuaInit(LuaRuntime::LuaInitResult result) noexcept {
    return result == LuaRuntime::LuaInitResult::Success ? Status::Ok()
                                                       : Status{StatusCode::Rejected};
}

} // namespace

Status EmbeddedLuaAdapter::Initialize() noexcept {
    return MapLuaInit(LuaRuntime::Initialize());
}

bool EmbeddedLuaAdapter::IsReady() const noexcept {
    return LuaRuntime::IsReady();
}

Status EmbeddedLuaAdapter::RunChunk(const LuaChunk& chunk) noexcept {
    if (chunk.name == nullptr || chunk.bytes == nullptr || chunk.size == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    return MapLuaInit(
        LuaRuntime::InitializeFromBuffer(chunk.bytes, chunk.size, chunk.name));
}

Status EmbeddedLuaAdapter::LoadManifestMod(const ManifestModLoad& load,
                                           std::uint32_t* failureDetail) noexcept {
    if (failureDetail != nullptr) {
        *failureDetail = 0;
    }
    if (load.entryBytes == nullptr || load.entryLength == 0 || load.chunkName == nullptr ||
        load.modRoot == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    // The engine reads sibling files through the same bindings, so `require`
    // keeps working exactly as before.
    const LuaRuntime::LuaInitResult result = LuaRuntime::InitializeManifestMod(
        load.entryBytes, load.entryLength, load.chunkName, load.modRoot, bindings_);
    if (result != LuaRuntime::LuaInitResult::Success && failureDetail != nullptr) {
        *failureDetail = static_cast<std::uint32_t>(result);
    }
    return MapLuaInit(result);
}

Status EmbeddedLuaAdapter::CallGlobal(const char*, int, int*) noexcept {
    return Status{StatusCode::Unsupported};
}

void EmbeddedLuaAdapter::Shutdown() noexcept {}

} // namespace isaac::runtime
