#include "diagnostics/diagnostic_health.hpp"

namespace isaac::runtime {

void DiagnosticHealth::RecordReason(DiagnosticHealthReason reason) noexcept {
    // Sticky and priority ordered: never cleared here, and only replaced by a
    // more severe failure.
    if (DiagnosticHealthReasonPriority(reason) >
        DiagnosticHealthReasonPriority(reason_)) {
        reason_ = reason;
    }
}

void DiagnosticHealth::RecordDrop() noexcept {
    ++droppedEvents_;
    RecordReason(DiagnosticHealthReason::RingOverflow);
}

void DiagnosticHealth::RecordFileFailure(DiagnosticHealthReason reason) noexcept {
    ++fileFailures_;
    RecordReason(reason);
}

void DiagnosticHealth::RecordClaimContention() noexcept {
    ++claimContention_;
    RecordReason(DiagnosticHealthReason::ClaimContended);
}

void DiagnosticHealth::Reset() noexcept {
    reason_ = DiagnosticHealthReason::None;
    droppedEvents_ = 0;
    fileFailures_ = 0;
    claimContention_ = 0;
}

} // namespace isaac::runtime
