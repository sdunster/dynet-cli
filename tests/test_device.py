"""The commands that talk to one device: info, lux and memory."""

import asyncio
import io
import json
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import TypedDict

import pytest
from aiodynet import (
    DeviceIdentify,
    DeviceStatusReport,
    Dynet2BuildNumberReply,
    Dynet2Command,
    Dynet2DeviceRequest,
    Dynet2Frame,
    Dynet2MemoryData,
    Dynet2MemoryErrorReply,
    Dynet2Message,
    Dynet2PcbVersionReply,
    Dynet2ReadMemory,
    Dynet2RequestSerialNumber,
    Dynet2SerialNumberReply,
    Dynet2SignonReply,
    Dynet2StatusRequest,
    DynetClient,
    Message,
    Packet,
    PhysicalStateReport,
    SetPhysicalChannelLevel,
    decode,
)
from aiodynet.testing import FakeGateway

from dynet_cli.cli import EXIT_ERROR, EXIT_OK, EXIT_TIMEOUT, EXIT_USAGE, run
from dynet_cli.device import (
    compact_hex_dump,
    data_ranges,
    eeprom_size,
    flash,
    render_info,
)

# A fake EEPROM for the kitchen sensor (0xb3 box 29): byte n holds n & 0xff.
MEMORY = bytes(range(256)) * 4


class Device(TypedDict):
    device_code: int
    box: int


class Target(TypedDict):
    target_device_code: int
    target_box: int


KITCHEN: Device = {"device_code": 0xB3, "box": 29}
TO_SB: Target = {"target_device_code": 0xDA, "target_box": 1}

# What the kitchen D5Sen answered to each command on 2026-11-27.
ANSWERS: dict[int, Dynet2Message] = {
    Dynet2Command.SIGNON: Dynet2SignonReply(
        **KITCHEN,
        firmware_major=2,
        firmware_minor=0x11,
        max_rx_size=128,
        max_tx_size=128,
    ),
    Dynet2Command.BOOTLOADER_SIGNON: Dynet2SignonReply(
        **KITCHEN,
        firmware_major=0x75,
        firmware_minor=5,
        max_rx_size=128,
        max_tx_size=128,
        bootloader=True,
    ),
    Dynet2Command.FIRMWARE_BUILD: Dynet2BuildNumberReply(
        **KITCHEN, **TO_SB, bootloader=False, build=1
    ),
    Dynet2Command.BOOTLOADER_BUILD: Dynet2BuildNumberReply(
        **KITCHEN, **TO_SB, bootloader=True, build=3
    ),
    Dynet2Command.PCB_VERSION: Dynet2PcbVersionReply(
        **KITCHEN, **TO_SB, version=1, revision=2
    ),
}


def kitchen_frame_replies(frame: Dynet2Frame) -> Iterable[Message | Dynet2Message]:
    """The kitchen D5Sen; nothing answers for any other box."""
    match decode(frame):
        case Dynet2DeviceRequest(box=29, command=command) if command in ANSWERS:
            return [ANSWERS[command]]
        case Dynet2RequestSerialNumber(box=29):
            return [Dynet2SerialNumberReply(**KITCHEN, **TO_SB, serial_number=50517129)]
        case Dynet2StatusRequest(box=29):
            return [PhysicalStateReport(**KITCHEN, state_type=1, value=15)]
        case Dynet2ReadMemory(box=29, address=address, length=length):
            data = MEMORY[address : address + length]
            if address >= 0x300:  # a stretch that answers short
                data = data[:-1]
            return [Dynet2MemoryData(**KITCHEN, **TO_SB, address=address, data=data)]
    return []


def sources(gateway: FakeGateway) -> set[int]:
    """The source device codes of the DyNet 2 requests the gateway received."""
    return {
        getattr(decode(frame), "source_device_code", -1)
        for frame in gateway.received_frames
    }


def reads(gateway: FakeGateway) -> list[tuple[int, int]]:
    return [
        (message.address, message.length)
        for frame in gateway.received_frames
        if isinstance(message := decode(frame), Dynet2ReadMemory)
    ]


def kitchen_replies(packet: Packet) -> Iterable[Message]:
    if packet == Packet.from_hex("5cb31d40000016"):
        return [
            DeviceStatusReport(
                device_code=0xB3,
                box=29,
                sub_opcode=0x16,
                data1_raw=0x55,
                data2_raw=0xFF,
            )
        ]
    return []


@pytest.fixture
async def gateway() -> AsyncIterator[FakeGateway]:
    async with FakeGateway(
        replies=kitchen_replies, frame_replies=kitchen_frame_replies
    ) as gateway:
        yield gateway


async def dynet(gateway: FakeGateway, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    target = [
        "--host",
        gateway.host,
        "--port",
        str(gateway.port),
        "--send-interval",
        "0",
    ]
    code = await run([args[0], *target, *args[1:]], out)
    return code, out.getvalue()


async def test_info(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "info", "0xb3:29")
    assert code == EXIT_OK
    assert out == (
        "device 0xb3 box 29\n"
        "  firmware         02.11\n"
        "  firmware build   1\n"
        "  bootloader       75.05\n"
        "  bootloader build 3\n"
        "  PCB version      1.2\n"
        "  serial number    50517129\n"
        "  running          application\n"
        "  max rx packet    128 bytes\n"
        "  max tx packet    128 bytes\n"
    )
    # Every request came from System Builder's address.
    assert sources(gateway) == {0xDA}


async def test_info_json_with_missing_answers(gateway: FakeGateway) -> None:
    code, out = await dynet(
        gateway, "info", "0xb3:42", "--json", "--timeout", "0.05", "--source", "0x01:2"
    )
    assert code == EXIT_TIMEOUT
    assert out == ""
    # The requests carry the chosen source address.
    assert sources(gateway) == {1}


async def test_info_json(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "info", "0xb3:29", "--json")
    assert code == EXIT_OK
    assert json.loads(out) == {
        "firmware": "02.11",
        "firmware_build": 1,
        "bootloader": "75.05",
        "bootloader_build": 3,
        "pcb_version": "1.2",
        "serial_number": 50517129,
        "application_running": True,
        "max_rx_size": 128,
        "max_tx_size": 128,
    }


def test_render_info_shows_gaps() -> None:
    info = dict.fromkeys(
        [
            "firmware",
            "firmware_build",
            "bootloader",
            "bootloader_build",
            "pcb_version",
            "serial_number",
            "application_running",
            "max_rx_size",
            "max_tx_size",
        ]
    )
    info["application_running"] = False
    text = render_info((0xB3, 1), info)
    assert "  firmware         no answer" in text
    assert "  running          bootloader" in text


async def test_lux(gateway: FakeGateway) -> None:
    assert await dynet(gateway, "lux", "0xb3:29") == (EXIT_OK, "15 lux\n")
    assert await dynet(gateway, "lux", "0xb3:29", "--json") == (
        EXIT_OK,
        '{"lux": 15}\n',
    )


async def test_lux_timeout(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "lux", "0xb3:42", "--timeout", "0.05")
    assert (code, out) == (EXIT_TIMEOUT, "")


async def test_memory_reads_in_chunks(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "memory", "0xb3:29", "0x10", "130")
    assert code == EXIT_OK
    lines = out.splitlines()
    assert lines[0] == "0x0010  " + bytes(range(0x10, 0x20)).hex(" ")
    assert lines[-1] == "0x0090  " + bytes((0x90, 0x91)).hex(" ")
    assert reads(gateway) == [(0x10, 111), (0x7F, 19)]


async def test_memory_json_and_selector(gateway: FakeGateway) -> None:
    code, out = await dynet(
        gateway, "memory", "0xb3:29", "0x1a", "5", "--json", "--selector", "0000"
    )
    assert code == EXIT_OK
    assert json.loads(out) == {"address": 0x1A, "data": MEMORY[0x1A:0x1F].hex()}


async def test_memory_short_answer(gateway: FakeGateway) -> None:
    code, _ = await dynet(gateway, "memory", "0xb3:29", "0x300", "4")
    assert code == EXIT_ERROR


async def test_memory_no_answer(gateway: FakeGateway) -> None:
    code, _ = await dynet(gateway, "memory", "0xb3:42", "0", "4", "--timeout", "0.05")
    assert code == EXIT_TIMEOUT


async def test_memory_past_the_end(gateway: FakeGateway) -> None:
    code, _ = await dynet(gateway, "memory", "0xb3:29", "0xffff", "2")
    assert code == EXIT_USAGE


@pytest.mark.parametrize(
    "args",
    [
        ["info", "all"],
        ["info", "0xb3:1,0xb3:2"],
        ["memory", "0xb3:29", "0", "1", "--selector", "00"],
        ["memory", "0xb3:29", "0", "1", "--selector", "zz"],
    ],
)
async def test_bad_arguments(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        await run(args, io.StringIO())
    assert exit_info.value.code == EXIT_USAGE


def commands(gateway: FakeGateway) -> list[int]:
    """The DyNet 2 device commands the gateway received, in order."""
    return [
        message.command
        for frame in gateway.received_frames
        if isinstance(message := decode(frame), Dynet2DeviceRequest)
    ]


async def test_flash(gateway: FakeGateway) -> None:
    code, out = await dynet(
        gateway, "flash", "0xb3:29", "--duration", "0.03", "--interval", "0.01"
    )
    assert code == EXIT_OK
    assert commands(gateway) == [
        Dynet2Command.SILENCE,
        Dynet2Command.SILENCE,
        Dynet2Command.SILENCE,
        Dynet2Command.GO_BACK_TO_WORK,
        Dynet2Command.SIGNON,
    ]
    lines = out.splitlines()
    assert lines[0] == (
        "sent: device 0xb3 box 29: silence, listen to me (flash) "
        "(from device 0xda box 1)"
    )
    assert lines[3] == (
        "sent: device 0xb3 box 29: go back to work (from device 0xda box 1)"
    )
    assert lines[4].startswith("back: device 0xb3 box 29: signed on")


async def test_flash_stop(gateway: FakeGateway) -> None:
    code, _ = await dynet(gateway, "flash", "0xb3:29", "--stop")
    assert code == EXIT_OK
    assert commands(gateway) == [Dynet2Command.GO_BACK_TO_WORK, Dynet2Command.SIGNON]


async def test_flash_without_a_sign_on(gateway: FakeGateway) -> None:
    code, _ = await dynet(gateway, "flash", "0xb3:42", "--stop", "--timeout", "0.05")
    assert code == EXIT_TIMEOUT


async def test_an_interrupted_flash_still_goes_back_to_work(
    gateway: FakeGateway,
) -> None:
    sent: list[Dynet2DeviceRequest] = []
    async with DynetClient(gateway.host, gateway.port, send_interval=0) as client:
        task = asyncio.create_task(
            flash(
                client, (0xB3, 29), (0xDA, 1), count=100, interval=10, sent=sent.append
            )
        )
        async with asyncio.timeout(2):
            while not sent:  # noqa: ASYNC110 - polling test state
                await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert [message.command for message in sent] == [
        Dynet2Command.SILENCE,
        Dynet2Command.GO_BACK_TO_WORK,
    ]


@pytest.mark.parametrize(
    ("level", "raw", "text"),
    [
        ("100", 0xFE, "set 100%"),
        ("on", 0xFE, "set 100%"),
        ("50%", 0x7F, "set 50%"),
        ("off", 0, "set 0%"),
        ("raw:0x10", 0x10, "set 6%"),
    ],
)
async def test_device_level(
    gateway: FakeGateway, level: str, raw: int, text: str
) -> None:
    code, out = await dynet(gateway, "device-level", "0x4c:5", "3", level)
    assert code == EXIT_OK
    assert out == f"sent: device 0x4c box 5: channel 3: {text}\n"
    packet = await gateway.next_packet()
    assert (
        packet
        == SetPhysicalChannelLevel(
            device_code=0x4C, box=5, channel=3, level=raw
        ).to_packet()
    )


@pytest.mark.parametrize("level", ["101", "-1", "half", "raw:0x100"])
async def test_bad_device_levels(level: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        await run(["device-level", "0x4c:5", "3", level], io.StringIO())
    assert exit_info.value.code == EXIT_USAGE


def resets_and_comes_back(frame: Dynet2Frame) -> Iterable[Message | Dynet2Message]:
    match decode(frame):
        case Dynet2DeviceRequest(box=box, command=Dynet2Command.RESET):
            return [
                DeviceIdentify(
                    device_code=0xFD,
                    box=box + 1,
                    firmware_major=3,
                    firmware_minor=0x23,
                    boot_raw=0x80,
                ),
                DeviceIdentify(
                    device_code=0xFD, box=box, firmware_major=3, firmware_minor=0x23
                ),
                DeviceIdentify(
                    device_code=0xFD,
                    box=box,
                    firmware_major=3,
                    firmware_minor=0x23,
                    boot_raw=0x80,
                ),
            ]
    return []


async def test_reset_needs_yes(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "reset", "0xfd:51")
    assert code == EXIT_OK
    assert out == (
        "Would send: ac 02 80 da 00 01 fd 00 33 00 1f 39  device 0xfd box 51: reset "
        "(from device 0xda box 1)\nNothing was sent; add --yes to send.\n"
    )
    assert gateway.received_frames == []


async def test_reset_waits_for_the_device() -> None:
    async with FakeGateway(frame_replies=resets_and_comes_back) as gateway:
        code, out = await dynet(gateway, "reset", "0xfd:51", "--yes")
    assert code == EXIT_OK
    assert out.splitlines() == [
        "sent: device 0xfd box 51: reset (from device 0xda box 1)",
        "back: device 0xfd box 51: identify, firmware 03.23, after a reset",
    ]


async def test_reset_without_a_reply(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "reset", "0xfd:51", "--yes", "--wait", "0.05")
    assert (code, out) == (EXIT_TIMEOUT, "")


# A panel's EEPROM for dump: data, a blank stretch, then more data.
PANEL = (bytes(range(1, 0x41)) + b"\xff" * 0x100 + b"\x10\x20").ljust(0x800, b"\xff")


class FlakyPanel:
    """Answers memory reads for 0xfd box 51, ignoring the first ask at 0x6f."""

    def __init__(self) -> None:
        self.dropped = False

    def __call__(self, frame: Dynet2Frame) -> Iterable[Message | Dynet2Message]:
        match decode(frame):
            case Dynet2ReadMemory(box=51, address=address, length=length):
                if address == 0x6F and not self.dropped:
                    self.dropped = True
                    return []
                return [
                    Dynet2MemoryData(
                        device_code=0xFD,
                        box=51,
                        **TO_SB,
                        address=address,
                        data=PANEL[address : address + length],
                    )
                ]
        return []


async def test_dump_hex_with_a_retry(capsys: pytest.CaptureFixture[str]) -> None:
    panel = FlakyPanel()
    async with FakeGateway(frame_replies=panel) as gateway:
        code, out = await dynet(
            gateway, "dump", "0xfd:51", "--end", "0x180", "--timeout", "0.1"
        )
    assert code == EXIT_OK
    assert panel.dropped
    lines = out.splitlines()
    assert lines[0] == "0x0000  " + bytes(range(1, 17)).hex(" ")
    assert lines[4] == "0x0040  ff … (blank to 0x013f, 256 bytes)"
    assert lines[5].startswith("0x0140  10 20 ff")
    assert lines[6] == "0x0150  ff … (blank to 0x017f, 48 bytes)"
    assert lines[7].startswith("-- read 384 bytes from device 0xfd box 51 in ")
    assert lines[7].endswith("s; data in 0x0000-0x003f, 0x0140-0x0141")
    err = capsys.readouterr().err
    assert "-- 384/384 bytes" in err


async def test_dump_to_a_file(tmp_path: Path) -> None:
    target = tmp_path / "panel.bin"
    async with FakeGateway(frame_replies=FlakyPanel()) as gateway:
        code, out = await dynet(
            gateway,
            "dump",
            "0xfd:51",
            "--start",
            "0x40",
            "--end",
            "0x800",
            "--output",
            str(target),
        )
    assert code == EXIT_OK
    assert target.read_bytes() == PANEL[0x40:0x800]
    assert out.endswith("data in 0x0140-0x0141\n")


async def test_dump_of_blank_memory() -> None:
    async with FakeGateway(frame_replies=FlakyPanel()) as gateway:
        code, out = await dynet(
            gateway, "dump", "0xfd:51", "--start", "0x400", "--end", "0x420"
        )
    assert code == EXIT_OK
    assert out.endswith("data in none (all blank)\n")


async def test_dump_gives_up_after_retries(gateway: FakeGateway) -> None:
    code, _ = await dynet(
        gateway,
        "dump",
        "0xfd:99",
        "--end",
        "0x10",
        "--timeout",
        "0.02",
        "--retries",
        "1",
    )
    assert code == EXIT_TIMEOUT


def blank_memory(frame: Dynet2Frame) -> Iterable[Message | Dynet2Message]:
    """Answer any device's memory read with blank bytes."""
    match decode(frame):
        case Dynet2ReadMemory(device_code=code, box=box, address=address, length=n):
            return [
                Dynet2MemoryData(
                    device_code=code,
                    box=box,
                    **TO_SB,
                    address=address,
                    data=b"\xff" * n,
                )
            ]
    return []


@pytest.mark.parametrize(
    ("device", "start", "end"),
    [
        ("0xb3:42", "0x7fe0", "0x7fff"),  # a 32 KiB sensor
        ("0xfd:51", "0xffe0", "0xffff"),  # a 64 KiB panel
    ],
)
async def test_dump_reads_to_the_end_of_a_known_device(
    device: str, start: str, end: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with FakeGateway(frame_replies=blank_memory) as gateway:
        code, out = await dynet(gateway, "dump", device, "--start", start)
    assert code == EXIT_OK
    assert out.splitlines()[0] == f"{start}  ff … (blank to {end}, 32 bytes)"
    assert "isn't known" not in capsys.readouterr().err


async def test_dump_of_an_unknown_device_reads_32_kib(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with FakeGateway(frame_replies=blank_memory) as gateway:
        code, out = await dynet(gateway, "dump", "0x99:1", "--start", "0x7ff0")
    assert code == EXIT_OK
    assert out.splitlines()[0] == "0x7ff0  ff … (blank to 0x7fff, 16 bytes)"
    assert (
        "-- the size of device 0x99's memory isn't known; reading to 0x8000 "
        "(set --end to read more)"
    ) in capsys.readouterr().err


class ShortMemory:
    """A device whose memory ends at `end`, like a real one.

    It refuses a read that starts at or past the end, and pads a read that
    runs past it with 0xFF.
    """

    def __init__(self, end: int) -> None:
        self.end = end
        self.memory = bytes(i % 0xFE for i in range(end))

    def __call__(self, frame: Dynet2Frame) -> Iterable[Message | Dynet2Message]:
        match decode(frame):
            case Dynet2ReadMemory(device_code=code, box=box, address=address, length=n):
                if address >= self.end:
                    return [
                        Dynet2MemoryErrorReply(
                            device_code=code,
                            box=box,
                            **TO_SB,
                            address=address,
                            error_code=19,
                        )
                    ]
                data = self.memory[address : address + n].ljust(n, b"\xff")
                return [
                    Dynet2MemoryData(
                        device_code=code, box=box, **TO_SB, address=address, data=data
                    )
                ]
        return []


@pytest.mark.parametrize("end", [0x150, 2 * 111, 111 + 1, 1])
async def test_dump_stops_at_the_end_of_memory(end: int, tmp_path: Path) -> None:
    target = tmp_path / "short.bin"
    device = ShortMemory(end)
    async with FakeGateway(frame_replies=device) as gateway:
        code, out = await dynet(
            gateway, "dump", "0x99:1", "--end", "0x400", "-o", str(target)
        )
    assert code == EXIT_OK
    assert target.read_bytes() == device.memory
    assert out.startswith(f"-- read {end} bytes from device 0x99 box 1 in ")
    assert out.endswith(f"; memory ends at 0x{end:04x}\n")


async def test_dump_that_starts_past_the_end() -> None:
    async with FakeGateway(frame_replies=ShortMemory(0x150)) as gateway:
        code, _ = await dynet(gateway, "dump", "0x99:1", "--start", "0x200")
    assert code == EXIT_ERROR


async def test_memory_refused_by_the_device(capsys: pytest.CaptureFixture[str]) -> None:
    async with FakeGateway(frame_replies=ShortMemory(0x150)) as gateway:
        code, _ = await dynet(gateway, "memory", "0x99:1", "0x150", "4")
    assert code == EXIT_ERROR
    assert (
        "device 0x99 box 1 refused a read at 0x0150: error 19 (memory does not exist)"
    ) in capsys.readouterr().err


def test_eeprom_sizes() -> None:
    assert eeprom_size(0xB3) == 0x8000
    assert eeprom_size(0xFD) == 0x10000
    assert eeprom_size(0x99) is None
    assert eeprom_size(0xFD, b"\x02\x00") is None


async def test_dump_range_must_not_be_empty() -> None:
    code = await run(
        ["dump", "0xfd:51", "--start", "0x10", "--end", "0x10"], io.StringIO()
    )
    assert code == EXIT_USAGE


def test_data_ranges_up_to_the_end() -> None:
    assert data_ranges(0x100, b"\xff\x01\x02") == [(0x101, 0x102)]


def test_data_ranges_merge_short_gaps() -> None:
    data = b"\x01" + b"\xff" * 3 + b"\x02" + b"\xff" * 10 + b"\x03"
    assert data_ranges(0, data) == [(0, 0), (4, 4), (15, 15)]
    assert data_ranges(0, data, merge_gap=4) == [(0, 4), (15, 15)]
    assert data_ranges(0, data, merge_gap=11) == [(0, 15)]
    assert compact_hex_dump(0, b"\x01") == "0x0000  01"
