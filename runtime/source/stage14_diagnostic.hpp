#pragma once

#include <atomic>
#include <cstdint>

namespace Stage14Diagnostic {

enum class Lifecycle : std::uint32_t {
    Installing,
    Ready,
    Finished,
};

enum class Claim : std::uint32_t {
    WaitForReady,
    Run,
    IgnoreFinished,
};

enum class CallbackResult : std::uint32_t {
    SuccessFalse,
    SuccessTrue,
    CallbackError,
    CallbackNotReached,
    BooleanTypeMismatch,
};

enum class Stage14Failure : std::uint32_t {
    InstallFailed = 1,
    LuaInitializationFailed = 2,
    CallbackError = 3,
    CallbackNotReached = 4,
    BooleanTypeMismatch = 5,
    StartupFailed = 6,
    ModuleNotFound = 7,
    WorkerFailed = 8,
};

constexpr CallbackResult ClassifyCallback(std::uint32_t postUpdateCount, bool callbackError) {
    if (callbackError) {
        return postUpdateCount == 3 ? CallbackResult::BooleanTypeMismatch
                                    : CallbackResult::CallbackError;
    }
    if (postUpdateCount == 1) {
        return CallbackResult::SuccessFalse;
    }
    if (postUpdateCount == 2) {
        return CallbackResult::SuccessTrue;
    }
    return CallbackResult::CallbackNotReached;
}

class Controller {
public:
    void MarkReady() {
        m_State.store(Lifecycle::Ready, std::memory_order_release);
    }

    Claim TryClaimCallback() {
        Lifecycle expected = Lifecycle::Ready;
        if (m_State.compare_exchange_strong(expected, Lifecycle::Finished,
                                            std::memory_order_acq_rel,
                                            std::memory_order_acquire)) {
            return Claim::Run;
        }
        return expected == Lifecycle::Installing ? Claim::WaitForReady
                                                  : Claim::IgnoreFinished;
    }

    Lifecycle State() const {
        return m_State.load(std::memory_order_acquire);
    }

private:
    std::atomic<Lifecycle> m_State{Lifecycle::Installing};
};

} // namespace Stage14Diagnostic
