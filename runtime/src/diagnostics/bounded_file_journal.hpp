#pragma once

#include "diagnostics/binary_event_codec.hpp"
#include "diagnostics/diagnostic_event.hpp"
#include "diagnostics/diagnostic_health.hpp"
#include "diagnostics/in_memory_ring_buffer_sink.hpp"
#include "ports/file_port.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace isaac::runtime {

// How many records one `Flush` may claim. Bounded on purpose: the game's update
// and render threads call the journal, and they must never be held by I/O long
// enough to miss a frame.
inline constexpr std::size_t kDiagnosticMaxRecordsPerFlush = 4;

// Appends diagnostic records to one file through `IFilePort`.
//
// Rules the device depends on (design §11.3):
//   * the caller never waits: each `Flush` claims at most
//     `kDiagnosticMaxRecordsPerFlush`
//     events from the ring, and the I/O happens outside the ring's claim flag;
//   * a short write or an uncertain close makes the sink **sticky degraded** —
//     after that it stops appending, because a half-written record would corrupt
//     every record after it, and the parser needs to trust what it reads;
//   * the file is opened in append mode and never truncated, so evidence from an
//     earlier Build ID survives (the parser filters by Build ID);
//   * failures only touch `DiagnosticHealth`; they are never returned to a game
//     thread as a business error.
template <std::size_t RingCapacity = kDiagnosticRingCapacity>
class BoundedFileJournal {
public:
    BoundedFileJournal(IFilePort& port, std::string_view path,
                       InMemoryRingBufferSink<RingCapacity>& ring,
                       DiagnosticHealth& health) noexcept
        : port_(port), path_(path), ring_(ring), health_(health) {}

    // Enables the sink once a file port has been registered. Opening lazily keeps
    // early-boot events in the ring until the platform can actually write.
    [[nodiscard]] Status Open() noexcept {
        if (handle_ != nullptr) {
            return Status::Ok();
        }
        if (healthy_ == false) {
            return Status{StatusCode::InvalidState};
        }
        void* handle = nullptr;
        const Status opened = port_.Open(path_, FileMode::Write, &handle);
        if (!opened.ok() || handle == nullptr) {
            healthy_ = false;
            health_.RecordFileFailure(DiagnosticHealthReason::OpenFailed);
            return opened.ok() ? Status{StatusCode::IoFailure} : opened;
        }
        handle_ = handle;
        return Status::Ok();
    }

    // Writes up to `kDiagnosticMaxRecordsPerFlush` queued events. Returns how many records
    // reached the file; the ring keeps everything that was not written.
    [[nodiscard]] std::size_t Flush() noexcept {
        if (handle_ == nullptr || !healthy_) {
            return 0;
        }
        std::size_t written = 0;
        const std::size_t available = ring_.Count();
        const std::size_t budget =
            available < kDiagnosticMaxRecordsPerFlush ? available : kDiagnosticMaxRecordsPerFlush;
        for (std::size_t index = 0; index < budget; ++index) {
            DiagnosticEvent event{};
            if (!ring_.At(index, &event)) {
                break;
            }
            std::uint8_t record[BinaryEventCodec::kRecordSize]{};
            if (!BinaryEventCodec::Encode(event, record, sizeof(record)).ok()) {
                health_.RecordFileFailure(DiagnosticHealthReason::ShortWrite);
                healthy_ = false;
                break;
            }
            std::size_t wrote = 0;
            const Status status = port_.Write(handle_, record, sizeof(record), &wrote);
            if (!status.ok() || wrote != sizeof(record)) {
                // A partial record would desynchronise every later record.
                health_.RecordFileFailure(DiagnosticHealthReason::ShortWrite);
                healthy_ = false;
                break;
            }
            ++written;
        }
        if (written != 0) {
            ring_.DiscardOldest(written);
            recordsWritten_ += written;
        }
        return written;
    }

    [[nodiscard]] Status Close() noexcept {
        if (handle_ == nullptr) {
            return Status::Ok();
        }
        const Status closed = port_.Close(handle_);
        handle_ = nullptr;
        if (!closed.ok()) {
            // The tail of the file may be missing; do not pretend it is complete
            // and do not retry automatically.
            health_.RecordFileFailure(DiagnosticHealthReason::CloseFailed);
            healthy_ = false;
        }
        return closed;
    }

    [[nodiscard]] bool healthy() const noexcept { return healthy_ && handle_ != nullptr; }
    [[nodiscard]] bool failed() const noexcept { return !healthy_; }
    [[nodiscard]] std::uint32_t RecordsWritten() const noexcept { return recordsWritten_; }
    [[nodiscard]] std::string_view Path() const noexcept { return path_; }

private:
    IFilePort& port_;
    std::string_view path_;
    InMemoryRingBufferSink<RingCapacity>& ring_;
    DiagnosticHealth& health_;
    void* handle_{nullptr};
    bool healthy_{true};
    std::uint32_t recordsWritten_{0};
};

} // namespace isaac::runtime
