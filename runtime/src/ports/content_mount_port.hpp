#pragma once

#include "domain/runtime/status.hpp"

#include <string_view>

namespace isaac::runtime {

// Registers a Mod's own content directories with the engine so that the engine
// resolves files that exist only in the packaged Mod tree (the PC `resources/`
// and `content/` contract).
//
// Two properties of the engine side shape this port:
//
//   * the engine clears and rebuilds its whole mount point table when content is
//     reloaded, so mounting is not a one-time setup and a caller must re-register
//     after every rebuild;
//   * mounting enumerates the directory through `nn::fs`, which is what makes the
//     Atmosphere SD overlay visible to the resource layer — and what makes a Mod
//     mount point work without a `kage_mount_points.dat` entry.
//
// Mounting the same directory twice is not an error: the engine skips a mount
// point that already exists with an equal path.
class IContentMountPort {
public:
    virtual ~IContentMountPort() = default;

    // Mounts `<modRoot><leaf>` where `modRoot` is the Mod directory exactly as the
    // Runtime addresses it, for example `rom:/isaac_mods/mods/MuteOnPause/`. The
    // adapter converts it into the form the engine's path constructor expects, so the
    // caller never has to know how the engine spells its application content root.
    //
    // A Mod that ships no such directory is not a failure: the engine accepts the
    // mount point and its file map simply stays empty, so the caller does not have to
    // probe the tree first.
    [[nodiscard]] virtual Status MountModDirectory(std::string_view modRoot,
                                                   std::string_view leaf) noexcept = 0;
};

} // namespace isaac::runtime
