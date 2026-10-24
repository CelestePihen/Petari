#include "Game/Archipelago/ArchipelagoMailbox.hpp"
#include "Game/Archipelago/Hooks.hpp"
#include "Game/System/GameSequenceFunction.hpp"
#include "revolution/os.h"
#include <cstring>

namespace Archipelago {
    void onStageResult() {
        const char* starName = GameSequenceFunction::getClearedStageName();
        s32 powerStarId = GameSequenceFunction::getClearedPowerStarId();

        OSReport("[ARCHIPELAGO] Star collected: %s, ID: %d\n", starName, powerStarId);

        StarCollectedEvent event = {};
        event.starId = powerStarId;

        if (starName != 0) {
            strncpy(event.galaxyName, starName, sizeof(event.galaxyName) - 1);
        }

        if (!gArchipelagoMailbox.starEvents.push(event)) {
            OSReport("[ARCHIPELAGO] Event queue full!\n");
        }

        // exécuter ce que le jeu faisait normalement
        GameSequenceFunction::updateGameDataAndSequenceAfterStageResultSequence();
    }
}  // namespace Archipelago

AP_CALL_HOOK(StageResult, Archipelago::onStageResult);
