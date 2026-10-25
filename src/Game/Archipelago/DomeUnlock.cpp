#include "Game/Archipelago/DomeUnlock.hpp"
#include "Game/Archipelago/Hooks.hpp"
#include "Game/MapObj/AstroMapObjFunction.hpp"
#include "Game/Util/EventUtil.hpp"
#include "Game/Util/StringUtil.hpp"
#include "revolution/os.h"
#include "revolution/types.h"

namespace Archipelago {
    // état des items Archipelago pour gérer les dômes reçus
    bool unlockedDomes[DOME_COUNT] = {};

    bool isDomeUnlocked(s32 domeId) {
        if (domeId >= 1 && domeId <= DOME_COUNT) {
            return unlockedDomes[domeId - 1];
        }

        return false;
    }

    void unlockDome(u32 itemId) {
        if (itemId >= 200 && itemId <= 205) {
            u32 posInArray = itemId - 200;
            unlockedDomes[posInArray] = true;
            OSReport("[ARCHIPELAGO] Dome Unlock received: ID=%u\n", itemId);
        }
    }

    bool onDomeRevival(const char* objName, s32 domeId) {
        if (MR::isEqualString(objName, "AstroDomeEntrance") && domeId >= 1 && domeId <= DOME_COUNT) {
            return isDomeUnlocked(domeId);
        }

        return AstroMapObjFunction::isAlreadyRevival(objName, domeId);
    }

    bool onDomeOpen(const char* objName, s32 domeId) {
        if (MR::isEqualString(objName, "AstroDomeEntrance") && domeId >= 1 && domeId <= DOME_COUNT) {
            return isDomeUnlocked(domeId);
        }

        return AstroMapObjFunction::isAlreadyOpen(objName, domeId);
    }

    bool onDomeEnter(int domeId) {
        if (domeId >= 1 && domeId <= DOME_COUNT) {
            return isDomeUnlocked(domeId);
        }

        return MR::hasGrandStar(domeId);
    }
}  // namespace Archipelago

AP_CALL_HOOK(DomeRevival, Archipelago::onDomeRevival);
AP_CALL_HOOK(DomeOpen, Archipelago::onDomeOpen);
AP_CALL_HOOK(DomeEnter, Archipelago::onDomeEnter);
