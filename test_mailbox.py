#!/usr/bin/env python3
"""Discover AP mailbox v1 in MEM1 and verify a Dolphin Memory Engine round trip."""
import argparse
import configparser
import json
from pathlib import Path
import os
import struct
import stat
import shutil
import sys
import subprocess
import time

MEM1_START, MEM1_END = 0x80000000, 0x81800000
SIGNATURE = b'APBOOT01'


def discover(data, region):
    found = []
    offset = 0
    while True:
        offset = data.find(SIGNATURE, offset)
        if offset < 0:
            break
        if offset % 4 == 0 and offset + 48 <= len(data):
            words = struct.unpack_from('>12I', data, offset)
            _, _, version, size, r0, r1, status, module, length, mailbox, mailbox_size, error = words
            actual_region = (r0.to_bytes(4, 'big') + r1.to_bytes(4, 'big')[:2]).decode('ascii', errors='replace')
            if version == 1 and size == 48 and actual_region == region and r1 == 0x30310000:
                if status == 4:
                    raise RuntimeError(f'Bootstrap error {error} at 0x{MEM1_START + offset:08X}')
                if (status == 3 and module % 32 == 0 and MEM1_START <= module < MEM1_END and
                        0 < length <= MEM1_END - module and mailbox_size == 12 and
                        module <= mailbox <= module + length - 12):
                    mailbox_offset = mailbox - MEM1_START
                    if mailbox_offset + 12 > len(data):
                        offset += len(SIGNATURE)
                        continue
                    magic, protocol, value = struct.unpack_from('>3I', data, mailbox_offset)
                    if (magic, protocol) == (0x4150534D, 1):
                        found.append(dict(discovery=MEM1_START + offset, address=mailbox,
                                          region=region, version=protocol, testValue=value,
                                          module=module, moduleSize=length))
        offset += len(SIGNATURE)
    if len(found) > 1:
        raise RuntimeError('Multiple valid AP descriptors; refusing an ambiguous write')
    return found[0] if found else None



class SharedMemoryReader:
    """Read only Dolphin's own MEM1 backing file, never process memory via ptrace."""

    def __init__(self, pid):
        self.pid = pid
        self.fd = None

    def hook(self):
        proc = Path('/proc') / str(self.pid)
        if (proc / 'exe').resolve(strict=True).name != 'dolphin-emu':
            raise RuntimeError(f'PID {self.pid} is not dolphin-emu')
        expected = f'/dev/shm/dolphin-emu.{self.pid}'
        candidates = []
        for entry in (proc / 'fd').iterdir():
            try:
                target = os.readlink(entry)
            except FileNotFoundError:
                continue
            if target in (expected, expected + ' (deleted)'):
                candidates.append(entry)
        if not candidates:
            raise RuntimeError(f'No Dolphin shared memory for PID {self.pid}; start emulation first')
        opened = None
        try:
            identities = set()
            for entry in candidates:
                fd = os.open(entry, os.O_RDONLY | os.O_CLOEXEC)
                info = os.fstat(fd)
                if opened is None:
                    opened = fd
                else:
                    os.close(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                        info.st_size < MEM1_END - MEM1_START):
                    raise RuntimeError('Invalid Dolphin shared-memory owner, size or type')
                identities.add((info.st_dev, info.st_ino))
            if len(identities) != 1:
                raise RuntimeError('Ambiguous Dolphin shared-memory files')
            device, inode = identities.pop()
            device_text = f'{os.major(device):02x}:{os.minor(device):02x}'
            valid_mapping = False
            for line in (proc / 'maps').read_text().splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) < 5:
                    continue
                start, end = (int(value, 16) for value in fields[0].split('-'))
                if (fields[1] == 'rw-s' and int(fields[2], 16) == 0 and
                        fields[3] == device_text and int(fields[4]) == inode and
                        end - start >= MEM1_END - MEM1_START):
                    valid_mapping = True
            if not valid_mapping:
                raise RuntimeError('No matching MEM1 mapping in this Dolphin process')
            self.fd = opened
            opened = None
        finally:
            if opened is not None:
                os.close(opened)

    def is_hooked(self):
        return self.fd is not None

    def get_status(self):
        return f'read-only shared memory, PID {self.pid}'

    def un_hook(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read_bytes(self, address, size):
        if (self.fd is None or size < 0 or not MEM1_START <= address <= MEM1_END or
                size > MEM1_END - address):
            raise RuntimeError('Shared-memory read outside MEM1 or reader closed')
        data = os.pread(self.fd, size, address - MEM1_START)
        if len(data) != size:
            raise RuntimeError('Short Dolphin shared-memory read')
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', choices=['RMGP01', 'RMGE01'])
    parser.add_argument('--value', type=lambda x: int(x, 0), default=0x12345678)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--observe-seconds', type=float, default=5, help='Check mailbox stability while the game continues booting')
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--shared-memory-pid', type=int, help='Linux read-only access to an already running Dolphin PID')
    parser.add_argument('--launch', type=Path, help='Launch a private test game as this Python process child')
    parser.add_argument('--dolphin-user', type=Path)
    parser.add_argument('--null-video', action='store_true', help='Use Dolphin Null video backend; no gameplay rendering test')
    parser.add_argument('--keep-running', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.observe_seconds < 0 or args.timeout <= 0:
        parser.error('Timeout must be positive and observation duration nonnegative')
    if not 0 <= args.value <= 0xFFFFFFFF:
        parser.error('--value must be a u32')
    if args.launch and not args.dolphin_user:
        parser.error('--launch requires a dedicated --dolphin-user directory')
    if args.shared_memory_pid is not None:
        if (not sys.platform.startswith('linux') or args.shared_memory_pid <= 0 or
                not args.read_only or args.launch):
            parser.error('--shared-memory-pid requires Linux, a positive PID, --read-only and no --launch')
        dme = SharedMemoryReader(args.shared_memory_pid)
    else:
        import dolphin_memory_engine as dme
    child = None
    log = None
    try:
        if args.launch:
            args.dolphin_user.mkdir(parents=True, exist_ok=True)
            config_dir = args.dolphin_user / 'Config'
            config_dir.mkdir(exist_ok=True)
            config = configparser.ConfigParser(); config.optionxform = str
            config.read(config_dir / 'Dolphin.ini')
            for section, values in {'Analytics': {'Enabled': 'False', 'PermissionAsked': 'True'}, 'Interface': {'ConfirmStop': 'False', 'UsePanicHandlers': 'False'}}.items():
                if not config.has_section(section): config.add_section(section)
                for key, value in values.items(): config.set(section, key, value)
            with (config_dir / 'Dolphin.ini').open('w') as stream: config.write(stream)
            (config_dir / 'Logger.ini').write_text('[Options]\nWriteToConsole = True\nWriteToFile = True\nVerbosity = 4\n[Logs]\nBOOT = True\nOSREPORT = True\nHLE = True\n')
            log = (args.dolphin_user / 'launch.log').open('w')
            executable = 'dolphin-emu'
            if sys.platform.startswith('linux'):
                # DME supports an exact process name; select our child, not another open Dolphin.
                process_name = f'apd-{os.getpid()}'
                executable_path = args.dolphin_user.resolve() / process_name
                executable_path.symlink_to(shutil.which('dolphin-emu'))
                executable = str(executable_path)
                os.environ['DME_DOLPHIN_PROCESS_NAME'] = process_name
            command = [executable, '-b', '-u', str(args.dolphin_user.resolve()), '-e', str(args.launch.resolve()),
                       '-C', 'Dolphin.Core.EnableCheats=False', '-C', 'Dolphin.Core.PauseOnPanic=False',
                       '-C', 'Dolphin.Core.EmulationSpeed=1.0', '-C', 'Dolphin.DSP.Backend=Null']
            env = dict(os.environ)
            if args.null_video:
                if sys.platform.startswith('linux'):
                    env['QT_QPA_PLATFORM'] = 'xcb'
                command += ['-v', 'Null']
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        deadline = time.monotonic() + args.timeout
        result = None
        while time.monotonic() < deadline:
            if child and child.poll() is not None:
                raise RuntimeError(f'Dolphin exited ({child.returncode}); inspect {args.dolphin_user}/launch.log')
            if not dme.is_hooked():
                dme.hook()
            if dme.is_hooked():
                try:
                    region = bytes(dme.read_bytes(MEM1_START, 6)).decode('ascii', errors='replace')
                except RuntimeError as exc:
                    raise RuntimeError(
                        'Dolphin detected but MEM1 is unreadable. On Linux, Yama may deny '
                        'an independently launched client. For this running game, use '
                        '--read-only --shared-memory-pid <DOLPHIN_PID>; for DME writes, '
                        'launch Dolphin with --launch. No system permission changes are required.'
                    ) from exc
                if region in ('RMGP01', 'RMGE01') and (args.region is None or region == args.region):
                    result = discover(bytes(dme.read_bytes(MEM1_START, MEM1_END - MEM1_START)), region)
                    if result:
                        break
            time.sleep(0.25)
        if result is None:
            raise RuntimeError(f'No ready AP mailbox; DME status={dme.get_status()}. Use --launch for parent/child Linux permissions; no kernel settings are changed.')
        address = result['address']
        # Check the descriptor again immediately before the write.
        current = bytes(dme.read_bytes(result['discovery'], 48))
        expected_header = struct.pack('>6I', 0x4150424F, 0x4F543031, 1, 48,
                                      int.from_bytes(result['region'][:4].encode(), 'big'), 0x30310000)
        if current[:24] != expected_header or struct.unpack_from('>6I', current, 24) != (3, result['module'], result['moduleSize'], address, 12, 0):
            raise RuntimeError('Descriptor changed during discovery')
        if bytes(dme.read_bytes(address, 8)) != struct.pack('>2I', 0x4150534D, 1):
            raise RuntimeError('Mailbox protocol changed before write')
        if not args.read_only:
            dme.write_word(address + 8, args.value)
            value = dme.read_word(address + 8)
            if value != args.value:
                raise RuntimeError(f'Mailbox readback failed: {value:08X}')
            result['written'] = args.value
            result['readback'] = value
        expected_value = result.get('written', result['testValue'])
        until = time.monotonic() + args.observe_seconds
        while time.monotonic() < until:
            if bytes(dme.read_bytes(address, 12)) != struct.pack('>3I', 0x4150534D, 1, expected_value):
                raise RuntimeError('Mailbox changed during the observation period')
            time.sleep(0.1)
        result['observed_seconds'] = args.observe_seconds
        if args.shared_memory_pid is not None:
            result['backend'] = 'shared-memory-read-only'
            result['dolphin_pid'] = args.shared_memory_pid
        if child:
            result['dolphin_pid'] = child.pid
            if sys.platform.startswith('linux'):
                result['dolphin_process_name'] = process_name
        print(json.dumps(result, indent=2))
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + '\n')
    finally:
        if dme.is_hooked():
            dme.un_hook()
        if child and child.poll() is None and not args.keep_running:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
        if log:
            log.close()
        if args.launch and sys.platform.startswith('linux'):
            (args.dolphin_user.resolve() / f'apd-{os.getpid()}').unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        sys.exit(f'Error: {exc}')
