#pragma once

#include <cstdint>

namespace isaac::runtime {

// Why the diagnostics pipeline considers itself degraded. The parser reports this
// so that "no events" and "events were dropped" can never look the same.
enum class DiagnosticHealthReason : std::uint32_t {
    None = 0,
    // The in-memory ring was full, so an event was not queued.
    RingOverflow = 1,
    // The journal could not open its file.
    OpenFailed = 2,
    // The journal wrote fewer bytes than the record needs; the append stream is
    // no longer trustworthy.
    ShortWrite = 3,
    // Flush reported success but closing the handle failed, so the tail of the
    // file may be missing.
    CloseFailed = 4,
    // A concurrent producer held the claim; the event was skipped rather than
    // blocking the caller.
    ClaimContended = 5,
};

// Diagnostics health. Logging failures must never change a business result, so
// they are recorded here instead of being returned to the caller.
//
// The reported reason is sticky and priority ordered: recording never clears it
// (only `Reset` does), and a more severe failure replaces a less severe one so
// the parser sees "the file stream is not trustworthy" rather than "the ring
// overflowed once". A later success can therefore never hide an evidence gap.
[[nodiscard]] constexpr int DiagnosticHealthReasonPriority(DiagnosticHealthReason reason) noexcept {
    switch (reason) {
        case DiagnosticHealthReason::None:          return 0;
        case DiagnosticHealthReason::ClaimContended: return 1;
        case DiagnosticHealthReason::RingOverflow:  return 2;
        case DiagnosticHealthReason::OpenFailed:    return 3;
        case DiagnosticHealthReason::ShortWrite:    return 4;
        case DiagnosticHealthReason::CloseFailed:   return 4;
    }
    return 0;
}
class DiagnosticHealth {
public:
    void RecordDrop() noexcept;
    void RecordFileFailure(DiagnosticHealthReason reason) noexcept;
    void RecordClaimContention() noexcept;

    [[nodiscard]] bool degraded() const noexcept { return reason_ != DiagnosticHealthReason::None; }
    [[nodiscard]] DiagnosticHealthReason Reason() const noexcept { return reason_; }
    [[nodiscard]] std::uint32_t DroppedEvents() const noexcept { return droppedEvents_; }
    [[nodiscard]] std::uint32_t FileFailures() const noexcept { return fileFailures_; }
    [[nodiscard]] std::uint32_t ClaimContention() const noexcept { return claimContention_; }

    void Reset() noexcept;

private:
    void RecordReason(DiagnosticHealthReason reason) noexcept;

    // The first failure explains the gap; later failures only increment counters.
    DiagnosticHealthReason reason_{DiagnosticHealthReason::None};
    std::uint32_t droppedEvents_{0};
    std::uint32_t fileFailures_{0};
    std::uint32_t claimContention_{0};
};

} // namespace isaac::runtime
