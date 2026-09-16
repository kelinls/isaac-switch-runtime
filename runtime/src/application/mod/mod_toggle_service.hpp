#pragma once

#include "application/mod/manifest_service.hpp"
#include "domain/mod/mod_toggle_state.hpp"
#include "domain/runtime/status.hpp"
#include "ports/save_file_port.hpp"

#include <cstddef>
#include <string_view>

namespace isaac::runtime {

// 模组开关（2026-09-16，路线 B 的第一步）：把"哪些模组被关掉了"存进**游戏存档分区**，
// 并在扫描/加载之前按它过滤。
//
// 语义（照 PC）：新装的模组默认**开着**；这里只记例外 —— 用户关掉的那些目录名。
// 两个生效时机上的事实（真机验过，见 `docs/问题与解决记录.md` 续三十四）：
//   * 模组加载那一刻存档分区**已经挂好** ⇒ 开机就能读到状态，**当次生效**；
//   * 但模组的资源挂载点是在启动时建立的，所以"在菜单里关掉一个模组"要**重启游戏**才真正不加载
//     （PC 版同样要求重启）。
//
// 这个服务只做三件事：读状态、改状态、按状态过滤一批已解析的模组。它不碰 Lua、不碰 UI，
// 因此菜单（画界面）与扫描器（开机过滤）可以各自独立地用它。
class ModToggleService {
public:
    explicit ModToggleService(ISaveFilePort& files) noexcept : files_(files) {}

    // 从存档读状态。文件不存在 ⇒ 清空状态并返回 `Ok`（第一次运行就是这种情况）。
    [[nodiscard]] Status Load() noexcept;

    // 把内存里的状态写回存档（写完由适配器提交）。
    [[nodiscard]] Status Save() noexcept;

    [[nodiscard]] bool IsEnabled(std::string_view directory) const noexcept {
        return !state_.IsDisabled(directory);
    }

    // 内存里改一个模组的开关（**不落盘**，落盘由调用方在合适时机调 `Save()`）。
    [[nodiscard]] Status SetEnabled(std::string_view directory, bool enabled) noexcept {
        return state_.SetEnabled(directory, enabled);
    }

    [[nodiscard]] const ModToggleState& state() const noexcept { return state_; }
    [[nodiscard]] std::size_t DisabledCount() const noexcept { return state_.disabledCount; }

    // 用户在 SD 卡上手工放的标记文件：`<modRoot>/disable.it` 存在 ⇒ 这个模组不加载。
    // 只读，不写卡（卡上的覆盖层是只读的），所以它是一条**给用户保底**的手工路径。
    [[nodiscard]] bool HasUserDisableMarker(const char* modRoot) noexcept;

    // 按状态过滤一批已解析的模组：被关掉的（或带用户标记的）整条移出去，其余**保持原顺序**。
    // 返回值 = 移掉了几个。指针在搬动后会被重新指向新的缓冲（`ResolvedManifestMod` 只有指针，
    // 见 `manifest_service.hpp`）。
    [[nodiscard]] std::size_t ApplyToBatch(ResolvedManifestModBatch* batch) noexcept;

private:
    ISaveFilePort& files_;
    ModToggleState state_{};
};

} // namespace isaac::runtime
