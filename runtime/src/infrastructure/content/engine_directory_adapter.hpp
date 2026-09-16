#pragma once

#include "ports/mod_directory_port.hpp"

namespace isaac::runtime {

// 列目录能力的**引擎实现**：调 `KAGE::Filesys::IContentManager::GetDirectoryEntries`
// （`0x4C23C4`），把结果解成"子目录名"。
//
// 为什么这样才可行（2026-09-16 真机取证，见 `docs/问题与解决记录.md` 续三十一）：
//   * 我们模块自己起 `sm`/`fs` 会话是死的（内核 `0x10801` OutOfResource）；
//   * 引擎这套用的是**游戏进程已有的 fs 会话** —— 真机实测能列出覆盖层里的两个模组目录，
//     名字读回来逐字节对上；
//   * 条目结构：返回的指针 = **第一个条目**（条目数走出参给），每项 `0x10` 字节，
//     `+0x00` 标志字节（bit1 = 目录）、`+0x08` 名字指针；
//   * ⚠️ **只对"挂载点根那一层"的路径安全**：对子目录再调一次会触发引擎内部断言
//     （崩溃报告 `01789549162`）⇒ 本适配器只允许列 `isaac_mods/mods` 这一层，
//     其它路径一律拒绝（见 `ListSubdirectories` 的前置检查）。
//
// 与 `EngineContentMountAdapter` 同一形态：只在**游戏线程**调用，入口先用 16 字节守卫核对，
// 解析结果一次成功后缓存；列出来的那块内存用**引擎的** `operator delete[]` 释放
// （它是引擎 `new[]` 分配的，用我们的 `free` 会踩坏游戏堆）。
class EngineDirectoryAdapter final : public IModDirectoryPort {
public:
    [[nodiscard]] Status ListSubdirectories(std::string_view relativePath,
                                            ModDirectoryEntry* out,
                                            std::size_t capacity,
                                            std::size_t* count) noexcept override;
};

} // namespace isaac::runtime
