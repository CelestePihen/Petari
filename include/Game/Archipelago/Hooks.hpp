#pragma once

// Older CodeWarrior versions need one hook declaration per source line.
#ifndef __COUNTER__
#define __COUNTER__ __LINE__
#endif

#include "ArchipelagoHookSites.generated.hpp"
#include <kamek.h>

// Hook names are resolved and checked against the regional retail DOL by the build.
#define AP_CALL_HOOK(name, function) kmCall(AP_HOOK_##name, function)
