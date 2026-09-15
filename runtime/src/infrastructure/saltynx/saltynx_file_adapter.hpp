#pragma once

#include "ports/file_port.hpp"
#include "ports/runtime_host_api.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace isaac::runtime {

// `IFilePort` over the file table registered by the SaltyNX host plugin. The
// table is re-read on every call, so a later registration takes effect without
// restarting the Runtime, and the four native function pointers never leave
// this adapter.
class SaltyNxFileAdapter final : public IFilePort {
public:
    static constexpr std::size_t kMaximumPathLength = 192;

    explicit SaltyNxFileAdapter(IRuntimeHostApi& host) noexcept : host_(host) {}

    [[nodiscard]] Status Open(std::string_view path, FileMode mode, void** handle) noexcept override;
    [[nodiscard]] Status Read(void* handle, std::uint8_t* target, std::size_t capacity,
                              std::size_t* readCount) noexcept override;
    [[nodiscard]] Status Write(void* handle, const std::uint8_t* bytes, std::size_t count,
                               std::size_t* writtenCount) noexcept override;
    [[nodiscard]] Status Close(void* handle) noexcept override;

    // True once the host has published a complete table.
    [[nodiscard]] bool Available() noexcept;

private:
    [[nodiscard]] bool Table(HostFileApiV1* out) noexcept;

    IRuntimeHostApi& host_;
};

} // namespace isaac::runtime
