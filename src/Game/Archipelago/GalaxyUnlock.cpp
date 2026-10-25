#include "Game/Archipelago/GalaxyUnlock.hpp"
#include "Game/Archipelago/Hooks.hpp"
#include "Game/System/GameDataConst.hpp"
#include "Game/System/GameDataHolder.hpp"
#include "Game/System/GameEventFlagChecker.hpp"

#include "Game/Util/StringUtil.hpp"
#include "revolution/types.h"

namespace Archipelago {
    // état des items Archipelago pour gérer les galaxies reçus
    bool unlockedGalaxies[GALAXY_COUNT] = {};

    // correspondance entre une galaxie et son item Archipelago
    static const GalaxyUnlockEntry galaxyUnlockEntries[] = {
        {"BeltConveyerExGalaxy", 100},   {"CocoonExGalaxy", 101},        {"TearDropGalaxy", 102},         {"FishTunnelGalaxy", 103},
        {"TransformationExGalaxy", 104}, {"SnowCapsuleGalaxy", 105},     {"TeresaMario2DGalaxy", 106},    {"KoopaBattleVs3Galaxy", 107},
        {"HeavensDoorGalaxy", 108},      {"EggStarGalaxy", 109},         {"HoneyBeeKingdomGalaxy", 110},  {"SurfingLv1Galaxy", 111},
        {"FlipPanelExGalaxy", 112},      {"TriLegLv1Galaxy", 113},       {"StarDustGalaxy", 114},         {"BattleShipGalaxy", 115},
        {"TamakoroExLv1Galaxy", 116},    {"BreakDownPlanetGalaxy", 117}, {"KoopaBattleVs1Galaxy", 118},   {"HeavenlyBeachGalaxy", 119},
        {"PhantomGalaxy", 120},          {"CubeBubbleExLv1Galaxy", 121}, {"OceanFloaterLandGalaxy", 122}, {"KoopaJrShipLv1Galaxy", 123},
        {"CosmosGardenGalaxy", 124},     {"IceVolcanoGalaxy", 125},      {"SandClockGalaxy", 126},        {"HoneyBeeExGalaxy", 127},
        {"KoopaBattleVs2Galaxy", 128},   {"ReverseKingdomGalaxy", 129},  {"OceanRingGalaxy", 130},        {"FactoryGalaxy", 131},
        {"SkullSharkGalaxy", 132},       {"FloaterOtaKingGalaxy", 133},  {"OceanPhantomCaveGalaxy", 134}, {"CannonFleetGalaxy", 135},
        {"HellProminenceGalaxy", 136},   {"DarkRoomGalaxy", 137},        {"TamakoroExLv2Galaxy", 138},    {"CubeBubbleExLv2Galaxy", 139},
        {"SurfingLv2Galaxy", 140},
    };

    const GalaxyUnlockEntry* findGalaxyUnlockEntry(const char* stageName) {
        for (int i = 0; i < GALAXY_COUNT; i++) {
            const GalaxyUnlockEntry& entry = galaxyUnlockEntries[i];

            if (MR::isEqualString(stageName, entry.stageName)) {
                return &entry;
            }
        }

        return nullptr;
    }

    bool isGalaxyUnlocked(const char* stageName) {
        const GalaxyUnlockEntry* entry = findGalaxyUnlockEntry(stageName);

        if (entry == nullptr) {
            return false;
        }

        u32 id = entry->itemId - 100;
        return unlockedGalaxies[id];
    }

    void unlockGalaxy(u32 itemId) {
        if (itemId >= 100 && itemId <= 140) {
            u32 posInArray = itemId - 100;
            unlockedGalaxies[posInArray] = true;
        }
    }

    bool onGalaxyUnlock(GameEventFlagChecker* checker, const char* galaxyName) {
        const GalaxyUnlockEntry* entry = findGalaxyUnlockEntry(galaxyName);

        // 107 = KoopaBattleVs3Galaxy -> galaxie contre Bowser 3 -> il faudra donc ajouter la vérif du nb d'étoiles
        // 108 = HeavensDoorGalaxy -> galaxie du "tutoriel", il faudra vérif si on est à la 1ère ou 2ème visite
        if (entry == nullptr || entry->itemId == 107 || entry->itemId == 108) {
            int requiredStars = GameDataConst::getPowerStarNumToOpenGalaxy(galaxyName);

            if (checker->mDataHolder->calcCurrentPowerStarNum() < requiredStars)
                return false;

            return checker->isOnGalaxyDepended(galaxyName);
        }

        if (unlockedGalaxies[entry->itemId - 100]) {
            return checker->isOnGalaxyDepended(galaxyName);
        }

        return false;
    }
}  // namespace Archipelago

AP_CALL_HOOK(GalaxyUnlock, Archipelago::onGalaxyUnlock);
