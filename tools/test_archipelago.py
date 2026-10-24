#!/usr/bin/env python3
"""Trust-boundary checks for module validation, DOL patching and mailbox discovery."""

import contextlib
import io
import importlib.util
import json
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
from archipelago_protocol import MAILBOX_SIZE, MAILBOX_VERSION

spec = importlib.util.spec_from_file_location("ap_mailbox", ROOT / "test_mailbox.py")
mailbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mailbox)


class ProtocolTests(unittest.TestCase):
    def descriptor(self, region="RMGP01", status=3, error=0):
        data = bytearray(0x3000)
        struct.pack_into(
            ">12I",
            data,
            0x100,
            0x4150424F,
            0x4F543031,
            1,
            48,
            int.from_bytes(region[:4].encode(), "big"),
            0x30310000,
            status,
            0x80001000,
            0x900,
            0x80001040,
            MAILBOX_SIZE,
            error,
        )
        struct.pack_into(">3I", data, 0x1040, 0x4150534D, MAILBOX_VERSION, 42)
        return data

    def test_discovery_both_regions(self):
        for region in ["RMGP01", "RMGE01"]:
            self.assertEqual(
                mailbox.discover(self.descriptor(region), region)["testValue"], 42
            )

    def test_region_mismatch(self):
        self.assertIsNone(mailbox.discover(self.descriptor(), "RMGE01"))

    def test_loading_not_published(self):
        self.assertIsNone(mailbox.discover(self.descriptor(status=2), "RMGP01"))

    def test_error_visible(self):
        with self.assertRaisesRegex(RuntimeError, "error 8"):
            mailbox.discover(self.descriptor(status=4, error=8), "RMGP01")

    def test_mailbox_outside_module(self):
        data = self.descriptor()
        struct.pack_into(">I", data, 0x124, 0x80002000)
        self.assertIsNone(mailbox.discover(data, "RMGP01"))

    def test_ambiguous_discovery(self):
        data = self.descriptor()
        data[0x200:0x230] = data[0x100:0x130]
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            mailbox.discover(data, "RMGP01")

    def test_signature_and_version(self):
        data = self.descriptor()
        struct.pack_into(">I", data, 0x1044, MAILBOX_VERSION + 1)
        self.assertIsNone(mailbox.discover(data, "RMGP01"))


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux shared-memory reader")
class SharedMemoryTests(unittest.TestCase):
    def test_reject_non_dolphin_process(self):
        with self.assertRaisesRegex(RuntimeError, "not dolphin-emu"):
            mailbox.SharedMemoryReader(os.getpid()).hook()

    def test_read_only_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            backing = Path(directory) / "memory"
            backing.write_bytes(b"RMGP01")
            reader = mailbox.SharedMemoryReader(os.getpid())
            reader.fd = os.open(backing, os.O_RDONLY)
            try:
                self.assertEqual(reader.read_bytes(0x80000000, 6), b"RMGP01")
                for address, size in [
                    (0x7FFFFFFF, 1),
                    (0x81800000, 1),
                    (0x80000000, -1),
                ]:
                    with self.assertRaisesRegex(RuntimeError, "outside MEM1"):
                        reader.read_bytes(address, size)
                with self.assertRaisesRegex(RuntimeError, "Short"):
                    reader.read_bytes(0x80000000, 7)
                with self.assertRaises(OSError):
                    os.write(reader.fd, b"X")
            finally:
                reader.un_hook()
            with self.assertRaisesRegex(RuntimeError, "reader closed"):
                reader.read_bytes(0x80000000, 1)
            self.assertEqual(backing.read_bytes(), b"RMGP01")


@unittest.skipUnless(shutil.which("cp"), "Private copy deployment uses cp")
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "retail/DATA"
        (self.source / "sys").mkdir(parents=True)
        (self.source / "files").mkdir()
        (self.source / "sys/boot.bin").write_bytes(b"RMGP01\0\0")
        (self.source / "sys/main.dol").write_bytes(b"retail DOL")
        sel = self.root / "orig/RMGP01/files/ModuleData/product.sel"
        sel.parent.mkdir(parents=True)
        sel.write_bytes(b"retail exports")
        self.out = self.root / "build/kamek/RMGP01"
        self.out.mkdir(parents=True)
        self.dol = self.out / "main.dol"
        self.module = self.out / "RMGP01.bin"
        self.dol.write_bytes(b"patched DOL 1")
        self.module.write_bytes(b"module 1")
        root_patch = patch.object(builder, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def deploy(self, **options):
        return builder.deploy_game(self.out, "RMGP01", self.dol, self.module, **options)

    def test_create_then_update_owned_copy(self):
        self.assertIsNone(self.deploy())
        output = self.deploy(game_dir=self.source)
        self.dol.write_bytes(b"patched DOL 2")
        self.module.write_bytes(b"module 2")
        self.assertEqual(self.deploy(), output)
        self.assertEqual((output / "sys/main.dol").read_bytes(), b"patched DOL 2")
        self.assertEqual(
            (output / "files/Archipelago/RMGP01.bin").read_bytes(), b"module 2"
        )
        self.assertEqual((self.source / "sys/main.dol").read_bytes(), b"retail DOL")

    def test_refuse_modified_test_dol(self):
        output = self.deploy(game_dir=self.source)
        (output / "sys/main.dol").write_bytes(b"manual edit")
        with self.assertRaisesRegex(ValueError, "changed outside this build"):
            self.deploy()
        self.assertEqual((output / "sys/main.dol").read_bytes(), b"manual edit")

    def test_refuse_original_as_output(self):
        with self.assertRaisesRegex(ValueError, "outside the source"):
            self.deploy(game_dir=self.source, game_output=self.source)
        self.assertEqual((self.source / "sys/main.dol").read_bytes(), b"retail DOL")

    def test_refuse_wrong_region_and_implicit_custom_output(self):
        (self.source / "sys/boot.bin").write_bytes(b"RMGE01\0\0")
        with self.assertRaisesRegex(ValueError, "region mismatch"):
            self.deploy(game_dir=self.source)
        with self.assertRaisesRegex(ValueError, "requires --game-dir"):
            self.deploy(game_output=self.out / "arbitrary")


class BinaryTests(unittest.TestCase):
    def test_branch_range(self):
        self.assertEqual(branch(0x80000000, 0x80000004), 0x48000005)
        with self.assertRaises(ValueError):
            branch(0x80000000, 0x82000000)
        with self.assertRaises(ValueError):
            branch(0x80000000, 0x80000002)

    def test_fnv_known_value(self):
        self.assertEqual(fnv(b"hello"), 0x4F9F2CAB)

    def test_dol_bounds(self):
        with self.assertRaises(ValueError):
            dol_sections(bytes(2))
        data = bytearray(0x100)
        struct.pack_into(">I", data, 0, 0x100)
        struct.pack_into(">I", data, 0x48, 0x80004000)
        struct.pack_into(">I", data, 0x90, 4)
        with self.assertRaises(ValueError):
            dol_sections(data)

    def module(self, commands=b""):
        return (
            struct.pack(">8I", 0x4B616D65, 0x6B000002, 4, 4, 0, 4, 0, 0)
            + bytes(4)
            + commands
        )

    def test_internal_relocation(self):
        self.assertEqual(
            validate_module(self.module(struct.pack(">2I", 0x01000000, 4))),
            (4, 4, 0, 4),
        )

    def test_reject_external_patch(self):
        with self.assertRaisesRegex(ValueError, "patch"):
            validate_module(self.module(struct.pack(">3I", 0x20FFFFFE, 0x80004000, 0)))

    def test_truncated_module(self):
        with self.assertRaises(ValueError):
            validate_module(self.module(b"\1"))

    def test_relocation_outside_block(self):
        with self.assertRaises(ValueError):
            validate_module(self.module(struct.pack(">2I", 0x01000008, 4)))

    def test_ctor_bounds(self):
        data = bytearray(self.module())
        struct.pack_into(">I", data, 20, 8)
        with self.assertRaises(ValueError):
            validate_module(data)


class CallHookTests(unittest.TestCase):
    def setUp(self):
        self.dol = bytearray(0x180)
        struct.pack_into(">I", self.dol, 0, 0x100)
        struct.pack_into(">I", self.dol, 0x48, 0x80004000)
        struct.pack_into(">I", self.dol, 0x90, 0x80)
        self.syms = {"owner": (0x80004000, 0x20), "callee": (0x80004060, 4)}
        self.word = branch(0x80004004, 0x80004060)
        struct.pack_into(">I", self.dol, 0x104, self.word)
        self.spec = {
            "version": 1,
            "hooks": {"Example": {"owner": "owner", "callee": "callee"}},
        }
        self.hooks = builder.resolve_call_hooks(self.spec, self.dol, self.syms)

    def module(self, commands):
        return (
            struct.pack(">8I", 0x4B616D65, 0x6B000002, 4, 4, 0, 0, 0, 0)
            + bytes(4)
            + commands
        )

    def command(self, kind=65, address=0x80004004, target=0):
        return struct.pack(">3I", (kind << 24) | 0xFFFFFE, address, target)

    def test_resolve_unique_call(self):
        self.assertEqual(self.hooks[0]["address"], 0x80004004)
        self.assertEqual(self.hooks[0]["expected"], self.word)

    def test_missing_and_ambiguous_calls(self):
        struct.pack_into(">I", self.dol, 0x104, 0x60000000)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            builder.resolve_call_hooks(self.spec, self.dol, self.syms)
        for offset in [4, 8]:
            struct.pack_into(
                ">I", self.dol, 0x100 + offset, branch(0x80004000 + offset, 0x80004060)
            )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            builder.resolve_call_hooks(self.spec, self.dol, self.syms)

    def test_unknown_symbol(self):
        with self.assertRaisesRegex(ValueError, "Unknown hook symbol"):
            builder.resolve_call_hooks(self.spec, self.dol, {})

    def test_overlapping_and_reserved_sites(self):
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            builder.resolve_call_hooks(self.spec, self.dol, self.syms, [0x80004004])
        self.spec["hooks"]["Alias"] = self.spec["hooks"]["Example"]
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            builder.resolve_call_hooks(self.spec, self.dol, self.syms)

    def test_bad_configuration(self):
        for document in [
            [],
            {},
            {"version": True, "hooks": {}},
            {"version": 2, "hooks": {}},
            {"version": 1, "hooks": {"Invalid-Name": {}}},
            {"version": 1, "hooks": {"Name": {"owner": "owner"}}},
            {"version": 1, "hooks": {"Name": {"owner": "owner", "callee": 3}}},
        ]:
            with self.subTest(document=document), self.assertRaises(ValueError):
                builder.resolve_call_hooks(document, self.dol, self.syms)

    def test_duplicate_json_names(self):
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            json.loads(
                '{"Name": {}, "Name": {}}', object_pairs_hook=builder.unique_json_object
            )

    def test_declared_call_is_accepted(self):
        sizes, installed = builder.inspect_module(
            self.module(self.command()), self.hooks
        )
        self.assertEqual(sizes, (4, 4, 0, 0))
        self.assertEqual(installed, [dict(**self.hooks[0], target_offset=0)])

    def test_unused_declarations_are_not_installed(self):
        _, installed = builder.inspect_module(self.module(b""), self.hooks)
        self.assertEqual(installed, [])

    def test_multiple_distinct_call_sites(self):
        second = dict(**self.hooks[0])
        second.update(name="Second", address=0x80004008)
        commands = self.command() + self.command(address=second["address"])
        _, installed = builder.inspect_module(
            self.module(commands), [*self.hooks, second]
        )
        self.assertEqual([hook["name"] for hook in installed], ["Example", "Second"])

    def test_unknown_call_site(self):
        with self.assertRaisesRegex(ValueError, "Undeclared"):
            validate_module(self.module(self.command(address=0x80004008)), self.hooks)

    def test_other_patch_types_are_rejected(self):
        for kind in [1, 10, 32, 35, 64]:
            with self.subTest(kind=kind), self.assertRaisesRegex(
                ValueError, "unsupported"
            ):
                validate_module(self.module(self.command(kind=kind)), self.hooks)

    def test_duplicate_call_patch(self):
        with self.assertRaisesRegex(ValueError, "Duplicate module patch"):
            validate_module(self.module(self.command() * 2), self.hooks)

    def test_call_target_must_be_module_code(self):
        for target in [1, 4, 8, 0x80004060]:
            with self.subTest(target=target), self.assertRaisesRegex(
                ValueError, "aligned code"
            ):
                validate_module(self.module(self.command(target=target)), self.hooks)

    def test_call_target_requires_a_complete_instruction(self):
        binary = (
            struct.pack(">8I", 0x4B616D65, 0x6B000002, 4, 3, 0, 0, 0, 0)
            + bytes(3)
            + self.command()
        )
        with self.assertRaisesRegex(ValueError, "aligned code"):
            validate_module(binary, self.hooks)

    def test_truncated_absolute_command(self):
        with self.assertRaisesRegex(ValueError, "Truncated"):
            validate_module(self.module(self.command()[:-4]), self.hooks)


class HookInspectionTests(unittest.TestCase):
    def setUp(self):
        self.data = ProtocolTests().descriptor()
        self.discovery = mailbox.discover(self.data, "RMGP01")
        self.manifest = dict(
            region="RMGP01",
            discovery=0x80000100,
            module_size=0x900,
            code_size=0x80,
            module_hooks=[dict(name="Example", address=0x80000400, target_offset=0x20)],
        )
        struct.pack_into(">I", self.data, 0x400, branch(0x80000400, 0x80001020))

    def read(self, address, size):
        return self.data[address - 0x80000000 : address - 0x80000000 + size]

    def test_installed_call(self):
        hooks = mailbox.verify_call_hooks(self.read, self.discovery, self.manifest)
        self.assertEqual(hooks[0]["target"], 0x80001020)

    def test_wrong_instruction(self):
        struct.pack_into(">I", self.data, 0x400, 0x60000000)
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            mailbox.verify_call_hooks(self.read, self.discovery, self.manifest)

    def test_wrong_manifest(self):
        self.manifest["region"] = "RMGE01"
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            mailbox.verify_call_hooks(self.read, self.discovery, self.manifest)

    def test_bad_hook_bounds(self):
        for address, offset in [
            (0x7FFFFFFC, 0),
            (0x81800000, 0),
            (0x80000401, 0),
            (0x80000400, -4),
            (0x80000400, 0x80),
            (0x80000400, 1),
        ]:
            self.manifest["module_hooks"] = [
                dict(name="Example", address=address, target_offset=offset)
            ]
            with self.subTest(address=address, offset=offset), self.assertRaisesRegex(
                RuntimeError, "bounds"
            ):
                mailbox.verify_call_hooks(self.read, self.discovery, self.manifest)


class StarQueueTests(unittest.TestCase):
    def setUp(self):
        self.data = ProtocolTests().descriptor()
        self.data[:6] = b"RMGP01"
        self.address = 0x80001040
        self.hooked = True

    def read_bytes(self, address, size):
        start = address - 0x80000000
        return bytes(self.data[start:start + size])

    def read_word(self, address):
        return struct.unpack('>I', self.read_bytes(address, 4))[0]

    def write_word(self, address, value):
        struct.pack_into('>I', self.data, address - 0x80000000, value)

    def is_hooked(self):
        return self.hooked

    def un_hook(self):
        self.hooked = False

    def queue(self, read_count, names):
        write_count = (read_count + len(names)) & 0xffffffff
        self.write_word(self.address + mailbox.WRITE_COUNT_OFFSET, write_count)
        self.write_word(self.address + mailbox.READ_COUNT_OFFSET, read_count)
        for index, name in enumerate(names):
            slot = ((read_count + index) & 0xffffffff) % mailbox.EVENT_CAPACITY
            offset = self.address + mailbox.EVENTS_OFFSET + slot * mailbox.EVENT_SIZE
            raw = name.encode().ljust(mailbox.GALAXY_NAME_SIZE, b'\0') + struct.pack('>I', index + 1)
            start = offset - 0x80000000
            self.data[start:start + len(raw)] = raw

    def test_read_fifo_without_consuming(self):
        self.queue(31, ['FirstGalaxy', 'SecondGalaxy'])
        queue = mailbox.read_star_events(self.read_bytes, self.address)
        self.assertEqual([event['galaxyName'] for event in queue['events']], ['FirstGalaxy', 'SecondGalaxy'])
        self.assertEqual([event['starId'] for event in queue['events']], [1, 2])
        self.assertEqual(self.read_word(self.address + mailbox.READ_COUNT_OFFSET), 31)

    def test_counter_wrap_and_acknowledgement(self):
        self.queue(0xffffffff, ['FirstGalaxy', 'SecondGalaxy'])
        queue = mailbox.read_star_events(self.read_bytes, self.address)
        self.assertEqual([event['sequence'] for event in queue['events']], [0xffffffff, 0])
        self.assertEqual(mailbox.ack_star_events(self, self.address, queue), 2)
        self.assertEqual(self.read_word(self.address + mailbox.READ_COUNT_OFFSET), 1)
        self.assertEqual(mailbox.read_star_events(self.read_bytes, self.address)['pending'], 0)

    def test_full_and_invalid_queue(self):
        self.queue(0, ['Galaxy'] * mailbox.EVENT_CAPACITY)
        self.assertEqual(mailbox.read_star_events(self.read_bytes, self.address)['pending'], mailbox.EVENT_CAPACITY)
        self.write_word(self.address + mailbox.WRITE_COUNT_OFFSET, mailbox.EVENT_CAPACITY + 1)
        with self.assertRaisesRegex(RuntimeError, 'Invalid event queue'):
            mailbox.read_star_events(self.read_bytes, self.address)

    def test_stale_acknowledgement(self):
        self.queue(0, ['Galaxy'])
        queue = mailbox.read_star_events(self.read_bytes, self.address)
        mailbox.ack_star_events(self, self.address, queue)
        with self.assertRaisesRegex(RuntimeError, 'readCount changed'):
            mailbox.ack_star_events(self, self.address, queue)

    def test_concurrent_producer_detected(self):
        self.queue(0, ['Galaxy'])
        reads = []
        def concurrent_read(address, size):
            result = self.read_bytes(address, size)
            if address == self.address + mailbox.EVENTS_OFFSET:
                self.write_word(self.address + mailbox.WRITE_COUNT_OFFSET, 2)
            reads.append(address)
            return result
        with self.assertRaisesRegex(RuntimeError, 'changed during read'):
            mailbox.read_star_events(concurrent_read, self.address)

    def test_watch_exit_does_not_acknowledge_old_snapshot(self):
        self.queue(0, ['Galaxy'])
        def watching(dme, address, acknowledge):
            self.assertTrue(acknowledge)
            mailbox.ack_star_events(dme, address, mailbox.read_star_events(dme.read_bytes, address))
        argv = ['test_mailbox.py', '--region', 'RMGP01', '--observe-seconds', '0', '--watch', '--ack-events']
        with patch.dict(sys.modules, {'dolphin_memory_engine': self}), patch.object(sys, 'argv', argv), \
                patch.object(mailbox, 'watch_star_events', side_effect=watching), contextlib.redirect_stdout(io.StringIO()):
            mailbox.main()
        self.assertEqual(self.read_word(self.address + mailbox.READ_COUNT_OFFSET), 1)


if __name__ == "__main__":
    unittest.main()
