#pragma once

#include <cstdint>

namespace isaac::runtime {

// The Runtime is built with -fno-exceptions. Every fallible operation reports
// failure through this value type instead of throwing.
enum class StatusCode : std::uint16_t {
    Ok = 0,
    InvalidArgument,
    InvalidState,
    Unsupported,
    NotFound,
    CapacityExceeded,
    IoFailure,
    Corrupted,
    Rejected,
};

class Status {
public:
    constexpr Status() noexcept = default;
    constexpr explicit Status(StatusCode code) noexcept : code_(code) {}

    [[nodiscard]] static constexpr Status Ok() noexcept { return Status{StatusCode::Ok}; }
    [[nodiscard]] constexpr bool ok() const noexcept { return code_ == StatusCode::Ok; }
    [[nodiscard]] constexpr StatusCode code() const noexcept { return code_; }

    friend constexpr bool operator==(Status left, Status right) noexcept {
        return left.code_ == right.code_;
    }
    friend constexpr bool operator!=(Status left, Status right) noexcept {
        return !(left == right);
    }

private:
    StatusCode code_{StatusCode::Ok};
};

// Result<T> carries either a value or a Status. A failure constructed from
// Status::Ok() would be meaningless, so it is normalised to InvalidState.
template <typename T>
class Result {
public:
    Result(T value) noexcept : value_(value), status_(StatusCode::Ok) {}

    Result(Status status) noexcept : status_(status.ok() ? Status{StatusCode::InvalidState} : status) {}

    [[nodiscard]] bool ok() const noexcept { return status_.ok(); }
    [[nodiscard]] StatusCode code() const noexcept { return status_.code(); }
    [[nodiscard]] const Status& status() const noexcept { return status_; }

    // Only valid when ok() is true; callers must check first.
    [[nodiscard]] const T& value() const noexcept { return value_; }
    [[nodiscard]] T& value() noexcept { return value_; }

private:
    T value_{};
    Status status_{};
};

} // namespace isaac::runtime
