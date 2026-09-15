#pragma once

#include "base.hpp"
#include "util/func_ptrs.hpp"
#include <atomic>
#include <functional>

#define HOOK_DEFINE_TRAMPOLINE(name)                        \
struct name : public ::exl::hook::impl::TrampolineHook<name>

namespace exl::hook::impl {

    template<typename Derived>
    class TrampolineHook {

        template<typename T = Derived>
        using CallbackFuncPtr = decltype(&T::Callback);

        static ALWAYS_INLINE auto& OrigRef() {
            _HOOK_STATIC_CALLBACK_ASSERT();

            static_assert(std::atomic<CallbackFuncPtr<>>::is_always_lock_free);
            static constinit std::atomic<CallbackFuncPtr<>> s_FnPtr{nullptr};

            return s_FnPtr;
        }

        public:
        template<typename... Args>
        static ALWAYS_INLINE decltype(auto) Orig(Args &&... args) {
            _HOOK_STATIC_CALLBACK_ASSERT();

            auto original = OrigRef().load(std::memory_order_acquire);
            return original(std::forward<Args>(args)...);
        }

        static ALWAYS_INLINE void InstallAtOffset(ptrdiff_t address) {
            _HOOK_STATIC_CALLBACK_ASSERT();

            OrigRef().store(hook::Hook(util::modules::GetTargetStart() + address, Derived::Callback, true),
                            std::memory_order_release);
        }

        template<typename T>
        static ALWAYS_INLINE void InstallAtFuncPtr(T ptr) {
            _HOOK_STATIC_CALLBACK_ASSERT();

            using Traits = util::FuncPtrTraits<T>;
            static_assert(std::is_same_v<typename Traits::CPtr, CallbackFuncPtr<>>, "Argument pointer type must match callback type!");

            OrigRef().store(hook::Hook(ptr, Derived::Callback, true), std::memory_order_release);
        }

        static ALWAYS_INLINE void InstallAtPtr(uintptr_t ptr) {
            _HOOK_STATIC_CALLBACK_ASSERT();
            
            OrigRef().store(hook::Hook(ptr, Derived::Callback, true), std::memory_order_release);
        }

        static ALWAYS_INLINE bool TryInstallAtPtr(uintptr_t ptr) {
            _HOOK_STATIC_CALLBACK_ASSERT();

            hook::HookPreparation preparation{};
            CallbackFuncPtr<> trampoline = nullptr;
            if (!hook::TryPrepareHook(ptr, Derived::Callback, &preparation, &trampoline, true)) {
                return false;
            }
            OrigRef().store(trampoline, std::memory_order_release);
            const auto commit = hook::TryCommitHook(preparation);
            return commit == hook::HookCommitResult::Success;
        }
    };

}
