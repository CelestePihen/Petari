#!/usr/bin/env python3
"""Discover AP mailbox v2 in MEM1 and verify a Dolphin Memory Engine round trip."""

import argparse
import configparser
import json
import math
from pathlib import Path
import os
import struct
import stat
import shutil
import sys
import subprocess
import tempfile
import time
from archipelago_protocol import (
    DISCOVERY_VERSION,
    DISCOVERY_SIZE,
    MAILBOX_MAGIC,
    MAILBOX_VERSION,
    MAILBOX_SIZE,
    EVENT_CAPACITY,
    EVENT_SIZE,
    GALAXY_NAME_SIZE,
    INCOMING_EVENTS_OFFSET,
    INCOMING_ITEM_SIZE,
    INCOMING_QUEUE_OFFSET,
    INCOMING_QUEUE_SIZE,
    INCOMING_READ_COUNT_OFFSET,
    INCOMING_WRITE_COUNT_OFFSET,
)

MEM1_START, MEM1_END = 0x80000000, 0x81800000
SIGNATURE = b"APBOOT01"

QUEUE_OFFSET = 12
WRITE_COUNT_OFFSET = QUEUE_OFFSET
READ_COUNT_OFFSET = QUEUE_OFFSET + 4
EVENTS_OFFSET = QUEUE_OFFSET + 8


def discover(data: bytes, region: str) -> dict | None:
    """
    Recherche et valide le descripteur de découverte Archipelago dans la mémoire MEM1.

    Parcourt les données mémoire à la recherche de la signature APBOOT01,
    puis vérifie la région du jeu, la version du protocole, l'état du
    bootstrap et les adresses mémoire du module et de la mailbox.

    Args:
        data (bytes): Contenu de la mémoire MEM1 de Dolphin.
        region (str): Identifiant de la région attendue (RMGP01 ou RMGE01).

    Returns:
        dict | None: Informations sur la mailbox si elle est trouvée,
        sinon None.

    Raises:
        RuntimeError: Si le bootstrap signale une erreur ou si plusieurs
        descripteurs valides sont trouvés.
    """
    found = []
    offset = 0
    while True:
        offset = data.find(SIGNATURE, offset)
        if offset < 0:
            break
        if offset % 4 == 0 and offset + 48 <= len(data):
            words = struct.unpack_from(">12I", data, offset)
            (
                _,
                _,
                version,
                size,
                r0,
                r1,
                status,
                module,
                length,
                mailbox,
                mailbox_size,
                error,
            ) = words
            actual_region = (r0.to_bytes(4, "big") + r1.to_bytes(4, "big")[:2]).decode(
                "ascii", errors="replace"
            )
            if (
                version == DISCOVERY_VERSION
                and size == DISCOVERY_SIZE
                and actual_region == region
                and r1 == 0x30310000
            ):
                if status == 4:
                    raise RuntimeError(
                        f"Bootstrap error {error} at 0x{MEM1_START + offset:08X}"
                    )
                if (
                    status == 3
                    and module % 32 == 0
                    and MEM1_START <= module < MEM1_END
                    and 0 < length <= MEM1_END - module
                    and mailbox_size == MAILBOX_SIZE
                    and module <= mailbox <= module + length - mailbox_size
                ):
                    mailbox_offset = mailbox - MEM1_START
                    if mailbox_offset + MAILBOX_SIZE > len(data):
                        offset += len(SIGNATURE)
                        continue
                    magic, protocol, value = struct.unpack_from(
                        ">3I", data, mailbox_offset
                    )
                    if (magic, protocol) == (MAILBOX_MAGIC, MAILBOX_VERSION):
                        found.append(
                            dict(
                                discovery=MEM1_START + offset,
                                address=mailbox,
                                region=region,
                                version=protocol,
                                testValue=value,
                                module=module,
                                moduleSize=length,
                            )
                        )
        offset += len(SIGNATURE)
    if len(found) > 1:
        raise RuntimeError("Multiple valid AP descriptors; refusing an ambiguous write")
    return found[0] if found else None


def verify_call_hooks(read_bytes, discovery: dict, manifest: dict) -> list[dict]:
    """
    Vérifie que les hooks Kamek sont correctement installés en mémoire.

    Compare les instructions PowerPC présentes aux adresses des hooks
    avec celles attendues à partir du manifeste de compilation.

    Args:
        read_bytes (callable): Fonction permettant de lire la mémoire Dolphin.
        discovery (dict): Informations du module obtenues par discover().
        manifest (dict): Manifeste contenant les hooks et leurs adresses.

    Returns:
        list[dict]: Liste des hooks vérifiés avec leurs adresses,
        destinations et instructions.

    Raises:
        RuntimeError: Si le manifeste est invalide, si un hook dépasse
        les limites autorisées ou si une instruction ne correspond pas.
    """
    if (
        manifest.get("region") != discovery["region"]
        or manifest.get("module_size") != discovery["moduleSize"]
        or manifest.get("discovery") != discovery["discovery"]
        or type(manifest.get("code_size")) is not int
        or not 0 <= manifest["code_size"] <= discovery["moduleSize"]
        or not isinstance(manifest.get("module_hooks"), list)
    ):
        raise RuntimeError("Hook manifest does not match the discovered module")
    verified = []
    seen = set()
    for hook in manifest["module_hooks"]:
        if not isinstance(hook, dict) or not isinstance(hook.get("name"), str):
            raise RuntimeError("Invalid call hook in manifest")
        address, offset = hook.get("address"), hook.get("target_offset")
        if (
            type(address) is not int
            or type(offset) is not int
            or address % 4
            or not MEM1_START <= address <= MEM1_END - 4
            or offset % 4
            or not 0 <= offset <= manifest["code_size"] - 4
            or address in seen
        ):
            raise RuntimeError("Invalid call hook bounds in manifest")
        seen.add(address)
        target = discovery["module"] + offset
        delta = target - address
        if not -0x02000000 <= delta < 0x02000000:
            raise RuntimeError("Call hook is outside PowerPC branch range")
        expected = 0x48000001 | (delta & 0x03FFFFFC)
        actual = struct.unpack(">I", bytes(read_bytes(address, 4)))[0]
        if actual != expected:
            raise RuntimeError(
                f'Hook {hook["name"]} is not installed at 0x{address:08X}'
            )
        verified.append(
            dict(name=hook["name"], address=address, target=target, instruction=actual)
        )
    return verified


class SharedMemoryReader:
    """
    Permet de lire la mémoire MEM1 d'une instance Dolphin déjà lancée.

    Accède au fichier de mémoire partagée Linux utilisé par Dolphin,
    sans utiliser ptrace ni modifier les protections du noyau.

    Cette classe fournit uniquement un accès en lecture.
    """

    def __init__(self, pid: int):
        """
        Initialise le lecteur de mémoire partagée.

        Args:
            pid (int): Identifiant du processus Dolphin à utiliser.
        """
        self.pid = pid
        self.fd = None

    def hook(self):
        """
        Ouvre et valide le fichier de mémoire partagée de Dolphin.

        Vérifie notamment l'identité du processus, les permissions,
        le fichier de mémoire partagée et sa correspondance avec
        le mapping MEM1 du processus.

        Raises:
            RuntimeError: Si le processus ou sa mémoire partagée
            ne correspondent pas aux conditions attendues.
            OSError: Si l'accès aux fichiers du processus échoue.
        """
        proc = Path("/proc") / str(self.pid)
        if (proc / "exe").resolve(strict=True).name != "dolphin-emu":
            raise RuntimeError(f"PID {self.pid} is not dolphin-emu")
        expected = f"/dev/shm/dolphin-emu.{self.pid}"
        candidates = []
        for entry in (proc / "fd").iterdir():
            try:
                target = os.readlink(entry)
            except FileNotFoundError:
                continue
            if target in (expected, expected + " (deleted)"):
                candidates.append(entry)
        if not candidates:
            raise RuntimeError(
                f"No Dolphin shared memory for PID {self.pid}; start emulation first"
            )
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
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_size < MEM1_END - MEM1_START
                ):
                    raise RuntimeError(
                        "Invalid Dolphin shared-memory owner, size or type"
                    )
                identities.add((info.st_dev, info.st_ino))
            if len(identities) != 1:
                raise RuntimeError("Ambiguous Dolphin shared-memory files")
            device, inode = identities.pop()
            device_text = f"{os.major(device):02x}:{os.minor(device):02x}"
            valid_mapping = False
            for line in (proc / "maps").read_text().splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) < 5:
                    continue
                start, end = (int(value, 16) for value in fields[0].split("-"))
                if (
                    fields[1] == "rw-s"
                    and int(fields[2], 16) == 0
                    and fields[3] == device_text
                    and int(fields[4]) == inode
                    and end - start >= MEM1_END - MEM1_START
                ):
                    valid_mapping = True
            if not valid_mapping:
                raise RuntimeError("No matching MEM1 mapping in this Dolphin process")
            self.fd = opened
            opened = None
        finally:
            if opened is not None:
                os.close(opened)

    def is_hooked(self) -> bool:
        """
        Indique si le lecteur possède un accès ouvert à la mémoire partagée.

        Returns:
            bool: True si la mémoire est accessible, False sinon.
        """
        return self.fd is not None

    def get_status(self) -> str:
        """
        Retourne une description de l'état du lecteur.

        Returns:
            str: Description du backend de lecture et PID de Dolphin.
        """
        return f"read-only shared memory, PID {self.pid}"

    def un_hook(self):
        """
        Ferme le descripteur du fichier de mémoire partagée.

        Libère les ressources utilisées par le lecteur.
        """
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read_bytes(self, address: int, size: int) -> bytes:
        """
        Lit une quantité précise d'octets dans la mémoire MEM1 de Dolphin.

        Convertit l'adresse mémoire Wii en position dans le fichier
        de mémoire partagée, puis récupère les octets demandés.

        Args:
            address (int): Adresse mémoire Wii à lire.
            size (int): Nombre d'octets à récupérer.

        Returns:
            bytes: Octets lus dans la mémoire du jeu.

        Raises:
            RuntimeError: Si le lecteur est fermé, si l'adresse dépasse
            MEM1 ou si la lecture est incomplète.
            OSError: Si la lecture du fichier échoue.
        """
        if (
            self.fd is None
            or size < 0
            or not MEM1_START <= address <= MEM1_END
            or size > MEM1_END - address
        ):
            raise RuntimeError("Shared-memory read outside MEM1 or reader closed")
        data = os.pread(self.fd, size, address - MEM1_START)
        if len(data) != size:
            raise RuntimeError("Short Dolphin shared-memory read")
        return data


def read_star_events(read_bytes, mailbox_address: int) -> dict:
    """
    Lit les événements StarCollected en attente dans la mailbox.

    Récupère writeCount et readCount, calcule le nombre d'événements
    non consommés, puis parcourt la file circulaire dans l'ordre FIFO.

    Chaque événement contient le nom interne de la galaxie,
    l'identifiant de l'étoile et un numéro de séquence.

    La fonction vérifie également l'état des compteurs afin de
    détecter certaines modifications concurrentes pendant la lecture.

    Cette fonction ne consomme pas les événements et ne modifie
    pas readCount.

    Args:
        read_bytes (callable): Fonction permettant de lire la mémoire Dolphin.
        mailbox_address (int): Adresse mémoire de la mailbox Archipelago.

    Returns:
        dict: État de la file contenant writeCount, readCount,
        le nombre d'événements en attente et leur liste.

    Raises:
        RuntimeError: Si les compteurs indiquent un état invalide
        ou changent pendant la vérification.
        UnicodeDecodeError: Si un nom de galaxie contient des octets
        incompatibles avec l'encodage ASCII.
    """
    header = bytes(read_bytes(mailbox_address + WRITE_COUNT_OFFSET, 8))
    write_count, read_count = struct.unpack(">2I", header)

    pending = (write_count - read_count) & 0xFFFFFFFF

    if pending > EVENT_CAPACITY:
        raise RuntimeError(f"Invalid event queue state: {pending} pending")

    events = []

    for sequence in range(read_count, read_count + pending):
        index = sequence % EVENT_CAPACITY
        address = mailbox_address + EVENTS_OFFSET + index * EVENT_SIZE

        raw = bytes(read_bytes(address, EVENT_SIZE))
        name = raw[:GALAXY_NAME_SIZE].split(b"\0", 1)[0].decode("ascii")
        star_id = struct.unpack_from(">I", raw, GALAXY_NAME_SIZE)[0]

        events.append(
            {"sequence": sequence & 0xFFFFFFFF, "galaxyName": name, "starId": star_id}
        )

        # vérifier que les compteurs n'ont pas changé pendant la lecture
        current = bytes(read_bytes(mailbox_address + WRITE_COUNT_OFFSET, 8))

        if current != header:
            raise RuntimeError("Event queue changed during read")

    return {
        "writeCount": write_count,
        "readCount": read_count,
        "pending": pending,
        "events": events,
    }


def ack_star_events(dme, mailbox_address: int, queue: dict) -> int:
    """
    Acquitte les événements lus en avançant readCount.

    Args:
        dme: Interface mémoire Dolphin disposant de read_word et write_word.
        mailbox_address (int): Adresse de la mailbox.
        queue (dict): Résultat de read_star_events().

    Returns:
        int: Nombre d'événements acquittés.

    Raises:
        RuntimeError: Si readCount a changé de manière inattendue
        ou si une écriture échoue.
    """
    address = mailbox_address + READ_COUNT_OFFSET
    expected = queue["readCount"]

    for event in queue["events"]:
        current = dme.read_word(address)

        if current != expected:
            raise RuntimeError(
                f"readCount changed unexpectedly: {current} != {expected}"
            )

        expected = (expected + 1) & 0xFFFFFFFF

        dme.write_word(address, expected)

        if dme.read_word(address) != expected:
            raise RuntimeError("Failed to acknowledge star event")

    return len(queue["events"])


def watch_star_events(
    dme,
    mailbox_address: int,
    acknowledge: bool = False,
    *,
    send_pending_item=None,
    stop_after_item: bool = False,
):
    """
    Surveille les événements StarCollected pendant l'exécution du jeu.

    Lit régulièrement la file d'événements, affiche les nouvelles étoiles
    et les acquitte si le mode écriture est activé.

    Args:
        dme: Interface de communication avec Dolphin.
        mailbox_address (int): Adresse de la mailbox Archipelago.
        acknowledge (bool): Indique si les événements doivent être acquittés.
        send_pending_item: Envoie l'item si son délai est écoulé et renvoie
            True une fois l'envoi effectué.
        stop_after_item (bool): Termine la surveillance après cet envoi.
    """
    print("Watching star events... Press Ctrl+C to stop.")

    last_write_count = None
    last_read_count = None

    try:
        while True:
            try:
                queue = read_star_events(dme.read_bytes, mailbox_address)
            except RuntimeError as exc:
                print(f"Dolphin memory unavailable: {exc}")
                print("Stopping mailbox monitoring.")
                break

            if (
                queue["writeCount"] != last_write_count
                or queue["readCount"] != last_read_count
            ):
                print(json.dumps(queue, indent=2), flush=True)

                last_write_count = queue["writeCount"]
                last_read_count = queue["readCount"]

            if acknowledge and queue["pending"] > 0:
                count = ack_star_events(dme, mailbox_address, queue)

                print(f"Acknowledged {count} star event(s)", flush=True)

                last_read_count = (queue["readCount"] + count) & 0xFFFFFFFF

            if send_pending_item is not None:
                if send_pending_item() and stop_after_item:
                    break

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopped watching star events.")


def send_test_item(dme, mailbox_address: int, item_id=2, item_index=0):
    """
    Ajoute un événement de test dans la file Python → Jeu.

    Args:
        dme: Interface dolphin-memory-engine.
        mailbox_address (int): Adresse de la mailbox.
        item_id (int): Identifiant de l'item à envoyer.
        item_index (int): Index de l'item dans la séquence Archipelago.

    Returns:
        int: Numéro de séquence de l'événement envoyé.

    Raises:
        RuntimeError: Si la file est pleine ou si une écriture échoue.
    """
    write_address = mailbox_address + INCOMING_WRITE_COUNT_OFFSET
    read_address = mailbox_address + INCOMING_READ_COUNT_OFFSET

    write_count = dme.read_word(write_address)
    read_count = dme.read_word(read_address)

    pending = (write_count - read_count) & 0xFFFFFFFF

    if pending >= EVENT_CAPACITY:
        raise RuntimeError("Incoming item queue is full")

    slot = write_count % EVENT_CAPACITY
    event_address = mailbox_address + INCOMING_EVENTS_OFFSET + slot * INCOMING_ITEM_SIZE

    event = struct.pack(">2I", item_id, item_index)

    dme.write_bytes(event_address, event)

    if bytes(dme.read_bytes(event_address, INCOMING_ITEM_SIZE)) != event:
        raise RuntimeError("Incoming item write verification failed")

    dme.write_word(write_address, (write_count + 1) & 0xFFFFFFFF)

    return write_count


def read_incoming_items(read_bytes, mailbox_address):
    """Lit les événements présents dans la file Python -> Jeu"""
    address = mailbox_address + INCOMING_QUEUE_OFFSET

    write_count, read_count = struct.unpack(">2I", bytes(read_bytes(address, 8)))

    pending = (write_count - read_count) & 0xFFFFFFFF

    if pending > EVENT_CAPACITY:
        raise RuntimeError("Invalid incoming item queue state")

    events = []

    for sequence in range(read_count, read_count + pending):
        slot = sequence % EVENT_CAPACITY
        event_address = (
            mailbox_address + INCOMING_EVENTS_OFFSET + slot * INCOMING_ITEM_SIZE
        )

        item_id, item_index = struct.unpack(
            ">2I", bytes(read_bytes(event_address, INCOMING_ITEM_SIZE))
        )

        events.append(
            {
                "sequence": sequence & 0xFFFFFFFF,
                "itemId": item_id,
                "itemIndex": item_index,
            }
        )

    return {
        "writeCount": write_count,
        "readCount": read_count,
        "pending": pending,
        "events": events,
    }


def parse_test_item_ids(value):
    """Parse a comma-separated list of unsigned 32-bit item IDs."""
    try:
        item_ids = [int(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected comma-separated integer item IDs (e.g. 200,201,202)"
        ) from exc
    if any(not 0 <= item_id <= 0xFFFFFFFF for item_id in item_ids):
        raise argparse.ArgumentTypeError("item IDs must be unsigned 32-bit integers")
    return item_ids


def main():
    """
    Point d'entrée principal du client de test Archipelago.

    Analyse les arguments de la ligne de commande, puis se connecte
    à Dolphin ou lance une nouvelle instance selon les options choisies.

    Recherche et valide la mailbox, vérifie éventuellement les hooks,
    teste la lecture/écriture de testValue, puis récupère les
    événements StarCollected présents dans la file.

    Affiche les résultats au format JSON et peut les enregistrer
    dans un fichier.

    Ferme les ressources ouvertes et arrête uniquement l'instance
    Dolphin lancée par le script, sauf si --keep-running est utilisé.
    """
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--region", choices=["RMGP01", "RMGE01"])

    parser.add_argument("--value", type=lambda x: int(x, 0), default=0x12345678)

    parser.add_argument("--timeout", type=float, default=60)

    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=5,
        help="Check mailbox stability while the game continues booting",
    )

    parser.add_argument("--read-only", action="store_true")

    parser.add_argument(
        "--shared-memory-pid",
        type=int,
        help="Linux read-only access to an already running Dolphin PID",
    )

    parser.add_argument(
        "--launch",
        type=Path,
        help="Launch a private test game as this Python process child",
    )

    parser.add_argument("--dolphin-user", type=Path)

    parser.add_argument(
        "--null-video",
        action="store_true",
        help="Use Dolphin Null video backend; no gameplay rendering test",
    )

    parser.add_argument("--keep-running", action="store_true")

    parser.add_argument("--output", type=Path)

    parser.add_argument(
        "--manifest",
        type=Path,
        help="Verify installed call hooks against a build manifest",
    )

    parser.add_argument(
        "--ack-events",
        action="store_true",
        help="Acknowledge pending star events after reading them",
    )

    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously monitor star events until Ctrl+C",
    )

    parser.add_argument(
        "--send-test-item",
        action="store_true",
        help="Send a test item to the incoming item queue",
    )

    parser.add_argument(
        "--send-test-stars",
        type=int,
        default=0,
        metavar="COUNT",
        help="Send COUNT test Power Stars to the game",
    )

    item_id_options = parser.add_mutually_exclusive_group()
    item_id_options.add_argument(
        "--send-test-item-id",
        type=int,
        default=None,
        metavar="ID",
        help="Send an Archipelago item with the specified ID",
    )

    item_id_options.add_argument(
        "--send-test-item-ids",
        type=parse_test_item_ids,
        metavar="ID,ID,...",
        help="Send Archipelago items in the specified comma-separated order",
    )

    parser.add_argument(
        "--send-test-item-delay",
        type=float,
        default=0,
        metavar="SECONDS",
        help="Delay --send-test-item-id or --send-test-item-ids by SECONDS after attaching to Dolphin (default: 0)",
    )

    args = parser.parse_args()
    test_item_ids = args.send_test_item_ids
    if test_item_ids is None:
        test_item_ids = [] if args.send_test_item_id is None else [args.send_test_item_id]

    if test_item_ids and args.read_only:
        parser.error("--send-test-item-id/--send-test-item-ids requires write access")

    if not math.isfinite(args.send_test_item_delay) or args.send_test_item_delay < 0:
        parser.error("--send-test-item-delay must be finite and nonnegative")

    if args.send_test_stars < 0:
        parser.error("--send-test-stars must be nonnegative")

    if args.send_test_stars and args.read_only:
        parser.error("--send-test-stars requires write access")

    if args.send_test_item and args.read_only:
        parser.error("--send-test-item requires write access to Dolphin")

    if args.ack_events and args.read_only:
        parser.error("--ack-events cannot be used with --read-only")

    if args.observe_seconds < 0 or args.timeout <= 0:
        parser.error("Timeout must be positive and observation duration nonnegative")

    if not 0 <= args.value <= 0xFFFFFFFF:
        parser.error("--value must be a u32")

    if args.shared_memory_pid is not None:
        if (
            not sys.platform.startswith("linux")
            or args.shared_memory_pid <= 0
            or not args.read_only
            or args.launch
        ):
            parser.error(
                "--shared-memory-pid requires Linux, a positive PID, --read-only and no --launch"
            )
        dme = SharedMemoryReader(args.shared_memory_pid)
    else:
        import dolphin_memory_engine as dme

    child = None
    log = None
    temp_dir = None
    executable_path: Path | None = None

    try:
        if args.launch:
            if args.dolphin_user:
                args.dolphin_user.mkdir(parents=True, exist_ok=True)

                config_dir = args.dolphin_user / "Config"
                config_dir.mkdir(exist_ok=True)

                config = configparser.ConfigParser()
                config.optionxform = str
                config.read(config_dir / "Dolphin.ini")

                for section, values in {
                    "Analytics": {"Enabled": "False", "PermissionAsked": "True"},
                    "Interface": {"ConfirmStop": "False", "UsePanicHandlers": "False"},
                }.items():
                    if not config.has_section(section):
                        config.add_section(section)

                    for key, value in values.items():
                        config.set(section, key, value)

                with (config_dir / "Dolphin.ini").open("w") as stream:
                    config.write(stream)
                (config_dir / "Logger.ini").write_text(
                    "[Options]\nWriteToConsole = True\nWriteToFile = True\nVerbosity = 4\n[Logs]\nBOOT = True\nOSREPORT = True\nHLE = True\n"
                )

            log_path = (
                args.dolphin_user / "launch.log"
                if args.dolphin_user
                else Path("dolphin-ap-launch.log")
            )

            log = log_path.open("w")
            executable = "dolphin-emu"

            if sys.platform.startswith("linux"):
                # DME supports an exact process name; select our child, not another open Dolphin.
                process_name = f"apd-{os.getpid()}"
                temp_dir = tempfile.TemporaryDirectory(prefix="ap-dolphin-")
                executable_path = Path(temp_dir.name) / process_name

                dolphin_path = shutil.which("dolphin-emu")

                if dolphin_path is None:
                    raise RuntimeError("Dolphin executable not found in PATH")

                executable_path.symlink_to(dolphin_path)

                executable = str(executable_path)
                os.environ["DME_DOLPHIN_PROCESS_NAME"] = process_name

            command = [
                executable,
                # "-b",
                "-e",
                str(args.launch.resolve()),
                "-C",
                "Dolphin.Core.EnableCheats=False",
                "-C",
                "Dolphin.Core.PauseOnPanic=False",
                "-C",
                "Dolphin.Core.EmulationSpeed=1.0",
                "-C",
                "Dolphin.DSP.Backend=Null",
            ]

            if args.dolphin_user:
                command += ["-u", str(args.dolphin_user.resolve())]

            env = dict(os.environ)

            if args.null_video:
                if sys.platform.startswith("linux"):
                    env["QT_QPA_PLATFORM"] = "xcb"
                command += ["-v", "Null"]

            child = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, env=env
            )

        deadline = time.monotonic() + args.timeout
        result = None
        attached_at = None

        while time.monotonic() < deadline:
            if child and child.poll() is not None:
                raise RuntimeError(
                    f"Dolphin exited ({child.returncode}); inspect {log_path}"
                )

            if not dme.is_hooked():
                dme.hook()

            if dme.is_hooked():
                try:
                    region = bytes(dme.read_bytes(MEM1_START, 6)).decode(
                        "ascii", errors="replace"
                    )
                except RuntimeError as exc:
                    raise RuntimeError(
                        "Dolphin detected but MEM1 is unreadable. On Linux, Yama may deny "
                        "an independently launched client. For this running game, use "
                        "--read-only --shared-memory-pid <DOLPHIN_PID>; for DME writes, "
                        "launch Dolphin with --launch. No system permission changes are required."
                    ) from exc
                if attached_at is None:
                    attached_at = time.monotonic()
                    if test_item_ids and args.send_test_item_delay > 0:
                        scheduled_items = ", ".join(f"itemId={item_id}" for item_id in test_item_ids)
                        print(
                            f"Scheduled test item(s): {scheduled_items}, "
                            f"delay={args.send_test_item_delay:g}s after Dolphin attachment",
                            flush=True,
                        )
                if region in ("RMGP01", "RMGE01") and (
                    args.region is None or region == args.region
                ):
                    result = discover(
                        bytes(dme.read_bytes(MEM1_START, MEM1_END - MEM1_START)), region
                    )
                    if result:
                        break
            time.sleep(0.25)

        if result is None:
            raise RuntimeError(
                f"No ready AP mailbox; DME status={dme.get_status()}. Use --launch for parent/child Linux permissions; no kernel settings are changed."
            )
        address = result["address"]

        # Check the descriptor again immediately before the write.
        current = bytes(dme.read_bytes(result["discovery"], DISCOVERY_SIZE))
        expected_header = struct.pack(
            ">6I",
            0x4150424F,
            0x4F543031,
            DISCOVERY_VERSION,
            DISCOVERY_SIZE,
            int.from_bytes(result["region"][:4].encode(), "big"),
            0x30310000,
        )

        if current[:24] != expected_header or struct.unpack_from(
            ">6I", current, 24
        ) != (3, result["module"], result["moduleSize"], address, MAILBOX_SIZE, 0):
            raise RuntimeError("Descriptor changed during discovery")

        if bytes(dme.read_bytes(address, 8)) != struct.pack(
            ">2I", MAILBOX_MAGIC, MAILBOX_VERSION
        ):
            raise RuntimeError("Mailbox protocol changed before write")

        if args.manifest:
            result["hooks"] = verify_call_hooks(
                dme.read_bytes, result, json.loads(args.manifest.read_text())
            )

        if not args.read_only:
            dme.write_word(address + 8, args.value)
            value = dme.read_word(address + 8)
            if value != args.value:
                raise RuntimeError(f"Mailbox readback failed: {value:08X}")
            result["written"] = args.value
            result["readback"] = value

        item_sent = False
        item_send_at = attached_at + args.send_test_item_delay

        def send_pending_item():
            nonlocal item_sent
            if not item_sent and time.monotonic() >= item_send_at:
                for index, item_id in enumerate(test_item_ids):
                    sequence = send_test_item(
                        dme,
                        address,
                        item_id=item_id,
                        item_index=index,
                    )
                    print(
                        f"Sent test item: itemId={item_id}, "
                        f"itemIndex={index}, sequence={sequence}",
                        flush=True,
                    )
                item_sent = True
            return item_sent

        expected_value = result.get("written", result["testValue"])
        until = time.monotonic() + args.observe_seconds

        while time.monotonic() < until:
            if bytes(dme.read_bytes(address, 12)) != struct.pack(
                ">3I", MAILBOX_MAGIC, MAILBOX_VERSION, expected_value
            ):
                raise RuntimeError("Mailbox changed during the observation period")
            if test_item_ids and args.send_test_item_delay > 0:
                send_pending_item()
            time.sleep(0.1)

        result["observed_seconds"] = args.observe_seconds

        if args.shared_memory_pid is not None:
            result["backend"] = "shared-memory-read-only"
            result["dolphin_pid"] = args.shared_memory_pid

        if child:
            result["dolphin_pid"] = child.pid
            if sys.platform.startswith("linux"):
                result["dolphin_process_name"] = process_name

        result["starQueue"] = read_star_events(dme.read_bytes, address)

        print(json.dumps(result, indent=2))

        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")

        if test_item_ids:
            send_pending_item()

        if args.send_test_stars:
            for index in range(args.send_test_stars):
                send_test_item(
                    dme,
                    address,
                    item_id=2,
                    item_index=index,
                )

            print(f"Sent {args.send_test_stars} test Power Stars", flush=True)

        if args.send_test_item:
            sequence = send_test_item(dme, address)

            print(
                json.dumps(
                    {"incomingQueue": read_incoming_items(dme.read_bytes, address)},
                    indent=2,
                )
            )

            print(
                f"Sent test item: itemId=2, itemIndex=0, sequence={sequence}",
                flush=True,
            )

        if test_item_ids and not item_sent:
            watch_star_events(
                dme,
                address,
                acknowledge=args.ack_events,
                send_pending_item=send_pending_item,
                stop_after_item=not args.watch,
            )
        elif args.watch:
            watch_star_events(dme, address, acknowledge=args.ack_events)
        elif args.ack_events:
            count = ack_star_events(dme, address, result["starQueue"])
            print(f"Acknowledge {count} star event(s)")

        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
    finally:
        if dme.is_hooked():
            dme.un_hook()
        if child and child.poll() is None and not args.keep_running:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if log:
            log.close()
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        sys.exit(f"Error: {exc}")
