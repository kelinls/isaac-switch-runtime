#include "diagnostics/diagnostic_event_bus.hpp"

// The bus is a small header-only policy object. This translation unit exists so
// the diagnostics core compiles as separate units the way the production build
// does and so a future out-of-line sink (the bounded file journal) can be added
// without moving the header.
namespace isaac::runtime {

// Intentionally empty; see above.

} // namespace isaac::runtime
