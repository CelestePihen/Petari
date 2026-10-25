#pragma once

#include "revolution/types.h"

#define DOME_COUNT 6

namespace Archipelago {
    bool isDomeUnlocked(s32 domeId);
    void unlockDome(u32 itemId);

    bool onDomeRevival(const char* objName, s32 domeId);
    bool onDomeOpen(const char* objName, s32 domeId);
    bool onDomeEnter(int domeId);
}  // namespace Archipelago
