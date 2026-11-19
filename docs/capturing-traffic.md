# Capturing DyNet traffic

This guide is for anyone asked to record the traffic on their Dynalite system,
for example to help support for their hardware get added to
[aiodynet](https://github.com/sdunster/aiodynet) or the Home Assistant
`dynalite` integration. A day of real traffic shows which messages your
keypads, sensors and controllers send, and that's hard to learn any other way.

It takes about ten minutes to set up, then runs on its own.

## What gets recorded, and what doesn't

The capture holds one line per DyNet packet: the time, the direction (from or
to the gateway), and the bytes of the packet. Newer gateways also send longer
DyNet 2 frames, which are recorded the same way, and any bytes that don't form
a packet are kept as `noise` lines, so nothing the gateway sends is left out. From those, anyone can see:

- area, channel and preset numbers, and channel levels;
- *when* things happened: when lights went on, keypads were pressed or
  motion sensors fired;
- any clock time your system broadcasts.

It also records when the connection to the gateway opened or closed, along
with the IP address and port of the machine connected to it (the
`connect`/`close`/`gap` lines).

It does **not** record anything else on your network, or any passwords. Names
aren't recorded either: DyNet only carries numbers. [Before you
send it](#before-you-send-it) shows how to remove the real times if you'd
rather not share them.

Recording never sends anything to your Dynalite system.

## Pick a method

| | **A. Passive capture** (preferred) | **B. Record through a second connection** |
|---|---|---|
| Use it when | Home Assistant, or another controller, is already connected to the gateway | Nothing else is connected, or you can't use A |
| Runs on | The Linux machine that's connected to the gateway, with root access | Any computer that can reach the gateway (tested on Linux and macOS) |
| Records | Both directions: what the gateway sends **and** what Home Assistant sends | Only what the gateway sends |
| Touches the gateway | No. It only listens to the existing connection | Yes. It opens its own TCP connection |

Method A watches the network traffic between the gateway and the machine
already connected to it, so the gateway doesn't know it's there. Method B is
simpler, but some gateways may refuse a second connection, or drop the first
one when a second arrives. That hasn't been tested, so if Home Assistant is
connected, try B for a minute first and check that Home Assistant stays
connected.

Method A was tested on a Raspberry Pi running Raspberry Pi OS (Debian), with
Home Assistant in a container using host networking. On **Home Assistant
OS** the usual SSH add-ons can't see raw network traffic, so use method B
from another computer.

## Find your gateway's address and port

You need the gateway's IP address and TCP port, the same ones Home Assistant
uses:

- **Address:** in Home Assistant go to *Settings → Devices & services →
  Signify Dynalite* (Home Assistant currently lists it as *Philips
  Dynalite*). The entry is named after the gateway's address.
- **Port:** it's in the bridge settings in the *Dynalite* panel in the Home
  Assistant sidebar. If you set the integration up yourself, it's the port you
  entered then.

The examples below use `192.0.2.10:50000`. Replace it with yours.

## Install `dynet`

`dynet` needs Python 3.13 or newer. The easiest install is with
[uv](https://docs.astral.sh/uv/), which also fetches a suitable Python if
your system's is older:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs uv
uv tool install dynet-cli
dynet --version
```

If `dynet` isn't found afterwards, open a new terminal, or run
`uv tool update-shell`.

<details>
<summary>Alternative: a single file, with nothing to install on the capturing machine</summary>

Download `dynet.pyz` from the
[latest release](https://github.com/sdunster/dynet-cli/releases/latest) and
copy it to the capturing machine:

```sh
curl -LO https://github.com/sdunster/dynet-cli/releases/latest/download/dynet.pyz
scp dynet.pyz you@capture-host:
```

On the capturing machine (which needs Python 3.13 or newer), run
`python3 dynet.pyz` wherever this guide says `dynet`.
</details>

## Method A: passive capture (Linux, root)

### 1. Find the network interface

On the machine that's connected to the gateway:

```sh
ip -br addr
```

Pick the interface that has this machine's LAN address. It's usually `eth0`,
`end0` or `enp…` for wired, or `wlan0` for Wi-Fi.

### 2. Check it works

Run a one-minute test. It needs root to read raw network traffic, so pass
`sudo` the full path to `dynet`:

```sh
sudo "$(command -v dynet)" sniff 192.0.2.10:50000 -i eth0 --duration 60
```

While it runs, press a keypad button or switch something on in Home
Assistant. Each packet prints as a line of JSON. If you see
`-- captured 0 packet(s)` after doing something, check the interface, the
address and the port.

### 3. Capture for a day

On a system with systemd (most Linux distributions), this runs the capture in
the background for 24 hours. It keeps running after you log out:

```sh
sudo systemd-run --unit dynet-capture --property=RuntimeMaxSec=90000 \
  "$(command -v dynet)" sniff 192.0.2.10:50000 -i eth0 \
  --duration 86400 -o "$HOME/dynet-capture.jsonl"
```

Check on it at any time:

```sh
systemctl status dynet-capture               # still running?
wc -l ~/dynet-capture.jsonl                  # how many lines so far
dynet decode ~/dynet-capture.jsonl | tail    # the latest packets, readable
```

It stops by itself after 24 hours. To stop it early, run
`sudo systemctl stop dynet-capture`. A reboot also stops it. Every line is
written straight away, so a capture that stops early is still useful.

Without systemd, run the same `sniff` command inside `tmux` or `screen`, or
with `sudo nohup … &`.

## Method B: record through a second connection

On any computer that can reach the gateway:

```sh
dynet check --host 192.0.2.10 --port 50000      # can we connect at all?
dynet monitor --host 192.0.2.10 --port 50000 \
  --duration 86400 --record dynet-capture.jsonl
```

`monitor` prints each packet as it arrives and appends it to
`dynet-capture.jsonl`. Leave the terminal open, or run it in `tmux` or
`screen`. Press Ctrl-C to stop early. If Home Assistant is connected to the
same gateway, watch it for the first few minutes and stop if it loses its
connection.

## Before you send it

### Check what you've got

```sh
dynet decode --summary dynet-capture.jsonl
```

This counts the packets by type and lists every packet the library can't
decode yet. Those are the interesting ones, so include this output in your
message.

### Optional: remove the real times

To share only the time *between* packets, not when they happened:

```sh
python3 -c 'import json,sys
rows = [json.loads(line) for line in open(sys.argv[1])]
start = rows[0]["t"]
for row in rows:
    row["t"] = round(row["t"] - start, 3)
    row.pop("client", None)
    print(json.dumps(row))' dynet-capture.jsonl > dynet-capture-shared.jsonl
```

This also drops the IP address in the connection lines. Any clock time your
system broadcasts stays in the packets themselves.

### Compress it

```sh
gzip -k dynet-capture.jsonl      # makes dynet-capture.jsonl.gz
```

Each packet takes about 60 bytes, so even a busy day stays small.

## Sending it

Open an issue at <https://github.com/sdunster/aiodynet/issues> and include:

- the `dynet decode --summary` output;
- what you did while it ran, if anything specific, such as "pressed the
  hallway keypad's button 2 at 19:05";
- your gateway model, and other Dynalite hardware if you know it;
- which method you used.

GitHub issues are public. Attach the capture there if you're happy for it to
be public. Otherwise, say so in the issue and we'll arrange another way to
send it.
