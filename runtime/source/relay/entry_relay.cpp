#include "relay/entry_relay.hpp"

#include <atomic>
#include <cstring>
#include <span>

#include "lib/util/sys/jit.hpp"

// 入口中继的真机侧实现。
//
// 设计、槽布局与安全门禁全部写在 `entry_relay.hpp` 的注释里；本文件只负责“碰系统”的那部分：
//   * 竞技场（`JIT_CREATE`，落在本模块 `.text`）的一次性初始化；
//   * 槽位分配的原子自增；
//   * 经 `exl::util::RwPages` 写入槽与目标入口，写完 `Flush()`（DCache + ICache）；
//   * 用只读地址读回校验；
//   * 失败码记账（`LastFailure()`）。
//
// 这里刻意**不引用** `hook_manager.cpp` / `probe/**` 的任何东西：本机制自包含，谁接线谁调用。

namespace isaac::runtime::relay {

namespace {

// 中继竞技场：`JIT_CREATE` 在本模块 `.text` 里放一块 0x1000 字节、页对齐的静态零区
// （section `.text.EntryRelayArena`），`exl::util::Jit` 再用 `RwPages` 映射出可写别名。
// 注意它**从来不是**游戏映像的一部分，也不占任何游戏代码洞。
JIT_CREATE(EntryRelayArena, kArenaBytes);

std::atomic<std::uint32_t> s_LastFailure{ToFailureCode(EntryRelayFailure::None)};
std::atomic<std::size_t> s_NextSlot{0};
std::atomic<std::size_t> s_InstalledCount{0};
// 竞技场只映射一次的“认领 / 就绪”标志。这里刻意用 u32 而不是 bool 原子量：本项目有一条门禁
// （`runtime/tests/test_manager_update_hook_audit.py` 的
// `test_runtime_source_contains_no_bool_atomics`）禁止 runtime 源码里出现 bool 原子量。
constexpr std::uint32_t kFlagClear = 0;
constexpr std::uint32_t kFlagSet = 1;
std::atomic<std::uint32_t> s_ArenaClaimed{kFlagClear};
std::atomic<std::uint32_t> s_ArenaReady{kFlagClear};

std::uint32_t RecordFailure(EntryRelayFailure failure) {
    const std::uint32_t code = ToFailureCode(failure);
    s_LastFailure.store(code, std::memory_order_release);
    return code;
}

// 竞技场只映射一次。安装期假定单线程，但不依赖它：`s_ArenaClaimed` 的 CAS 保证只有一个
// 线程去建映射，其他线程等到 `s_ArenaReady`。`RwPages` 建映射失败会直接 abort 整个进程
// （`R_ABORT_UNLESS`/`EXL_ASSERT`），所以这里的自旋不会永久卡死。
void EnsureArenaReady() {
    if (s_ArenaReady.load(std::memory_order_acquire) != kFlagClear) {
        return;
    }
    std::uint32_t expected = kFlagClear;
    if (s_ArenaClaimed.compare_exchange_strong(expected, kFlagSet, std::memory_order_acq_rel)) {
        EntryRelayArena.Initialize(std::span {impl::EntryRelayArena::s_Area});
        s_ArenaReady.store(kFlagSet, std::memory_order_release);
        return;
    }
    while (s_ArenaReady.load(std::memory_order_acquire) == kFlagClear) {
    }
}

}  // namespace

bool TryInstallEntryRelay(std::uintptr_t target, const void* expectedEntry16,
                          std::uintptr_t callback, std::uintptr_t* outOriginalEntry) {
    if (outOriginalEntry != nullptr) {
        *outOriginalEntry = 0;
    }

    // 先把“能不能读目标地址”挡住，再解引用。
    if (target == 0 || callback == 0 || expectedEntry16 == nullptr ||
        (target & 0x3u) != 0 || (callback & 0x3u) != 0) {
        RecordFailure(EntryRelayFailure::InvalidAddress);
        return false;
    }

    // 现场 16 字节。**任何写入都发生在这之后**，判定不通过就一个字节都不写。
    std::uint8_t current[kOriginalEntryBytes];
    std::memcpy(current, reinterpret_cast<const void*>(target), sizeof(current));

    const EntryRelayFailure precondition = EvaluateEntryRelayPreconditions(
        target, callback, expectedEntry16, current, s_NextSlot.load(std::memory_order_acquire));
    if (precondition != EntryRelayFailure::None) {
        RecordFailure(precondition);
        return false;
    }

    EnsureArenaReady();

    // 槽位分配：原子自增，避免重入；拿到的 index 超过上限即竞技场耗尽（失败不回退计数器，
    // 所以之后再装也仍然报 3）。
    const std::size_t index = s_NextSlot.fetch_add(1, std::memory_order_acq_rel);
    if (index >= kMaxRelays) {
        RecordFailure(EntryRelayFailure::ArenaExhausted);
        return false;
    }

    const std::uintptr_t roSlot = EntryRelayArena.GetRo() + SlotOffset(index);
    auto* rwSlot = reinterpret_cast<std::uint8_t*>(EntryRelayArena.GetRw() + SlotOffset(index));

    std::uint8_t expectedSlot[kSlotBytes];
    BuildSlotImage(expectedSlot, target, current, callback);
    std::memcpy(rwSlot, expectedSlot, sizeof(expectedSlot));
    // 槽必须在入口改写生效之前对指令侧可见：先刷槽，再改入口。
    EntryRelayArena.Flush();

    std::uint8_t expectedEntry[kEntryPatchBytes];
    BuildEntryPatch(expectedEntry, roSlot);

    // 入口改写走 RW 别名，只改这 16 字节。
    exl::util::RwPages entryPages(target, kEntryPatchBytes);
    auto* rwEntry = reinterpret_cast<std::uint8_t*>(entryPages.GetRw());
    std::memcpy(rwEntry, expectedEntry, sizeof(expectedEntry));
    entryPages.Flush();

    // 读回校验：用**只读**地址读（也就是游戏真正执行的那份映射）。
    if (std::memcmp(reinterpret_cast<const void*>(roSlot), expectedSlot, kSlotBytes) != 0 ||
        std::memcmp(reinterpret_cast<const void*>(target), expectedEntry, kEntryPatchBytes) != 0) {
        RecordFailure(EntryRelayFailure::WriteVerifyFailed);
        return false;
    }

    s_InstalledCount.fetch_add(1, std::memory_order_acq_rel);
    s_LastFailure.store(ToFailureCode(EntryRelayFailure::None), std::memory_order_release);
    if (outOriginalEntry != nullptr) {
        *outOriginalEntry = roSlot + kSlotFallbackOffset;
    }
    return true;
}

std::size_t InstalledCount() {
    return s_InstalledCount.load(std::memory_order_acquire);
}

std::uint32_t LastFailure() {
    return s_LastFailure.load(std::memory_order_acquire);
}

}  // namespace isaac::runtime::relay
