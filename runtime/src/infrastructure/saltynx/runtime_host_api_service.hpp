#pragma once

#include "ports/runtime_host_api.hpp"

#include <atomic>
#include <cstdint>

namespace isaac::runtime {

// Runtime side of the versioned SaltyNX host contract. The plugin registers
// platform capabilities here and nothing else; every registration is validated
// before it becomes visible, and the published table is swapped in one step so
// a reader can never observe a half-written table.
class RuntimeHostApiService final : public IRuntimeHostApi {
public:
    explicit RuntimeHostApiService(std::uint64_t buildId) noexcept;

    [[nodiscard]] HostRegisterResult RegisterFilePort(const HostFileApiV1& api) noexcept override;
    [[nodiscard]] HostRegisterResult PublishHostEvent(const void* payload,
                                                      std::uint32_t size) noexcept override;
    [[nodiscard]] bool PublishedFileApi(HostFileApiV1* out) const noexcept override;
    [[nodiscard]] const RuntimeHostApiV1& Abi() const noexcept override;

    [[nodiscard]] std::uint64_t BuildId() const noexcept { return abi_.buildId; }
    [[nodiscard]] bool HasFileApi() const noexcept;
    [[nodiscard]] std::uint32_t HostEventCount() const noexcept;
    [[nodiscard]] std::uint32_t LastHostEventSize() const noexcept;

private:
    // `kNoFileApi` means "nothing published yet"; 0 and 1 select a slot. A
    // registration writes the inactive slot first, so a concurrent reader keeps
    // a consistent snapshot of the previous table.
    static constexpr std::uint32_t kNoFileApi = 0xFFFFFFFFU;

    RuntimeHostApiV1 abi_{};
    HostFileApiV1 slots_[2]{};
    std::atomic<std::uint32_t> published_{kNoFileApi};
    std::atomic<std::uint32_t> hostEventCount_{0};
    std::atomic<std::uint32_t> lastHostEventSize_{0};
};

// Process-wide instance that `IsaacModRuntime_GetHostApi` publishes. It is a
// namespace-scope object (not a function-local static) so the Runtime keeps
// its no-`__cxa_guard__` static-initialization contract.
[[nodiscard]] RuntimeHostApiService& HostApiService() noexcept;

} // namespace isaac::runtime
