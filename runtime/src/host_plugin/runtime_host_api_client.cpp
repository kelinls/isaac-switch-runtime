#include "host_plugin/runtime_host_api_client.hpp"

namespace isaac::runtime::host_plugin {

std::uint64_t RuntimeHostApiClient::RegisterFileApi(const HostFileApi& api) const noexcept {
    if (registerFileApi_ == nullptr) {
        return 0;
    }
    return registerFileApi_(api.open, api.read, api.write, api.close);
}

} // namespace isaac::runtime::host_plugin
