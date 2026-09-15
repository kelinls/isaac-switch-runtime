#include "saltynx_external_plugin_persistence_bridge.hpp"

extern "C" void exl_main(void*, void*) {
    RunSaltyNxPersistenceBridge();
}
