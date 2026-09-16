#pragma once

#include "domain/runtime/status.hpp"

#include <cstddef>
#include <string_view>

namespace isaac::runtime {

// 游戏**存档分区**里的小文件（2026-09-16，B1 探针真机验收之后定的能力缝）。
//
// 为什么是"存档分区"而不是 SD 卡：游戏把它的存档分区挂成了 `sdmc` 这个名字
// （`SaveDataManager::GetMountPoint()` 硬编码返回 `"sdmc:/"`，初始化时截断成 `"sdmc"` 再调
// `nn::fs::MountSaveData("sdmc", uid)`），而它**根本没有导入** `nn::fs::MountSdCard` ——
// 也就是说游戏进程里没有"挂载真 SD 卡"的入口。真机验收（`docs/问题与解决记录.md` 续三十四）：
// 写 27 字节 → 重开读到 27 → **重启主机后仍然读得到**，所以这一层足以承载"模组开关状态"这类
// 需要跨启动保留的小数据。
//
// 名字一律**相对存档根**（例如 `isaac-switch-mods-state.txt`）。端口只描述"要什么"，
// 引擎细节（模块基址、导入跳板、守卫字节、对齐缓冲）全在适配器里。
class ISaveFilePort {
public:
    virtual ~ISaveFilePort() = default;

    // 读整个文件。文件不存在 ⇒ `NotFound`（**不是**错误：调用方要能区分"还没有状态"与
    // "读坏了"）；文件比 `capacity` 大 ⇒ `CapacityExceeded`（不截断，见下面 `Write` 的口径）。
    [[nodiscard]] virtual Status Read(std::string_view name, char* out, std::size_t capacity,
                                      std::size_t* size) noexcept = 0;

    // 覆盖写整个文件：文件不存在就创建、存在就截到新长度，写完**提交存档**。
    // 超过实现上限 ⇒ `CapacityExceeded`（宁可报错也不写半截 —— 半截状态比没有状态更难查）。
    [[nodiscard]] virtual Status Write(std::string_view name, std::string_view data) noexcept = 0;

    // 删掉文件并提交存档；文件本来就不存在 ⇒ 仍返回 `Ok`（"删干净了"这件事已经成立）。
    [[nodiscard]] virtual Status Remove(std::string_view name) noexcept = 0;

    // 存在性检查，路径是**完整的**（例如 `rom:/isaac_mods/mods/<dir>/disable.it`）。
    // 用途：读用户在 SD 卡上**手工**放下的标记文件 —— 只读，不经过存档分区。
    [[nodiscard]] virtual Status Exists(const char* absolutePath, bool* exists) noexcept = 0;
};

} // namespace isaac::runtime
