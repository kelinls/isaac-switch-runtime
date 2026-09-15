#include "probe_logger.hpp"

#include "fs_ipc.h"
#include "runtime_constants.hpp"

#include <cstdio>

bool ProbeLogger::Open() {
    if (m_IsOpen) {
        return true;
    }
    if (m_ErrorCount != 0) {
        return false;
    }

    int64_t writeOffset = 0;
    if (!RuntimeFsLogOpen(kLogDirectory, kLogPath, &writeOffset)) {
        return false;
    }
    m_WriteOffset = static_cast<s64>(writeOffset);
    m_IsOpen = true;
    return true;
}

void ProbeLogger::DisableOnError() {
    ++m_ErrorCount;
    if (m_IsOpen) {
        RuntimeFsLogClose();
        m_IsOpen = false;
    }
}

void ProbeLogger::Event(std::string_view event) {
    Event(event, {});
}

void ProbeLogger::Event(std::string_view event, std::string_view fields) {
    if (!m_IsOpen || m_ErrorCount != 0) {
        return;
    }

    WriteLine(event, fields);
}

bool ProbeLogger::WriteLine(std::string_view event, std::string_view fields) {
    char line[384]{};
    int result;
    if (fields.empty()) {
        result = std::snprintf(line, sizeof(line), "event=%.*s\n", static_cast<int>(event.size()), event.data());
    } else {
        result = std::snprintf(line, sizeof(line), "event=%.*s %.*s\n",
                               static_cast<int>(event.size()), event.data(),
                               static_cast<int>(fields.size()), fields.data());
    }
    if (result < 0 || static_cast<size_t>(result) >= sizeof(line) ||
        !RuntimeFsLogWrite(m_WriteOffset, line, static_cast<uint64_t>(result))) {
        DisableOnError();
        return false;
    }
    m_WriteOffset += result;
    return true;
}

void ProbeLogger::Heartbeat(u64 frame) {
    if (frame % 300 != 0) {
        return;
    }
    u64 pending = m_PendingHeartbeat.load(std::memory_order_relaxed);
    while (pending < frame &&
           !m_PendingHeartbeat.compare_exchange_weak(pending, frame, std::memory_order_release,
                                                     std::memory_order_relaxed)) {
    }
}

void ProbeLogger::FlushPendingHeartbeat() {
    const u64 frame = m_PendingHeartbeat.exchange(0, std::memory_order_acq_rel);
    if (frame == 0) {
        return;
    }
    if (!m_IsOpen || m_ErrorCount != 0) {
        return;
    }
    char fields[64]{};
    std::snprintf(fields, sizeof(fields), "frame=%llu", static_cast<unsigned long long>(frame));
    Event("heartbeat", fields);
}

u32 ProbeLogger::ErrorCount() const {
    return m_ErrorCount;
}
