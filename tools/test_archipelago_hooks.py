#!/usr/bin/env python3
"""Run the built SMG bootstrap/loader and StageResult wrapper in Unicorn.

SDK calls are stubbed; this is an isolated PowerPC execution test, not gameplay.
Requires unicorn==2.1.4 and completed PAL/USA Kamek builds.
"""

import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_archipelago import (
    REGIONS,
    align,
    branch,
    dol_sections,
    map_symbols,
    symbols,
)


class BootstrapError(RuntimeError):
    pass


def exercise(region, conflict=False):
    from unicorn import Uc, UC_ARCH_PPC, UC_MODE_32, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE
    import unicorn.ppc_const as ppc

    out = ROOT / "build/kamek" / region
    manifest = json.loads((out / "manifest.json").read_text())
    binary = (out / f"{region}.bin").read_bytes()
    boot_symbols = map_symbols(out / "bootstrap.map")
    game_symbols = symbols(region)
    hooks = manifest["module_hooks"]
    stage_hooks = [hook for hook in hooks if hook["name"] == "StageResult"]
    if len(stage_hooks) != 1:
        raise RuntimeError(
            "This execution fixture requires the StageResult example hook"
        )

    machine = Uc(UC_ARCH_PPC, UC_MODE_32 | UC_MODE_BIG_ENDIAN)
    machine.mem_map(0x80000000, 0x01800000)
    dol = (out / "main.dol").read_bytes()
    for offset, address, size in dol_sections(dol):
        if size:
            machine.mem_write(address, dol[offset : offset + size])
    machine.mem_write(0x80000000, region.encode() + b"\0\0")
    # Synthetic test stack/string/sentinel, outside the simulated game's arena.
    stack, stage_name, sentinel = 0x817E0000, 0x817F0000, 0x817FF000
    machine.mem_write(stage_name, b"TestGalaxy\0")
    machine.reg_write(ppc.UC_PPC_REG_1, stack)
    machine.reg_write(ppc.UC_PPC_REG_MSR, 0x2000)
    descriptor = manifest["discovery"]
    arena_low = manifest["bootstrap_base"]
    allocations, root_heaps, logs = [], [], []
    original_calls = []
    stubs = {}

    def reg(index):
        return machine.reg_read(getattr(ppc, f"UC_PPC_REG_{index}"))

    def word(address):
        return struct.unpack(">I", machine.mem_read(address, 4))[0]

    def string(address):
        result = bytearray()
        for index in range(1024):
            byte = bytes(machine.mem_read(address + index, 1))
            if byte == b"\0":
                return result.decode("utf-8", errors="replace")
            result.extend(byte)
        raise RuntimeError("Unterminated string in execution fixture")

    def stub(name):
        def register(function):
            stubs[game_symbols[name][0]] = function
            return function

        return register

    @stub("OSGetMEM1ArenaLo")
    def arena_get_low():
        return arena_low

    @stub("OSGetMEM1ArenaHi")
    def arena_get_high():
        return 0x81700000

    @stub("OSSetMEM1ArenaLo")
    def arena_set_low():
        nonlocal arena_low
        arena_low = reg(3)

    @stub("OSAllocFromMEM1ArenaLo")
    def allocate():
        nonlocal arena_low
        assert reg(4) == 32
        result = align(arena_low)
        arena_low = result + reg(3)
        allocations.append((result, reg(3)))
        return result

    @stub("DVDConvertPathToEntrynum")
    def dvd_entry():
        assert string(reg(3)) == f"/Archipelago/{region}.bin"
        return 1

    @stub("DVDFastOpen")
    def dvd_open():
        assert reg(3) == 1
        machine.mem_write(reg(4), bytes(52) + struct.pack(">I", len(binary)) + bytes(4))
        return 1

    @stub("DVDReadPrio")
    def dvd_read():
        assert reg(5) == align(len(binary)) and reg(6) == 0
        machine.mem_write(reg(4), binary.ljust(reg(5), b"\0"))
        return reg(5)

    @stub("DVDClose")
    def dvd_close():
        return 1

    @stub("OSReport")
    def report():
        format_string = string(reg(3))
        if format_string.startswith("[ARCHIPELAGO] Star collected:"):
            logs.append(
                dict(format=format_string, stage=string(reg(4)), star_id=reg(5))
            )

    @stub("OSFatal")
    def fatal():
        raise RuntimeError("Kamek called OSFatal in the execution fixture")

    @stub("createRootHeap__17HeapMemoryWatcherFv")
    def root_heap():
        root_heaps.append(arena_low)

    @stub("getClearedStageName__20GameSequenceFunctionFv")
    def cleared_stage():
        return stage_name

    @stub("getClearedPowerStarId__20GameSequenceFunctionFv")
    def cleared_star():
        return 42

    @stub("updateGameDataAndSequenceAfterStageResultSequence__20GameSequenceFunctionFv")
    def original_stage_result():
        original_calls.append(True)

    def step(uc, address, size, user):
        if word(descriptor + 24) == 4:
            raise BootstrapError(f"Bootstrap error {word(descriptor + 44)}")
        if address in stubs:
            result = stubs[address]()
            if result is not None:
                machine.reg_write(ppc.UC_PPC_REG_3, result)
            machine.reg_write(ppc.UC_PPC_REG_PC, machine.reg_read(ppc.UC_PPC_REG_LR))

    machine.hook_add(UC_HOOK_CODE, step)

    def call(address, stop=sentinel, argument=0):
        machine.reg_write(ppc.UC_PPC_REG_3, argument)
        machine.reg_write(ppc.UC_PPC_REG_LR, sentinel)
        machine.emu_start(address, stop, count=2000000)
        assert (
            machine.reg_read(ppc.UC_PPC_REG_PC) == stop
        ), "PowerPC instruction budget exhausted"

    call(boot_symbols["apReserveArena"][0], argument=arena_low)
    assert word(descriptor + 24) == 1
    assert arena_low == manifest["bootstrap_base"] + manifest["bootstrap_size"]
    if conflict:
        machine.mem_write(hooks[0]["address"], struct.pack(">I", 0x60000000))
    try:
        call(boot_symbols["apLoadAndCreateRootHeap"][0])
    except BootstrapError as error:
        if not conflict or str(error) != "Bootstrap error 12":
            raise
        for index, hook in enumerate(hooks):
            assert word(hook["address"]) == (
                0x60000000 if index == 0 else hook["expected"]
            )
        assert not root_heaps and not original_calls
        return dict(
            region=region,
            conflicting_instruction="rejected before installing hooks",
            error=12,
        )
    assert not conflict, "Conflicting game instruction was accepted"
    assert word(descriptor + 24) == 3
    module = word(descriptor + 28)
    assert allocations[0] == (module, align(manifest["module_size"]))
    assert root_heaps == [module + align(manifest["module_size"])]
    assert word(module + manifest["ready_offset"]) == 0x41505244
    assert word(module + manifest["mailbox_offset"]) == 0x4150534D
    assert word(module + manifest["mailbox_offset"] + 8) == 0
    for hook in hooks:
        assert word(hook["address"]) == branch(
            hook["address"], module + hook["target_offset"]
        )

    # Execute the real installed BL and C++ wrapper; SDK/game service bodies are stubbed.
    before = {}
    for index in [2, 13, *range(14, 32)]:
        value = 0x12340000 + index
        machine.reg_write(getattr(ppc, f"UC_PPC_REG_{index}"), value)
        before[index] = value
    call(stage_hooks[0]["address"], stop=stage_hooks[0]["address"] + 4)
    assert reg(1) == stack
    assert all(reg(index) == value for index, value in before.items())
    assert len(original_calls) == 1
    assert (
        len(logs) == 1 and logs[0]["stage"] == "TestGalaxy" and logs[0]["star_id"] == 42
    )

    # Retail sections may change only at bootstrap hooks and declared module call sites.
    retail = (ROOT / f"orig/{region}/sys/main.dol").read_bytes()
    replacements = {hook["address"]: hook["replacement"] for hook in manifest["hooks"]}
    replacements.update(
        {
            hook["address"]: branch(hook["address"], module + hook["target_offset"])
            for hook in hooks
        }
    )
    for offset, address, size in dol_sections(retail):
        if size:
            expected = bytearray(retail[offset : offset + size])
            for site, instruction in replacements.items():
                if address <= site < address + size:
                    struct.pack_into(">I", expected, site - address, instruction)
            assert (
                bytes(machine.mem_read(address, size)) == expected
            ), f"Unexpected retail write at {address:08X}"
    return dict(
        region=region,
        module=module,
        hooks=len(hooks),
        log=logs[0],
        original_calls=1,
        return_and_saved_registers="preserved",
        retail_sections="only declared hook words changed",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=REGIONS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    regions = [args.region] if args.region else REGIONS
    results = [
        exercise(region, conflict) for region in regions for conflict in [False, True]
    ]
    result = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result, end="")


if __name__ == "__main__":
    main()
