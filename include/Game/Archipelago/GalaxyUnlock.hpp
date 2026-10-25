#pragma once

#include "Game/System/GameEventFlagChecker.hpp"
#include "revolution/types.h"

#define GALAXY_COUNT 41

struct GalaxyUnlockEntry {
    const char* stageName;
    u32 itemId;
};

namespace Archipelago {
    const GalaxyUnlockEntry* findGalaxyUnlockEntry(const char* stageName);
    bool isGalaxyUnlocked(const char* stageName);
    void unlockGalaxy(u32 itemId);
    bool onGalaxyUnlock(GameEventFlagChecker* checker, const char* galaxyName);
}  // namespace Archipelago
