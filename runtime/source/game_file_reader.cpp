#include "game_file_reader.hpp"

#if !defined(EXL_DIAGNOSTIC_STAGE) || \
    (EXL_DIAGNOSTIC_STAGE == 11 || EXL_DIAGNOSTIC_STAGE == 12 || EXL_DIAGNOSTIC_STAGE == 13 || \
     EXL_DIAGNOSTIC_STAGE == 118)

#include "lib.hpp"
#include "lib/nx/kernel/svc.h"
#include "runtime_constants.hpp"

#include <array>
#include <cstring>
#include <limits>

namespace {

using ConstructFn = void (*)(void*);
using OpenReadFn = bool (*)(void*, const char*);
using GetLengthFn = int (*)(const void*);
using ReadFn = int (*)(void*, void*, int, int);
using CloseFn = void (*)(void*);
using DestroyFn = void (*)(void*);

bool IsMappedRxModuleCodeWindow(uintptr_t address, size_t length) {
    if (length == 0 || address > std::numeric_limits<uintptr_t>::max() - length) {
        return false;
    }

    MemoryInfo info{};
    u32 pageInfo = 0;
    if (R_FAILED(svcQueryMemory(&info, &pageInfo, address)) || info.size == 0) {
        return false;
    }

    const uintptr_t end = address + length;
    const uintptr_t mappingEnd = info.addr + info.size;
    return mappingEnd >= info.addr && address >= info.addr && end <= mappingEnd &&
           (info.type & MemState_Type) == MemType_ModuleCodeStatic && info.perm == Perm_Rx;
}

template <std::size_t ExpectedSize>
bool VerifyGameEntry(const TargetModule& module, uintptr_t offset,
                     const std::array<u8, ExpectedSize>& expected, uintptr_t* address) {
    if (address == nullptr || offset % 4 != 0 || offset > std::numeric_limits<uintptr_t>::max() - module.base) {
        return false;
    }

    const uintptr_t candidate = module.base + offset;
    if (candidate < module.base || !module.Contains(candidate, expected.size()) ||
        !IsMappedRxModuleCodeWindow(candidate, expected.size())) {
        return false;
    }
    if (std::memcmp(reinterpret_cast<const void*>(candidate), expected.data(), expected.size()) != 0) {
        return false;
    }

    *address = candidate;
    return true;
}

} // namespace

namespace GameFileReader {

bool VerifyBindings(const TargetModule& module, Bindings* bindings) {
    if (bindings == nullptr || module.buildId != kTargetBuildId) {
        return false;
    }

    Bindings verified{};
    if (!VerifyGameEntry(module, kGameFileConstructorFileOffset, kGameFileConstructorExpectedBytes, &verified.construct) ||
        !VerifyGameEntry(module, kGameFileOpenReadFileOffset, kGameFileOpenReadExpectedBytes, &verified.openRead) ||
        !VerifyGameEntry(module, kGameFileGetLengthFileOffset, kGameFileGetLengthExpectedBytes, &verified.getLength) ||
        !VerifyGameEntry(module, kGameFileReadFileOffset, kGameFileReadExpectedBytes, &verified.read) ||
        !VerifyGameEntry(module, kGameFileCloseFileOffset, kGameFileCloseExpectedBytes, &verified.close) ||
        !VerifyGameEntry(module, kGameFileDestructorFileOffset, kGameFileDestructorExpectedBytes, &verified.destroy)) {
        return false;
    }

    *bindings = verified;
    return true;
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
bool VerifyWriteBindings(const TargetModule& module, WriteBindings* bindings) {
    if (bindings == nullptr || module.buildId != kTargetBuildId) return false;
    WriteBindings verified{};
    if (!VerifyGameEntry(module, kGameFileConstructorFileOffset, kGameFileConstructorExpectedBytes, &verified.construct) ||
        !VerifyGameEntry(module, kGameFileOpenWriteFileOffset, kGameFileOpenWriteExpectedBytes, &verified.openWrite) ||
        !VerifyGameEntry(module, kGameFileWriteFileOffset, kGameFileWriteExpectedBytes, &verified.write) ||
        !VerifyGameEntry(module, kGameFileCloseFileOffset, kGameFileCloseExpectedBytes, &verified.close) ||
        !VerifyGameEntry(module, kGameFileDestructorFileOffset, kGameFileDestructorExpectedBytes, &verified.destroy)) {
        return false;
    }
    *bindings = verified;
    return true;
}

WriteDiagnosticResult WriteDiagnosticFile(const WriteBindings& bindings) {
    using OpenWriteFn = bool (*)(void*, const char*);
    using WriteFn = int (*)(void*, const void*, int, int);
    const auto construct = reinterpret_cast<ConstructFn>(bindings.construct);
    const auto openWrite = reinterpret_cast<OpenWriteFn>(bindings.openWrite);
    const auto write = reinterpret_cast<WriteFn>(bindings.write);
    const auto close = reinterpret_cast<CloseFn>(bindings.close);
    const auto destroy = reinterpret_cast<DestroyFn>(bindings.destroy);
    constexpr char kStage118Path[] = "save:/isaac_mod_stage118.bin";
    constexpr char kStage118Payload[] = "ISAAC_STAGE118!!";
    static_assert(sizeof(kStage118Payload) - 1 == 16);

    alignas(16) std::array<u8, kGameFileObjectSize> fileStorage{};
    construct(fileStorage.data());
    if (!openWrite(fileStorage.data(), kStage118Path)) {
        destroy(fileStorage.data());
        return WriteDiagnosticResult::OpenFailed;
    }
    const int written = write(fileStorage.data(), kStage118Payload, 1, sizeof(kStage118Payload) - 1);
    close(fileStorage.data());
    destroy(fileStorage.data());
    return written == static_cast<int>(sizeof(kStage118Payload) - 1)
               ? WriteDiagnosticResult::Success : WriteDiagnosticResult::WriteMismatch;
}
#endif

ReadResult ReadSentinel(const Bindings& bindings) {
    const auto construct = reinterpret_cast<ConstructFn>(bindings.construct);
    const auto openRead = reinterpret_cast<OpenReadFn>(bindings.openRead);
    const auto getLength = reinterpret_cast<GetLengthFn>(bindings.getLength);
    const auto read = reinterpret_cast<ReadFn>(bindings.read);
    const auto close = reinterpret_cast<CloseFn>(bindings.close);
    const auto destroy = reinterpret_cast<DestroyFn>(bindings.destroy);

    alignas(16) std::array<u8, kGameFileObjectSize> fileStorage{};
    std::array<u8, kRomfsSentinelLength> buffer{};

    construct(fileStorage.data());
    if (!openRead(fileStorage.data(), kRomfsSentinelPath)) {
        destroy(fileStorage.data());
        return ReadResult::OpenFailed;
    }
    const int length = getLength(fileStorage.data());
    if (length != kRomfsSentinelLength) {
        close(fileStorage.data());
        destroy(fileStorage.data());
        return ReadResult::LengthMismatch;
    }

    const int bytesRead = read(fileStorage.data(), buffer.data(), 1, kRomfsSentinelLength);
    if (bytesRead != static_cast<int>(kRomfsSentinelLength)) {
        close(fileStorage.data());
        destroy(fileStorage.data());
        return ReadResult::ReadMismatch;
    }
    const bool matches = std::memcmp(buffer.data(), kRomfsSentinelContents, kRomfsSentinelLength) == 0;
    close(fileStorage.data());
    destroy(fileStorage.data());
    return matches ? ReadResult::Success : ReadResult::ContentMismatch;
}

ScriptReadResult ReadScript(const Bindings& bindings,
                            std::array<u8, kRomfsLuaProbeMaximumLength>* buffer,
                            std::size_t* outputLength) {
    if (buffer == nullptr || outputLength == nullptr) {
        return ScriptReadResult::ReadMismatch;
    }

    const auto construct = reinterpret_cast<ConstructFn>(bindings.construct);
    const auto openRead = reinterpret_cast<OpenReadFn>(bindings.openRead);
    const auto getLength = reinterpret_cast<GetLengthFn>(bindings.getLength);
    const auto read = reinterpret_cast<ReadFn>(bindings.read);
    const auto close = reinterpret_cast<CloseFn>(bindings.close);
    const auto destroy = reinterpret_cast<DestroyFn>(bindings.destroy);

    alignas(16) std::array<u8, kGameFileObjectSize> fileStorage{};
    construct(fileStorage.data());
    if (!openRead(fileStorage.data(), kRomfsLuaProbePath)) {
        destroy(fileStorage.data());
        return ScriptReadResult::OpenFailed;
    }
    const int length = getLength(fileStorage.data());
    if (length <= 0 || length > static_cast<int>(kRomfsLuaProbeMaximumLength)) {
        close(fileStorage.data());
        destroy(fileStorage.data());
        return ScriptReadResult::LengthOutOfRange;
    }
    const int bytesRead = read(fileStorage.data(), buffer->data(), 1, length);
    close(fileStorage.data());
    destroy(fileStorage.data());
    if (bytesRead != length) {
        return ScriptReadResult::ReadMismatch;
    }
    *outputLength = static_cast<std::size_t>(length);
    return ScriptReadResult::Success;
}

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
TextReadResult ReadTextFile(const Bindings& bindings, const char* path,
                            u8* buffer, std::size_t capacity, std::size_t* outputLength) {
    if (path == nullptr || path[0] == '\0' || buffer == nullptr || capacity == 0 || outputLength == nullptr ||
        capacity > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        return TextReadResult::InvalidArgument;
    }

    const auto construct = reinterpret_cast<ConstructFn>(bindings.construct);
    const auto openRead = reinterpret_cast<OpenReadFn>(bindings.openRead);
    const auto getLength = reinterpret_cast<GetLengthFn>(bindings.getLength);
    const auto read = reinterpret_cast<ReadFn>(bindings.read);
    const auto close = reinterpret_cast<CloseFn>(bindings.close);
    const auto destroy = reinterpret_cast<DestroyFn>(bindings.destroy);

    alignas(16) std::array<u8, kGameFileObjectSize> fileStorage{};
    construct(fileStorage.data());
    if (!openRead(fileStorage.data(), path)) {
        destroy(fileStorage.data());
        return TextReadResult::OpenFailed;
    }
    const int length = getLength(fileStorage.data());
    if (length <= 0 || length > static_cast<int>(capacity)) {
        // 超长时也要把**观测到的长度**交出去：`getLength` 已经问过引擎了，这个数字是诊断
        // "文件放不进缓冲区"（而不是"文件不存在"）的唯一证据。真机上没有第二条通道能拿到它
        // ——2026-09-12 的 EID 轮次就是因为报告里只有"EntryRead 失败"而没能定位到 16 KiB 上限。
        if (length > 0) {
            *outputLength = static_cast<std::size_t>(length);
        }
        close(fileStorage.data());
        destroy(fileStorage.data());
        return TextReadResult::LengthOutOfRange;
    }

    const int bytesRead = read(fileStorage.data(), buffer, 1, length);
    close(fileStorage.data());
    destroy(fileStorage.data());
    if (bytesRead != length) {
        return TextReadResult::ReadMismatch;
    }
    *outputLength = static_cast<std::size_t>(length);
    return TextReadResult::Success;
}
#endif


} // namespace GameFileReader

#endif
