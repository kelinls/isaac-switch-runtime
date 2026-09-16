#pragma once

#include "domain/mod/mod_manifest.hpp"
#include "domain/runtime/status.hpp"

#include <array>
#include <cstddef>
#include <string_view>

namespace isaac::runtime {

// 一个子目录名（引擎列出来的那一种）。容量沿用清单那边的约定，
// 这样"发现的目录"与"清单里写的目录"在下面几层看来是同一个东西。
struct ModDirectoryEntry {
    std::array<char, kModDirectoryCapacity> name{};
};

// 列目录的能力缝（2026-09-16，方案 A）。
//
// 为什么需要它：`isaac_mods/manifest.json` 现在必须由 PC 侧工具生成，用户加一个模组就要
// 重跑一次工具。而 PC 的原生语义是"把文件夹丢进 `mods/` 就生效"，所以运行时应当能自己列出
// `isaac_mods/mods/` 下的子目录。**这件事只能交给引擎自己的文件层**：
//   * 游戏自己的 `ModManager::ListMods()` 在真机上枚举会中断（`docs/开发规范.md` 第 26 条）；
//   * 我们模块自己起 `sm`/`fs` 会话也是死的（内核 `0x10801` OutOfResource）；
//   * 引擎的 `KAGE::Filesys::IContentManager::GetDirectoryEntries` 用的是游戏进程已有的
//     fs 会话 —— 真机已证实它能列出覆盖层里的模组目录（`docs/问题与解决记录.md` 续三十一）。
//
// 端口只描述"要什么"，引擎细节（模块基址、槽位、守卫字节、内存释放）全在适配器里。
class IModDirectoryPort {
public:
    virtual ~IModDirectoryPort() = default;

    // 列出 `relativePath` 下的**子目录**名字（不含文件），按名字**升序**写进 `out`。
    //
    // `relativePath` 是相对**应用根**的路径（与 `IContentMountPort::MountModDirectory` 同一口径）：
    // 例如 `isaac_mods/mods`，不带 `rom:/` 前缀。
    //
    // 目录数超过 `capacity` 时返回 `CapacityExceeded`（**不截断**）—— 截断会让某些模组安静地
    // 不被加载，症状是"某个模组没生效"却查不出原因。
    [[nodiscard]] virtual Status ListSubdirectories(std::string_view relativePath,
                                                    ModDirectoryEntry* out,
                                                    std::size_t capacity,
                                                    std::size_t* count) noexcept = 0;
};

} // namespace isaac::runtime
