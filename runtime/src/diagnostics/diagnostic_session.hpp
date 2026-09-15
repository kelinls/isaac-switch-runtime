#pragma once

#include "diagnostics/bounded_file_journal.hpp"
#include "diagnostics/diagnostic_event.hpp"
#include "diagnostics/diagnostic_event_bus.hpp"
#include "diagnostics/diagnostic_event_ids.hpp"
#include "diagnostics/diagnostic_health.hpp"
#include "diagnostics/in_memory_ring_buffer_sink.hpp"
#include "ports/file_port.hpp"

#include <cstdint>
#include <optional>
#include <string_view>

namespace isaac::runtime {

// One diagnostics session: the ring that is always available, the health state,
// the bus producers publish to, and the journal that starts writing once a file
// port exists.
//
// The session is deliberately inert until `AttachFilePort` is called: early boot
// has no reliable file API, and the design requires those events to be queued in
// memory rather than lost. Attaching the port also records the
// `FilePortRegistered` event, which is what tells an offline reader that the
// pre-port event prefix and the file may differ.
class DiagnosticSession {
public:
    DiagnosticSession(std::uint64_t buildId, DiagnosticOrigin origin) noexcept
        : bus_(ring_, health_), buildId_(buildId), origin_(origin) {}

    // Registers the file port and opens the journal. Returns the open status so a
    // caller can log it, but callers must keep running either way: diagnostics
    // never gate the Runtime.
    [[nodiscard]] Status AttachFilePort(IFilePort& port, std::string_view path) noexcept {
        // A second attach must not replace a working journal: the caller is a
        // "try once per boot" entry point, and re-emplacing would discard queued
        // evidence.
        if (journal_.has_value()) {
            return Status{StatusCode::InvalidState};
        }
        journal_.emplace(port, path, ring_, health_);
        const Status opened = journal_->Open();
        if (!opened.ok()) {
            // ③ A failed attach must leave a trace the offline reader can find.
            // "No port was registered" and "the file could not be opened" are
            // different problems, so the reason is a distinct event rather than a
            // silently absent file.
            DiagnosticEvent failure = MakeEvent(DiagnosticSubsystem::Diagnostics,
                                               kDiagnosticsEventAttachFailed,
                                               DiagnosticPhase::Failed);
            failure.result.code = static_cast<std::uint32_t>(
                opened.code() == StatusCode::InvalidState ? StatusCode::Ok
                                                          : opened.code());
            failure.detail = opened.code() == StatusCode::InvalidState
                                 ? kDiagnosticsAttachFailureNoPort
                                 : kDiagnosticsAttachFailureOpenFailed;
            failure.severity = DiagnosticSeverity::Error;
            static_cast<void>(bus_.Publish(failure));
            return opened;
        }
        DiagnosticEvent event = MakeEvent(DiagnosticSubsystem::Diagnostics,
                                         kDiagnosticsEventFilePortRegistered,
                                         DiagnosticPhase::None);
        event.result.code = 0;
        static_cast<void>(bus_.Publish(event));
        return opened;
    }

    [[nodiscard]] bool filePortAttached() const noexcept { return journal_.has_value(); }

    // Publishes one event. `false` means it was dropped; business code must not
    // branch on it.
    [[nodiscard]] bool Publish(const DiagnosticEvent& event) noexcept {
        return bus_.Publish(event);
    }

    // Writes the queued events, bounded per call.
    //
    // This method only writes: it never enqueues an event of its own. Emitting a
    // "journal flushed" record from here would make every flush enqueue work for
    // the next one, so a caller that loops until the ring is empty would never
    // terminate (and a stable frame would produce I/O forever, which the design
    // forbids). Callers that want to record a flush publish it themselves.
    [[nodiscard]] std::size_t Flush() noexcept {
        if (!journal_.has_value()) {
            return 0;
        }
        return journal_->Flush();
    }

    // Records that `count` events were written. Kept separate from `Flush` so the
    // observation is a deliberate, one-off event rather than a side effect.
    void PublishJournalFlushed(std::size_t count) noexcept {
        DiagnosticEvent event = MakeEvent(DiagnosticSubsystem::Diagnostics,
                                         kDiagnosticsEventJournalFlushed,
                                         DiagnosticPhase::None);
        event.detail = count;
        static_cast<void>(bus_.Publish(event));
    }

    [[nodiscard]] DiagnosticEvent MakeEvent(DiagnosticSubsystem subsystem, std::uint16_t event,
                                           DiagnosticPhase phase) noexcept {
        DiagnosticEvent created{};
        created.origin = origin_;
        created.buildId = buildId_;
        created.sequence = ++sequence_;
        created.subsystem = subsystem;
        created.event = event;
        created.phase = phase;
        created.threadTag = kDiagnosticThreadTagUnknown;
        return created;
    }

    // Publishes the current health state so an offline reader can distinguish
    // "no problem" from "evidence was lost".
    void PublishHealth() noexcept {
        DiagnosticEvent event = MakeEvent(DiagnosticSubsystem::Diagnostics,
                                         kDiagnosticsEventHealthChanged,
                                         DiagnosticPhase::None);
        event.result.code = static_cast<std::uint32_t>(health_.Reason());
        event.detail = health_.DroppedEvents();
        event.severity = health_.degraded() ? DiagnosticSeverity::Error : DiagnosticSeverity::Info;
        static_cast<void>(bus_.Publish(event));
    }

    [[nodiscard]] DiagnosticHealth& Health() noexcept { return health_; }
    [[nodiscard]] DiagnosticEventBus<>& Bus() noexcept { return bus_; }
    [[nodiscard]] InMemoryRingBufferSink<>& Ring() noexcept { return ring_; }
    [[nodiscard]] std::uint64_t buildId() const noexcept { return buildId_; }
    [[nodiscard]] DiagnosticOrigin origin() const noexcept { return origin_; }

private:
    InMemoryRingBufferSink<> ring_{};
    DiagnosticHealth health_{};
    DiagnosticEventBus<> bus_;
    // Constructed lazily in `AttachFilePort`: the journal needs a port reference,
    // and there is no port during early boot.
    std::optional<BoundedFileJournal<>> journal_{};
    std::uint64_t buildId_{0};
    DiagnosticOrigin origin_{DiagnosticOrigin::Unknown};
    std::uint32_t sequence_{0};
};

} // namespace isaac::runtime
