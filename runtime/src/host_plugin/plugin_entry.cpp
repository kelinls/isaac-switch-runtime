#include "host_plugin/saltynx_host_plugin.hpp"

// SaltyNX loads the plugin ELF and branches to `exl_main` from the module crt0.
// The plugin registers the file table once and returns; all further work belongs
// to the Runtime's own callbacks, never to this thread.
extern "C" void exl_main(void*, void*) {
    isaac::runtime::host_plugin::SaltyNxCoreResolver resolver{};
    isaac::runtime::host_plugin::SaltyNxHostPlugin plugin{resolver};
    // Report the outcome once, through the plugin's own log file. This is the only
    // file write the plugin performs, and it tells a reader which stage was reached
    // even when the Runtime never hears from the plugin.
    static_cast<void>(plugin.Run(/*report=*/true));
}
