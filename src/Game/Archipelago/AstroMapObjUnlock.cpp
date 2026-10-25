#include "Game/Archipelago/AstroMapObjUnlock.hpp"
#include "Game/Archipelago/DomeUnlock.hpp"
#include "Game/Archipelago/Hooks.hpp"
#include "Game/LiveActor/Nerve.hpp"
#include "Game/LiveActor/Spine.hpp"
#include "Game/Util/SceneUtil.hpp"
#include "Game/Util/StringUtil.hpp"

namespace NrvAstroMapObj {
    class AstroMapObjNrvDead : public Nerve {
    public:
        virtual void execute(Spine* pSpine) const;
        static AstroMapObjNrvDead sInstance;
    };
}  // namespace NrvAstroMapObj

namespace Archipelago {
    void onAstroMapObjControl(AstroMapObj* obj) {
        if (MR::isEqualStageName("AstroGalaxy") && MR::isEqualString(obj->_CC, "AstroDomeEntrance") && obj->_D8 >= 1 && obj->_D8 <= DOME_COUNT &&
            isDomeUnlocked(obj->_D8)) {
            if (obj->isNerve(&NrvAstroMapObj::AstroMapObjNrvDead::sInstance)) {
                obj->selectNrvWait();
            }
        }

        obj->MapObjActor::control();
    }
}  // namespace Archipelago

AP_CALL_HOOK(AstroMapObjControl, Archipelago::onAstroMapObjControl);
