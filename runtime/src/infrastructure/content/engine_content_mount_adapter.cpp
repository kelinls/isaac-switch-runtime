#include "infrastructure/content/engine_content_mount_adapter.hpp"

#include "module_finder.hpp"
#include "runtime_constants.hpp"

#include <array>
#include <cstdint>
#include <cstring>

namespace isaac::runtime {
namespace {

// `ContentMountPointPath` as its constructor and destructor use it: the path pointer
// at +0x00 and the add-on-content index at +0x08 (the string constructor stores -1,
// meaning "application content"). The audited call sites reserve 0x18 bytes of stack
// for the object, so the same footprint is reserved here.
struct ContentMountPointPathLike {
    const char* path;
    std::int32_t aocIndex;
    std::int32_t reserved;
    std::uint64_t tail;
};
static_assert(offsetof(ContentMountPointPathLike, path) == 0x00);
static_assert(offsetof(ContentMountPointPathLike, aocIndex) == 0x08);
static_assert(sizeof(ContentMountPointPathLike) == 0x18);

using PathCtorFn = void (*)(ContentMountPointPathLike*, const char*);
using PathDtorFn = void (*)(ContentMountPointPathLike*);
using AddMountPointFn = void (*)(void*, const ContentMountPointPathLike*);

// Verified once per session: the module base plus the three guarded entry points.
// The game thread is the only caller (the Mod load path and the rebuild relay both
// run there), so no locking is needed.
struct ResolvedEngineCalls {
    bool valid{false};
    void* contentManager{nullptr};
    PathCtorFn pathCtor{nullptr};
    PathDtorFn pathDtor{nullptr};
    AddMountPointFn addMountPoint{nullptr};
};

bool GuardedCode(const TargetModule& module, std::uintptr_t offset,
                 const std::array<u8, 16>& expected) {
    if (!module.Contains(module.base + offset, expected.size())) {
        return false;
    }
    return std::memcmp(reinterpret_cast<const void*>(module.base + offset),
                       expected.data(), expected.size()) == 0;
}

ResolvedEngineCalls ResolveEngineCalls() {
    ResolvedEngineCalls calls{};
    const std::optional<TargetModule> module = FindTargetModule();
    if (!module.has_value()) {
        return calls;
    }
    if (!GuardedCode(*module, kContentMountPointPathCtorOffset,
                     kContentMountPointPathCtorExpectedBytes) ||
        !GuardedCode(*module, kContentMountPointPathDtorOffset,
                     kContentMountPointPathDtorExpectedBytes) ||
        !GuardedCode(*module, kContentAddMountPointOffset,
                     kContentAddMountPointExpectedBytes)) {
        // A partially matching deployment must not call into the game: without the
        // guards the offsets would be trusted blindly and could call a stray address
        // on the game thread.
        return calls;
    }
    const auto* managerSlot = reinterpret_cast<void* const*>(
        module->base + kContentManagerSlotOffset);
    if (managerSlot == nullptr || *managerSlot == nullptr) {
        return calls;
    }
    calls.contentManager = *managerSlot;
    calls.pathCtor = reinterpret_cast<PathCtorFn>(module->base + kContentMountPointPathCtorOffset);
    calls.pathDtor = reinterpret_cast<PathDtorFn>(module->base + kContentMountPointPathDtorOffset);
    calls.addMountPoint =
        reinterpret_cast<AddMountPointFn>(module->base + kContentAddMountPointOffset);
    calls.valid = true;
    return calls;
}

// Deliberately not a function-local `static` with dynamic initialisation: this module
// is linked with `-nostartfiles`, so a magic-static guard would depend on runtime
// pieces the module does not run. A plain flag keeps the retry explicit instead: while
// the game module is not mapped yet every call fails closed, and the first success is
// cached because the guards cannot change within a session.
ResolvedEngineCalls g_engineCalls{};
bool g_engineCallsResolved = false;

const ResolvedEngineCalls& EngineCalls() {
    if (!g_engineCallsResolved) {
        const ResolvedEngineCalls calls = ResolveEngineCalls();
        if (calls.valid) {
            g_engineCalls = calls;
            g_engineCallsResolved = true;
        }
        return g_engineCalls;
    }
    return g_engineCalls;
}

constexpr std::size_t kRelativePathCapacity = 256;

}  // namespace

namespace {

// `rom:/isaac_mods/mods/<dir>/` -> `isaac_mods/mods/<dir>`: the engine's
// `ContentMountPointPath` constructor joins its argument with
// `ContentManager::ApplicationMountPoint()` itself, so passing the `rom:/` prefix
// through would produce `rom:/rom:/...`.
std::string_view RelativeToApplicationRoot(std::string_view modRoot) noexcept {
    constexpr std::string_view kApplicationPrefix = "rom:/";
    if (modRoot.rfind(kApplicationPrefix, 0) == 0) {
        modRoot.remove_prefix(kApplicationPrefix.size());
    }
    while (!modRoot.empty() && modRoot.back() == '/') {
        modRoot.remove_suffix(1);
    }
    return modRoot;
}

}  // namespace

Status EngineContentMountAdapter::MountModDirectory(std::string_view modRoot,
                                                    std::string_view leaf) noexcept {
    if (modRoot.empty() || leaf.empty()) {
        return Status{StatusCode::InvalidArgument};
    }
    const std::string_view relativeRoot = RelativeToApplicationRoot(modRoot);
    if (relativeRoot.empty() || leaf.front() == '/' || leaf.back() == '/') {
        return Status{StatusCode::InvalidArgument};
    }

    char relativePath[kRelativePathCapacity];
    const std::size_t rootLength = relativeRoot.size();
    const std::size_t leafLength = leaf.size();
    if (rootLength + 1 + leafLength + 1 > sizeof(relativePath)) {
        return Status{StatusCode::CapacityExceeded};
    }
    std::memcpy(relativePath, relativeRoot.data(), rootLength);
    relativePath[rootLength] = '/';
    std::memcpy(relativePath + rootLength + 1, leaf.data(), leafLength);
    relativePath[rootLength + 1 + leafLength] = '\0';

    const ResolvedEngineCalls& calls = EngineCalls();
    if (!calls.valid) {
        return Status{StatusCode::NotFound};
    }

    ContentMountPointPathLike path{};
    calls.pathCtor(&path, relativePath);
    calls.addMountPoint(calls.contentManager, &path);
    calls.pathDtor(&path);
    return Status::Ok();
}

} // namespace isaac::runtime
