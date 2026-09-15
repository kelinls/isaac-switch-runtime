#pragma once

#include "common.hpp"
#include "inline_impl.hpp"

namespace exl::hook::nx64 {

    struct HookPreparation {
        uintptr_t hook = 0;
        uintptr_t callback = 0;
        uintptr_t rxTrampoline = 0;
        uintptr_t rwTrampoline = 0;
        u32 original[5]{};
        u32 instructionCount = 0;
    };

    enum class HookPrepareResult : u8 {
        Success,
        InvalidArgument,
        BranchOutOfRange,
        TrampolineAllocationFailed,
    };

    enum class HookCommitResult : u8 {
        Success,
        InvalidPreparation,
        BranchOutOfRange,
        OriginalInstructionChanged,
        CompareExchangeFailed,
    };

    enum class HookAttemptResult : u8 {
        Success,
        PrepareInvalidArgument,
        PrepareBranchOutOfRange,
        PrepareTrampolineAllocationFailed,
        CommitInvalidPreparation,
        CommitBranchOutOfRange,
        CommitOriginalInstructionChanged,
        CommitCompareExchangeFailed,
    };

    void Initialize();

    HookPrepareResult TryPrepareHook(uintptr_t hook, uintptr_t callback, bool do_trampoline,
                                     HookPreparation* preparation, uintptr_t* trampoline);
    HookCommitResult TryCommitHook(const HookPreparation& preparation);
    bool TryHook(uintptr_t hook, uintptr_t callback, bool do_trampoline, uintptr_t* trampoline);
    uintptr_t Hook(uintptr_t hook, uintptr_t callback, bool do_trampoline = false);
    void HookInline(uintptr_t hook, uintptr_t callback, bool capture_floats);
}
