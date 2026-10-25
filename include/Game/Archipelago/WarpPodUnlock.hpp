#pragma once

#include "Game/MapObj/WarpPod.hpp"

namespace Archipelago {
    bool isWarpPodGroupUnlocked(s32 groupId);
    void onWarpPodMovement(WarpPod* warpPod);
}
