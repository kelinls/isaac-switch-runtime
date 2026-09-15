#pragma once

#include "types.h"

#include <atomic>
#include <string_view>

static_assert(std::atomic<u64>::is_always_lock_free);

class ProbeLogger {
public:
    bool Open();
    void Event(std::string_view event);
    void Event(std::string_view event, std::string_view fields);
    void Heartbeat(u64 frame);
    void FlushPendingHeartbeat();
    u32 ErrorCount() const;

private:
    bool WriteLine(std::string_view event, std::string_view fields);
    void DisableOnError();

    s64 m_WriteOffset = 0;
    bool m_IsOpen = false;
    u32 m_ErrorCount = 0;
    std::atomic<u64> m_PendingHeartbeat{0};
};
