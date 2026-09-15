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

    enum class HookCommitResult : u8 {
        Success,
        NotCommitted,
        Indeterminate,
    };

    void Initialize();

    bool TryPrepareHook(uintptr_t hook, uintptr_t callback, bool do_trampoline,
                        HookPreparation* preparation, uintptr_t* trampoline);
    HookCommitResult TryCommitHook(const HookPreparation& preparation);
    bool TryHook(uintptr_t hook, uintptr_t callback, bool do_trampoline, uintptr_t* trampoline);
    uintptr_t Hook(uintptr_t hook, uintptr_t callback, bool do_trampoline = false);
    void HookInline(uintptr_t hook, uintptr_t callback, bool capture_floats);
}
