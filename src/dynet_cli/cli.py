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
    DynetClient,
    DynetError,
    Level,
    Packet,
    PresetReport,
    PresetSelect,
    Received,
    RequestChannelLevel,
    RequestPreset,
    SetChannelLevel,
    check_connection,
    decode,
)

from . import __version__
from .capture import (
    CaptureFormatError,
    CaptureWriter,
    PacketRecord,
    Summary,
    read_records,
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

    send = commands.add_parser("send", parents=[connection], help="send raw packets")
    send.add_argument("packets", nargs="+", type=Packet.from_hex, metavar="HEX")

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


async def cmd_send(args: argparse.Namespace, out: TextIO) -> int:
    """Send raw packets."""
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
    except (DynetError, CaptureFormatError, OSError) as err:
        print(f"dynet: {err}", file=sys.stderr)
        return EXIT_ERROR


def main() -> None:  # pragma: no cover - thin wrapper over run()
    """Entry point for the ``dynet`` script."""
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(asyncio.run(run()))
    sys.exit(130)
