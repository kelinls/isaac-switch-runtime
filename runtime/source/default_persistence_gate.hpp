#pragma once

#include <cstdint>

class DefaultPersistenceGate {
public:
    static constexpr std::uint32_t kMaximumDeferredUpdates = 120;

    bool ShouldDispatch(bool fileApiReady, bool persistenceRequired) {
        if (fileApiReady) {
            return true;
        }
        if (persistenceRequired) {
            return false;
        }
        if (deferredUpdates_ < kMaximumDeferredUpdates) {
            ++deferredUpdates_;
            return false;
        }
        return true;
    }

private:
    std::uint32_t deferredUpdates_ = 0;
};
