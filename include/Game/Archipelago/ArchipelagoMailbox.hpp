#pragma once

#include <revolution/types.h>

struct ArchipelagoMailbox {
    u32 magic;
    u32 version;
    u32 testValue;
};

extern ArchipelagoMailbox gArchipelagoMailbox;
