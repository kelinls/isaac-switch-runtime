#pragma once

#include "module_finder.hpp"
#include "runtime_constants.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace GameFileReader {
struct Bindings {
    uintptr_t construct;
    uintptr_t openRead;
    uintptr_t getLength;
    uintptr_t read;
    uintptr_t close;
    uintptr_t destroy;
};

struct WriteBindings {
    uintptr_t construct;
    uintptr_t openWrite;
    uintptr_t write;
    uintptr_t close;
    uintptr_t destroy;
};

enum class ReadResult : std::uint32_t {
    Success,
    OpenFailed,
    LengthMismatch,
    ReadMismatch,
    ContentMismatch,
};

enum class ScriptReadResult : std::uint32_t {
    Success,
    OpenFailed,
    LengthOutOfRange,
    ReadMismatch,
};

bool VerifyBindings(const TargetModule& module, Bindings* bindings);
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 118
enum class WriteDiagnosticResult : std::uint32_t { Success, OpenFailed, WriteMismatch };
bool VerifyWriteBindings(const TargetModule& module, WriteBindings* bindings);
WriteDiagnosticResult WriteDiagnosticFile(const WriteBindings& bindings);
#endif
ReadResult ReadSentinel(const Bindings& bindings);
ScriptReadResult ReadScript(const Bindings& bindings,
                            std::array<u8, kRomfsLuaProbeMaximumLength>* buffer,
                            std::size_t* length);

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
enum class TextReadResult : std::uint32_t {
    Success,
    InvalidArgument,
    OpenFailed,
    LengthOutOfRange,
    ReadMismatch,
};

TextReadResult ReadTextFile(const Bindings& bindings, const char* path,
                            u8* buffer, std::size_t capacity, std::size_t* length);
#endif
}
