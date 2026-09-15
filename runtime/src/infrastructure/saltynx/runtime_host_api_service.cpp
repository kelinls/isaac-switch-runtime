#include "infrastructure/saltynx/runtime_host_api_service.hpp"

#ifndef EXL_TEST_BUILD_ID
#define EXL_TEST_BUILD_ID 1ULL
#endif
static_assert(static_cast<std::uint64_t>(EXL_TEST_BUILD_ID) != 0,
              "the Runtime Build ID published over the host API must be non-zero");

namespace isaac::runtime {
namespace {

RuntimeHostApiService g_hostApiService{static_cast<std::uint64_t>(EXL_TEST_BUILD_ID)};

HostRegisterResult RegisterFilePortThunk(const HostFileApiV1* api) noexcept {
    if (api == nullptr) {
        return HostRegisterResult::RejectedIncomplete;
    }
    return g_hostApiService.RegisterFilePort(*api);
}

HostRegisterResult PublishHostEventThunk(const void* payload, std::uint32_t size) noexcept {
    return g_hostApiService.PublishHostEvent(payload, size);
}

} // namespace

RuntimeHostApiService::RuntimeHostApiService(std::uint64_t buildId) noexcept {
    abi_.abiVersion = kHostApiVersion1;
    abi_.structSize = kRuntimeHostApiSizeV1;
    abi_.buildId = buildId;
    abi_.capabilities = kHostCapabilityFilePort | kHostCapabilityHostEvent;
    abi_.RegisterFilePort = &RegisterFilePortThunk;
    abi_.PublishHostEvent = &PublishHostEventThunk;
}

HostRegisterResult RuntimeHostApiService::RegisterFilePort(const HostFileApiV1& api) noexcept {
    if (api.abiVersion != kHostApiVersion1 || api.structSize != kHostFileApiSizeV1) {
        return HostRegisterResult::RejectedVersion;
    }
    if (!api.complete()) {
        return HostRegisterResult::RejectedIncomplete;
    }
    const std::uint32_t current = published_.load(std::memory_order_acquire);
    const std::uint32_t next = current == kNoFileApi ? 0U : (current ^ 1U);
    slots_[next] = api;
    published_.store(next, std::memory_order_release);
    return HostRegisterResult::Accepted;
}

HostRegisterResult RuntimeHostApiService::PublishHostEvent(const void* payload,
                                                           std::uint32_t size) noexcept {
    if (payload == nullptr || size == 0) {
        return HostRegisterResult::RejectedIncomplete;
    }
    // Ordered before the counters so a reader that sees the count also sees the
    // size of the event that produced it. Task 7 replaces this with the event bus.
    lastHostEventSize_.store(size, std::memory_order_relaxed);
    hostEventCount_.fetch_add(1, std::memory_order_release);
    return HostRegisterResult::Accepted;
}

bool RuntimeHostApiService::PublishedFileApi(HostFileApiV1* out) const noexcept {
    const std::uint32_t index = published_.load(std::memory_order_acquire);
    if (index > 1U || out == nullptr) {
        return false;
    }
    *out = slots_[index];
    return true;
}

const RuntimeHostApiV1& RuntimeHostApiService::Abi() const noexcept {
    return abi_;
}

bool RuntimeHostApiService::HasFileApi() const noexcept {
    return published_.load(std::memory_order_acquire) <= 1U;
}

std::uint32_t RuntimeHostApiService::HostEventCount() const noexcept {
    return hostEventCount_.load(std::memory_order_acquire);
}

std::uint32_t RuntimeHostApiService::LastHostEventSize() const noexcept {
    return lastHostEventSize_.load(std::memory_order_acquire);
}

RuntimeHostApiService& HostApiService() noexcept {
    return g_hostApiService;
}

} // namespace isaac::runtime

extern "C" __attribute__((visibility("default"))) const isaac::runtime::RuntimeHostApiV1*
IsaacModRuntime_GetHostApi(std::uint32_t requestedVersion) {
    if (requestedVersion != isaac::runtime::kHostApiVersion1) {
        return nullptr;
    }
    return &isaac::runtime::HostApiService().Abi();
}
