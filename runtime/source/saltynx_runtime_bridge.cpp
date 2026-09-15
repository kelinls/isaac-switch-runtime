#include "mod_persistence.hpp"
#include "manager_update_hook_audit.hpp"
#include "persistence_event_journal.hpp"
#include "persistence_trace.hpp"

#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)
#include "program/saltynx_state_container.hpp"
#include <array>
#endif
#if defined(EXL_LAYERED_RUNTIME)
#include "diagnostics/diagnostic_event_ids.hpp"
#include "diagnostics/diagnostic_session.hpp"
#include "test_run_observer.hpp"
#include "infrastructure/saltynx/runtime_host_api_service.hpp"
#include "ports/runtime_host_api.hpp"
#include "interfaces/lua/engine_frame_probe.hpp"
#include "saltynx_probe_break.hpp"
#include "saltynx_self_journal.hpp"
// libnx, for the self-journal's own file path. Every other write in this module
// goes through the file table the host plugin registers; the self-journal exists
// precisely because that table has never been observed to reach the code that
// runs, so it must not depend on it at all.
#include <switch.h>
// `LuaRuntime` 里探针需要的两项，**只声明、不包含 `lua_runtime.hpp`**：那个头经 `common.hpp`
// 拉进 vendored 的 `lib/nx/nx.h`，与本文件必需的 devkitpro `<switch.h>` 在同一个编译单元里
// 直接冲突（`R_FAILED`/`MAKERESULT`/`SplConfigItem` 重定义，`-Werror` 下必失败）。声明与
// `runtime/source/lua_runtime.hpp` 逐字一致，链接期按名字对上即可。
namespace LuaRuntime {
std::uint32_t PostUpdateCount();
[[nodiscard]] std::uint32_t ReadLuaTableNumber(const char* globalName, const char* fieldName,
                                              double* value);
}  // namespace LuaRuntime
#endif
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>
#include <string_view>

// Diagnostics records must carry a non-zero Build ID so the offline parser can
// filter by build; the build system supplies it, and the fallback keeps a
// standalone compile honest instead of silently writing Build ID 0.
#ifndef EXL_TEST_BUILD_ID
#define EXL_TEST_BUILD_ID 1ULL
#endif
static_assert(static_cast<std::uint64_t>(EXL_TEST_BUILD_ID) != 0,
              "EXL_TEST_BUILD_ID must be non-zero");

// 这个 TU 只在**分层构建**里存在：`EXL_LAYERED_RUNTIME` 的副本给 hook_manager/runtime_entry
// 提供诊断桥（`TryAttachDiagnostics`/`WriteSelfJournal`/`PublishRuntimeSelf`/`EnsureThreadTls`），
// 而非分层副本按设计是空 TU（见 hook_manager.cpp 里 `ISAAC_RUNTIME_HAS_DIAGNOSTICS_BRIDGE` 的注释）。
// 但文件里有些块（`WriteDiagnosticsGate`、`SaltyNxDiagnosticFilePort` 等）历史上没有并进守卫，
// 于是 `make probe DIAGNOSTIC_STAGE=<n>`（非分层）会一片 "was not declared in this scope"。
// 这里把整段实现体收进同一个守卫，使"非分层 = 空 TU"这条设计真的成立（2026-09-13 修）。
#if defined(EXL_LAYERED_RUNTIME)
namespace {
#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)
std::uintptr_t g_salty_file_api[4]{};
#endif

constexpr std::uint64_t kFileApiRegistered = 0x49534141435F4631ULL;
constexpr std::uint64_t kObserverStateExported = 0x49534141435F4F31ULL;
#if !defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)
// Keep the production Runtime's verified PT_LOAD page boundaries unchanged.
[[gnu::used, gnu::section(".rodata.layout_padding")]] const std::uint8_t
    kProductionLayoutPadding[0x40]{};
#endif
#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)
constexpr char kRuntimeIoPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-runtime-io.bin";
constexpr std::uint8_t kRuntimeIoExpected[] = "ISAAC_STAGE141!!";
constexpr char kStateContainerPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-state.bin";
constexpr saltynx_state::StateRecord kStateRecords[] = {
    {0x47414D455F535441ULL, 0x5452454153555245ULL, 7},
    {0x52554E54494D455FULL, 0x434F4D5041545F31ULL, 42},
};
#endif

#if defined(EXL_LAYERED_RUNTIME)
// The registered file table, plus the one-shot attachment flag. Nothing is
// constructed here: this translation unit's static initialisation must stay
// constant-initialisable.
std::uintptr_t g_diagnosticFileApi[4]{};
bool g_diagnosticSessionCreated = false;
bool g_diagnosticGateStoppedEarly = false;
// How many times the plugin registered the file table in this session. Used to
// defer our own file I/O out of the registration window.
std::uint32_t g_diagnosticRegistrationCalls = 0;
// Registration handshake. `IsaacModRuntime_RegisterSaltyFileApi` returns a constant
// acknowledgement, and a constant proves nothing: it is returned by the same call that
// stores the pointers, so it cannot distinguish "the store landed in the state the
// running code reads" from "it landed somewhere else". This word is written by the
// store and read back through a separate export, so the plugin can prove the
// registration actually reached the live table. Reads as 'HAND' ("HANDshake") once it
// has.
constexpr std::uint32_t kFileApiHandshake = 0x48414E44U;
std::uint32_t g_diagnosticHandshake = 0;
constexpr char kDiagnosticsPath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-events.bin";
#endif

using SaltyFopen = void* (*)(const char*, const char*);
using SaltyFread = std::size_t (*)(void*, std::size_t, std::size_t, void*);
using SaltyFwrite = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using SaltyFclose = int (*)(void*);

// Wait this many registrations before touching files or constructing objects.
constexpr std::uint32_t kDiagnosticAttachAfterRegistrations = 4;

constexpr std::uint32_t kDiagnosticsGateMagic = 0x3147444341415349ULL & 0xFFFFFFFFU;  // "ISAACDG1" low half
void WriteDiagnosticsGate(std::uint32_t state) {
    if (g_diagnosticFileApi[0] == 0 || g_diagnosticFileApi[2] == 0 || g_diagnosticFileApi[3] == 0) {
        // The file table is incomplete, so nothing can be written at all; the
        // absence of any record is then the signal.
        g_diagnosticGateStoppedEarly = true;
        return;
    }
    const auto open = reinterpret_cast<SaltyFopen>(g_diagnosticFileApi[0]);
    const auto write = reinterpret_cast<SaltyFwrite>(g_diagnosticFileApi[2]);
    const auto close = reinterpret_cast<SaltyFclose>(g_diagnosticFileApi[3]);
    std::uint8_t record[64]{};
    const char magic[] = "ISAACDG1";
    for (std::size_t index = 0; index < 8; ++index) {
        record[index] = static_cast<std::uint8_t>(magic[index]);
    }
    // version 1, record size, then the state word: an offline reader only needs the
    // 8-byte magic and the state to classify the failure.
    record[8] = 1;
    for (std::size_t index = 0; index < 4; ++index) {
        record[12 + index] = static_cast<std::uint8_t>(state >> (8 * index));
        record[16 + index] = static_cast<std::uint8_t>(
            static_cast<std::uint32_t>(sizeof(record)) >> (8 * index));
    }
    void* file = open(kDiagnosticsPath, "ab");
    if (file == nullptr) {
        return;
    }
    static_cast<void>(write(record, 1, sizeof(record), file));
    static_cast<void>(close(file));
}


// Adapts the file table the SaltyNX host plugin registers onto `IFilePort`, so
// the diagnostics journal only ever sees the port interface (design §11.3).
//
// The four pointers are read LIVE from `g_diagnosticFileApi` on every use, never
// cached. The previous version captured them in the constructor, which contradicted
// its own comment and had a consequence that cost several hardware rounds: the port is
// constructed on the first attach attempt, and if that attempt happens before the host
// plugin registers, the port keeps four nulls forever. `AttachFilePort` then fails with
// `InvalidState` on every later frame, `WriteDiagnosticsGate` reads the live table but
// is only reached through the same failing path, and nothing anywhere reports it --
// so "the Runtime armed itself before the plugin arrived" and "the Runtime never ran"
// look identical from the outside.
class SaltyNxDiagnosticFilePort final : public isaac::runtime::IFilePort {
public:
    SaltyNxDiagnosticFilePort() noexcept = default;

    // True when the registered table is complete enough to write a journal. A
    // missing entry reports `InvalidState` from `Open`, which the session turns
    // into a visible AttachFailed event rather than a silent absence of evidence.
    [[nodiscard]] bool usable() const noexcept {
        return OpenFn() != nullptr && WriteFn() != nullptr && CloseFn() != nullptr;
    }

    isaac::runtime::Status Open(std::string_view path, isaac::runtime::FileMode mode,
                                void** handle) noexcept override {
        const SaltyFopen open = OpenFn();
        if (!usable() || handle == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidState};
        }
        // Only append-style writes are needed: the journal must never truncate
        // evidence from an earlier Build ID.
        const char* modeText = mode == isaac::runtime::FileMode::Write ? "ab" : "rb";
        // The path is a compile-time literal in the caller; NUL-terminate into a
        // bounded buffer instead of assuming the view is.
        if (path.size() >= path_.size()) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::CapacityExceeded};
        }
        std::memcpy(path_.data(), path.data(), path.size());
        path_[path.size()] = '\0';
        void* opened = open(path_.data(), modeText);
        if (opened == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::IoFailure};
        }
        *handle = opened;
        return isaac::runtime::Status::Ok();
    }

    isaac::runtime::Status Read(void* handle, std::uint8_t* target, std::size_t capacity,
                                std::size_t* readCount) noexcept override {
        const SaltyFread read = ReadFn();
        if (read == nullptr || handle == nullptr || target == nullptr || readCount == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidArgument};
        }
        *readCount = read(target, 1, capacity, handle);
        return isaac::runtime::Status::Ok();
    }

    isaac::runtime::Status Write(void* handle, const std::uint8_t* bytes, std::size_t count,
                                 std::size_t* writtenCount) noexcept override {
        const SaltyFwrite write = WriteFn();
        if (write == nullptr || handle == nullptr || bytes == nullptr || writtenCount == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidArgument};
        }
        // fwrite returns the item count; one byte per item makes that the byte count.
        *writtenCount = write(bytes, 1, count, handle);
        return isaac::runtime::Status::Ok();
    }

    isaac::runtime::Status Close(void* handle) noexcept override {
        const SaltyFclose close = CloseFn();
        if (close == nullptr || handle == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidArgument};
        }
        return close(handle) == 0 ? isaac::runtime::Status::Ok()
                                  : isaac::runtime::Status{isaac::runtime::StatusCode::IoFailure};
    }

private:
    static SaltyFopen OpenFn() noexcept {
        return reinterpret_cast<SaltyFopen>(g_diagnosticFileApi[0]);
    }
    static SaltyFread ReadFn() noexcept {
        return reinterpret_cast<SaltyFread>(g_diagnosticFileApi[1]);
    }
    static SaltyFwrite WriteFn() noexcept {
        return reinterpret_cast<SaltyFwrite>(g_diagnosticFileApi[2]);
    }
    static SaltyFclose CloseFn() noexcept {
        return reinterpret_cast<SaltyFclose>(g_diagnosticFileApi[3]);
    }

    // Only the path buffer is per-object state; everything else is read live.
    std::array<char, 192> path_{};
};

// Process-lifetime storage for the journal port and its session. The objects are
// constructed by `TryAttachDiagnostics` and deliberately never destroyed: nothing
// in this unit may add an `__cxa_atexit` registration.
alignas(16) std::uint8_t g_diagnosticPortStorage[sizeof(SaltyNxDiagnosticFilePort)]{};
alignas(16) std::uint8_t g_diagnosticSessionStorage[sizeof(isaac::runtime::DiagnosticSession)]{};



}

#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)
extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_SaltyBridgeProbe() {
    return 0x49534141435F4231ULL;
}
#endif

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_RegisterSaltyFileApi(std::uintptr_t open, std::uintptr_t read,
                                     std::uintptr_t write, std::uintptr_t close) {
    if (open == 0 || read == 0 || write == 0 || close == 0) {
        return 0;
    }

#if defined(EXL_LAYERED_RUNTIME)
    // Compatibility wrapper: publish the same table through the versioned host
    // API so both entry points enforce one validation rule. A rejected table
    // must not reach the legacy consumers below.
    isaac::runtime::HostFileApiV1 table{};
    table.open = reinterpret_cast<isaac::runtime::HostFileOpenFn>(open);
    table.read = reinterpret_cast<isaac::runtime::HostFileReadFn>(read);
    table.write = reinterpret_cast<isaac::runtime::HostFileWriteFn>(write);
    table.close = reinterpret_cast<isaac::runtime::HostFileCloseFn>(close);
    if (isaac::runtime::HostApiService().RegisterFilePort(table) !=
        isaac::runtime::HostRegisterResult::Accepted) {
        return 0;
    }
#endif

#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)
    g_salty_file_api[0] = open;
    g_salty_file_api[1] = read;
    g_salty_file_api[2] = write;
    g_salty_file_api[3] = close;
#endif
    ModPersistence::ConfigureFileApi(reinterpret_cast<SaltyFopen>(open),
                                     reinterpret_cast<SaltyFread>(read),
                                     reinterpret_cast<SaltyFwrite>(write),
                                     reinterpret_cast<SaltyFclose>(close));
    PersistenceTrace::ConfigureFileApi(reinterpret_cast<SaltyFopen>(open),
                                       reinterpret_cast<SaltyFwrite>(write),
                                       reinterpret_cast<SaltyFclose>(close));
    ManagerUpdateHookAudit::ConfigureFileApi(reinterpret_cast<SaltyFopen>(open),
                                              reinterpret_cast<SaltyFwrite>(write),
                                              reinterpret_cast<SaltyFclose>(close));
    PersistenceEventJournal::ConfigureFileApi(reinterpret_cast<PersistenceEventJournal::OpenFn>(open),
                                               reinterpret_cast<PersistenceEventJournal::WriteFn>(write),
                                               reinterpret_cast<PersistenceEventJournal::CloseFn>(close));
    PersistenceEventJournal::Mark(PersistenceEventJournal::Event::FileApiAccepted);
    PersistenceEventJournal::DrainPendingBounded();

#if defined(EXL_LAYERED_RUNTIME)
    // Only record the table here. Attaching the diagnostics journal happens later,
    // from the game's own update callback: this function runs inside the SaltyNX
    // service call that registers the table, and doing file I/O, object
    // construction and a virtual call in that window crashed on hardware
    // (crash report 01789053980, Instruction Abort at the adapter's Open entry).
    g_diagnosticFileApi[0] = open;
    g_diagnosticFileApi[1] = read;
    g_diagnosticFileApi[2] = write;
    g_diagnosticFileApi[3] = close;
    g_diagnosticHandshake = kFileApiHandshake;
    ++g_diagnosticRegistrationCalls;
    // Discriminating experiment: from the registration stack itself, do nothing but
    // open/write/close one status record. No object construction, no virtual call,
    // no libc path of our own choosing beyond the registered service, so this cannot
    // reproduce the crash that report 01789053980 showed (that was construction plus
    // a virtual call in this window). If the record appears, the earlier silence was
    // "the code never ran"; if it does not, creating a brand-new file through this
    // service is what fails.
    if (g_diagnosticRegistrationCalls == kDiagnosticAttachAfterRegistrations) {
        WriteDiagnosticsGate(static_cast<std::uint32_t>(TestRunDiagnosticsAttach::NoPort));
    }
#endif
    return kFileApiRegistered;
}

#if defined(EXL_LAYERED_RUNTIME)
// Arms the diagnostics journal once, from a safe point.
//
// Called from the main thread's update callback, after Hook installation and
// after the file table exists. Before that moment every event stays in the
// in-memory ring, which is exactly what the two-stage design asks for.
//
// Construction is deliberately plain static storage with a manual flag: function
// local statics with dynamic initialisation would emit a guard variable and an
// `__cxa_atexit` registration, changing the startup code shape that hardware
// verification pinned down.
// Writes one 64-byte status record into the diagnostics file using the registered
// file functions, before any other gate. This is the only capture path that does
// not depend on the host plugin reading anything back, which is what every earlier
// attempt relied on. The record uses the legacy protocol's 64-byte shape with a
// non-matching magic, so existing readers skip it as a resynchronisation region
// while the header itself spells out the outcome.
void TryAttachDiagnostics() {
    // Explicit storage plus a manual flag: a function-local static with a
    // non-trivial destructor would make the compiler emit `__cxa_atexit`
    // registrations and a `__tcf_` thunk, changing the pinned startup code shape.
    auto* port = reinterpret_cast<SaltyNxDiagnosticFilePort*>(g_diagnosticPortStorage);
    auto* session = reinterpret_cast<isaac::runtime::DiagnosticSession*>(g_diagnosticSessionStorage);

    // If this entry point never runs, the snapshot keeps NotAttempted, which is how
    // "the update callback never reached the diagnostics path" becomes provable.
    if (!g_diagnosticSessionCreated) {
        TestRunObserver::MarkDiagnosticsAttach(
            static_cast<std::uint32_t>(TestRunDiagnosticsAttach::SessionMissing));
        new (g_diagnosticPortStorage) SaltyNxDiagnosticFilePort();
        new (g_diagnosticSessionStorage) isaac::runtime::DiagnosticSession(
            static_cast<std::uint64_t>(EXL_TEST_BUILD_ID),
            isaac::runtime::DiagnosticOrigin::RuntimeModule);
        g_diagnosticSessionCreated = true;
        // Proof-of-life event: if the update callback never reaches here, no record
        // of any kind will appear anywhere, which is itself the diagnosis.
        const isaac::runtime::DiagnosticEvent entered = session->MakeEvent(
            isaac::runtime::DiagnosticSubsystem::Bootstrap,
            isaac::runtime::kBootstrapEventRuntimeEntered, isaac::runtime::DiagnosticPhase::None);
        static_cast<void>(session->Publish(entered));
        session->PublishHealth();
    }

    // Retry every update until the journal is armed. A single attempt would give up
    // permanently if the file table arrives after the first frame; retrying keeps
    // the early-boot events queued in the ring and turns into a visible
    // AttachFailed event as long as no usable port exists.
    if (!session->filePortAttached()) {
        const isaac::runtime::Status attached = session->AttachFilePort(*port, kDiagnosticsPath);
        // Publish the outcome where the host plugin already reads it, so a failed
        // attach is provable on hardware instead of looking like "nothing ran".
        const std::uint32_t state =
            attached.ok()
                ? static_cast<std::uint32_t>(TestRunDiagnosticsAttach::Attached)
                : (attached.code() == isaac::runtime::StatusCode::InvalidState
                       ? static_cast<std::uint32_t>(TestRunDiagnosticsAttach::NoPort)
                       : static_cast<std::uint32_t>(TestRunDiagnosticsAttach::OpenFailed));
        TestRunObserver::MarkDiagnosticsAttach(state);
        WriteDiagnosticsGate(state);
        // Keep trying while the port is unusable: a later frame may see a complete
        // file table, and the events stay queued in the ring meanwhile.
        return;
    }
    TestRunObserver::MarkDiagnosticsAttach(
        static_cast<std::uint32_t>(TestRunDiagnosticsAttach::Attached));

    // Bounded drain, on the same safe point the legacy protocols flush from.
    for (int guard = 0; guard < static_cast<int>(isaac::runtime::kDiagnosticMaxRecordsPerFlush); ++guard) {
        if (session->Flush() == 0) {
            break;
        }
    }
}
#endif

// Entry point for the Manager update hook.
//
// Deliberately HIDDEN rather than default-visibility. With a default-visibility
// declaration the linker cannot prove the call binds locally, so it routes every call
// through a `.plt` stub whose GOT slot is a lazy-binding `R_AARCH64_JUMP_SLOT`: the slot
// starts at the linker's `PLT0` address (0x4acf0 here) and only becomes the real target
// if the module's rtld resolves it. That slot's fallback path reads `*(0x74B80)`, a
// resolver pointer that carries no relocation and holds 0 -- so a single missed
// resolution silently turns the call into a jump through null. Nothing here needs the
// symbol outside the module, so hidden visibility removes the indirection entirely and
// makes the call a direct `bl`.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_TryAttachDiagnostics() {
#if defined(EXL_LAYERED_RUNTIME)
    TryAttachDiagnostics();
#endif
}

#if defined(EXL_LAYERED_RUNTIME)
// Runtime identity, published from `exl_main`.
//
// `exl_main` is the one place that proves *this* copy of the module was initialised: it
// is the module's entry, and it is where the worker, the hooks and the Lua runtime come
// from. The host plugin reads this word through the ordinary symbol lookup, so if it
// reads zero while a Mod demonstrably runs, then the symbol lookup and the game are
// looking at two different copies of the module -- which is the only remaining
// explanation for a plugin that observes an empty table in a Runtime that is busy
// dispatching callbacks.
std::uintptr_t g_runtimeSelfAddress = 0;
constexpr std::uint64_t kRuntimeIdentityMagic = 0x3144494341415349ULL;  // "ISAACID1"

extern "C" __attribute__((visibility("default"))) void
IsaacModRuntime_PublishRuntimeSelf(std::uintptr_t self) {
    g_runtimeSelfAddress = self;
}

// Returns [0..1] the address `exl_main` published for this copy, and [2..3] the address
// of this function inside this same copy. A caller that knows the offset between the two
// symbols can therefore derive this copy's load base and compare it with the base of the
// copy its own symbol lookup returned.
extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetRuntimeIdentity(std::uint32_t* output, std::size_t wordCount) {
    if (output == nullptr || wordCount < 4) {
        return 0;
    }
    const auto self = static_cast<std::uint64_t>(g_runtimeSelfAddress);
    const auto here = reinterpret_cast<std::uintptr_t>(&IsaacModRuntime_GetRuntimeIdentity);
    output[0] = static_cast<std::uint32_t>(self & 0xFFFFFFFFULL);
    output[1] = static_cast<std::uint32_t>(self >> 32);
    output[2] = static_cast<std::uint32_t>(here & 0xFFFFFFFFULL);
    output[3] = static_cast<std::uint32_t>(here >> 32);
    return kRuntimeIdentityMagic;
}

// The Runtime's own view of the diagnostics pipeline.
//
// Every earlier attempt to explain the missing `isaac-runtime-events.bin` had to assume
// something about this state from the outside: that the registered file table really
// reached `g_diagnosticFileApi`, that the session was constructed, that the gate did or
// did not stop early. None of it was ever observable. These words are that state:
//   [0] bitmask of non-zero g_diagnosticFileApi entries (bit0 open, bit1 read,
//       bit2 write, bit3 close)
//   [1] 1 once the diagnostic session was constructed
//   [2] how many times the host plugin registered a file table
//   [3] 1 when WriteDiagnosticsGate stopped early on an incomplete table
//   [4] the registration handshake: reads 'HAND' once the store that `RegisterSaltyFileApi`
//       performed is visible to this reader. This is the word that separates "the plugin
//       registered into the table the running code reads" from "the acknowledgement came
//       back but the store went somewhere the running code never looks".
constexpr std::size_t kDiagnosticsStateWordCount = 5;
constexpr std::uint64_t kDiagnosticsStateMagic = 0x3153444341415349ULL;  // "ISAACDS1"

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetDiagnosticsState(std::uint32_t* output, std::size_t wordCount) {
    if (output == nullptr || wordCount < kDiagnosticsStateWordCount) {
        return 0;
    }
    std::uint32_t mask = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        if (g_diagnosticFileApi[index] != 0) {
            mask |= 1U << index;
        }
    }
    output[0] = mask;
    output[1] = g_diagnosticSessionCreated ? 1U : 0U;
    output[2] = g_diagnosticRegistrationCalls;
    output[3] = g_diagnosticGateStoppedEarly ? 1U : 0U;
    output[4] = g_diagnosticHandshake;
    return kDiagnosticsStateMagic;
}

// ---------------------------------------------------------------------------
// Self-journal: the running copy writes its own evidence straight to the SD card.
//
// Every earlier design routed the Runtime's writes through the file table the
// SaltyNX host plugin registers, and `isaac-runtime-events.bin` has never appeared.
// `GetDiagnosticsState` was added to find out why, and on hardware it reports the
// opposite of what that pipeline needs: the runtime the plugin talks to holds the
// table (mask 0xf, handshake 'HAND') and still reports sessionCreated 0,
// gateStoppedEarly 0 and zero hook results -- so that copy has never entered
// `exl_main` or a hook callback. Yet the module's hooks demonstrably do run: crash
// report 01789112265 returns through `runtime+0x13ac`, inside
// `ManagerUpdateHook::Callback`. A copy that holds the table and never ran is not
// the copy that dispatches.
//
// This journal removes the host plugin from the path entirely. The copy that is
// executing writes through the `fs` service itself, states who it is (its own
// writer address, and the `exl_main` address it published) and what it can see
// (table mask, session flag, gate flag, hook words), and appends that record to a
// file the plugin never touches. Nothing has to be trusted: if the file appears,
// the running copy can write, and the record names the copy that wrote it.
//
// The record is composed after the file is open, so its Result codes are the ones
// this attempt produced. A failed open writes nothing, which is why the same
// counters are also published through `GetSelfJournalState` on the plugin's read
// path.
namespace self_journal {
constexpr char kPath[] = "/SaltySD/plugins/010021C000B6A000/isaac-runtime-self.bin";
constexpr std::size_t kRecordSize = 144;
constexpr std::uint64_t kMagic = 0x3153524341415349ULL;       // "ISAACRS1"
constexpr std::uint64_t kStateMagic = 0x31534A4341415349ULL;  // "ISAACJS1"
constexpr std::size_t kStateWordCount = 16;
// Marker ids live in `saltynx_self_journal.hpp` because the call sites are in two
// other translation units; the ids below are the same wire format.
constexpr std::size_t kMarkerCount = 4;
// Three attempts per marker: enough to survive the `fs` service or the card
// arriving late, few enough that no frame performs file I/O after the first
// moments of a session.
constexpr std::uint32_t kAttemptLimit = 3;
constexpr std::size_t kResultCount = 6;
// 0 = not attempted, 1 = the sm and fs sessions are up, 2 = service init failed.
constexpr std::uint32_t kServicesUnattempted = 0;
constexpr std::uint32_t kServicesReady = 1;
constexpr std::uint32_t kServicesFailed = 2;
// Where devkitA64 keeps the libc thread pointer: a word inside the thread-local
// region the kernel maps for every thread, addressed off `tpidrro_el0`.
constexpr std::uintptr_t kThreadTlsPointerOffset = 0x1f8;
constexpr std::uint32_t kTlsAlreadySet = 0;
constexpr std::uint32_t kTlsInstalledHere = 1;
constexpr std::uint32_t kTlsNoThreadRegion = 2;

// Plain static storage with constant initialisers only: this translation unit's
// static initialisation must not need a runtime constructor.
std::uint32_t g_attempts[kMarkerCount]{};
std::uint32_t g_records[kMarkerCount]{};
std::uint32_t g_totalRecords = 0;
std::uint32_t g_lastMarker = 0;
std::uint32_t g_serviceState = kServicesUnattempted;
std::uint32_t g_tlsState = kTlsAlreadySet;
std::uint32_t g_lastResults[kResultCount]{};
bool g_servicesReady = false;

// The libc thread block this module hands to devkitA64's libc when a thread has
// none. Zero-initialised on purpose: the only field libnx's IPC path touches
// through it is `errno`, and "no error" is exactly the state to start from.
alignas(16) std::uint8_t g_threadTlsBlock[0x200]{};

void PutWord(std::uint8_t* target, std::uint32_t value) {
    for (std::size_t index = 0; index < 4; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (8 * index));
    }
}

void PutDoubleWord(std::uint8_t* target, std::uint64_t value) {
    PutWord(target, static_cast<std::uint32_t>(value & 0xFFFFFFFFULL));
    PutWord(target + 4, static_cast<std::uint32_t>(value >> 32));
}

std::uint32_t Checksum(const std::uint8_t* bytes, std::size_t count) {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < count; ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}
}  // namespace self_journal

// Gives the calling thread a libc thread pointer if it does not have one.
//
// devkitA64's `__aarch64_read_tp` loads the thread pointer from
// `[tpidrro_el0 + 0x1f8]` and the caller dereferences it immediately, so a null slot
// is a fault, not a stale value. That is the crash this module already paid for once:
// its former `thread_local` re-entry guard died at `runtime+0x440c` with fault address
// 0 (reports 01789099948 and 01789112265), because a game thread never runs a
// devkitA64 CRT and leaves the slot zero. libnx's `fs` implementation calls the same
// helper on every request (`_fsFsOpenCommon`, `_fsCmdGetSession`, `fsFsCreateFile`,
// `fsFileWrite`, `fsFileGetSize`), so the slot is filled before the first libnx call
// rather than discovered by crashing.
//
// The slot is only written when it is still zero, so a thread that already has a
// thread pointer -- SaltyNX's own plugin thread, for instance -- is left alone.
bool EnsureThreadTlsPointer() {
    std::uint8_t* threadRegion = nullptr;
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(threadRegion));
    if (threadRegion == nullptr) {
        self_journal::g_tlsState = self_journal::kTlsNoThreadRegion;
        return false;
    }
    auto* slot = reinterpret_cast<volatile std::uintptr_t*>(
        threadRegion + self_journal::kThreadTlsPointerOffset);
    if (*slot != 0) {
        self_journal::g_tlsState = self_journal::kTlsAlreadySet;
        return true;
    }
    *slot = reinterpret_cast<std::uintptr_t>(self_journal::g_threadTlsBlock);
    self_journal::g_tlsState = self_journal::kTlsInstalledHere;
    return true;
}

// Entry point for `exl_main`, so the thread pointer is in place before the Runtime
// touches anything that devkitA64 compiled. Hidden: the call binds with a direct `bl`.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_EnsureThreadTls() {
    static_cast<void>(EnsureThreadTlsPointer());
}

// Brings up the `sm` and `fs` sessions this module needs to reach the SD card
// without a file table. libnx reports `AlreadyInitialized` for a session that is
// already active, which is a success for this purpose; anything else is recorded
// rather than retried into a loop.
bool EnsureSelfJournalServices() {
    if (!EnsureThreadTlsPointer()) {
        self_journal::g_serviceState = self_journal::kServicesFailed;
        return false;
    }
    if (self_journal::g_servicesReady) {
        return true;
    }
    const Result smResult = smInitialize();
    const Result fsResult = fsInitialize();
    self_journal::g_lastResults[0] = static_cast<std::uint32_t>(smResult);
    self_journal::g_lastResults[1] = static_cast<std::uint32_t>(fsResult);
    const bool fsUsable =
        R_SUCCEEDED(fsResult) ||
        fsResult == MAKERESULT(Module_Libnx, LibnxError_AlreadyInitialized);
    self_journal::g_serviceState =
        fsUsable ? self_journal::kServicesReady : self_journal::kServicesFailed;
    self_journal::g_servicesReady = fsUsable;
    return fsUsable;
}

extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_FillHookJournalWords(std::uint32_t* output);

// Appends one self-journal record. Hidden, so the callers in `runtime_entry.cpp`
// and `hook_manager.cpp` bind with a direct `bl` instead of a `.plt` stub.
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_WriteSelfJournal(std::uint32_t marker) {
    using namespace self_journal;
    if (marker == 0 || marker >= kMarkerCount) {
        return;
    }
    if (g_attempts[marker] >= kAttemptLimit) {
        return;
    }
    const std::uint32_t attempt = ++g_attempts[marker];
    g_lastMarker = marker;
    if (!EnsureSelfJournalServices()) {
        return;
    }

    FsFileSystem filesystem{};
    const Result openFileSystem = fsOpenSdCardFileSystem(&filesystem);
    g_lastResults[2] = static_cast<std::uint32_t>(openFileSystem);
    if (R_FAILED(openFileSystem)) {
        return;
    }

    FsFile file{};
    Result openFile = fsFsOpenFile(&filesystem, kPath, FsOpenMode_Write | FsOpenMode_Append, &file);
    Result create = 0;
    if (R_FAILED(openFile)) {
        // The first attempt of a session is also the one that has to create the file;
        // `fsFsOpenFile` never does, and this libnx has no named result for "already
        // exists", so the create result is recorded and the retry decides: if the retry
        // opens the file, an earlier attempt already created it.
        create = fsFsCreateFile(&filesystem, kPath, static_cast<s64>(kRecordSize), 0);
        openFile = fsFsOpenFile(&filesystem, kPath, FsOpenMode_Write | FsOpenMode_Append, &file);
    }
    g_lastResults[3] = static_cast<std::uint32_t>(openFile);
    g_lastResults[4] = static_cast<std::uint32_t>(create);
    if (R_FAILED(openFile)) {
        fsFsClose(&filesystem);
        return;
    }

    std::uint32_t hookWords[5]{};
    IsaacModRuntime_FillHookJournalWords(hookWords);

    std::uint8_t record[kRecordSize]{};
    const char magic[] = "ISAACRS1";
    for (std::size_t index = 0; index < 8; ++index) {
        record[index] = static_cast<std::uint8_t>(magic[index]);
    }
    PutWord(record + 8, 1);  // record version
    PutWord(record + 12, marker);
    PutWord(record + 16, attempt);
    PutWord(record + 20, g_records[marker]);
    PutDoubleWord(record + 24, static_cast<std::uint64_t>(
                                   reinterpret_cast<std::uintptr_t>(&IsaacModRuntime_WriteSelfJournal)));
    PutDoubleWord(record + 32, static_cast<std::uint64_t>(g_runtimeSelfAddress));
    PutDoubleWord(record + 40, static_cast<std::uint64_t>(EXL_TEST_BUILD_ID));
    std::uint32_t mask = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        if (g_diagnosticFileApi[index] != 0) {
            mask |= 1U << index;
        }
    }
    PutWord(record + 48, mask);
    PutWord(record + 52, g_diagnosticSessionCreated ? 1U : 0U);
    PutWord(record + 56, g_diagnosticRegistrationCalls);
    PutWord(record + 60, g_diagnosticGateStoppedEarly ? 1U : 0U);
    PutWord(record + 64, g_diagnosticHandshake);
    for (std::size_t index = 0; index < 5; ++index) {
        PutWord(record + 68 + index * 4, hookWords[index]);
    }
    for (std::size_t index = 0; index < kResultCount; ++index) {
        PutWord(record + 88 + index * 4, g_lastResults[index]);
    }
    PutWord(record + 120, g_totalRecords);
    PutWord(record + 124, g_tlsState);
    PutDoubleWord(record + 128, armGetSystemTick());
    PutWord(record + 136, 0);  // reserved
    PutWord(record + 140, Checksum(record, 140));

    s64 offset = 0;
    if (R_FAILED(fsFileGetSize(&file, &offset)) || offset < 0) {
        offset = 0;
    }
    const Result write = fsFileWrite(&file, static_cast<u64>(offset), record, kRecordSize,
                                     FsWriteOption_Flush);
    g_lastResults[5] = static_cast<std::uint32_t>(write);
    fsFileClose(&file);
    fsFsClose(&filesystem);
    if (R_SUCCEEDED(write)) {
        ++g_records[marker];
        ++g_totalRecords;
    }
}

// ---------------------------------------------------------------------------
// Who can actually write a file in this process?
//
// 2026-09-11: the file table now reaches the copy of the module that runs (`fileApiMask`
// 0xf in that copy's own registers), the diagnostics session is created, and yet
// `isaac-runtime-events.bin` still does not exist. So the failing step is the file
// operation itself, and the question is which thread may perform it:
//
//   * the host plugin's own thread provably can -- it appends its records and creates
//     `isaac-runtime-create-probe.bin` in this very directory;
//   * the Runtime runs on the game's threads, and the only Runtime-side write that ever
//     succeeded (`isaac-runtime-runtime-io.bin`) was one the plugin triggered on its own
//     thread. SaltyNX's `SaltySDCore_fopen` is a wrapper around the game's libc, and
//     whether it works off SaltyNX's thread was never measured.
//
// This probe measures exactly that, in one session, from both sides, each through its own
// channel: the game thread's result travels in the probe break's registers, and the plugin
// thread's result is a file only that thread writes. Two files in the same directory and
// one crash report settle it.
constexpr char kThreadWriteProbePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-write-game-thread.bin";
constexpr char kThreadWriteProbeText[] = "ISAACGT1 game-thread file write probe\n";
constexpr std::size_t kThreadWriteProbeLimit = 3;

// 0 while the table was still incomplete, then one per attempt.
std::uint32_t g_threadWriteTableMissing = 0;
std::uint32_t g_threadWriteAttempts = 0;
std::uint32_t g_threadWriteOpenOk = 0;
std::uint32_t g_threadWriteWritten = 0;
std::uint32_t g_threadWriteCloseOk = 0;
std::uintptr_t g_threadWriteOpenFn = 0;
std::uintptr_t g_threadWriteWriteFn = 0;
std::uintptr_t g_threadWriteCloseFn = 0;

// Called from the game's own hook callbacks. Bounded, and it never retries after the
// limit, so a failing file operation cannot turn into per-frame I/O.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeFileWriteFromHook() {
    if (g_threadWriteAttempts >= kThreadWriteProbeLimit) {
        return;
    }
    if (g_diagnosticFileApi[0] == 0 || g_diagnosticFileApi[2] == 0 || g_diagnosticFileApi[3] == 0) {
        ++g_threadWriteTableMissing;
        return;
    }
    ++g_threadWriteAttempts;
    g_threadWriteOpenFn = g_diagnosticFileApi[0];
    g_threadWriteWriteFn = g_diagnosticFileApi[2];
    g_threadWriteCloseFn = g_diagnosticFileApi[3];
    const auto open = reinterpret_cast<SaltyFopen>(g_diagnosticFileApi[0]);
    const auto write = reinterpret_cast<SaltyFwrite>(g_diagnosticFileApi[2]);
    const auto close = reinterpret_cast<SaltyFclose>(g_diagnosticFileApi[3]);
    // "wb" on purpose: this probe must also create the file, not only append to one.
    void* file = open(kThreadWriteProbePath, "wb");
    g_threadWriteOpenOk = file != nullptr ? 1U : 0U;
    if (file == nullptr) {
        return;
    }
    g_threadWriteWritten = static_cast<std::uint32_t>(
        write(kThreadWriteProbeText, 1, sizeof(kThreadWriteProbeText) - 1, file));
    g_threadWriteCloseOk = close(file) == 0 ? 1U : 0U;
}

// A note the host plugin leaves in this module, and that the probe break reports.
//
// 2026-09-11: a thread created by the plugin cannot perform file I/O. The hand-over thread
// crashed with `Result 0x2A8` and `PC = 0` while a `fopen(..., "ab")` was in flight: the
// game's libc is not built for a thread the game did not create, and its thread-local state
// is null there. So the helper's findings travel in memory instead, and the deliberate
// break -- which runs on the game's own thread -- carries them out in registers.
constexpr std::size_t kPluginNoteWordCount = 8;
std::uint32_t g_pluginNote[kPluginNoteWordCount]{};
constexpr std::uint64_t kPluginNoteMagic = 0x314E504341415349ULL;  // "ISAACPN1"

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_SetPluginNote(const std::uint32_t* words, std::size_t wordCount) {
    if (words == nullptr || wordCount < kPluginNoteWordCount) {
        return 0;
    }
    for (std::size_t index = 0; index < kPluginNoteWordCount; ++index) {
        g_pluginNote[index] = words[index];
    }
    return kPluginNoteMagic;
}

// The address of one `g_diagnosticFileApi` entry, for a caller in another image.
//
// The host plugin reaches a copy of this module whose `exl_main` never ran, so the
// file table it registers lands where no running code will look. The plugin can only
// find the copy that does run by scanning the process for this module's bytes, and
// then it needs to know where that copy's table lives. It cannot see this static
// array, so it asks this copy for the slot address and applies the difference to the
// copy it found: same file, same layout.
extern "C" __attribute__((visibility("default"))) std::uintptr_t
IsaacModRuntime_GetFileApiSlot(std::uint32_t index) {
    if (index >= 4) {
        return 0;
    }
    return reinterpret_cast<std::uintptr_t>(&g_diagnosticFileApi[index]);
}

// The Runtime's own view of the self-journal, on the same read path the plugin
// already uses. It reports this copy's attempts and the `fs` Result codes, which is
// the only way to see a failure that wrote no record at all.
//
//   [0..1]   update-callback attempts, records
//   [2..3]   render-callback attempts, records
//   [4..5]   exl_main attempts, records
//   [6..11]  the last attempt's Result codes: smInitialize, fsInitialize,
//            fsOpenSdCardFileSystem, fsFsOpenFile, fsFsCreateFile, fsFileWrite
//   [12]     total records this copy has written
//   [13]     the marker of the last attempt
//   [14]     0 not attempted, 1 services ready, 2 service init failed
//   [15]     libc thread pointer: 0 the thread already had one, 1 this module
//            installed a private block, 2 the thread-local region was unreadable
extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetSelfJournalState(std::uint32_t* output, std::size_t wordCount) {
    using namespace self_journal;
    if (output == nullptr || wordCount < kStateWordCount) {
        return 0;
    }
    output[0] = g_attempts[isaac::runtime::kSelfJournalUpdateMarker];
    output[1] = g_records[isaac::runtime::kSelfJournalUpdateMarker];
    output[2] = g_attempts[isaac::runtime::kSelfJournalRenderMarker];
    output[3] = g_records[isaac::runtime::kSelfJournalRenderMarker];
    output[4] = g_attempts[isaac::runtime::kSelfJournalExlMainMarker];
    output[5] = g_records[isaac::runtime::kSelfJournalExlMainMarker];
    for (std::size_t index = 0; index < kResultCount; ++index) {
        output[6 + index] = g_lastResults[index];
    }
    output[12] = g_totalRecords;
    output[13] = g_lastMarker;
    output[14] = g_serviceState;
    output[15] = g_tlsState;
    return kStateMagic;
}

#if defined(EXL_PROBE_BREAK)
// The loader's symbol lookup, handed over by the host plugin after the Runtime's entry.
//
// With this the Runtime resolves names for itself -- the game's own file functions and
// SaltyNX's IPC entries -- instead of depending only on what the plugin passed in.
std::uintptr_t g_symbolLookup = 0;

extern "C" __attribute__((visibility("default"))) void
IsaacModRuntime_SetSymbolLookup(std::uintptr_t lookup) {
    g_symbolLookup = lookup;
}

using SaltyPrintfFn = std::uint32_t (*)(const char*, ...);
using SaltyGetBidFn = std::uint64_t (*)(void);

// Re-open SaltyNX's service session, and write through it from the game's thread.
//
// SaltyNX's own source settles why every attempt after plugin load failed: the injected
// payload calls `SaltySD_Init()` before it starts working and `SaltySD_Deinit()` when it
// finishes -- `SaltySD_Term()` (which sends EndSession) followed by `svcCloseHandle(saltysd)`
// (`saltysd_core/source/main.c:449` and `:496`). Every later IPC on that handle fails with
// the kernel's "invalid handle" (0xE401), the sysmodule logs nothing, and it prints
// `serviceEndSession` / `done accepting service calls` right after the host plugin's writes.
// What looked like a thread restriction was a session that had been closed.
//
// `SaltySD_Init()` only connects to the named port, and the sysmodule accepts sessions in a
// loop, so the Runtime can open its own session and use the file functions again -- from the
// game's thread, which is where the Runtime lives. This probe does exactly that and reports
// every Result, with the file it creates as the primary evidence.
using SaltyInitFn = std::uint32_t (*)(void);
using SaltyFopenFn = void* (*)(const char*, const char*);
using SaltyFwriteFn = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using SaltyFcloseFn = int (*)(void*);

constexpr char kReinitProbePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-write-after-reinit.bin";
constexpr char kReinitProbeText[] = "ISAACRI1 session reopened, file written from the game thread\n";
constexpr char kReinitProbeLog[] = "ISAACRI1 runtime reopened the SaltySD session\n";

std::uint32_t g_reinitAttempts = 0;
std::uint32_t g_reinitLookupMask = 0;
std::uint32_t g_reinitResult = 0xFFFFFFFFU;
std::uint32_t g_reinitPrintfResult = 0xFFFFFFFFU;
std::uint32_t g_reinitOpenOk = 0;
std::uint32_t g_reinitWritten = 0;
std::uint32_t g_reinitCloseOk = 0;
std::uintptr_t g_reinitInitAddress = 0;

// Called from the game's own hook callbacks, once.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeSaltyReinit() {
    if (g_reinitAttempts != 0 || g_symbolLookup == 0) {
        return;
    }
    using FindSymbolFn = std::uintptr_t (*)(const char*);
    const auto find = reinterpret_cast<FindSymbolFn>(g_symbolLookup);
    const std::uintptr_t initAddress = find("SaltySD_Init");
    const std::uintptr_t printfAddress = find("SaltySD_printf");
    const std::uintptr_t fopenAddress = find("SaltySDCore_fopen");
    const std::uintptr_t fwriteAddress = find("SaltySDCore_fwrite");
    const std::uintptr_t fcloseAddress = find("SaltySDCore_fclose");
    g_reinitInitAddress = initAddress;
    g_reinitLookupMask = (initAddress != 0 ? 1U : 0U) | (printfAddress != 0 ? 2U : 0U) |
                         (fopenAddress != 0 ? 4U : 0U) | (fwriteAddress != 0 ? 8U : 0U) |
                         (fcloseAddress != 0 ? 16U : 0U);
    if (g_reinitLookupMask != 31U) {
        return;
    }
    g_reinitAttempts = 1;
    // Our own session. The payload closed its own; the sysmodule keeps accepting.
    g_reinitResult = reinterpret_cast<SaltyInitFn>(initAddress)();
    if (g_reinitResult != 0) {
        return;
    }
    g_reinitPrintfResult =
        reinterpret_cast<SaltyPrintfFn>(printfAddress)(kReinitProbeLog);
    const auto open = reinterpret_cast<SaltyFopenFn>(fopenAddress);
    const auto write = reinterpret_cast<SaltyFwriteFn>(fwriteAddress);
    const auto close = reinterpret_cast<SaltyFcloseFn>(fcloseAddress);
    void* file = open(kReinitProbePath, "wb");
    g_reinitOpenOk = file != nullptr ? 1U : 0U;
    if (file == nullptr) {
        return;
    }
    g_reinitWritten =
        static_cast<std::uint32_t>(write(kReinitProbeText, 1, sizeof(kReinitProbeText) - 1, file));
    g_reinitCloseOk = close(file) == 0 ? 1U : 0U;
}

// Can the Runtime talk to SaltyNX's sysmodule at all?
//
// Reading SaltyNX's own source settled what the file functions are: `SaltySDCore_fopen` does
// no I/O itself, it hands the request to SaltyNX's sysmodule over IPC (`Transact` ->
// `ipcDispatch(saltysd)`), and the sysmodule performs the read or write. Its sysmodule logs
// every open, and the device log shows every write the host plugin performs -- but not one
// line for any attempt made from the game's thread, and no "reserved only for Core" refusal
// either. So the request never leaves the payload: the failure is in the IPC call itself, not
// a permission check on the far side.
//
// `SaltySD_printf` and `SaltySD_GetBID` are the cheapest way to test that, and they return
// their Result instead of hiding it: a zero with a line in `saltysd.log` means the Runtime can
// use this channel, which would also give the journal somewhere on the SD card to go.
constexpr char kSaltyIpcProbeText[] = "ISAACIPC1 runtime->sysmodule ipc probe\n";
constexpr std::uint32_t kSaltyIpcAttemptLimit = 3;

std::uint32_t g_saltyIpcAttempts = 0;
std::uint32_t g_saltyIpcLookupMask = 0;
std::uint32_t g_saltyPrintfResult = 0xFFFFFFFFU;
std::uint64_t g_saltyGetBid = 0;

// Called from the game's own hook callbacks. Hidden, so the call binds with a direct `bl`.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeSaltyIpc() {
    if (g_saltyIpcAttempts >= kSaltyIpcAttemptLimit || g_symbolLookup == 0) {
        return;
    }
    using FindSymbolFn = std::uintptr_t (*)(const char*);
    const auto find = reinterpret_cast<FindSymbolFn>(g_symbolLookup);
    const std::uintptr_t printfAddress = find("SaltySD_printf");
    const std::uintptr_t getBidAddress = find("SaltySD_GetBID");
    g_saltyIpcLookupMask = (printfAddress != 0 ? 1U : 0U) | (getBidAddress != 0 ? 2U : 0U);
    if (g_saltyIpcLookupMask != 3U) {
        return;
    }
    ++g_saltyIpcAttempts;
    const auto saltyPrintf = reinterpret_cast<SaltyPrintfFn>(printfAddress);
    const auto saltyGetBid = reinterpret_cast<SaltyGetBidFn>(getBidAddress);
    g_saltyPrintfResult = saltyPrintf(kSaltyIpcProbeText);
    g_saltyGetBid = saltyGetBid();
}

// The Runtime's third route to the SD card: the game's own file functions, by name.
//
// Both other routes are measured dead. SaltyNX's file wrappers work on SaltyNX's thread and
// return null on this one (`openOk = 0`, three attempts), and libnx's `fs` service fails at
// its first step and then blocks (`smInitialize` 0x00010801, `fsInitialize` 0x0000E401, and
// `fsOpenSdCardFileSystem` never returning). What has never been tried is calling the
// *game's* `fopen` directly, resolved by name through SaltyNX's symbol lookup, with
// SaltyNX's wrapper taken out of the path. The game's own libc has its devices mounted --
// the game writes its saves through it -- so this is the last route that would let the
// Runtime write its journal by itself.
//
// The lookup address arrives from the host plugin after the Runtime's entry (a store before
// that would be erased by `.bss` clearing, which is what the file table taught us), and
// every result travels out through the probe break.
using NativeFopen = void* (*)(const char*, const char*);
using NativeFwrite = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using NativeFclose = int (*)(void*);

constexpr char kNativeWritePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-write-game-thread-native.bin";
constexpr char kNativeWriteText[] = "ISAACGN1 game libc file write probe\n";
constexpr std::size_t kNativeWriteAttemptLimit = 3;

std::uintptr_t g_nativeFopen = 0;
std::uintptr_t g_nativeFwrite = 0;
std::uintptr_t g_nativeFclose = 0;
std::uint32_t g_nativeWriteAttempts = 0;
std::uint32_t g_nativeWriteLookupMask = 0;
std::uint32_t g_nativeWriteOpenOk = 0;
std::uint32_t g_nativeWriteWritten = 0;
std::uint32_t g_nativeWriteCloseOk = 0;

// Called from the game's own hook callbacks. Hidden, so the call is a direct `bl`; it is
// bounded, so a failing route cannot become per-frame I/O.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeNativeFileWrite() {
    if (g_nativeWriteAttempts >= kNativeWriteAttemptLimit || g_symbolLookup == 0) {
        return;
    }
    using FindSymbolFn = std::uintptr_t (*)(const char*);
    const auto find = reinterpret_cast<FindSymbolFn>(g_symbolLookup);
    if (g_nativeFopen == 0) {
        g_nativeFopen = find("fopen");
    }
    if (g_nativeFwrite == 0) {
        g_nativeFwrite = find("fwrite");
    }
    if (g_nativeFclose == 0) {
        g_nativeFclose = find("fclose");
    }
    g_nativeWriteLookupMask = (g_nativeFopen != 0 ? 1U : 0U) |
                              (g_nativeFwrite != 0 ? 2U : 0U) |
                              (g_nativeFclose != 0 ? 4U : 0U);
    if (g_nativeWriteLookupMask != 7U) {
        return;
    }
    ++g_nativeWriteAttempts;
    const auto open = reinterpret_cast<NativeFopen>(g_nativeFopen);
    const auto write = reinterpret_cast<NativeFwrite>(g_nativeFwrite);
    const auto close = reinterpret_cast<NativeFclose>(g_nativeFclose);
    void* file = open(kNativeWritePath, "wb");
    g_nativeWriteOpenOk = file != nullptr ? 1U : 0U;
    if (file == nullptr) {
        return;
    }
    g_nativeWriteWritten = static_cast<std::uint32_t>(
        write(kNativeWriteText, 1, sizeof(kNativeWriteText) - 1, file));
    g_nativeWriteCloseOk = close(file) == 0 ? 1U : 0U;
}

// Step-by-step `fs` probe: which step of the Runtime's own write path fails, and why.
//
// The module's independent route to the SD card goes through libnx's `fs` service and never
// touches the host plugin's file table, so it is the one route a thread restriction cannot
// break. It never worked, and on 2026-09-11 running it on the game's own thread **hung the
// game**: the picture froze at eight seconds into a room with the music still playing, and
// nothing was reported, because a blocked thread does not fault.
//
// So the sequence runs on a thread of this module's own. A step that blocks then takes only
// that thread with it, the game keeps running, and the probe break on the game's thread still
// reports how far the sequence got and every Result it obtained
// (`IsaacModRuntime_ProbeFsSteps` below breaks with them).
//
// The first step installs a libc thread pointer if the thread has none, for the measured
// reason in `EnsureThreadTlsPointer`: devkitA64's thread-pointer read dereferences
// `[tpidrro_el0 + 0x1f8]`, and a fresh thread leaves that slot at zero.
constexpr char kFsProbePath[] = "/SaltySD/plugins/010021C000B6A000/isaac-runtime-fsprobe.bin";
constexpr char kFsProbeText[] = "ISAACFS1 fs step probe\n";
constexpr std::uint64_t kFsProbeMagic = 0x3153464341415349ULL;  // "ISAACFS1"

std::uint32_t g_fsProbeStarted = 0;
std::uint32_t g_fsProbeDone = 0;
// Frames since the probe thread was started. The break must happen even when one of the
// calls never comes back, so the wait is bounded by frames rather than by the thread.
std::uint32_t g_fsProbeCalls = 0;
constexpr std::uint32_t kFsProbeGraceFrames = 600;
// Written before each call and left at the call that did not come back.
std::uint32_t g_fsProbeStep = 0;
std::uint32_t g_fsProbeSm = 0xFFFFFFFFU;         // smInitialize
std::uint32_t g_fsProbeFs = 0xFFFFFFFFU;         // fsInitialize
std::uint32_t g_fsProbeOpenFs = 0xFFFFFFFFU;     // fsOpenSdCardFileSystem
std::uint32_t g_fsProbeOpenFile = 0xFFFFFFFFU;   // fsFsOpenFile of the probe path
std::uint32_t g_fsProbeCreate = 0xFFFFFFFFU;     // fsFsCreateFile, when the open failed
std::uint32_t g_fsProbeOpenAgain = 0xFFFFFFFFU;  // fsFsOpenFile after creating
std::uint32_t g_fsProbeWrite = 0xFFFFFFFFU;      // fsFileWrite
std::uint32_t g_fsProbeShut = 0xFFFFFFFFU;       // reached the close steps
std::uint32_t g_fsProbeTls = 0xFFFFFFFFU;        // 0 the thread already had a thread pointer
alignas(16) std::uint8_t g_fsProbeStack[0x8000]{};

void FsProbeThread(void*) {
    EnsureThreadTlsPointer();
    g_fsProbeTls = self_journal::g_tlsState;

    g_fsProbeStep = 1;
    g_fsProbeSm = static_cast<std::uint32_t>(smInitialize());
    g_fsProbeStep = 2;
    g_fsProbeFs = static_cast<std::uint32_t>(fsInitialize());
    g_fsProbeStep = 3;
    FsFileSystem filesystem{};
    g_fsProbeOpenFs = static_cast<std::uint32_t>(fsOpenSdCardFileSystem(&filesystem));
    if (R_SUCCEEDED(static_cast<Result>(g_fsProbeOpenFs))) {
        g_fsProbeStep = 4;
        FsFile file{};
        g_fsProbeOpenFile = static_cast<std::uint32_t>(
            fsFsOpenFile(&filesystem, kFsProbePath, FsOpenMode_Write | FsOpenMode_Append, &file));
        if (R_FAILED(static_cast<Result>(g_fsProbeOpenFile))) {
            g_fsProbeStep = 5;
            g_fsProbeCreate = static_cast<std::uint32_t>(fsFsCreateFile(
                &filesystem, kFsProbePath, static_cast<s64>(sizeof(kFsProbeText)), 0));
            g_fsProbeStep = 6;
            g_fsProbeOpenAgain = static_cast<std::uint32_t>(fsFsOpenFile(
                &filesystem, kFsProbePath, FsOpenMode_Write | FsOpenMode_Append, &file));
            g_fsProbeOpenFile = g_fsProbeOpenAgain;
        }
        if (R_SUCCEEDED(static_cast<Result>(g_fsProbeOpenFile))) {
            g_fsProbeStep = 7;
            g_fsProbeWrite = static_cast<std::uint32_t>(
                fsFileWrite(&file, 0, kFsProbeText, sizeof(kFsProbeText) - 1,
                            FsWriteOption_Flush));
            g_fsProbeStep = 8;
            fsFileClose(&file);  // returns void in this libnx: the step index is the evidence
        }
        g_fsProbeStep = 9;
        fsFsClose(&filesystem);  // returns void as well
    }
    g_fsProbeStep = 10;
    g_fsProbeShut = 0;
    g_fsProbeDone = 1;
    // Park: a raw thread entry has no return address.
    for (;;) {
        svcSleepThread(1'000'000'000LL);
    }
}

bool StartFsProbeThread() {
    if (g_fsProbeStarted != 0) {
        return true;
    }
    g_fsProbeStarted = 1;
    Handle thread{};
    const auto stackTop = reinterpret_cast<void*>(g_fsProbeStack + sizeof(g_fsProbeStack));
    if (R_FAILED(svcCreateThread(&thread, reinterpret_cast<void*>(&FsProbeThread), nullptr,
                                 stackTop, 0x2C, -2))) {
        g_fsProbeStarted = 0;
        return false;
    }
    if (R_FAILED(svcStartThread(thread))) {
        return false;
    }
    return true;
}

// Starts that thread once, and reports what it has reached. Hidden, so the call site in the
// update callback binds with a direct `bl`.
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeFsSteps() {
    if (g_fsProbeStarted == 0) {
        static_cast<void>(StartFsProbeThread());
        return;
    }
    ++g_fsProbeCalls;
    if (g_fsProbeDone == 0 && g_fsProbeCalls < kFsProbeGraceFrames) {
        // Still running -- or blocked in one of the calls. The grace window is what makes a
        // blocked call reportable: the break below then names the step it stopped at.
        return;
    }

    // One code per register, in call order, so nothing has to be unpacked or guessed. A
    // non-zero step with the calls after it still at 0xFFFFFFFF is the call that did not
    // come back.
    register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
    register std::uint64_t x1 __asm__("x1") = kFsProbeMagic;
    register std::uint64_t x2 __asm__("x2") = g_fsProbeStep;
    register std::uint64_t x3 __asm__("x3") = g_fsProbeSm;
    register std::uint64_t x4 __asm__("x4") = g_fsProbeFs;
    register std::uint64_t x5 __asm__("x5") = g_fsProbeOpenFs;
    register std::uint64_t x6 __asm__("x6") = g_fsProbeOpenFile;
    register std::uint64_t x7 __asm__("x7") = g_fsProbeCreate;
    register std::uint64_t x8 __asm__("x8") = g_fsProbeWrite;
    register std::uint64_t x9 __asm__("x9") = g_fsProbeShut;
    register std::uint64_t x10 __asm__("x10") = g_fsProbeTls;
    register std::uint64_t x11 __asm__("x11") = g_fsProbeOpenAgain;
    register std::uint64_t x12 __asm__("x12") = g_fsProbeDone;
    register std::uint64_t x13 __asm__("x13") =
        static_cast<std::uint64_t>(self_journal::g_threadTlsBlock[0]);
    __asm__ volatile("svc 0x7f"
                     :
                     : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5), "r"(x6), "r"(x7),
                       "r"(x8), "r"(x9), "r"(x10), "r"(x11), "r"(x12), "r"(x13)
                     : "memory");
    __builtin_unreachable();
}

// The probe break: reaching the probe point ends the session on purpose.
//
// The rule this implements and the register payload are documented in
// `saltynx_probe_break.hpp`. One shot per session, so a session never crashes twice
// and a crash report can always be attributed to exactly one probe point.
std::uint32_t g_probeBreakTaken = 0;

extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeBreak(std::uint32_t marker) {
    if (g_probeBreakTaken != 0) {
        return;
    }
    g_probeBreakTaken = marker;

    std::uint32_t hookWords[5]{};
    IsaacModRuntime_FillHookJournalWords(hookWords);
    std::uint32_t mask = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        if (g_diagnosticFileApi[index] != 0) {
            mask |= 1U << index;
        }
    }
    const std::uint64_t state =
        (static_cast<std::uint64_t>(mask) & 0xFFU) |
        ((g_diagnosticSessionCreated ? 1ULL : 0ULL) << 8) |
        ((g_diagnosticGateStoppedEarly ? 1ULL : 0ULL) << 9) |
        ((static_cast<std::uint64_t>(g_diagnosticRegistrationCalls) & 0xFFULL) << 16) |
        ((static_cast<std::uint64_t>(self_journal::g_serviceState) & 0xFFULL) << 24);
    const std::uint64_t hooks =
        (static_cast<std::uint64_t>(hookWords[0]) & 0xFFULL) |
        ((static_cast<std::uint64_t>(hookWords[1]) & 0xFFULL) << 8) |
        ((static_cast<std::uint64_t>(hookWords[2]) & 0xFFULL) << 16) |
        ((static_cast<std::uint64_t>(hookWords[3]) & 0xFFULL) << 24);

    register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
    register std::uint64_t x1 __asm__("x1") = isaac::runtime::kProbeBreakMagic;
    register std::uint64_t x2 __asm__("x2") =
        (static_cast<std::uint64_t>(marker) << 32) |
        (static_cast<std::uint64_t>(self_journal::g_totalRecords) & 0xFFFFFFFFULL);
    register std::uint64_t x3 __asm__("x3") = static_cast<std::uint64_t>(
        reinterpret_cast<std::uintptr_t>(&IsaacModRuntime_WriteSelfJournal));
    register std::uint64_t x4 __asm__("x4") = static_cast<std::uint64_t>(g_runtimeSelfAddress);
    register std::uint64_t x5 __asm__("x5") = state;
    register std::uint64_t x6 __asm__("x6") = hooks;
    register std::uint64_t x7 __asm__("x7") = static_cast<std::uint64_t>(g_diagnosticHandshake);
    // The self-journal's own diagnostics, so one crash report also says which `fs` step
    // failed when no journal file appears. Two Result codes per register.
    // Re-opening SaltyNX's session and writing through it: the Results of `SaltySD_Init`
    // and `SaltySD_printf`, then the open/write/close outcome of the file it created.
    register std::uint64_t x8 __asm__("x8") =
        (static_cast<std::uint64_t>(g_reinitLookupMask) & 0xFFULL) |
        ((static_cast<std::uint64_t>(g_reinitAttempts) & 0xFFULL) << 8) |
        ((static_cast<std::uint64_t>(g_reinitResult) & 0xFFFFFFFFULL) << 16);
    register std::uint64_t x9 __asm__("x9") = g_reinitPrintfResult;
    register std::uint64_t x10 __asm__("x10") =
        (static_cast<std::uint64_t>(g_reinitOpenOk) & 0xFFULL) |
        ((static_cast<std::uint64_t>(g_reinitWritten) & 0xFFFFULL) << 8) |
        ((static_cast<std::uint64_t>(g_reinitCloseOk) & 0xFFULL) << 24);
    register std::uint64_t x11 __asm__("x11") =
        (static_cast<std::uint64_t>(self_journal::g_tlsState) & 0xFFULL) |
        ((static_cast<std::uint64_t>(self_journal::g_lastMarker) & 0xFFULL) << 8) |
        ((static_cast<std::uint64_t>(hookWords[4]) & 0xFFFFULL) << 16) |
        ((static_cast<std::uint64_t>(self_journal::g_records[0]) & 0xFFULL) << 32) |
        ((static_cast<std::uint64_t>(self_journal::g_records[1]) & 0xFFULL) << 40) |
        ((static_cast<std::uint64_t>(self_journal::g_records[2]) & 0xFFULL) << 48);
    register std::uint64_t x12 __asm__("x12") =
        (static_cast<std::uint64_t>(g_diagnosticFileApi[0]) << 32) |
        (static_cast<std::uint64_t>(g_diagnosticFileApi[0] >> 32) & 0xFFFFFFFFULL);
    // The plugin's own findings, in the registers the crash report dumps after the ones
    // above: it cannot write them itself from the thread that produced them.
    register std::uint64_t x13 __asm__("x13") = g_pluginNote[0];
    register std::uint64_t x14 __asm__("x14") = g_pluginNote[1];
    register std::uint64_t x15 __asm__("x15") = g_pluginNote[2];
    register std::uint64_t x16 __asm__("x16") = g_pluginNote[3];
    register std::uint64_t x17 __asm__("x17") = g_pluginNote[4];
    register std::uint64_t x18 __asm__("x18") = g_pluginNote[5];
    register std::uint64_t x19 __asm__("x19") = g_pluginNote[6];
    register std::uint64_t x20 __asm__("x20") = g_pluginNote[7];
    // The game thread's own file-write probe: how many times the table was still incomplete,
    // how many attempts were made once it was complete, and what each step returned.
    register std::uint64_t x21 __asm__("x21") =
        (static_cast<std::uint64_t>(g_threadWriteTableMissing) & 0xFFULL) |
        ((static_cast<std::uint64_t>(g_threadWriteAttempts) & 0xFFULL) << 8) |
        ((static_cast<std::uint64_t>(g_threadWriteOpenOk) & 0xFFULL) << 16) |
        ((static_cast<std::uint64_t>(g_threadWriteCloseOk) & 0xFFULL) << 24);
    register std::uint64_t x22 __asm__("x22") = g_threadWriteWritten;
    // Which of SaltyNX's own IPC entries resolved, what `SaltySD_printf` returned, and the
    // title id `SaltySD_GetBID` answered with. A zero printf result plus a line in
    // `saltysd.log` means the Runtime can use this channel at all.
    register std::uint64_t x23 __asm__("x23") =
        (static_cast<std::uint64_t>(g_saltyIpcLookupMask) & 0xFFULL) |
        ((static_cast<std::uint64_t>(g_saltyIpcAttempts) & 0xFFULL) << 8);
    register std::uint64_t x24 __asm__("x24") = g_saltyPrintfResult;
    register std::uint64_t x25 __asm__("x25") = g_saltyGetBid;
    // The game's own file functions, resolved by name: which of the three resolved (bytes
    // 0..2), whether the open succeeded, whether the close did, and how many attempts.
    register std::uint64_t x26 __asm__("x26") =
        (static_cast<std::uint64_t>(g_nativeWriteLookupMask) & 0xFFULL) |
        ((static_cast<std::uint64_t>(g_nativeWriteOpenOk) & 0xFFULL) << 8) |
        ((static_cast<std::uint64_t>(g_nativeWriteCloseOk) & 0xFFULL) << 16) |
        ((static_cast<std::uint64_t>(g_nativeWriteAttempts) & 0xFFULL) << 24);
    register std::uint64_t x27 __asm__("x27") = g_nativeWriteWritten;
    register std::uint64_t x28 __asm__("x28") = g_nativeFopen;
    __asm__ volatile("svc 0x7f"
                     :
                     : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5), "r"(x6), "r"(x7),
                       "r"(x8), "r"(x9), "r"(x10), "r"(x11), "r"(x12), "r"(x13), "r"(x14),
                       "r"(x15), "r"(x16), "r"(x17), "r"(x18), "r"(x19), "r"(x20),
                       "r"(x21), "r"(x22), "r"(x23), "r"(x24), "r"(x25), "r"(x26), "r"(x27),
                       "r"(x28)
                     : "memory");
    __builtin_unreachable();
}

// 帧时间归因探针（"物品信息显示时卡顿"）：负载布局与 `tools/decode_engine_probe_payload.py`
// 一一对应。同一个会话内部做了 A/B（缓存开/缓存关），所以报告自带对照，不需要额外一轮真机
// 也能量出"每次引擎访问一次系统调用"到底吃掉多少帧时间。
//
// 字段约定（x1 是魔数，x0 是 `BreakReason_User`）：
//   x2  triggerReason | phasesDone << 32
//   x3  managedFrames（累计"真的派发过受管 POST_RENDER"的帧数）
//   x4  ticksPerMs（睡眠标定）
//   x5  ON.frames | OFF.frames << 32
//   x6/x7   ON/OFF attempts（进入可读性检查的次数）
//   x8/x9   ON/OFF syscalls（真正 `svcQueryMemory` 的次数）
//   x10/x11 ON/OFF syscallTicks（花在 `svcQueryMemory` 里的 tick）
//   x12/x13 ON/OFF cacheHits
//   x14/x15 ON/OFF frameTicks 合计
//   x16/x17 ON/OFF 单帧最大 tick
//   x18 ON/OFF 超过 20 ms 的帧数（各 32 位）
//   x19 ON/OFF 超过 33 ms 的帧数（各 32 位）
//   x20/x21 ON/OFF 派发耗时合计（tick）
//   x22 ON/OFF 绘制调用数（各 32 位）
//   x23 lastApiPrimary | filterMask << 32
//   x24 postRenderCount | postUpdateCount << 32
//       （`postRenderCount` 只在 15 号诊断阶段由 `MarkPostRender` 计数，帧归因探针构建里
//        恒为 0，所以这里直接给 0，不把该诊断阶段拖进依赖；`postUpdateCount` 是恒有的
//        `MarkPostUpdate` 计数，用来证明"这些诊断标记没有被调用过"）
//   x25 EID.GameRenderCount：低 32 = 读取原因（0 成功 / 1 全局不是表 / 2 没这个字段 /
//       3 字段不是 number），高 32 = 值（原因非 0 时为 0）
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_EngineProbeBreak() {
    double eidRenderCount = 0.0;
    // 用"全局表 + 字段"这条通道读 `EID.GameRenderCount`：`ReadLuaGlobalNumber` 在 2026-09-12
    // 被证伪（连读两次都答"没有"，而同一份报告里 update/render 回调进入次数是 900/878）。
    // 原因码一起进负载，读不到时也能分清是"EID 表没建出来""没这个字段"还是"字段不是数字"。
    const std::uint32_t eidRenderReason =
        LuaRuntime::ReadLuaTableNumber("EID", "GameRenderCount", &eidRenderCount);
    // 见上面 x24 的说明：`LuaRuntime::PostRenderCount()` 只在 `EXL_DIAGNOSTIC_STAGE == 15`
    // 下声明与定义（同阶段的 `MarkPostRender` 才会去累加 `g_PostRenderCount`），本探针构建
    // 里它必然读不到东西，所以不引用它。
    constexpr std::uint32_t kPostRenderCountUnavailable = 0U;
    const isaac::runtime::EngineFrameProbeSnapshot snapshot =
        isaac::runtime::EngineFrameProbeCapture(
            kPostRenderCountUnavailable, LuaRuntime::PostUpdateCount(), eidRenderReason,
            eidRenderReason == 0U ? static_cast<std::uint32_t>(eidRenderCount) : 0U);
    const auto& on = snapshot.cacheOn;
    const auto& off = snapshot.cacheOff;
    const auto pack32 = [](std::uint64_t low, std::uint64_t high) {
        return (low & 0xFFFFFFFFULL) | ((high & 0xFFFFFFFFULL) << 32);
    };

    register std::uint64_t x0 __asm__("x0") = 2;  // BreakReason_User
    register std::uint64_t x1 __asm__("x1") = 0x3156454341415349ULL;  // "ISAACEV1"
    register std::uint64_t x2 __asm__("x2") =
        (static_cast<std::uint64_t>(snapshot.triggerReason) & 0xFFFFFFFFULL) |
        ((static_cast<std::uint64_t>(snapshot.phasesDone) & 0xFFFFFFFFULL) << 32);
    register std::uint64_t x3 __asm__("x3") = snapshot.managedFrames;
    register std::uint64_t x4 __asm__("x4") = snapshot.ticksPerMs;
    register std::uint64_t x5 __asm__("x5") = pack32(on.frames, off.frames);
    register std::uint64_t x6 __asm__("x6") = on.attempts;
    register std::uint64_t x7 __asm__("x7") = off.attempts;
    register std::uint64_t x8 __asm__("x8") = on.syscalls;
    register std::uint64_t x9 __asm__("x9") = off.syscalls;
    register std::uint64_t x10 __asm__("x10") = on.syscallTicks;
    register std::uint64_t x11 __asm__("x11") = off.syscallTicks;
    register std::uint64_t x12 __asm__("x12") = on.cacheHits;
    register std::uint64_t x13 __asm__("x13") = off.cacheHits;
    register std::uint64_t x14 __asm__("x14") = on.frameTicks;
    register std::uint64_t x15 __asm__("x15") = off.frameTicks;
    register std::uint64_t x16 __asm__("x16") = on.maxFrameTicks;
    register std::uint64_t x17 __asm__("x17") = off.maxFrameTicks;
    register std::uint64_t x18 __asm__("x18") = pack32(on.framesOver20ms, off.framesOver20ms);
    register std::uint64_t x19 __asm__("x19") = pack32(on.framesOver33ms, off.framesOver33ms);
    register std::uint64_t x20 __asm__("x20") = on.dispatchTicks;
    register std::uint64_t x21 __asm__("x21") = off.dispatchTicks;
    register std::uint64_t x22 __asm__("x22") = pack32(on.drawCalls, off.drawCalls);
    register std::uint64_t x23 __asm__("x23") =
        (static_cast<std::uint64_t>(snapshot.lastApiPrimary) & 0xFFFFFFFFULL) |
        ((static_cast<std::uint64_t>(snapshot.filterMask) & 0xFFFFFFFFULL) << 32);
    register std::uint64_t x24 __asm__("x24") =
        pack32(snapshot.postRenderCount, snapshot.postUpdateCount);
    register std::uint64_t x25 __asm__("x25") =
        pack32(snapshot.eidRenderReason, snapshot.eidRenderCount);
    __asm__ volatile("svc 0x7f"
                     :
                     : "r"(x0), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5), "r"(x6), "r"(x7),
                       "r"(x8), "r"(x9), "r"(x10), "r"(x11), "r"(x12), "r"(x13), "r"(x14),
                       "r"(x15), "r"(x16), "r"(x17), "r"(x18), "r"(x19), "r"(x20),
                       "r"(x21), "r"(x22), "r"(x23), "r"(x24), "r"(x25)
                     : "memory");
    __builtin_unreachable();
}
#endif
#endif

#if defined(EXL_PERSISTENCE_TRACE)
extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_GetObserverState(std::uint32_t* output, std::size_t wordCount) {
    if (output == nullptr || wordCount < ManagerUpdateHookAudit::kObserverSnapshotWordCount) {
        return 0;
    }
    const ManagerUpdateHookAudit::ObserverSnapshot snapshot =
        ManagerUpdateHookAudit::CaptureObserverSnapshot();
    output[0] = snapshot.workerState;
    output[1] = snapshot.workerDetail;
    output[2] = snapshot.scanAttempt;
    output[3] = snapshot.sampleCount;
    output[4] = snapshot.callbackCount;
    output[5] = snapshot.flags;
    return kObserverStateExported;
}
#endif

#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)
extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_SaltyFileApiStatus() {
    std::uint64_t status = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        if (g_salty_file_api[index] != 0) {
            status |= std::uint64_t{1} << index;
        }
    }
    return status;
}

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_SaltyFileIoProbe() {
    for (std::uintptr_t address : g_salty_file_api) {
        if (address == 0) {
            return 0;
        }
    }

    const auto open = reinterpret_cast<SaltyFopen>(g_salty_file_api[0]);
    const auto read = reinterpret_cast<SaltyFread>(g_salty_file_api[1]);
    const auto write = reinterpret_cast<SaltyFwrite>(g_salty_file_api[2]);
    const auto close = reinterpret_cast<SaltyFclose>(g_salty_file_api[3]);
    std::uint64_t status = 0;

    void* write_file = open(kRuntimeIoPath, "wb");
    if (write_file == nullptr) {
        return status;
    }
    status |= 1U;
    if (write(kRuntimeIoExpected, sizeof(kRuntimeIoExpected) - 1, 1, write_file) != 1) {
        close(write_file);
        return status;
    }
    status |= 2U;
    if (close(write_file) != 0) {
        return status;
    }
    status |= 4U;

    void* read_file = open(kRuntimeIoPath, "rb");
    if (read_file == nullptr) {
        return status;
    }
    status |= 8U;
    std::uint8_t bytes[sizeof(kRuntimeIoExpected) - 1]{};
    if (read(bytes, sizeof(bytes), 1, read_file) == 1) {
        status |= 16U;
        bool matches = true;
        for (std::size_t index = 0; index < sizeof(bytes); ++index) {
            if (bytes[index] != kRuntimeIoExpected[index]) {
                matches = false;
                break;
            }
        }
        if (matches) {
            status |= 32U;
        }
    }
    if (close(read_file) == 0) {
        status |= 64U;
    }
    return status;
}

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_SaltyStateContainerProbe() {
    for (std::uintptr_t address : g_salty_file_api) {
        if (address == 0) {
            return 0;
        }
    }

    const auto open = reinterpret_cast<SaltyFopen>(g_salty_file_api[0]);
    const auto read = reinterpret_cast<SaltyFread>(g_salty_file_api[1]);
    const auto write = reinterpret_cast<SaltyFwrite>(g_salty_file_api[2]);
    const auto close = reinterpret_cast<SaltyFclose>(g_salty_file_api[3]);
    std::uint64_t status = 0;
    std::array<std::uint8_t, saltynx_state::kContainerSize> written{};
    saltynx_state::BuildStateContainer(written, 1, kStateRecords);

    void* write_file = open(kStateContainerPath, "wb");
    if (write_file == nullptr) {
        return status;
    }
    status |= 1U;
    if (write(written.data(), written.size(), 1, write_file) != 1) {
        close(write_file);
        return status;
    }
    status |= 2U;
    if (close(write_file) != 0) {
        return status;
    }
    status |= 4U;

    void* read_file = open(kStateContainerPath, "rb");
    if (read_file == nullptr) {
        return status;
    }
    status |= 8U;
    std::array<std::uint8_t, saltynx_state::kContainerSize> restored{};
    if (read(restored.data(), restored.size(), 1, read_file) == 1) {
        status |= 16U;
        if (saltynx_state::ValidateContainerEnvelope(restored)) {
            status |= 32U;
        }
        if (saltynx_state::ValidateContainerRecords(restored, kStateRecords)) {
            status |= 64U;
        }
    }
    if (close(read_file) == 0) {
        status |= 128U;
    }
    return status;
}

extern "C" __attribute__((visibility("default"))) std::uint64_t
IsaacModRuntime_SaltyStateContainerPersistProbe() {
    for (std::uintptr_t address : g_salty_file_api) {
        if (address == 0) {
            return 0;
        }
    }

    const auto open = reinterpret_cast<SaltyFopen>(g_salty_file_api[0]);
    const auto read = reinterpret_cast<SaltyFread>(g_salty_file_api[1]);
    const auto close = reinterpret_cast<SaltyFclose>(g_salty_file_api[3]);
    std::uint64_t status = 0;

    void* read_file = open(kStateContainerPath, "rb");
    if (read_file == nullptr) {
        return status;
    }
    status |= 1U;
    std::array<std::uint8_t, saltynx_state::kContainerSize> restored{};
    if (read(restored.data(), restored.size(), 1, read_file) == 1) {
        status |= 2U;
        if (saltynx_state::ValidateContainerEnvelope(restored)) {
            status |= 4U;
        }
        if (saltynx_state::ValidateContainerRecords(restored, kStateRecords)) {
            status |= 8U;
        }
    }
    if (close(read_file) == 0) {
        status |= 16U;
    }
    return status;
}
#endif
#endif  // EXL_LAYERED_RUNTIME（整段实现体）
