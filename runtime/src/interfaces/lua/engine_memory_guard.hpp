#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

// `<atomic>` 现在是无条件引入的：缓存自旋锁与派发/开关标志在生产构建里也要用（见下），
// 不再只服务于 `PROBE_BREAK=1` 的计数器。

namespace isaac::runtime {

// ============================================================================
// 引擎内存可读性：每次访问一次系统调用 → 派发作用域内的区间缓存
// ============================================================================
//
// **问题（2026-09-13 卡顿归因）**：`IsEngineMemoryReadable` 原先对每一次引擎字段读取都调用
// `svcQueryMemory`。真机上那是内核入口（系统调用），而 EID 的"物品信息显示"路径每帧要做
// 上千次这种读取：
//
//   * `entity.Type` / `.Variant` / `.SubType` 这类字段访问 = 句柄 vptr 校验 1 次 + 字段读 1 次；
//   * `entity:GetData()` 一次 ≈ 8–11 次（`ResolveRoom` 三级链 + 容器指纹 + 表描述符 + 出生帧），
//     而 EID 的 `getEntityData` 每次调用都要走两遍 `entity:GetData()`；
//   * `Isaac.FindInRadius` 每帧每玩家一次，对活实体表逐元素还要再各校验一次。
//
// PC 上这些是直接读内存，所以同一条 Lua 代码在 PC 上不卡；在 Switch 上系统调用把帧预算吃掉，
// 表现就是"描述一出现就卡"。
//
// **做法**：`svcQueryMemory` 成功时返回的 `MemoryInfo` 描述的是一个**连续且权限一致**的映射
// 区间，所以整个 `[addr, addr + size)` 都能复用那一次结论。调用方（四个 API 族各自那份
// `IsEngineMemoryReadable`，它们已经持有 libnx 的 `MemoryInfo`）在系统调用成功后把区间登记
// 到这里；下一次落在同一区间里的检查直接命中缓存，不再进内核。
//
// **为什么缓存只在一次受管回调派发内有效**：Lua 只在游戏线程的派发里跑，这段时间内没有
// 任何东西会改页表，所以缓存条目不可能过期 —— 过期窗口为零，比"按时间过期"更安全。
// 出了派发（`EngineGuardLeaveDispatch`）就关闭，任何非派发路径（Mod 加载、绑定解析）仍然
// 每次都做真正的系统调用。
//
// **线程归属（2026-09-13 修正）**：上面那句"派发只在游戏线程上跑"只对**写**这一侧成立。
// 读这一侧不是：宿主插件的线程在绑定解析阶段也会走同一段可读性检查（`EngineGuardProbe*`
// 的原子计数器就是为此加的）。所以缓存、派发标志和 A/B 开关都必须是原子的，否则
// "游戏线程 `Insert`/`Clear` + 插件线程 `Covers`"会撕裂读，让 `Covers()` 对并不可读的区间
// 返回 true。这是本文件 2026-09-13 修掉的数据竞争。
//
// **本头文件不引用任何 libnx**：设备侧 4 个 TU 里已经有 `MemoryInfo`/`Perm_R`，而 vendored
// libnx 与 devkitpro libnx 的 `MemoryInfo` 字段顺序不同（`ipc_refcount`/`device_refcount`
// 互换），把 libnx 头拉进同时包含 `<switch.h>` 的编译单元会直接编译失败。所以这里只做
// "缓存 + 计数"，平台细节留在各自 TU 里（本模块只读 `addr`/`size`/`perm` 三个字段，两种
// 布局下逐字相同）。
struct EngineReadableRegion {
    std::uintptr_t begin{0};
    std::uintptr_t end{0};
};

// 一帧里真正会被触及的映射区间很少（模块映像 + 游戏堆 + 少量独立分配），8 个槽位足够；
// 槽位只做轮转覆盖，不做 LRU —— 命中率对淘汰策略不敏感，而简单实现更容易验证。
constexpr std::size_t kEngineReadableRegionCapacity = 8;

struct EngineReadableRegionCache {
    EngineReadableRegion entries[kEngineReadableRegionCapacity]{};
    std::size_t count{0};
    std::size_t victim{0};

    // 自旋锁：只保护 `entries` / `count` / `victim` 这三个成员。
    //
    // 为什么需要它：缓存本身只在派发作用域内有效，而派发（`DispatchPostUpdate` /
    // `DispatchPostRender`）只在游戏线程上跑；但**读**这一侧不是 —— 宿主插件的线程在绑定解析
    // 阶段也会走同一段可读性检查。于是"游戏线程在 `Insert`/`Clear`、插件线程同时在 `Covers`"
    // 是真实可达的交错，普通成员在这种交错下会被撕裂读，可能让 `Covers()` 对一个并不可读的
    // 区间返回 true，进而让上层去读无效内存。
    //
    // 临界区只有十几条指令，且绝大多数时候没有竞争（读写都在游戏线程上），所以自旋的成本
    // 远低于它省掉的那次 `svcQueryMemory` 内核入口。
    //
    // **必须是 32 位原子，不能用布尔型 `atomic<bool>`**：本项目的 AArch64 `-Oz` 构建下，
    // 布尔型原子的 `load()` 会生成对 PLT 跳板的调用，而该跳板落在可执行文本边界之后，
    // 真机表现为 `Instruction Abort`、PC 恰为 `__text_end__`（见 `docs/问题与解决记录.md`
    // 的「2026-09-04」布尔原子 PLT 一节）。项目统一改用 32 位 `0/1` 原子。
    mutable std::atomic<std::uint32_t> locked{0};

    void Lock() const noexcept {
        while (locked.exchange(1, std::memory_order_acquire)) {
        }
    }

    void Unlock() const noexcept {
        locked.store(0, std::memory_order_release);
    }

    void Clear() noexcept {
        Lock();
        count = 0;
        victim = 0;
        Unlock();
    }

    // `address + length` 必须已经被调用方做过回绕检查。
    bool Covers(std::uintptr_t address, std::size_t length) const noexcept {
        if (length == 0) {
            return false;
        }
        const std::uintptr_t end = address + length;
        Lock();
        bool covered = false;
        for (std::size_t index = 0; index < count; ++index) {
            if (address >= entries[index].begin && end <= entries[index].end) {
                covered = true;
                break;
            }
        }
        Unlock();
        return covered;
    }

    void Insert(std::uintptr_t begin, std::uintptr_t end) noexcept {
        if (end <= begin) {
            return;
        }
        Lock();
        if (count < kEngineReadableRegionCapacity) {
            entries[count].begin = begin;
            entries[count].end = end;
            ++count;
        } else {
            entries[victim].begin = begin;
            entries[victim].end = end;
            victim = (victim + 1) % kEngineReadableRegionCapacity;
        }
        Unlock();
    }
};

// 整个模块只有一份：与 `api_sequence_probe.hpp` 同例，函数内 `static` 保证单实例。
inline EngineReadableRegionCache& EngineGuardRegionCache() noexcept {
    static EngineReadableRegionCache cache;
    return cache;
}

// 派发作用域标志：只有派发期间才允许用缓存。
//
// 与缓存同因：写侧是游戏线程，读侧可能来自宿主插件的线程，普通 `bool` 会构成数据竞争。
// 用 32 位 `0/1` 原子而不是布尔型 `atomic<bool>`（AArch64 `-Oz` 下会生成越界的 PLT 跳板，
// 见 `EngineReadableRegionCache::locked` 的说明）。
inline std::atomic<std::uint32_t>& EngineGuardDispatchActive() noexcept {
    static std::atomic<std::uint32_t> active{0};
    return active;
}

// A/B 开关：探针构建在同一会话里对照"有缓存/没缓存"。生产构建恒为 1。
inline std::atomic<std::uint32_t>& EngineGuardCacheEnabledFlag() noexcept {
    static std::atomic<std::uint32_t> enabled{1};
    return enabled;
}

inline bool EngineGuardCacheEnabled() noexcept {
    return EngineGuardCacheEnabledFlag().load(std::memory_order_acquire) != 0;
}

inline void EngineGuardSetCacheEnabled(bool enabled) noexcept {
    EngineGuardCacheEnabledFlag().store(enabled ? 1U : 0U, std::memory_order_release);
    // 切换一定要丢掉旧条目：否则"关掉缓存"的那一相还会命中上一相留下的区间。
    EngineGuardRegionCache().Clear();
}

inline void EngineGuardEnterDispatch() noexcept {
    EngineGuardRegionCache().Clear();
    EngineGuardDispatchActive().store(1U, std::memory_order_release);
}

inline void EngineGuardLeaveDispatch() noexcept {
    EngineGuardDispatchActive().store(0U, std::memory_order_release);
    EngineGuardRegionCache().Clear();
}

// 调用点（设备侧）用法：
//
//     if (!EngineGuardRangeUsable(address, length)) return false;
//     if (EngineGuardLookupCached(address, length)) return true;
//     const std::uint64_t start = EngineGuardSyscallBegin();
//     ... svcQueryMemory ...
//     EngineGuardSyscallEnd(start);
//     ... 区间与权限判定通过后 ...
//     EngineGuardRememberReadable(info.addr, info.addr + info.size);
//
// 这些入口在宿主构建里都不会被调用（宿主分支直接返回 true），仍然是普通 inline 函数，
// 所以不引入任何设备侧符号。

// ============================================================================
// 时间戳：`cntvct_el0` / `cntfrq_el0`，不使用 `svcGetSystemTick()`
// ============================================================================
// 用系统寄存器而不是 libnx 的 `svcGetSystemTick()`，理由与上面同一条：本头文件不能引用
// libnx（vendored 与 devkitpro 的 `MemoryInfo` 布局不同，把 libnx 头拉进已经包含
// `<switch.h>` 的编译单元会直接编译失败）。这两个寄存器在 EL0 可读，`mrs` 不需要头文件。
// 非探针构建恒返回 0 —— 计时只在 A/B 探针里用。
inline std::uint64_t EngineGuardTick() noexcept {
#if defined(EXL_PROBE_BREAK) && defined(__aarch64__)
    std::uint64_t value = 0;
    __asm__ __volatile__("mrs %0, cntvct_el0" : "=r"(value));
    return value;
#else
    return 0;
#endif
}

// Tegra X1 上 `cntfrq_el0` = 19.2 MHz；读不到时按这个值兜底。
constexpr std::uint64_t kEngineGuardFallbackTicksPerSecond = 19200000ULL;

inline std::uint64_t EngineGuardTicksPerSecond() noexcept {
#if defined(__aarch64__)
    std::uint64_t value = 0;
    __asm__ __volatile__("mrs %0, cntfrq_el0" : "=r"(value));
    return value != 0 ? value : kEngineGuardFallbackTicksPerSecond;
#else
    return kEngineGuardFallbackTicksPerSecond;
#endif
}

#if defined(EXL_PROBE_BREAK)
// ---------------------------------------------------------------------------
// 探针计数（只有 `PROBE_BREAK=1` 的构建才累加）
// ---------------------------------------------------------------------------
// `std::atomic` + relaxed：只读路径在游戏线程上，但绑定解析阶段可能由宿主插件的线程
// 触发同一段检查，普通全局会变成数据竞争；relaxed 原子在这个量级上开销可以忽略。
inline std::atomic<std::uint64_t>& EngineGuardProbeAttempts() noexcept {
    static std::atomic<std::uint64_t> value{0};
    return value;
}

inline std::atomic<std::uint64_t>& EngineGuardProbeSyscalls() noexcept {
    static std::atomic<std::uint64_t> value{0};
    return value;
}

inline std::atomic<std::uint64_t>& EngineGuardProbeCacheHits() noexcept {
    static std::atomic<std::uint64_t> value{0};
    return value;
}

inline std::atomic<std::uint64_t>& EngineGuardProbeSyscallTicks() noexcept {
    static std::atomic<std::uint64_t> value{0};
    return value;
}

// 进一次 `svcQueryMemory`：先把调用次数加一，再取起始时间戳；返回时间戳交给 End 收尾。
inline std::uint64_t EngineGuardSyscallBegin() noexcept {
    EngineGuardProbeSyscalls().fetch_add(1, std::memory_order_relaxed);
    return EngineGuardTick();
}

inline void EngineGuardSyscallEnd(std::uint64_t startTick) noexcept {
    const std::uint64_t endTick = EngineGuardTick();
    if (endTick > startTick) {
        EngineGuardProbeSyscallTicks().fetch_add(endTick - startTick, std::memory_order_relaxed);
    }
}
#else
inline std::uint64_t EngineGuardSyscallBegin() noexcept { return 0; }
inline void EngineGuardSyscallEnd(std::uint64_t) noexcept {}
#endif

// 空地址、长度 0、`address + length` 回绕一律不可用。四个 API 族的 `IsEngineMemoryReadable`
// 都先过这一层，宿主上这就是全部的判定（宿主没有 `svcQueryMemory`，测试里的"引擎内存"是
// 本进程 malloc 出来的伪造块，本来就一定可读）。
inline bool EngineGuardRangeUsable(std::uintptr_t address, std::size_t length) noexcept {
    return address != 0 && length != 0 && address <= UINTPTR_MAX - length;
}

inline bool EngineGuardLookupCached(std::uintptr_t address, std::size_t length) noexcept {
#if defined(EXL_PROBE_BREAK)
    EngineGuardProbeAttempts().fetch_add(1, std::memory_order_relaxed);
#endif
    if (EngineGuardDispatchActive().load(std::memory_order_acquire) == 0 || !EngineGuardCacheEnabled() ||
        !EngineGuardRegionCache().Covers(address, length)) {
        return false;
    }
#if defined(EXL_PROBE_BREAK)
    EngineGuardProbeCacheHits().fetch_add(1, std::memory_order_relaxed);
#endif
    return true;
}

inline void EngineGuardRememberReadable(std::uintptr_t begin, std::uintptr_t end) noexcept {
    if (EngineGuardDispatchActive().load(std::memory_order_acquire) != 0 && EngineGuardCacheEnabled()) {
        EngineGuardRegionCache().Insert(begin, end);
    }
}

} // namespace isaac::runtime
