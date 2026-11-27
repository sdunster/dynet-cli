"""The ``dynet`` command."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

from aiodynet import (
    MAX_CHANNEL_FADE,
    MAX_PRESET_FADE,
    PHYSICAL_LEVEL_FULL,
    Dynet2Command,
    Dynet2Frame,
    DynetClient,
    DynetError,
    InvalidPacketError,
    Level,
    Packet,
    PresetReport,
    PresetSelect,
    Received,
    RequestChannelLevel,
    RequestPreset,
    SetChannelLevel,
    SetPhysicalChannelLevel,
    check_connection,
    decode,
)

from . import __version__
from .area import (
    FADE_ACTIONS,
    LINK_ACTIONS,
    SIMPLE_ACTIONS,
    SWITCH_ACTIONS,
    VALUE_ACTIONS,
    build_message,
    read_links,
)
from .capture import (
    CaptureFormatError,
    CaptureWriter,
    PacketRecord,
    Summary,
    read_records,
)
from .device import (
    DEFAULT_EEPROM_SIZE,
    FLASH_INTERVAL,
    MERGE_GAP,
    SYSTEM_BUILDER,
    NoReplyError,
    compact_hex_dump,
    data_ranges,
    device_info,
    device_name,
    device_request,
    eeprom_size,
    flash,
    go_back_to_work,
    hex_dump,
    light_level,
    read_memory,
    render_info,
    reset,
)
from .probe import (
    ALL_DEVICES,
    Session,
    Step,
    area_requests,
    ask_firmware,
    ask_preset,
    ask_signon,
    exercise_steps,
)
from .render import describe, describe_noise, message_area, noise_json, to_json
from .sniff import Reassembler, open_raw_socket, sniff

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from aiodynet import Dynet2DeviceRequest

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 3


class UsageError(Exception):
    """Bad input that argparse can't catch on its own."""


# Argument types


def _int_in(low: int, high: int, what: str) -> type[int]:
    def parse(text: str) -> int:
        try:
            value = int(text, 0)
        except ValueError:
            msg = f"{what} must be a whole number, got {text!r}"
            raise argparse.ArgumentTypeError(msg) from None
        if not low <= value <= high:
            msg = f"{what} must be {low}-{high}, got {value}"
            raise argparse.ArgumentTypeError(msg)
        return value

    parse.__name__ = what
    return parse  # type: ignore[return-value]


def parse_level(text: str) -> Level:
    """Parse ``40``, ``40%``, ``off``, ``on``/``full`` or ``raw:0x80``."""
    lowered = text.strip().lower()
    try:
        if lowered == "off":
            return Level.OFF
        if lowered in {"on", "full"}:
            return Level.FULL
        if lowered.startswith("raw:"):
            return Level(int(lowered[4:], 0))
        return Level.from_percent(float(lowered.removesuffix("%")))
    except ValueError as err:
        msg = f"level must be 0-100 (%), off, on or raw:<byte>, got {text!r} ({err})"
        raise argparse.ArgumentTypeError(msg) from None


def parse_fade(limit: float) -> type[float]:
    """Make a parser for a fade of up to `limit` seconds."""

    def parse(text: str) -> float:
        try:
            value = float(text.removesuffix("s"))
        except ValueError:
            msg = f"fade must be seconds, got {text!r}"
            raise argparse.ArgumentTypeError(msg) from None
        if not 0 <= value <= limit:
            msg = f"fade must be 0-{limit:g} seconds, got {value:g}"
            raise argparse.ArgumentTypeError(msg)
        return value

    parse.__name__ = "fade"
    return parse  # type: ignore[return-value]


def _int_list(low: int, high: int, what: str) -> Callable[[str], list[int]]:
    """Make a parser for a list such as ``1-10,12,20-22``, or ``none``."""
    number = _int_in(low, high, what)

    def parse(text: str) -> list[int]:
        if text.strip().lower() == "none":
            return []
        values: set[int] = set()
        for part in text.split(","):
            first, sep, last = part.strip().partition("-")
            start = number(first)
            end = number(last) if sep else start
            if end < start:
                msg = f"{what} range {part!r} runs backwards"
                raise argparse.ArgumentTypeError(msg)
            values.update(range(start, end + 1))
        return sorted(values)

    parse.__name__ = f"{what} list"
    return parse


def parse_areas(text: str) -> list[int]:
    """Parse an area list such as ``1-10,12,20-22``."""
    return _int_list(0, 255, "area")(text)


def parse_targets(text: str) -> list[tuple[int, int]]:
    """Parse devices such as ``0xb3:42,0xb3:43``, or ``all``."""
    targets: list[tuple[int, int]] = []
    device_code, box = _int_in(0, 255, "device code"), _int_in(0, 255, "box")
    for part in text.split(","):
        item = part.strip()
        if item.lower() == "all":
            targets.append(ALL_DEVICES)
            continue
        device, sep, number = item.partition(":")
        if not sep:
            msg = f"expected DEVICE:BOX (e.g. 0xb3:42) or all, got {item!r}"
            raise argparse.ArgumentTypeError(msg)
        targets.append((device_code(device), box(number)))
    return targets


def parse_device(text: str) -> tuple[int, int]:
    """Parse one device such as ``0xb3:42`` (not ``all``)."""
    targets = parse_targets(text)
    if len(targets) != 1 or targets[0] == ALL_DEVICES:
        msg = f"expected one DEVICE:BOX (e.g. 0xb3:42), got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return targets[0]


def parse_physical_level(text: str) -> int:
    """Parse a device channel level: a percentage, ``on``, ``off`` or ``raw:N``."""
    value = text.strip().lower()
    if value == "off":
        return 0
    if value in {"on", "full"}:
        return PHYSICAL_LEVEL_FULL
    if value.startswith("raw:"):
        return _int_in(0, 0xFF, "raw level")(value[4:])
    try:
        percent = float(value.removesuffix("%"))
    except ValueError:
        percent = -1
    if not 0 <= percent <= 100:
        msg = f"expected 0-100%, on, off or raw:N, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return round(percent * PHYSICAL_LEVEL_FULL / 100)


def parse_selector(text: str) -> bytes:
    """Parse a two-byte memory selector such as ``0000``."""
    try:
        selector = bytes.fromhex(text)
    except ValueError:
        selector = b""
    if len(selector) != 2:
        msg = f"expected two bytes of hex (e.g. 0000), got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return selector


def parse_endpoint(text: str) -> tuple[str, int]:
    """Parse ``HOST:PORT``."""
    host, sep, port = text.rpartition(":")
    if not sep or not host:
        msg = f"expected HOST:PORT, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return host, _int_in(1, 65535, "port")(port)


AREA = _int_in(0, 255, "area")
CHANNEL = _int_in(1, 1024, "channel")
PRESET = _int_in(1, 2048, "preset")
JOIN = _int_in(0, 255, "join")


# Parser


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="dynet",
        description="Watch, control and capture a Signify Dynalite DyNet bus "
        "through a TCP gateway.",
        epilog="The gateway can also be set with DYNET_HOST and DYNET_PORT.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="more logging"
    )

    connection = argparse.ArgumentParser(add_help=False)
    group = connection.add_argument_group("gateway")
    group.add_argument("-H", "--host", default=os.environ.get("DYNET_HOST"))
    group.add_argument(
        "-p",
        "--port",
        type=_int_in(1, 65535, "port"),
        default=os.environ.get("DYNET_PORT"),
    )
    group.add_argument(
        "--send-interval",
        type=float,
        default=0.2,
        metavar="SECONDS",
        help="gap between packets sent (default 0.2)",
    )

    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    commands.add_parser(
        "check", parents=[connection], help="check the gateway accepts connections"
    )

    monitor = commands.add_parser(
        "monitor", parents=[connection], help="show bus traffic as it happens"
    )
    monitor.add_argument(
        "-a",
        "--area",
        type=parse_areas,
        action="extend",
        help="only these areas, e.g. 1-10,12",
    )
    monitor.add_argument("--json", action="store_true", help="one JSON object per line")
    monitor.add_argument(
        "--duration", type=float, metavar="SECONDS", help="stop after this long"
    )
    monitor.add_argument(
        "--record",
        metavar="FILE",
        help="also append a capture file",
    )

    preset = commands.add_parser("preset", parents=[connection], help="select a preset")
    preset.add_argument("area", type=AREA)
    preset.add_argument("preset", type=PRESET)
    preset.add_argument("-f", "--fade", type=parse_fade(MAX_PRESET_FADE), default=0.0)
    preset.add_argument("-j", "--join", type=JOIN, default=0xFF)

    level = commands.add_parser(
        "level", parents=[connection], help="set a channel level"
    )
    level.add_argument("area", type=AREA)
    level.add_argument("channel", type=CHANNEL)
    level.add_argument("level", type=parse_level, help="40, 40%%, off, on or raw:0x80")
    level.add_argument("-f", "--fade", type=parse_fade(MAX_CHANNEL_FADE), default=0.0)
    level.add_argument("-j", "--join", type=JOIN, default=0xFF)

    query = commands.add_parser("query", help="ask an area or channel for its state")
    what = query.add_subparsers(dest="what", required=True, metavar="WHAT")
    query_preset = what.add_parser(
        "preset", parents=[connection], help="current preset"
    )
    query_preset.add_argument("area", type=AREA)
    query_preset.add_argument(
        "-c", "--channel", type=_int_in(1, 256, "channel"), default=1
    )
    query_level = what.add_parser("level", parents=[connection], help="channel level")
    query_level.add_argument("area", type=AREA)
    query_level.add_argument("channel", type=_int_in(1, 256, "channel"))
    for sub in (query_preset, query_level):
        sub.add_argument("-t", "--timeout", type=float, default=2.0)
        sub.add_argument("--json", action="store_true")

    scan = commands.add_parser(
        "scan",
        parents=[connection],
        help="ask each area for its preset, to find the areas in use",
        description="Sends one request per area, so it adds traffic to the bus.",
    )
    scan.add_argument("--areas", type=parse_areas, default=list(range(1, 256)))
    scan.add_argument("-c", "--channel", type=_int_in(1, 256, "channel"), default=1)
    scan.add_argument("-t", "--timeout", type=float, default=0.5, help="wait per area")
    scan.add_argument("--json", action="store_true")

    probe = commands.add_parser(
        "probe",
        parents=[connection],
        help="send read-only requests and check that every reply decodes",
        description="Asks each area for its preset. For each area that answers, "
        "also asks for its channel levels, user preferences and preset levels. "
        "Shows every packet sent (>) and received (<), then a summary that lists "
        "anything that didn't decode. Only requests are sent, but they all add "
        "traffic to the bus.",
    )
    probe.add_argument("--areas", type=parse_areas, default=list(range(1, 256)))
    probe.add_argument(
        "--channels",
        type=_int_list(1, 256, "channel"),
        default=[1],
        help="channels to ask about in each area (default 1)",
    )
    probe.add_argument(
        "--preferences",
        type=_int_list(0, 255, "preference"),
        default=[0x0C],
        help="user preferences to ask for (default 0x0c, temperature)",
    )
    probe.add_argument(
        "--presets",
        type=_int_list(1, 256, "preset"),
        default=[1],
        help="presets whose channel levels to ask for (default 1)",
    )
    probe.add_argument(
        "--signon",
        type=parse_targets,
        action="extend",
        metavar="DEVICE:BOX,...",
        help="first ask these devices to sign on (physical 0x40, sub-opcode "
        "0x0a), e.g. 0xb3:42. 'all' sends one request to every device, which "
        "some sites ignore",
    )
    probe.add_argument(
        "--firmware",
        type=parse_targets,
        action="extend",
        metavar="DEVICE:BOX,...",
        help="first ask these devices for their firmware version (physical "
        "0x80); each answers with a Device Identify",
    )
    probe.add_argument(
        "--signon-wait",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="longest wait for each --signon reply (default 5)",
    )
    probe.add_argument(
        "-t", "--timeout", type=float, default=0.5, help="wait per request"
    )
    probe.add_argument("--record", metavar="FILE", help="also append a capture file")

    exercise = commands.add_parser(
        "exercise",
        parents=[connection],
        help="send one of each command to one area, to check the replies decode",
        description="Sends one of each harmless command aiodynet can build "
        "(preset selects, channel levels and fades, on/off, stop fade, area "
        "level up/down/off) to one area, then puts the area back on the "
        "preset it started on. It never programs or saves presets, disables "
        "panels, panics, writes user preferences or sends physical messages. "
        "The area's lights WILL change: pick one that is safe to play with. "
        "Without --yes it only prints what it would send.",
    )
    exercise.add_argument("area", type=AREA)
    exercise.add_argument("-c", "--channel", type=CHANNEL, default=1)
    exercise.add_argument(
        "-l",
        "--level",
        type=parse_level,
        default=Level.from_percent(50),
        help="level for the level commands (default 50%%)",
    )
    exercise.add_argument(
        "--pause",
        type=float,
        default=1.5,
        metavar="SECONDS",
        help="how long to listen after each command (default 1.5)",
    )
    exercise.add_argument("--yes", action="store_true", help="really send")
    exercise.add_argument("--record", metavar="FILE", help="also append a capture file")

    device_options = argparse.ArgumentParser(add_help=False, parents=[connection])
    device_options.add_argument("device", type=parse_device, help="DEVICE:BOX")
    device_options.add_argument(
        "--source",
        type=parse_device,
        default=SYSTEM_BUILDER,
        metavar="DEVICE:BOX",
        help="address to send DyNet 2 requests from (default 0xda:1, System Builder)",
    )
    device_options.add_argument(
        "-t", "--timeout", type=float, default=2.0, help="wait per request"
    )

    info = commands.add_parser(
        "info",
        parents=[device_options],
        help="ask one device for its versions, serial number and state",
        description="Read-only. Sends about seven requests to the device.",
    )
    info.add_argument("--json", action="store_true")

    lux = commands.add_parser(
        "lux",
        parents=[device_options],
        help="ask a sensor for its light level",
        description="Read-only: a DyNet 2 PE cell status poll.",
    )
    lux.add_argument("--json", action="store_true")

    memory = commands.add_parser(
        "memory",
        parents=[device_options],
        help="read bytes of a device's memory (EEPROM)",
        description="Read-only. Reads in chunks of at most 111 bytes.",
    )
    memory.add_argument("address", type=_int_in(0, 0xFFFF, "address"))
    memory.add_argument("length", type=_int_in(1, 0x10000, "length"))
    memory.add_argument(
        "--selector",
        type=parse_selector,
        default=b"\x00\x00",
        help="memory selector bytes (default 0000, the main device's EEPROM)",
    )
    memory.add_argument("--json", action="store_true")

    flash_cmd = commands.add_parser(
        "flash",
        parents=[device_options],
        help="make a device blink so you can find it (System Builder's flash)",
        description="Sends 'Silence. Listen to me' every 2 s, which makes the "
        "device blink three times and stop sending to the bus, then 'Go back "
        "to work' (always, even if interrupted) and a sign-on request to check "
        "it's back. A sensor misses motion while silenced.",
    )
    flash_cmd.add_argument(
        "-d",
        "--duration",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="how long to flash (default 10)",
    )
    flash_cmd.add_argument(
        "--interval", type=float, default=FLASH_INTERVAL, help=argparse.SUPPRESS
    )
    flash_cmd.add_argument(
        "--stop",
        action="store_true",
        help="only send 'Go back to work', for a device left silent",
    )

    area_cmd = commands.add_parser(
        "area",
        help="area commands: panels, panic, join level, RMask, bank, links",
        description="Commands System Builder sends to an area. Some change "
        "state devices only keep at run time; a run of them once left a panel "
        "sending nothing until it was reset (dynet reset). Without --yes each "
        "only prints what it would send.",
    )
    actions = area_cmd.add_subparsers(dest="action", required=True, metavar="ACTION")
    area_options = argparse.ArgumentParser(add_help=False, parents=[connection])
    area_options.add_argument("area", type=AREA)
    area_options.add_argument("-j", "--join", type=JOIN, default=0xFF)
    sends = argparse.ArgumentParser(add_help=False, parents=[area_options])
    sends.add_argument("--yes", action="store_true", help="really send")
    for name, text in SIMPLE_ACTIONS.items():
        actions.add_parser(name, parents=[sends], help=text)
    for name, text in FADE_ACTIONS.items():
        sub = actions.add_parser(name, parents=[sends], help=text)
        sub.add_argument("-f", "--fade", type=parse_fade(MAX_PRESET_FADE), default=0.0)
    for name, text in VALUE_ACTIONS.items():
        sub = actions.add_parser(name, parents=[sends], help=text)
        sub.add_argument("value", type=_int_in(0, 255, name))
    for name, text in SWITCH_ACTIONS.items():
        sub = actions.add_parser(name, parents=[sends], help=text)
        sub.add_argument("state", choices=["resume", "suspend"])
    for name, text in LINK_ACTIONS.items():
        sub = actions.add_parser(name, parents=[sends], help=text)
        sub.add_argument("bits", type=_int_in(0, 0xFFFFFF, "bits"))
    links = actions.add_parser(
        "links", parents=[area_options], help="ask for the area's link bits"
    )
    links.add_argument(
        "-t", "--timeout", type=float, default=1.0, help="how long to collect answers"
    )

    reset_cmd = commands.add_parser(
        "reset",
        parents=[device_options],
        help="restart a device, as System Builder does after a write",
        description="Sends 'Reset the device' (DyNet 2 command 0x00). The "
        "device restarts with its stored configuration, which undoes settings "
        "changed only at run time (for example a panel left unresponsive by "
        "area commands), then announces itself. Without --yes it only prints "
        "what it would send.",
    )
    reset_cmd.add_argument(
        "-w",
        "--wait",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="how long to wait for the device to come back (default 5)",
    )
    reset_cmd.add_argument("--yes", action="store_true", help="really send")

    device_level = commands.add_parser(
        "device-level",
        parents=[connection],
        help="set one output channel of a device (physical, not an area)",
        description="Physical opcode 0x92, as System Builder's 'flash channel' "
        "sends. It bypasses areas and presets, so the area's state won't "
        "reflect it.",
    )
    device_level.add_argument("device", type=parse_device, help="DEVICE:BOX")
    device_level.add_argument("channel", type=_int_in(0, 255, "channel"))
    device_level.add_argument(
        "level", type=parse_physical_level, help="40, 40%%, off, on or raw:0xfe"
    )

    dump = commands.add_parser(
        "dump",
        parents=[device_options],
        help="read a device's whole EEPROM, to a file or a compact hex dump",
        description="Read-only. Reads 111 bytes per request, the most one reply "
        "holds, retrying a chunk that gets no answer. A 32 KiB EEPROM takes "
        "about a minute, which is about as fast as a 9600-baud bus allows.",
    )
    dump.add_argument(
        "--start", type=_int_in(0, 0xFFFF, "start"), default=0, help="default 0x0000"
    )
    dump.add_argument(
        "--end",
        type=_int_in(1, 0x10000, "end"),
        help="one past the last address (default: the device type's EEPROM "
        "size where known, otherwise 0x8000, 32 KiB)",
    )
    dump.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="write the raw bytes to FILE instead of printing a hex dump",
    )
    dump.add_argument(
        "--retries", type=_int_in(0, 10, "retries"), default=3, help="per chunk"
    )
    dump.add_argument(
        "--selector",
        type=parse_selector,
        default=b"\x00\x00",
        help="memory selector bytes (default 0000, the main device's EEPROM)",
    )
    dump.set_defaults(timeout=1.0)

    send = commands.add_parser(
        "send",
        parents=[connection],
        help="send raw packets or DyNet 2 frames",
        description="Send raw DyNet 1 packets (8 bytes) or DyNet 2 frames "
        "(starting with ac), in order. The checksum is added if you leave it "
        "off.",
    )
    send.add_argument("packets", nargs="+", type=_raw_hex, metavar="HEX")

    decode_cmd = commands.add_parser(
        "decode",
        help="decode packets from a capture file or hex (no gateway needed)",
    )
    decode_cmd.add_argument(
        "file",
        nargs="?",
        default="-",
        help="default: stdin",
    )
    decode_cmd.add_argument("--json", action="store_true")
    decode_cmd.add_argument(
        "--summary", action="store_true", help="report counts and anything undecoded"
    )

    sniff_cmd = commands.add_parser(
        "sniff",
        help="passively capture another client's gateway traffic (Linux, root)",
        description="Records the traffic between a gateway and the client already\n"
        "connected to it, without connecting or sending anything.",
        epilog="Step-by-step guide:\n"
        "  https://github.com/sdunster/dynet-cli/blob/main/docs/capturing-traffic.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sniff_cmd.add_argument("gateway", type=parse_endpoint, metavar="HOST:PORT")
    sniff_cmd.add_argument(
        "-i", "--interface", default="eth0", help="network interface (default: eth0)"
    )
    sniff_cmd.add_argument("--duration", type=float, metavar="SECONDS")
    sniff_cmd.add_argument(
        "-o",
        "--output",
        default="-",
        help="file to append to (default: stdout)",
    )
    return parser


# Commands


def _client(args: argparse.Namespace) -> DynetClient:
    if not args.host or args.port is None:
        msg = "set the gateway with --host and --port (or DYNET_HOST and DYNET_PORT)"
        raise UsageError(msg)
    return DynetClient(args.host, int(args.port), send_interval=args.send_interval)


async def cmd_check(args: argparse.Namespace, out: TextIO) -> int:
    """Check the gateway is reachable."""
    client = _client(args)
    await check_connection(client.host, client.port)
    print(f"{client.host}:{client.port} is accepting connections", file=out)
    return EXIT_OK


def _timestamp(now: float) -> str:
    millis = int(now * 1000) % 1000
    return time.strftime("%H:%M:%S", time.localtime(now)) + f".{millis:03d}"


def _open_text(path: str, mode: str) -> contextlib.AbstractContextManager[TextIO]:
    """Open `path` as UTF-8 text, or stdin/stdout for ``-``."""
    if path == "-":
        return contextlib.nullcontext(sys.stdin if mode == "r" else sys.stdout)
    stream = Path(path).open(mode, encoding="utf-8")  # noqa: SIM115 - returned
    return cast("contextlib.AbstractContextManager[TextIO]", stream)


async def cmd_monitor(args: argparse.Namespace, out: TextIO) -> int:
    """Print bus traffic until interrupted or `--duration` passes."""
    with contextlib.ExitStack() as stack:
        recorder = (
            CaptureWriter(stack.enter_context(_open_text(args.record, "a")))
            if args.record
            else None
        )
        return await _monitor(args, out, recorder)


async def _monitor(
    args: argparse.Namespace, out: TextIO, recorder: CaptureWriter | None
) -> int:
    areas = set(args.area) if args.area else None

    def show(received: Received) -> None:
        now = time.time()
        if recorder is not None:
            recorder.packet(now, "rx", received.raw)
        message = received.message
        # Physical packets and DyNet 2 frames have no area, so a filter hides them.
        if areas is not None and message_area(message) not in areas:
            return
        raw = bytes(received.raw)
        _print_live(args, out, now, raw, describe(message), to_json(message))

    def show_noise(data: bytes) -> None:
        now = time.time()
        if recorder is not None:
            recorder.noise(now, "rx", data)
        if areas is None:  # noise has no area, so a filter hides it
            _print_live(args, out, now, data, describe_noise(data), noise_json(data))

    def connection(connected: bool) -> None:  # noqa: FBT001
        state = "reconnected" if connected else "connection lost, reconnecting"
        print(f"-- {state}", file=sys.stderr, flush=True)

    async with _client(args) as client:
        client.subscribe(show)
        client.subscribe_noise(show_noise)
        client.subscribe_connection(connection)
        if not args.json:
            print(
                f"-- watching {client.host}:{client.port}", file=sys.stderr, flush=True
            )
        if args.duration is None:  # pragma: no cover - runs until interrupted
            await asyncio.Event().wait()
        await asyncio.sleep(args.duration)
    return EXIT_OK


def _print_live(  # noqa: PLR0913, PLR0917
    args: argparse.Namespace,
    out: TextIO,
    now: float,
    data: bytes,
    text: str,
    fields: dict[str, Any],
) -> None:
    """Print one item as it arrives, as text or (with ``--json``) JSON."""
    if args.json:
        line = {"t": round(now, 3), "hex": data.hex()} | fields
        print(json.dumps(line), file=out, flush=True)
    else:
        print(f"{_timestamp(now)}  {data.hex(' ')}  {text}", file=out, flush=True)


async def cmd_preset(args: argparse.Namespace, out: TextIO) -> int:
    """Select a preset."""
    message = PresetSelect(
        area=args.area, preset=args.preset, fade=args.fade, join=args.join
    )
    async with _client(args) as client:
        await client.send(message)
    print(f"sent: {describe(message)}", file=out)
    return EXIT_OK


async def cmd_level(args: argparse.Namespace, out: TextIO) -> int:
    """Set a channel level."""
    message = SetChannelLevel(
        area=args.area,
        channel=args.channel,
        level=args.level,
        fade=args.fade,
        join=args.join,
    )
    async with _client(args) as client:
        await client.send(message)
    print(f"sent: {describe(message)}", file=out)
    return EXIT_OK


async def cmd_query(args: argparse.Namespace, out: TextIO) -> int:
    """Query an area's preset or a channel's level."""
    request = (
        RequestPreset(area=args.area, channel=args.channel)
        if args.what == "preset"
        else RequestChannelLevel(area=args.area, channel=args.channel)
    )
    async with _client(args) as client:
        try:
            report = await client.query(request, timeout=args.timeout)
        except TimeoutError:
            print(f"no answer within {args.timeout:g}s", file=sys.stderr)
            return EXIT_TIMEOUT
    print(json.dumps(to_json(report)) if args.json else describe(report), file=out)
    return EXIT_OK


async def cmd_scan(args: argparse.Namespace, out: TextIO) -> int:
    """Ask every area in a range for its preset."""
    found = 0
    print(
        f"-- asking {len(args.areas)} area(s); this sends one packet per area",
        file=sys.stderr,
        flush=True,
    )
    async with _client(args) as client:
        for area in args.areas:
            request = RequestPreset(area=area, channel=args.channel)
            try:
                report = await client.query(request, timeout=args.timeout)
            except TimeoutError:
                continue
            found += 1
            print(
                json.dumps(to_json(report)) if args.json else describe(report),
                file=out,
                flush=True,
            )
    print(f"-- {found} area(s) answered", file=sys.stderr)
    return EXIT_OK


def _print_now(out: TextIO) -> Callable[[str], None]:
    def print_line(line: str) -> None:
        print(line, file=out, flush=True)

    return print_line


async def cmd_probe(args: argparse.Namespace, out: TextIO) -> int:
    """Send read-only requests and summarise every reply."""
    per_area = len(
        area_requests(
            0,
            channels=args.channels,
            preferences=args.preferences,
            presets=args.presets,
        )
    )
    print(
        f"-- asking {len(args.areas)} area(s) for their preset, then {per_area} "
        "more request(s) to each area that answers",
        file=sys.stderr,
        flush=True,
    )
    with contextlib.ExitStack() as stack:
        recorder = (
            CaptureWriter(stack.enter_context(_open_text(args.record, "a")))
            if args.record
            else None
        )
        async with _client(args) as client:
            session = Session(client, _print_now(out), recorder)
            for device_code, box in args.firmware or ():
                await session.run(ask_firmware(device_code, box), args.timeout)
            for device_code, box in args.signon or ():
                await session.run(ask_signon(device_code, box), args.signon_wait)
            areas = 0
            for area in args.areas:
                if not await session.run(ask_preset(area), args.timeout):
                    continue
                areas += 1
                for step in area_requests(
                    area,
                    channels=args.channels,
                    preferences=args.preferences,
                    presets=args.presets,
                ):
                    await session.run(step, args.timeout)
            session.close()
    print(
        f"-- {areas} area(s) answered; sent {session.sent} request(s), "
        f"{session.answered} answered",
        file=out,
    )
    print(session.summary.render(), file=out)
    return EXIT_OK


async def cmd_exercise(args: argparse.Namespace, out: TextIO) -> int:
    """Send one of each harmless command to one area."""
    steps = exercise_steps(args.area, args.channel, args.level)
    if not args.yes:
        print(f"Would send to area {args.area}, {args.pause:g}s apart:", file=out)
        for step in steps:
            print(f"  {step.packet.hex()}  {describe(decode(step.packet))}", file=out)
        print(
            "then restore the area's starting preset. "
            "Nothing was sent; add --yes to send.",
            file=out,
        )
        return EXIT_OK
    with contextlib.ExitStack() as stack:
        recorder = (
            CaptureWriter(stack.enter_context(_open_text(args.record, "a")))
            if args.record
            else None
        )
        async with _client(args) as client:
            session = Session(client, _print_now(out), recorder)
            start = await session.run(ask_preset(args.area), 2.0)
            for step in steps:
                await session.run(step, args.pause)
            if isinstance(start, PresetReport):
                await session.run(
                    Step(PresetSelect(area=args.area, preset=start.preset)), args.pause
                )
            session.close()
    if not isinstance(start, PresetReport):
        print(
            f"-- area {args.area} didn't say which preset it was on, "
            "so it wasn't put back",
            file=sys.stderr,
        )
    print(f"-- sent {session.sent} packet(s)", file=out)
    print(session.summary.render(), file=out)
    return EXIT_OK


async def cmd_info(args: argparse.Namespace, out: TextIO) -> int:
    """Interrogate one device."""
    async with _client(args) as client:
        info = await device_info(client, args.device, args.source, args.timeout)
    if all(value is None for value in info.values()):
        print("no answer", file=sys.stderr)
        return EXIT_TIMEOUT
    print(json.dumps(info) if args.json else render_info(args.device, info), file=out)
    return EXIT_OK


async def cmd_lux(args: argparse.Namespace, out: TextIO) -> int:
    """Ask a sensor for its light level."""
    async with _client(args) as client:
        try:
            lux = await light_level(client, args.device, args.source, args.timeout)
        except TimeoutError:
            print(f"no answer within {args.timeout:g}s", file=sys.stderr)
            return EXIT_TIMEOUT
    print(json.dumps({"lux": lux}) if args.json else f"{lux} lux", file=out)
    return EXIT_OK


async def cmd_memory(args: argparse.Namespace, out: TextIO) -> int:
    """Read a device's memory."""
    if args.address + args.length > 0x10000:
        msg = "the read runs past address 0xffff"
        raise UsageError(msg)
    async with _client(args) as client:
        data = await read_memory(
            client,
            args.device,
            args.source,
            args.address,
            args.length,
            selector=args.selector,
            timeout=args.timeout,
        )
    if args.json:
        print(json.dumps({"address": args.address, "data": data.hex()}), file=out)
    else:
        print(hex_dump(args.address, data), file=out)
    return EXIT_OK


async def cmd_flash(args: argparse.Namespace, out: TextIO) -> int:
    """Flash a device, then put it back to work."""

    def sent(message: Dynet2DeviceRequest) -> None:
        print(f"sent: {describe(message)}", file=out, flush=True)

    async with _client(args) as client:
        if args.stop:
            await go_back_to_work(client, args.device, args.source, sent=sent)
        else:
            count = max(1, round(args.duration / args.interval))
            await flash(
                client,
                args.device,
                args.source,
                count=count,
                interval=args.interval,
                sent=sent,
            )
        signon = device_request(args.device, args.source, Dynet2Command.SIGNON)
        try:
            reply = await client.query(signon, timeout=args.timeout)
        except TimeoutError:
            print(f"no sign-on within {args.timeout:g}s", file=sys.stderr)
            return EXIT_TIMEOUT
    print(f"back: {describe(reply)}", file=out)
    return EXIT_OK


async def cmd_area(args: argparse.Namespace, out: TextIO) -> int:
    """Send an area command, or ask an area for its link bits."""
    if args.action == "links":
        async with _client(args) as client:
            reports = await read_links(client, args.area, args.join, args.timeout)
        if not reports:
            print(f"no answer within {args.timeout:g}s", file=sys.stderr)
            return EXIT_TIMEOUT
        for report in reports:
            print(describe(report), file=out)
        return EXIT_OK
    message = build_message(args)
    packet = message.to_packet()
    if not args.yes:
        print(
            f"Would send: {packet.hex()}  {describe(message)}\n"
            "Nothing was sent; add --yes to send.",
            file=out,
        )
        return EXIT_OK
    async with _client(args) as client:
        await client.send(packet)
    print(f"sent: {describe(message)}", file=out)
    return EXIT_OK


async def cmd_reset(args: argparse.Namespace, out: TextIO) -> int:
    """Restart a device and wait for it to come back."""
    request = device_request(args.device, args.source, Dynet2Command.RESET)
    if not args.yes:
        print(
            f"Would send: {request.to_frame().hex()}  {describe(request)}\n"
            "Nothing was sent; add --yes to send.",
            file=out,
        )
        return EXIT_OK
    async with _client(args) as client:
        identify = await reset(client, args.device, args.source, timeout=args.wait)
    print(f"sent: {describe(request)}", file=out)
    print(f"back: {describe(identify)}", file=out)
    return EXIT_OK


async def cmd_device_level(args: argparse.Namespace, out: TextIO) -> int:
    """Set one output channel of a device."""
    device_code, box = args.device
    message = SetPhysicalChannelLevel(
        device_code=device_code, box=box, channel=args.channel, level=args.level
    )
    async with _client(args) as client:
        await client.send(message)
    print(f"sent: {describe(message)}", file=out)
    return EXIT_OK


async def cmd_dump(args: argparse.Namespace, out: TextIO) -> int:
    """Read a device's whole EEPROM."""
    if args.end is None:
        size = eeprom_size(args.device[0], args.selector)
        if size is None:
            size = DEFAULT_EEPROM_SIZE
            print(
                f"-- the size of device 0x{args.device[0]:02x}'s memory isn't "
                f"known; reading to 0x{size:04x} (set --end to read more)",
                file=sys.stderr,
            )
        args.end = size
    if args.end <= args.start:
        msg = "--end must be after --start"
        raise UsageError(msg)
    length = args.end - args.start
    shown = 0

    def progress(done: int) -> None:
        nonlocal shown
        percent = done * 100 // length
        if percent >= shown + 10 or done == length:
            shown = percent
            print(f"-- {done}/{length} bytes", file=sys.stderr, flush=True)

    started = time.monotonic()
    async with _client(args) as client:
        data = await read_memory(
            client,
            args.device,
            args.source,
            args.start,
            length,
            selector=args.selector,
            timeout=args.timeout,
            retries=args.retries,
            progress=progress,
            stop_at_end=True,
        )
    elapsed = time.monotonic() - started
    if args.output:
        await asyncio.to_thread(Path(args.output).write_bytes, data)
    else:
        print(compact_hex_dump(args.start, data), file=out)
    found = data_ranges(args.start, data, merge_gap=MERGE_GAP)
    ranges = ", ".join(f"0x{a:04x}-0x{b:04x}" for a, b in found)
    ended = (
        f"; memory ends at 0x{args.start + len(data):04x}" if len(data) < length else ""
    )
    print(
        f"-- read {len(data)} bytes from {device_name(args.device)} "
        f"in {elapsed:.1f}s; data in {ranges or 'none (all blank)'}{ended}",
        file=out,
    )
    return EXIT_OK


def _raw_hex(text: str) -> Packet | Dynet2Frame:
    """Parse a DyNet 1 packet or, if it starts with 0xAC, a DyNet 2 frame."""
    try:
        if text.strip().lower().startswith("ac"):
            return Dynet2Frame.from_hex(text)
        return Packet.from_hex(text)
    except InvalidPacketError as err:
        raise argparse.ArgumentTypeError(str(err)) from err


async def cmd_send(args: argparse.Namespace, out: TextIO) -> int:
    """Send raw packets and DyNet 2 frames."""
    async with _client(args) as client:
        for packet in args.packets:
            await client.send(packet)
            print(f"sent {packet.hex()}  {describe(decode(packet))}", file=out)
    return EXIT_OK


def cmd_decode(args: argparse.Namespace, out: TextIO) -> int:
    """Decode a capture file or hex."""
    summary = Summary()
    with _open_text(args.file, "r") as lines:
        for record in read_records(lines):
            if args.summary:
                summary.add(record)
            elif isinstance(record, PacketRecord):
                _print_record(record, out, as_json=args.json)
            else:
                details = " ".join(f"{k}={v}" for k, v in record.details.items())
                print(f"-- {record.event} {details}".rstrip(), file=out)
    if args.summary:
        print(summary.render(), file=out)
    return EXIT_OK


def _print_record(record: PacketRecord, out: TextIO, *, as_json: bool) -> None:
    raw = record.raw
    message = decode(raw)
    if as_json:
        line = {"hex": raw.hex("")} | to_json(message)
        if record.time is not None:
            line = {"t": record.time, "dir": record.direction} | line
        print(json.dumps(line), file=out)
        return
    prefix = f"{_timestamp(record.time)}  " if record.time is not None else ""
    direction = f"{record.direction}  " if record.direction else ""
    print(f"{prefix}{direction}{raw.hex()}  {describe(message)}", file=out)


def cmd_sniff(args: argparse.Namespace) -> int:
    """Capture another client's gateway traffic."""
    host, port = args.gateway
    with _open_text(args.output, "a") as output:
        return _sniff(args, host, port, CaptureWriter(output))


def _sniff(
    args: argparse.Namespace, host: str, port: int, writer: CaptureWriter
) -> int:
    reassembler = Reassembler(host, port, writer)
    sock = open_raw_socket(args.interface)
    print(
        f"-- capturing {host}:{port} on {args.interface}"
        + (f" for {args.duration:g}s" if args.duration else ""),
        file=sys.stderr,
        flush=True,
    )
    try:
        sniff(sock, reassembler, duration=args.duration)
    finally:
        sock.close()
        print(
            f"-- captured {reassembler.packets} packet(s) and "
            f"{reassembler.frames} DyNet 2 frame(s)",
            file=sys.stderr,
        )
    return EXIT_OK


ASYNC_COMMANDS = {
    "check": cmd_check,
    "monitor": cmd_monitor,
    "preset": cmd_preset,
    "level": cmd_level,
    "query": cmd_query,
    "scan": cmd_scan,
    "probe": cmd_probe,
    "exercise": cmd_exercise,
    "send": cmd_send,
    "info": cmd_info,
    "lux": cmd_lux,
    "memory": cmd_memory,
    "flash": cmd_flash,
    "device-level": cmd_device_level,
    "reset": cmd_reset,
    "area": cmd_area,
    "dump": cmd_dump,
}


async def run(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the command line `argv` and return the exit code."""
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "decode":
            return cmd_decode(args, out)
        if args.command == "sniff":
            return cmd_sniff(args)
        return await ASYNC_COMMANDS[args.command](args, out)
    except UsageError as err:
        print(f"dynet: error: {err}", file=sys.stderr)
        return EXIT_USAGE
    except NoReplyError as err:
        print(f"dynet: {err}", file=sys.stderr)
        return EXIT_TIMEOUT
    except (DynetError, CaptureFormatError, OSError) as err:
        print(f"dynet: {err}", file=sys.stderr)
        return EXIT_ERROR


def main() -> None:  # pragma: no cover - thin wrapper over run()
    """Entry point for the ``dynet`` script."""
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(asyncio.run(run()))
    sys.exit(130)
