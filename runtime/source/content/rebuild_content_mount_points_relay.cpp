// Rebuild relay callback: the engine has just rebuilt its content mount point table,
// which drops every Mod mount point with it, so this is where the Runtime puts them
// back.
//
// Lives in a subdirectory of `source/` on purpose: a new root-level `.cpp` is not
// collected by the build (only subdirectories of the source root are), and a missing
// translation unit would show up as an undefined reference at link time.
//
// The callback body is deliberately small and allocation-free: it runs inside
// `RebuildContentMountPoints()`'s return path, on the game thread, possibly before a
// Mod has ever been registered (in which case the session service is still null and
// there is nothing to restore).

#include "application/mod/content_mount_service.hpp"

#include <cstdint>

namespace {

// Reentrancy guard. The callback mounts directories, and mounting goes
// `AddMountPoint -> ContentMountPoint::Build -> FileMap::build -> nn::fs` enumeration.
// Static analysis says nothing on that path rebuilds the mount point table, but the
// cost of being wrong is unbounded recursion inside the engine, and unlinking the
// relay mid-recursion would leave a half-published slot, so the guard is unconditional.
std::uint32_t g_rebuildRelayDepth = 0;
std::uint32_t g_rebuildRelayFired = 0;
std::uint32_t g_rebuildRelayRemountFailures = 0;

}  // namespace

extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_RebuildContentMountPointsRelay() {
    if (g_rebuildRelayDepth != 0) {
        return;
    }
    g_rebuildRelayDepth = 1;
    ++g_rebuildRelayFired;

    isaac::runtime::ContentMountService* service =
        isaac::runtime::SessionContentMountService();
    if (service != nullptr) {
        const isaac::runtime::Status status = service->RemountAll();
        if (!status.ok()) {
            ++g_rebuildRelayRemountFailures;
        }
    }

    g_rebuildRelayDepth = 0;
}

// Probe/observability surface: how often the relay ran and how many of those runs could
// not remount. Read by the update-callback probe payload, so the fact that the relay
// actually fired is evidence rather than an assumption.
extern "C" __attribute__((visibility("hidden"))) std::uint32_t
IsaacModRuntime_RebuildContentMountPointsRelayFiredCount() {
    return g_rebuildRelayFired;
}

extern "C" __attribute__((visibility("hidden"))) std::uint32_t
IsaacModRuntime_RebuildContentMountPointsRelayFailureCount() {
    return g_rebuildRelayRemountFailures;
}
