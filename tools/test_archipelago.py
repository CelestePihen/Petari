#!/usr/bin/env python3
"""Trust-boundary checks for module validation, DOL patching and mailbox discovery."""
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_archipelago import align, branch, dol_sections, fnv, validate_module
from tools import build_archipelago as builder
spec = importlib.util.spec_from_file_location('ap_mailbox', ROOT / 'test_mailbox.py')
mailbox = importlib.util.module_from_spec(spec); spec.loader.exec_module(mailbox)


class ProtocolTests(unittest.TestCase):
    def descriptor(self, region='RMGP01', status=3, error=0):
        data = bytearray(0x3000)
        struct.pack_into('>12I', data, 0x100, 0x4150424F, 0x4F543031, 1, 48,
                         int.from_bytes(region[:4].encode(), 'big'), 0x30310000,
                         status, 0x80001000, 0x100, 0x80001040, 12, error)
        struct.pack_into('>3I', data, 0x1040, 0x4150534D, 1, 42)
        return data

    def test_discovery_both_regions(self):
        for region in ['RMGP01', 'RMGE01']:
            self.assertEqual(mailbox.discover(self.descriptor(region), region)['testValue'], 42)

    def test_region_mismatch(self):
        self.assertIsNone(mailbox.discover(self.descriptor(), 'RMGE01'))

    def test_loading_not_published(self):
        self.assertIsNone(mailbox.discover(self.descriptor(status=2), 'RMGP01'))

    def test_error_visible(self):
        with self.assertRaisesRegex(RuntimeError, 'error 8'):
            mailbox.discover(self.descriptor(status=4, error=8), 'RMGP01')

    def test_mailbox_outside_module(self):
        data = self.descriptor(); struct.pack_into('>I', data, 0x124, 0x80002000)
        self.assertIsNone(mailbox.discover(data, 'RMGP01'))

    def test_ambiguous_discovery(self):
        data = self.descriptor(); data[0x200:0x230] = data[0x100:0x130]
        with self.assertRaisesRegex(RuntimeError, 'Multiple'):
            mailbox.discover(data, 'RMGP01')

    def test_signature_and_version(self):
        data = self.descriptor(); struct.pack_into('>I', data, 0x1044, 2)
        self.assertIsNone(mailbox.discover(data, 'RMGP01'))


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux shared-memory reader')
class SharedMemoryTests(unittest.TestCase):
    def test_reject_non_dolphin_process(self):
        with self.assertRaisesRegex(RuntimeError, 'not dolphin-emu'):
            mailbox.SharedMemoryReader(os.getpid()).hook()

    def test_read_only_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            backing = Path(directory) / 'memory'
            backing.write_bytes(b'RMGP01')
            reader = mailbox.SharedMemoryReader(os.getpid())
            reader.fd = os.open(backing, os.O_RDONLY)
            try:
                self.assertEqual(reader.read_bytes(0x80000000, 6), b'RMGP01')
                for address, size in [(0x7fffffff, 1), (0x81800000, 1), (0x80000000, -1)]:
                    with self.assertRaisesRegex(RuntimeError, 'outside MEM1'):
                        reader.read_bytes(address, size)
                with self.assertRaisesRegex(RuntimeError, 'Short'):
                    reader.read_bytes(0x80000000, 7)
                with self.assertRaises(OSError):
                    os.write(reader.fd, b'X')
            finally:
                reader.un_hook()
            with self.assertRaisesRegex(RuntimeError, 'reader closed'):
                reader.read_bytes(0x80000000, 1)
            self.assertEqual(backing.read_bytes(), b'RMGP01')


@unittest.skipUnless(shutil.which('cp'), 'Private copy deployment uses cp')
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'retail/DATA'
        (self.source / 'sys').mkdir(parents=True)
        (self.source / 'files').mkdir()
        (self.source / 'sys/boot.bin').write_bytes(b'RMGP01\0\0')
        (self.source / 'sys/main.dol').write_bytes(b'retail DOL')
        sel = self.root / 'orig/RMGP01/files/ModuleData/product.sel'
        sel.parent.mkdir(parents=True)
        sel.write_bytes(b'retail exports')
        self.out = self.root / 'build/kamek/RMGP01'
        self.out.mkdir(parents=True)
        self.dol = self.out / 'main.dol'
        self.module = self.out / 'RMGP01.bin'
        self.dol.write_bytes(b'patched DOL 1')
        self.module.write_bytes(b'module 1')
        root_patch = patch.object(builder, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def deploy(self, **options):
        return builder.deploy_game(self.out, 'RMGP01', self.dol, self.module, **options)

    def test_create_then_update_owned_copy(self):
        self.assertIsNone(self.deploy())
        output = self.deploy(game_dir=self.source)
        self.dol.write_bytes(b'patched DOL 2')
        self.module.write_bytes(b'module 2')
        self.assertEqual(self.deploy(), output)
        self.assertEqual((output / 'sys/main.dol').read_bytes(), b'patched DOL 2')
        self.assertEqual((output / 'files/Archipelago/RMGP01.bin').read_bytes(), b'module 2')
        self.assertEqual((self.source / 'sys/main.dol').read_bytes(), b'retail DOL')

    def test_refuse_modified_test_dol(self):
        output = self.deploy(game_dir=self.source)
        (output / 'sys/main.dol').write_bytes(b'manual edit')
        with self.assertRaisesRegex(ValueError, 'changed outside this build'):
            self.deploy()
        self.assertEqual((output / 'sys/main.dol').read_bytes(), b'manual edit')

    def test_refuse_original_as_output(self):
        with self.assertRaisesRegex(ValueError, 'outside the source'):
            self.deploy(game_dir=self.source, game_output=self.source)
        self.assertEqual((self.source / 'sys/main.dol').read_bytes(), b'retail DOL')

    def test_refuse_wrong_region_and_implicit_custom_output(self):
        (self.source / 'sys/boot.bin').write_bytes(b'RMGE01\0\0')
        with self.assertRaisesRegex(ValueError, 'region mismatch'):
            self.deploy(game_dir=self.source)
        with self.assertRaisesRegex(ValueError, 'requires --game-dir'):
            self.deploy(game_output=self.out / 'arbitrary')


class BinaryTests(unittest.TestCase):
    def test_branch_range(self):
        self.assertEqual(branch(0x80000000, 0x80000004), 0x48000005)
        with self.assertRaises(ValueError): branch(0x80000000, 0x82000000)
        with self.assertRaises(ValueError): branch(0x80000000, 0x80000002)

    def test_fnv_known_value(self):
        self.assertEqual(fnv(b'hello'), 0x4F9F2CAB)

    def test_dol_bounds(self):
        with self.assertRaises(ValueError): dol_sections(bytes(2))
        data = bytearray(0x100)
        struct.pack_into('>I', data, 0, 0x100)
        struct.pack_into('>I', data, 0x48, 0x80004000)
        struct.pack_into('>I', data, 0x90, 4)
        with self.assertRaises(ValueError): dol_sections(data)

    def module(self, commands=b''):
        return struct.pack('>8I', 0x4B616D65, 0x6B000002, 4, 4, 0, 4, 0, 0) + bytes(4) + commands

    def test_internal_relocation(self):
        self.assertEqual(validate_module(self.module(struct.pack('>2I', 0x01000000, 4))), (4, 4, 0, 4))

    def test_reject_external_patch(self):
        with self.assertRaisesRegex(ValueError, 'patch'):
            validate_module(self.module(struct.pack('>3I', 0x20FFFFFE, 0x80004000, 0)))

    def test_truncated_module(self):
        with self.assertRaises(ValueError): validate_module(self.module(b'\1'))

    def test_relocation_outside_block(self):
        with self.assertRaises(ValueError): validate_module(self.module(struct.pack('>2I', 0x01000008, 4)))

    def test_ctor_bounds(self):
        data = bytearray(self.module()); struct.pack_into('>I', data, 20, 8)
        with self.assertRaises(ValueError): validate_module(data)


if __name__ == '__main__':
    unittest.main()
