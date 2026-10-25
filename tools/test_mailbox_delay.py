"""Exercise delayed CLI sends against simulated Dolphin memory and monotonic time."""

import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_mailbox as mailbox


class Clock:
    def __init__(self, stop_at=None):
        self.now = 100.0
        self.stop_at = stop_at

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if not 0 < seconds <= 0.25:
            raise AssertionError(f"Blocking sleep: {seconds}")
        self.now += seconds
        if self.stop_at is not None and self.now >= self.stop_at:
            raise KeyboardInterrupt


class DolphinMemory:
    address = mailbox.MEM1_START + 0x1040

    def __init__(self, clock, attach_delay=0, ready_delay=0):
        self.clock = clock
        self.attach_at = clock.now + attach_delay
        self.ready_at = self.attach_at + ready_delay
        self.hooked = False
        self.unhooked = False
        self.sent = []
        self.acknowledged = []
        self.stars_added = 0
        self.data = bytearray(0x4000)
        self.data[:6] = b"RMGP01"
        struct.pack_into(
            ">12I", self.data, 0x100,
            0x4150424F, 0x4F543031, mailbox.DISCOVERY_VERSION,
            mailbox.DISCOVERY_SIZE, 0x524D4750, 0x30310000,
            3, mailbox.MEM1_START + 0x1000, 0x1800,
            self.address, mailbox.MAILBOX_SIZE, 0,
        )
        struct.pack_into(
            ">3I", self.data, 0x1040,
            mailbox.MAILBOX_MAGIC, mailbox.MAILBOX_VERSION, 42,
        )

    def hook(self):
        self.hooked = self.clock.now >= self.attach_at

    def is_hooked(self):
        return self.hooked

    def un_hook(self):
        self.hooked = False
        self.unhooked = True

    def read_bytes(self, address, size):
        while self.stars_added < 2 and self.clock.now >= self.attach_at + (1, 5)[self.stars_added]:
            slot = self.stars_added
            self.stars_added += 1
            offset = self.address - mailbox.MEM1_START
            struct.pack_into(">I", self.data, offset + mailbox.WRITE_COUNT_OFFSET, self.stars_added)
            event = offset + mailbox.EVENTS_OFFSET + slot * mailbox.EVENT_SIZE
            self.data[event:event + 13] = b"EggStarGalaxy"
            struct.pack_into(">I", self.data, event + mailbox.GALAXY_NAME_SIZE, 1)
        struct.pack_into(
            ">I", self.data, 0x118, 3 if self.clock.now >= self.ready_at else 2,
        )
        offset = address - mailbox.MEM1_START
        return bytes(self.data[offset:offset + size])

    def read_word(self, address):
        return struct.unpack(">I", self.read_bytes(address, 4))[0]

    def write_bytes(self, address, data):
        offset = address - mailbox.MEM1_START
        self.data[offset:offset + len(data)] = data

    def write_word(self, address, value):
        self.write_bytes(address, struct.pack(">I", value))
        if address == self.address + mailbox.INCOMING_WRITE_COUNT_OFFSET:
            event = (self.address + mailbox.INCOMING_EVENTS_OFFSET
                     + (value - 1) % mailbox.EVENT_CAPACITY * mailbox.INCOMING_ITEM_SIZE)
            item_id, item_index = struct.unpack(">2I", self.read_bytes(event, 8))
            self.sent.append((item_id, item_index, self.clock.now))
        if address == self.address + mailbox.READ_COUNT_OFFSET:
            self.acknowledged.append(self.clock.now)


class DelayedItemTests(unittest.TestCase):
    def run_client(self, *args, observe=0, attach_delay=0, ready_delay=0, stop_at=None):
        clock = Clock(stop_at)
        dme = DolphinMemory(clock, attach_delay, ready_delay)
        output = io.StringIO()
        with (
            patch.dict(sys.modules, {"dolphin_memory_engine": dme}),
            patch.object(mailbox, "time", clock),
            patch.object(sys, "argv", ["test_mailbox.py", "--observe-seconds", str(observe), *args]),
            contextlib.redirect_stdout(output),
        ):
            mailbox.main()
        self.assertTrue(dme.unhooked)
        return clock, dme, output.getvalue()

    def test_multiple_ids_are_sent_once_in_order_while_watching(self):
        _, dme, output = self.run_client(
            "--send-test-item-ids", "200,201,202,203,204,205",
            "--watch", "--ack-events", stop_at=106,
        )
        self.assertEqual(
            [item[:2] for item in dme.sent],
            [(item_id, index) for index, item_id in enumerate(range(200, 206))],
        )
        self.assertEqual([item[2] for item in dme.sent], [100] * 6)
        self.assertEqual(output.count("Sent test item:"), 6)
        self.assertEqual(len(dme.acknowledged), 2)

    def test_multiple_ids_support_delay_whitespace_and_duplicates(self):
        clock, dme, _ = self.run_client(
            "--send-test-item-ids", " 205, 200,205 ",
            "--send-test-item-delay", "2", observe=3,
        )
        self.assertEqual([item[:2] for item in dme.sent], [(205, 0), (200, 1), (205, 2)])
        self.assertTrue(all(102 <= item[2] < 102.11 for item in dme.sent))
        self.assertGreaterEqual(clock.now, 103)

    def test_rejects_invalid_item_lists_and_conflicting_options(self):
        invalid_args = [
            ["--send-test-item-ids", value]
            for value in ["", "200,", ",201", "200,,201", "200,no", "-1", "4294967296"]
        ]
        invalid_args.append([
            "--send-test-item-id", "200", "--send-test-item-ids", "201,202",
        ])
        for args in invalid_args:
            with (
                self.subTest(args=args),
                patch.object(sys, "argv", ["test_mailbox.py", *args]),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                mailbox.main()
            self.assertEqual(raised.exception.code, 2)

    def test_custom_item_sends_require_write_access(self):
        for option, value in [("--send-test-item-id", "200"), ("--send-test-item-ids", "200,201")]:
            with (
                self.subTest(option=option),
                patch.object(sys, "argv", ["test_mailbox.py", "--read-only", option, value]),
                contextlib.redirect_stderr(io.StringIO()) as errors,
                self.assertRaises(SystemExit) as raised,
            ):
                mailbox.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("requires write access", errors.getvalue())

    def test_watch_continues_acknowledging_before_send_and_after_send(self):
        clock, dme, output = self.run_client(
            "--send-test-item-id", "201", "--send-test-item-delay", "3",
            "--watch", "--ack-events", attach_delay=2, stop_at=108,
        )
        self.assertEqual([item[:2] for item in dme.sent], [(201, 0)])
        self.assertGreaterEqual(dme.sent[0][2], dme.attach_at + 3)
        self.assertLess(dme.sent[0][2], dme.attach_at + 3.11)
        self.assertLess(dme.acknowledged[0], dme.sent[0][2])
        self.assertEqual(len(dme.acknowledged), 2)
        self.assertGreater(dme.acknowledged[1], dme.sent[0][2])
        self.assertGreaterEqual(clock.now, 108)
        self.assertIn("delay=3s after Dolphin attachment", output)
        self.assertEqual(output.count("Sent test item: itemId=201"), 1)

    def test_without_watch_waits_and_other_test_sends_stay_immediate(self):
        clock, dme, _ = self.run_client(
            "--send-test-item-id", "201", "--send-test-item-delay", "2.5",
            "--send-test-stars", "2", "--send-test-item", "--ack-events",
        )
        self.assertEqual([item[0] for item in dme.sent], [2, 2, 2, 201])
        self.assertEqual([item[2] for item in dme.sent[:3]], [100, 100, 100])
        self.assertGreaterEqual(dme.sent[-1][2], 102.5)
        self.assertLess(clock.now, 102.61)
        self.assertEqual(len(dme.acknowledged), 1)
        self.assertLess(dme.acknowledged[0], dme.sent[-1][2])

    def test_sends_during_initial_observation(self):
        clock, dme, _ = self.run_client(
            "--send-test-item-id", "201", "--send-test-item-delay", "1", observe=3,
        )
        self.assertGreaterEqual(dme.sent[0][2], 101)
        self.assertLess(dme.sent[0][2], 101.11)
        self.assertGreaterEqual(clock.now, 103)

    def test_mailbox_discovery_does_not_restart_delay(self):
        _, dme, _ = self.run_client(
            "--send-test-item-id", "201", "--send-test-item-delay", "3",
            attach_delay=2, ready_delay=2,
        )
        self.assertGreaterEqual(dme.sent[0][2], 105)
        self.assertLess(dme.sent[0][2], 105.11)

    def test_zero_delay_keeps_send_after_observation_and_before_other_items(self):
        for delay_args in [(), ("--send-test-item-delay", "0")]:
            with self.subTest(delay_args=delay_args):
                _, dme, output = self.run_client(
                    "--send-test-item-id", "201", "--send-test-item",
                    *delay_args, observe=2,
                )
                self.assertEqual([item[0] for item in dme.sent], [201, 2])
                self.assertGreaterEqual(dme.sent[0][2], 102)
                self.assertNotIn("Scheduled test item", output)

    def test_delay_without_custom_id_does_not_delay_default_item(self):
        clock, dme, output = self.run_client("--send-test-item-delay", "30", "--send-test-item")
        self.assertEqual(dme.sent, [(2, 0, 100)])
        self.assertEqual(clock.now, 100)
        self.assertNotIn("Scheduled test item", output)

    def test_interrupt_cancels_pending_send_and_detaches(self):
        _, dme, output = self.run_client(
            "--send-test-item-id", "201", "--send-test-item-delay", "30",
            "--watch", "--ack-events", stop_at=102,
        )
        self.assertEqual(dme.sent, [])
        self.assertEqual(len(dme.acknowledged), 1)
        self.assertNotIn("Sent test item", output)

    def test_rejects_negative_and_nonfinite_delays(self):
        for value in ["-1", "nan", "inf", "-inf"]:
            with (
                self.subTest(value=value),
                patch.object(sys, "argv", ["test_mailbox.py", f"--send-test-item-delay={value}"]),
                contextlib.redirect_stderr(io.StringIO()) as errors,
                self.assertRaises(SystemExit) as raised,
            ):
                mailbox.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("must be finite and nonnegative", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
