#include "Game/Archipelago/ArchipelagoMailbox.hpp"
#include "Game/Archipelago/DomeUnlock.hpp"
#include "Game/Archipelago/GalaxyUnlock.hpp"
#include "Game/Archipelago/Hooks.hpp"
#include "Game/System/GameSystem.hpp"
#include "Game/System/GameSystemSceneController.hpp"
#include "Game/Util/StringUtil.hpp"
#include "revolution/os.h"
#include "revolution/types.h"

namespace Archipelago {
    // état du nombre d'étoiles Archipelago reçus
    u32 receivedPowerStars = 0;

    void onGameUpdate(GameSystem* system) {
        system->update();

        GameSystemSceneController* controller = system->mSceneController;

        if (controller == nullptr)
            return;

        if (!MR::isEqualString(controller->mCurrSceneControlInfo.mScene, "Game"))
            return;

        if (MR::isEqualString(controller->mCurrSceneControlInfo.mStage, "FileSelect"))
            return;

        IncomingItemEvent event;

        if (gArchipelagoMailbox.incomingItems.pop(event)) {
            OSReport("[ARCHIPELAGO] Incoming item: ID=%u, Index=%u\n", event.itemId, event.itemIndex);

            if (event.itemId == AP_ITEM_POWER_STAR) {
                ++receivedPowerStars;

                OSReport("[ARCHIPELAGO] Power Star received! Total: %u, Index: %u\n", receivedPowerStars, event.itemIndex);
            }

            else if (event.itemId >= 100 && event.itemId <= 140) {
                // vérifie si l'item est un déblocage de galaxy et la débloque si cela en est un
                unlockGalaxy(event.itemId);
            }

            else if (event.itemId >= 200 && event.itemId <= 205) {
                // vérifie si l'item est un déblocage de dôme et la débloque si cela en est un
                unlockDome(event.itemId);
            }

            else {
                OSReport("[ARCHIPELAGO] Unknown item: ID: %u, Index: %u\n", event.itemId, event.itemIndex);
            }
        }
    }
}  // namespace Archipelago

AP_CALL_HOOK(GameUpdate, Archipelago::onGameUpdate);
