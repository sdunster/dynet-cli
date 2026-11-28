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
| `dynet info DEVICE:BOX` | Asks one device for its firmware and bootloader versions and builds, PCB version, serial number and whether its application is running | Yes (about seven requests). Read-only |
| `dynet lux DEVICE:BOX` | Asks a sensor for its light level | Yes (one request). Read-only |
| `dynet memory DEVICE:BOX ADDRESS LENGTH` | Reads bytes of a device's memory (EEPROM) and prints a hex dump | Yes (one request per 111 bytes). Read-only |
| `dynet dump DEVICE:BOX` | Reads a device's whole EEPROM (its size by device type, or 32 KiB) to a file, or prints a compact hex dump | Yes (one request per 111 bytes). Read-only |
| `dynet flash DEVICE:BOX` | Makes a device blink so you can find it, then puts it back to work | Yes. The device stops sending while it flashes |
| `dynet area ACTION AREA [VALUE]` | Area commands: enable or disable panels, panic and end panic, recall previous preset, join level, RMask, preset bank, light control and occupancy detection, area links. `area links AREA` reads the link bits | Yes, with `--yes` (`links` only asks) |
| `dynet write DEVICE:BOX NEW --base BASE` | Writes the bytes that differ between two dumps to a device, as System Builder does, checking before and after | Yes, with `--yes`. **Changes the device's stored configuration** |
| `dynet reset DEVICE:BOX` | Restarts a device with its stored configuration and waits for it to come back | Yes, with `--yes`. The device is offline for a moment |
| `dynet device-level DEVICE:BOX CHANNEL LEVEL` | Sets one output channel of a device directly (physical, not an area) | Yes. Changes that output |
| `dynet send HEX...` | Sends raw packets, or DyNet 2 frames when the hex starts with `ac` (the checksum is added if you leave it off) | Yes |
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
sites have that turned off, and then nothing answers. `--firmware 0xfd:9,...`
asks each listed device for its firmware version (physical opcode 0x80), and
the device answers with a Device Identify (`5c fd 09 00 03 23 00` is firmware
03.23). Firmware requests go first, then sign-ons, then the areas; use
`--areas none` to ask only the devices. The device codes and
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

### Asking a device about itself

Devices are named by their device code and box, as `monitor` shows them:
`0xb3:29` is a D5Sen sensor at box 29. These commands use DyNet 2 requests,
whose replies go back to a source address. That's System Builder's
(`0xda:1`) unless you pass `--source`, because devices are known to answer it.

```console
$ dynet info 0xb3:29
device 0xb3 box 29 (DUS360CR or DUS90CS sensor)
  firmware         02.11
  firmware build   1
  bootloader       75.05
  bootloader build 1
  PCB version      1.2
  serial number    50517129
  running          application
  max rx packet    128 bytes
  max tx packet    128 bytes
$ dynet lux 0xb3:29
15 lux
$ dynet memory 0xb3:29 0x1a 5
0x001a  00 03 94 96 51
```

`dynet dump 0xfd:51 -o rumpus.bin` reads a device's whole EEPROM, from 0x0000
to the end, unless you pass `--start` and `--end`. The end comes from the
device type. These are the device codes known so far, from one site; `info`
and `dump` name the product for them:

| Device code | Products | EEPROM |
|---|---|---|
| 0x4c | DDRC1220FR relay controller | 32 KiB |
| 0x64 | DDBC320-DALI ballast controller | 64 KiB |
| 0xb3 | DUS360CR and DUS90CS sensors (D5Sen) | 32 KiB |
| 0xdc | PDEG Ethernet gateway | 64 KiB |
| 0xfd | PA6BPA Antumbra panel | 64 KiB |

For other device types it reads 32 KiB and says so; pass `--end 0x10000` to
read all a request can address. Each request gets 111 bytes,
the most one reply holds, and a chunk that gets no answer is asked again
(`--retries`, default 3). A 9600-baud bus carries about one such reply every
0.2 s, so 32 KiB takes about a minute; progress goes to stderr. Without `-o`
it prints a hex dump in which runs of blank (0xff) lines are one line. Either
way it ends with the ranges that hold data. Two dumps compare well with
`cmp -l a.bin b.bin`.

None of these change anything on the device. If System Builder is connected
too, it reacts to each sign-on reply by asking the device for its serial
number, so expect a little extra traffic.

### Finding a device or an output

`dynet flash 0xb3:29` does what System Builder's "flash" does. It sends
"Silence. Listen to me" every 2 s for `--duration` seconds (default 10), and
the device blinks three times for each. Then it always sends "Go back to
work", even if you press Ctrl-C, and asks the device to sign on to check it's
back. While silenced a device doesn't send anything, so a sensor misses
motion and a panel's buttons do nothing. If a device is ever left silent,
`dynet flash DEVICE:BOX --stop` sends just "Go back to work".

`dynet device-level 0x4c:5 3 on` sets channel 3 of the relay controller at
box 5, the way System Builder's "flash channel" does (physical opcode 0x92).
The level is `0`–`100` (with or without `%`), `on`, `off`, or `raw:N` for the
byte itself (0xfe is 100 %). It bypasses areas and presets, so the area
still reports its old state.

### Area commands

`dynet area ACTION AREA` sends the area commands System Builder offers:

```sh
dynet area enable-panels 8 --yes
dynet area join-level 8 255 --yes      # smart panels' join level (normally 255)
dynet area occupancy 8 resume --yes
dynet area recall-previous 8 --fade 2 --yes
dynet area links 8                     # read-only: prints each device's answer
```

Without `--yes` each only prints the packet it would send. Some of these
change state that devices keep only at run time. On one site, a run of them
(panic, disable panels, join level 0, RMask 0, suspending light control and
occupancy, clearing links) left a panel sending nothing when pressed.
Undoing them one by one didn't fix it; `dynet reset` on the panel did. In a
later test, each action on its own was undone by its opposite (enable after
disable, end panic after panic, and so on). Disable panels turns off every
panel button for the area, including buttons on panels in other rooms.

### Restarting a device

`dynet reset 0xfd:51 --yes` sends System Builder's "Reset the device", which
it sends after every configuration write. The device restarts with the
configuration it has stored, then announces itself (a Device Identify "after
a reset"), which `reset` waits for (`--wait`, default 5 s). That undoes
anything changed only at run time. For example, a panel left unresponsive by
area commands works again after a reset. Without `--yes` it only prints what
it would send.

### Writing a device's memory

`dynet write DEVICE:BOX NEW --base BASE` changes a device's stored
configuration. BASE is a dump from `dynet dump -o`, and NEW is the same dump
with changes, made by a tool that knows the layout, such as
[dynalite-eeprom](https://github.com/sdunster/dynalite-eeprom)'s `compile`.
Only the bytes that differ are written, in the order System Builder uses:
"Silence. Listen to me", blank the stamp at 0x1A, write each block and wait
for the device's acknowledgement, write the new stamp, then reset the device.

Two checks guard it. Before writing, the bytes about to change and the stamp
are read back and compared with BASE: if the device has changed since that
dump, nothing is written. After the reset they are read again and compared
with NEW. A block that isn't acknowledged is retried (`--retries`, default
3). If a write still fails part way, the device is told to go back to work,
isn't reset, and its stamp is left blank. Keep BASE: dumping the device again
and writing BASE back with the new dump as `--base` undoes a change. The
header (bytes 0-9) is never written. Without `--yes` it only prints what it
would write.

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
(`connect`, `close`, `reset`, `gap`, `noise`). TCP is put back in order
first: keepalive probes and retransmits are dropped, and lost data shows up as
a `gap` rather than as a bogus packet.

Nothing read from the gateway is dropped silently. Bytes that aren't part of
any packet or frame are written as a `noise` event, in stream order, with the
bytes themselves as `hex`. That includes a partial packet cut off by a gap, a
closed connection or a reconnect. `sniff`, `monitor --record`, `probe` and
`exercise` all record noise, `monitor` shows it as it arrives, and `decode`
reports it for raw hex streams too. `decode --summary` totals it.

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
