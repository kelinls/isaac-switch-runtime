#include "composition/runtime_kernel.hpp"

namespace isaac::runtime {
RuntimeKernel& RuntimeKernel::Instance() noexcept {
    // Static storage with a constant-initializable default constructor: no
    // dynamic allocation, no construction-order dependency on .init_array and
    // no guard variable in the generated code.
    static RuntimeKernel kernel;
    return kernel;
}

Status RuntimeKernel::Initialize(const RuntimeDependencies& dependencies) noexcept {
    if (initialized_) {
        context_.lastFailure = Status{StatusCode::InvalidState};
        return context_.lastFailure;
    }
    dependencies_ = dependencies;
    context_ = RuntimeContext{};
    context_.buildId = dependencies.buildId;
    initialized_ = true;
    return Status::Ok();
}

void RuntimeKernel::PublishEvent(std::uint32_t eventId) noexcept {
    ++publishedEventCount_;
    if (dependencies_.events == nullptr) {
        return;
    }
    EventHeader header{};
    header.id = eventId;
    header.sequence = publishedEventCount_;
    header.buildId = context_.buildId;
    dependencies_.events->Publish(header, nullptr, 0);
}

} // namespace isaac::runtime
