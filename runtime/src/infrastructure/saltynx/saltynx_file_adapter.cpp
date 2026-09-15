#include "infrastructure/saltynx/saltynx_file_adapter.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

constexpr const char* kReadMode = "rb";
constexpr const char* kWriteMode = "wb";

} // namespace

bool SaltyNxFileAdapter::Table(HostFileApiV1* out) noexcept {
    return host_.PublishedFileApi(out) && out->complete();
}

bool SaltyNxFileAdapter::Available() noexcept {
    HostFileApiV1 table{};
    return Table(&table);
}

Status SaltyNxFileAdapter::Open(std::string_view path, FileMode mode, void** handle) noexcept {
    if (path.empty() || handle == nullptr || path.size() >= kMaximumPathLength) {
        return Status{path.size() >= kMaximumPathLength ? StatusCode::CapacityExceeded
                                                        : StatusCode::InvalidArgument};
    }
    HostFileApiV1 table{};
    if (!Table(&table)) {
        return Status{StatusCode::InvalidState};
    }
    char pathBuffer[kMaximumPathLength]{};
    std::memcpy(pathBuffer, path.data(), path.size());
    void* file = table.open(pathBuffer, mode == FileMode::Read ? kReadMode : kWriteMode);
    if (file == nullptr) {
        return Status{StatusCode::NotFound};
    }
    *handle = file;
    return Status::Ok();
}

Status SaltyNxFileAdapter::Read(void* handle, std::uint8_t* target, std::size_t capacity,
                                std::size_t* readCount) noexcept {
    if (handle == nullptr || target == nullptr || readCount == nullptr || capacity == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    HostFileApiV1 table{};
    if (!Table(&table)) {
        return Status{StatusCode::InvalidState};
    }
    *readCount = table.read(target, 1, capacity, handle);
    return Status::Ok();
}

Status SaltyNxFileAdapter::Write(void* handle, const std::uint8_t* bytes, std::size_t count,
                                 std::size_t* writtenCount) noexcept {
    if (handle == nullptr || bytes == nullptr || writtenCount == nullptr || count == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    HostFileApiV1 table{};
    if (!Table(&table)) {
        return Status{StatusCode::InvalidState};
    }
    const std::size_t written = table.write(bytes, 1, count, handle);
    *writtenCount = written;
    // A short write must be visible to the caller: the diagnostic journal turns
    // it into a sticky degraded state instead of silently losing records.
    return written == count ? Status::Ok() : Status{StatusCode::IoFailure};
}

Status SaltyNxFileAdapter::Close(void* handle) noexcept {
    if (handle == nullptr) {
        return Status{StatusCode::InvalidArgument};
    }
    HostFileApiV1 table{};
    if (!Table(&table)) {
        return Status{StatusCode::InvalidState};
    }
    return table.close(handle) == 0 ? Status::Ok() : Status{StatusCode::IoFailure};
}

} // namespace isaac::runtime
