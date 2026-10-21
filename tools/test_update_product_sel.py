import struct
import unittest

from update_product_sel import update_sel


def fixtures():
    names = b'\0.text\0.sdata\0.symtab\0.strtab\0.shstrtab\0'
    strings = b'\0foo\0_SDA_BASE_\0'
    elf = bytearray(0x400)
    struct.pack_into('>16sHHIIIIIHHHHHH', elf, 0,
                     b'\x7fELF\x01\x02\x01' + b'\0' * 9,
                     2, 20, 1, 0, 0, 0x200, 0, 52, 0, 0, 40, 6, 5)
    sections = [
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (1, 1, 6, 0x800070a0, 0x80, 0x80, 0, 0, 4, 0),
        (7, 1, 3, 0x8069cce0, 0x100, 4, 0, 0, 4, 0),
        (14, 2, 0, 0, 0x120, 48, 4, 1, 4, 16),
        (22, 3, 0, 0, 0x160, len(strings), 0, 0, 1, 0),
        (30, 3, 0, 0, 0x180, len(names), 0, 0, 1, 0),
    ]
    for i, section in enumerate(sections):
        struct.pack_into('>10I', elf, 0x200 + i * 40, *section)
    struct.pack_into('>IIIBBH', elf, 0x130, 1, 0x80007100, 4, 0x12, 0, 1)
    struct.pack_into('>IIIBBH', elf, 0x140, 5, 0x806a4ce0, 0, 0x10, 0, 0xfff1)
    elf[0x160:0x160 + len(strings)] = strings
    elf[0x180:0x180 + len(names)] = names
    sel = bytearray(0x100)
    struct.pack_into('>II', sel, 8, 14, 0x58)
    struct.pack_into('>III', sel, 0x40, 0xc8, 32, 0xe8)
    struct.pack_into('>IIII', sel, 0xc8, 0, 0x20, 2, 0x123)
    struct.pack_into('>IIII', sel, 0xd8, 4, 0x806a4ca0, 0xfff1, 0x456)
    sel[0xe8:0xe8 + len(strings) - 1] = strings[1:]
    return bytes(sel), bytes(elf)


class UpdateSelTest(unittest.TestCase):
    def test_offsets_absolute_exports_and_idempotence(self):
        sel, elf = fixtures()
        updated, count = update_sel(sel, elf)
        self.assertEqual(count, 2)
        self.assertEqual(struct.unpack_from('>I', updated, 0xcc)[0], 0x60)
        self.assertEqual(struct.unpack_from('>I', updated, 0xdc)[0], 0x806a4ce0)
        expected = bytearray(sel)
        expected[0xcc:0xd0] = updated[0xcc:0xd0]
        expected[0xdc:0xe0] = updated[0xdc:0xe0]
        self.assertEqual(updated, expected)
        self.assertEqual(update_sel(updated, elf), (updated, 0))

    def test_weak_export(self):
        sel, elf = fixtures()
        elf = bytearray(elf)
        elf[0x13c] = 0x22
        self.assertEqual(update_sel(sel, elf)[1], 2)

    def test_each_region_uses_its_elf(self):
        sel, elf = fixtures()
        outputs = []
        for delta in (0, 0x1c):
            regional_elf = bytearray(elf)
            struct.pack_into('>I', regional_elf, 0x134, 0x80007100 + delta)
            updated, _ = update_sel(sel, regional_elf)
            self.assertEqual(struct.unpack_from('>I', updated, 0xcc)[0], 0x60 + delta)
            self.assertEqual(update_sel(updated, regional_elf), (updated, 0))
            outputs.append(updated)
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertEqual(sel, fixtures()[0])

    def test_rejects_bad_inputs(self):
        sel, elf = fixtures()
        for bad_sel, bad_elf in [(sel[:60], elf), (sel, elf[:52]), (sel, b'bad')]:
            with self.subTest(lengths=(len(bad_sel), len(bad_elf))):
                with self.assertRaises((ValueError, IndexError)):
                    update_sel(bad_sel, bad_elf)
        for position, value in [(0xc8, 0xffff), (0xd0, 6), (0x34, 12)]:
            corrupted = bytearray(sel)
            struct.pack_into('>I', corrupted, position, value)
            with self.subTest(position=position):
                with self.assertRaises(ValueError):
                    update_sel(corrupted, elf)


if __name__ == '__main__':
    unittest.main()
