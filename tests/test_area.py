"""The area command group: dry runs, sends, and reading area links."""

import io
from collections.abc import AsyncIterator, Iterable

import pytest
from aiodynet import AreaLinksReport, Packet, RequestAreaLinks
from aiodynet.testing import FakeGateway

from dynet_cli.cli import EXIT_OK, EXIT_TIMEOUT, EXIT_USAGE, run


def links_site(packet: Packet) -> Iterable[AreaLinksReport]:
    """Two devices answer area 8's link request, as at one site."""
    if packet == RequestAreaLinks(area=8).to_packet():
        return [
            AreaLinksReport(area=9, bits=1),  # another area's answer
            AreaLinksReport(area=8, bits=0),
            AreaLinksReport(area=8, bits=0x000101),
        ]
    return []


@pytest.fixture
async def gateway() -> AsyncIterator[FakeGateway]:
    async with FakeGateway(replies=links_site) as gateway:
        yield gateway


async def area(gateway: FakeGateway | None, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    target = (
        ["--host", gateway.host, "--port", str(gateway.port), "--send-interval", "0"]
        if gateway
        else []
    )
    code = await run(["area", args[0], *target, *args[1:]], out)
    return code, out.getvalue()


# Each action, with the packet System Builder sent for it on 2026-11-27.
ACTIONS = [
    (["enable-panels", "8"], "1c08ff160000ff", "area 8: enable panels"),
    (["disable-panels", "8"], "1c08ff150000ff", "area 8: disable panels"),
    (["panic", "8"], "1c0800170000ff", "area 8: panic"),
    (["end-panic", "8"], "1c0800180000ff", "area 8: end panic"),
    (["recall-previous", "8"], "1c08000f0000ff", "area 8: recall previous preset"),
    (
        ["recall-previous", "8", "--fade", "2"],
        "1c08640f0000ff",
        "area 8: recall previous preset fade 2s",
    ),
    (["join-level", "8", "255"], "1c08ff140000ff", "area 8: set panel join level 255"),
    (["rmask", "8", "0"], "1c0800400000ff", "area 8: set RMask 0x00"),
    (["bank", "8", "0"], "1c0800640000ff", "area 8: set preset bank 0"),
    (
        ["light-control", "8", "resume"],
        "1c08ff110001ff",
        "area 8 all channels: resume light control, all presets",
    ),
    (
        ["occupancy", "8", "suspend"],
        "1c08ff310000ff",
        "area 8 all channels: suspend occupancy detection, all presets",
    ),
    (
        ["links-set", "8", "0xffffff"],
        "1c08ff20ffffff",
        "area 8: set area link bits 0xffffff",
    ),
    (
        ["links-clear", "8", "0x000001", "--join", "0x02"],
        "1c080121000002",
        "area 8: clear area link bits 0x000001 join 0x02",
    ),
]


@pytest.mark.parametrize(("args", "raw", "text"), ACTIONS)
async def test_dry_run(args: list[str], raw: str, text: str) -> None:
    code, out = await area(None, *args)
    assert code == EXIT_OK
    assert out == (
        f"Would send: {Packet.from_hex(raw).hex()}  {text}\n"
        "Nothing was sent; add --yes to send.\n"
    )


@pytest.mark.parametrize(("args", "raw", "text"), ACTIONS)
async def test_send(gateway: FakeGateway, args: list[str], raw: str, text: str) -> None:
    code, out = await area(gateway, *args, "--yes")
    assert (code, out) == (EXIT_OK, f"sent: {text}\n")
    assert await gateway.next_packet() == Packet.from_hex(raw)


async def test_links(gateway: FakeGateway) -> None:
    code, out = await area(gateway, "links", "8", "--timeout", "0.2")
    assert code == EXIT_OK
    assert out.splitlines() == [
        "area 8: area link bits are 0x000000",
        "area 8: area link bits are 0x000101",
    ]


async def test_links_without_an_answer(gateway: FakeGateway) -> None:
    code, out = await area(gateway, "links", "9", "--timeout", "0.05")
    assert (code, out) == (EXIT_TIMEOUT, "")


@pytest.mark.parametrize(
    "args",
    [
        ["join-level", "8", "256"],
        ["occupancy", "8", "maybe"],
        ["links-set", "8", "0x1000000"],
        ["panic", "8", "--fade", "-1"],
    ],
)
async def test_bad_arguments(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        await run(["area", *args], io.StringIO())
    assert exit_info.value.code == EXIT_USAGE
