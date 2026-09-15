#pragma once

#include "ports/hook_port.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// 一个零占洞挂点的台账条目：地址与 16 字节守卫留在基础设施层，
// 应用层只通过 HookId 说"我要这个能力"（与 hook_port.hpp 的注释一致）。
struct EntryRelayBinding {
    const char* name;
    std::uintptr_t offset;         // NRO 内偏移
    const void* expectedEntry16;   // 版本 guard（16 字节）
    void* callback;                // 与目标 ABI 一致的函数指针
    std::uintptr_t* fallbackEntry; // 安装后写入；回调靠它跑原函数
};

// 安装前的**纯判定**（可在宿主机直接测，不碰任何系统调用）：
//   * target 计算与模块边界；
//   * 地址对齐（4 字节）与 16 字节不跨页 —— 与 entry_relay 的门禁保持一致；
// 通过则返回 BindingVerdict::Ok 并把 outTarget 填好。
// 现场 16 字节的逐字节比对与 PC 相对判定由 entry_relay 的安装门禁负责（单一实现处）。
enum class BindingVerdict : std::uint8_t { Ok = 0, Rejected };

// 纯判定依赖的三个几何不变量：入口 4 字节对齐、占 16 字节、且不跨 4 KiB 页。
// 口径与 `source/relay/entry_relay.hpp` 的安装门禁一致（那边是唯一写字节的地方）。
inline constexpr std::size_t kEntryRelayAlignmentBytes = 4;
inline constexpr std::size_t kEntryRelayEntryBytes = 16;
inline constexpr std::size_t kEntryRelayPageBytes = 0x1000;

// 判定的**地址部分**，编译期可求值：`base + offset` 是否指向一个可挂钩的入口。
// 恒等逻辑只此一份 —— 宿主机测试、编译期 `static_assert` 与 `.cpp` 里的判定
// 核对的是同一个函数，不会各自漂移。`outTarget` 为空时只判定、不写出。
[[nodiscard]] constexpr bool IsHookableEntryAddress(std::uintptr_t base, std::size_t codeSize,
                                                    std::uintptr_t offset,
                                                    std::uintptr_t* outTarget) noexcept {
    if (base == 0 || codeSize == 0 || offset == 0) {
        return false;
    }
    const std::uintptr_t address = base + offset;
    if (address < base || address + kEntryRelayEntryBytes < address) {
        return false;  // 地址回绕
    }
    if (address + kEntryRelayEntryBytes > base + codeSize) {
        return false;  // 越出代码窗口
    }
    if ((address % kEntryRelayAlignmentBytes) != 0 ||
        (address % kEntryRelayPageBytes) + kEntryRelayEntryBytes > kEntryRelayPageBytes) {
        return false;  // 未 4 字节对齐 / 16 字节跨页
    }
    if (outTarget != nullptr) {
        *outTarget = address;
    }
    return true;
}

// 编译期自证：判据对反例确实报错（否则上面的函数与恒真断言无异）。
static_assert(IsHookableEntryAddress(0x7100000000u, 0x700000u, 0x3F8DB8u, nullptr),
              "合法入口必须通过");
static_assert(!IsHookableEntryAddress(0x7100000000u, 0x700000u, 0x3F8DBAu, nullptr),
              "未 4 字节对齐必须拒绝");
static_assert(!IsHookableEntryAddress(0x7100000000u, 0x700000u, 0xFF8u, nullptr),
              "16 字节跨页必须拒绝");
static_assert(!IsHookableEntryAddress(0x7100000000u, 0x700000u, 0x700000u, nullptr),
              "越出代码窗口必须拒绝");
static_assert(!IsHookableEntryAddress(0, 0x700000u, 0x3F8DB8u, nullptr), "空基址必须拒绝");

[[nodiscard]] BindingVerdict EvaluateEntryRelayBinding(const EntryRelayBinding& binding,
                                                       const HookTarget& target,
                                                       std::uintptr_t* outTarget) noexcept;

// 取该挂点安装时记下的回退入口（= 槽里回放原序言的那段）。回调靠它执行原函数。
[[nodiscard]] std::uintptr_t EntryRelayFallbackEntry(HookId id) noexcept;

// `entry_relay` 的回退入口与目标函数入口同 ABI：把记录的地址转成函数指针即可调用。
template <typename Fn>
[[nodiscard]] Fn Original(HookId id) noexcept {
    return reinterpret_cast<Fn>(EntryRelayFallbackEntry(id));
}

} // namespace isaac::runtime
