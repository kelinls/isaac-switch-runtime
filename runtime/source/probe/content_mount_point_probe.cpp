// Stage 147: content mount point probe. See `content_mount_point_probe.hpp` for the
// payload layout and the questions it answers.
//
// Probe builds only: `EXL_PROBE_BREAK` compiles the whole translation unit away.
// 另外受 `PROBE_BREAK_CONTENT_MOUNT` 单独控制：本 TU 里还定义着 `ProbeCallbackError` /
// `ProbeEnginePlayers`，而更新钩子每帧都调它们（不受"600 帧绘制"约束），所以它会在**加载阶段**
// 执行；二分定位探针包启动故障时需要能单独关掉（2026-09-13）。

#include "content_mount_point_probe.hpp"

#if defined(EXL_PROBE_BREAK) && EXL_PROBE_BREAK_CONTENT_MOUNT

#include "lua_runtime.hpp"
// 回调登记普查、回调错误标志与"最近一次 Lua 错误信息"的只读访问器
// （`CallbackErrorPending` / `LastLuaErrorHead` / `ManagedCallbackRegistry`）都声明在这里。
// `lua_runtime.cpp` 一行都不用改，本探针只读它已有的状态。
#include "lua_runtime_state.hpp"
#include "module_finder.hpp"
#include "runtime_constants.hpp"
// `LuaRuntime::ManagedCallbackRegistry()` 返回的类型的完整定义（为了 `.Count()`）。
#include "application/callback/callback_registry.hpp"
// 诊断字 [11] 的打包格式与"是否失败"判定（`IsModLoadFailureWord`）。探针必须只按低 8 位判，
// 否则 bits 8..15 的原因与 bits 16..47 的文件长度会被当成步骤。
#include "application/mod/mod_load_step.hpp"
// `Font:Load` 的探针读数（本轮"EID 为什么提前 return"的唯一判据，见探针负载 `[12]`）。
#include "interfaces/lua/api_sequence_probe.hpp"
#include "interfaces/lua/font_api.hpp"
// `Isaac.GetPlayer` 解析链的阶段码与原始读数（本轮唯一挡住 EID 的环节）。
#include "interfaces/lua/isaac_api.hpp"
// 动作一（`Sprite:GetTexel`）与动作三（`Level:GetCurses`）的探针读数口。
#include "interfaces/lua/remaining_api.hpp"
#include "interfaces/lua/sprite_api.hpp"
// `Game:IsPaused()` 的最后一次返回值（EID 用它决定是否隐藏描述）。
#include "interfaces/lua/game_api.hpp"

#include <array>
#include <atomic>
#include <cstdint>
#include <cstddef>
#include <cstdlib>
#include <cstring>

extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_RebuildContentMountPointsRelay();
extern "C" __attribute__((visibility("hidden"))) std::uint32_t
IsaacModRuntime_RebuildContentMountPointsRelayFiredCount();
// `MC_POST_RENDER` 的新派发点（`Present` 之前）：word0 = 安装结果，word1 = 进入次数。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_RenderPresentRelayDiagnostics(std::uint32_t* output);
// 钩子诊断 5 字契约：word3 = update 回调进入次数、word4 = 渲染回调进入次数。
extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetHookDiagnostics(std::uint32_t* output, std::size_t wordCount);

namespace {

// `Sprite` 实测需要的两个小工具：判断一段地址是否可读（避免对"候选指针"盲解引用），
// 以及按 16 字节对齐分配一块对象存储。
bool IsReadableRange(std::uintptr_t address, std::size_t length) {
    if (address < 0x1000 || length == 0 || address > UINTPTR_MAX - length) {
        return false;
    }
    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0) {
        return false;
    }
    const std::uintptr_t end = address + length;
    return info.addr <= address && end >= address && end <= info.addr + info.size &&
           (info.perm & Perm_R) != 0;
}

void* AllocateAlignedObject(std::size_t size, std::size_t alignment) {
    // 多要**一个指针宽**：对齐后回退 `sizeof(void*)` 存原始指针的位置必定落在我们自己的
    // malloc 块内部。2026-09-13 的教训：早先只多要 `alignment`，当 malloc 本来就返回对齐地址时
    // `aligned == raw`，那一写就落到了块头**前面**，直接把 malloc 的元数据写坏，随后 free 崩溃
    // （报告 `01789146756`：`PC = runtime + 0x3ca80` 落在 `_free_r`，调用者是本探针函数）。
    void* raw = std::malloc(size + alignment + sizeof(void*));
    if (raw == nullptr) {
        return nullptr;
    }
    const std::uintptr_t aligned =
        (reinterpret_cast<std::uintptr_t>(raw) + sizeof(void*) + alignment - 1) & ~(alignment - 1);
    *reinterpret_cast<void**>(aligned - sizeof(void*)) = raw;
    return reinterpret_cast<void*>(aligned);
}

void ReleaseAlignedObject(void* object) {
    if (object == nullptr) {
        return;
    }
    std::free(*reinterpret_cast<void**>(reinterpret_cast<std::uintptr_t>(object) - sizeof(void*)));
}


// Offsets and guards come from `analysis/stage146-content-mount-point/…json`, which
// decodes them from this exact build (`91C73FDD…`). A guard mismatch means the
// deployment pair does not match the audited build, so the probe reports the bitmask
// instead of calling anything.
constexpr std::uintptr_t kContentManagerSlot = 0xAAC748;
constexpr std::uintptr_t kPathCtorStringOffset = 0x4C0D84;
constexpr std::uintptr_t kPathDtorOffset = 0x4C0F60;
constexpr std::uintptr_t kAddMountPointOffset = 0x4C1700;
constexpr std::uintptr_t kDoesMountPointExistOffset = 0x4C1E14;
constexpr std::uintptr_t kGetFileMountPointOffset = 0x4C2200;
constexpr std::uintptr_t kGetMountPointsOffset = 0x4C2084;
// The relay and Font ABI constants live in `runtime_constants.hpp`: production and probe
// must not carry two copies of the same offset (the names collide and the compiler is right
// to refuse the ambiguity).

constexpr std::array<std::uint8_t, 16> kPathCtorStringBytes = {
    0xfd, 0x7b, 0xbe, 0xa9, 0xf4, 0x4f, 0x01, 0xa9,
    0xfd, 0x03, 0x00, 0x91, 0x08, 0x00, 0x80, 0x12};
constexpr std::array<std::uint8_t, 16> kPathDtorBytes = {
    0xfd, 0x7b, 0xbe, 0xa9, 0xf3, 0x0b, 0x00, 0xf9,
    0xfd, 0x03, 0x00, 0x91, 0xf3, 0x03, 0x00, 0xaa};
constexpr std::array<std::uint8_t, 16> kAddMountPointBytes = {
    0xfd, 0x7b, 0xbb, 0xa9, 0xfa, 0x67, 0x01, 0xa9,
    0xfd, 0x03, 0x00, 0x91, 0xf8, 0x5f, 0x02, 0xa9};
constexpr std::array<std::uint8_t, 16> kDoesMountPointExistBytes = {
    0xfd, 0x7b, 0xbd, 0xa9, 0xf5, 0x0b, 0x00, 0xf9,
    0xfd, 0x03, 0x00, 0x91, 0xf4, 0x4f, 0x02, 0xa9};
constexpr std::array<std::uint8_t, 16> kGetFileMountPointBytes = {
    0xff, 0x83, 0x00, 0xd1, 0xfd, 0x7b, 0x01, 0xa9,
    0xfd, 0x43, 0x00, 0x91, 0xa2, 0x13, 0x00, 0xd1};
constexpr std::array<std::uint8_t, 16> kGetMountPointsBytes = {
    0xfd, 0x7b, 0xba, 0xa9, 0xfc, 0x6f, 0x01, 0xa9,
    0xfd, 0x03, 0x00, 0x91, 0xfa, 0x67, 0x02, 0xa9};

// The test mod ships in the deployment tree; its resources directory is what the
// probe mounts, and `gfx/probe147.anm2` is the new file name it must resolve.
constexpr char kTestMountRelative[] = "isaac_mods/mods/Stage147Probe/resources";
constexpr char kTestFileRelative[] = "gfx/probe147.anm2";
// A file that exists in the base game content: the baseline for "lookups work at all".
constexpr char kBaselineFileRelative[] = "gfx/005.100_collectible.anm2";
// The same name is also shipped by the test Mod, which is what makes it a priority
// question: after registration the engine has two candidates for one name.
constexpr char kSameNamedFileRelative[] = "gfx/005.100_collectible.anm2";
constexpr char kTestModDirectoryMarker[] = "Stage147Probe";

// `ContentMountPointPath`'s constructor writes { char const* path; int32 aocIndex }
// at offsets 0x00 and 0x08; the audited call sites reserve 0x18 bytes of stack for it,
// so the probe reserves the same and asserts only the two fields it touches.
struct ContentMountPointPathLike {
    const char* path;
    std::int32_t aocIndex;
    std::int32_t reserved;
    std::uint64_t tail;
};
static_assert(offsetof(ContentMountPointPathLike, path) == 0x00,
              "ContentMountPointPath::path offset");
static_assert(offsetof(ContentMountPointPathLike, aocIndex) == 0x08,
              "ContentMountPointPath::aocIndex offset");
static_assert(sizeof(ContentMountPointPathLike) == 0x18,
              "ContentMountPointPath stack footprint");

using PathCtorFn = void (*)(ContentMountPointPathLike*, const char*);
using PathDtorFn = void (*)(ContentMountPointPathLike*);
using AddMountPointFn = void (*)(void*, const ContentMountPointPathLike*);
using DoesMountPointExistFn = bool (*)(void*, const ContentMountPointPathLike*);
using GetFileMountPointFn = const char* (*)(void*, const char*);
using GetMountedFilePathFn = const char* (*)(void*, const char*);
// `GetMountPoints()` returns a `std::vector<ContentMountPoint*>` *by value*. On
// AAPCS64 such a return travels through the indirect-return register `x8`, so calling
// it through a `void(*)(void*, void*)` pointer would hand the vector pointer to `x1`
// and let the callee write to whatever `x8` happens to hold. Declaring the result as a
// type with a user-provided destructor keeps it non-trivial, which is what makes the
// compiler emit the indirect-return call sequence. The destructor deliberately does
// nothing: the storage belongs to the engine, and the probe ends the session right
// after this.
struct MountPointVector {
    void* begin;
    void* end;
    void* capacity;
    ~MountPointVector() {}
};
using GetMountPointsFn = MountPointVector (*)(void*);
using RebuildContentMountPointsFn = void (*)();
using FontCtorFn = void (*)(void*);
using FontLoadFn = bool (*)(void*, const char*, const char*);
using FontUnloadFn = void (*)(void*);
using FontIsLoadedFn = bool (*)(const void*);
using FontGetStringWidthFn = int (*)(const void*, const char*);
using FontGetLineHeightFn = std::uint16_t (*)(const void*);

// The probe's Font lives in .bss on purpose: `~Font()` never frees `this`, so a static
// buffer removes the allocator from the experiment entirely. 0x20050 is the audited
// object size.
#if defined(EXL_PROBE_MOUNT_SECTIONS)  // Font 实测存储：仅 Font 段使用
alignas(16) unsigned char g_fontStorage[kFontObjectSize];
#endif  // EXL_PROBE_MOUNT_SECTIONS（Font 实测）
constexpr char kTestFileResource[] = "font/eid_default.fnt";
constexpr char kTestString[] = "Stage148";

std::uint32_t g_probeTaken = 0;

[[maybe_unused]] bool GuardMatches(std::uintptr_t offset, const std::array<std::uint8_t, 16>& expected) {
    const auto* at = reinterpret_cast<const std::uint8_t*>(offset);
    return std::memcmp(at, expected.data(), expected.size()) == 0;
}

// How many mount points the manager currently holds. `GetMountPoints()` returns a
// `std::vector` by value through an sret pointer, so the count is the two pointers the
// vector stores; the temporary is deliberately leaked because the probe ends the
// session immediately afterwards.
[[maybe_unused]] std::uint64_t MountPointCount(void* self, GetMountPointsFn getMountPoints) {
    if (getMountPoints == nullptr) {
        return 0;
    }
    MountPointVector points = getMountPoints(self);
    const auto begin = reinterpret_cast<std::uintptr_t>(points.begin);
    const auto end = reinterpret_cast<std::uintptr_t>(points.end);
    if (end < begin) {
        return 0;
    }
    return static_cast<std::uint64_t>((end - begin) / sizeof(void*));
}

bool BytesMatch(std::uintptr_t address, const std::uint8_t* expected, std::size_t length) {
    return std::memcmp(reinterpret_cast<const void*>(address), expected, length) == 0;
}

// The first eight bytes of a resolved path, as ASCII evidence in the crash report.
[[maybe_unused]] std::uint64_t FirstEightBytes(const char* text) {
    if (text == nullptr) {
        return 0;
    }
    std::uint64_t packed = 0;
    std::memcpy(&packed, text, sizeof(packed));
    return packed;
}

}  // namespace

extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeContentMountPoint() {
    // 本轮开关：挂载点/字体/中继这一整批问题早就答完了（`ISAACMP1` 那几轮真机报告，以及纯贴图
    // Mod 真的把图标画出来）。而它的触发口径是"Mod 自己画了 600 帧"，一旦 EID 开始正常绘制就会
    // 抢在 `ISAACERR` 前面结束会话 —— 正是本轮最不想发生的事。关掉它，把会话留给回调错误与普查字。
    if (!isaac::runtime::kContentMountProbeArmed) {
        return;
    }
    if (g_probeTaken != 0) {
        return;
    }
    g_probeTaken = 1;

    [[maybe_unused]] std::uint64_t status = 0;
    [[maybe_unused]] std::uint64_t base = 0;
    [[maybe_unused]] std::uint64_t manager = 0;
    [[maybe_unused]] std::uint64_t testMountPath = 0;
    [[maybe_unused]] std::uint64_t baselineMountPath = 0;
    [[maybe_unused]] std::uint64_t testResolvedPath = 0;
    [[maybe_unused]] std::uint64_t sameNamedMountPath = 0;
    [[maybe_unused]] std::uint64_t mountPointsBefore = 0;
    [[maybe_unused]] std::uint64_t mountPointsAfter = 0;
    [[maybe_unused]] std::uint64_t relayFired = 0;
    [[maybe_unused]] std::uint64_t fontLoaded = 0;
    [[maybe_unused]] std::uint64_t fontWidthValue = 0;
    [[maybe_unused]] std::uint64_t fontLineHeightValue = 0;
    [[maybe_unused]] std::uint64_t modMark = 0;
    [[maybe_unused]] std::uint64_t modFont = 0;
    [[maybe_unused]] std::uint64_t postRenderCallbacks = 0;
    [[maybe_unused]] std::uint64_t postUpdateCallbacks = 0;

    // `g_UpdateCallbackEntries` 在 hook_manager 的匿名命名空间里，探针通过诊断出口读它
    // （分层构建才导出，探针构建就是分层构建）。
    [[maybe_unused]] std::uint64_t updateEntries = 0;
    // 16 字诊断：除了回调进入次数，本轮还要看“Mod 登记了哪些回调种类”“有没有派发点”
    // “清单 Mod 加载有没有失败”“Lua 错误信息是什么”。真机上文件写入不可靠，所以这些
    // 都装进诊断字，再随下面的探针报错一起进崩溃报告（寄存器就是结构化证据）。
    std::uint32_t census[16] = {};
    const bool censusValid =
        IsaacModRuntime_GetHookDiagnostics(census, 16) == 0x3152484341415349ULL;
    if (censusValid) {
        updateEntries = census[3];
    }

    // --- Mod 加载失败：探针口径要求“到点必须直接报错” ---
    // 成功不崩（会话继续跑，画面本身就是证据），失败必崩并把普查字装进寄存器。
    //
    // ★ 判据只看诊断字 [11] 的**低 8 位**：0 = 带脚本加载成功，1..4 = 失败在哪一步，
    // **5 = 纯资源型 Mod 已挂载**（清单没写 `entry`，或 entry 指向的文件包内不存在：
    // 内容挂载点照常注册、Lua 刻意不初始化、`InitializeDefaultManifestMod` 返回成功）。
    // 只判 `!= 0` 会把"资源型 Mod 正常加载"当失败，下一轮探针就会在正常情况上崩掉。
    // 2026-09-12 起 bits 8..15 = 失败原因（`StatusCode`）、bits 16..47 = 观测到的文件长度
    // （见 `mod_load_step.hpp` 的 `PackModLoadWord`），所以这里必须按掩码判，不能整字比较。
    if (censusValid && isaac::runtime::IsModLoadFailureWord(census[11])) {
        // 用 `std::atomic<u32>` 而不是 `atomic<bool>`：仓库有一条测试禁止模块里出现
        // bool 原子（`test_runtime_source_contains_no_bool_atomics`），理由是 AArch64 上
        // bool 的原子访问容易在“值域只有 0/1”的假设上出岔子。
        static std::atomic<std::uint32_t> reported{0};
        std::uint32_t expected = 0;
        if (reported.compare_exchange_strong(expected, 1, std::memory_order_acq_rel)) {
            const std::uint64_t kindsLow =
                (static_cast<std::uint64_t>(census[6]) << 32) | census[5];
            const std::uint64_t kindsHigh =
                (static_cast<std::uint64_t>(census[8]) << 32) | census[7];
            const std::uint64_t errorHead =
                (static_cast<std::uint64_t>(census[15]) << 32) | census[14];
            register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
            register std::uint64_t x1 __asm__("x1") = isaac::runtime::kModLoadProbeMagic;
            register std::uint64_t x2 __asm__("x2") =
                ((static_cast<std::uint64_t>(census[11]) & 0xFFFFULL) << 32) |
                ((static_cast<std::uint64_t>(census[10]) & 0xFFULL) << 8) |
                (census[12] != 0 ? 1ULL : 0ULL);
            register std::uint64_t x3 __asm__("x3") = kindsLow;
            register std::uint64_t x4 __asm__("x4") = kindsHigh;
            register std::uint64_t x5 __asm__("x5") = census[9];
            register std::uint64_t x6 __asm__("x6") = census[10];
            register std::uint64_t x7 __asm__("x7") =
                (static_cast<std::uint64_t>(census[13]) << 32) | (errorHead & 0xFFFFFFFFULL);
            register std::uint64_t x8 __asm__("x8") = errorHead;
            __asm__ volatile("svc 0x7f"
                             :
                             : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5),
                               "r"(x6), "r"(x7), "r"(x8)
                             : "memory");
            __builtin_unreachable();
        }
    }

    const auto module = FindTargetModule();
    if (!module.has_value()) {
        status |= 1ULL << 8;  // module not found: everything else stays zero
    } else {
        base = static_cast<std::uint64_t>(module->base);

        // 挂载点与字体那几组判据在早前轮次已逐项验过（stage147/stage148），本轮把它们编掉以腾出
        // **只读段余量**：当时探针构建曾超出 `link.ld` 的 0x75000 历史边界 0x160 字节。该边界已于
        // 2026-09-12 换成失控增长闸门（代码 512 KiB / 只读段结束 1 MiB，见 `misc/link.ld` 与
        // `tools/runtime_layout_budget.py`），不再需要为它压缩；保留编掉的原因是这几组判据已验证过，
        // 而"Mod 能否解析自己的 resources/"这一轮由测试 Mod 自己加载 .anm2 直接证明（见 v8）。
        // 需要复跑它们时把 `EXL_PROBE_MOUNT_SECTIONS` 打开即可。
#if defined(EXL_PROBE_MOUNT_SECTIONS)
        // 1. Verify every function we are about to call, before calling it.
        const auto check = [&](std::uintptr_t offset,
                               const std::array<std::uint8_t, 16>& expected,
                               int bit) {
            if (module->Contains(module->base + offset, expected.size()) &&
                GuardMatches(module->base + offset, expected)) {
                status |= 1ULL << bit;
            }
        };
        check(kPathCtorStringOffset, kPathCtorStringBytes, 0);
        check(kAddMountPointOffset, kAddMountPointBytes, 1);
        check(kPathDtorOffset, kPathDtorBytes, 2);
        check(kDoesMountPointExistOffset, kDoesMountPointExistBytes, 3);
        check(kGetFileMountPointOffset, kGetFileMountPointBytes, 4);
        check(kContentGetMountedFilePathOffset, kContentGetMountedFilePathExpectedBytes, 5);
        check(kGetMountPointsOffset, kGetMountPointsBytes, 6);
        // The rebuild constants come from `runtime_constants.hpp`: production and probe
        // must not carry two copies of the same offset and guard.
        check(kRebuildContentMountPointsOffset, kRebuildContentMountPointsExpectedBytes, 7);

        const auto* managerSlot =
            reinterpret_cast<void* const*>(module->base + kContentManagerSlot);
        if (managerSlot != nullptr && *managerSlot != nullptr) {
            manager = reinterpret_cast<std::uint64_t>(*managerSlot);
            status |= 1ULL << 9;
        }

        // Only proceed when every guard matched and the manager is live: a partially
        // matching deployment would otherwise call a random address on the game thread.
#endif  // EXL_PROBE_MOUNT_SECTIONS（上面是守卫检查与 manager 取址）


        if ((status & 0xFFULL) == 0xFFULL && (status & (1ULL << 9)) != 0) {
#if defined(EXL_PROBE_MOUNT_SECTIONS)
            auto* const self = reinterpret_cast<void*>(manager);
            const auto pathCtor = reinterpret_cast<PathCtorFn>(
                module->base + kPathCtorStringOffset);
            const auto pathDtor = reinterpret_cast<PathDtorFn>(
                module->base + kPathDtorOffset);
            const auto addMountPoint = reinterpret_cast<AddMountPointFn>(
                module->base + kAddMountPointOffset);
            const auto doesMountPointExist = reinterpret_cast<DoesMountPointExistFn>(
                module->base + kDoesMountPointExistOffset);
            const auto getFileMountPoint = reinterpret_cast<GetFileMountPointFn>(
                module->base + kGetFileMountPointOffset);
            const auto getMountedFilePath = reinterpret_cast<GetMountedFilePathFn>(
                module->base + kContentGetMountedFilePathOffset);
            const auto getMountPoints = reinterpret_cast<GetMountPointsFn>(
                module->base + kGetMountPointsOffset);

            // Baseline: a base-game file must resolve before anything is registered.
            const char* baselineMount = getFileMountPoint(self, kBaselineFileRelative);
            if (baselineMount != nullptr) {
                status |= 1ULL << 10;
                baselineMountPath = FirstEightBytes(baselineMount);
            }

            ContentMountPointPathLike testPath{};
            pathCtor(&testPath, kTestMountRelative);
            // The mount point must not exist yet: if it does, something else registered it
            // and the probe could not tell its own registration apart.
            if (!doesMountPointExist(self, &testPath)) {
                status |= 1ULL << 11;
            }

            addMountPoint(self, &testPath);

            if (doesMountPointExist(self, &testPath)) {
                // 位 12 本轮让给 Sprite 结论（挂载点这些判据在早前轮次已逐项验过）。
            }

            const char* testMount = getFileMountPoint(self, kTestFileRelative);
            if (testMount != nullptr) {
                // 位 13 本轮让给 Sprite 结论（挂载点这些判据在早前轮次已逐项验过）。
                testMountPath = FirstEightBytes(testMount);
                if (std::strstr(testMount, "Stage147Probe") != nullptr) {
                    // 位 14 本轮让给 Sprite 结论（挂载点这些判据在早前轮次已逐项验过）。
                }
            }

            // The final answer: the engine's own resolution of the new file name.
            testResolvedPath = FirstEightBytes(getMountedFilePath(self, kTestFileRelative));

            // Priority: the base game ships a file with this name too, so whichever mount
            // point answers says who wins for a same-named file.
            const char* sameNamed = getFileMountPoint(self, kSameNamedFileRelative);
            if (sameNamed != nullptr && std::strstr(sameNamed, kTestModDirectoryMarker) != nullptr) {
                // 位 15 本轮让给 Sprite 结论（挂载点这些判据在早前轮次已逐项验过）。
                sameNamedMountPath = FirstEightBytes(sameNamed);
            } else if (sameNamed != nullptr) {
                sameNamedMountPath = FirstEightBytes(sameNamed);
            }

            // Timing: run the engine's own rebuild, which starts with ClearMountPoints and
            // then re-adds base/add-on/Rep+ content. This is the event that decides whether a
            // Mod mount point needs restoring.
            const auto rebuild = reinterpret_cast<RebuildContentMountPointsFn>(
                module->base + kRebuildContentMountPointsOffset);
            mountPointsBefore = MountPointCount(self, getMountPoints);
            rebuild();
            mountPointsAfter = MountPointCount(self, getMountPoints);
            if (!doesMountPointExist(self, &testPath)) {
                // 位 16 本轮让给 Sprite 结论（挂载点这些判据在早前轮次已逐项验过）。
            }

            // Restoring strategy the production relay uses: mount again after the rebuild.
            addMountPoint(self, &testPath);
            if (doesMountPointExist(self, &testPath)) {
                // 位 17 本轮让给 Sprite 结论（挂载点这些判据在早前轮次已逐项验过）。
            }
            if (getMountedFilePath(self, kTestFileRelative) != nullptr) {
                // 位 18 本轮让给 Sprite 结论（挂载点这些判据在早前轮次已逐项验过）。
            }

            pathDtor(&testPath);
#endif  // EXL_PROBE_MOUNT_SECTIONS（挂载点实测本体）



            // --- Stage 148 Font: can a Mod load and measure its own .fnt? ---
            // Uses the engine offsets directly so the answer does not depend on the Lua
            // layer: allocate into .bss, construct, Load the Mod-provided font name (which
            // only exists inside the mount point registered above), then measure.
#if defined(EXL_PROBE_MOUNT_SECTIONS)  // Font 实测：早前轮次已验，本轮编掉
            if ((status & (1ULL << 12)) != 0 &&
                BytesMatch(module->base + kFontCtorOffset, kFontCtorExpectedBytes.data(), 16) &&
                BytesMatch(module->base + kFontLoadOffset, kFontLoadExpectedBytes.data(), 16) &&
                BytesMatch(module->base + kFontIsLoadedOffset, kFontIsLoadedExpectedBytes.data(), 16) &&
                BytesMatch(module->base + kFontGetStringWidthOffset,
                           kFontGetStringWidthExpectedBytes.data(), 16) &&
                BytesMatch(module->base + kFontGetLineHeightOffset,
                           kFontGetLineHeightExpectedBytes.data(), 16)) {
                const auto fontCtor = reinterpret_cast<FontCtorFn>(module->base + kFontCtorOffset);
                const auto fontLoad = reinterpret_cast<FontLoadFn>(module->base + kFontLoadOffset);
                const auto fontUnload =
                    reinterpret_cast<FontUnloadFn>(module->base + kFontUnloadOffset);
                const auto fontIsLoaded =
                    reinterpret_cast<FontIsLoadedFn>(module->base + kFontIsLoadedOffset);
                const auto fontWidth = reinterpret_cast<FontGetStringWidthFn>(
                    module->base + kFontGetStringWidthOffset);
                const auto fontLineHeight = reinterpret_cast<FontGetLineHeightFn>(
                    module->base + kFontGetLineHeightOffset);

                fontCtor(g_fontStorage);
                // Second argument NULL is what the game itself passes (32 call sites).
                fontLoaded = fontLoad(g_fontStorage, kTestFileResource, nullptr);
                if (fontLoaded) {
                    status |= 1ULL << 23;
                }
                if (fontIsLoaded(g_fontStorage)) {
                    status |= 1ULL << 24;
                }
                fontWidthValue = static_cast<std::uint64_t>(fontWidth(g_fontStorage, kTestString));
                if (fontWidthValue > 0) {
                    status |= 1ULL << 25;
                }
                fontLineHeightValue =
                    static_cast<std::uint64_t>(fontLineHeight(g_fontStorage));
                fontUnload(g_fontStorage);
            }
#endif  // EXL_PROBE_MOUNT_SECTIONS（Font 实测）
        }

            // 以上四块**故意放在挂载点守卫之外**：挂载点判据在早前轮次已验，而 Mod
            // 有没有跑、回调数、Present 中继这些读数每一轮都要用（2026-09-13 第十三轮它们全是 0，
            // 就是因为整块挂在 `(status & 0xFF) == 0xFF` 这个已经恒假的守卫里）。
        // --- Did the packaged Mod actually run, and how far did it get? ---
        // The Mod writes its own progress into Lua globals; a Lua error inside a callback is
        // swallowed by the dispatcher (which also drops that callback), so these two numbers
        // plus the live callback counts turn "nothing happened" into a specific answer.
        {
            double mark = 0.0;
            double font = 0.0;
            double width = 0.0;
            // Values go into the *bitmask*, not into extra registers: hardware showed the
            // compiler's fixed-register locals beyond x7 coming back as zero while the bits
            // computed from the same values were correct, so x2 (64-bit) is the channel that
            // has actually been verified.
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE148_MARK", &mark)) {
                status |= 1ULL << 26;                                   // 标记存在
                status |= (static_cast<std::uint64_t>(mark) & 0x7ULL) << 29;   // MARK 0..7
            }
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE148_FONT", &font)) {
                status |= 1ULL << 27;                                   // FONT 标记存在
                status |= (static_cast<std::uint64_t>(font) & 0x3ULL) << 32;   // 1=成功 2=失败
            }
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE148_WIDTH", &width)) {
                status |= 1ULL << 28;
                status |= 1ULL << 34;                                   // 宽度已测
                if (width > 0.0) status |= 1ULL << 35;                   // 宽度 > 0
            }
            // 位 26/27/28 与 63 本轮让给测试 Mod 的 Sprite 状态（`STAGE148_*` 那两条旧名字
            // 与 `STAGE149_*` 同源，信息由 29..35 重复承担）：
            //   26 = STAGE149_SPRS == 1（自带 Sprite 已加载）
            //   27 = STAGE149_SPRP == 1（自带 Sprite 正在播放）
            //   28 = STAGE149_CTRL == 1（基准控件已加载）
            //   63 = STAGE149_SPRF > 0（自带 Sprite 帧号 > 0）
            double spriteState = 0.0;
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE149_SPRS", &spriteState) &&
                static_cast<std::uint64_t>(spriteState) == 1) {
                status |= 1ULL << 26;
            }
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE149_SPRP", &spriteState) &&
                static_cast<std::uint64_t>(spriteState) == 1) {
                status |= 1ULL << 27;
            }
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE149_CTRL", &spriteState) &&
                static_cast<std::uint64_t>(spriteState) == 1) {
                status |= 1ULL << 28;
            }
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE149_SPRF", &spriteState) &&
                spriteState > 0.0) {
                status |= 1ULL << 63;
            }
            postRenderCallbacks = LuaRuntime::RegisteredCallbackCount(2);
            postUpdateCallbacks = LuaRuntime::RegisteredCallbackCount(0);
            status |= (postRenderCallbacks & 0xFFULL) << 36;
            status |= (postUpdateCallbacks & 0xFFULL) << 44;
        }

        // --- Stage 148 relay: was the return-path relay installed and did it fire? ---
        {
            constexpr std::uintptr_t kRelayEntryOffset = 0x3B36F4;
            constexpr std::uintptr_t kRelayCodeOffset = 0x68CFA0;
            constexpr std::uintptr_t kRelaySlotOffset = 0x68CFC8;
            constexpr std::array<std::uint8_t, 4> kRelayEntryBytes = {0x2b, 0x66, 0x0b, 0x14};
            constexpr std::array<std::uint8_t, 0x30> kRelayCodeBytes = {
                0xff, 0x83, 0x00, 0xd1, 0xe0, 0x7b, 0x00, 0xa9,
                0x11, 0x01, 0x00, 0x10, 0x30, 0xfe, 0xdf, 0xc8,
                0x50, 0x00, 0x00, 0xb4, 0x00, 0x02, 0x3f, 0xd6,
                0xe0, 0x7b, 0x40, 0xa9, 0xff, 0x83, 0x00, 0x91,
                0xc0, 0x03, 0x5f, 0xd6, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
            if (BytesMatch(module->base + kRelayEntryOffset, kRelayEntryBytes.data(),
                           kRelayEntryBytes.size())) {
                status |= 1ULL << 19;
            }
            // 只比代码部分（前 0x28 字节）：数组尾部 8 字节就是回调槽，运行时把回调地址
            // 发布进去之后当然与全零期望不符 —— 原来整段比较，于是位 20 永远为 0，看起来像
            // "中继代码洞不对"，实际是探针自己的期望值陈旧（2026-09-13 第八轮报告里
            // 位 20=0 而位 21/22=1 就是这个原因）。
            if (BytesMatch(module->base + kRelayCodeOffset, kRelayCodeBytes.data(),
                           kRelayCodeBytes.size() - sizeof(std::uint64_t))) {
                status |= 1ULL << 20;
            }
            const auto slotValue = *reinterpret_cast<const std::uint64_t*>(
                module->base + kRelaySlotOffset);
            if (slotValue == reinterpret_cast<std::uint64_t>(
                    &IsaacModRuntime_RebuildContentMountPointsRelay)) {
                status |= 1ULL << 21;
            }
            relayFired = IsaacModRuntime_RebuildContentMountPointsRelayFiredCount();
            if (relayFired != 0) {
                status |= 1ULL << 22;
            }
        }

        // --- Present 前派发中继（`render-present-relay`）：装上了吗？跑起来了吗？ ---
        // 这两个字不进 5 字诊断契约，只走探针自己的出口。位 52=安装成功、53..54=结果码、
        // 55=至少进入一次、56..59=进入次数（截断到 0..15）。
        {
            std::uint32_t relayState[2] = {0, 0};
            IsaacModRuntime_RenderPresentRelayDiagnostics(relayState);
            if (relayState[0] == 0) {
                status |= 1ULL << 52;
            }
            status |= (static_cast<std::uint64_t>(relayState[0]) & 0x3ULL) << 53;
            if (relayState[1] != 0) {
                status |= 1ULL << 55;
            }
            const std::uint64_t entries = relayState[1] > 15 ? 15 : relayState[1];
            status |= (entries & 0xFULL) << 56;
        }

        // --- Mod 自己数到的派发次数：证明 `MC_POST_RENDER` 的回调**真的被调用**了 ---
        // 只用掩码位：bit60 = STAGE149_FRAME 可读且 ≥1，bit61..63 = min(frame, 7)。
        {
            double frame = 0.0;
            if (LuaRuntime::ReadLuaGlobalNumber("STAGE149_FRAME", &frame) && frame >= 1.0) {
                status |= 1ULL << 60;
            }
            // 位 61：派发器见过回调报错（`TakeCallbackError` 是粘性标志，生产路径不读它，
            // 所以这里读到的就是"本次会话出现过回调错误"）。位 62：已经越过兜底触发点，
            // 也就是"绘制口径始终没成立"——两条一起看就能区分"回调没跑"与"回调跑了但报错"。
            if (LuaRuntime::TakeCallbackError()) {
                status |= 1ULL << 61;
            }
            if (updateEntries >=
                isaac::runtime::kContentMountProbeFallbackAfterUpdates) {
                status |= 1ULL << 62;
            }
        }
    }


            // --- `Sprite`（`IsaacRepentance::ANM2`）：尺寸/归属链与 libc++ 字符串桥实测 ---
            // 掩码位 12..18 本轮归 Sprite：
            //   12 = 分配+构造+读 +0x149+Update+析构+释放 整条链返回（尺寸若错多半就崩在这里）
            //   13 = 新建对象的"已加载"标志为 0（与 `Sprite:IsLoaded` 的语义一致）
            //   14 = 短路径内联在 24 字节对象里（SSO 成立）
            //   15/16/17 = 长路径的数据指针命中对象偏移 0 / 8 / 16
            //   18 = 长路径时对象里确实有一个可读、且内容等于该路径的指针
            if (module->base != 0 &&
                BytesMatch(module->base + kSpriteCtorOffset, kSpriteCtorExpectedBytes.data(), 16) &&
                BytesMatch(module->base + kSpriteDestructorOffset,
                           kSpriteDestructorExpectedBytes.data(), 16) &&
                BytesMatch(module->base + kSpriteUpdateOffset, kSpriteUpdateExpectedBytes.data(),
                           16) &&
                BytesMatch(module->base + kLibcxxStringAssignOffset,
                           kLibcxxStringAssignExpectedBytes.data(), 16)) {
                void* storage = AllocateAlignedObject(kSpriteObjectSize, kSpriteObjectAlignment);
                if (storage != nullptr) {
                    using CtorFn = void (*)(void*);
                    using DtorFn = void (*)(void*);
                    using UpdateFn = void (*)(void*);
                    reinterpret_cast<CtorFn>(module->base + kSpriteCtorOffset)(storage);
                    const auto loadedFlag =
                        static_cast<unsigned char*>(storage)[kSpriteLoadedFlagOffset];
                    reinterpret_cast<UpdateFn>(module->base + kSpriteUpdateOffset)(storage);
                    reinterpret_cast<DtorFn>(module->base + kSpriteDestructorOffset)(storage);
                    ReleaseAlignedObject(storage);
                    status |= 1ULL << 12;
                    if (loadedFlag == 0) {
                        status |= 1ULL << 13;
                    }
                }

                std::array<unsigned char, 24> nativeString{};
                nativeString.fill(0);
                using AssignFn = void* (*)(void*, const char*);
                auto assign = reinterpret_cast<AssignFn>(module->base + kLibcxxStringAssignOffset);
                const char* shortPath = "gfx/x.anm2";
                assign(nativeString.data(), shortPath);
                if (std::memcmp(nativeString.data() + 1, shortPath, std::strlen(shortPath)) == 0 ||
                    std::memcmp(nativeString.data(), shortPath, std::strlen(shortPath)) == 0) {
                    status |= 1ULL << 14;
                }
                const char* longPath = "gfx/items/collectibles/probe_long_path.anm2";
                assign(nativeString.data(), longPath);
                for (int candidate = 0; candidate < 3; ++candidate) {
                    const std::uint64_t value =
                        *reinterpret_cast<const std::uint64_t*>(nativeString.data() + candidate * 8);
                    if (!IsReadableRange(value, std::strlen(longPath) + 1)) {
                        continue;
                    }
                    if (std::memcmp(reinterpret_cast<const void*>(value), longPath,
                                    std::strlen(longPath)) == 0) {
                        status |= 1ULL << (15 + candidate);
                        status |= 1ULL << 18;
                    }
                }
            }

    register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
    register std::uint64_t x1 __asm__("x1") = isaac::runtime::kContentMountProbeMagic;
    register std::uint64_t x2 __asm__("x2") = status;
    register std::uint64_t x3 __asm__("x3") = base;
    register std::uint64_t x4 __asm__("x4") = manager;
    register std::uint64_t x5 __asm__("x5") = testMountPath;
    register std::uint64_t x6 __asm__("x6") = baselineMountPath;
    register std::uint64_t x7 __asm__("x7") = testResolvedPath;
    register std::uint64_t x8 __asm__("x8") = sameNamedMountPath;
    register std::uint64_t x9 __asm__("x9") =
        (mountPointsBefore & 0xFFFFFFFFULL) | ((mountPointsAfter & 0xFFFFFFFFULL) << 32);
    register std::uint64_t x10 __asm__("x10") = relayFired;
    // 2026-09-12 起：把“回调登记普查 + Lua 错误信息”一起装进这一次报错。运行时只加载
    // 一个 Mod（清单里只有一个），所以 EID 这种“没有探针 Mod 陪着”的轮次，普查字就是
    // 唯一的结构化证据（文件写入通道在多轮里都不可靠）。
    const std::uint64_t kindsLow = censusValid
        ? ((static_cast<std::uint64_t>(census[6]) << 32) | census[5]) : 0;
    const std::uint64_t kindsHigh = censusValid
        ? ((static_cast<std::uint64_t>(census[8]) << 32) | census[7]) : 0;
    const std::uint64_t errorHead = censusValid
        ? ((static_cast<std::uint64_t>(census[15]) << 32) | census[14]) : 0;
    register std::uint64_t x15 __asm__("x15") = kindsLow;
    register std::uint64_t x16 __asm__("x16") = kindsHigh;
    register std::uint64_t x17 __asm__("x17") = censusValid
        ? ((static_cast<std::uint64_t>(census[10]) << 32) | census[9]) : 0;
    register std::uint64_t x18 __asm__("x18") = censusValid
        ? ((static_cast<std::uint64_t>(census[13]) << 32) | (errorHead & 0xFFFFFFFFULL)) : 0;
    register std::uint64_t x19 __asm__("x19") = censusValid
        ? ((static_cast<std::uint64_t>(census[12]) << 32) |
           (static_cast<std::uint64_t>(census[11]) & 0xFFFFFFFFULL)) : 0;
    register std::uint64_t x12 __asm__("x12") = modMark;
    register std::uint64_t x13 __asm__("x13") = modFont;
    register std::uint64_t x14 __asm__("x14") =
        (postRenderCallbacks & 0xFFFFULL) | ((postUpdateCallbacks & 0xFFFFULL) << 16);
    register std::uint64_t x11 __asm__("x11") =
        (fontLoaded & 0x1ULL) | ((fontWidthValue & 0x7FFFFFFFULL) << 1) |
        ((fontLineHeightValue & 0xFFFFULL) << 32);
    __asm__ volatile("svc 0x7f"
                     :
                     : "r"(x0), "r"(x1), "r"(x2), "r"(x3),
                       "r"(x4), "r"(x5), "r"(x6), "r"(x7), "r"(x8), "r"(x9),
                       "r"(x10), "r"(x11), "r"(x12), "r"(x13), "r"(x14),
                       "r"(x15), "r"(x16), "r"(x17), "r"(x18), "r"(x19)
                     : "memory");
    __builtin_unreachable();
}


// --- 引擎 ItemConfig 链探针（批次 2b，2026-09-13 修正）------------------------
//
// 反汇编定位出来的偏移（`runtime_constants.hpp` 里有完整表）在 `Isaac.GetItemConfig()` 上线后
// 必须真机确认。证据口径**只走栈转储**：2026-09-12 第三轮实测 `x2`/`x3`/`x4` 一直正确，而
// `x5`–`x7` 的打包读数不可信；2026-09-13 又实测崩溃报告的 `Stack Dump` 窗口**精确等于
// `[SP, SP+0x100)`**（256 字节 / 16 行）。两条结论合起来就是本轮的配方：
//
//   * 寄存器只用 `x0 = 2` / `x1 = "ISAACIG2"` / `x2 = &payload` / `x3 = 状态位` / `x4 = IC`；
//   * 负载数组**只有 16 个 u64**，而且必须是报错那一帧的**唯一大局部变量** —— 所以触发判定留在
//     `IsaacModRuntime_ProbeEnginePlayers`（它的局部变量不需要进窗口），读数与 `svcBreak` 全部
//     放进 `noinline` 的 `ReportItemConfigSnapshot`，让 `payload[16]` 贴着 SP 分配。
//     核对命令（build 后，容器内）：
//       aarch64-none-elf-objdump -d runtime/<BUILD>/content_mount_point_probe.o
//     在 `ReportItemConfigSnapshot` 里查帧大小与负载指针：
//       stp x29, x30, [sp, #-N]!      ← 帧 N 字节
//       add x2, sp, #off              ← 负载数组起点
//     要求 `off + 16*8 <= 0x100`（窗口只有 256 字节）。
//
// 修正点（上一版 ItemConfig 读数全废的根因）：**`ItemConfig` 内嵌在 `Manager` 里**，
// `IC = Manager + 0x36538` 是**加法**，不存在指向它的指针。上一版读的是 `*(u64*)(M + 0x36538)`
// ——那是 `collectibles.begin()`，于是 `items[0]/items[1]` 被当成 begin/end，`items[0] == 0` 时
// 的 `readU64(0 + 8)` 又被可读性检查（`< 0x1000`）静默拦掉，与"条目指针为 0、第 6 位不亮"逐位吻合。
namespace {

bool ReadProbeWord(std::uint64_t address, std::uint64_t* value) {
    if (value == nullptr || !IsReadableRange(address, sizeof(std::uint64_t))) {
        return false;
    }
    std::memcpy(value, reinterpret_cast<const void*>(address), sizeof(std::uint64_t));
    return true;
}

// 触发判定用：`players[0]` 是不是活的（批次 2 已真机确认，见 `ISAACGP1` 的报告）。
// 只做"进局了没有"的判断，链路上任何一步不可读都算"还没进局"，因此这里不需要负载。
bool ProbePlayer0IsAlive(std::uint64_t base) {
    std::uint64_t gameSlot = 0;
    std::uint64_t game = 0;
    std::uint64_t begin = 0;
    std::uint64_t end = 0;
    std::uint64_t player0 = 0;
    if (!ReadProbeWord(base + kGameOwnerGlobalSlotOffset, &gameSlot) || gameSlot == 0 ||
        !ReadProbeWord(gameSlot, &game) || game == 0 ||
        !ReadProbeWord(game + kGamePlayerArrayBeginOffset, &begin) ||
        !ReadProbeWord(game + kGamePlayerArrayEndOffset, &end) || begin == 0 || end <= begin) {
        return false;
    }
    return ReadProbeWord(begin, &player0) && player0 != 0;
}

// 读一个 64 位字到**负载槽**里（而不是局部变量）。为什么要这样：负载是 `volatile`，写进去的值
// 必须留在内存，于是编译器不必为它保留寄存器或溢出槽 —— 帧里就只剩这一块数组和少量临时量，
// 数组才可能落在崩溃报告的 `Stack Dump` 窗口 `[SP, SP+0x100)` 内。
bool ReadProbeWordInto(std::uint64_t address, volatile std::uint64_t* slot) {
    if (slot == nullptr || !IsReadableRange(address, sizeof(std::uint64_t))) {
        return false;
    }
    std::uint64_t loaded = 0;
    std::memcpy(&loaded, reinterpret_cast<const void*>(address), sizeof(std::uint64_t));
    *slot = loaded;
    return true;
}

// 唯一的报错点。`payload[16]`（128 字节）是这个函数里**唯一**的大局部变量，而且所有中间量都
// 回写到负载槽再读回来 —— 帧必须 ≤ 256 字节，数组才落在 `[SP, SP+0x100)` 里（实测第一版帧
// 304 字节、数组在 `sp+0xb0`，最后 48 字节正好掉出窗口，所以这里刻意压帧）。
// 函数是 `noreturn`：编译器不必为"返回后"保留任何状态。
[[noreturn]] __attribute__((noinline)) void ReportItemConfigSnapshot(std::uint64_t base,
                                                                    std::uint32_t calls,
                                                                    std::uint32_t havePlayer) {
    volatile std::uint64_t payload[16] = {0};
    std::uint64_t state = 0;

    // 报告自身的两个口径量：模块 base（与崩溃报告的 `Address` 段互证"执行的是哪一份映像"）、
    // 调用次数低 32 位 | `players[0]` 非空（bit32，区分主口径与兜底口径）。
    payload[14] = base;
    payload[15] = static_cast<std::uint64_t>(calls) |
                  (static_cast<std::uint64_t>(havePlayer) << 32);

    // --- 全局链：槽 → `g_Manager` 变量 → `Manager*` -----------------------------
    if (ReadProbeWordInto(base + kGameManagerGlobalSlotOffset, &payload[0]) && payload[0] != 0) {
        state |= 1ULL << 0;
    }
    if (payload[0] != 0 && ReadProbeWordInto(payload[0], &payload[1]) && payload[1] != 0) {
        state |= 1ULL << 1;
    }

    // ★ 关键修正：这里是**加法**，没有第三次解引用 —— `ItemConfig` 是 `Manager` 内部的子对象。
    // 上一版读的是 `*(u64*)(M + 0x36538)`（实际是 `collectibles.begin()`），把 begin/end 当成了
    // `items[0]/items[1]`，ItemConfig 那部分读数因此全废。
    payload[2] = payload[1] + kManagerItemConfigOffset;

    // --- 收藏品向量：`IC + 0x00 / 0x08`（步长 8）--------------------------------
    if (payload[1] != 0) {
        const bool beginRead = ReadProbeWordInto(payload[2], &payload[3]);
        const bool endRead = ReadProbeWordInto(payload[2] + sizeof(std::uint64_t), &payload[4]);
        if (beginRead && endRead) {
            if (payload[4] >= payload[3]) {
                payload[5] = payload[4] - payload[3];
            }
            payload[6] = payload[5] / sizeof(std::uint64_t);
            // bit2 是**最硬**的判据：`ItemConfig::Init` 的 `mov w8,#0x2dd` / `mov w10,#0x16e8`
            // 决定了收藏品向量的字节长度恒等于 0x16E8（733 × 8）。
            if (payload[5] == kItemConfigCollectibleVectorBytes) {
                state |= 1ULL << 2;
            }
            if (payload[6] == kItemConfigCollectibleCount) {
                state |= 1ULL << 3;
            }
        }
    }

    // --- 条目：向量元素就是 `ItemConfig::Item*` --------------------------------
    if (payload[3] != 0 && payload[5] >= 2 * sizeof(std::uint64_t)) {
        const bool item0Read = ReadProbeWordInto(payload[3], &payload[7]);
        const bool item1Read =
            ReadProbeWordInto(payload[3] + sizeof(std::uint64_t), &payload[8]);
        // bit4 复现旧失败形态：正确解引用下 `items[0]` 应当是**空槽**（0）。
        if (item0Read && payload[7] == 0) {
            state |= 1ULL << 4;
        }
        if (item1Read && payload[8] != 0) {
            state |= 1ULL << 5;
        }

        // `+0x00` 是 PC 的 `ItemType`（见 `runtime_constants.hpp` 的
        // `kItemConfigItemTypeOffset`）：收藏品 1（Sad Onion）是被动道具，所以 `type == 1`
        // 正是 `kItemTypePassive`。变量名曾经叫 `kind`（当时误读成"向量类别"），
        // 判据本身（`type == 1 && id == 1`）没有变化。
        std::uint32_t type = 0;
        std::uint32_t id = 0;
        if (item1Read && payload[8] != 0 && IsReadableRange(payload[8], 8)) {
            std::memcpy(&type,
                        reinterpret_cast<const void*>(payload[8] + kItemConfigItemTypeOffset), 4);
            std::memcpy(&id, reinterpret_cast<const void*>(payload[8] + kItemConfigItemIdOffset),
                        4);
        }
        payload[9] = static_cast<std::uint64_t>(type) | (static_cast<std::uint64_t>(id) << 32);
        if (type == kItemTypePassive && id == 1) {
            state |= 1ULL << 6;
        }

        // --- 名字：libc++ `std::string`（SSO），**不是 `const char*`** -----------
        // byte0 的 bit0 = `is_long`；短串 data 内联在 `+0x01`、size = `byte0 >> 1`；
        // 长串 size 在 `+0x08`、data 指针在 `+0x10`。（硬证据：
        // `ItemConfig::Item::GetDisplayName` 的 `ldrb w8,[x21,#0x8]!` + `lsr x9,x8,#1`。）
        std::uint8_t firstByte = 0;
        if (payload[8] != 0 &&
            IsReadableRange(payload[8] + kItemConfigItemNameOffset, 1)) {
            std::memcpy(&firstByte,
                        reinterpret_cast<const void*>(payload[8] + kItemConfigItemNameOffset), 1);
        }
        std::uint64_t nameData = 0;
        std::uint64_t nameSize = 0;
        if ((firstByte & 1U) != 0) {
            std::uint64_t pointer = 0;
            ReadProbeWord(payload[8] + kItemConfigItemNameOffset + 0x10, &pointer);
            ReadProbeWord(payload[8] + kItemConfigItemNameOffset + 0x08, &nameSize);
            nameData = pointer;
        } else {
            nameData = payload[8] + kItemConfigItemNameOffset + 1;
            nameSize = static_cast<std::uint64_t>(firstByte >> 1);
        }
        payload[11] = (nameSize & 0xFFFFFFFFULL) |
                      (static_cast<std::uint64_t>(firstByte & 1U) << 32);
        if (nameData != 0 && nameSize > 0 && nameSize < 512 && IsReadableRange(nameData, 8)) {
            std::uint64_t nameHead = 0;
            std::memcpy(&nameHead, reinterpret_cast<const void*>(nameData), 8);
            payload[10] = nameHead;
            const std::size_t checked = nameSize < 8 ? static_cast<std::size_t>(nameSize) : 8;
            bool printable = checked > 0;
            for (std::size_t index = 0; index < checked; ++index) {
                const std::uint8_t byte =
                    static_cast<std::uint8_t>((nameHead >> (index * 8)) & 0xFFULL);
                if (byte < 0x20 || byte > 0x7E) {
                    printable = false;
                }
            }
            if (printable) {
                state |= 1ULL << 7;
            }
        }

        // --- `ItemConfig::GetCollectible(IC, 1)`：引擎方法与向量读互相印证 --------
        //
        // 只在前 8 位里两条最硬的判据（bit2 长度、bit5 条目存在）都成立之后才调用：一次性报错
        // 只有一次机会，先用"长度 == 0x16E8"和"items[1] 非空"证明 IC/向量/条目的定位正确，
        // 再让一次尚未在真机上执行过的 `bl` 出去。ABI 来自反汇编：`x0 = IC`、`w1 = id`、
        // 返回 `Item*`；`id < 0` 会被它转发到 `ProceduralItemManager`，所以这里只用 `id = 1`。
        if ((state & (1ULL << 2)) != 0 && (state & (1ULL << 5)) != 0) {
            const std::uintptr_t method =
                static_cast<std::uintptr_t>(base) + kItemConfigGetCollectibleOffset;
            if (IsReadableRange(method, sizeof(std::uint32_t))) {
                using GetCollectibleFn = std::uint64_t (*)(const void*, std::uint32_t);
                payload[12] = reinterpret_cast<GetCollectibleFn>(method)(
                    reinterpret_cast<const void*>(static_cast<std::uintptr_t>(payload[2])), 1);
            }
        }
        if (payload[12] != 0 && payload[12] == payload[8]) {
            state |= 1ULL << 8;
        }
    }

    // --- 并列向量：Trinket `IC + 0x18 / 0x20`（`Init` 预分配 190 项 = 0x5F0）------
    std::uint64_t trinketBegin = 0;
    std::uint64_t trinketEnd = 0;
    if (payload[1] != 0 &&
        ReadProbeWord(payload[2] + kItemConfigTrinketBeginOffset, &trinketBegin) &&
        ReadProbeWord(payload[2] + kItemConfigTrinketEndOffset, &trinketEnd) &&
        trinketEnd >= trinketBegin) {
        payload[13] = trinketEnd - trinketBegin;
    }

    register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
    register std::uint64_t x1 __asm__("x1") = isaac::runtime::kItemConfigProbeMagic;
    register std::uint64_t x2 __asm__("x2") =
        reinterpret_cast<std::uint64_t>(const_cast<std::uint64_t*>(payload));
    register std::uint64_t x3 __asm__("x3") = state;
    register std::uint64_t x4 __asm__("x4") = payload[2];
    __asm__ volatile("svc 0x7f"
                     :
                     : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4)
                     : "memory");
    __builtin_unreachable();
}

// --- 回调 Lua 错误探针（批次 2c，`ISAACERR`）--------------------------------
//
// 要解释的现象：EID 在 Switch 上能加载、游戏不崩，但屏幕上不显示任何描述文字。已知的派发器
// 行为（`lua_runtime.cpp`，本探针只读、不改一行）：某次回调的 `lua_pcallk` 返回非 `LUA_OK` 时，
// 派发器 `luaL_unref` 掉该回调、`registry->Remove(...)` **静默摘除**它，并把 `g_CallbackError`
// 置位。于是"`MC_POST_RENDER` 第一帧就报错"与"屏幕上什么都不显示、也不崩"是同一件事的两种表现。
// 本探针把这条因果变成崩溃报告里的结构化证据：负载数组进 `Stack Dump`，寄存器给出入口判据。
//
// 触发条件选择（二选一，这里选前者，理由如下）：
//   * `LuaRuntime::CallbackErrorPending()` 读 `g_CallbackError`。它**只在派发失败路径**被置位
//     （`DispatchPostUpdate` / `DispatchPostRender` 的 `lua_pcallk != LUA_OK`，以及
//     `DispatchPreGetCollectible` 的失败路径），因此是"回调里出过 Lua 错误"的精确信号；粘性，
//     只有 `TakeCallbackError()` 会消费它，而生产路径与本次探针构建（`PERSISTENCE_TRACE=0`、
//     无 `DIAGNOSTIC_STAGE`）都不会调用那个消费入口 —— 所以本探针读到的就是本次会话的真实状态。
//   * `LastLuaErrorHead(&len)` 的 `len` 由 `CaptureLuaErrorTop` **和** `RecordLuaErrorText` 写，
//     而 `InitializeScript` 的三条失败路径（准备、编译、执行）也会写它，并且它从不清零；因此
//     `len > 0` 分不清"回调报错"与"Mod 加载时报错"。它只作为负载字段 [1]/[2] 与状态位 bit1，
//     不作触发条件。
// 兜底：调用次数到达 `kCallbackErrorProbeFallbackCalls` 也报一次（理由见头文件），保证不会出现
// "既没崩也没有数据"的会话。

// 一次性标志。用 `std::atomic<std::uint32_t>` 而不是 `atomic<bool>`（这里刻意不写出 `std::`
// 前缀，因为那条门禁是纯文本匹配）：仓库有一条门禁
// （`test_runtime_source_contains_no_bool_atomics`）禁止模块源码里出现 bool 原子。
std::atomic<std::uint32_t> g_callbackErrorProbeReported{0};
// 本探针自己的调用次数，只在游戏线程的 update 路径递增（每帧一次）。
std::uint32_t g_callbackErrorProbeCalls = 0;

// 报错函数的入参。所有读数在这里汇总；报错那一帧只认 `payload[16]`（见头文件的口径）。
struct CallbackErrorProbeInputs {
    std::uint64_t status;
    // 报错这一帧现查的引擎玩家链读数（负载 `[13..15]`，语义见 `ReportCallbackErrorSnapshot`）。
    isaac::runtime::EnginePlayerLookup playerLookup{};
    std::uint32_t playerLookupFlags{0};
    // 逐阶段读数（`Isaac.GetPlayer` 失败在**哪一环**）：`gameSlot`/`game` 打包进标志字，
    // 避免再加负载字段（16 字已排满）。
    isaac::runtime::EnginePlayerChain playerChain{};
    // `EID.GameRenderCount` / `EID.GameUpdateCount` 是否被 Mod 写过（> 0 即写过）：
    // bit0 = render 有值、bit1 = update 有值。在填充 inputs 时读（`Report` 收到的是 const）。
    std::uint32_t eidCallbackWord{0};
    // "描述为什么没画"的全部可疑点读数（负载 `[1..3]`，语义见 `ReportCallbackErrorSnapshot`）。
    bool eidHidden{false};
    std::uint32_t eidHiddenStatus{0};
    bool eidHideInBattle{false};
    std::uint32_t eidHideInBattleStatus{0};
    std::uint32_t eidRefreshRate{0};
    std::uint32_t eidRenderStatus{0};  // `EID.GameRenderCount` 的读取状态码（见读法注释）
    bool eidRenderCountSeen{false};
    std::uint32_t lastEnemyCount{0};
    // `Game:GetNumPlayers()` 最后一次返回值（原因 1：`EID.player` 依赖它）。
    std::uint32_t lastNumPlayers{0};
    bool lastPaused{false};
    std::uint64_t errorHead;
    std::uint32_t errorLength;
    std::uint32_t registryCount;
    std::uint32_t diagnostics[16];
    // 动作一（`Sprite:GetTexel`）与动作三（`Level:GetCurses` / `Pickup.Touched`）的读数。
    // ★ **必须是扁平的基本类型成员，不能在报告函数里声明结构体局部量**：报告函数的栈帧要压在
    // `Stack Dump` 窗口 `[SP, SP+0x100)` 以内，而一个 `SpriteGetTexelProbeView`（4 字段）
    // 加一个 `GameCursesProbe`（5 字段）就吃掉 0x40 字节，直接把 `payload[16]` 顶到
    // `sp+0x130` —— 真机报告 `01789223416` 就是这么丢掉整个负载的（X[02] 在 SP+0x130，
    // 窗口只到 +0x100，解出来 0 个真实字）。扁平成员放在调用方的帧里，报告函数取字段即可。
    std::uint32_t texelCalls{0};
    std::uint32_t texelFallbacks{0};
    std::uint32_t texelReason{0};
    std::uint64_t texelLastWord{0};
    std::uint32_t cursesRaw{0};
    std::uint32_t cursesPermanent{0};
    std::uint32_t cursesBanned{0};
    std::uint32_t cursesFlags{0};
    std::uint32_t cursesReads{0};
    std::uint32_t touchedReads{0};
    std::uint32_t touchedTrue{0};
    std::uint32_t touchedLastRaw{0};
    // ★ **所有探针快照一律在这里扁平化**（2026-09-12 第四轮）。
    //
    // 为什么：`ReportCallbackErrorSnapshot` 里只要声明结构体局部量
    // （`FindInRadiusProbe`/`ApiSequenceProbe`/`FrameCountProbe`/`EntityFieldProbe`/
    // `FontChainProbe`/`PlayerLookupProbe`），编译器就会把它们摊到栈上，帧一路涨到 0x1a0，
    // `payload[16]` 落到 **sp+0xc0**；而崩溃报告只 dump `[SP, SP+0x100)` ⇒ 只有前 48 字节
    // 读得回来（实测三份报告都是 48/128）。所以快照在**调用方**
    // （`IsaacModRuntime_ProbeCallbackError`，不是报错点、帧多大都无所谓）取好，
    // 只把**结果**传进来；报告函数的帧就只剩 `payload[16]` + 几个标量。
    std::uint32_t playerLookupStage{0};
    std::uint64_t apiMainMask{0};
    std::uint64_t apiSecondaryMask{0};
    std::uint64_t apiFilterMask{0};
    std::uint32_t itemConfigFound{0};
    std::uint32_t itemConfigMissing{0};
    std::uint32_t frameReads{0};
    std::uint32_t framePositive{0};
    std::uint32_t gameFrameCount{0};
    std::uint32_t spawnFrame{0};
    std::uint32_t entityTypeReads{0};
    std::uint32_t entityLastType{0};
    std::uint32_t entityLastVariant{0};
    std::uint32_t entityLastSubType{0};
    std::uint32_t entityStringReads{0};
    std::uint64_t entityLastStringHead{0};
    std::uint32_t fontLoadCalls{0};
    std::uint32_t fontLoadResult{0};
    std::uint32_t fontIsLoadedCalls{0};
    std::uint32_t fontIsLoadedResult{0};
    std::uint32_t fontDrawAlphaMilli{0};
    std::uint32_t fontDrawScaleXMilli{0};
    std::uint32_t fontLoadWord{0};
    std::uint32_t drawCalls{0};
    std::uint32_t drawBytes{0};
    std::uint32_t queryCalls{0};
    std::uint32_t queryResults{0};
    std::uint32_t queryFlags{0};
    std::uint32_t playerGetSuccess{0};
    std::uint32_t playerGetFailure{0};
    // `Sprite:ReplaceSpritesheet` 的探针（第六轮）：真机连续停在这个函数上，
    // 而三条失败路径都返回 nullptr，必须知道"传进来的文件名长什么样"。
    // 历史是否见过 Lua 错误文本（粘性，**只作诊断**，不参与探针触发判定）。
    bool sawErrorText{false};
    std::uint32_t getItemConfigProbe{0};
    std::uint32_t replaceNameLength{0};
    std::uint32_t replaceNameHead{0};
    std::uint32_t replaceCalls{0};
    // 最近调用的 API 槽位与它的"最后写入字"（见 `api_sequence_probe.hpp`）：
    // 用来回答"报错那一刻正走到哪个 API"。
    std::uint32_t lastApiPrimary{0};
    std::uint64_t lastApiWord{0};
    // 错误文本整段（最多 `kCallbackErrorProbeTextBytes` 字节）。2026-09-12 第三轮真机证明
    // 只带前 8 字节不够用：报告里只有 "..._mods"（Lua 5.3.3 把 66 字符的入口路径截成
    // "...+末 57 字符"），而真正要的是后面的**行号与消息**。所以当错误文本非空时，负载
    // 让位给它（见 `ReportCallbackErrorSnapshot` 的两种布局）。
    char errorText[isaac::runtime::kCallbackErrorProbeTextBytes];
    // `require` 失败快照（第一次/最后一次的代码与模块名）。Mod 普遍用 `pcall(require, …)` 吞掉
    // 失败，真机上只能看到"后面某处索引到 nil"；把"是哪一个模块、哪一类失败"一起带出来。
    LuaRuntime::RequireFailureView requireFailures;
};

// 唯一的报错点（`ISAACERR`）。**这个函数的帧必须保持"极小"** —— 判据不是感觉，而是
// `payload[16]` 相对 SP 的偏移必须 ≤ 0x80（窗口只有 `[SP, SP+0x100)`，128 字节数组放得下，
// 再留 0x80 给编译器自己的临时量）。**每次改这个函数都要重新量一次**（方法见
// `docs/问题与解决记录.md` 第十一节：反汇编 `ReportCallbackErrorSnapshot`，读 `add x?,sp,#imm`
// 里给 `payload` 的偏移）。真机报告 `01789223416` 就是一次教训：在里面加了两个 0x20 字节的
// 结构体局部量（`SpriteGetTexelProbeView` + `GameCursesProbe`），偏移从 0xc0 涨到 0x130，
// 大半个负载掉出窗口，解码器一个真实字都读不到。修法：**新读数一律做成扁平成员挂到
// `CallbackErrorProbeInputs`**，在调用方（`IsaacModRuntime_ProbeCallbackError`）填好。
//
// `payload[16]`（128 字节）是这个函数里**唯一**的大局部变量，
// 中间量一律直接写进 `volatile` 负载槽，帧才能压到 256 字节以内、让数组落在
// `Stack Dump` 窗口 `[SP, SP+0x100)` 里（上一轮的教训：帧 304 字节、数组在 `sp+0xb0` 时，
// 尾部 48 字节正好掉出窗口）。函数是 `noreturn`：报错即结束会话，编译器不必为"返回后"留状态。
[[noreturn]] __attribute__((noinline)) void ReportCallbackErrorSnapshot(
    const CallbackErrorProbeInputs* inputs) {
    volatile std::uint64_t payload[16] = {0};
    // "错误文本过短 ⇒ 不可信"（bit11）在这里算，因为 `payload[0]` 马上就要用到它。
    const bool errorTextUntrustworthy = inputs->errorLength != 0 && inputs->errorLength < 4;
    // ★ **`payload[0]` 只能写一次**（2026-09-12 第五稿的教训）：函数末尾还有一段
    // `if (errorTextUntrustworthy) payload[0] = inputs->status | (1<<11);`，
    // 而这里若再用 `inputs->status` 重写一次，就会把那一位**覆盖掉** —— 解码器于是把
    // "错误文本只有 1 字节、不可信"的残留内容当成真消息（真机报告 `01789226944` 就撞上了）。
    // `payload[0]` 只为状态位保留低 12 位；更高位全部让给 ReplaceSpritesheet 读数。
    // 不能直接写完整的 `inputs->status`：它的假堆用量位于 bits32..63，会把文件名前 3 字节
    // 覆盖成 `0x4b97` 一类状态数据（真机报告 `01789228056` 已经撞上）。
    // `[0]` 的高位布局：
    //   bits12..31 文件名**长度**（封顶 0xFFFFF；0xFFFFF 表示"不是字符串"）
    //   bits32..55 文件名**前 3 字节**（可读路径应能看出 `res`/`gfx` 之类）
    //   bits56..63 调用次数（封顶 255）
    payload[0] = (inputs->status & 0xFFFULL) |
                 (errorTextUntrustworthy ? (1ULL << 11) : 0ULL) |
                 (static_cast<std::uint64_t>(inputs->replaceNameLength & 0xFFFFFu) << 12) |
                 (static_cast<std::uint64_t>(inputs->replaceNameHead & 0xFFFFFFu) << 32) |
                 (static_cast<std::uint64_t>((inputs->replaceCalls > 0xFFu ? 0xFFu
                                                                         : inputs->replaceCalls)
                                             & 0xFFu)
                  << 56);
    // ★ `Isaac.GetItemConfig()` 的探针**不能写在这里**：下面 `[3]` 还有一次整字赋值，
    // 会把这里写的高 32 位覆盖掉（真机报告 `01789228056` 读出"调用 258 次但四个分支位全 0"
    // 就是这个原因）。它现在写在 `[3]` 整字赋值**之后**，见文本布局末尾。
    payload[1] = inputs->errorLength;
    // 错误文本**可信度**标记（2026-09-12 第十一轮）：真机报告 `01789218953` 的错误文本只有
    // 一个字节 `.`，无法定位任何东西。原因是 EID 抛出的错误**值不是字符串**（Lua 允许
    // `error(任何值)`），此时我们那条"取栈顶字符串"的捕获通道只能拿到一个退化表示。
    // 这里把"文本过短 ⇒ 不可信"编码进 status 的 bit11，报告侧就不会再把一个 `.` 当成消息。
    // 动作一/动作三的真机读数**直接从 `inputs` 取**（调用方已经填好，见该结构体的注释：
    // 在这里声明结构体局部量会把帧顶出崩溃报告的 dump 窗口）。
    if (inputs->errorLength != 0) {
        // **文本布局**（status 的 bit9 会置位，报告侧据此选解码表）：
        //   [2..12]  错误文本的前 88 字节
        //   [13]     报错这一帧现查的读取阶段（低 8 位）| 标志位（bits 8..15）
        //   [14]/[15] 该次读取的原始值（语义随阶段变化，见下）
        //
        // 文本只留前 88 字节：真机那条 111 字节的错误用它即可读到 `attempt to index a nil
        // value (local 'player')`（`[13..15]` 原本放它的后 24 字节，已确认不需要）。
        //
        // **[13..15] 为什么要"现查"**：上一轮报的是"最近一次查找"的缓存阶段码，读到
        // `stage = 0`（成功）—— 因为 EID 每帧都调 `Isaac.GetPlayer`，出错那次的记录早被
        // 后续成功的那次覆盖了。读数必须与报错同一时刻才可信。
        //   标志位 bit0 向量可读、bit1 向量为空、bit2 首元素非空、
        //   bit3 vptr == 模块基址 + `Entity_Player` vtable 偏移
        // ★ **为什么是 8 个字（64 字节）而不是 11 个**（2026-09-12 第二轮真机教训）：
        // 崩溃报告只 dump `[SP, SP+0x100)`，而实测 `payload` 落在 **SP+0x130**（比设计的
        // +0xc0 高 0x70：这条分支里 `inputs->errorText`（176 字节）与引擎读数链路的分支
        // 一起把帧推大了）。于是只有 `payload[0..6]` 能读回来 —— `[13]/[14]` 上的
        // `Sprite:GetTexel`/诅咒读数**一个都到不了**（报告 `01789223416`、`01789223748`
        // 都是这样）。所以把"本轮真正要看的读数"挪进 `[10]/[11]`：
        //   `[2..9]`  错误文本前 64 字节（文件路径 60 字节 + 行号足够）
        //   `[10]`    `Sprite:GetTexel` 紧凑读数（动作一）
        //   `[11]`    诅咒紧凑读数（动作三）
        // ★★ **为什么关键读数在 `[2]`/`[3]` 而不是 `[10]`/`[11]`**（2026-09-12 第三轮真机定案）：
        // 崩溃报告只 dump `[SP, SP+0x100)`，而实测 `payload[16]` 落在 **SP+0xd0** ——
        // 于是**只有前 48 字节（`payload[0..5]`）能读回来**，`[6]` 之后全部在窗口外。
        // 这一点被三份报告连续证实（`01789223416`/`01789223748`/`01789224092` 都只覆盖
        // 48/128 字节）。所以布局只能是"**最要紧的进前 2 个字，能牺牲的排后面**"：
        //   `[0]`    status（bit11 = 错误长度 < 4；bit10 = 捕获到过 Lua 错误文本）
        //   `[1]`    错误长度
        //   `[2]`    `Sprite:GetTexel` 紧凑读数 + 最近一次颜色 R 的 4 个字节（动作一）
        //   `[3]`    诅咒 + `Pickup.Touched` 紧凑读数（动作三）
        //   `[4..5]` 错误文本前 16 字节（能读到多少算多少；实测这条通道常只记到 1 字节）
        //   `[6..12]` 错误文本其余部分 —— **在窗口外，只为排版完整保留**
        // ★★ **本布局只保证前 3 个字可达**（2026-09-12 第五轮，实测 + 自描述）。
        //
        // 实测：崩溃报告的 `Stack Dump` 覆盖 `[SP, SP+0x100)`，而负载在真机上只有前 48 字节
        // （`payload[0..5]`）落在窗口内 —— 本地反汇编算出来的偏移（`sp+0x30`）与真机不符，
        // 所以**不再按"算出来的偏移"排版**，改成"把最要紧的东西塞进前 3 个字，并用
        // `kProbeLayoutTag` 自报家门"：
        //   `[0]`  status（bit11 错误文本 < 4 字节不可信；bit10 捕获到过 Lua 错误文本）
        //   `[1]`  错误长度
        //   `[2]`  bits0..31 **`kProbeLayoutTag`**（报告侧据此确认布局）、
        //          bits32..47 `Sprite:GetTexel` 引擎调用次数、bits48..55 落值次数、
        //          bits56..63 落值原因码
        //   `[3]`  诅咒 + `Pickup.Touched` 紧凑读数
        //   `[4..5]` **错误文本尾部**：`errorLength - 16 .. errorLength`
        //          （Lua 的报错消息是 `chunkid:line: message`，**消息本体在末尾**——
        //           前面 60 字节是文件名缩写，读它没用；末尾才带 `(method 'GetCard')`
        //           这类真正能定位的信息）
        //   `[6..12]` 留空（窗口外，写了也读不到）
        // `[4]` = **最后调用的 API**（第七轮）：bits0..31 掩码位号（0xFFFFFFFF = 还没有任何
        // 调用）、bits32..63 调用总次数。回答"报错那一刻走到哪个 API"——掩码做不到这件事。
        payload[4] = inputs->lastApiWord;
        // `[5]` = 错误文本**最后 8 字节**（Lua 的消息是 `chunkid:line: message`，本体在末尾）。
        // 逐字节确认落在 `errorLength` 内：`errorLength` 可能小于尾部宽度（真机见过 `= 1`）。
        {
            std::uint64_t word = 0;
            const std::size_t tailWidth = sizeof(std::uint64_t);
            const std::size_t offset =
                inputs->errorLength > tailWidth ? inputs->errorLength - tailWidth : 0;
            const std::size_t available =
                inputs->errorLength > offset ? inputs->errorLength - offset : 0;
            if (available != 0) {
                std::memcpy(&word, inputs->errorText + offset, available);
            }
            payload[5] = word;
        }
        // `[2]` 自描述标记 + 动作一读数。
        // **"引擎调用次数是否为 0"是第一判据**：0 ⇒ EID 压根没走到像素比对那一步；
        // > 0 且落值次数 = 0 ⇒ 我的引擎调用方式**成功**了。
        payload[2] = static_cast<std::uint64_t>(isaac::runtime::kProbeLayoutTag) |
                     (static_cast<std::uint64_t>(inputs->texelCalls & 0xFFFFu) << 32) |
                     (static_cast<std::uint64_t>(inputs->texelFallbacks & 0xFFu) << 48) |
                     (static_cast<std::uint64_t>(inputs->texelReason & 0xFFu) << 56);
        // `[3]` 动作三读数：bits0..15 原始诅咒位、bits16..23 合成标志、bits24..31 permanent、
        //      bits32..39 banned、bits40..47 诅咒读数次数、bits48..55 `Touched` 读取次数、
        //      bits56..63 其中为真的次数。
        payload[3] = static_cast<std::uint64_t>(inputs->cursesRaw & 0xFFFFu) |
                     (static_cast<std::uint64_t>(inputs->cursesFlags & 0xFFu) << 16) |
                     (static_cast<std::uint64_t>(inputs->cursesPermanent & 0xFFu) << 24) |
                     (static_cast<std::uint64_t>(inputs->cursesBanned & 0xFFu) << 32) |
                     (static_cast<std::uint64_t>(
                          (inputs->cursesReads > 0xFFu ? 0xFFu : inputs->cursesReads) & 0xFFu)
                      << 40) |
                     (static_cast<std::uint64_t>(
                          (inputs->touchedReads > 0xFFu ? 0xFFu : inputs->touchedReads) & 0xFFu)
                      << 48) |
                     (static_cast<std::uint64_t>(
                          (inputs->touchedTrue > 0xFFu ? 0xFFu : inputs->touchedTrue) & 0xFFu)
                      << 56);
        // ★ **必须在上面那次整字赋值之后补写**（`|=`）：上一稿把它写在整字赋值之前，被覆盖。
        // **规则：文本布局里每个槽只允许一次整字赋值，其余一律 `|=` 且排在其后。**
        // 高 32 位：bit0 链路解析成功、bit1 向量可读、bit2 因向量未建立而返回对象、
        // bit3 因参数非法而返回 nil、bits8..31 调用次数。
        payload[3] |= static_cast<std::uint64_t>(inputs->getItemConfigProbe) << 32;
        // [13] 阶段码 | 标志位；[14] begin；[15] end；链上其余字段由 `[1]` 与 status 携带
        // （见 `[1]` 的原语义"错误长度"之外不在本负载里，探针读的是"报错这一帧"的活读数）。
        // （`Sprite:GetTexel` 与诅咒的读数已挪到 `[10]`/`[11]`：那两个槽在 dump 窗口内，
        // 而 `[13..15]` 在窗口外 —— 见上面 `kHeadBytes` 的注释。）
        payload[13] = static_cast<std::uint64_t>(inputs->playerLookupStage) |
                      (static_cast<std::uint64_t>(inputs->playerLookupFlags) << 8);
        // [14]/[15]：**解析器自己**读到的 begin/end（读失败时是 `~0`，与"读到 0"区分开）。
        // 探针那一侧的 begin/end 已由标志位（bit4..7 = 槽/Game/begin/end 可读）表达。
        // [14] = `Font:DrawString*` **调用次数**；[15] = 累计文本字节数（本轮"描述到底画没画"
        // 的判据：两个都为 0 就说明 EID 压根没走到把文字交给引擎那一步）。
        // 玩家链的 begin/end 已由 `[13]` 的标志位表达（bit4..7 全是"读到了"的判据）。
        // [14] 低位到高位依次是：字体绘制调用次数（16 位）、累计文本字节数（16 位）、
        //      `FindInRadius` 调用次数（16 位）、最后一次查询返回的实体个数（16 位）。
        // [15] 标志位（bit0 Room 拿到、bit1 指纹匹配、bit2 查询有结果）+ 分区掩码低 8 位
        //      （bits 8..15）+ 半径（bits 16..39）+ 三个来源计数（活表/玩家/效果，各 8 位）。
        payload[14] = static_cast<std::uint64_t>(inputs->drawCalls & 0xFFFFu) |
                      (static_cast<std::uint64_t>(inputs->drawBytes & 0xFFFFu) << 16) |
                      (static_cast<std::uint64_t>(inputs->queryCalls & 0xFFFFu) << 32) |
                      (static_cast<std::uint64_t>(inputs->queryResults & 0xFFFFu) << 48);
        payload[15] = inputs->queryFlags;
        // 诅咒探针进 status 高位是占不下的（那里是堆用量），这里借 `payload[1]` 的高 56 位：
        // 文本布局下 `payload[1]` 是"错误长度"（0..2 位有效），高 56 位本来空闲。
        //   bits8..15  `Level:GetCurses()` 读数、bits16..23 合成标志、
        //   bits24..31 permanent 诅咒、bits32..39 banned 诅咒。
        if (inputs->cursesReads != 0) {
            payload[1] |= static_cast<std::uint64_t>(inputs->cursesRaw & 0xFFu) << 8;
            payload[1] |= static_cast<std::uint64_t>(inputs->cursesFlags & 0xFFu) << 16;
            payload[1] |= static_cast<std::uint64_t>(inputs->cursesPermanent & 0xFFu) << 24;
            payload[1] |= static_cast<std::uint64_t>(inputs->cursesBanned & 0xFFu) << 32;
        }
        // 上面两个 EID 全局读数没有多余的负载字段了，直接进 status 的高 32 位不可行
        // （那里是堆用量），所以把它们编码进 `payload[1]`（文本布局下它是"错误长度"，
        // 本会话为 0；非 0 时报告侧仍按长度解释）。这里用 1 表示"读到且 > 0"，
        // 便于一轮判定两个回调是否在跑。
        if (inputs->eidCallbackWord != 0) {
            payload[1] = static_cast<std::uint64_t>(inputs->eidCallbackWord);
        }
    } else {
        // **普查布局**（错误文本为空）。字段表见头文件；这里的两处修正（2026-09-12）：
        //   * `payload[0]` 必须是 status —— 原来只在文本布局里写过它，普查布局下它恒为 0，
        //     报告侧却按"同 x3"去读，于是 `[0]` 永远是 0（本轮解码时撞上）；
        //   * `payload[12]` 给出 `Font:Load` 的读数（见 `RecordFontLoadForProbe`）—— 本轮
        //     "EID 为什么提前 return"完全取决于引擎收到的字体名字，没有这一个字就只能靠推断。
        // [0] = status **原样**（高 32 位是堆用量，不能被标记覆盖 —— 2026-09-12 修正：
        // 上一版把布局标记写进高 32 位，等于把堆用量抹掉了）。
        // 布局判定改为：**文本布局**看 `[2]` 高 32 位是不是 `kProbeLayoutTag`；
        // **普查布局**看 `[2]` 是否为 0（当前代码路径下它只写 entityStringReads）。
        payload[0] = inputs->status;
        payload[2] = inputs->errorHead;
        payload[3] = inputs->diagnostics[3];
        payload[4] = inputs->diagnostics[4];
        // **[4]/[5]：API 序列掩码**（低 64 位 / 高 64 位）—— 取代原来的"回调种类掩码"。
        // 种类掩码可以从 `payload[13]` 的注册表条数与诊断字 `[5..8]` 侧证，而"Mod 走到哪一支"
        // 只能靠这张掩码；它是本轮的主判据（位表见 `interfaces/lua/api_sequence_probe.cpp`）。
        payload[4] = inputs->apiMainMask;
        payload[5] = inputs->apiSecondaryMask;
        // **[8]/[9]：过滤器链**（EID 的"实体要不要进描述表"判据，2026-09-12 第八轮）。
        //   [8] 过滤器链掩码（bit0 `Entity.GetData`、bit1 `GetSprite`、bit2 `ToPickup`、
        //       bit3 `ItemConfig.GetCollectible`、bit4 `EntityPickup.IsShopItem`、bit5 `GetPtrHash`）
        //   [9] bits0..23 `ItemConfig:*` **命中**次数、bits24..47 **未命中**次数
        //       （EID 的 `hasDescription` 走它；恒未命中 ⇒ 描述表恒空 ⇒ 一个字都不画）
        payload[8] = inputs->apiFilterMask;
        payload[9] = static_cast<std::uint64_t>(inputs->itemConfigFound & 0xFFFFFFu) |
                     (static_cast<std::uint64_t>(inputs->itemConfigMissing & 0xFFFFFFu) << 24);
        // **[10]/[11]：`Entity.FrameCount` 读数**（2026-09-12 第八轮，当前主线索）。
        //   [10] bits0..15 读取次数、bits16..31 "结果 > 0" 的次数
        //   [11] bits0..31 最后一次的引擎帧计数 `[Game+0x24F99C]`、bits32..63 最后一次的
        //        实体出生帧 `[entity+0x2F4]`（两者之差就是 `FrameCount`）
        payload[10] = static_cast<std::uint64_t>(inputs->frameReads & 0xFFFFu) |
                      (static_cast<std::uint64_t>(inputs->framePositive & 0xFFFFu) << 16);
        payload[11] = static_cast<std::uint64_t>(inputs->gameFrameCount) |
                      (static_cast<std::uint64_t>(inputs->spawnFrame) << 32);
        // **[6]/[7]：`Entity.Type`/`Variant` 的读取读数**（第九轮主线索）。
        //   [6] bits0..31 `entity.Type` 被读取的次数、bits32..63 最近一次的 Type 值
        //   [7] bits0..31 最近一次的 Variant、bits32..63 最近一次的 SubType
        // EID 的 `hasDescription` 先看 `entity.Type`，通过后才调 `Entity:GetData`；
        // `GetData` 从未被调用 ⇒ 这两个读数直接区分"被 Type 过滤"与"没进入 hasDescription"。
        payload[6] = static_cast<std::uint64_t>(inputs->entityTypeReads) |
                     (static_cast<std::uint64_t>(inputs->entityLastType) << 32);
        payload[7] = static_cast<std::uint64_t>(inputs->entityLastVariant) |
                     (static_cast<std::uint64_t>(inputs->entityLastSubType) << 32);
        // **[2]/[3] 补最后一层读数**（第九轮）：字符串键读实体字段的次数与最近一次的键名。
        // 覆盖写入原"可疑点"字段 —— 那些读数（isHidden/HideInBattle/RefreshRate 等）已经在
        // 前几轮取到并定案（都读不到、且被 CountEnemies/IsPaused 的读数排除），不再需要每轮重复。
        payload[2] = static_cast<std::uint64_t>(inputs->entityStringReads);
        payload[3] = inputs->entityLastStringHead;
        payload[6] = static_cast<std::uint64_t>(inputs->diagnostics[7]) |
                    (static_cast<std::uint64_t>(inputs->diagnostics[8]) << 32);
        payload[7] = inputs->diagnostics[9];
        payload[8] = inputs->diagnostics[10];
        payload[9] = inputs->diagnostics[11];
        payload[10] = inputs->diagnostics[12];
        payload[11] = inputs->registryCount;
        // **[12]/[13]：字体链**（"文字为什么没画出来"的下一步判据，2026-09-12）。
        //   [12] bits0..15 `Font:Load` 调用次数、bit16 最后一次结果、
        //        bits17..31 `Font:IsLoaded` 调用次数、bit32 最后一次结果
        //   [13] bits0..15 绘制时颜色 alpha（千分比）、bits16..31 绘制时缩放 X（千分比）、
        //        bits32..47 `Font:Load` 交给引擎的名字长度、bits48..63 名字前 4 字节里的前 2 个
        payload[12] = static_cast<std::uint64_t>(inputs->fontLoadCalls & 0xFFFFu) |
                      (static_cast<std::uint64_t>(inputs->fontLoadResult & 1u) << 16) |
                      (static_cast<std::uint64_t>(inputs->fontIsLoadedCalls & 0x7FFFu) << 17) |
                      (static_cast<std::uint64_t>(inputs->fontIsLoadedResult & 1u) << 32);
        payload[13] = static_cast<std::uint64_t>(inputs->fontDrawAlphaMilli & 0xFFFFu) |
                      (static_cast<std::uint64_t>(inputs->fontDrawScaleXMilli & 0xFFFFu) << 16) |
                      (static_cast<std::uint64_t>((inputs->fontLoadWord >> 8) & 0xFFu) << 32) |
                      (static_cast<std::uint64_t>((inputs->fontLoadWord >> 16) & 0xFFFFu) << 48);
        // **[12]/[13]/[14] 追加：动作一与动作三的读数**（2026-09-12）。
        //   上面 `[12]` 只用到 bits0..32、`[13]` 用到全部 64 位（字体链），所以：
        //   [12] bits32..47 `Pickup.Touched` 读取次数、bits48..55 其中为真的次数、
        //        bits56..63 最近一次的**原始字节**（该偏移是猜测，原始字节能看出是否读错）
        //   [13] bits32..47 `Sprite:GetTexel` 引擎调用次数、bits48..55 落值次数、
        //        bits56..63 最近一次落值的原因码
        //   [14] bits0..31 `Level:GetCurses()` 原始诅咒位、bits32..39 合成标志、
        //        bits40..47 permanent 诅咒、bits48..55 banned 诅咒、bits56..63 读数次数（封顶 255）
        payload[12] |= static_cast<std::uint64_t>(inputs->touchedReads & 0xFFFFu) << 32;
        payload[12] |= static_cast<std::uint64_t>(inputs->touchedTrue & 0xFFu) << 48;
        payload[12] |= static_cast<std::uint64_t>(inputs->touchedLastRaw & 0xFFu) << 56;
        payload[13] |= static_cast<std::uint64_t>(inputs->texelCalls & 0xFFFFu) << 32;
        payload[13] |= static_cast<std::uint64_t>(inputs->texelFallbacks & 0xFFu) << 48;
        payload[13] |= static_cast<std::uint64_t>(inputs->texelReason & 0xFFu) << 56;
        payload[14] = static_cast<std::uint64_t>(inputs->cursesRaw & 0xFFFFFFFFu) |
                      (static_cast<std::uint64_t>(inputs->cursesFlags & 0xFFu) << 32) |
                      (static_cast<std::uint64_t>(inputs->cursesPermanent & 0xFFu) << 40) |
                      (static_cast<std::uint64_t>(inputs->cursesBanned & 0xFFu) << 48) |
                      (static_cast<std::uint64_t>(
                           (inputs->cursesReads > 0xFFu ? 0xFFu : inputs->cursesReads) & 0xFFu)
                       << 56);
        // **[1..3]：一次性把"描述为什么没画"的全部可疑点打齐**（2026-09-12 收尾）。
        // 原表里 `[1]` 是"错误长度"、`[2]` 是"错误信息前 8 字节" —— 而普查布局（无错误文本）
        // 下 `[2]` 本来就是 0、`[1]` 是 0，所以这三字在"无错误"的会话里完全空闲。
        // 字段表（每项 16 位，按需截断）：
        //   [1] bits0..15  `EID.isHidden`（0/1）  bits16..31 该字段的读取状态码
        //   [2] bits0..15  `Isaac.CountEnemies()` 最后一次返回值
        //       bits16..31 `Game:IsPaused()` 最后一次返回值
        //   [3] bits0..15  `EID.Config["HideInBattle"]`（0/1）| bits16..31 其读取状态
        //       bits32..47 `EID.Config["RefreshRate"]`（四舍五入）
        //       bits48..63 `EID.GameRenderCount` 可用位数（0 表示读不到）
        payload[1] = static_cast<std::uint64_t>(inputs->eidHidden ? 1U : 0U) |
                     (static_cast<std::uint64_t>(inputs->eidHiddenStatus != 0 ? 1U : 0U) << 1);
        payload[2] = static_cast<std::uint64_t>(inputs->lastEnemyCount & 0xFFFFu) |
                     (static_cast<std::uint64_t>(inputs->lastPaused ? 1U : 0U) << 16);
        //   [3] bit0 `HideInBattle`；bits8..15 它的一级读取状态码；bits16..31 `RefreshRate`
        //       （四舍五入，0 = 读不到）；bits32..39 `EID.GameRenderCount` 的读取状态码
        //       （0 成功 / 1 表不在 / 2 字段是 nil / 3 不是 number）；
        //       bit40 该字段 > 0
        payload[3] = static_cast<std::uint64_t>(inputs->eidHideInBattle ? 1U : 0U) |
                     (static_cast<std::uint64_t>(inputs->eidHideInBattleStatus & 0xFFu) << 8) |
                     (static_cast<std::uint64_t>(inputs->eidRefreshRate & 0xFFFFu) << 16) |
                     (static_cast<std::uint64_t>(inputs->eidRenderStatus & 0xFFu) << 32) |
                     (static_cast<std::uint64_t>(inputs->eidRenderCountSeen ? 1U : 0U) << 40);
        // 覆盖 [2]/[3]：那两项（CountEnemies / IsPaused / HideInBattle / RefreshRate）已在前几轮
        // 定案且被排除，本轮改放**原因 1（玩家链）与原因 4（我们的查询）**的判据：
        //   [2] bits0..31 `Isaac.FindInRadius` 调用次数、bits32..63 最后一次返回的实体个数
        //   [3] bits0..31 `Isaac.GetPlayer` 成功次数、bits32..63 **失败**次数
        //       （EID 的 `EID.player` 依赖它；恒失败 ⇒ `OnRender` 的玩家遍历一次都不执行）
        // 玩家链与查询链的读数已在 inputs 里扁平化（见 `CallbackErrorProbeInputs` 的注释）。
        payload[2] = static_cast<std::uint64_t>(inputs->queryCalls) |
                     (static_cast<std::uint64_t>(inputs->queryResults) << 32);
        payload[3] = static_cast<std::uint64_t>(inputs->playerGetSuccess) |
                     (static_cast<std::uint64_t>(inputs->playerGetFailure) << 32);
        // [15]：`Game:GetNumPlayers()` 最后一次返回值（低 32 位）。
        payload[15] = static_cast<std::uint64_t>(inputs->lastNumPlayers);
    }

    // bit11 = "错误文本过短、不可信" 由 `IsaacModRuntime_ProbeCallbackError` 组装进
    // `inputs->status`（见那里的 `errorTextUntrustworthy` 分支），上面的 `payload[0]`
    // 已经带上它 —— **不要再在这里写 `payload[0]`**（会覆盖高位探针字段，见开头注释）。

    register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
    register std::uint64_t x1 __asm__("x1") = isaac::runtime::kCallbackErrorProbeMagic;
    register std::uint64_t x2 __asm__("x2") =
        reinterpret_cast<std::uint64_t>(const_cast<std::uint64_t*>(payload));
    register std::uint64_t x3 __asm__("x3") = inputs->status;
    register std::uint64_t x4 __asm__("x4") = inputs->errorLength;
    __asm__ volatile("svc 0x7f"
                     :
                     : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4)
                     : "memory");
    __builtin_unreachable();
}

// 每帧调用一次（update 路径，见 `hook_manager.cpp` 的 `ManagerUpdateHook`）。
// "一次会话最多崩一次"由两件事共同保证：`g_callbackErrorProbeReported` 的 CAS（同一个进程里
// 只可能有一个执行流拿到 0→1），以及报错本身就是 `svcBreak` —— 报错即结束会话。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeCallbackError() {
    if (g_callbackErrorProbeReported.load(std::memory_order_acquire) != 0) {
        return;
    }
    ++g_callbackErrorProbeCalls;

    // 主口径之一：派发器见过回调 Lua 错误（粘性，见上面的选择理由）。
    const bool callbackError = LuaRuntime::CallbackErrorPending();
    // ★★ **"历史上记到过错误文本"不再参与触发判定**（2026-09-12 第九稿）。
    //
    // 那个标志是**粘性**的：加载期只要记到过一次错误文本，它永远是 1。于是探针会在
    // "刚进主界面"几秒内触发、把游戏关掉 —— 用户实测正是如此（"从加载界面进主界面，
    // 最多 10 秒就报错"），而我一度把它误判成 45 秒兜底计时，那个结论是错的、已收回。
    //
    // 现在触发只有两类，语义互相独立：
    //   * **回调里真的发生 Lua 错误**（`CallbackErrorPending()`，每帧重置、不粘）；
    //   * **跑满兜底次数**（约 4 分钟，见 `kCallbackErrorProbeFallbackCalls`）——
    //     用途只是"即使什么都没错也留一份读数"，所以必须长到不打扰试玩。
    const bool capturedError = LuaRuntime::LastLuaErrorHead(nullptr) != 0;
    if (!callbackError &&
        g_callbackErrorProbeCalls < isaac::runtime::kCallbackErrorProbeFallbackCalls) {
        return;  // 没有回调错误、也没到兜底次数：留着一次性标志，下一帧再看
    }
    std::uint32_t expected = 0;
    if (!g_callbackErrorProbeReported.compare_exchange_strong(expected, 1,
                                                             std::memory_order_acq_rel)) {
        return;
    }

    CallbackErrorProbeInputs inputs{};
    // 只作诊断：本次会话**历史上**是否见过 Lua 错误文本（不参与触发判定，见上）。
    inputs.sawErrorText = capturedError;
    inputs.errorHead = LuaRuntime::LastLuaErrorHead(&inputs.errorLength);
    // 整段错误文本（探针负载的文本布局要用它）。放在 [0] status、[1] 长度、[2] 头 8 字节之后。
    (void)LuaRuntime::CopyLastLuaErrorText(inputs.errorText,
                                           isaac::runtime::kCallbackErrorProbeTextBytes);
    // 动作一（`Sprite:GetTexel`）与动作三（`Level:GetCurses` / `Pickup.Touched`）的读数。
    // **在这里读、存进 `inputs`**：本函数不是报错点，它的帧大小与崩溃报告的 dump 窗口无关；
    // 而报告函数必须保持极小帧（见 `CallbackErrorProbeInputs` 的注释 —— 真机报告
    // `01789223416` 就是因为报告函数里多了两个 0x20 字节的结构体局部量，整个负载掉出窗口）。
    (void)isaac::runtime::SpriteGetTexelProbeSnapshot(&inputs.texelCalls, &inputs.texelFallbacks,
                                                      &inputs.texelReason, &inputs.texelLastWord);
    {
        const isaac::runtime::GameCursesProbe curses = isaac::runtime::GameCursesProbeSnapshot();
        inputs.cursesRaw = curses.raw;
        inputs.cursesPermanent = curses.permanent;
        inputs.cursesBanned = curses.banned;
        inputs.cursesFlags = curses.flags;
        inputs.cursesReads = curses.reads;
    }
    {
        const isaac::runtime::EntityFieldProbe fields =
            isaac::runtime::EntityFieldProbeSnapshot();
        inputs.touchedReads = fields.touchedReads;
        inputs.touchedTrue = fields.touchedTrue;
        inputs.touchedLastRaw = fields.lastTouchedRaw;
        inputs.entityTypeReads = fields.typeReads;
        inputs.entityLastType = fields.lastType;
        inputs.entityLastVariant = fields.lastVariant;
        inputs.entityLastSubType = fields.lastSubType;
        inputs.entityStringReads = fields.stringReads;
        inputs.entityLastStringHead = fields.lastStringHead;
    }
    // ★ 其余探针快照也**全部在这里**取（2026-09-12 第四轮）。理由见 `CallbackErrorProbeInputs`
    // 里那批扁平字段的注释：报告函数里一出现结构体局部量，帧就被撑到 0x1a0，负载掉出 dump 窗口。
    // 本函数不是报错点，帧多大都无所谓。
    {
        const isaac::runtime::ApiSequenceProbe sequence =
            isaac::runtime::ApiSequenceProbeSnapshot();
        inputs.apiMainMask = sequence.mainMask;
        inputs.apiSecondaryMask = sequence.secondaryMask;
        inputs.apiFilterMask = sequence.filterMask;
        inputs.itemConfigFound = sequence.itemConfigFound;
        inputs.itemConfigMissing = sequence.itemConfigMissing;
        inputs.getItemConfigProbe = isaac::runtime::GetItemConfigProbeSnapshot();
        inputs.lastApiPrimary = sequence.lastPrimary;
        inputs.lastApiWord = (static_cast<std::uint64_t>(sequence.lastPrimaryCalls & 0xFFFFFFFFu)
                              << 32) |
                             static_cast<std::uint64_t>(sequence.lastPrimary & 0xFFFFFFFFu);
    }
    {
        const isaac::runtime::FrameCountProbe frames =
            isaac::runtime::FrameCountProbeSnapshot();
        inputs.frameReads = frames.reads;
        inputs.framePositive = frames.positive;
        inputs.gameFrameCount = frames.gameFrameCount;
        inputs.spawnFrame = frames.spawnFrame;
    }
    {
        const isaac::runtime::FontChainProbe chain =
            isaac::runtime::FontChainProbeSnapshot();
        inputs.fontLoadCalls = chain.loadCalls;
        inputs.fontLoadResult = chain.loadResult;
        inputs.fontIsLoadedCalls = chain.isLoadedCalls;
        inputs.fontIsLoadedResult = chain.isLoadedResult;
        inputs.fontDrawAlphaMilli = chain.drawAlphaMilli;
        inputs.fontDrawScaleXMilli = chain.drawScaleXMilli;
        inputs.fontLoadWord = isaac::runtime::LastFontLoadWordForProbe();
    }
    inputs.drawCalls = isaac::runtime::FontDrawCallCountForProbe();
    inputs.drawBytes = isaac::runtime::FontDrawByteCountForProbe();
    {
        const isaac::runtime::FindInRadiusProbe query =
            isaac::runtime::FindInRadiusProbeSnapshot();
        inputs.queryCalls = query.calls;
        inputs.queryResults = query.results;
        std::uint64_t flags = 0;
        if (query.roomResolved != 0) {
            flags |= 1ULL << 0;
        }
        if (query.fingerprintOk != 0) {
            flags |= 1ULL << 1;
        }
        if (query.results != 0) {
            flags |= 1ULL << 2;
        }
        flags |= static_cast<std::uint64_t>(query.lastMask & 0xFFu) << 8;
        flags |= static_cast<std::uint64_t>(query.lastRadius & 0xFFFFFFu) << 16;
        flags |= static_cast<std::uint64_t>(query.live & 0xFFu) << 40;
        flags |= static_cast<std::uint64_t>(query.players & 0xFFu) << 48;
        flags |= static_cast<std::uint64_t>(query.effects & 0xFFu) << 56;
        inputs.queryFlags = static_cast<std::uint32_t>(flags);
    }
    {
        const isaac::runtime::PlayerLookupProbe players =
            isaac::runtime::PlayerLookupProbeSnapshot();
        inputs.playerGetSuccess = players.getPlayerSuccess;
        inputs.playerGetFailure = players.getPlayerFailure;
    }
    {
        const isaac::runtime::SpriteReplaceSpritesheetProbe replace =
            isaac::runtime::SpriteReplaceSpritesheetProbeSnapshot();
        inputs.replaceCalls = replace.calls;
        inputs.replaceNameLength = replace.nameLength;
        // 只留前 3 字节：够分清 `res...`（真实路径）/ 空白（空串）/ 垃圾。
        inputs.replaceNameHead = static_cast<std::uint32_t>(replace.nameHead & 0xFFFFFFu);
    }
    // EID 自己的全局读数（`EID.GameRenderCount` / `EID.GameUpdateCount`）：探针直接读，
    // 用来回答"`EID:OnRender` / `EID:onGameUpdate` 到底进没进"。由 Mod 自己在回调第一句写入，
    // 比在我们这边加计数器更能反映真实情况（2026-09-12：描述没画出来，需要先确认 OnRender 是否在跑）。
    {
        double renderCount = 0.0;
        double updateCount = 0.0;
        if (LuaRuntime::ReadLuaGlobalNumber("EID.GameRenderCount", &renderCount) &&
            renderCount > 0.0) {
            inputs.eidCallbackWord |= 1U;
        }
        if (LuaRuntime::ReadLuaGlobalNumber("EID.GameUpdateCount", &updateCount) &&
            updateCount > 0.0) {
            inputs.eidCallbackWord |= 2U;
        }
    }
    // **一次把"描述为什么没画"的全部可疑点读齐**（负载 `[1..3]`）。用的是"表 + 字段"通路
    // （`ReadLuaTableNumber`），并把取不到的原因分成状态码报出来 —— 上一轮用
    // `ReadLuaGlobalNumber("EID.GameRenderCount")` 得到的"没在跑"与回调计数 900/878 矛盾，
    // 所以这一版换成能区分"表不在 / 字段是 nil / 字段不是 number"的读法。
    {
        bool hidden = false;
        inputs.eidHiddenStatus =
            LuaRuntime::ReadLuaTableBoolean("EID", "isHidden", &hidden) ? 0U : 1U;
        inputs.eidHidden = hidden;
        // `EID.Config.HideInBattle`（二级字段）与 `EID.Config.RefreshRate`。
        bool hideInBattle = false;
        inputs.eidHideInBattle =
            LuaRuntime::ReadLuaNestedBoolean("EID", "Config", "HideInBattle", &hideInBattle)
                ? hideInBattle
                : false;
        double refreshRate = 0.0;
        double hideInBattleNumber = 0.0;
        // `HideInBattle` 在配置里是布尔，这里用 number 读法拿状态码：0 = 读到（值忽略），
        // 2 = 字段是 nil。两种都记进状态字，便于报告侧分辨。
        inputs.eidHideInBattleStatus =
            LuaRuntime::ReadLuaNestedNumber("EID", "Config", "HideInBattle", &hideInBattleNumber);
        const std::uint32_t refreshStatus =
            LuaRuntime::ReadLuaNestedNumber("EID", "Config", "RefreshRate", &refreshRate);
        if (refreshStatus == 0 && refreshRate > 0.0 && refreshRate < 1000.0) {
            inputs.eidRefreshRate = static_cast<std::uint32_t>(refreshRate + 0.5);
        }
        // `EID.GameRenderCount`：上一轮用 `ReadLuaGlobalNumber` 读到"没有"，与回调计数矛盾，
        // 这一版走"表 + 字段"，并用状态码区分"EID 表不在"与"字段是 nil"。
        double renderCount = 0.0;
        const std::uint32_t renderStatus =
            LuaRuntime::ReadLuaTableNumber("EID", "GameRenderCount", &renderCount);
        inputs.eidRenderStatus = renderStatus;
        inputs.eidRenderCountSeen = renderStatus == 0 && renderCount > 0.0;
        inputs.eidCallbackWord = renderStatus;
    }
    inputs.lastEnemyCount = isaac::runtime::LastCountEnemiesForProbe();
    inputs.lastNumPlayers = isaac::runtime::LastNumPlayersForProbe();
    // `Game:IsPaused()` 的最后一次返回值（EID 用它配合 `HideInBattle` 决定是否隐藏描述）。
    inputs.lastPaused = isaac::runtime::LastIsPausedForProbe() != 0;
    // 现查一次引擎玩家链（与报错同一时刻，见 `[13..15]` 的注释）。
    {
        const isaac::runtime::EnginePlayerArray players =
            isaac::runtime::ResolveEnginePlayerArray();
        std::uint32_t flags = 0;
        if (players.readable) {
            flags |= 1u << 0;
            if (players.count == 0) {
                flags |= 1u << 1;
            }
        }
        const isaac::runtime::EnginePlayerLookup probe =
            isaac::runtime::LastEnginePlayerLookupForProbe();
        inputs.playerChain = isaac::runtime::ReadEnginePlayerChainForProbe();
        const std::uintptr_t base = LuaRuntime::EngineModuleBase();
        if (probe.firstElement != 0) {
            flags |= 1u << 2;
            if (base != 0 && probe.second == base + kEntityPlayerVtableOffset) {
                flags |= 1u << 3;
            }
        }
        if (inputs.playerChain.gameSlot != 0) {
            flags |= 1u << 4;  // 槽读到了
        }
        if (inputs.playerChain.game != 0) {
            flags |= 1u << 5;  // `Game*` 拿到了
        }
        if (inputs.playerChain.beginReadable != 0) {
            flags |= 1u << 6;
        }
        if (inputs.playerChain.endReadable != 0) {
            flags |= 1u << 7;
        }
        inputs.playerLookup = probe;
        inputs.playerLookupFlags = flags;
        inputs.playerLookupStage = probe.stage;
    }
    LuaRuntime::RequireFailureSnapshot(&inputs.requireFailures);
    inputs.registryCount =
        static_cast<std::uint32_t>(LuaRuntime::ManagedCallbackRegistry().Count());
    const bool diagnosticsValid =
        IsaacModRuntime_GetHookDiagnostics(inputs.diagnostics, 16) == 0x3152484341415349ULL;

    std::uint64_t status = 0;
    if (callbackError || inputs.diagnostics[12] != 0) {
        status |= 1ULL << 0;  // 回调错误标志（两个来源互证）
    }
    if (inputs.errorLength != 0) {
        status |= 1ULL << 1;
    }
    if (inputs.errorHead != 0) {
        status |= 1ULL << 2;
    }
    if (inputs.diagnostics[3] != 0) {
        status |= 1ULL << 3;  // update 回调进入次数 > 0
    }
    if (inputs.diagnostics[4] != 0) {
        status |= 1ULL << 4;  // render 回调进入次数 > 0
    }
    if (inputs.registryCount != 0) {
        status |= 1ULL << 5;
    }
    const std::uint64_t kindsLow = static_cast<std::uint64_t>(inputs.diagnostics[5]) |
                                   (static_cast<std::uint64_t>(inputs.diagnostics[6]) << 32);
    const std::uint64_t kindsHigh = static_cast<std::uint64_t>(inputs.diagnostics[7]) |
                                    (static_cast<std::uint64_t>(inputs.diagnostics[8]) << 32);
    if ((kindsLow | kindsHigh) != 0) {
        status |= 1ULL << 6;
    }
    if (diagnosticsValid) {
        status |= 1ULL << 7;  // 16 字诊断出口握手成功
    }
    if (!callbackError) {
        // bit8 = **兜底口径**：报错是被调用次数顶出来的，不是"真的看到了回调错误"。
        // 读报告时这一位决定 bit0 该按"没有回调错误"解释（探针自己加的一位，见头文件）。
        status |= 1ULL << 8;
    }
    if (inputs.errorLength != 0) {
        // bit9 = **文本布局**：负载 [3..15] 是错误文本，不是普查字。报告侧据此选解码表。
        status |= 1ULL << 9;
    }
    {
        // bits 32..63：模块假堆已用 KiB（`LUA_ERRMEM` 那类问题的唯一量化依据）。
        const std::uint64_t usedKiB =
            static_cast<std::uint64_t>(LuaRuntime::HeapUsedBytes() / 1024u);
        status |= (usedKiB > 0xFFFFFFFFULL ? 0xFFFFFFFFULL : usedKiB) << 32;
    }
    if (inputs.sawErrorText) {
        // bit10 = **历史上**捕获到过 Lua 错误文本（**纯诊断**：它不再参与探针触发判定，
        // 见上面 `IsaacModRuntime_ProbeCallbackError` 里那段注释 —— 这个标志是粘性的，
        // 曾经导致"刚进主界面就被探针关掉游戏"）。
        status |= 1ULL << 10;
    }
    inputs.status = status;
    ReportCallbackErrorSnapshot(&inputs);
}

// 触发口径（与上一轮一致）：**每帧检查，只有 `players[0]` 非空才报错**，外加调用次数兜底，
// 每会话最多一次。负载与报错都在 `ReportItemConfigSnapshot` 里（见那里的注释）。
//
// 批次 2c（2026-09-13）：本探针**先给回调错误探针让路** —— 让路开关是头文件里的
// `kEnginePlayerProbeDeferUntilErrorProbe`（当前 `true`）。触发时机本身没动（仍是每帧判一次
// `players[0]`），只是在它前面加了一句"错误探针报过了吗"。上一轮的故障是：本探针一进房间就
// `svcBreak`，会话在 EID 的回调错误发生之前就结束，于是拿不到错误信息。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeEnginePlayers() {
    static std::atomic<std::uint32_t> taken{0};
    static std::uint32_t calls = 0;
    ++calls;
    if (taken.load(std::memory_order_acquire) != 0) {
        return;
    }

    // 让路：错误探针一旦报错就 `svcBreak` 结束会话，所以这个标志置位的瞬间本函数不会再被
    // 调用到；错误探针没报错时，本探针一直等 —— 两个探针不可能各崩一次。
    if (isaac::runtime::kEnginePlayerProbeDeferUntilErrorProbe &&
        g_callbackErrorProbeReported.load(std::memory_order_acquire) == 0) {
        return;
    }

    const auto module = FindTargetModule();
    if (!module.has_value()) {
        // 连模块都没找到：状态位留 0（`x3 == 0` 就是"没找到模块"），负载指针给 0。
        register std::uint64_t x0 __asm__("x0") = 2;
        register std::uint64_t x1 __asm__("x1") = isaac::runtime::kItemConfigProbeMagic;
        register std::uint64_t x2 __asm__("x2") = 0;
        register std::uint64_t x3 __asm__("x3") = 0;
        register std::uint64_t x4 __asm__("x4") = 0;
        __asm__ volatile("svc 0x7f"
                         :
                         : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4)
                         : "memory");
        __builtin_unreachable();
    }

    const std::uint64_t base = static_cast<std::uint64_t>(module->base);
    const bool havePlayer = ProbePlayer0IsAlive(base);
    if (!havePlayer && calls < isaac::runtime::kEnginePlayerProbeFallbackCalls) {
        return;  // 还没真正进局：留着一次性标志，下一帧再看
    }
    std::uint32_t expected = 0;
    if (!taken.compare_exchange_strong(expected, 1, std::memory_order_acq_rel)) {
        return;
    }
    ReportItemConfigSnapshot(base, calls, havePlayer ? 1U : 0U);
}

// --- 房间实体容器探针（批次 4 真机确认，`ISAACEL1`）--------------------------------
//
// 静态结论（`runtime_constants.hpp` 的「房间实体容器」段、`docs/问题与解决记录.md` 批次 4）：
//
//   slot = *(u64*)(base + 0xAAC698) → Game = *(u64*)slot → Room = *(u64*)(Game + 0x21550)
//   EL   = Room + 0x1950   ← **内嵌对象，加法**：既不是指针，也不是 `std::vector`
//
// 这一整套从来没有上过真机。本探针用**一次**报错同时给出：两级链与 `Room*`、容器指纹
// （三个固定容量）、三张表的 begin/count、首个实体的全部字段、以及一次自遍历
// （元素数 | vptr 命中数 | 分区直方图）。头文件里有完整的寄存器 / status / 负载字段表。
//
// 证据口径与 `ISAACIG2`/`ISAACERR` 完全相同（理由见 `ReportItemConfigSnapshot` 上面的长注释）：
// 寄存器只用 `x0`–`x4`，负载是报错函数里**唯一**的大局部变量 `volatile std::uint64_t payload[16]`，
// 必须落在崩溃报告 `Stack Dump` 窗口 `[SP, SP+0x100)` 内。所以"读首实体字段"和"自遍历"这两段
// **各自单独成函数**：它们的局部量进不了报错函数的帧，帧里只剩负载数组（`ISAACIG2` 的教训是
// 帧 304 字节时负载尾部就掉出窗口了）。
// 核对命令（build 之后）：
//   aarch64-none-elf-objdump -d runtime/<BUILD>/content_mount_point_probe.o
// 在 `ReportEntityListSnapshot` 里查 `sub sp, sp, #N`（帧 N 字节）与负载数组的起点偏移，
// 判据是 `offset + 16*8 <= 0x100`。
//
// 触发：每帧一次（挂在 `hook_manager.cpp` 更新中继的探针块**最后**）。主口径 =
// "两级链与容器指纹全部成立 + 活表 count > 0 + `players[0]` 非空"；兜底 = 已经在局内
// （`players[0]` 非空）但主口径始终不成立（理由见头文件 `kEntityListProbeFallbackCalls`）。
//
// ★ 与其它探针的先后关系：一次会话只允许一个探针真正报错（`svcBreak` 直接结束会话）。本探针
// 排在最后，并且在触发前显式检查既有的一次性标志（`g_callbackErrorProbeReported` 与
// `g_probeTaken`），它们一旦置位就让路。**本探针由此可能被更早的会话结束者抢在前头**
// （`ISAACERR` 的 600 次兜底、内容挂载点的 600 帧绘制口径、通用探针 break 的 1500 渲染帧），
// 见 `kEntityListProbeYieldsToOtherProbes` 与 `hook_manager.cpp` 调用点上的注释。

// 分区编号（`EntityList::collide()` 的 7 类；位序与 `EntityPartition` 掩码 bit0..bit6 一致）。
enum EntityListPartition : std::uint32_t {
    kPartitionFamiliar = 0,
    kPartitionBullet = 1,
    kPartitionTear = 2,
    kPartitionEnemy = 3,
    kPartitionPickup = 4,
    kPartitionPlayer = 5,
    kPartitionEffect = 6,
};
constexpr std::uint32_t kEntityListPartitionCount = 7;
// `Type == 3`（Familiar）里 `Variant == 0xEF` 的实体会被 `collide()` 归进 ENEMY。
constexpr std::uint32_t kEntityVariantEnemyFamiliar = 0xEF;

// `Type`/`Variant` → 分区。只做算术，不碰内存（调用方负责先把两个字段读出来）。
// 判据与顺序都照抄 `EntityList::collide()` @ `0x77690`：`Type 2=TEAR`、`3=FAMILIAR`
// （`Variant==0xEF` 归 ENEMY）、`8=KNIFE→TEAR`、`9=PROJECTILE→BULLET`、`5/6→PICKUP`、
// `IsEnemy=(Type-10)<0x3DE`、`4=BOMB→ENEMY`、`0x3E8=EFFECT`、`1=PLAYER`。
std::uint32_t ClassifyEntityPartition(std::uint32_t type, std::uint32_t variant) {
    if (type == kEntityTypePlayer) {  // 1
        return kPartitionPlayer;
    }
    if (type == 2 || type == 8) {
        return kPartitionTear;
    }
    if (type == 3) {
        return variant == kEntityVariantEnemyFamiliar ? kPartitionEnemy : kPartitionFamiliar;
    }
    if (type == 4) {
        return kPartitionEnemy;
    }
    if (type == kEntityTypePickup || type == 6) {  // 5 / 6
        return kPartitionPickup;
    }
    if (type == 9) {
        return kPartitionBullet;
    }
    if (type == kEntityTypeEffect) {  // 0x3E8
        return kPartitionEffect;
    }
    // `IsEnemy = (u32)(Type - 10) < 0x3DE`：Type 10..999（0x3E8 已在上面单独处理）。
    if (static_cast<std::uint32_t>(type - kEntityEnemyTypeBase) <
        kEntityEnemyTypeSpan) {
        return kPartitionEnemy;
    }
    return kEntityListPartitionCount;  // 不属于任何已知分区
}

// 读一个 u32 到**局部变量**（容器指纹与计数用）。与 `ReadProbeWordInto`（写进负载槽）成对。
bool ReadProbeWord32(std::uint64_t address, std::uint32_t* value) {
    if (value == nullptr || !IsReadableRange(address, sizeof(std::uint32_t))) {
        return false;
    }
    std::memcpy(value, reinterpret_cast<const void*>(address), sizeof(std::uint32_t));
    return true;
}

// 触发判定用：只走"廉价"路径（两级链、容器指纹、活表 count/begin 对齐），**不做遍历**——
// 逐帧遍历 0x800 个槽（每个都要 `svcQueryMemory`）会把探针自己的开销烧进游戏帧里。
// 返回 status（0 = 全部通过，见头文件的 status 表；这里只可能返回 1..6），活表 count 带出。
std::uint32_t EvaluateEntityListStatus(std::uint64_t base, std::uint64_t* liveCount) {
    if (liveCount != nullptr) {
        *liveCount = 0;
    }
    std::uint64_t slot = 0;
    if (!ReadProbeWord(base + kGameOwnerGlobalSlotOffset, &slot) || slot == 0) {
        return 1;
    }
    std::uint64_t game = 0;
    if (!ReadProbeWord(slot, &game) || game == 0) {
        return 2;
    }
    std::uint64_t room = 0;
    if (!ReadProbeWord(game + kGameRoomPointerOffset, &room) || room == 0) {
        return 3;
    }
    const std::uint64_t entityList = room + kRoomEntityListOffset;
    std::uint32_t generalCapacity = 0;
    std::uint32_t liveCapacity = 0;
    std::uint32_t arenaCapacity = 0;
    std::uint32_t count = 0;
    if (!ReadProbeWord32(entityList + kEntityListGeneralCapacityOffset, &generalCapacity) ||
        !ReadProbeWord32(entityList + kEntityListLiveCapacityOffset, &liveCapacity) ||
        !ReadProbeWord32(entityList + kEntityListArenaCapacityOffset, &arenaCapacity) ||
        !ReadProbeWord32(entityList + kEntityListLiveCountOffset, &count)) {
        return 4;  // 指纹字段本身不可读，按"指纹不成立"处理（status 表里没有第 9 种）
    }
    if (generalCapacity != kEntityListGeneralCapacity ||
        liveCapacity != kEntityListLiveCapacity || arenaCapacity != kEntityListArenaCapacity) {
        return 4;
    }
    if (liveCount != nullptr) {
        *liveCount = static_cast<std::uint64_t>(count);
    }
    if (count > liveCapacity) {
        return 5;
    }
    std::uint64_t liveBegin = 0;
    if (!ReadProbeWord(entityList + kEntityListLiveBeginOffset, &liveBegin) || liveBegin == 0 ||
        (liveBegin & 7) != 0) {
        return 6;
    }
    return 0;
}

// 读首实体（`payload[8]`）的字段到 `payload[9..13]`，并判定它的 vptr 是否落在 `Entity` 家族的
// vtable 区间 `[base+0xA34F58, base+0xA38230)` 内。返回 0 = 通过、7 = 首实体不可用
// （为空 / 不可读 / vptr 不在区间）。单独成函数是为了让报错函数的帧只有负载数组这一块大局部量。
// `noinline` 是**窗口要求**，不是优化偏好：这两个函数一旦被内联进 `ReportEntityListSnapshot`，
// 它们的内联体就会把报错函数的帧顶到 272 字节（实测），负载数组被放到 `sp+0x90`，尾部两个字
// 正好掉出 `[SP, SP+0x100)` 窗口（`payload[14]`/`[15]` 就是自遍历的读数）。
__attribute__((noinline)) std::uint32_t ReadFirstEntityFields(std::uint64_t base,
                                                             volatile std::uint64_t* payload) {
    const std::uint64_t entity = payload[8];
    // 一次可读性检查覆盖 `[entity, entity + 0x348)`（最远字段是 `Size` @ `+0x344`），
    // 之后的逐字段读取只是 `memcpy` —— `runtime_constants.hpp` 的 `kEntitySnapshotSpan` 就是这么定的。
    if (entity == 0 || !IsReadableRange(entity, kEntitySnapshotSpan)) {
        return 7;
    }
    const auto* raw = reinterpret_cast<const std::uint8_t*>(entity);
    std::uint64_t vptr = 0;
    std::uint64_t flags = 0;
    std::uint32_t type = 0;
    std::uint32_t variant = 0;
    std::uint32_t subType = 0;
    std::uint32_t spawnFrame = 0;
    std::uint32_t positionX = 0;
    std::uint32_t positionY = 0;
    std::uint32_t size = 0;
    std::uint32_t index = 0;
    std::memcpy(&vptr, raw, sizeof(std::uint64_t));
    std::memcpy(&type, raw + kEntityTypeOffset, sizeof(std::uint32_t));
    std::memcpy(&variant, raw + kEntityVariantOffset, sizeof(std::uint32_t));
    std::memcpy(&subType, raw + kEntitySubTypeOffset, sizeof(std::uint32_t));
    std::memcpy(&spawnFrame, raw + kEntitySpawnFrameOffset, sizeof(std::uint32_t));
    std::memcpy(&positionX, raw + kEntityPositionOffset, sizeof(std::uint32_t));
    std::memcpy(&positionY, raw + kEntityPositionOffset + sizeof(std::uint32_t),
                sizeof(std::uint32_t));
    std::memcpy(&size, raw + kEntitySizeOffset, sizeof(std::uint32_t));
    std::memcpy(&index, raw + kEntityIndexOffset, sizeof(std::uint32_t));
    std::memcpy(&flags, raw + kEntityFlagsOffset, sizeof(std::uint64_t));
    payload[9] = static_cast<std::uint64_t>(type) |
                 (static_cast<std::uint64_t>(variant) << 8) |
                 (static_cast<std::uint64_t>(subType) << 16) |
                 (static_cast<std::uint64_t>(spawnFrame) << 32);
    payload[10] = vptr;
    payload[11] = static_cast<std::uint64_t>(positionX) |
                  (static_cast<std::uint64_t>(positionY) << 32);
    payload[12] = static_cast<std::uint64_t>(size) | (static_cast<std::uint64_t>(index) << 32);
    payload[13] = flags;
    if (vptr < base + kEntityVtableRangeBeginOffset || vptr >= base + kEntityVtableRangeEndOffset) {
        return 7;
    }
    return 0;
}

// 自遍历活表：写 `payload[14]` = `validVptrCount << 32 | liveCount`、`payload[15]` = 分区直方图位。
// 返回 0 = 每个元素都非空且 8 字节对齐、8 = 见过空/未对齐元素（头文件 status 表里的第 8 种；
// 槽位地址或元素地址**不可读**不算第 8 种，只体现在 `validVptrCount < liveCount` 上）。
// 单独成函数（并且 `noinline`，理由见上面 `ReadFirstEntityFields`）：循环变量与临时量都不进
// 报错函数的帧，帧里只剩负载数组。
__attribute__((noinline)) std::uint32_t TraverseEntityLiveTable(
    std::uint64_t base, volatile std::uint64_t* payload) {
    const std::uint64_t liveBegin = payload[7];
    const std::uint32_t liveCount = static_cast<std::uint32_t>(payload[5] & 0xFFFFFFFFULL);
    std::uint32_t validVptrCount = 0;
    std::uint32_t histogram = 0;
    bool sawBadElement = false;
    if (liveBegin == 0 || liveCount == 0) {
        payload[14] = 0;
        payload[15] = 0;
        return 0;
    }
    for (std::uint32_t index = 0; index < liveCount; ++index) {
        const std::uint64_t slotAddress =
            liveBegin + static_cast<std::uint64_t>(index) * sizeof(std::uint64_t);
        std::uint64_t entity = 0;
        if (!IsReadableRange(slotAddress, sizeof(std::uint64_t))) {
            continue;  // 表尾落到未映射页：不算"空元素"，只让 vptr 命中数少一个
        }
        std::memcpy(&entity, reinterpret_cast<const void*>(slotAddress), sizeof(std::uint64_t));
        if (entity == 0 || (entity & 7) != 0) {
            sawBadElement = true;
            continue;
        }
        // 一次检查覆盖实体前 0x348 字节：vptr（+0x00）与分类要的 `Type`/`Variant`（+0x38/+0x3C）
        // 都在里面，于是每个元素只需一次 `svcQueryMemory`。
        if (!IsReadableRange(entity, kEntitySnapshotSpan)) {
            continue;
        }
        const auto* raw = reinterpret_cast<const std::uint8_t*>(entity);
        std::uint64_t vptr = 0;
        std::memcpy(&vptr, raw, sizeof(std::uint64_t));
        if (vptr >= base + kEntityVtableRangeBeginOffset &&
            vptr < base + kEntityVtableRangeEndOffset) {
            ++validVptrCount;
        }
        std::uint32_t type = 0;
        std::uint32_t variant = 0;
        std::memcpy(&type, raw + kEntityTypeOffset, sizeof(std::uint32_t));
        std::memcpy(&variant, raw + kEntityVariantOffset, sizeof(std::uint32_t));
        const std::uint32_t partition = ClassifyEntityPartition(type, variant);
        if (partition < kEntityListPartitionCount) {
            histogram |= 1U << partition;
        }
    }
    payload[14] = (static_cast<std::uint64_t>(validVptrCount) << 32) |
                  static_cast<std::uint64_t>(liveCount);
    payload[15] = histogram;
    return sawBadElement ? 8 : 0;
}

// 唯一的报错点（`ISAACEL1`）。`payload[16]`（128 字节）是这个函数里**唯一**的大局部变量，
// 其余读数都在 `ReadFirstEntityFields` / `TraverseEntityLiveTable` 里做 —— 帧必须 ≤ 256 字节，
// 数组才落在 `[SP, SP+0x100)` 内。函数是 `noreturn`：报错即结束会话，编译器不必为"返回后"留状态。
[[noreturn]] __attribute__((noinline)) void ReportEntityListSnapshot(std::uint64_t base) {
    volatile std::uint64_t payload[16] = {0};
    std::uint32_t status = 0;

    // --- [0..3] 两级链：槽 → `Game*` → `Room*` → 内嵌 `EntityList` -----------------
    if (!ReadProbeWordInto(base + kGameOwnerGlobalSlotOffset, &payload[0]) || payload[0] == 0) {
        status = 1;
    } else if (!ReadProbeWordInto(payload[0], &payload[1]) || payload[1] == 0) {
        status = 2;
    } else if (!ReadProbeWordInto(payload[1] + kGameRoomPointerOffset, &payload[2]) ||
               payload[2] == 0) {
        status = 3;
    } else {
        // ★ 这里是**加法**：`EntityList` 内嵌在 `Room` 里，不存在指向它的指针。
        payload[3] = payload[2] + kRoomEntityListOffset;
    }

    if (status == 0) {
        // --- [4..7] 容器指纹 + 一般表/活表的 count/begin ---------------------------
        const std::uint64_t entityList = payload[3];
        std::uint32_t generalCapacity = 0;
        std::uint32_t liveCapacity = 0;
        std::uint32_t arenaCapacity = 0;
        std::uint32_t generalCount = 0;
        std::uint32_t liveCount32 = 0;
        const bool fingerprinted =
            ReadProbeWord32(entityList + kEntityListGeneralCapacityOffset, &generalCapacity) &&
            ReadProbeWord32(entityList + kEntityListLiveCapacityOffset, &liveCapacity) &&
            ReadProbeWord32(entityList + kEntityListArenaCapacityOffset, &arenaCapacity) &&
            ReadProbeWord32(entityList + kEntityListGeneralCountOffset, &generalCount) &&
            ReadProbeWord32(entityList + kEntityListLiveCountOffset, &liveCount32);
        payload[4] = static_cast<std::uint64_t>(generalCount) |
                     (static_cast<std::uint64_t>(generalCapacity) << 32);
        payload[5] = static_cast<std::uint64_t>(liveCount32) |
                     (static_cast<std::uint64_t>(liveCapacity) << 32);
        if (!fingerprinted || generalCapacity != kEntityListGeneralCapacity ||
            liveCapacity != kEntityListLiveCapacity ||
            arenaCapacity != kEntityListArenaCapacity) {
            status = 4;
        } else if (liveCount32 > liveCapacity) {
            status = 5;
        } else if (!ReadProbeWordInto(entityList + kEntityListGeneralBeginOffset, &payload[6]) ||
                   !ReadProbeWordInto(entityList + kEntityListLiveBeginOffset, &payload[7]) ||
                   payload[7] == 0 || (payload[7] & 7) != 0) {
            status = 6;
        }
    }

    if (status == 0) {
        // --- [8..13] 首个实体与它的字段 -------------------------------------------
        // 活表 count 为 0 时**不读** `payload[7]` 指向的槽：表空时那个槽可能是上一轮的残留，
        // 读出来的"首实体"没有意义（触发口径也要求 count > 0）。只判 `payload[5]` 的**低 32 位**
        // ——高 32 位是恒定非零的容量（0x800），整字判断会让这里永远成立。
        if ((payload[5] & 0xFFFFFFFFULL) != 0) {
            if (ReadProbeWordInto(payload[7], &payload[8])) {
                status = ReadFirstEntityFields(base, payload);
            } else {
                status = 7;
            }
        }
    }

    if (status == 0 || status == 7) {
        // --- [14][15] 自遍历 ------------------------------------------------------
        // status == 7 时也照走：`payload[14]`/`[15]` 能把"首个元素怪"与"整张表怪"分开。
        const std::uint32_t traversalStatus = TraverseEntityLiveTable(base, payload);
        if (status == 0 && traversalStatus != 0) {
            status = traversalStatus;
        }
    }

    register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
    register std::uint64_t x1 __asm__("x1") = isaac::runtime::kEntityListProbeMagic;
    register std::uint64_t x2 __asm__("x2") =
        reinterpret_cast<std::uint64_t>(const_cast<std::uint64_t*>(payload));
    register std::uint64_t x3 __asm__("x3") =
        (static_cast<std::uint64_t>(isaac::runtime::kEntityListProbePayloadWords) << 32) |
        static_cast<std::uint64_t>(status);
    register std::uint64_t x4 __asm__("x4") = payload[3];
    __asm__ volatile("svc 0x7f"
                     :
                     : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4)
                     : "memory");
    __builtin_unreachable();
}

// 本探针自己的调用次数（每帧一次，只在游戏线程的 update 路径递增）。
std::uint32_t g_entityListProbeCalls = 0;

// 每帧调用一次（update 路径，见 `hook_manager.cpp` 的 `ManagerUpdateHook`；本探针排在既有探针
// 之后，是那个块里的最后一个）。
// "一次会话最多崩一次"由两件事共同保证：`reported` 的 CAS（同一个进程里只可能有一个执行流拿到
// 0→1），以及报错本身就是 `svcBreak` —— 报错即结束会话。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeEntityList() {
    // 本轮开关：容器探针要问的问题已经在真机报告 `01789200335` 里以 `status = 0` 全部答完，
    // 而它会在"进房间的第一帧"就结束会话，把 `ISAACERR`（EID 的 Lua 回调错误）挤掉。关掉它，
    // 让本轮只剩回调错误这一条判据。理由与复跑方法见头文件 `kEntityListProbeArmed`。
    if (!isaac::runtime::kEntityListProbeArmed) {
        return;
    }
    // 一次性标志。用 `std::atomic<std::uint32_t>` 而不是 bool 原子（仓库门禁
    // `test_runtime_source_contains_no_bool_atomics` 禁止模块源码里出现 bool 原子）。
    static std::atomic<std::uint32_t> reported{0};
    if (reported.load(std::memory_order_acquire) != 0) {
        return;
    }
    ++g_entityListProbeCalls;

    // 让路：既有探针的一次性标志一旦置位就不再触发（理由见头文件
    // `kEntityListProbeYieldsToOtherProbes`）。
    if (isaac::runtime::kEntityListProbeYieldsToOtherProbes &&
        (g_callbackErrorProbeReported.load(std::memory_order_acquire) != 0 ||
         g_probeTaken != 0)) {
        return;
    }

    const auto module = FindTargetModule();
    if (!module.has_value()) {
        return;  // 连模块都没找到：没有 base 就没有任何可读的判据，下一帧再看
    }
    const std::uint64_t base = static_cast<std::uint64_t>(module->base);

    std::uint64_t liveCount = 0;
    const std::uint32_t status = EvaluateEntityListStatus(base, &liveCount);
    // `players[0]` 的取法与 `IsaacModRuntime_ProbeEnginePlayers` 完全相同（批次 2 已真机确认）。
    const bool havePlayer = ProbePlayer0IsAlive(base);
    const bool primaryReady = status == 0 && liveCount > 0 && havePlayer;
    // 兜底：已经在局内（`players[0]` 非空）却始终不满足主口径 —— 说明容器判据不成立，
    // 这时也必须有一次报告，否则会话只剩"没有报错也没有数据"。
    const bool fallbackReady =
        !primaryReady && havePlayer &&
        g_entityListProbeCalls >= isaac::runtime::kEntityListProbeFallbackCalls;
    if (!primaryReady && !fallbackReady) {
        return;  // 还没进局（或还没到时候）：留着一次性标志，下一帧再看
    }
    std::uint32_t expected = 0;
    if (!reported.compare_exchange_strong(expected, 1, std::memory_order_acq_rel)) {
        return;
    }
    ReportEntityListSnapshot(base);
}

}  // namespace


#endif  // EXL_PROBE_BREAK
