#!/usr/bin/env python3
"""Refresh a retail static RSO export table from the linked PowerPC ELF."""
import argparse
import struct
from pathlib import Path

# RSOStaticLocateObject uses SDK section IDs, not ELF section indices.
RSO_SECTIONS = {
    1: '.init', 2: '.text', 5: '.rodata', 6: '.data', 7: '.bss',
    8: '.sdata', 9: '.sdata2', 11: '.sbss', 12: '.sbss2',
}


def checked_slice(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError('Table extends beyond the input file')
    return data[offset:offset + size]


def cstring(data, offset):
    if not 0 <= offset < len(data) or b'\0' not in data[offset:]:
        raise ValueError('Invalid symbol string offset')
    return data[offset:data.index(0, offset)].decode('ascii')


def elf_symbols(data):
    if data[:7] != b'\x7fELF\x01\x02\x01':
        raise ValueError('Expected a big-endian ELF32 file')
    header = struct.unpack('>16sHHIIIIIHHHHHH', checked_slice(data, 0, 52))
    if header[1:3] != (2, 20) or header[11] != 40 or not header[12]:
        raise ValueError('Expected a linked PowerPC executable with section headers')
    sections = [struct.unpack('>10I', checked_slice(data, header[6] + i * 40, 40))
                for i in range(header[12])]
    shstrings = sections[header[13]]
    names = checked_slice(data, shstrings[4], shstrings[5])
    symbols = {}
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        strings = sections[section[6]]
        strings = checked_slice(data, strings[4], strings[5])
        if section[9] != 16 or section[5] % 16:
            raise ValueError('Invalid ELF symbol table')
        for offset in range(section[4], section[4] + section[5], 16):
            name, value, _, info, _, index = struct.unpack('>IIIBBH', checked_slice(data, offset, 16))
            if info >> 4 not in (1, 2) or index == 0:  # Defined global or weak
                continue
            name = cstring(strings, name)
            if index == 0xfff1:  # SHN_ABS
                symbol = (value, None, 0)
            else:
                target = sections[index]
                symbol = (value, cstring(names, target[0]), target[3])
            if name in symbols and symbols[name] != symbol:
                raise ValueError('Ambiguous ELF symbol: ' + name)
            symbols[name] = symbol
    return symbols


def update_sel(original, elf):
    data = bytearray(original)
    checked_slice(data, 0, 0x58)
    # Accept an unlocated static export-only SEL, not a loaded or dynamic RSO.
    if any(struct.unpack_from('>II', data, 0)) or any(data[0x20:0x30]):
        raise ValueError('Expected an unlocated static SEL')
    if any(struct.unpack_from('>I', data, offset)[0] for offset in (0x34, 0x3c, 0x50)):
        raise ValueError('Expected a static SEL without relocations or imports')
    section_count, section_offset = struct.unpack_from('>II', data, 8)
    checked_slice(data, section_offset, section_count * 8)
    offset, size, strings = struct.unpack_from('>III', data, 0x40)
    checked_slice(data, offset, size)
    if size % 16:
        raise ValueError('Invalid SEL export table size')
    symbols = elf_symbols(elf)
    changed = 0
    for entry in range(offset, offset + size, 16):
        name_offset, old_value, section, _ = struct.unpack_from('>IIII', data, entry)
        name = cstring(data, strings + name_offset)
        if name not in symbols:
            raise ValueError('Missing ELF export: ' + name)
        value, elf_section, base = symbols[name]
        if section == 0xfff1:
            if elf_section is not None:
                raise ValueError('Expected absolute ELF export: ' + name)
        elif section in RSO_SECTIONS and elf_section == RSO_SECTIONS[section]:
            value -= base
        else:
            raise ValueError('ELF/RSO section mismatch: ' + name)
        struct.pack_into('>I', data, entry + 4, value)
        changed += value != old_value
    return bytes(data), changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sel', type=Path)
    parser.add_argument('elf', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.resolve() in (args.sel.resolve(), args.elf.resolve()):
        parser.error('Output must differ from both input files')
    try:
        data, changed = update_sel(args.sel.read_bytes(), args.elf.read_bytes())
    except (ValueError, IndexError, struct.error) as error:
        parser.exit(1, str(error) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f'{args.output}: updated {changed} exports')


if __name__ == '__main__':
    main()
