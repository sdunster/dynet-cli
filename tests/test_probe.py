import io
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiodynet import (
    ChannelLevelReport,
    Dynet2Frame,
    Level,
    Message,
    Packet,
    PresetLevelReport,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    RequestPresetLevel,
    RequestUserPreference,
    UserPreferenceReport,
)
from aiodynet.testing import FakeGateway

from dynet_cli.cli import EXIT_OK, run
from dynet_cli.probe import SIGNON, exercise_steps

REPLY = Dynet2Frame.from_hex(
    "ac 04 81 b3 00 2a da 00 01 0a 0a 03 02 c8 7a 00 00 00 e8 44"
)
ODD = Packet.from_hex("1c 03 00 99 00 00 ff")  # an opcode aiodynet doesn't know


SITE: dict[Packet, list[Message | Packet | Dynet2Frame]] = {
    SIGNON: [REPLY],
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
    ],
    RequestPresetLevel(area=3, channel=1, preset=1).to_packet(): [
        PresetLevelReport(area=3, channel=1, preset=1, level=Level.OFF)
    ],
}


def site(packet: Packet) -> list[Message | Packet | Dynet2Frame]:
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
        "--signon-wait",
        "0.2",
        "--timeout",
        "0.2",
        "--record",
        str(record),
    )
    assert code == EXIT_OK
    assert "then 3 more request(s)" in capsys.readouterr().err
    assert ">  5c ff ff 40 00 00 0a 5c" in out
    assert f"<  {REPLY.hex()}  DyNet 2 frame, 20 bytes" in out
    assert "<  1c 03 0c 4a 1b 06 ff" in out
    # Area 3 answers, so it gets three more requests; area 4 is asked once.
    sent = [p.data[3] for p in gateway.received if p.is_logical]
    assert sent == [0x63, 0x61, 0x49, 0x91, 0x63]
    assert "-- 1 area(s) answered; sent 6 request(s), 4 answered" in out
    assert "DyNet 2 frames (1, 1 distinct, not decoded):" in out
    assert "Undecoded packets (1 distinct):" in out
    assert bytes(ODD).hex() in out
    lines = [json.loads(line) for line in record.read_text().splitlines()]
    assert [line["dir"] for line in lines][:3] == ["tx", "rx", "tx"]
    assert {"t": lines[1]["t"], "dir": "rx", "dynet2": REPLY.hex("")} == lines[1]


@pytest.mark.parametrize(("signon", "sent"), [([], 1), (["--signon"], 2)])
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
    assert "Every packet decoded to a typed message." in out


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
