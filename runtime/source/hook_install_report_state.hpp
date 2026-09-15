#pragma once

#include "application/runtime/hook_install_service.hpp"

#include <atomic>
#include <cstdint>

namespace isaac::runtime {

// 最近一次挂点安装结果的快照口径（供 `test_run_observer.cpp` 发布到 `reserved` 的高 24 位）。
struct HookInstallReportView {
    std::uint32_t installed{0};    // 已安装挂点数
    std::uint32_t failureSlot{0};  // 第一个失败挂点 index+1；0 = 无失败
    std::uint32_t failureCode{0};  // 最近一次 entry_relay 失败码；0 = 无
};

// 模块级一份：安装发生在初始化路径，发布可能在之后任一时刻。
// 只用 32 位原子（本项目禁用布尔原子：AArch64 -Oz 下曾导致真机 Instruction Abort）。
inline std::atomic<std::uint32_t>& HookInstallInstalledCount() noexcept {
    static std::atomic<std::uint32_t> value{0};
    return value;
}
inline std::atomic<std::uint32_t>& HookInstallFailureSlot() noexcept {
    static std::atomic<std::uint32_t> value{0};
    return value;
}
inline std::atomic<std::uint32_t>& HookInstallFailureCode() noexcept {
    static std::atomic<std::uint32_t> value{0};
    return value;
}

inline void PublishHookInstallReport(const HookInstallReport& report,
                                     std::uint32_t relayFailureCode) noexcept {
    HookInstallInstalledCount().store(report.InstalledCount(), std::memory_order_relaxed);
    HookInstallFailureSlot().store(report.FirstFailureSlot(), std::memory_order_relaxed);
    HookInstallFailureCode().store(relayFailureCode, std::memory_order_relaxed);
}

inline HookInstallReportView HookInstallReportSnapshot() noexcept {
    return {HookInstallInstalledCount().load(std::memory_order_relaxed),
            HookInstallFailureSlot().load(std::memory_order_relaxed),
            HookInstallFailureCode().load(std::memory_order_relaxed)};
}

// 打包进 `TestRunSnapshot::reserved` 的高 24 位。低 8 位继续承载诊断 attach（值域 0-4），
// 所以报告整体左移 8 位；高位从项目开始至今恒为 0，旧读者忽略它们，48 字节布局不变。
//   位 8-15 已安装数 · 位 16-23 失败挂点序号 · 位 24-31 失败码
inline constexpr std::uint32_t kHookReportShift = 8;
inline constexpr std::uint32_t kHookReportFieldMask = 0xFFU;

inline std::uint32_t PackHookInstallReport(const HookInstallReportView& view) noexcept {
    return ((view.installed & kHookReportFieldMask) << 8) |
           ((view.failureSlot & kHookReportFieldMask) << 16) |
           ((view.failureCode & kHookReportFieldMask) << 24);
}

inline HookInstallReportView UnpackHookInstallReport(std::uint32_t reserved) noexcept {
    return {(reserved >> 8) & kHookReportFieldMask,
            (reserved >> 16) & kHookReportFieldMask,
            (reserved >> 24) & kHookReportFieldMask};
}

} // namespace isaac::runtime
