"""dynet write: plan the changed bytes, check, write with acks, reset, verify."""

import io
from collections.abc import Iterable
from pathlib import Path

import pytest
from aiodynet import (
    MAX_MEMORY_CHUNK,
    DeviceIdentify,
    Dynet2Command,
    Dynet2DeviceRequest,
    Dynet2Frame,
    Dynet2MemoryData,
    Dynet2Message,
    Dynet2ReadMemory,
    Dynet2WriteMemory,
    Dynet2WriteMemoryAck,
    Message,
    decode,
)
from aiodynet.testing import FakeGateway

from dynet_cli.cli import EXIT_ERROR, EXIT_OK, EXIT_TIMEOUT, run
from dynet_cli.write import Block, WriteError, describe_plan, plan_write

HALL = (0xB3, 54)
HEADER = bytes.fromhex("b3 00 36 02 11 03 78 00 01 02")
OLD_STAMP = bytes.fromhex("00 03 94 94 02")
NEW_STAMP = bytes.fromhex("00 03 95 f5 cf")


def image(**changes: bytes) -> bytes:
    """A 1 KiB sensor image; `changes` maps hex addresses (``a0x223``) to bytes."""
    data = bytearray(HEADER + bytes(range(10, 256)) * 4)[:0x400]
    data[0x1A:0x1F] = OLD_STAMP
    for key, value in changes.items():
        address = int(key[1:], 16)
        data[address : address + len(value)] = value
    return bytes(data)


OLD = image(a0x223=b"\xfc")
NEW = image(a0x1a=NEW_STAMP, a0x223=b"\xf8", a0x228=b"\x01\x2c")


class Sensor:
    """The hall D5Sen: memory that reads and writes, acks, and resets.

    `drop_acks` holds addresses whose next write goes unacknowledged (once
    each); `failing` addresses are acked with an error result; writes to
    `ignored` addresses are acked but not stored. With `silent_reset` it
    never announces itself after a reset.
    """

    def __init__(self, memory: bytes) -> None:
        self.memory = bytearray(memory)
        self.drop_acks: set[int] = set()
        self.failing: set[int] = set()
        self.ignored: set[int] = set()
        self.silent_reset = False
        self.commands: list[int] = []
        self.writes: list[tuple[int, bytes]] = []

    def __call__(self, frame: Dynet2Frame) -> Iterable[Message | Dynet2Message]:
        match decode(frame):
            case Dynet2ReadMemory(box=54, address=address, length=length):
                data = bytes(self.memory[address : address + length])
                return [
                    Dynet2MemoryData(
                        device_code=0xB3,
                        box=54,
                        target_device_code=0xDA,
                        target_box=1,
                        address=address,
                        data=data,
                    )
                ]
            case Dynet2WriteMemory(target_box=54, address=address, data=data):
                return self._write(address, data)
            case Dynet2DeviceRequest(box=54, command=command):
                self.commands.append(command)
                if command == Dynet2Command.RESET and not self.silent_reset:
                    return [
                        DeviceIdentify(
                            device_code=0xB3,
                            box=54,
                            firmware_major=2,
                            firmware_minor=0x11,
                            boot_raw=0x80,
                        )
                    ]
        return []

    def _write(self, address: int, data: bytes) -> list[Dynet2Message]:
        if address in self.drop_acks:
            self.drop_acks.discard(address)
            return []
        result = b"\x00\x13" if address in self.failing else b"\x00\x00"
        if address not in self.failing and address not in self.ignored:
            self.memory[address : address + len(data)] = data
            self.writes.append((address, data))
        return [
            Dynet2WriteMemoryAck(
                device_code=0xB3,
                box=54,
                target_device_code=0xDA,
                target_box=1,
                address=address,
                result_raw=result,
            )
        ]


def files(tmp_path: Path, old: bytes = OLD, new: bytes = NEW) -> tuple[str, str]:
    base, target = tmp_path / "base.bin", tmp_path / "new.bin"
    base.write_bytes(old)
    target.write_bytes(new)
    return str(base), str(target)


async def dynet(sensor: Sensor, *args: str) -> tuple[int, str, FakeGateway]:
    out = io.StringIO()
    async with FakeGateway(frame_replies=sensor) as gateway:
        target = ["--host", gateway.host, "--port", str(gateway.port)]
        code = await run(
            ["write", *target, "--send-interval", "0", "--timeout", "0.1", *args],
            out,
        )
    return code, out.getvalue(), gateway


def frames_of(gateway: FakeGateway, kind: type) -> list[object]:
    return [m for f in gateway.received_frames if isinstance(m := decode(f), kind)]


# Planning.


def test_plan() -> None:
    plan = plan_write(HALL, OLD, NEW)
    assert plan.blocks == [Block(0x223, b"\xf8"), Block(0x228, b"\x01\x2c")]
    assert plan.stamp == NEW_STAMP
    assert plan.size == 3
    assert plan.writes() == [
        Block(0x1A, b"\xff" * 5),
        Block(0x223, b"\xf8"),
        Block(0x228, b"\x01\x2c"),
        Block(0x1A, NEW_STAMP),
    ]
    assert plan.checked_ranges() == [(0x1A, 5), (0x223, 1), (0x228, 2)]


def test_long_runs_are_split_into_frames() -> None:
    new = image(a0x100=b"\x00" * 250, a0x223=b"\xfc")
    plan = plan_write(HALL, OLD, new)
    assert [(b.address, len(b.data)) for b in plan.blocks] == [
        (0x100, MAX_MEMORY_CHUNK),
        (0x100 + MAX_MEMORY_CHUNK, MAX_MEMORY_CHUNK),
        (0x100 + 2 * MAX_MEMORY_CHUNK, 250 - 2 * MAX_MEMORY_CHUNK),
    ]
    assert plan.stamp is None
    assert plan.writes() == plan.blocks


def test_a_change_at_the_end_of_memory() -> None:
    plan = plan_write(HALL, OLD, OLD[:-1] + b"\x00")
    assert plan.blocks == [Block(len(OLD) - 1, b"\x00")]


def test_bytes_either_side_of_the_stamp() -> None:
    new = image(a0x19=b"\x00" * 7, a0x223=b"\xfc")  # 0x19 to 0x1f
    plan = plan_write(HALL, OLD, new)
    assert plan.blocks == [Block(0x19, b"\x00"), Block(0x1F, b"\x00")]
    assert plan.stamp == b"\x00" * 5


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (OLD, OLD[:-1], "differ in size"),
        (OLD[:0x10], OLD[:0x10], "too short"),
        (
            image(a0x2=b"\x37"),
            image(a0x2=b"\x37"),
            "base image is for device 0xb3 box 55",
        ),
        (OLD, b"\xfd" + OLD[1:], "new image is for device 0xfd box 54"),
        (OLD, image(a0x5=b"\x00"), "differ in the header"),
    ],
)
def test_plan_refuses(old: bytes, new: bytes, message: str) -> None:
    with pytest.raises(WriteError, match=message):
        plan_write(HALL, old, new)


def test_describe_nothing_to_write() -> None:
    assert describe_plan(plan_write(HALL, OLD, OLD)) == [
        (
            "Nothing to write to device 0xb3 box 54 (DUS360CR or DUS90CS sensor): "
            "no changes."
        )
    ]
    stamp_only = image(a0x1a=NEW_STAMP, a0x223=b"\xfc")
    assert describe_plan(plan_write(HALL, OLD, stamp_only))[0].endswith(
        "no changes besides the stamp."
    )


# The command.


async def test_without_yes_it_only_describes(tmp_path: Path) -> None:
    sensor = Sensor(OLD)
    base, new = files(tmp_path)
    code, out, gateway = await dynet(sensor, "0xb3:54", new, "--base", base)
    assert code == EXIT_OK
    assert out.splitlines() == [
        (
            "Would write 3 byte(s) in 2 block(s) to device 0xb3 box 54 "
            "(DUS360CR or DUS90CS sensor):"
        ),
        "  silence the device",
        "  0x001a: ff ff ff ff ff",
        "  0x0223: f8",
        "  0x0228: 01 2c",
        "  0x001a: 00 03 95 f5 cf",
        "  reset the device",
        (
            "First it reads back 3 range(s) to check the device still matches "
            "the base image; afterwards it reads them again to check the write."
        ),
        "Nothing was sent; add --yes to write.",
    ]
    assert gateway.received_frames == []


async def test_nothing_to_write_sends_nothing(tmp_path: Path) -> None:
    sensor = Sensor(OLD)
    base, new = files(tmp_path, new=OLD)
    code, out, gateway = await dynet(sensor, "0xb3:54", new, "--base", base, "--yes")
    assert code == EXIT_OK
    assert out.endswith("no changes.\n")
    assert gateway.received_frames == []


async def test_write(tmp_path: Path) -> None:
    sensor = Sensor(OLD)
    base, new = files(tmp_path)
    code, out, gateway = await dynet(sensor, "0xb3:54", new, "--base", base, "--yes")
    assert code == EXIT_OK
    assert bytes(sensor.memory) == NEW
    assert out.splitlines() == [
        "checked: the device matches the base image (3 range(s))",
        "sent: silence",
        "wrote 0x001a: ff ff ff ff ff",
        "wrote 0x0223: f8",
        "wrote 0x0228: 01 2c",
        "wrote 0x001a: 00 03 95 f5 cf",
        "sent: reset; the device came back",
        "verified: 8 byte(s) read back match the new image",
        "back: device 0xb3 box 54: identify, firmware 02.11, after a reset",
    ]
    assert sensor.commands == [Dynet2Command.SILENCE, Dynet2Command.RESET]
    # The order on the wire: check, silence, writes, reset, verify.
    kinds = [type(decode(f)).__name__ for f in gateway.received_frames]
    assert kinds == (
        ["Dynet2ReadMemory"] * 3
        + ["Dynet2DeviceRequest"]
        + ["Dynet2WriteMemory"] * 4
        + ["Dynet2DeviceRequest"]
        + ["Dynet2ReadMemory"] * 3
    )
    (write,) = [w for w in frames_of(gateway, Dynet2WriteMemory) if w.address == 0x223]  # type: ignore[attr-defined]
    # System Builder's own LED write, byte for byte.
    assert write.to_frame().hex("") == "ac049cda0001b30036000002230001f800003b2e"  # type: ignore[attr-defined]


async def test_a_lost_ack_is_retried(tmp_path: Path) -> None:
    sensor = Sensor(OLD)
    sensor.drop_acks.add(0x223)
    base, new = files(tmp_path)
    code, _, gateway = await dynet(sensor, "0xb3:54", new, "--base", base, "--yes")
    assert code == EXIT_OK
    assert bytes(sensor.memory) == NEW
    assert len(frames_of(gateway, Dynet2WriteMemory)) == 5


async def test_a_changed_device_is_not_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sensor = Sensor(image(a0x1a=bytes.fromhex("00 03 99 99 99"), a0x223=b"\xfc"))
    base, new = files(tmp_path)
    code, out, gateway = await dynet(sensor, "0xb3:54", new, "--base", base, "--yes")
    assert code == EXIT_ERROR
    assert out == ""
    assert "doesn't match the base image at 0x001a" in capsys.readouterr().err
    assert frames_of(gateway, Dynet2WriteMemory) == []
    assert sensor.commands == []


@pytest.mark.parametrize("fault", ["drop", "fail"])
async def test_a_failed_write_stops_without_a_reset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    sensor = Sensor(OLD)
    if fault == "drop":
        sensor.drop_acks.add(0x228)
        args = ["--retries", "0"]
    else:
        sensor.failing.add(0x228)
        args = []
    base, new = files(tmp_path)
    code, out, _ = await dynet(sensor, "0xb3:54", new, "--base", base, "--yes", *args)
    assert code == EXIT_ERROR
    assert out.splitlines()[-1] == "wrote 0x0223: f8"
    err = capsys.readouterr().err
    assert "2 of 4 write(s) went through" in err
    assert sensor.commands == [Dynet2Command.SILENCE, Dynet2Command.GO_BACK_TO_WORK]
    assert sensor.memory[0x1A:0x1F] == b"\xff" * 5  # the stamp stays blank


async def test_no_identify_after_the_reset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sensor = Sensor(OLD)
    sensor.silent_reset = True
    base, new = files(tmp_path)
    code, _, _ = await dynet(
        sensor, "0xb3:54", new, "--base", base, "--yes", "--wait", "0.05"
    )
    assert code == EXIT_ERROR
    assert "every write went through, but after the reset" in capsys.readouterr().err
    assert bytes(sensor.memory) == NEW


async def test_a_write_that_didnt_stick_fails_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sensor = Sensor(OLD)
    sensor.ignored.add(0x228)
    base, new = files(tmp_path)
    code, _, _ = await dynet(sensor, "0xb3:54", new, "--base", base, "--yes")
    assert code == EXIT_ERROR
    assert "differs from the new image at 0x0228" in capsys.readouterr().err


async def test_the_check_read_times_out(tmp_path: Path) -> None:
    base, new = files(  # box 55: nothing answers
        tmp_path,
        image(a0x1=b"\x00\x37", a0x223=b"\xfc"),
        image(a0x1=b"\x00\x37", a0x223=b"\xf8"),
    )
    code, _, _ = await dynet(
        Sensor(OLD), "0xb3:55", new, "--base", base, "--yes", "--retries", "0"
    )
    assert code == EXIT_TIMEOUT
