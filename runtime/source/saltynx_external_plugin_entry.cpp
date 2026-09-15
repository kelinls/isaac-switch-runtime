#include "program/saltynx_external_plugin_probe.hpp"

#include <cstdint>

namespace {
#if EXL_DIAGNOSTIC_STAGE == 133
// ISAAC_SP: SaltyNX external-plugin link diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5350ULL;
#elif EXL_DIAGNOSTIC_STAGE == 134
// ISAAC_SR: SaltyNX external-plugin read diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5352ULL;
#elif EXL_DIAGNOSTIC_STAGE == 135
// ISAAC_SW: SaltyNX external-plugin write-readback diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5357ULL;
#elif EXL_DIAGNOSTIC_STAGE == 136
// ISAAC_SV: SaltyNX external-plugin persisted-file diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5356ULL;
#elif EXL_DIAGNOSTIC_STAGE == 137
// ISAAC_SC: SaltyNX external-plugin state-container diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5343ULL;
#elif EXL_DIAGNOSTIC_STAGE == 138
// ISAAC_SX: SaltyNX external-plugin state-container persisted-read diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5358ULL;
#elif EXL_DIAGNOSTIC_STAGE == 139
// ISAAC_SB: SaltyNX external-plugin/default Runtime bridge diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5342ULL;
#elif EXL_DIAGNOSTIC_STAGE == 140
// ISAAC_ST: SaltyNX Core file-table transfer diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5354ULL;
#elif EXL_DIAGNOSTIC_STAGE == 141
// ISAAC_SU: Runtime-internal SaltyNX file I/O diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5355ULL;
#elif EXL_DIAGNOSTIC_STAGE == 142
// ISAAC_SQ: Runtime-internal versioned state-container diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5351ULL;
#elif EXL_DIAGNOSTIC_STAGE == 143
// ISAAC_SY: Runtime-internal persisted state-container diagnostic magic.
constexpr std::uint64_t kDiagnosticMagic = 0x49534141435F5359ULL;
#else
#error "SaltyNX external-plugin entry only supports diagnostic stages 133 through 143"
#endif

[[noreturn]] void Finish(std::uint64_t detail) {
    register std::uint64_t reason asm("x0") = 2;
    register std::uint64_t magic asm("x1") = kDiagnosticMagic;
    register std::uint64_t value asm("x2") = detail;
    asm volatile("svc #0x26" : "+r"(reason) : "r"(magic), "r"(value) : "memory");
    asm volatile("svc #0x07" ::: "memory");
    __builtin_unreachable();
}
} // namespace

extern "C" void exl_main(void*, void*) {
    constexpr std::uint64_t kStage = EXL_DIAGNOSTIC_STAGE;
    Finish((kStage << 32) | RunSaltyNxExternalPluginProbe());
}
