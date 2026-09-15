#pragma once

#include "game_observer.hpp"

#include <atomic>

class DefaultCallbackGate {
public:
    bool Observe(GameIsPausedObservation observation, bool musicReady) {
        if (observation == GameIsPausedObservation::PausedFalse && musicReady) {
            open_.store(true, std::memory_order_release);
        }
        return IsOpen();
    }

    bool IsOpen() const {
        return open_.load(std::memory_order_acquire);
    }

private:
    std::atomic<unsigned int> open_{false};
};
