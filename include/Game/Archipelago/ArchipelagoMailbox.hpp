#pragma once

#include "Game/Archipelago/EventQueue.hpp"
#include <revolution/types.h>

#define AP_MAILBOX_MAGIC 0x4150534D
#define AP_MAILBOX_VERSION 2
#define AP_EVENT_CAPACITY 32
#define AP_GALAXY_NAME_SIZE 64

struct StarCollectedEvent {
    char galaxyName[AP_GALAXY_NAME_SIZE];
    u32 starId;
};

struct ArchipelagoMailbox {
    u32 magic;
    u32 version;
    u32 testValue;

    EventQueue< StarCollectedEvent, AP_EVENT_CAPACITY > starEvents;
};

extern ArchipelagoMailbox gArchipelagoMailbox;
