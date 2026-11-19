# dynet-cli

`dynet` is a command-line tool for the Signify Dynalite **DyNet** bus: watch
traffic, recall presets, set channel levels, query state, discover areas, and
capture or decode traffic for later analysis. It is built on
[aiodynet](https://github.com/sdunster/aiodynet) and doubles as a worked
example of that library.

Requires Python 3.13 or newer. Install it with
[uv](https://docs.astral.sh/uv/), which also fetches a suitable Python if
needed, or with pipx:

```sh
uv tool install dynet-cli      # or: pipx install dynet-cli
export DYNET_HOST=192.0.2.10 DYNET_PORT=50000
```

> **Asked to capture traffic from your system?** Follow
> [Capturing DyNet traffic](https://github.com/sdunster/dynet-cli/blob/main/docs/capturing-traffic.md). It walks through
> recording a day of traffic and sending it in.

## Commands

| Command | What it does | Sends to the bus? |
|---|---|---|
| `dynet check` | Checks the gateway accepts TCP connections | No |
| `dynet monitor` | Shows bus traffic as it happens, decoded | No |
| `dynet preset AREA PRESET` | Selects a preset | Yes |
| `dynet level AREA CHANNEL LEVEL` | Fades a channel to a level | Yes |
| `dynet query preset AREA` | Asks an area for its current preset and waits for the answer | Yes (one request) |
| `dynet query level AREA CHANNEL` | Asks a channel for its level | Yes (one request) |
| `dynet scan` | Asks every area for its preset, to find the areas in use | Yes (one request per area) |
| `dynet probe` | Sends read-only requests of every kind and checks every reply decodes | Yes (several requests per area) |
| `dynet exercise AREA` | Sends one of each harmless command to one area and shows the replies | Yes, with `--yes`. Changes that area's lights |
| `dynet send HEX...` | Sends raw packets (the checksum is added if you leave it off) | Yes |
| `dynet decode [FILE]` | Decodes hex or a capture file. Needs no gateway | No |
| `dynet sniff HOST:PORT` | Passively records another client's gateway traffic (Linux, root) | No |

Run `dynet COMMAND --help` for every option.

### Watching the bus

```console
$ dynet monitor
-- watching 192.0.2.10:50000
19:02:11.402  1c 0c 00 00 00 00 ff d9  area 12: select preset 1
19:02:11.630  1c 0c 00 60 01 01 ff 77  area 12 channel 1: is at 100%
19:02:15.017  5c aa 55 53 18 71 26 a3  all devices: date is 2026-11-18 (day of week 3)
19:02:16.250  ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8  DyNet 2 frame, 20 bytes (not decoded)
```

DyNet 2 frames (sync byte `0xAC`, sent by some gateways after each connect and
by devices signing on) are shown raw, with their length. Sign-on replies are
decoded; aiodynet doesn't understand any other frame yet.

- `--area 1-10,12` shows only those areas.
- `--json` prints one JSON object per line.
- `--record FILE` also appends every packet to a capture file.
- `--duration SECONDS` stops after that long.

### Control

```sh
dynet preset 12 1 --fade 2      # area 12, preset 1, 2 s fade
dynet level 12 3 40% --fade 1   # area 12 channel 3 to 40 %
dynet level 12 3 off
dynet level 12 3 raw:0x80       # the raw DyNet level byte
```

Out-of-range values are rejected before anything is sent. For example, a
channel fade is at most 5.1 s, which is all the packet can carry.

### Finding your areas

`dynet scan --areas 1-255` asks each area for its current preset and lists
the ones that answer. It sends one packet per area, 0.2 s apart, so a full
scan takes about a minute. Some areas only answer for a channel they actually
have, so try `--channel` if an area you know about stays silent.

### Checking that everything decodes

A quiet bus only shows the traffic it happens to carry. These two commands
make devices talk, so you can check that aiodynet decodes all of it. Both
print each packet sent (`>`) and received (`<`), take `--record FILE`, and
end with the same summary as `decode --summary`.

`dynet probe` only asks questions. It asks each area in `--areas` (default
1-255) for its preset, and each area that answers for its channel levels
(`--channels`, default 1), user preferences (`--preferences`, default 0x0c,
temperature) and preset levels (`--presets`, default 1). Pass `none` to skip
a kind. `--signon 0xb3:42,...` first asks each listed device (device code:box) to sign
on, with physical opcode 0x40 and sub-opcode 0x0a ("Do D2 Signon" in the DyNet
opcode list). Each device answers with a DyNet 2 sign-on reply giving its
firmware version and packet sizes, and probe moves on as soon as it arrives.
`--signon all` sends one request to every device (0xaa:0x55) instead, but some
sites have that turned off, and then nothing answers. The device codes and
boxes on your bus show up in `monitor` as physical packets, for example a
Device Identify (`5c b3 2a 00 ...` is device 0xb3, box 42).

```console
$ dynet probe --areas 19 --signon 0xb3:42
19:40:01.120  >  5c b3 2a 40 00 00 0a 7d  device 0xb3 box 42: sign on
19:40:01.173  <  ac 04 81 b3 00 2a aa 55 55 01 00 00 02 11 1f 1f 00 00 3f b4  device 0xb3 box 42: signed on to all devices, firmware 02.11, max rx 128 / tx 128 bytes, port 0, slave 0
19:40:06.130  >  1c 13 00 63 00 00 ff 6f  area 19: which preset? (channel 1)
19:40:06.190  <  1c 13 00 62 00 00 ff 70  area 19: is at preset 1
...
-- 1 area(s) answered; sent 5 request(s), 4 answered
```

`dynet exercise AREA` sends one of each harmless command aiodynet can build
to one area: preset selects, channel levels and fades, on and off, stop fade,
level up and down, and area off. Then it puts the area back on the preset it
started on. It never programs or saves presets, disables panels, panics,
writes user preferences or sends physical messages. It changes the area's
lights, so pick an area that's safe to play with. Without `--yes` it only
prints what it would send.

### Capturing traffic

There are two ways to record traffic to a file:

- `dynet sniff GATEWAY:PORT -i IFACE` records the traffic between a gateway
  and a client already connected to it, such as Home Assistant, from that
  client's machine. It never connects to the gateway and never sends
  anything. It needs Linux and root, because it reads raw frames from the
  network interface.
- `dynet monitor --record FILE` records through its own connection to the
  gateway, on any OS.

[Capturing DyNet traffic](https://github.com/sdunster/dynet-cli/blob/main/docs/capturing-traffic.md) covers both step by
step, including running a capture for a day under systemd. Each line of the file is one packet (`{"t": ..., "dir": "rx"|"tx", "hex":
...}`), one DyNet 2 frame (the same, with its hex under `dynet2`), or one event
(`connect`, `close`, `reset`, `gap`, `noise`). A `noise` event keeps the
skipped bytes as `hex`. TCP is put
back in order first: keepalive probes and retransmits are dropped, and lost
data shows up as a `gap` rather than as a bogus packet.

### Checking a capture

```console
$ dynet decode --summary sample.jsonl
4 packet(s) over 24.00 h
  direction: rx 3, tx 1
By type:
        1  PresetSelect
        1  RequestPreset
        1  PresetReport
        1  Unknown physical 0x53
Areas: 12 (3)
Undecoded (1 distinct):
        1  5caa5553187126a3
```

The "Undecoded" list shows every packet and DyNet 2 frame aiodynet doesn't
yet have a typed message for. DyNet 2 frames are counted apart from packets
in the first line. Without `--summary`, `decode` prints each packet the same
way `monitor` does, or as JSON with `--json`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Gateway or file error |
| 2 | Bad arguments |
| 3 | `query` got no answer in time |

## Development

```sh
uv sync
uv run pytest --cov      # 100 % branch coverage is enforced
uv run ruff check && uv run ruff format --check && uv run mypy
```

To cut a release, see [RELEASING.md](https://github.com/sdunster/dynet-cli/blob/main/RELEASING.md).

## Licence

MIT. Not affiliated with or endorsed by Signify.
