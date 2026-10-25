#include "Game/Archipelago/WarpPodUnlock.hpp"
#include "Game/Archipelago/DomeUnlock.hpp"
#include "Game/Archipelago/Hooks.hpp"
#include "Game/MapObj/WarpPod.hpp"
#include "Game/Util/JMapIdInfo.hpp"
#include "Game/Util/SceneUtil.hpp"
#include "revolution/types.h"

namespace Archipelago {
    bool isWarpPodGroupUnlocked(s32 groupId) {
        switch (groupId) {
        case 18:  // Fountain
            return isDomeUnlocked(2);

        case 0:   // Engine Room
        case 19:  // Raccourci Engine Room
            return isDomeUnlocked(5);

        case 1:   // Accès Garden via Engine Room
        case 17:  // Accès Garden via la Salle de jeux
        case 22:  // Raccourci direct vers Garden
            return isDomeUnlocked(6);

        default:
            return false;
        }
    }

    void onWarpPodMovement(WarpPod* warpPod) {
        if (MR::isEqualStageName("AstroGalaxy") && warpPod->mJMapIdInfo != nullptr && warpPod->mJMapIdInfo->mZoneID == 0 &&
            warpPod->mPairPod != nullptr && isWarpPodGroupUnlocked(warpPod->mGroupId)) {
            WarpPod* pair = warpPod->mPairPod;

            WarpPod* firstPod = !warpPod->mArg7 ? warpPod : pair;

            if (!firstPod->mArg7 && firstPod->mIsInactive) {
                firstPod->appear();
            }
        }

        warpPod->LiveActor::movement();
    }
}  // namespace Archipelago

AP_CALL_HOOK(WarpPodMovement, Archipelago::onWarpPodMovement);
