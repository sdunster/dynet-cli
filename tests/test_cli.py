import asyncio
import functools
import io
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
from aiodynet import (
    ChannelLevelReport,
    Dynet2Frame,
    DynetClient,
    Level,
    Packet,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    SetChannelLevel,
)
from aiodynet.testing import FakeGateway

from dynet_cli.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    parse_areas,
    parse_level,
    run,
)


def site(packet: Packet) -> list[PresetReport | ChannelLevelReport]:
    """Areas 3 and 7 answer preset requests; area 3 channel 2 answers levels."""
    for area, preset in ((3, 1), (7, 4)):
        if packet == RequestPreset(area=area).to_packet():
            return [PresetReport(area=area, preset=preset)]
    if packet == RequestChannelLevel(area=3, channel=2).to_packet():
        return [
            ChannelLevelReport(
                area=3, channel=2, target_level=Level.FULL, actual_level=Level(0x80)
            )
        ]
    return []


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
    # Connection options belong to the subcommand, after its name(s).
    split = 2 if args[:1] == ("query",) else 1
    code = await run([*args[:split], *target, *args[split:]], out)
    return code, out.getvalue()


# Argument parsing


@pytest.mark.parametrize(
    ("text", "raw"),
    [
        ("40", 0x99),
        ("40%", 0x99),
        ("off", 0xFF),
        ("ON", 0x01),
        ("full", 0x01),
        ("raw:0x80", 0x80),
    ],
)
def test_parse_level(text: str, raw: int) -> None:
    assert parse_level(text).raw == raw


@pytest.mark.parametrize("text", ["101", "-1", "dim", "raw:300", "raw:x"])
def test_parse_level_rejects(text: str) -> None:
    with pytest.raises(Exception, match="level must be"):
        parse_level(text)


def test_parse_areas() -> None:
    assert parse_areas("1-3,7, 5-5") == [1, 2, 3, 5, 7]
    with pytest.raises(Exception, match="runs backwards"):
        parse_areas("5-1")
    with pytest.raises(Exception, match="area must be 0-255"):
        parse_areas("300")
    with pytest.raises(Exception, match="whole number"):
        parse_areas("one")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["preset", "1", "0"], "preset must be 1-2048"),
        (["preset", "1", "1", "--fade", "2000"], "fade must be 0-1310.7"),
        (["preset", "1", "1", "--fade", "soon"], "fade must be seconds"),
        (["level", "1", "1", "50", "--fade", "6"], "fade must be 0-5.1"),
        (["sniff", "nohost"], "expected HOST:PORT"),
        (["sniff", ":50000"], "expected HOST:PORT"),
    ],
)
async def test_bad_arguments(
    args: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        await run(args)
    assert exit_info.value.code == EXIT_USAGE
    assert message in capsys.readouterr().err


async def test_gateway_is_required(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DYNET_HOST", raising=False)
    code, _ = await dynet(None, "check")
    assert code == EXIT_USAGE
    assert "--host and --port" in capsys.readouterr().err


async def test_gateway_from_environment(
    gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNET_HOST", gateway.host)
    monkeypatch.setenv("DYNET_PORT", str(gateway.port))
    out = io.StringIO()
    assert await run(["check"], out) == EXIT_OK


async def test_verbose_logging(gateway: FakeGateway) -> None:
    out = io.StringIO()
    args = ["-vv", "check", "--host", gateway.host, "--port", str(gateway.port)]
    assert await run(args, out) == EXIT_OK


# Gateway commands


async def test_check(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "check")
    assert code == EXIT_OK
    assert f"{gateway.port} is accepting connections" in out


async def test_unreachable_gateway(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    code = await run(["check", "--host", "127.0.0.1", "--port", str(port)])
    assert code == EXIT_ERROR
    assert "Can't connect" in capsys.readouterr().err


async def test_preset(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "preset", "3", "9", "--fade", "0.5")
    assert code == EXIT_OK
    assert out == "sent: area 3: select preset 9 fade 0.5s\n"
    assert gateway.received == [PresetSelect(area=3, preset=9, fade=0.5).to_packet()]


async def test_level(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "level", "1", "5", "off", "-j", "0x01")
    assert code == EXIT_OK
    assert out == "sent: area 1 channel 5: set 0% join 0x01\n"
    expected = SetChannelLevel(area=1, channel=5, level=Level.OFF, join=1)
    assert gateway.received == [expected.to_packet()]


async def test_query_preset(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "query", "preset", "7")
    assert (code, out) == (EXIT_OK, "area 7: is at preset 4\n")


async def test_query_level_json(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "query", "level", "3", "2", "--json")
    assert code == EXIT_OK
    assert json.loads(out) == {
        "type": "ChannelLevelReport",
        "area": 3,
        "join": 255,
        "channel": 2,
        "target_level": {"raw": 1, "percent": 100.0},
        "actual_level": {"raw": 128, "percent": 50.0},
    }


async def test_query_timeout(
    gateway: FakeGateway, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = await dynet(gateway, "query", "preset", "9", "--timeout", "0.05")
    assert code == EXIT_TIMEOUT
    assert "no answer within 0.05s" in capsys.readouterr().err


async def test_scan(gateway: FakeGateway, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = await dynet(gateway, "scan", "--areas", "1-8", "--timeout", "0.05")
    assert code == EXIT_OK
    assert out == "area 3: is at preset 1\narea 7: is at preset 4\n"
    assert "2 area(s) answered" in capsys.readouterr().err
    assert len(gateway.received) == 8


async def test_scan_json(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "scan", "--areas", "3", "--json")
    assert code == EXIT_OK
    assert json.loads(out)["preset"] == 1


async def test_send(gateway: FakeGateway) -> None:
    code, out = await dynet(gateway, "send", "1c0300630000ff", "5c 01 02 03 04 05 ff")
    assert code == EXIT_OK
    assert "area 3: which preset? (channel 1)" in out
    assert len(gateway.received) == 2


async def test_send_dynet2_frame(gateway: FakeGateway) -> None:
    # The checksum (43 c8) is added when left off.
    frame = "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00"
    code, out = await dynet(gateway, "send", frame, "1c0300630000ff")
    assert code == EXIT_OK
    assert out.splitlines()[0] == (
        "sent ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8  "
        "all devices: who are you? (from device 0xdc box 90)"
    )
    assert len(gateway.received) == 1
    assert bytes(gateway.raw_received).startswith(
        bytes.fromhex("ac0482dc005aaa5555000c0000000000000043c8")
    )


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("1c00", "A packet is 8 bytes, got 2"),
        ("ac 04 82", "Length byte 0x04 means 20 bytes"),
        ("ac0482dc005aaa5555000c00000000000000ffff", "Bad checksum"),
    ],
)
async def test_send_rejects_bad_hex(
    capsys: pytest.CaptureFixture[str], text: str, error: str
) -> None:
    with pytest.raises(SystemExit):
        await run(["send", "--host", "x", "--port", "1", text])
    err = capsys.readouterr().err
    assert "argument HEX:" in err
    assert error in err


async def test_monitor(
    gateway: FakeGateway, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = tmp_path / "capture.jsonl"

    async def traffic() -> None:
        await gateway.wait_for_client()
        await gateway.send(
            PresetSelect(area=3, preset=2), PresetSelect(area=4, preset=1)
        )
        await gateway.disconnect_clients()

    fast_client = functools.partial(DynetClient, reconnect_delay=0.01)
    task = asyncio.create_task(traffic())
    with patch("dynet_cli.cli.DynetClient", fast_client):
        code, out = await dynet(
            gateway,
            "monitor",
            "--area",
            "3",
            "--duration",
            "0.3",
            "--record",
            str(record),
        )
    await task
    assert code == EXIT_OK
    assert out.endswith("1c 03 00 01 00 00 ff e1  area 3: select preset 2\n")
    assert "area 4" not in out
    err = capsys.readouterr().err
    assert "watching" in err
    assert "connection lost" in err
    assert "reconnected" in err
    lines = [json.loads(line) for line in record.read_text().splitlines()]
    assert [line["hex"] for line in lines] == [
        PresetSelect(area=3, preset=2).to_packet().hex(""),
        PresetSelect(area=4, preset=1).to_packet().hex(""),
    ]


async def test_monitor_json(gateway: FakeGateway) -> None:
    async def traffic() -> None:
        await gateway.wait_for_client()
        await gateway.send(Packet.from_hex("5c0102030405ff"))

    task = asyncio.create_task(traffic())
    code, out = await dynet(gateway, "monitor", "--json", "--duration", "0.2")
    await task
    assert code == EXIT_OK
    line = json.loads(out)
    assert line["type"] == "UnknownMessage"
    assert line["hex"] == "5c0102030405ff96"


# The gateway's answer to "who are you": a frame aiodynet has no type for.
WHO_REPLY = Dynet2Frame.from_hex(
    "ac 04 83 dc 00 5a dc 00 5a 00 00 0d 00 00 00 00 00 00 31 ac"
)


async def test_monitor_shows_and_records_dynet2(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    record = tmp_path / "capture.jsonl"

    async def traffic() -> None:
        await gateway.wait_for_client()
        await gateway.send(WHO_REPLY)

    task = asyncio.create_task(traffic())
    code, out = await dynet(
        gateway, "monitor", "--duration", "0.2", "--record", str(record)
    )
    await task
    assert code == EXIT_OK
    assert out.endswith(f"{WHO_REPLY.hex()}  DyNet 2 frame, 20 bytes (not decoded)\n")
    assert json.loads(record.read_text()) == {
        "t": json.loads(record.read_text())["t"],
        "dir": "rx",
        "dynet2": WHO_REPLY.hex(""),
    }


async def test_monitor_dynet2_json_and_area_filter(gateway: FakeGateway) -> None:
    async def traffic() -> None:
        await gateway.wait_for_client()
        await gateway.send(WHO_REPLY)

    task = asyncio.create_task(traffic())
    code, out = await dynet(gateway, "monitor", "--json", "--duration", "0.2")
    await task
    assert code == EXIT_OK
    line = json.loads(out)
    assert line["type"] == "UnknownDynet2Message"
    assert line["hex"] == line["frame"] == WHO_REPLY.hex("")

    async def second_client_traffic() -> None:
        async with asyncio.timeout(2):
            while gateway.connections < 2:  # noqa: ASYNC110 - polling test state
                await asyncio.sleep(0.005)
        await gateway.send(WHO_REPLY)
        sent.set()

    sent = asyncio.Event()
    task = asyncio.create_task(second_client_traffic())
    code, out = await dynet(gateway, "monitor", "-a", "1", "--duration", "0.2")
    await task
    assert sent.is_set()
    assert out == ""


# Offline commands


async def test_decode_plain_and_capture(tmp_path: Path) -> None:
    capture = tmp_path / "in.jsonl"
    capture.write_text(
        "# a comment\n"
        "\n"
        "1c 05 00 0a 00 00 ff\n"
        '{"t": 1794969681.956, "dir": "tx", "hex": "1c0100630000ff81"}\n'
        '{"t": 1794969682.5, "event": "gap", "dir": "rx", "missing": 3}\n'
        '{"t": 1794969683, "event": "connect"}\n'
    )
    code, out = await dynet(None, "decode", str(capture))
    assert code == EXIT_OK
    lines = out.splitlines()
    assert lines[0] == "1c 05 00 0a 00 00 ff d6  area 5: select preset 5"
    assert lines[1].endswith(
        "tx  1c 01 00 63 00 00 ff 81  area 1: which preset? (channel 1)"
    )
    assert lines[2:] == ["-- gap dir=rx missing=3", "-- connect"]


async def test_decode_dynet2(tmp_path: Path) -> None:
    capture = tmp_path / "in.jsonl"
    capture.write_text(
        f'{{"t": 1794969681.956, "dir": "rx", "dynet2": "{WHO_REPLY.hex("")}"}}\n'
        f"{WHO_REPLY.hex()}\n"
    )
    code, out = await dynet(None, "decode", str(capture))
    assert code == EXIT_OK
    first, second = out.splitlines()
    assert first.endswith(
        f"rx  {WHO_REPLY.hex()}  DyNet 2 frame, 20 bytes (not decoded)"
    )
    assert second == f"{WHO_REPLY.hex()}  DyNet 2 frame, 20 bytes (not decoded)"

    code, out = await dynet(None, "decode", "--json", str(capture))
    first_json, second_json = (json.loads(line) for line in out.splitlines())
    assert first_json["dir"] == "rx"
    assert second_json == {
        "hex": WHO_REPLY.hex(""),
        "type": "UnknownDynet2Message",
        "frame": WHO_REPLY.hex(""),
    }


async def test_decode_json(tmp_path: Path) -> None:
    capture = tmp_path / "in.txt"
    capture.write_text(
        '1c05000a0000ff\n{"t": 1.5, "dir": "rx", "hex": "1c0500620000ff"}\n'
    )
    code, out = await dynet(None, "decode", "--json", str(capture))
    assert code == EXIT_OK
    first, second = (json.loads(line) for line in out.splitlines())
    assert first == {
        "hex": "1c05000a0000ffd6",
        "type": "PresetSelect",
        "area": 5,
        "join": 255,
        "preset": 5,
        "fade": 0.0,
    }
    assert second["t"] == 1.5
    assert second["dir"] == "rx"
    assert second["type"] == "PresetReport"


async def test_decode_summary(tmp_path: Path) -> None:
    capture = tmp_path / "in.jsonl"
    capture.write_text(
        '{"t": 0, "dir": "rx", "hex": "1c0500010000ffdf"}\n'
        '{"t": 3600, "dir": "rx", "hex": "5c0102030405ff96"}\n'
    )
    code, out = await dynet(None, "decode", "--summary", str(capture))
    assert code == EXIT_OK
    assert "2 packet(s) over 1.00 h" in out
    assert "Undecoded (1 distinct)" in out


async def test_decode_bad_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capture = tmp_path / "bad.txt"
    capture.write_text("hello\n")
    code, _ = await dynet(None, "decode", str(capture))
    assert code == EXIT_ERROR
    assert "Line 1: not JSON or hex" in capsys.readouterr().err


async def test_sniff_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "day.jsonl"

    class NoFrames:
        def recv(self, _bufsize: int, /) -> bytes:
            raise TimeoutError

        def settimeout(self, value: float | None, /) -> None:
            pass

        def close(self) -> None:
            pass

    with patch("dynet_cli.cli.open_raw_socket", return_value=NoFrames()):
        code, _ = await dynet(
            None, "sniff", "192.0.2.10:50000", "--duration", "0.01", "-o", str(output)
        )
    assert code == EXIT_OK
    err = capsys.readouterr().err
    assert "capturing 192.0.2.10:50000 on eth0 for 0.01s" in err
    assert "captured 0 packet(s)" in err


async def test_sniff_without_raw_sockets(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("dynet_cli.cli.open_raw_socket", side_effect=OSError("needs Linux")):
        code = await run(
            ["sniff", "192.0.2.10:50000", "--duration", "1"], io.StringIO()
        )
    assert code == EXIT_ERROR
    assert "needs Linux" in capsys.readouterr().err


async def test_monitor_shows_and_records_noise(
    gateway: FakeGateway, tmp_path: Path
) -> None:
    record = tmp_path / "capture.jsonl"
    preset = bytes(PresetSelect(area=3, preset=2).to_packet())

    async def traffic() -> None:
        await gateway.wait_for_client()
        await gateway.send(b"\x09\x09" + preset)

    task = asyncio.create_task(traffic())
    code, out = await dynet(
        gateway, "monitor", "--duration", "0.2", "--record", str(record)
    )
    await task
    assert code == EXIT_OK
    lines = out.splitlines()
    assert lines[0].endswith("09 09  noise, 2 byte(s) (not a packet or frame)")
    assert lines[1].endswith("area 3: select preset 2")
    first = json.loads(record.read_text().splitlines()[0])
    assert (first["event"], first["hex"], first["bytes"]) == ("noise", "0909", 2)


async def test_monitor_noise_json_and_area_filter(gateway: FakeGateway) -> None:
    async def traffic(clients: int) -> None:
        async with asyncio.timeout(2):
            while gateway.connections < clients:  # noqa: ASYNC110 - polling test state
                await asyncio.sleep(0.005)
        await gateway.send(b"\x09")

    task = asyncio.create_task(traffic(1))
    code, out = await dynet(gateway, "monitor", "--json", "--duration", "0.2")
    await task
    line = json.loads(out)
    del line["t"]
    assert line == {"hex": "09", "type": "Noise", "length": 1}

    task = asyncio.create_task(traffic(2))
    code, out = await dynet(gateway, "monitor", "-a", "1", "--duration", "0.2")
    await task
    assert code == EXIT_OK
    assert out == ""
