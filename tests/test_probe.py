import argparse
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiodynet import (
    ChannelLevelReport,
    DeviceIdentify,
    Dynet2Frame,
    Level,
    Message,
    Packet,
    PresetLevelReport,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestFirmwareVersion,
    RequestPreset,
    RequestPresetLevel,
    RequestSignon,
    RequestUserPreference,
    UserPreferenceReport,
)
from aiodynet.testing import FakeGateway

from dynet_cli.cli import EXIT_OK, parse_targets, run
from dynet_cli.probe import ALL_DEVICES, ask_firmware, exercise_steps

# The D5Sen's real sign-on reply, and the reply it then sends System Builder.
SIGNED_ON = Dynet2Frame.from_hex(
    "ac 04 81 b3 00 2a aa 55 55 01 00 00 02 11 1f 1f 00 00 3f b4"
)
REPLY = Dynet2Frame.from_hex(
    "ac 04 81 b3 00 2a da 00 01 0a 0a 03 02 c8 7a 00 00 00 e8 44"
)
ODD = Packet.from_hex("1c 03 00 99 00 00 ff")  # an opcode aiodynet doesn't know


SITE: dict[Packet, list[Message | Packet | Dynet2Frame | bytes]] = {
    # The other reply comes first, so the step must wait for the sign-on.
    RequestSignon(device_code=0xB3, box=42).to_packet(): [REPLY, SIGNED_ON],
    # Another device's identify comes first, so the step must wait for its own.
    RequestFirmwareVersion(device_code=0xFD, box=9).to_packet(): [
        DeviceIdentify(device_code=0xB3, box=42, firmware_major=2, firmware_minor=0x11),
        DeviceIdentify(device_code=0xFD, box=9, firmware_major=3, firmware_minor=0x23),
    ],
    # A site where devices also answer the all-devices request.
    RequestSignon(device_code=0xAA, box=0x55).to_packet(): [SIGNED_ON, b"\x07"],
    # Devices report a change of preset.
    PresetSelect(area=3, preset=1, fade=1).to_packet(): [
        PresetReport(area=3, preset=1)
    ],
    RequestPreset(area=3).to_packet(): [PresetReport(area=3, preset=2)],
    RequestChannelLevel(area=3, channel=1).to_packet(): [
        ChannelLevelReport(
            area=3, channel=1, target_level=Level.FULL, actual_level=Level.FULL
        )
    ],
    RequestUserPreference(area=3, preference=0x0C).to_packet(): [
        UserPreferenceReport(area=3, preference=0x0C, value=0x1B06),
        ODD,
        b"\x09\x09",  # noise on the bus
    ],
    RequestPresetLevel(area=3, channel=1, preset=1).to_packet(): [
        PresetLevelReport(area=3, channel=1, preset=1, level=Level.OFF)
    ],
}


def site(packet: Packet) -> list[Message | Packet | Dynet2Frame | bytes]:
    """Area 3 answers everything; a sign-on gets a DyNet 2 reply."""
    return SITE.get(packet, [])


@pytest.fixture
async def gateway() -> AsyncIterator[FakeGateway]:
    async with FakeGateway(replies=site) as gateway:
        yield gateway


async def dynet(gateway: FakeGateway | None, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    target = (
        ["--host", gateway.host, "--port", str(gateway.port), "--send-interval", "0"]
        if gateway
        else []
    )
    code = await run([args[0], *target, *args[1:]], out)
    return code, out.getvalue()


async def test_probe(
    gateway: FakeGateway, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = tmp_path / "probe.jsonl"
    code, out = await dynet(
        gateway,
        "probe",
        "--areas",
        "3-4",
        "--signon",
        "0xb3:42",
        "--signon-wait",
        "2",
        "--timeout",
        "0.2",
        "--record",
        str(record),
    )
    assert code == EXIT_OK
    assert "then 3 more request(s)" in capsys.readouterr().err
    assert ">  5c b3 2a 40 00 00 0a 7d" in out
    assert (
        f"<  {REPLY.hex()}  device 0xb3 box 42: serial number 50514042 "
        "(to device 0xda box 1)"
    ) in out
    assert (
        f"<  {SIGNED_ON.hex()}  device 0xb3 box 42: signed on to all devices, "
        "firmware 02.11, max rx 128 / tx 128 bytes, port 0, slave 0"
    ) in out
    assert "<  1c 03 0c 4a 1b 06 ff" in out
    # Area 3 answers, so it gets three more requests; area 4 is asked once.
    sent = [p.data[3] for p in gateway.received if p.is_logical]
    assert sent == [0x63, 0x61, 0x49, 0x91, 0x63]
    assert "-- 1 area(s) answered; sent 6 request(s), 5 answered" in out
    assert "1  Dynet2SignonReply" in out
    assert "<  09 09  noise, 2 byte(s) (not a packet or frame)" in out
    assert "Noise: 2 byte(s), not in any packet or frame" in out
    assert "Undecoded (1 distinct):" in out
    assert bytes(ODD).hex() in out
    lines = [json.loads(line) for line in record.read_text().splitlines()]
    assert [line["dir"] for line in lines][:4] == ["tx", "rx", "rx", "tx"]
    assert {"t": lines[2]["t"], "dir": "rx", "dynet2": SIGNED_ON.hex("")} == lines[2]


@pytest.mark.parametrize(("signon", "sent"), [([], 1), (["--signon", "all"], 2)])
async def test_probe_only_presets(
    gateway: FakeGateway, signon: list[str], sent: int
) -> None:
    code, out = await dynet(
        gateway,
        "probe",
        "--areas",
        "3",
        "--channels",
        "none",
        "--preferences",
        "none",
        "--presets",
        "none",
        "--timeout",
        "0.2",
        "--signon-wait",
        "0.1",
        *signon,
    )
    assert code == EXIT_OK
    assert f"sent {sent} request(s), 1 answered" in out
    assert "Everything decoded to a typed message." in out


async def test_exercise_dry_run_sends_nothing() -> None:
    code, out = await dynet(None, "exercise", "19", "--channel", "2")
    assert code == EXIT_OK
    assert out.startswith("Would send to area 19, 1.5s apart:\n")
    assert "area 19: select preset 1 fade 1s" in out
    assert "area 19 channel 2: set 50%" in out
    assert out.endswith("Nothing was sent; add --yes to send.\n")


def test_exercise_leaves_out_risky_commands() -> None:
    opcodes = {step.packet.data[3] for step in exercise_steps(19, 1, Level.FULL)}
    # program preset, disable panels, panic, save preset, set preference
    assert opcodes.isdisjoint({0x08, 0x15, 0x17, 0x66, 0x48})
    assert all(step.packet.is_logical for step in exercise_steps(19, 1, Level.FULL))


async def test_exercise_restores_the_starting_preset(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "exercise", "3", "--pause", "0.02", "--yes")
    assert code == EXIT_OK
    steps = exercise_steps(3, 1, Level.from_percent(50))
    assert gateway.received[0] == RequestPreset(area=3).to_packet()
    assert gateway.received[1:-1] == [step.packet for step in steps]
    assert gateway.received[-1] == PresetSelect(area=3, preset=2).to_packet()
    assert f"-- sent {len(steps) + 2} packet(s)" in out
    assert "<  1c 03 00 62 00 00 ff" in out  # the report after preset 1


async def test_exercise_without_a_starting_preset(
    gateway: FakeGateway, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = await dynet(gateway, "exercise", "9", "--pause", "0.01", "--yes")
    assert code == EXIT_OK
    assert gateway.received[-1] != PresetSelect(area=9, preset=2).to_packet()
    assert "area 9 didn't say which preset it was on" in capsys.readouterr().err


def test_parse_targets() -> None:
    assert parse_targets("0xb3:42, all,1:2") == [(0xB3, 42), ALL_DEVICES, (1, 2)]
    with pytest.raises(argparse.ArgumentTypeError, match="expected DEVICE:BOX"):
        parse_targets("0xb3")
    with pytest.raises(argparse.ArgumentTypeError, match="box must be 0-255"):
        parse_targets("0xb3:300")


async def test_signon_accepts_the_long_form_from_all_devices() -> None:
    # The 44-byte sign-on comes from the all-devices address, not the device's.
    long_signon = Dynet2Frame.from_hex(
        "ac 0a 81 aa 55 55 aa 55 55 01 00 00 01 11 86 86 00 00 03 00 00 00 00"
        " 20 01 0d b8 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00"
    )
    request = RequestSignon(device_code=0x01, box=2).to_packet()
    async with FakeGateway(
        replies=lambda p: [long_signon] if p == request else []
    ) as gateway:
        code, out = await dynet(
            gateway,
            "probe",
            "--areas",
            "none",
            "--signon",
            "0x01:2",
            "--signon-wait",
            "2",
        )
    assert code == EXIT_OK
    assert "IPv6 2001:db8::1" in out
    assert "sent 1 request(s), 1 answered" in out


async def test_signon_waits_out_a_device_that_doesnt_answer(
    gateway: FakeGateway,
) -> None:
    code, out = await dynet(
        gateway,
        "probe",
        "--areas",
        "4",
        "--signon",
        "0x01:2",
        "--signon-wait",
        "0.1",
        "--timeout",
        "0.1",
    )
    assert code == EXIT_OK
    assert "sent 2 request(s), 0 answered" in out


async def test_probe_firmware(gateway: FakeGateway) -> None:
    code, out = await dynet(
        gateway,
        "probe",
        "--areas",
        "none",
        "--firmware",
        "0xfd:9,0x01:2",
        "--timeout",
        "0.2",
    )
    assert code == EXIT_OK
    assert (
        ">  5c fd 09 80 00 00 00 1e  device 0xfd box 9: which firmware version?" in out
    )
    assert (
        "<  5c fd 09 00 03 23 00 78  device 0xfd box 9: identify, firmware 03.23" in out
    )
    # The first device answers; nothing answers for the second.
    assert "-- 0 area(s) answered; sent 2 request(s), 1 answered" in out
    assert [p.data[3] for p in gateway.received] == [0x80, 0x80]


def test_firmware_to_all_devices_listens_for_the_whole_wait() -> None:
    assert ask_firmware(*ALL_DEVICES).answer is None
    assert ask_firmware(0xFD, 9).answer is not None
