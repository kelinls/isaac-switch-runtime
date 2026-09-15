#pragma once

#include <cstddef>
#include <cstdint>

// ============================================================================
// 入口中继（entry relay）
// ============================================================================
//
// 目的：让 Runtime 能在**引擎函数入口**上挂钩子（例如 `Room::Init` → `MC_POST_NEW_ROOM`），
// 而**不占用游戏映像里的任何字节**。
//
// 为什么必须换路线（2026-09-12 复核，见 `docs/问题与解决记录.md`）：
//   `Repentance.nro` 的可执行段只有 `[0, 0x68D000)`；段内 ≥0x10 字节的连续零区只剩
//   `0x68CBE0..0x68D000`，20 个既有中继（IPS 静态补丁，`tools/build_patches.py`）已经
//   登记到 `0x68CFD0`，真正空闲只有 0x34 字节。所以「游戏映像代码洞 + IPS 静态补丁」
//   这条路已经没有空间；本机制改为**把中继体放进我们自己模块的 `.text`**，运行时只改写
//   目标函数的入口 16 字节。
//
// 组成（三块，互相独立）：
//
// 1. 竞技场：`JIT_CREATE(EntryRelayArena, kArenaBytes)`（`lib/util/sys/jit.hpp`）在模块
//    `.text` 里放一块页对齐的静态零区，`exl::util::Jit` 用 `exl::util::RwPages`
//    （`lib/util/sys/rw_pages.*`）映射出可写别名，`Flush()` 就是
//    `armDCacheFlush` + `armICacheInvalidate`（真实的 `ic ivau` 序列，
//    `lib/nx/arm/cache.s`）。写入一律走 RW 别名，写完必须 `Flush()`。
//
// 2. 槽（每槽 0x40 字节，槽地址 = 竞技场 + i * kSlotStride）：
//
//        +0x00  adr  x17, #0x18       ; 0x100000D1 → x17 = 槽 + 0x18
//        +0x04  ldar x16, [x17]       ; 0xC8DFFE30 → x16 = 槽里登记的回调地址
//        +0x08  cbz  x16, #0x18       ; 0xB40000D0（槽为 0 时直接走 +0x20 的回退入口）
//        +0x0C  br   x16              ; 0xD61F0200 → 尾调用回调，x0..x7 原样传入
//        +0x10  8 字节保留（全 0，不放指令，正常路径永远不会执行到这里）
//        +0x18  u64 槽：回调地址（安装时写入）
//        +0x20  回退入口：原函数入口的 16 字节（把 expectedEntry16 原样拷贝过来）
//        +0x30  ldr  x16, #8          ; 0x58000050（literal 就在紧随其后的 +0x38）
//        +0x34  br   x16              ; 0xD61F0200
//        +0x38  .quad target + 16     ; 回放原序言后跳回原函数第 5 条指令
//
//    `+0x08` 的偏移必须是 **0x18**（跳到 +0x20），不是 0x8：cbz 的落点按
//    “cbz 自身地址 + 偏移”算，`cbz x16, #8` 会落到 +0x10 的保留区，那里是 8 个零字节
//    （`0x00000000` 是未定义指令），槽为 0 时必然崩溃。这里按“槽为 0 就走回退入口”的语义
//    取 0x18；`runtime/tests/test_entry_relay_encoding.py` 用解码回跳转落点的方式把这个
//    不变量钉死，而不是只比对魔数。
//
//    `+0x0C` 是 `br`（0xD61F0200），**不是** `blr`（0xD63F0200）：入口处的 x30 仍然是
//    游戏调用方的返回地址，回调 `ret` 直接回到游戏（与既有 20 个中继的派发桩同一形态，
//    见 `runtime_constants.hpp` 的 `kManagerRenderRelayExpectedBytes` 前 16 字节）。
//    若写成 `blr`，返回地址会落在 `+0x10` 的保留区——那里没有指令——必然崩溃。
//    因此回调自己决定是否执行原函数：调用 `*outOriginalEntry` 才是跑原函数，不调就是
//    “吞掉”这次调用。回退入口是回调 `blr` 进去的，所以 x30 保存的是回调里的返回地址；
//    回放原序言后 `br` 到 `target + 16`，原函数最后的 `ret` 会正常返回到回调。
//
// 3. 入口改写（**只改写这 16 字节**，一个字节都不多写）：
//
//        target+0x00  ldr x16, #8     ; 0x58000050（literal 就在紧随其后的 target+0x08）
//        target+0x04  br  x16         ; 0xD61F0200
//        target+0x08  .quad 槽地址
//
//    `ldr` 的 literal 紧随其后，所以 16 字节刚好容纳一次绝对跳转（不受 ±128 MiB 的
//    `b` 范围限制）；`x16`/`x17` 是 IP0/IP1，函数入口处按 AAPCS64 可以自由使用，因此
//    挂钩不需要保存/恢复任何寄存器。
//
// 安全门禁（都在安装前完成，全部失败都**不写任何字节**）：
//   * 目标入口现场 16 字节必须与调用方给的期望 16 字节 `memcmp` 完全相同（版本/布局不符
//     直接拒绝，绝不半改）；
//   * 这 16 字节里不得含 PC 相对 / 相对定址指令：`adr`/`adrp`/`ldr(literal)`/`b`/`bl`/
//     `b.cond`/`cbz`/`cbnz`/`tbz`/`tbnz`。它们会被原样拷进槽里执行，PC 变了地址就算错。
//
// 本头文件刻意只依赖 `<cstddef>`/`<cstdint>`（不碰 libnx/exlaunch），因此判定表、编码器
// 与镜像生成都是**纯函数**，可以在宿主机上直接编译执行（`runtime/tests/test_entry_relay_*`）。

namespace isaac::runtime::relay {

// ---------------------------------------------------------------------------
// 竞技场与槽布局
// ---------------------------------------------------------------------------

// 竞技场字节数（一页，`JIT_CREATE` 的 size 参数）。
constexpr std::size_t kArenaBytes = 0x1000;
// 槽步长：槽地址 = 竞技场 + index * kSlotStride。
constexpr std::size_t kSlotStride = 0x40;
// 最多允许的中继数：竞技场一页 0x1000 ÷ 槽步长 0x40 = 64 槽，这里正好取满，
// 不再人为留半页 —— 兼容层的挂点数会超过 16，而竞技场是我们自己的空间（可增长）。
constexpr std::size_t kMaxRelays = 64;

// 槽内偏移。
constexpr std::size_t kSlotStubOffset = 0x00;
constexpr std::size_t kSlotReservedOffset = 0x10;
constexpr std::size_t kSlotCallbackOffset = 0x18;
constexpr std::size_t kSlotFallbackOffset = 0x20;
constexpr std::size_t kSlotFallbackJumpOffset = 0x30;
constexpr std::size_t kSlotFallbackTargetOffset = 0x38;
constexpr std::size_t kSlotBytes = 0x40;

// 被改写/被回放的入口字节数。
constexpr std::size_t kEntryPatchBytes = 0x10;
constexpr std::size_t kOriginalEntryBytes = 0x10;

// 目标入口 16 字节必须落在同一个 4 KiB 页内：`RwPages` 对跨页 claim 的映射大小按
// “对齐后的大小”计算，跨页会算错第二段。真实的函数入口不会落在页尾 16 字节内，
// 这里只是把它挡在门外而不是留下隐患。
constexpr std::size_t kPageBytes = 0x1000;

static_assert(kMaxRelays * kSlotStride <= kArenaBytes, "竞技场必须容纳 kMaxRelays 个槽");
static_assert(kSlotFallbackTargetOffset + 8 == kSlotBytes, "回退入口的 target 字必须正好填满槽尾");
static_assert(kEntryPatchBytes == 4 * sizeof(std::uint32_t), "入口改写必须是 4 条指令");
// 桩里的 `cbz` 落点按 “cbz 自身地址（槽 +0x08）+ 偏移” 算，必须正好是回退入口（槽 +0x20）。
// 写成 0x8 会落到 +0x10 的保留区（8 个零字节 = 未定义指令），槽为 0 时必崩。
static_assert(kSlotStubOffset + 0x8 + 0x18 == kSlotFallbackOffset, "cbz 的落点必须正好是回退入口");

// ---------------------------------------------------------------------------
// 机器码常量（槽与入口真正要执行的字节）
// ---------------------------------------------------------------------------

constexpr std::uint32_t kStubAdrX17Word = 0x100000D1u;       // adr  x17, #0x18
constexpr std::uint32_t kStubLdarX16Word = 0xC8DFFE30u;      // ldar x16, [x17]
constexpr std::uint32_t kStubCbzX16Word = 0xB40000D0u;       // cbz  x16, #0x18（跳到 +0x20 回退入口）
constexpr std::uint32_t kStubBrX16Word = 0xD61F0200u;        // br   x16（尾调用回调）
constexpr std::uint32_t kFallbackLdrX16Word = 0x58000050u;   // ldr  x16, #8
constexpr std::uint32_t kFallbackBrX16Word = 0xD61F0200u;    // br   x16
constexpr std::uint32_t kEntryLdrX16Word = 0x58000050u;      // ldr  x16, #8
constexpr std::uint32_t kEntryBrX16Word = 0xD61F0200u;       // br   x16

// ---------------------------------------------------------------------------
// 失败码：`LastFailure()` 的取值
// ---------------------------------------------------------------------------

enum class EntryRelayFailure : std::uint32_t {
    None = 0,                    // 安装成功
    InvalidAddress = 1,          // 目标/回调地址非法（空、未 4 字节对齐、入口跨页）
    EntryMismatch = 2,           // 目标入口 16 字节与期望不符
    ArenaExhausted = 3,          // 竞技场耗尽（已用满 kMaxRelays 个槽）
    WriteVerifyFailed = 4,       // 写入后用只读地址读回校验失败
    PcRelativeInstruction = 5,   // 入口 16 字节含 PC 相对 / 相对定址指令
};

constexpr std::uint32_t ToFailureCode(EntryRelayFailure failure) {
    return static_cast<std::uint32_t>(failure);
}

// ---------------------------------------------------------------------------
// 纯编码器（宿主机可直接编译执行；`static_assert` 把常量与编码器绑在一起）
// ---------------------------------------------------------------------------

// `adr Rd, #delta`：op=0(31)、immlo(30:29)、10000(28:24)、immhi(23:5)、Rd(4:0)。
constexpr std::uint32_t EncodeAdr(std::uint32_t rd, std::int32_t delta) {
    const auto imm = static_cast<std::uint32_t>(delta);
    return 0x10000000u | ((imm & 0x3u) << 29) | (((imm >> 2) & 0x7FFFFu) << 5) | (rd & 0x1Fu);
}

// `ldar Xt, [Xn]`：size=11(31:30)、001000(29:24)、1(23)、1(22)、111111(21:16)、Rn(9:5)、Rt(4:0)。
constexpr std::uint32_t EncodeLdar(std::uint32_t rt, std::uint32_t rn) {
    return 0xC8DFFC00u | ((rn & 0x1Fu) << 5) | (rt & 0x1Fu);
}

// `cbz Xt, #delta`：sf=1(31)、011010(30:25)、0(24)、imm19(23:5)、Rt(4:0)。
constexpr std::uint32_t EncodeCbz(std::uint32_t rt, std::int32_t delta) {
    const auto imm = static_cast<std::uint32_t>(delta);
    return 0xB4000000u | (((imm >> 2) & 0x7FFFFu) << 5) | (rt & 0x1Fu);
}

// `br Xn`。
constexpr std::uint32_t EncodeBr(std::uint32_t rn) {
    return 0xD61F0000u | ((rn & 0x1Fu) << 5);
}

// `ldr Xt, #delta`（literal，imm19 是 4 字节为单位的字面量偏移）。
constexpr std::uint32_t EncodeLdrLiteral(std::uint32_t rt, std::int32_t delta) {
    const auto imm = static_cast<std::uint32_t>(delta);
    return 0x58000000u | (((imm >> 2) & 0x7FFFFu) << 5) | (rt & 0x1Fu);
}

static_assert(EncodeAdr(17, 0x18) == kStubAdrX17Word, "adr x17, #0x18 编码不符");
static_assert(EncodeLdar(16, 17) == kStubLdarX16Word, "ldar x16, [x17] 编码不符");
static_assert(EncodeCbz(16, 0x18) == kStubCbzX16Word, "cbz x16, #0x18 编码不符");
static_assert(EncodeBr(16) == kStubBrX16Word, "br x16 编码不符");
static_assert(EncodeLdrLiteral(16, 8) == kFallbackLdrX16Word, "ldr x16, #8 编码不符");
static_assert(kEntryLdrX16Word == EncodeLdrLiteral(16, 8), "入口 ldr x16, #8 编码不符");
static_assert(kEntryBrX16Word == EncodeBr(16), "入口 br x16 编码不符");

// ---------------------------------------------------------------------------
// PC 相对 / 相对定址指令判定
// ---------------------------------------------------------------------------

// 一条判定规则：`(word & mask) == value` 即命中。
struct PcRelativeRule {
    std::uint32_t mask;
    std::uint32_t value;
};

// 命中的指令（按 AAPCS64/IP0-IP1 无关，全部是“地址由 PC 算出”的指令）：
//   b / bl（ADDR_PCREL26）、b.cond（ADDR_PCREL19）、cbz / cbnz（ADDR_PCREL19）、
//   tbz / tbnz（ADDR_PCREL14）、adr / adrp（ADDR_PCREL21 / ADDR_ADRP）、
//   ldr（literal，含 SIMD 变体与 prfm(literal)）、ldrsw（literal）。
// 这些指令被原样拷到槽里执行时 PC 已经变了，算出来的地址是错的，所以一律拒绝安装。
inline constexpr PcRelativeRule kPcRelativeRules[] = {
    {0xFC000000u, 0x14000000u},  // b
    {0xFC000000u, 0x94000000u},  // bl
    {0x9F000000u, 0x10000000u},  // adr
    {0x9F000000u, 0x90000000u},  // adrp
    {0x3B000000u, 0x18000000u},  // ldr (literal) Wt/Xt/St/Dt/Qt，以及 prfm (literal)
    {0xFF000000u, 0x98000000u},  // ldrsw (literal)
    {0xFF000010u, 0x54000000u},  // b.cond
    {0x7F000000u, 0x34000000u},  // cbz
    {0x7F000000u, 0x35000000u},  // cbnz
    {0x7F000000u, 0x36000000u},  // tbz
    {0x7F000000u, 0x37000000u},  // tbnz
};

inline constexpr std::size_t kPcRelativeRuleCount = sizeof(kPcRelativeRules) / sizeof(kPcRelativeRules[0]);

constexpr bool IsPcRelativeWord(std::uint32_t word) {
    for (std::size_t index = 0; index < kPcRelativeRuleCount; ++index) {
        if ((word & kPcRelativeRules[index].mask) == kPcRelativeRules[index].value) {
            return true;
        }
    }
    return false;
}

// 小端读出一个指令字（避免依赖 memcpy / 未对齐访问，保持可 constexpr）。
constexpr std::uint32_t LoadLittleEndianWord(const std::uint8_t* bytes, std::size_t wordIndex) {
    const std::size_t base = wordIndex * sizeof(std::uint32_t);
    return static_cast<std::uint32_t>(bytes[base]) |
           (static_cast<std::uint32_t>(bytes[base + 1]) << 8) |
           (static_cast<std::uint32_t>(bytes[base + 2]) << 16) |
           (static_cast<std::uint32_t>(bytes[base + 3]) << 24);
}

// 16 字节（4 条指令）里是否含上面那类指令。
constexpr bool HasPcRelativeInstruction(const std::uint8_t* bytes, std::size_t size) {
    for (std::size_t word = 0; word * sizeof(std::uint32_t) + sizeof(std::uint32_t) <= size; ++word) {
        if (IsPcRelativeWord(LoadLittleEndianWord(bytes, word))) {
            return true;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// 镜像生成（纯函数：只往调用方给的内存里写字节，不碰任何系统调用）
// ---------------------------------------------------------------------------

constexpr void StoreLittleEndianWord(std::uint8_t* out, std::uint32_t word) {
    out[0] = static_cast<std::uint8_t>(word & 0xFFu);
    out[1] = static_cast<std::uint8_t>((word >> 8) & 0xFFu);
    out[2] = static_cast<std::uint8_t>((word >> 16) & 0xFFu);
    out[3] = static_cast<std::uint8_t>((word >> 24) & 0xFFu);
}

constexpr void StoreLittleEndianDoubleWord(std::uint8_t* out, std::uint64_t value) {
    for (std::size_t index = 0; index < 8; ++index) {
        out[index] = static_cast<std::uint8_t>((value >> (index * 8)) & 0xFFu);
    }
}

// 生成一个槽的完整 0x40 字节：
//   * 回调地址写进 +0x18（桩里的 ldar 读的就是它）；
//   * 原入口 16 字节原样拷进 +0x20 作为回退入口；
//   * 槽尾写 `target + 16`，回退入口回放完原序言后跳回原函数第 5 条指令。
constexpr void BuildSlotImage(std::uint8_t* slot, std::uintptr_t target,
                              const std::uint8_t* originalEntry16, std::uintptr_t callback) {
    StoreLittleEndianWord(slot + kSlotStubOffset, kStubAdrX17Word);
    StoreLittleEndianWord(slot + kSlotStubOffset + 0x4, kStubLdarX16Word);
    StoreLittleEndianWord(slot + kSlotStubOffset + 0x8, kStubCbzX16Word);
    StoreLittleEndianWord(slot + kSlotStubOffset + 0xC, kStubBrX16Word);
    StoreLittleEndianDoubleWord(slot + kSlotReservedOffset, 0);
    StoreLittleEndianDoubleWord(slot + kSlotCallbackOffset, callback);
    for (std::size_t index = 0; index < kOriginalEntryBytes; ++index) {
        slot[kSlotFallbackOffset + index] = originalEntry16[index];
    }
    StoreLittleEndianWord(slot + kSlotFallbackJumpOffset, kFallbackLdrX16Word);
    StoreLittleEndianWord(slot + kSlotFallbackJumpOffset + 0x4, kFallbackBrX16Word);
    StoreLittleEndianDoubleWord(slot + kSlotFallbackTargetOffset,
                               static_cast<std::uint64_t>(target) + kOriginalEntryBytes);
}

// 生成目标入口的 16 字节改写：`ldr x16, #8`（literal 在 +8）；`br x16`；槽地址。
constexpr void BuildEntryPatch(std::uint8_t* entry, std::uintptr_t slotAddress) {
    StoreLittleEndianWord(entry, kEntryLdrX16Word);
    StoreLittleEndianWord(entry + 0x4, kEntryBrX16Word);
    StoreLittleEndianDoubleWord(entry + 0x8, slotAddress);
}

// 槽 index 在竞技场里的字节偏移。
constexpr std::size_t SlotOffset(std::size_t index) {
    return index * kSlotStride;
}

// ---------------------------------------------------------------------------
// 安装前置判定（纯函数：宿主机可直接测；真机路径同样只认这一份判定表）
// ---------------------------------------------------------------------------

// 逐字节比较，避免 `memcmp`（本判定要在宿主机与编译期都能跑）。
constexpr bool BytesEqual(const std::uint8_t* left, const std::uint8_t* right, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        if (left[index] != right[index]) {
            return false;
        }
    }
    return true;
}

// 判定顺序（也是失败码的优先级）：
//   1. 地址非法（空指针 / 未 4 字节对齐 / 入口 16 字节跨 4 KiB 页）→ InvalidAddress
//   2. 现场 16 字节与期望不符 → EntryMismatch（此时**一个字节都不许写**）
//   3. 入口含 PC 相对指令 → PcRelativeInstruction
//   4. 槽位已用满 → ArenaExhausted
//   5. 通过 → None
constexpr EntryRelayFailure EvaluateEntryRelayPreconditions(std::uintptr_t target,
                                                            std::uintptr_t callback,
                                                            const void* expectedEntry16,
                                                            const std::uint8_t* currentEntry16,
                                                            std::size_t nextSlotIndex) {
    if (target == 0 || callback == 0 || expectedEntry16 == nullptr || currentEntry16 == nullptr ||
        (target & 0x3u) != 0 || (callback & 0x3u) != 0 ||
        (target % kPageBytes) + kEntryPatchBytes > kPageBytes) {
        return EntryRelayFailure::InvalidAddress;
    }
    if (!BytesEqual(currentEntry16, static_cast<const std::uint8_t*>(expectedEntry16),
                    kOriginalEntryBytes)) {
        return EntryRelayFailure::EntryMismatch;
    }
    if (HasPcRelativeInstruction(currentEntry16, kOriginalEntryBytes)) {
        return EntryRelayFailure::PcRelativeInstruction;
    }
    if (nextSlotIndex >= kMaxRelays) {
        return EntryRelayFailure::ArenaExhausted;
    }
    return EntryRelayFailure::None;
}

// ---------------------------------------------------------------------------
// 公共接口
// ---------------------------------------------------------------------------

// 安装一个入口中继。
//   target            目标函数入口地址（4 字节对齐，且 16 字节不跨页）
//   expectedEntry16   目标入口**当前**的 16 字节；必须与现场逐字节相同才允许安装
//   callback          我们模块里的 C++ 回调地址（4 字节对齐）
//   outOriginalEntry  返回回退入口地址（= 槽 + kSlotFallbackOffset），回调要靠它跑原函数
// 成功返回 true 并把 LastFailure 清 0，失败返回 false 并记下失败码。失败码 1/2/3/5 都在任何写入
// 之前返回，**目标函数的字节保持原样**；只有码 4 表示已经写过但读回不一致（入口可能已被改写，
// 调用方应把它当不可恢复的异常处理，不要再重试）。
bool TryInstallEntryRelay(std::uintptr_t target, const void* expectedEntry16,
                          std::uintptr_t callback, std::uintptr_t* outOriginalEntry);

// 已成功安装的中继数。
std::size_t InstalledCount();

// 最近一次 `TryInstallEntryRelay` 的结果码（0 = 成功）。
std::uint32_t LastFailure();

}  // namespace isaac::runtime::relay
