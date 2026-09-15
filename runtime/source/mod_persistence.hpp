#pragma once

#include "ports/file_port.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

namespace ModPersistence {

using OpenFn = void* (*)(const char*, const char*);
using ReadFn = std::size_t (*)(void*, std::size_t, std::size_t, void*);
using WriteFn = std::size_t (*)(const void*, std::size_t, std::size_t, void*);
using CloseFn = int (*)(void*);

enum class Result {
    Success,
    Missing,
    Unavailable,
    Corrupt,
    TooLarge,
    IoFailure,
};

constexpr std::size_t kMaximumFileSize = 16 * 1024;
constexpr std::size_t kMaximumRecordCount = 256;

namespace detail {

constexpr char kMagic[] = "ISMODST2";
constexpr std::uint16_t kVersion = 2;
constexpr std::size_t kHeaderSize = 32;
constexpr std::size_t kRecordHeaderSize = 24;
constexpr std::uint8_t kStringType = 1;
constexpr char kStatePath[] =
    "sdmc:/SaltySD/plugins/010021C000B6A000/isaac-mod-data.bin";
constexpr std::uint64_t kModDataKey = 0x6D6F642D64617461ULL; // "mod-data"

struct FileApi {
    OpenFn open = nullptr;
    ReadFn read = nullptr;
    WriteFn write = nullptr;
    CloseFn close = nullptr;
};

// 文件能力走**端口**（`isaac::runtime::IFilePort`，定义在 `src/ports/file_port.hpp`）：
// 消费者只认这个接口，"以后换一个提供方"就只需换一个 `IFilePort` 实现。
// `Unavailable` 是**一等状态**：没有注册提供方时，所有操作都以它返回（而不是沉默失败）。
inline isaac::runtime::IFilePort* g_filePort = nullptr;
inline std::atomic<std::uint32_t> g_fileApiReady{false};

// 宿主（SaltyNX 插件）那套裸函数：只在**桥接处**注册一次，然后被包成端口。
// 之所以保留这条兼容路径：插件导出的是 4 个裸函数指针（见 `ports/runtime_host_api.hpp`），
// 而端口是 C++ 接口 —— 包一层就够，不需要让两边都改。
inline FileApi g_rawFunctions{};

class RawFilePort final : public isaac::runtime::IFilePort {
public:
    [[nodiscard]] isaac::runtime::Status Open(std::string_view path, isaac::runtime::FileMode mode,
                                              void** handle) noexcept override {
        if (g_rawFunctions.open == nullptr || handle == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::Unsupported};
        }
        // 裸 API 的失败只给一个空指针，分不出"文件不存在"和"打不开"；历史语义是当成
        // "还没有数据"（`LoadCache` 据此返回 Success + exists=false），这里保持同一口径。
        void* file = g_rawFunctions.open(path.data(),
                                         mode == isaac::runtime::FileMode::Write ? "wb" : "rb");
        if (file == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::NotFound};
        }
        *handle = file;
        return isaac::runtime::Status::Ok();
    }

    [[nodiscard]] isaac::runtime::Status Read(void* handle, std::uint8_t* target,
                                              std::size_t capacity,
                                              std::size_t* readCount) noexcept override {
        if (g_rawFunctions.read == nullptr || handle == nullptr || target == nullptr ||
            readCount == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidArgument};
        }
        *readCount = g_rawFunctions.read(target, 1, capacity, handle);
        return isaac::runtime::Status::Ok();
    }

    [[nodiscard]] isaac::runtime::Status Write(void* handle, const std::uint8_t* bytes,
                                               std::size_t count,
                                               std::size_t* writtenCount) noexcept override {
        if (g_rawFunctions.write == nullptr || handle == nullptr || bytes == nullptr ||
            writtenCount == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidArgument};
        }
        *writtenCount = g_rawFunctions.write(bytes, 1, count, handle);
        return isaac::runtime::Status::Ok();
    }

    [[nodiscard]] isaac::runtime::Status Close(void* handle) noexcept override {
        if (g_rawFunctions.close == nullptr || handle == nullptr) {
            return isaac::runtime::Status{isaac::runtime::StatusCode::InvalidArgument};
        }
        return g_rawFunctions.close(handle) == 0
                   ? isaac::runtime::Status::Ok()
                   : isaac::runtime::Status{isaac::runtime::StatusCode::IoFailure};
    }
};

inline RawFilePort g_rawPort{};

struct Cache {
    std::array<std::uint8_t, kMaximumFileSize> payload{};
    std::size_t payloadLength = 0;
    std::uint64_t generation = 0;
    bool loaded = false;
    bool exists = false;
};

inline Cache g_cache{};

inline void WriteU16(std::uint8_t* target, std::uint16_t value) {
    target[0] = static_cast<std::uint8_t>(value);
    target[1] = static_cast<std::uint8_t>(value >> 8);
}

inline void WriteU32(std::uint8_t* target, std::uint32_t value) {
    for (std::size_t index = 0; index < 4; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

inline void WriteU64(std::uint8_t* target, std::uint64_t value) {
    for (std::size_t index = 0; index < 8; ++index) {
        target[index] = static_cast<std::uint8_t>(value >> (index * 8));
    }
}

inline std::uint16_t ReadU16(const std::uint8_t* source) {
    return static_cast<std::uint16_t>(source[0]) |
           (static_cast<std::uint16_t>(source[1]) << 8);
}

inline std::uint32_t ReadU32(const std::uint8_t* source) {
    std::uint32_t value = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        value |= static_cast<std::uint32_t>(source[index]) << (index * 8);
    }
    return value;
}

inline std::uint64_t ReadU64(const std::uint8_t* source) {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(source[index]) << (index * 8);
    }
    return value;
}

inline std::uint32_t Checksum(const std::uint8_t* bytes, std::size_t length) {
    std::uint32_t value = 2166136261U;
    for (std::size_t index = 0; index < length; ++index) {
        value ^= bytes[index];
        value *= 16777619U;
    }
    return value;
}

inline bool HasFileApi() {
    return g_filePort != nullptr;
}

// 端口的状态码 → 本模块的结果码。`Unsupported`（没有提供方）单独映射成 `Unavailable`：
// 这是"能力缺失"，与"操作失败"必须能分开，调用方（含 Lua 侧）据此报不同的错。
inline Result MapStatus(isaac::runtime::Status status) {
    switch (status.code()) {
        case isaac::runtime::StatusCode::Ok:
            return Result::Success;
        case isaac::runtime::StatusCode::Unsupported:
            return Result::Unavailable;
        case isaac::runtime::StatusCode::NotFound:
            return Result::Missing;
        case isaac::runtime::StatusCode::Corrupted:
            return Result::Corrupt;
        case isaac::runtime::StatusCode::CapacityExceeded:
            return Result::TooLarge;
        case isaac::runtime::StatusCode::InvalidArgument:
        case isaac::runtime::StatusCode::InvalidState:
        case isaac::runtime::StatusCode::IoFailure:
        case isaac::runtime::StatusCode::Rejected:
            return Result::IoFailure;
    }
    return Result::IoFailure;
}

inline bool RecordAt(const std::uint8_t* payload, std::size_t payloadLength,
                     std::size_t offset, std::size_t* recordLength,
                     std::uint64_t* namespaceId, std::uint64_t* key,
                     std::uint8_t* type, std::size_t* valueLength) {
    if (offset > payloadLength || payloadLength - offset < kRecordHeaderSize) {
        return false;
    }
    const std::uint8_t* record = payload + offset;
    const std::size_t length = ReadU32(record + 20);
    if (length > payloadLength - offset - kRecordHeaderSize) {
        return false;
    }
    *recordLength = kRecordHeaderSize + length;
    *namespaceId = ReadU64(record);
    *key = ReadU64(record + 8);
    *type = record[16];
    *valueLength = length;
    return true;
}

inline Result LoadCache() {
    if (g_cache.loaded) {
        return Result::Success;
    }
    if (!HasFileApi()) {
        return Result::Unavailable;
    }

    void* file = nullptr;
    const isaac::runtime::Status opened =
        g_filePort->Open(kStatePath, isaac::runtime::FileMode::Read, &file);
    if (opened.code() == isaac::runtime::StatusCode::NotFound) {
        // 还没有数据：历史语义就是"成功 + exists=false"（第一次保存前就是这样），保持不动。
        g_cache.loaded = true;
        g_cache.exists = false;
        return Result::Success;
    }
    if (!opened.ok()) {
        return MapStatus(opened);
    }

    std::array<std::uint8_t, kMaximumFileSize> encoded{};
    std::size_t bytes = 0;
    const isaac::runtime::Status readStatus =
        g_filePort->Read(file, encoded.data(), encoded.size(), &bytes);
    const isaac::runtime::Status closeStatus = g_filePort->Close(file);
    if (!readStatus.ok()) {
        return MapStatus(readStatus);
    }
    if (!closeStatus.ok()) {
        return Result::IoFailure;
    }
    if (bytes < kHeaderSize || std::memcmp(encoded.data(), kMagic, sizeof(kMagic) - 1) != 0 ||
        ReadU16(encoded.data() + 8) != kVersion || ReadU16(encoded.data() + 10) != kHeaderSize) {
        return Result::Corrupt;
    }

    const std::uint32_t count = ReadU32(encoded.data() + 20);
    const std::size_t payloadLength = ReadU32(encoded.data() + 24);
    if (count > kMaximumRecordCount || payloadLength > kMaximumFileSize - kHeaderSize ||
        payloadLength != bytes - kHeaderSize ||
        ReadU32(encoded.data() + 28) != Checksum(encoded.data() + kHeaderSize, payloadLength)) {
        return Result::Corrupt;
    }

    std::size_t offset = 0;
    for (std::uint32_t index = 0; index < count; ++index) {
        std::size_t recordLength = 0;
        std::size_t valueLength = 0;
        std::uint64_t namespaceId = 0;
        std::uint64_t key = 0;
        std::uint8_t type = 0;
        if (!RecordAt(encoded.data() + kHeaderSize, payloadLength, offset, &recordLength,
                      &namespaceId, &key, &type, &valueLength)) {
            return Result::Corrupt;
        }
        offset += recordLength;
    }
    if (offset != payloadLength) {
        return Result::Corrupt;
    }

    std::memcpy(g_cache.payload.data(), encoded.data() + kHeaderSize, payloadLength);
    g_cache.payloadLength = payloadLength;
    g_cache.generation = ReadU64(encoded.data() + 12);
    g_cache.exists = true;
    g_cache.loaded = true;
    return Result::Success;
}

inline Result WriteCache(const std::uint8_t* payload, std::size_t payloadLength,
                         std::uint32_t recordCount) {
    if (payloadLength > kMaximumFileSize - kHeaderSize || recordCount > kMaximumRecordCount) {
        return Result::TooLarge;
    }
    std::array<std::uint8_t, kMaximumFileSize> encoded{};
    std::memcpy(encoded.data(), kMagic, sizeof(kMagic) - 1);
    WriteU16(encoded.data() + 8, kVersion);
    WriteU16(encoded.data() + 10, kHeaderSize);
    WriteU64(encoded.data() + 12, g_cache.generation + 1);
    WriteU32(encoded.data() + 20, recordCount);
    WriteU32(encoded.data() + 24, static_cast<std::uint32_t>(payloadLength));
    if (payloadLength != 0) {
        std::memcpy(encoded.data() + kHeaderSize, payload, payloadLength);
    }
    WriteU32(encoded.data() + 28, Checksum(encoded.data() + kHeaderSize, payloadLength));

    void* file = nullptr;
    // 写模式拿不到句柄就是写不成：与旧实现一致，一律按 IO 失败报。
    if (!g_filePort->Open(kStatePath, isaac::runtime::FileMode::Write, &file).ok()) {
        return Result::IoFailure;
    }
    const std::size_t totalLength = kHeaderSize + payloadLength;
    std::size_t written = 0;
    const isaac::runtime::Status writeStatus =
        g_filePort->Write(file, encoded.data(), totalLength, &written);
    const isaac::runtime::Status closeStatus = g_filePort->Close(file);
    if (!writeStatus.ok() || written != totalLength || !closeStatus.ok()) {
        return Result::IoFailure;
    }

    std::memcpy(g_cache.payload.data(), payload, payloadLength);
    g_cache.payloadLength = payloadLength;
    g_cache.generation += 1;
    g_cache.exists = true;
    g_cache.loaded = true;
    return Result::Success;
}

inline Result Rewrite(std::uint64_t namespaceId, const char* value, std::size_t valueLength,
                      bool remove) {
    const Result loaded = LoadCache();
    if (loaded != Result::Success) {
        return loaded;
    }
    std::array<std::uint8_t, kMaximumFileSize> rewritten{};
    std::size_t rewrittenLength = 0;
    std::uint32_t rewrittenCount = 0;
    bool matched = false;
    for (std::size_t offset = 0; offset < g_cache.payloadLength;) {
        std::size_t recordLength = 0;
        std::size_t existingValueLength = 0;
        std::uint64_t existingNamespace = 0;
        std::uint64_t existingKey = 0;
        std::uint8_t type = 0;
        if (!RecordAt(g_cache.payload.data(), g_cache.payloadLength, offset, &recordLength,
                      &existingNamespace, &existingKey, &type, &existingValueLength)) {
            return Result::Corrupt;
        }
        const bool replace = existingNamespace == namespaceId && existingKey == kModDataKey &&
                             type == kStringType;
        matched = matched || replace;
        if (!replace) {
            if (recordLength > rewritten.size() - rewrittenLength) {
                return Result::TooLarge;
            }
            std::memcpy(rewritten.data() + rewrittenLength, g_cache.payload.data() + offset,
                        recordLength);
            rewrittenLength += recordLength;
            ++rewrittenCount;
        }
        offset += recordLength;
    }
    if (remove && !matched) {
        return Result::Success;
    }
    if (!remove) {
        if (value == nullptr || rewrittenLength > rewritten.size() - kRecordHeaderSize ||
            valueLength > rewritten.size() - rewrittenLength - kRecordHeaderSize ||
            rewrittenCount == kMaximumRecordCount) {
            return Result::TooLarge;
        }
        std::uint8_t* record = rewritten.data() + rewrittenLength;
        WriteU64(record, namespaceId);
        WriteU64(record + 8, kModDataKey);
        record[16] = kStringType;
        record[17] = record[18] = record[19] = 0;
        WriteU32(record + 20, static_cast<std::uint32_t>(valueLength));
        if (valueLength != 0) {
            std::memcpy(record + kRecordHeaderSize, value, valueLength);
        }
        rewrittenLength += kRecordHeaderSize + valueLength;
        ++rewrittenCount;
    }
    return WriteCache(rewritten.data(), rewrittenLength, rewrittenCount);
}

} // namespace detail

inline std::uint64_t HashModNamespace(const char* name, std::size_t length) {
    std::uint64_t value = 14695981039346656037ULL;
    for (std::size_t index = 0; index < length; ++index) {
        value ^= static_cast<unsigned char>(name[index]);
        value *= 1099511628211ULL;
    }
    return value;
}

// 文件能力的**唯一注入点**：谁提供就注入谁（桥接处那一次调用）。
// 传 nullptr = 明确撤销提供方（此后所有操作返回 `Unavailable`，是一等状态而不是沉默失败）。
inline void ConfigureFilePort(isaac::runtime::IFilePort* port) {
    detail::g_filePort = port;
    detail::g_cache = {};
    detail::g_fileApiReady.store(port != nullptr, std::memory_order_release);
}

// 兼容入口：宿主导出的是 4 个裸函数指针，包成端口后走同一条缝。
// 仍然只允许在桥接处调用（`tests/unit/test_file_api_seam.py` 钉住"注入点唯一"）。
inline void ConfigureFileApi(OpenFn open, ReadFn read, WriteFn write, CloseFn close) {
    detail::g_rawFunctions = {open, read, write, close};
    ConfigureFilePort(&detail::g_rawPort);
}

inline bool IsFileApiReady() {
    return detail::g_fileApiReady.load(std::memory_order_acquire);
}

inline void ResetForTesting() {
    detail::g_rawFunctions = {};
    ConfigureFilePort(nullptr);
    detail::g_cache = {};
}

inline void ResetCacheForTesting() {
    detail::g_cache = {};
}

inline Result SaveModData(std::uint64_t namespaceId, const char* data, std::size_t length) {
    return detail::Rewrite(namespaceId, data, length, false);
}

inline Result LoadModData(std::uint64_t namespaceId, const char** data, std::size_t* length) {
    if (data == nullptr || length == nullptr) {
        return Result::IoFailure;
    }
    const Result loaded = detail::LoadCache();
    if (loaded != Result::Success) {
        return loaded;
    }
    for (std::size_t offset = 0; offset < detail::g_cache.payloadLength;) {
        std::size_t recordLength = 0;
        std::size_t valueLength = 0;
        std::uint64_t recordNamespace = 0;
        std::uint64_t key = 0;
        std::uint8_t type = 0;
        if (!detail::RecordAt(detail::g_cache.payload.data(), detail::g_cache.payloadLength, offset,
                              &recordLength, &recordNamespace, &key, &type, &valueLength)) {
            return Result::Corrupt;
        }
        if (recordNamespace == namespaceId && key == detail::kModDataKey &&
            type == detail::kStringType) {
            *data = reinterpret_cast<const char*>(detail::g_cache.payload.data() + offset +
                                                  detail::kRecordHeaderSize);
            *length = valueLength;
            return Result::Success;
        }
        offset += recordLength;
    }
    *data = nullptr;
    *length = 0;
    return Result::Missing;
}

inline Result HasModData(std::uint64_t namespaceId, bool* has) {
    if (has == nullptr) {
        return Result::IoFailure;
    }
    const char* data = nullptr;
    std::size_t length = 0;
    const Result result = LoadModData(namespaceId, &data, &length);
    if (result == Result::Success) {
        *has = true;
        return Result::Success;
    }
    if (result == Result::Missing) {
        *has = false;
        return Result::Success;
    }
    return result;
}

inline Result RemoveModData(std::uint64_t namespaceId) {
    return detail::Rewrite(namespaceId, nullptr, 0, true);
}

} // namespace ModPersistence
