#include "Game/Archipelago/Hooks.hpp"
#include "Game/System/GameSequenceFunction.hpp"
#include "revolution/os.h"

namespace Archipelago {
    void onStageResult() {
        const char* starName = GameSequenceFunction::getClearedStageName();
        s32 powerStarId = GameSequenceFunction::getClearedPowerStarId();

        OSReport("[ARCHIPELAGO] Star collected: %s, ID: %d\n", starName, powerStarId);

        GameSequenceFunction::updateGameDataAndSequenceAfterStageResultSequence();
    }
}  // namespace Archipelago

AP_CALL_HOOK(StageResult, Archipelago::onStageResult);
