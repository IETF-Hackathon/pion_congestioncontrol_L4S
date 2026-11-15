# Complete the Prague loop with a separate sender laptop

The sender laptop runs the same Pion application as the Mac receiver. A web
server, browser, or UDP-Prague example process alone cannot replace that peer.

```text
Sender laptop → servers switch → lld-black4 → Mac en7 (Pion receiver)
              ← RTCP RFC 8888 / ECN feedback ←
```

## 1. Connect and identify both hosts

Connect the sender's Ethernet adapter to an operator-assigned **servers-switch**
port. Keep the receiver Mac connected behind lld-black4. Obtain the sender's
own lab address, subnet and route from DHCP/the operator. Do not reuse the
router, switch, monitor or existing `.212` addresses.

The Mac was using `en7` / `192.168.1.79`; confirm this is still true with
`ifconfig en7`. On Linux use `ip -br address` to find the sender's actual
Ethernet interface and IPv4 address; on a second Mac use `networksetup
-listallhardwareports` and `ifconfig`. The two interface names can differ.

Arrange SSH access to the sender if you want to operate it from the receiver
Mac. Otherwise run its commands directly on that laptop. SSH is for control;
the application uses direct UDP for media.

## 2. Transfer the actual implementation

The current branch contains uncommitted source, so cloning the public repository
alone does not retrieve the implementation. On the receiver Mac:

```sh
cd /Users/albisht/ietf/pion-l4s-interop
python3 scripts/server.py bundle
scp results/raw/sender-source.tar.gz results/raw/sender-source.tar.gz.manifest.json USER@SERVER_IP:/tmp/
ssh USER@SERVER_IP
```

Replace `USER@SERVER_IP` with the sender's real login/address. If a bundle
already exists, use a new filename with `--output` rather than overwriting it.
On the sender, extract into a new directory:

```sh
mkdir ~/pion-l4s-interop
tar -xzf /tmp/sender-source.tar.gz -C ~/pion-l4s-interop
cd ~/pion-l4s-interop
```

Keep the manifest with the run evidence. The archive excludes host secrets,
local lab assignments, generated media, binaries and raw measurements.

## 3. Build on the sender's own OS

Both hosts need Go 1.24+, Bash, Python 3.9+, and a C++11 compiler/cgo. Linux
needs g++/libstdc++; macOS needs Xcode Command Line Tools/Clang. The sender also
needs FFmpeg with libvpx to generate the VP8 source. Verify `go version`,
`python3 --version` and the compiler before building.

```sh
# Run in each host's own project directory
bash scripts/build.sh
bash scripts/check.sh

# Sender only, once
bash scripts/make-media.sh
```

Each machine builds a native binary. Do not copy the Mac executable onto Linux.
Keep one source clip for the experiment; do not regenerate it between controller
runs. The source and binary hashes are recorded in each sender run's metadata.

For a fresh Ubuntu 24.04 sender, install the prerequisites first:

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends build-essential golang-go python3 git ffmpeg openssh-server
```

Ubuntu's distro Go starts the build and automatically downloads the newer Go
toolchain requested by `go.mod` with the default `GOTOOLCHAIN=auto` setting.
This path was used to build the owned Ubuntu sender VM. Both laptops need
Internet access during dependency/toolchain installation; the media call itself
uses the lab addresses. On macOS install Go and FFmpeg and enable the Xcode
Command Line Tools instead of running the Ubuntu package commands.

## 4. Share one configuration

On the Mac, copy `testbed/lld-black4.example.json` to `testbed/local.json` and
edit the following values, preserving the other fields:

| Field | Value |
| --- | --- |
| `receiver.interface` | `en7`, if confirmed active |
| `receiver.ipv4` | `192.168.1.79`, if still assigned |
| `sender.interface` | Actual sender Ethernet interface |
| `sender.ipv4` | Actual assigned sender lab address |
| `controller` | `prague` |
| `feedback` | `ccfb` |
| `duration_seconds` | `60` for the first run |

Copy this exact config to the sender:

```sh
scp testbed/local.json USER@SERVER_IP:~/pion-l4s-interop/testbed/local.json
```

Allow TCP 8080 **to the receiver** and UDP 50000–50100 **between both peers**,
or use operator-approved alternatives in the config. The scripts inspect routes
and interfaces; they do not change firewall, DHCP, switch or qdisc settings.

## 5. Start the Mac receiver first

```sh
cd /Users/albisht/ietf/pion-l4s-interop
python3 scripts/testbed.py plan
python3 scripts/testbed.py doctor --role receiver
python3 scripts/testbed.py run --role receiver --run-id prague-lab-01
```

Leave that terminal running. `receiver ready` means HTTP signaling is listening;
it does not yet establish a media path. The receiver does not decode/display video.

## 6. Start the sender

In a second terminal, SSH to the sender, or use its own terminal:

```sh
ssh USER@SERVER_IP
cd ~/pion-l4s-interop
python3 scripts/testbed.py doctor --role sender
python3 scripts/testbed.py run --role sender --run-id prague-lab-01
```

The sender checks the Mac's readiness, exchanges SDP, establishes ICE/DTLS and
sends paced RTP. The receiver returns authenticated RTCP CCFB reports with the
observed ECN fields. The sender stops on invalid ECN or feedback timeout.

## 7. Verify both the media call and the congestion loop

First establish transport:

- Both logs show a selected ICE candidate pair using the two lab addresses.
- Receiver packet/byte counters and sender `congestion_feedback_reports` increase.
- Prague receiver `ect1_count` increases and `nonect_count` stays zero.

Then verify the physical ECN loop with the lab operator:

- Record the active lld-black4 qdisc, capacity and configured delay.
- Confirm the flow in lld-black4 captures/counters; endpoint addresses alone
  cannot prove that a transparent bridge shaped the traffic.
- Observe ECT(1) before the marking point and CE after it under controlled load.
- Match receiver `ce_count` to the sender's Prague CE counters, alpha and rate
  response. Account for separate timestamps and feedback delay.

The fixed 500 kbps clip is a light-load transport check against a 20 Mbps link.
**Zero CE can be expected when there is no congestion.** To exercise the CE
response, the operator needs to arrange controlled load through the same
lld-black4 bottleneck or an appropriate marking scenario. Netflix traffic on
monitor10 uses a different bottleneck. Adaptive encoding and the complete
performance comparison remain separate implementation work.

## 8. Retain evidence and compare GCC

The two hosts each retain `results/raw/prague-lab-01/<role>/` with endpoint
logs, config, source/binary details and process outcome. Preserve both folders
along with the operator's qdisc telemetry/captures. Stop the receiver with Ctrl+C
when the sender finishes.

For GCC, change `controller` to `gcc` in both copies, keep the other experiment
settings fixed, restart the receiver, and use a fresh ID such as `gcc-lab-01`.
Restart the receiver for every run because it accepts one session. Never reuse
an existing role/run folder. Compare application goodput and queueing only when
the corresponding measurements have actually been instrumented and collected.
