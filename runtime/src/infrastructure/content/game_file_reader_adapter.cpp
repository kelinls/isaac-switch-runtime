#include "infrastructure/content/game_file_reader_adapter.hpp"

#include <cstring>

namespace isaac::runtime {
namespace {

Status MapReadResult(GameFileReader::TextReadResult result) noexcept {
    switch (result) {
        case GameFileReader::TextReadResult::Success:
            return Status::Ok();
        case GameFileReader::TextReadResult::InvalidArgument:
            return Status{StatusCode::InvalidArgument};
        case GameFileReader::TextReadResult::OpenFailed:
            return Status{StatusCode::NotFound};
        case GameFileReader::TextReadResult::LengthOutOfRange:
            return Status{StatusCode::CapacityExceeded};
        case GameFileReader::TextReadResult::ReadMismatch:
            return Status{StatusCode::Corrupted};
    }
    return Status{StatusCode::Corrupted};
}

} // namespace

Status GameFileReaderAdapter::Size(std::string_view path, std::size_t* size) noexcept {
    if (size == nullptr || path.empty()) {
        return Status{StatusCode::InvalidArgument};
    }
    // The game API only reports a length as part of a read, so a caller that
    // needs the length reads into its own buffer and uses readCount.
    return Status{StatusCode::Unsupported};
}

Status GameFileReaderAdapter::Read(std::string_view path, std::uint8_t* target,
                                   std::size_t capacity, std::size_t* readCount) noexcept {
    if (path.empty() || target == nullptr || readCount == nullptr || capacity == 0) {
        return Status{StatusCode::InvalidArgument};
    }
    if (path.size() >= kMaximumPathLength) {
        return Status{StatusCode::CapacityExceeded};
    }
    char pathBuffer[kMaximumPathLength]{};
    std::memcpy(pathBuffer, path.data(), path.size());
    std::size_t length = 0;
    const Status read =
        MapReadResult(GameFileReader::ReadTextFile(bindings_, pathBuffer, target, capacity, &length));
    // 失败时也把观测到的长度交出去（`LengthOutOfRange` 会填它，其它错误保持 0）：
    // "文件多大"是区分"不存在"与"缓冲区太小"的唯一线索，见 `ModLoadFailure::observedBytes`。
    *readCount = length;
    if (!read.ok()) {
        return read;
    }
    return Status::Ok();
}

} // namespace isaac::runtime
