#include "BootstrapConfig.hpp"
#include "kamekLoader.h"

struct DVDHandle {
    u32 unknown[12];
    u32 address, length, callback;
};
extern "C" {
void* OSGetMEM1ArenaLo();
void* OSGetMEM1ArenaHi();
void OSSetMEM1ArenaLo(void*);
void* OSAllocFromMEM1ArenaLo(u32, u32);
int DVDConvertPathToEntrynum(const char*);
bool DVDFastOpen(int, DVDHandle*);
int DVDReadPrio(DVDHandle*, void*, int, int, int);
bool DVDClose(DVDHandle*);
void createRootHeap__17HeapMemoryWatcherFv();
}
extern u8 _e_bss[];
extern void loadKamekBinary(const loaderFunctions*, const void*, u32);

struct Discovery {
    u32 magic0, magic1, version, size, region0, region1;
    u32 status, module, moduleSize, mailbox, mailboxSize, error;
};
extern "C" volatile Discovery gArchipelagoDiscovery = {0x4150424F, 0x4F543031, 1, sizeof(Discovery), AP_REGION, 0x30310000, 0,
                                                       0,          0,          0, AP_MAILBOX_SIZE,   0};

static u32 aligned(u32 value) {
    return (value + 31) & ~31u;
}
static void fail(u32 code) {
    gArchipelagoDiscovery.error = code;
    gArchipelagoDiscovery.status = 4;
    // Also used before OSInit completes, when OSFatal cannot safely be called.
    while (true) {
        asm { sync }
    }
}

extern "C" void apReserveArena(void* low) {
    u32 end = aligned((u32)_e_bss);
    if ((u32)low > AP_BOOT_BASE && (u32)low < end)
        fail(1);
    OSSetMEM1ArenaLo((void*)((u32)low < end ? end : (u32)low));
    gArchipelagoDiscovery.status = 1;
}

static void* reserve(u32 size) {
    u32 low = aligned((u32)OSGetMEM1ArenaLo());
    u32 high = (u32)OSGetMEM1ArenaHi();
    if (size > 0x01800000 || low < 0x80000000 || low > high || aligned(size) > high - low)
        fail(2);
    return OSAllocFromMEM1ArenaLo(aligned(size), 32);
}

static void* moduleAllocation(u32 size, bool code, const loaderFunctions*) {
    if (!code || size != gArchipelagoDiscovery.moduleSize)
        return 0;
    return (void*)gArchipelagoDiscovery.module;
}
static void loaderFatal(u32* fg, u32* bg, const char* message, ...) {
    OSFatal(fg, bg, message);
}
static const loaderFunctions functions = {
    OSReport, loaderFatal, DVDConvertPathToEntrynum, DVDFastOpen, DVDReadPrio, DVDClose, sprintf, moduleAllocation, 0};

extern "C" void apLoadAndCreateRootHeap() {
    if (gArchipelagoDiscovery.status != 1)
        fail(3);
    gArchipelagoDiscovery.status = 2;
    if (*(volatile u32*)0x80000000 != AP_REGION || *(volatile u16*)0x80000004 != 0x3031)
        fail(4);
    DVDHandle file;
    int entry = DVDConvertPathToEntrynum(AP_MODULE_PATH);
    if (entry < 0 || !DVDFastOpen(entry, &file))
        fail(5);
    if (file.length != AP_FILE_SIZE)
        fail(6);
    gArchipelagoDiscovery.moduleSize = AP_MODULE_SIZE;
    gArchipelagoDiscovery.module = (u32)reserve(AP_MODULE_SIZE);
    u32 scratchStart = (u32)OSGetMEM1ArenaLo();
    u32 readSize = aligned(file.length);
    u8* binary = (u8*)reserve(readSize);
    int read = DVDReadPrio(&file, binary, readSize, 0, 2);
    DVDClose(&file);
    if (read != (int)readSize)
        fail(7);
    u32 hash = 2166136261u;
    for (u32 i = 0; i < AP_FILE_SIZE; ++i)
        hash = (hash ^ binary[i]) * 16777619u;
    if (hash != AP_FILE_HASH)
        fail(8);
    const u32* header = (const u32*)binary;
    if (header[0] != 0x4B616D65 || header[1] != 0x6B000002 || header[2] != AP_BSS_SIZE || header[3] != AP_CODE_SIZE)
        fail(9);

    // Check every site before the loader installs any gameplay hook.
    for (u32 i = 0; i < AP_CALL_HOOK_COUNT; ++i) {
        if (*(volatile u32*)sAPCallHooks[i].address != sAPCallHooks[i].original)
            fail(12);
    }
    loadKamekBinary(&functions, binary, AP_FILE_SIZE);
    u32 base = gArchipelagoDiscovery.module;
    for (u32 i = 0; i < AP_CALL_HOOK_COUNT; ++i) {
        const APCallHook& hook = sAPCallHooks[i];
        u32 expected = 0x48000001 | ((base + hook.targetOffset - hook.address) & 0x03FFFFFC);
        if (*(volatile u32*)hook.address != expected)
            fail(13);
    }

    if (AP_MAILBOX_OFFSET > AP_MODULE_SIZE || AP_MAILBOX_SIZE > AP_MODULE_SIZE - AP_MAILBOX_OFFSET)
        fail(10);

    volatile u32* mailbox = (volatile u32*)(base + AP_MAILBOX_OFFSET);

    // mailbox[1] = mailbox version
    if (mailbox[0] != 0x4150534D || mailbox[1] != 3 || *(volatile u32*)(base + AP_READY_OFFSET) != 0x41505244)
        fail(10);
    // No heap exists yet; return only the last, temporary arena allocation.
    if ((u32)OSGetMEM1ArenaLo() != (u32)binary + readSize)
        fail(11);
    OSSetMEM1ArenaLo((void*)scratchStart);
    gArchipelagoDiscovery.mailbox = (u32)mailbox;
    asm { sync }
    gArchipelagoDiscovery.status = 3;
    OSReport("[AP] Hooks installed: %u\n", AP_CALL_HOOK_COUNT);
    OSReport("[AP] Ready: module=%p size=%u mailbox=%p\n", (void*)base, AP_MODULE_SIZE, (void*)mailbox);
    createRootHeap__17HeapMemoryWatcherFv();
}
