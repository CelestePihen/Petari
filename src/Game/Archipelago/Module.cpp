#include "Game/Archipelago/ArchipelagoMailbox.hpp"

// The bootstrap checks this to distinguish loaded data from completed C++ initialization.
__declspec(export) volatile u32 gArchipelagoModuleReady;

namespace {
    struct ModuleInit {
        ModuleInit() {
            gArchipelagoModuleReady = gArchipelagoModuleReady == 0 ? 0x41505244 : 0x42414421;
        }
    } sModuleInit;
}
