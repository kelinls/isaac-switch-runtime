#include "bootstrap/runtime_bootstrap.hpp"

#include "domain/runtime/runtime_state_machine.hpp"

namespace isaac::runtime {
namespace {

std::uint64_t BuildId() noexcept {
#if defined(EXL_TEST_BUILD_ID)
    return static_cast<std::uint64_t>(EXL_TEST_BUILD_ID);
#else
    return 0;
#endif
}

} // namespace

Status RuntimeBootstrap::Start(const RuntimeDependencies& dependencies) noexcept {
    RuntimeKernel& kernel = RuntimeKernel::Instance();
    const Status initialized = kernel.Initialize(dependencies);
    if (!initialized.ok()) {
        return initialized;
    }
    RuntimeContext& context = kernel.context();
    const Status entered = RuntimeStateMachine::Apply(context, RuntimeEvent::ModuleEntered);
    if (!entered.ok()) {
        return entered;
    }
    kernel.PublishEvent(kEventModuleEntered);
    return Status::Ok();
}

Status RuntimeBootstrap::Start() noexcept {
    RuntimeDependencies dependencies{};
    dependencies.buildId = BuildId();
    return Start(dependencies);
}

} // namespace isaac::runtime
