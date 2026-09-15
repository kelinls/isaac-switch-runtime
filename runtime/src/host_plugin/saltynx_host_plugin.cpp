#include "host_plugin/saltynx_host_plugin.hpp"

// For `svcSleepThread`, which the frozen Stage145 plugin already used on this same
// thread. libnx is linked into the plugin either way; only `SaltySDCore_FindSymbol`
// stays undefined, and the build gate enforces that.
#include <switch.h>

#include <cstddef>

namespace isaac::runtime::host_plugin {
namespace {

constexpr char kOpenSymbol[] = "SaltySDCore_fopen";
constexpr char kReadSymbol[] = "SaltySDCore_fread";
constexpr char kWriteSymbol[] = "SaltySDCore_fwrite";
constexpr char kCloseSymbol[] = "SaltySDCore_fclose";
constexpr char kRegisterSymbol[] = "IsaacModRuntime_RegisterSaltyFileApi";
constexpr char kObserverStateSymbol[] = "IsaacModRuntime_GetObserverState";
constexpr char kTestRunSnapshotSymbol[] = "IsaacModRuntime_GetTestRunSnapshot";

// The acknowledgement the Runtime returns for an accepted file table. Kept equal
// to the module-side constant so a mismatch is detectable from either end.
constexpr std::uint64_t kFileApiRegistered = 0x49534141435F4631ULL;

// Snapshot contract shared with the Runtime. Only the fields the plugin needs are
// read, and the magic is checked before anything is trusted.
constexpr std::uint64_t kTestRunSnapshotMagic = 0x3152544341415349ULL;  // "ISAACTR1"
constexpr std::size_t kTestRunSnapshotWords = 48;
constexpr std::uint64_t kObserverStateExported = 0x49534141435F4F31ULL;
constexpr std::size_t kObserverStateWordCount = 6;

using GetObserverStateFn = std::uint64_t (*)(std::uint32_t*, std::size_t);
using GetTestRunSnapshotFn = std::uint64_t (*)(void*, std::size_t);

using FileOpenFn = void* (*)(const char*, const char*);
using FileWriteFn = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using FileCloseFn = int (*)(void*);

constexpr char kBridgePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-bridge.bin";
constexpr char kBridgeMagic[] = "ISAACBR2";  // appended by the Task 6b plugin

// A brand-new path, used only as a capability probe. The diagnostics journal writes
// `isaac-runtime-events.bin`, which does not exist yet, and no artifact so far has
// shown whether this channel can *create* a file as opposed to appending to one that
// already exists. This probe answers that on its own: it is an independent third
// write, so its presence or absence cannot be confused with the other two records.
constexpr char kCreateProbePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-create-probe.bin";
constexpr char kCreateProbeMagic[] = "ISAACNF1";

// The plugin's half of the "who can write a file" measurement.
//
// The Runtime now holds the file table in the copy that runs (`fileApiMask` 0xf measured in
// that copy's own registers) and still cannot produce `isaac-runtime-events.bin`, and its
// own `fs` path fails too. So the question is which thread may perform file I/O here. The
// Runtime's half of the answer travels in the probe break's registers; this half is a file
// this thread creates, plus a record of what each step returned, so the two can be compared
// from one session.
constexpr char kThreadWriteProbePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-write-plugin-thread.bin";
// The same measurement, from the thread this plugin creates for the hand-over. Which thread
// may perform file I/O decides how the journal can ever be written: the Runtime lives on the
// game's threads, and the game's thread provably cannot open a file through this table
// (`openOk = 0` after three attempts, recorded in the probe break's `x21`). If a thread the
// plugin creates can, the journal has a writer; if it cannot, only SaltyNX's own thread can
// and the transport has to change.
constexpr char kHandoverWriteProbePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-write-handover-thread.bin";
// 交接线程自己的取样通道，**与加载线程的 `kBridgePath` 分开**。
//
// 实测（2026-09-14 00:30 那两次会话）：两个线程同时往 `bridge.bin` 追加时记录会**互相覆盖**——
// 两边都以 `"ab"` 打开，写位置都取自"打开那一刻的文件尾"，后写的把先写的盖掉。证据是每次
// 会话的条数都不一样（752 / 720 / 624 字节），而记录流本身干净（无乱码、无残缺、无尾部残留）：
// 加载线程的 `ISAACHS1`/`ISAACSN1` 会丢，交接线程的取样也会丢。一条通道只允许一个写入者。
constexpr char kHandoverSamplePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-handover-samples.bin";
constexpr char kThreadWriteProbeText[] = "ISAACPT1 plugin-thread file write probe\n";
constexpr char kHandoverWriteProbeText[] = "ISAACHT1 handover-thread file write probe\n";
constexpr char kThreadWriteRecordMagic[] = "ISAACIO1";
constexpr std::size_t kThreadWriteRecordSize = 96;

struct ThreadWriteResult {
    std::uint32_t attempts = 0;
    std::uint32_t openOk = 0;
    std::uint32_t written = 0;
    std::uint32_t closeOk = 0;
    std::uintptr_t openFn = 0;
    std::uintptr_t writeFn = 0;
    std::uintptr_t closeFn = 0;
};

// The Runtime's `TestRunSnapshot`, copied verbatim. `reserved` (byte offset 40 inside
// the snapshot) carries `TestRunDiagnosticsAttach`, which is the only value that says
// how far the Runtime's diagnostics attach actually got:
//   0 NotAttempted  1 Attached  2 NoPort  3 OpenFailed  4 SessionMissing
// Without it, "the journal never opened its file" cannot be told apart from "the
// attach path never ran", which is exactly the ambiguity this record removes.
constexpr char kSnapshotMagic[] = "ISAACSN1";
constexpr std::size_t kSnapshotRecordSize = 80;

// The Runtime's hook diagnostics. A production module reports nothing about whether
// the Manager update hook was installed or whether its callback is ever entered, so
// these words are the only way to tell "the diagnostics attach is never reached"
// apart from "the update hook never fires".
constexpr char kHookDiagnosticsSymbol[] = "IsaacModRuntime_GetHookDiagnostics";
constexpr std::uint64_t kHookDiagnosticsMagic = 0x3152484341415349ULL;  // "ISAACHR1"
constexpr std::size_t kHookDiagnosticsWordCount = 5;
constexpr char kHookRecordMagic[] = "ISAACHD1";
constexpr std::size_t kHookRecordSize = 64;

// The Runtime's own view of the diagnostics pipeline: which of the four registered
// file pointers actually reached it, whether the session was constructed, how many
// times the table was registered, and whether the gate stopped early on an empty
// table. Every previous explanation of the missing `isaac-runtime-events.bin` had to
// assume these; now they are readable.
constexpr char kDiagnosticsStateSymbol[] = "IsaacModRuntime_GetDiagnosticsState";
constexpr std::uint64_t kDiagnosticsStateMagic = 0x3153444341415349ULL;  // "ISAACDS1"
constexpr std::size_t kDiagnosticsStateWordCount = 5;
constexpr char kDiagnosticsRecordMagic[] = "ISAACDS1";
constexpr std::size_t kDiagnosticsRecordSize = 48;

// 交接线程启动取证。此前"线程起没起来"只能靠"它没写出文件"间接推断，而唯一那把
// 指示它的标志位（`HandoverThreadStarted`）又写在所有记录之后、任何记录都读不到。
// 这条记录把创建/启动的返回码直接带出来，让"创建失败 / 启动失败 / 启动成功但线程
// 自己走不到写盘点"三种情形可以区分。
constexpr char kHandoverStartMagic[] = "ISAACHS1";
constexpr std::size_t kHandoverStartRecordSize = 48;

// 交接线程的取样记录。读取工具里**早就定义了** `ISAACWT1` 的布局（"等运行时入口 + 交接"），
// 插件侧却从未实现过它 —— 于是"线程走到了哪一步"在文件侧一直是空白：它写不出文件时，
// "没等到入口"与"写盘失败"两种情形留下的证据完全一样。
//
// 这条记录补上那个空白。布局与解码器既有的 `ISAACWT1` 分支逐字段对齐，所以读取工具不用改：
//   +20 polls / +24 registrations（本轮真的把表写进去的轮数）
//   +28 fileApiMask / +32 sessionCreated / +36 registrationCalls / +40 gateStoppedEarly
//       / +44 handshake   —— 全部来自运行时自己的 `GetDiagnosticsState`
//   +48 publishedExlMain（0 = 这次会话里始终没读到非零入口）
//   +56 tickSelfAppeared / +64 tickRegistered / +72 tickNow
//   +80 exlMainAttempts / +84 exlMainRecords / +88 serviceState —— 来自 `GetSelfJournalState`
constexpr char kHandoverSampleMagic[] = "ISAACWT1";
constexpr std::size_t kHandoverSampleRecordSize = 96;
// 同一次取样里还要按既有 `ISAACSN1` 格式补一条 `readIndex = 2` 的快照记录（"延迟后的
// 第二次读取"，布局与读取工具都已存在）。这是 M1 判据⑤ 的载体。
constexpr std::uint8_t kHandoverSnapshotReadIndex = 2;
// 驻停前的重查节奏。入口只是"出得晚"也是一种可能，所以第一次没等到时不立刻驻停：
// 每 30 秒重查一次，最多 40 次（约 20 分钟）才转成永久驻停。
constexpr std::int64_t kHandoverRecheckIntervalNanoseconds = 30'000'000'000LL;
constexpr std::uint32_t kHandoverRecheckCount = 40;

// Word [4] of that record is a handshake: the Runtime writes it in the same store that
// saves the four file pointers, and reports it through this separate call. 'HAND' means
// the registration provably reached the table the running code reads; 0 means the
// acknowledgement came back but the store never landed where the Runtime looks.
constexpr std::uint32_t kFileApiHandshake = 0x48414E44U;  // "HAND"

// Runtime identity, so the plugin can tell *which copy* of the module its symbol lookup
// returned.
//
// `exl_main` is the module's entry: the worker, the hooks and the Lua runtime all come
// from it, and it publishes its own address as its very first statement. The plugin reads
// that word through the ordinary lookup. A zero here, while a Mod demonstrably runs,
// means the lookup and the game are looking at two different copies of the module -- the
// only remaining explanation for a plugin that sees an empty file table in a Runtime that
// is busy dispatching callbacks.
constexpr char kRuntimeIdentitySymbol[] = "IsaacModRuntime_GetRuntimeIdentity";
constexpr std::uint64_t kRuntimeIdentityMagic = 0x3144494341415349ULL;  // "ISAACID1"

// The Runtime's own self-journal: how many times the copy the plugin talks to tried to
// append its own record through the `fs` service, how many landed, and the Result code
// of each step. Those records arrive on the SD card through a path that does not involve
// this plugin at all, so the two channels together are what makes the copy question
// answerable: identical attempt counts mean one copy, zero attempts in this channel while
// the journal file grows mean two.
constexpr char kSelfJournalStateSymbol[] = "IsaacModRuntime_GetSelfJournalState";
constexpr std::uint64_t kSelfJournalStateMagic = 0x31534A4341415349ULL;  // "ISAACJS1"
constexpr std::size_t kSelfJournalStateWordCount = 16;

// The address of one entry of the Runtime's registered file table. The plugin cannot
// see that static array, so it asks the copy it already reaches and applies the
// difference to the copy that actually runs: same file, same layout, so the same
// offset holds the same slot.
constexpr char kFileApiSlotSymbol[] = "IsaacModRuntime_GetFileApiSlot";
// Where the hand-over thread leaves its findings, since it cannot write anything itself.
constexpr char kPluginNoteSymbol[] = "IsaacModRuntime_SetPluginNote";
// Where the Runtime gets the loader's symbol lookup, so it can resolve the game's own file
// functions by name instead of using the wrappers measured not to work on the game's threads.
constexpr char kSetSymbolLookupSymbol[] = "IsaacModRuntime_SetSymbolLookup";
using SetSymbolLookupFn = void (*)(std::uintptr_t);
using GetFileApiSlotFn = std::uintptr_t (*)(std::uint32_t);

// The scan for every copy of the Runtime module in this process.
//
// The plugin's symbol lookup reaches a copy whose `exl_main` never ran -- measured, not
// assumed: that copy reports `fileApiMask` 0xf with `sessionCreated` 0,
// `gateStoppedEarly` 0, zero callback entries and zero self-journal attempts, while the
// game demonstrably dispatches callbacks. Registering the file table there can therefore
// never produce a record. This scan finds the copies by their bytes instead of by the
// lookup, so the table can be handed to the one that runs.
constexpr char kModuleCopyRecordMagic[] = "ISAACSG1";
constexpr std::size_t kModuleCopyRecordSize = 224;
constexpr std::size_t kMaxModuleCopies = 6;
// Only regions that could hold a module image are scanned: below this size they cannot
// hold one, above it they are heaps and stacks that would cost seconds to sweep.
constexpr std::uint64_t kMinScannedRegionSize = 0x10000;
constexpr std::uint64_t kMaxScannedRegionSize = 0x800000;
// A candidate is only ever called into after this many bytes of its code match the copy
// the plugin already holds, so the probe cannot branch into an unrelated module.
constexpr std::size_t kImageVerifyBytes = 64;

constexpr std::uintptr_t kAddressSpaceLimit = 0x8000000000ULL;
// A second copy of the same file maps the same number of pages, so candidates are limited
// to regions whose size is within a page or two of the region that holds the plugin's own
// identity symbol. That keeps the probe from branching into `Repentance.nrs` or `nnSdk`.
constexpr std::uint64_t kRegionSizeSlack = 0x2000;
constexpr std::size_t kMaxRegions = 6;

constexpr char kMappingRecordMagic[] = "ISAACMM1";
constexpr std::size_t kMappingRecordSize = 176;
constexpr char kSelfJournalRecordMagic[] = "ISAACJS1";
constexpr std::size_t kSelfJournalRecordSize = 88;

using GetRuntimeIdentityFn = std::uint64_t (*)(std::uint32_t*, std::size_t);
using SetPluginNoteFn = std::uint64_t (*)(const std::uint32_t*, std::size_t);
using RegisterFileApiRawFn = std::uint64_t (*)(std::uintptr_t, std::uintptr_t,
                                               std::uintptr_t, std::uintptr_t);

struct MappingHit {
    std::uintptr_t base;
    std::uint64_t size;
};

// One copy of the Runtime module found by the byte scan.
struct ModuleCopy {
    std::uintptr_t base;      // image base: where the crt0 signature sits at offset 8
    std::uintptr_t identity;  // its `IsaacModRuntime_GetRuntimeIdentity`
    std::uint64_t size;       // size of the region that holds it
    std::uint32_t type;       // the region's reported type
    std::uint32_t perm;       // the region's reported permissions
    std::uintptr_t self;      // what this copy published from `exl_main` (0 = never ran)
};

// Where a scan stopped, for the record.
struct ModuleCopyScan {
    std::uint64_t regionsVisited = 0;
    std::uint64_t regionsScanned = 0;
    std::size_t count = 0;
};

struct MappingScanStats {
    std::uint64_t regionsVisited;
    std::uint64_t moduleCodeRegions;
    std::uint64_t signatureHits;
};

// Regions that can hold module code, of any reported type.
//
// The previous version filtered on `MemType_ModuleCodeStatic`, which is what `nnrtld`
// reports for a module it mapped itself. It found six such regions and the plugin's own
// identity symbol was in none of them -- so the module's code pages carry a different type,
// almost certainly because installing inline hooks remaps them through
// `svcControlCodeMemory`, which reports `CodeReadOnly`/`CodeWritable`. The scan therefore
// stops guessing at types: it reports the type it actually sees.
struct RegionInfo {
    std::uintptr_t base;
    std::uint64_t size;
    std::uint32_t type;
    std::uint32_t perm;
    bool found;
};

RegionInfo FindRegionContaining(std::uintptr_t address, std::uint64_t* visited) noexcept {
    RegionInfo info{};
    std::uintptr_t cursor = 0;
    while (cursor < kAddressSpaceLimit) {
        MemoryInfo memory{};
        u32 pageInfo = 0;
        if (R_FAILED(svcQueryMemory(&memory, &pageInfo, cursor))) {
            break;
        }
        const std::uintptr_t next = memory.addr + memory.size;
        ++(*visited);
        if (address >= memory.addr && address < next) {
            info.base = memory.addr;
            info.size = memory.size;
            info.type = memory.type;
            info.perm = memory.perm;
            info.found = true;
            return info;
        }
        if (next <= cursor) {
            break;
        }
        cursor = next;
    }
    return info;
}

// Readable regions whose size is close to the given one: a second copy of the same file
// maps the same number of pages. The caller only ever probes these, so the scan can never
// branch into an unrelated module.
std::size_t FindSameSizeRegions(std::uint64_t size, MappingHit* out, std::size_t capacity,
                                std::uint64_t* visited) noexcept {
    std::size_t found = 0;
    std::uintptr_t cursor = 0;
    while (cursor < kAddressSpaceLimit) {
        MemoryInfo memory{};
        u32 pageInfo = 0;
        if (R_FAILED(svcQueryMemory(&memory, &pageInfo, cursor))) {
            break;
        }
        const std::uintptr_t next = memory.addr + memory.size;
        ++(*visited);
        if ((memory.perm & Perm_R) != 0 && memory.size >= 0x1000) {
            const std::uint64_t delta = memory.size > size ? memory.size - size : size - memory.size;
            if (delta <= kRegionSizeSlack && found < capacity) {
                out[found].base = memory.addr;
                out[found].size = memory.size;
                ++found;
            }
        }
        if (next <= cursor) {
            break;
        }
        cursor = next;
    }
    return found;
}

// Finds every mapped copy of the Runtime module by content.
//
// 2026-09-11: the first version of this scan looked for the `~~exlaunch uwu~~` crt0 string
// at image offset 8 and found nothing, while the same session's crash report listed the
// module in the loader's module list at the very address the plugin had measured. The
// crt0 string is simply not at that offset in the loaded image, so the needle is now the
// module's own code: 32 bytes read from inside `IsaacModRuntime_GetRuntimeIdentity` in
// the copy the plugin already holds. A hit is that same function in another mapping (or
// in the same one), an image base follows from the identity symbol's known offset, and a
// candidate is only ever called into after 64 of its bytes matched this module.
//
// Asking a candidate for the address `exl_main` published is then meaningful: the copy
// that ran reports one, a copy that was merely mapped reports zero.
//
// The scan is bounded by region size, so it walks code and data images but never sweeps a
// heap or a stack, and it stops at `capacity` copies.
std::size_t FindModuleCopies(ModuleCopy* out, std::size_t capacity, std::size_t* scanned,
                             std::uintptr_t ownBase, std::uintptr_t ownIdentity,
                             GetRuntimeIdentityFn identityFn, ModuleCopyScan* stats) noexcept {
    if (out == nullptr || identityFn == nullptr || ownIdentity == 0) {
        return 0;
    }
    const std::uintptr_t identityOffset = ownIdentity - ownBase;
    const auto* ownCode = reinterpret_cast<const std::uint8_t*>(ownIdentity);
    // The needle starts past the prologue: a function's first instructions are boilerplate
    // that appears all over a module, the ones after it carry this function's own constants.
    constexpr std::size_t kNeedleOffset = 16;
    constexpr std::size_t kNeedleSize = 32;
    const std::uint8_t* needle = ownCode + kNeedleOffset;
    std::size_t found = 0;
    std::uintptr_t cursor = 0;
    while (cursor < kAddressSpaceLimit) {
        MemoryInfo memory{};
        u32 pageInfo = 0;
        if (R_FAILED(svcQueryMemory(&memory, &pageInfo, cursor))) {
            break;
        }
        const std::uintptr_t next = memory.addr + memory.size;
        ++stats->regionsVisited;
        const bool scannable = (memory.perm & Perm_R) != 0 &&
                               memory.size >= kMinScannedRegionSize &&
                               memory.size <= kMaxScannedRegionSize;
        if (scannable && found < capacity) {
            ++stats->regionsScanned;
            const auto* bytes = reinterpret_cast<const std::uint8_t*>(memory.addr);
            if (memory.size > kNeedleSize) {
                for (std::uint64_t offset = 0; offset + kNeedleSize <= memory.size; ++offset) {
                    if (bytes[offset] != needle[0]) {
                        continue;
                    }
                    bool match = true;
                    for (std::size_t index = 1; index < kNeedleSize; ++index) {
                        if (bytes[offset + index] != needle[index]) {
                            match = false;
                            break;
                        }
                    }
                    if (!match) {
                        continue;
                    }
                    // The needle sits `kNeedleOffset` into the identity export, so the
                    // candidate's identity address is the hit minus that offset.
                    const std::uintptr_t candidateIdentity = memory.addr + offset - kNeedleOffset;
                    if (candidateIdentity < identityOffset + kImageVerifyBytes) {
                        continue;
                    }
                    const std::uintptr_t candidate = candidateIdentity - identityOffset;
                    if (candidate + identityOffset + kImageVerifyBytes >
                        memory.addr + memory.size) {
                        continue;
                    }
                    const auto* candidateCode =
                        reinterpret_cast<const std::uint8_t*>(candidate + identityOffset);
                    bool codeMatches = true;
                    for (std::size_t index = 0; index < kImageVerifyBytes; ++index) {
                        if (candidateCode[index] != ownCode[index]) {
                            codeMatches = false;
                            break;
                        }
                    }
                    if (!codeMatches) {
                        continue;
                    }
                    ModuleCopy copy{};
                    copy.base = candidate;
                    copy.identity = candidateIdentity;
                    copy.size = memory.size;
                    copy.type = memory.type;
                    copy.perm = memory.perm;
                    std::uint32_t words[4]{};
                    const auto candidateExport =
                        reinterpret_cast<GetRuntimeIdentityFn>(copy.identity);
                    if (candidateExport(words, 4) == kRuntimeIdentityMagic) {
                        copy.self = static_cast<std::uintptr_t>(words[0]) |
                                    (static_cast<std::uintptr_t>(words[1]) << 32);
                    }
                    out[found] = copy;
                    ++found;
                    if (found >= capacity) {
                        break;
                    }
                }
            }
        }
        if (next <= cursor) {
            break;
        }
        cursor = next;
    }
    stats->count = found;
    if (scanned != nullptr) {
        *scanned = stats->regionsScanned;
    }
    return found;
}

// Sampling schedule. The first delay is the one the frozen Stage145 plugin already used
// on this thread, so it is known safe; the second is what actually catches the game
// running, because this plugin loads before the game's first Manager update (a
// three-second sample observed zero callback entries even in a session that reached the
// game's main loop). Both are kept so that a session which dies early still leaves the
// first sample behind.
constexpr std::int64_t kFirstSampleDelayNanoseconds = 3'000'000'000LL;
constexpr std::int64_t kSecondSampleDelayNanoseconds = 7'000'000'000LL;

// Waiting for the Runtime's entry, then handing over the file table.
//
// 2026-09-11: the registration used to happen once, at the top of this run. The session
// that crashed on purpose (report 01789117484) showed why that can never work: the plugin
// reads the table it just registered (`fileApiMask` 0xf, one registration) at three and ten
// seconds, and the Runtime reads an empty table twenty-odd seconds later in the very same
// image, while its `exl_main` address goes from zero to non-zero. The module image is
// mapped when the process starts but its entry runs much later -- the game reaches it
// through `nn::init::Start` during its own startup -- and `__module_start` clears `.bss`
// before anything else. So an early registration is wiped by the Runtime's own startup.
//
// The plugin therefore polls until the Runtime reports its entry, and only then registers.
// The polling interval is short enough that the journal's own retry (every update frame)
// picks the table up immediately, and the deadline only bounds a session in which the
// Runtime never starts at all.
constexpr std::int64_t kEntryPollIntervalNanoseconds = 250'000'000LL;
constexpr std::int64_t kEntryWaitDeadlineNanoseconds = 240'000'000'000LL;
// 第一次"等入口"的窗口，与上面那个驻停时长分开：它只决定**多久之后就该把
// "这个副本看不到入口"当成一条结论写下来**，不决定线程还会不会继续等（后面那段重查循环
// 最多再等 20 分钟）。240 秒意味着任何短于 4 分钟的会话都拿不到任何读数 —— 而玩家的会话
// 常态就是一两分钟，所以这个窗口曾经把一盘证据整体挡在门外。
constexpr std::int64_t kEntryFirstWaitNanoseconds = 30'000'000'000LL;
// 等待循环里的进度取样。**v10 起刻意关闭**（`kHandoverProgressEarlyPolls = 0`、
// `kHandoverProgressEveryPolls` 远大于窗口内可能的轮数）：循环里唯一的写盘动作本身就是
// 嫌疑 —— 前几版线程都在写了几条之后停住。这一轮让循环几乎不写盘，看 30 秒窗口走完后那条
// "没等到入口"的记录能不能出现（`polls` 约 120）。能出现 ⇒ 循环能撑 30 秒、写盘是累积性的
// 杀手；不出现 ⇒ 停点与写盘无关，转探针崩溃通道。这一对常量保留着，便于下一轮重新打开。
constexpr std::uint32_t kHandoverProgressEarlyPolls = 0;
constexpr std::uint32_t kHandoverProgressEveryPolls = 1000;
constexpr std::uint32_t kHandoverProgressSampleLimit = 10;
constexpr std::int64_t kPostRegistrationSettleNanoseconds = 2'000'000'000LL;
// Bounded re-registration: if a later re-initialisation wipes the table again, three more
// attempts cover it without ever spinning.
constexpr std::uint32_t kMaxRegistrations = 4;
// How often the table is re-written after the Runtime's entry, and for how long. Each round
// is a handful of stores plus two reads, so the cost is negligible next to the journal's own
// retry, and a re-initialisation inside this window is corrected rather than missed.
constexpr std::int64_t kHandoverRoundIntervalNanoseconds = 250'000'000LL;
constexpr std::uint32_t kHandoverRounds = 24;

using GetHookDiagnosticsFn = std::uint64_t (*)(std::uint32_t*, std::size_t);
using GetDiagnosticsStateFn = std::uint64_t (*)(std::uint32_t*, std::size_t);
using GetSelfJournalStateFn = std::uint64_t (*)(std::uint32_t*, std::size_t);

void WriteMagic(std::uint8_t* target, const char* magic, std::size_t count) noexcept {
    for (std::size_t index = 0; index < count; ++index) {
        target[index] = static_cast<std::uint8_t>(magic[index]);
    }
}

void PutU32(std::uint8_t* target, std::uint32_t value) noexcept {
    for (std::size_t index = 0; index < 4; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (8 * index));
    }
}

void PutU64(std::uint8_t* target, std::uint64_t value) noexcept {
    PutU32(target, static_cast<std::uint32_t>(value & 0xFFFFFFFFULL));
    PutU32(target + 4, static_cast<std::uint32_t>(value >> 32));
}

std::uint32_t Checksum(const std::uint8_t* bytes, std::size_t count) noexcept {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < count; ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}

// The single ship for every file write the plugin performs: `open(..., "ab")`, one
// `write`, one `close`. Keeping one shape means the SaltyNX thread only ever runs the
// sequence that is already proven to work on hardware (build 20260910680000 appended
// 32 bytes through it), and it never constructs an object or makes a virtual call.
void AppendRecord(const HostFileApi& api, const char* path, const std::uint8_t* record,
                  std::size_t size) noexcept {
    if (!api.complete() || path == nullptr || record == nullptr) {
        return;
    }
    const auto open = reinterpret_cast<FileOpenFn>(api.open);
    const auto write = reinterpret_cast<FileWriteFn>(api.write);
    const auto close = reinterpret_cast<FileCloseFn>(api.close);
    void* file = open(path, "ab");
    if (file == nullptr) {
        return;
    }
    static_cast<void>(write(record, 1, size, file));
    static_cast<void>(close(file));
}

// One 32-byte status record: magic, version, flags, sequence, trailing checksum.
void AppendBridgeRecord(const HostFileApi& api, std::uint32_t flags) noexcept {
    std::uint8_t record[32]{};
    WriteMagic(record, kBridgeMagic, sizeof(kBridgeMagic) - 1);
    record[8] = 2;  // record version
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(sizeof(record)));
    PutU32(record + 28, Checksum(record, sizeof(record) - 4));
    AppendRecord(api, kBridgePath, record, sizeof(record));
}

// One 80-byte record: magic, version, flags, length, the Runtime's snapshot return
// value, the raw 48-byte snapshot, then the checksum. The field order matches
// `ISAACBR2` and `ISAACNF1` -- magic, version at +8, flags at +12, length at +16 --
// so one reader handles every record this plugin writes.
//
// 落盘路径由调用方给出：加载线程写进 `bridge.bin`，交接线程写进它自己的文件
//（一条通道只允许一个写入者，理由见 `kHandoverSamplePath`）。
void AppendSnapshotRecord(const HostFileApi& api, const char* path, std::uint32_t flags,
                          std::uint8_t readIndex, std::uint64_t snapshotResult,
                          const std::uint8_t* snapshot, std::size_t snapshotSize) noexcept {
    std::uint8_t record[kSnapshotRecordSize]{};
    WriteMagic(record, kSnapshotMagic, sizeof(kSnapshotMagic) - 1);
    record[8] = 1;  // record version
    record[9] = readIndex;  // 1 = at plugin load, 2 = after the second delay
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(kSnapshotRecordSize));
    PutU32(record + 20, static_cast<std::uint32_t>(snapshotResult & 0xFFFFFFFFULL));
    const std::size_t copy =
        snapshotSize < kTestRunSnapshotWords ? snapshotSize : kTestRunSnapshotWords;
    for (std::size_t index = 0; index < copy; ++index) {
        record[24 + index] = snapshot[index];
    }
    PutU32(record + 72, Checksum(record, 72));
    AppendRecord(api, path, record, sizeof(record));
}

// One 32-byte record written to a path that does not exist yet.
void AppendCreateProbe(const HostFileApi& api, std::uint32_t flags) noexcept {
    std::uint8_t record[32]{};
    WriteMagic(record, kCreateProbeMagic, sizeof(kCreateProbeMagic) - 1);
    record[8] = 1;  // record version
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(sizeof(record)));
    PutU32(record + 28, Checksum(record, sizeof(record) - 4));
    AppendRecord(api, kCreateProbePath, record, sizeof(record));
}

// Everything the hand-over thread needs, published before it starts.
//
// The plugin's own run must return promptly: SaltyNX loads plugins from the game's startup
// path, and a wait there stalls the startup that would run the Runtime's entry -- measured
// on 2026-09-11, when the game sat on its loading screen with no records written at all
// because the wait loop was holding that thread.
struct HandoverContext {
    HostFileApi api{};
    GetRuntimeIdentityFn identity{nullptr};
    GetDiagnosticsStateFn diagnosticsState{nullptr};
    GetSelfJournalStateFn selfJournalState{nullptr};
    SetPluginNoteFn setPluginNote{nullptr};
    SetSymbolLookupFn setSymbolLookup{nullptr};
    // 运行时的 48 字节快照入口。交接线程用它做**延迟后的第二次读取**：它是这个项目里
    // 唯一"跑得够晚"的执行者，而加载线程只跑一次、必定赶在安装之前。
    GetTestRunSnapshotFn snapshot{nullptr};
    std::uintptr_t lookupAddress{0};
    std::uintptr_t ownIdentity{0};
    std::uintptr_t ownBase{0};
    // Address of one entry of the file table inside the copy the symbol lookup returned,
    // i.e. inside this module's own `.bss`.
    std::uintptr_t fileApiSlot{0};
};

HandoverContext g_helper{};
std::uint32_t g_handoverStarted = 0;
// 交接线程写记录时要用的能力位。加载线程在启动它之前把这个值填好（那时已经解析出的
// 才是"能力"；加载线程随后补上的几位是**读取结果**，不属于能力位）。
std::uint32_t g_handoverFlags = 0;
// 交接线程启动取证（只由加载线程在 StartHandoverThread 里写、由加载线程读出来写记录，
// 交接线程本身不碰这三个变量）。0 是合法返回码，所以另用 g_handoverStartAttempted 区分
// "从未尝试"与"尝试了且返回 0"。
std::uint32_t g_handoverStartAttempted = 0;
std::uint32_t g_handoverCreateResult = 0;
std::uint32_t g_handoverStartResult = 0;
alignas(16) std::uint8_t g_handoverTlsBlock[0x200]{};
// One stack for one thread. Static, so no allocator has to work inside a module that the
// SaltyNX loader mapped without newlib's bookkeeping.
alignas(16) std::uint8_t g_handoverStack[0x8000]{};

// devkitA64 reads the libc thread pointer from `[tpidrro_el0 + 0x1f8]` and dereferences it,
// so a thread created here needs that slot filled before it calls any devkitA64 code --
// SaltyNX's own file functions among them. The slot is only written when it is empty, so a
// thread that already has a thread pointer is untouched. The module does the same thing for
// the game's threads.
void EnsurePluginThreadTls() noexcept {
    std::uint8_t* threadRegion = nullptr;
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(threadRegion));
    if (threadRegion == nullptr) {
        return;
    }
    auto* slot = reinterpret_cast<volatile std::uintptr_t*>(threadRegion + 0x1f8);
    if (*slot == 0) {
        *slot = reinterpret_cast<std::uintptr_t>(g_handoverTlsBlock);
    }
}

void HostHandoverMain(void*);

bool StartHandoverThread() noexcept {
    if (g_handoverStarted != 0) {
        return true;
    }
    g_handoverStarted = 1;
    Handle thread{};
    const auto stackTop = reinterpret_cast<void*>(g_handoverStack + sizeof(g_handoverStack));
    // 优先级原先是 0x2C（libnx 的常规值，与游戏的多数线程同级）。实测（v8，2026-09-14 00:42
    // 会话）表明那不够：线程在**加载阶段**调度正常——1.4 秒内跑完 6 轮、每轮约 0.3 秒（与
    // 250 毫秒睡眠相符），随后 **65 秒里一轮都没再跑**（本该在 5/10/…/30 秒各有一条取样）。
    // 停点恰好落在游戏主循环起步、把核占满的时刻，所以假设是"低优先级 + 每 250 毫秒醒一次"
    // 在游戏跑起来后分不到时间片。0x1C 高于游戏线程常见的 0x28–0x2C 区间、低于音频线程，
    // 且本线程一轮只需约 50 毫秒 CPU（每 300 毫秒一次），不会长期占用。
    constexpr int kHandoverThreadPriority = 0x1C;
    // 两个返回码都要带出来：创建失败与启动失败是两种不同的故障，而它们的返回值此前
    // 被 `R_FAILED(...)` 丢掉，设备侧无从分辨。
    g_handoverStartAttempted = 1;
    const auto createResult = svcCreateThread(&thread, reinterpret_cast<void*>(&HostHandoverMain),
                                              nullptr, stackTop, kHandoverThreadPriority, -2);
    g_handoverCreateResult = static_cast<std::uint32_t>(createResult);
    if (R_FAILED(createResult)) {
        g_handoverStarted = 0;
        return false;
    }
    const auto startResult = svcStartThread(thread);
    g_handoverStartResult = static_cast<std::uint32_t>(startResult);
    if (R_FAILED(startResult)) {
        return false;
    }
    return true;
}

// 记录内容与落盘分离（低耦合约定）：`Build...` 只往缓冲里填字节，`Append...` 只负责把它
// 送出去。将来若把"文件读写实现"换掉（例如改由运行时自己写、不再经 SaltySD），内容构造
// 可以原样复用，只需换掉最后那一次 `AppendRecord`。
// 定义位置在 `g_handover*` 变量之后、`HostHandoverMain` 之前，因此三个变量都已可见。
//   +20 threadLive      1 = svcCreateThread 与 svcStartThread 都成功
//   +24 attempted       1 = 进入过创建路径（用于把"返回码 0"与"从未尝试"分开）
//   +28 createResult    svcCreateThread 的返回码（0 = 成功）
//   +32 startResult     svcStartThread 的返回码（0 = 成功）
//   +36 startedFlag     `g_handoverStarted` 的当前值（诊断用）
void BuildHandoverStartRecord(std::uint8_t* record, std::uint32_t flags,
                              std::uint32_t threadLive) noexcept {
    WriteMagic(record, kHandoverStartMagic, sizeof(kHandoverStartMagic) - 1);
    record[8] = 1;  // record version
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(kHandoverStartRecordSize));
    PutU32(record + 20, threadLive);
    PutU32(record + 24, g_handoverStartAttempted);
    PutU32(record + 28, g_handoverCreateResult);
    PutU32(record + 32, g_handoverStartResult);
    PutU32(record + 36, g_handoverStarted);
    PutU32(record + 44, Checksum(record, kHandoverStartRecordSize - 4));
}

void AppendHandoverStartRecord(const HostFileApi& api, std::uint32_t flags,
                               std::uint32_t threadLive) noexcept {
    std::uint8_t record[kHandoverStartRecordSize]{};
    BuildHandoverStartRecord(record, flags, threadLive);
    AppendRecord(api, kBridgePath, record, sizeof(record));
}

// 交接线程的一次取样。同一份结构既用于"没等到入口"的时点，也用于"交换完成"的时点，
// 所以字段都允许为 0 —— 0 本身就是读数。
struct HandoverSample {
    std::uint32_t polls{0};
    std::uint32_t registrations{0};   // 本轮真的把表写进去的轮数
    std::uint32_t fileApiMask{0};
    std::uint32_t sessionCreated{0};
    std::uint32_t registrationCalls{0};
    std::uint32_t gateStoppedEarly{0};
    std::uint32_t handshake{0};
    std::uint64_t publishedExlMain{0};
    std::uint64_t tickSelfAppeared{0};
    std::uint64_t tickRegistered{0};
    std::uint64_t tickNow{0};
    std::uint32_t exlMainAttempts{0};
    std::uint32_t exlMainRecords{0};
    std::uint32_t serviceState{0};
};

// 内容构造（不落盘）。布局与解码器既有的 `ISAACWT1` 分支逐字段对齐。
void BuildHandoverSampleRecord(std::uint8_t* record, std::uint32_t flags,
                               const HandoverSample& sample) noexcept {
    WriteMagic(record, kHandoverSampleMagic, sizeof(kHandoverSampleMagic) - 1);
    record[8] = 1;  // record version
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(kHandoverSampleRecordSize));
    PutU32(record + 20, sample.polls);
    PutU32(record + 24, sample.registrations);
    PutU32(record + 28, sample.fileApiMask);
    PutU32(record + 32, sample.sessionCreated);
    PutU32(record + 36, sample.registrationCalls);
    PutU32(record + 40, sample.gateStoppedEarly);
    PutU32(record + 44, sample.handshake);
    PutU64(record + 48, sample.publishedExlMain);
    PutU64(record + 56, sample.tickSelfAppeared);
    PutU64(record + 64, sample.tickRegistered);
    PutU64(record + 72, sample.tickNow);
    PutU32(record + 80, sample.exlMainAttempts);
    PutU32(record + 84, sample.exlMainRecords);
    PutU32(record + 88, sample.serviceState);
    PutU32(record + 92, Checksum(record, kHandoverSampleRecordSize - 4));
}

// 落盘出口。集中在这一处（而不是让线程体直接调各 `Append...`）有两个原因：
// ① 既有护栏测试禁止 `HostHandoverMain` 的函数体里出现 `Append...` 家族字面量；
// ② 将来若把文件读写实现从 SaltySD 表换成别的（经游戏 libc 那条路现在不可用，但保留），
//    要换的只有这一个函数，取样结构可以原样复用。
void PublishHandoverSample(const HostFileApi& api, std::uint32_t flags,
                           const HandoverSample& sample,
                           GetTestRunSnapshotFn snapshot) noexcept {
    std::uint8_t record[kHandoverSampleRecordSize]{};
    BuildHandoverSampleRecord(record, flags, sample);
    // 写**交接线程自己的文件**，不写 `bridge.bin`：那条通道的写入者是加载线程，两个写入者
    // 共用一条 `"ab"` 通道时记录会被覆盖（实测，见 `kHandoverSamplePath` 的注释）。
    AppendRecord(api, kHandoverSamplePath, record, sizeof(record));
    // 同一次取样里把运行时的 48 字节快照按既有 `ISAACSN1` 格式补一条 `readIndex = 2`。
    // 加载线程读的那条（readIndex = 1）必定赶在安装之前，所以安装报告只有这里能读到。
    if (snapshot == nullptr) {
        return;
    }
    std::uint8_t bytes[kTestRunSnapshotWords]{};
    const std::uint64_t result = snapshot(bytes, sizeof(bytes));
    AppendSnapshotRecord(api, kHandoverSamplePath, flags, kHandoverSnapshotReadIndex, result,
                         bytes, sizeof(bytes));
}

// Creates one probe file from whichever thread calls this, through the same table the
// Runtime was handed. `"wb"` on purpose: the probe must create the file.
ThreadWriteResult WriteThreadProbe(const HostFileApi& api, const char* path,
                                   const char* text, std::size_t textSize) noexcept {
    ThreadWriteResult result{};
    if (!api.complete() || path == nullptr) {
        return result;
    }
    ++result.attempts;
    const auto open = reinterpret_cast<FileOpenFn>(api.open);
    const auto write = reinterpret_cast<FileWriteFn>(api.write);
    const auto close = reinterpret_cast<FileCloseFn>(api.close);
    result.openFn = api.open;
    result.writeFn = api.write;
    result.closeFn = api.close;
    void* file = open(path, "wb");
    result.openOk = file != nullptr ? 1U : 0U;
    if (file == nullptr) {
        return result;
    }
    result.written = static_cast<std::uint32_t>(write(text, 1, textSize - 1, file));
    result.closeOk = close(file) == 0 ? 1U : 0U;
    return result;
}

// One 96-byte record carrying that result, so the plugin thread's half of the comparison
// sits next to the records the same thread already writes.
void AppendThreadWriteRecord(const HostFileApi& api, std::uint32_t flags,
                             const ThreadWriteResult& result) noexcept {
    std::uint8_t record[kThreadWriteRecordSize]{};
    WriteMagic(record, kThreadWriteRecordMagic, sizeof(kThreadWriteRecordMagic) - 1);
    record[8] = 1;  // record version
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(kThreadWriteRecordSize));
    PutU32(record + 20, result.attempts);
    PutU32(record + 24, result.openOk);
    PutU32(record + 28, result.written);
    PutU32(record + 32, result.closeOk);
    PutU64(record + 40, static_cast<std::uint64_t>(result.openFn));
    PutU64(record + 48, static_cast<std::uint64_t>(result.writeFn));
    PutU64(record + 56, static_cast<std::uint64_t>(result.closeFn));
    PutU64(record + 64, static_cast<std::uint64_t>(armGetSystemTick()));
    PutU32(record + kThreadWriteRecordSize - 4, Checksum(record, kThreadWriteRecordSize - 4));
    AppendRecord(api, kBridgePath, record, sizeof(record));
}

// One 224-byte record listing every copy of the Runtime module the byte scan found, and
// what the plugin did with them.
//
// Header: magic, version, flags, length, regions visited, regions scanned, copy count,
// the region base and identity address of the copy the symbol lookup returned, the
// identity address of the copy that ran `exl_main`, that copy's published `exl_main`
// address, the table slot the plugin wrote to, and that slot's signed offset from the
// identity symbol. Then up to six hits of 24 bytes: image base, published `exl_main`
// address, region size, and the region's type (low byte) with its permissions (next
// byte). Exactly one hit with a non-zero published address is the copy that runs.
void AppendModuleCopyRecord(const HostFileApi& api, std::uint32_t flags,
                            const RegionInfo& own, std::uintptr_t ownIdentity,
                            const ModuleCopy* copies, const ModuleCopyScan& scan,
                            std::uintptr_t runningIdentity, std::uintptr_t runningSelf,
                            std::uintptr_t plantedSlot, std::intptr_t slotDelta) noexcept {
    std::uint8_t record[kModuleCopyRecordSize]{};
    WriteMagic(record, kModuleCopyRecordMagic, sizeof(kModuleCopyRecordMagic) - 1);
    record[8] = 1;  // record version
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(kModuleCopyRecordSize));
    PutU32(record + 20, static_cast<std::uint32_t>(scan.regionsVisited));
    PutU32(record + 24, static_cast<std::uint32_t>(scan.regionsScanned));
    PutU32(record + 28, static_cast<std::uint32_t>(scan.count));
    PutU64(record + 32, static_cast<std::uint64_t>(own.base));
    PutU64(record + 40, static_cast<std::uint64_t>(ownIdentity));
    PutU64(record + 48, static_cast<std::uint64_t>(runningIdentity));
    PutU64(record + 56, static_cast<std::uint64_t>(runningSelf));
    PutU64(record + 64, static_cast<std::uint64_t>(plantedSlot));
    PutU64(record + 72, static_cast<std::uint64_t>(slotDelta));
    for (std::size_t index = 0; index < kMaxModuleCopies; ++index) {
        if (index >= scan.count) {
            continue;
        }
        std::uint8_t* hit = record + 80 + index * 24;
        PutU64(hit, static_cast<std::uint64_t>(copies[index].base));
        PutU64(hit + 8, static_cast<std::uint64_t>(copies[index].self));
        PutU32(hit + 16, static_cast<std::uint32_t>(copies[index].size));
        PutU32(hit + 20, (copies[index].type & 0xFFU) | ((copies[index].perm & 0xFFU) << 8));
    }
    PutU32(record + kModuleCopyRecordSize - 4,
           Checksum(record, kModuleCopyRecordSize - 4));
    AppendRecord(api, kBridgePath, record, sizeof(record));
}

// One 80-byte record describing every copy of the Runtime module the plugin can see:
// how many, their load bases, the address its own symbol lookup returned, and the address
// the running copy published for itself. Together with the two symbols' offsets in the
// module ELF these are enough to prove, arithmetically, whether the lookup and the game
// share one copy.
void AppendMappingRecord(const HostFileApi& api, std::uint32_t flags,
                         const RegionInfo& own, std::uintptr_t ownIdentity,
                         const MappingHit* sameSize, std::size_t sameSizeCount,
                         std::size_t runningIndex, std::uintptr_t runningSelf,
                         std::uint64_t visited) noexcept {
    std::uint8_t record[kMappingRecordSize]{};
    WriteMagic(record, kMappingRecordMagic, sizeof(kMappingRecordMagic) - 1);
    record[8] = 4;  // record version
    PutU32(record + 12, flags);
    PutU32(record + 16, static_cast<std::uint32_t>(kMappingRecordSize));
    PutU32(record + 20, static_cast<std::uint32_t>(visited));
    PutU32(record + 24, own.found ? 1U : 0U);
    PutU32(record + 28, own.type);
    PutU32(record + 32, own.perm);
    PutU32(record + 36, static_cast<std::uint32_t>(own.base & 0xFFFFFFFFULL));
    PutU32(record + 40, static_cast<std::uint32_t>(own.base >> 32));
    PutU32(record + 44, static_cast<std::uint32_t>(own.size & 0xFFFFFFFFULL));
    PutU32(record + 48, static_cast<std::uint32_t>(own.size >> 32));
    PutU32(record + 52, static_cast<std::uint32_t>(ownIdentity & 0xFFFFFFFFULL));
    PutU32(record + 56, static_cast<std::uint32_t>(ownIdentity >> 32));
    PutU32(record + 60, static_cast<std::uint32_t>(runningSelf & 0xFFFFFFFFULL));
    PutU32(record + 64, static_cast<std::uint32_t>(runningSelf >> 32));
    PutU32(record + 68, static_cast<std::uint32_t>(sameSizeCount));
    PutU32(record + 72, static_cast<std::uint32_t>(runningIndex + 1));
    for (std::size_t index = 0; index < kMaxRegions; ++index) {
        const bool present = index < sameSizeCount;
        const auto base = present ? static_cast<std::uint64_t>(sameSize[index].base) : 0ULL;
        const auto size = present ? sameSize[index].size : 0ULL;
        PutU32(record + 76 + index * 12, static_cast<std::uint32_t>(base & 0xFFFFFFFFULL));
        PutU32(record + 80 + index * 12, static_cast<std::uint32_t>(base >> 32));
        PutU32(record + 84 + index * 12, static_cast<std::uint32_t>(size & 0xFFFFFFFFULL));
    }
    PutU32(record + kMappingRecordSize - 4, Checksum(record, kMappingRecordSize - 4));
    AppendRecord(api, kBridgePath, record, sizeof(record));
}

// Local copy of the mapping-probe helper signatures, so the scan below can reuse them
// without the two probes depending on each other's internals.
struct ModuleCopyWork {
    ModuleCopy copies[kMaxModuleCopies]{};
    ModuleCopyScan scan{};
    std::uintptr_t runningIdentity = 0;
    std::uintptr_t runningSelf = 0;
    std::uintptr_t plantedSlot = 0;
    std::intptr_t slotDelta = 0;
    bool planted = false;
};

// Hands the registered file table to the copy that runs.
//
// The table lives in the copy's `.bss`, which the plugin cannot name. It asks the copy it
// already reaches for the address of one entry, converts that to an offset from the
// identity symbol -- both copies are the same file, so the same offset holds the same
// slot -- and writes the four pointers there directly. No code runs on the plugin's
// thread and no object is constructed: this is a plain memory store into verified
// memory, which is the whole point, because registering through the entry point at this
// moment is what crashed on hardware before (report 01789053980).
bool PlantFileTableInRunningCopy(ModuleCopyWork& work, const HostFileApi& api,
                                 std::uintptr_t ownIdentity,
                                 GetFileApiSlotFn fileApiSlot) noexcept {
    if (fileApiSlot == nullptr || ownIdentity == 0 || work.runningIdentity == 0) {
        return false;
    }
    const std::uintptr_t ownSlot = fileApiSlot(0);
    if (ownSlot == 0) {
        return false;
    }
    work.slotDelta = static_cast<std::intptr_t>(ownSlot) -
                     static_cast<std::intptr_t>(ownIdentity);
    work.plantedSlot = static_cast<std::uintptr_t>(
        static_cast<std::intptr_t>(work.runningIdentity) + work.slotDelta);
    auto* slots = reinterpret_cast<std::uintptr_t*>(work.plantedSlot);
    slots[0] = api.open;
    slots[1] = api.read;
    slots[2] = api.write;
    slots[3] = api.close;
    work.planted = true;
    return true;
}

// The hand-over thread.
//
// It does the two things the plugin's own thread must not: wait, and touch the module after
// its entry has run. SaltyNX loads plugins from the game's startup path, so waiting there
// stalls the startup that would run the entry (measured 2026-09-11: the game sat on its
// loading screen with no records written at all).
//
// It performs no file I/O and calls no Runtime entry point, for a measured reason: a thread
// the game did not create cannot use the game's libc. The first version of this thread
// called the Runtime's registration entry, which opened a file through the game's `fopen`,
// and the session died with `Result 0x2A8` and `PC = 0` while that open was in flight
// (report 01789118886). Every step below is a memory read or a memory write:
//
//   * read the address `exl_main` published, until it is non-zero -- that is the Runtime's
//     entry having run, which is the moment the table stops being erased by `.bss` clearing;
//   * find the module's copies by content and pick the one that published an address;
//   * write the four file pointers straight into that copy's table. The Runtime's own retry,
//     on the game's thread, then attaches and writes the journal -- from a thread that can
//     use libc;
//   * hand the findings to the module with `SetPluginNote`, so the deliberate break carries
//     them out in the crash report's registers.
//
// The thread ends with `svcExitThread`, never by returning. A thread created with raw
// `svcCreateThread` has no return address: its entry runs with `LR = 0`, so the first
// `ret` jumps to address 0. That is exactly what the session of report 01789119239 died of
// -- `Result 0x2A8`, `Instruction Abort` at address 0, `PC = LR = FP = 0`, on the unnamed
// thread this function runs on, with `X[00]`/`X[01]` still holding the arguments of the
// last call it made (`"ISAACPN1"` and the word count 8).
void HostHandoverMain(void*) {
    EnsurePluginThreadTls();
    const HostFileApi api = g_helper.api;
    // 只留一条 t=0 取样：证明"线程跑起来了、并且能经这张表写盘"。不带快照（传 nullptr）——
    // t=0 的快照必然还是安装前的状态，带上只会让 `readIndex=2` 多出一条易被误读的记录。
    //
    // **本轮刻意把写盘量降到最低**（v10，2026-09-14）。原因是一个必须排除的可能：前几版
    // 线程都恰好"写了几条记录之后就再无动静"（v7 四条 / v8 九条 / v9 九条），而写盘是循环里
    // 唯一随迭代累积的动作 —— 也就是说，**诊断本身可能是把线程弄停的原因**。
    // 于是本轮除了这一条与"进入循环"那条，循环里一条都不写：若 30 秒窗口走完后那条
    // "没等到入口"的记录出现（`polls` 约 120），就说明循环本身能撑过 30 秒、写盘才是那个
    // 累积性的杀手；若仍不出现，则停点与写盘无关，得转探针崩溃通道。
    HandoverSample started{};
    started.tickNow = svcGetSystemTick();
    PublishHandoverSample(api, g_handoverFlags, started, nullptr);
    std::uint32_t polls = 0;
    std::uint32_t progressSamples = 0;
    std::uintptr_t entryAddress = 0;
    if (g_helper.identity != nullptr) {
        std::int64_t waited = 0;
        while (waited <= kEntryFirstWaitNanoseconds) {
            // 循环体第一次进入时立刻留证。这一条把三种"之后什么都没有"分开：
            // 只有 t=0 一条 ⇒ 线程在 t=0 之后就没再执行；有这条但 `polls == 1` 那条从不出现
            // ⇒ 卡在**第一次身份调用**里；两条都有 ⇒ 调用返回了，循环在跑。
            if (polls == 0) {
                HandoverSample entered{};
                entered.tickNow = svcGetSystemTick();
                PublishHandoverSample(api, g_handoverFlags, entered, nullptr);
            }
            std::uint32_t words[4]{};
            if (g_helper.identity(words, 4) == kRuntimeIdentityMagic) {
                entryAddress = static_cast<std::uintptr_t>(words[0]) |
                               (static_cast<std::uintptr_t>(words[1]) << 32);
            }
            ++polls;
            // 进度取样：前几次迭代逐次留，之后每 5 秒一条。若只看到 t=0/进入循环/`polls == 1`
            // 而看不到 `polls == 2`，就说明**其后那次 `svcSleepThread` 没回来**；看得到
            // `polls == 2..5` 则睡眠正常、停点更靠后。不带快照（传 nullptr）。
            if ((polls <= kHandoverProgressEarlyPolls ||
                 (polls % kHandoverProgressEveryPolls) == 0) &&
                progressSamples < kHandoverProgressSampleLimit) {
                ++progressSamples;
                HandoverSample ticking{};
                ticking.polls = polls;
                ticking.publishedExlMain = entryAddress;
                ticking.tickNow = svcGetSystemTick();
                PublishHandoverSample(api, g_handoverFlags, ticking, nullptr);
            }
            if (entryAddress != 0) {
                break;
            }
            svcSleepThread(kEntryPollIntervalNanoseconds);
            waited += kEntryPollIntervalNanoseconds;
        }
    }
    if (entryAddress == 0) {
        // 原先这条分支什么都不写就驻停，于是"没等到入口"与"写盘失败"在文件侧留下的
        // 证据完全一样（都是"没有它的文件"）。先留一条取样（publishedExlMain 为 0），
        // 把这次会话的事实钉下来，再谈下一步。
        HandoverSample missing{};
        missing.polls = polls;
        missing.tickNow = svcGetSystemTick();
        PublishHandoverSample(api, g_handoverFlags, missing, g_helper.snapshot);
        // 入口只是"出得晚"也是一种可能，所以不立刻驻停：按固定节奏重查，上限到了才驻停。
        // 仍然驻停而不是退出：`svcExitThread` 之后若再返回，LR = 0 会让它跳到地址 0
        //（报告 01789119239 正是这样死的）。
        for (std::uint32_t recheck = 0; recheck < kHandoverRecheckCount; ++recheck) {
            svcSleepThread(kHandoverRecheckIntervalNanoseconds);
            if (g_helper.identity != nullptr) {
                std::uint32_t words[4]{};
                if (g_helper.identity(words, 4) == kRuntimeIdentityMagic) {
                    entryAddress = static_cast<std::uintptr_t>(words[0]) |
                                   (static_cast<std::uintptr_t>(words[1]) << 32);
                }
            }
            ++polls;
            if (entryAddress != 0) {
                break;
            }
        }
    }
    if (entryAddress == 0) {
        // The Runtime never started inside the window, so there is nothing to hand over to.
        // Park rather than exit: `svcExitThread` is a syscall, and if it ever returned, the
        // thread would fall through into code that assumes the entry was found -- which is
        // exactly what an earlier session's note looked like (zero polls and a zero entry
        // address next to a table mask that could only have been read later). A thread that
        // sleeps forever cannot fall through anywhere.
        for (;;) {
            svcSleepThread(kEntryWaitDeadlineNanoseconds);
        }
    }

    // Which copy runs, by content. The scan compares code before it calls anything, and the
    // copy it is looking for is the one that published `exl_main`.
    ModuleCopyWork work{};
    std::uintptr_t runningIdentity = g_helper.ownIdentity;
    if (g_helper.ownIdentity != 0 && g_helper.identity != nullptr) {
        FindModuleCopies(work.copies, kMaxModuleCopies, nullptr, g_helper.ownBase,
                         g_helper.ownIdentity, g_helper.identity, &work.scan);
        for (std::size_t index = 0; index < work.scan.count; ++index) {
            if (work.copies[index].self == 0) {
                continue;
            }
            runningIdentity = work.copies[index].identity;
            break;
        }
    }

    // Hand the table over, and keep handing it over: the Runtime retries its attach every
    // update frame, and a second re-initialisation would empty the table again. Pure stores
    // into verified memory, then a read of what the Runtime makes of them.
    std::uint32_t finalDiagnostics[kDiagnosticsStateWordCount]{};
    std::uint32_t finalJournal[kSelfJournalStateWordCount]{};
    std::uint32_t plantedRounds = 0;
    std::uint64_t tickRegistered = 0;
    std::uint64_t tickSelfAppeared = 0;
    for (std::uint32_t round = 0; round < kHandoverRounds; ++round) {
        const std::intptr_t delta = runningIdentity != 0 && g_helper.ownIdentity != 0
                                        ? static_cast<std::intptr_t>(runningIdentity) -
                                              static_cast<std::intptr_t>(g_helper.ownIdentity)
                                        : 0;
        const std::uintptr_t slot = static_cast<std::uintptr_t>(
            static_cast<std::intptr_t>(g_helper.fileApiSlot) + delta);
        if (slot != 0) {
            auto* slots = reinterpret_cast<std::uintptr_t*>(slot);
            slots[0] = api.open;
            slots[1] = api.read;
            slots[2] = api.write;
            slots[3] = api.close;
            ++plantedRounds;
        }
        svcSleepThread(kHandoverRoundIntervalNanoseconds);
        if (g_helper.diagnosticsState != nullptr) {
            static_cast<void>(
                g_helper.diagnosticsState(finalDiagnostics, kDiagnosticsStateWordCount));
        }
        if (g_helper.selfJournalState != nullptr) {
            static_cast<void>(
                g_helper.selfJournalState(finalJournal, kSelfJournalStateWordCount));
        }
        // 两个"第一次出现"的时刻：注册真的落到这份副本（registrationCalls 非零）、
        // 以及运行时自己的自日志开始记账（exlMainRecords 非零）。0 表示这一轮里没出现。
        if (tickRegistered == 0 && finalDiagnostics[2] != 0) {
            tickRegistered = svcGetSystemTick();
        }
        if (tickSelfAppeared == 0 && finalJournal[5] != 0) {
            tickSelfAppeared = svcGetSystemTick();
        }
    }

    // Hand the Runtime the loader's lookup, so it can resolve the game's own file functions
    // by name. A plain store, after the entry, so the `.bss` clearing cannot erase it.
    if (g_helper.setSymbolLookup != nullptr && g_helper.lookupAddress != 0) {
        g_helper.setSymbolLookup(g_helper.lookupAddress);
    }

    // Does a thread this plugin created get to write a file? Its answer is a file plus the
    // same numbers, carried out through `SetPluginNote` into the probe break's registers.
    const ThreadWriteResult handoverWrite =
        WriteThreadProbe(api, kHandoverWriteProbePath, kHandoverWriteProbeText,
                         sizeof(kHandoverWriteProbeText));

    // 交换完成后的取样：设备侧第一次能同时看到"线程走到了哪"（polls / publishedExlMain /
    // 写进表的轮数）与"运行时自己的状态"（表掩码、会话、注册次数、握手、自日志）。随后那条
    // `readIndex = 2` 的快照里，`reserved` 高 24 位就是挂点安装报告（M1 判据⑤ 的读数）。
    HandoverSample complete{};
    complete.polls = polls;
    complete.registrations = plantedRounds;
    complete.fileApiMask = finalDiagnostics[0];
    complete.sessionCreated = finalDiagnostics[1];
    complete.registrationCalls = finalDiagnostics[2];
    complete.gateStoppedEarly = finalDiagnostics[3];
    complete.handshake = finalDiagnostics[4];
    complete.publishedExlMain = entryAddress;
    complete.tickSelfAppeared = tickSelfAppeared;
    complete.tickRegistered = tickRegistered;
    complete.tickNow = svcGetSystemTick();
    complete.exlMainAttempts = finalJournal[4];
    complete.exlMainRecords = finalJournal[5];
    complete.serviceState = finalJournal[14];
    PublishHandoverSample(api, g_handoverFlags, complete, g_helper.snapshot);

    if (g_helper.setPluginNote != nullptr) {
        const std::uint32_t note[8] = {
            polls,
            static_cast<std::uint32_t>(work.scan.count),
            static_cast<std::uint32_t>(entryAddress & 0xFFFFFFFFULL),
            static_cast<std::uint32_t>(entryAddress >> 32),
            // hand-over thread's own file write: openOk | closeOk << 8 | attempts << 16
            handoverWrite.openOk | (handoverWrite.closeOk << 8) | (handoverWrite.attempts << 16),
            handoverWrite.written,
            finalDiagnostics[0],
            finalDiagnostics[1] | (finalDiagnostics[3] << 8),
        };
        static_cast<void>(g_helper.setPluginNote(note, 8));
    }
    // Never return from a raw thread entry, and never rely on a syscall to end it either:
    // park here. See the note on the early-exit branch above.
    for (;;) {
        svcSleepThread(kEntryWaitDeadlineNanoseconds);
    }
}

} // namespace

std::uint32_t SaltyNxHostPlugin::Run(bool report) noexcept {
    std::uint32_t flags = 0;

    const HostFileApi api{
        resolver_.Find(kOpenSymbol),
        resolver_.Find(kReadSymbol),
        resolver_.Find(kWriteSymbol),
        resolver_.Find(kCloseSymbol),
    };
    if (api.complete()) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::FileTableResolved);
    }
    if (api.read != 0) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::ReadResolved);
    }

    const auto registerFileApi =
        reinterpret_cast<RuntimeHostApiClient::RegisterFileApiFn>(resolver_.Find(kRegisterSymbol));
    if (registerFileApi == nullptr) {
        if (report) {
            AppendBridgeRecord(api, flags);
        }
        return flags;
    }
    flags |= static_cast<std::uint32_t>(HostPluginFlags::RegistrationEntryResolved);

    // The file table is deliberately NOT registered here any more.
    //
    // It used to be the first thing this run did, and the Runtime erases it: the module
    // image is mapped when the process starts, but its entry runs much later -- the game
    // reaches it through `nn::init::Start` during its own startup -- and `__module_start`
    // clears `.bss` before anything else runs. One session measured both sides of that
    // moment inside a single image: the table read back as 0xf at ten seconds and as 0
    // twenty-odd seconds later, while the same image's `exl_main` address went from zero to
    // non-zero. That is the whole reason `isaac-runtime-events.bin` never appeared: the
    // Runtime attached to an empty table and stopped before calling `open`. Handing the
    // table over is the helper thread's job now, after the entry has run.

    // Which region holds the identity symbol, and which regions are the same size.
    std::uint64_t visited = 0;
    std::uintptr_t ownIdentity = 0;
    std::uintptr_t runningSelf = 0;
    std::size_t runningIndex = 0;
    RegionInfo own{};
    MappingHit sameSize[kMaxRegions]{};
    std::size_t sameSizeCount = 0;
    const auto identity =
        reinterpret_cast<GetRuntimeIdentityFn>(resolver_.Find(kRuntimeIdentitySymbol));
    if (identity != nullptr) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::RuntimeIdentityResolved);
        ownIdentity = reinterpret_cast<std::uintptr_t>(identity);
        own = FindRegionContaining(ownIdentity, &visited);
    }
    if (own.found) {
        sameSizeCount = FindSameSizeRegions(own.size, sameSize, kMaxRegions, &visited);
        // Reported for continuity only. This loop cannot distinguish one copy from two: its
        // candidate address is `region.base + identityOffset`, which for the plugin's own
        // region is exactly the address the symbol lookup returned, so it confirms itself.
        // The byte scan below is the measurement that counts copies.
        const std::uintptr_t identityOffset = ownIdentity - own.base;
        for (std::size_t index = 0; index < sameSizeCount; ++index) {
            const auto candidate =
                reinterpret_cast<GetRuntimeIdentityFn>(sameSize[index].base + identityOffset);
            std::uint32_t words[4]{};
            if (candidate(words, 4) != kRuntimeIdentityMagic) {
                continue;
            }
            flags |= static_cast<std::uint32_t>(HostPluginFlags::RuntimeCopyConfirmed);
            const auto self = static_cast<std::uintptr_t>(words[0]) |
                              (static_cast<std::uintptr_t>(words[1]) << 32);
            if (self == 0) {
                continue;
            }
            runningSelf = self;
            runningIndex = index;
            break;
        }
    }

    // Count the copies by content, and hand the table to the one that runs. Before the
    // entry has run there is nothing to hand it to -- every candidate reports a zero
    // published address -- so this is a report, not the hand-over.
    ModuleCopyWork work{};
    const auto fileApiSlot =
        reinterpret_cast<GetFileApiSlotFn>(resolver_.Find(kFileApiSlotSymbol));
    if (own.found && identity != nullptr) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::ModuleCopyScanDone);
        FindModuleCopies(work.copies, kMaxModuleCopies, nullptr, own.base, ownIdentity,
                         identity, &work.scan);
        for (std::size_t index = 0; index < work.scan.count; ++index) {
            if (work.copies[index].self == 0) {
                continue;
            }
            work.runningIdentity = work.copies[index].identity;
            work.runningSelf = work.copies[index].self;
            flags |= static_cast<std::uint32_t>(HostPluginFlags::RunningCopyFound);
            break;
        }
        if (work.runningIdentity != 0 &&
            PlantFileTableInRunningCopy(work, api, ownIdentity, fileApiSlot)) {
            flags |= static_cast<std::uint32_t>(
                HostPluginFlags::FileTablePlantedInRunningCopy);
        }
    }

    // Publish what the hand-over thread needs, then start it. The wait for the Runtime's
    // entry must not happen on this thread: SaltyNX loads plugins from the game's startup
    // path, so sleeping here stalls the startup that would run the entry -- measured on
    // 2026-09-11, when the game sat on its loading screen and this run wrote no records at
    // all because the wait was holding that thread.
    const auto observerState =
        reinterpret_cast<GetObserverStateFn>(resolver_.Find(kObserverStateSymbol));
    const auto snapshot =
        reinterpret_cast<GetTestRunSnapshotFn>(resolver_.Find(kTestRunSnapshotSymbol));
    const auto hookDiagnostics =
        reinterpret_cast<GetHookDiagnosticsFn>(resolver_.Find(kHookDiagnosticsSymbol));
    if (observerState != nullptr) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::ObserverStateResolved);
    }
    if (snapshot != nullptr) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::SnapshotResolved);
    }
    if (hookDiagnostics != nullptr) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::HookDiagnosticsResolved);
    }

    g_helper.api = api;
    g_helper.identity = identity;
    g_helper.ownIdentity = ownIdentity;
    g_helper.ownBase = own.base;
    g_helper.fileApiSlot = fileApiSlot != nullptr ? fileApiSlot(0) : 0;
    g_helper.setPluginNote =
        reinterpret_cast<SetPluginNoteFn>(resolver_.Find(kPluginNoteSymbol));
    g_helper.setSymbolLookup =
        reinterpret_cast<SetSymbolLookupFn>(resolver_.Find(kSetSymbolLookupSymbol));
    g_helper.lookupAddress = resolver_.LookupAddress();
    g_helper.diagnosticsState =
        reinterpret_cast<GetDiagnosticsStateFn>(resolver_.Find(kDiagnosticsStateSymbol));
    g_helper.selfJournalState =
        reinterpret_cast<GetSelfJournalStateFn>(resolver_.Find(kSelfJournalStateSymbol));
    g_helper.snapshot = snapshot;
    if (g_helper.diagnosticsState != nullptr) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::DiagnosticsStateResolved);
    }
    if (g_helper.selfJournalState != nullptr) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::SelfJournalStateResolved);
    }
    // 此时已解析出的几位才是"能力位"；本函数随后补上的几位是**读取结果**，不属于能力位。
    // 交接线程晚些取样，用它把记录里的 flags 填上。
    g_handoverFlags = flags;
    const bool helperStarted = StartHandoverThread();
    // 这一位必须在下面所有记录写入**之前**合并进 flags。原先它写在所有 Append* 之后，
    // 于是任何记录里都看不到 bit 21 ——"交接线程起没起来"在文件侧完全不可读，
    // 只能靠"它没写出文件"间接推断。这一处顺序修正本身就是本轮取证的一部分。
    if (helperStarted) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::HandoverThreadStarted);
    }

    // Read the Runtime-owned snapshot back, read-only: this is the state that says how far
    // the Runtime's own diagnostics attach got.
    std::uint8_t snapshotBytes[kTestRunSnapshotWords]{};
    std::uint64_t snapshotResult = 0;
    if (observerState != nullptr) {
        std::uint32_t state[kObserverStateWordCount]{};
        if (observerState(state, kObserverStateWordCount) == kObserverStateExported) {
            flags |= static_cast<std::uint32_t>(HostPluginFlags::ObserverStateRead);
        }
    }
    if (snapshot != nullptr) {
        snapshotResult = snapshot(snapshotBytes, sizeof(snapshotBytes));
        if (snapshotResult == kTestRunSnapshotMagic) {
            // Shares bit 5 with the symbol check above on purpose: the 32-byte record
            // layout and every existing reader stay unchanged, and the on-device record
            // from build 20260910680000 was decoded with bit 5 set. So bit 5 means "the
            // Runtime answered with the snapshot magic", which is what a reader reports.
            flags |= static_cast<std::uint32_t>(HostPluginFlags::SnapshotResolved);
        }
    }

    // The plugin thread's half of the "who can write" measurement: it runs here, on the
    // thread SaltyNX called, before anything else in this run.
    const ThreadWriteResult pluginThreadWrite =
        WriteThreadProbe(api, kThreadWriteProbePath, kThreadWriteProbeText,
                         sizeof(kThreadWriteProbeText));
    if (pluginThreadWrite.openOk != 0) {
        flags |= static_cast<std::uint32_t>(HostPluginFlags::PluginThreadFileWriteOk);
    }

    if (report) {
        AppendBridgeRecord(api, flags);
        AppendThreadWriteRecord(api, flags, pluginThreadWrite);
        // 交接线程启动取证：创建/启动返回码。放在加载阶段的记录里，因此"这一轮有没有
        // 尝试启动线程、返回码是多少"一定能读出来（不依赖线程自己能否跑起来、能否写盘）。
        AppendHandoverStartRecord(api, flags, helperStarted ? 1U : 0U);
        // The snapshot is the only place the Runtime states how far its diagnostics attach
        // got. It rides in the file that is already proven writable, so a missing record
        // can only mean the plugin never reached this point.
        AppendSnapshotRecord(api, kBridgePath, flags, /*readIndex=*/1, snapshotResult,
                             snapshotBytes, sizeof(snapshotBytes));
        // Independent capability probe: can this channel create a file at all?
        AppendCreateProbe(api, flags);
        AppendMappingRecord(api, flags, own, ownIdentity, sameSize, sameSizeCount,
                            runningIndex, runningSelf, visited);
        AppendModuleCopyRecord(api, flags, own, ownIdentity, work.copies, work.scan,
                               work.runningIdentity, work.runningSelf, work.plantedSlot,
                               work.slotDelta);
    }
    // （原先这里有一个 `if (helperStarted) flags |= HandoverThreadStarted;`，已上移到
    //   记录写入之前——见上面那一处注释。）
    return flags;
}

} // namespace isaac::runtime::host_plugin
