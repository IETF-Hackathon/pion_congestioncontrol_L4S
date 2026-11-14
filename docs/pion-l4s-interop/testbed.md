# Run the Pion peers on the IETF 127 testbed

This workflow runs the current GCC transport starter using **Altanai's ICE and
interceptor forks**. It checks the endpoint attachment and records a repeatable
run. Prague, per-packet ECN marking/observation, adaptive encoding, and the full
six-metric comparison are still pending. No lab results have been collected.

See the [recorded local-run preview](../../index.html#preview) for the GCC/CCFB
and GCC/TWCC checks already completed on one Mac. They exercised the controller
and feedback path without the lab bottleneck. The next run described below
checks the physical testbed transport before a full performance comparison.

## Placement in the supplied diagram

```text
Separate server-side host                         My laptop
Pion sender                                      Pion receiver
     |                                                |
servers switch --> lld-black4 (DualPI2/SRM) --> client Ethernet
                   192.168.1.75
     <------------- RTCP feedback --------------------
```

The dedicated **Altanai's client** branch is attached to `lld-black4`, rather than
the separate monitor9 client switch or monitor10 Netflix branch. The diagram
labels 20 Mbps and 10 ms added delay per direction. Ask the operator for the
actual ports, host addresses, active qdisc mode (DualPI2/SRM), and live shaping
settings. Put competing traffic through the **same** bottleneck for contention
tests. The sender host is not supplied by this repository and does not run on
the switch or monitor; arrange a host attached to the servers switch.

The monitor's IP is a management address, not the laptop's IP, not a media peer,
and not an assumed default gateway. The existing `.212` client belongs to a
different branch. Do not assign either address to your new endpoint.

## 1. Build the same project revision on both hosts

The implementation was still uncommitted when the recorded preview was made.
Make sure the complete current source tree is present on the sender: a clone
does not include another checkout's unpushed edits. Transfer the same source
and edited config to the server, or publish/check out the tested branch, and
build for each host's own OS/architecture. Do not copy a Mac binary to a Linux
server.

Requirements: Go 1.24 or newer, Bash, and Python 3.9 or newer. The receiver may
run on macOS; the server may run Linux with `iproute2`. FFmpeg with `libvpx` is
needed only on the sender to generate a sample clip.

```sh
bash scripts/build.sh
```

The script disables local Go workspaces and uses committed module pins. In
`go.mod`, replacements map the original module identities to:

| Original module | Selected implementation | Tag commit |
| --- | --- | --- |
| `github.com/pion/interceptor` | `github.com/altanai/interceptor v0.1.51` | `3084336e7b893862766504226840a9f3e4f999c9` |
| `github.com/pion/ice/v4` | `github.com/altanai/ice/v4 v4.4.8` | `7562bf57f5cd713344cbb903de9df66010180c13` |

These tags contain the existing Pion implementations; creating/using a fork
does not itself add Prague or ECN. The rest of WebRTC remains the pinned
`github.com/pion/webrtc/v4 v4.2.26` module. A WebRTC fork can be added when a
tested transport change requires it; it is not needed just to select these
two forks.

`testbed.py run` inspects the **compiled binary** and rejects missing, stock,
or machine-local replacements for either library. A pinned future fork commit
can be selected with `go mod edit -replace=...@<version-or-pseudo-version>` and
`GOWORK=off go mod tidy`; run the checks and commit both `go.mod` and `go.sum`.
Keep imports and the library `module` declarations at their original Pion paths.

## 2. Configure the actual host addresses

```sh
cp testbed/lld-black4.example.json testbed/local.json
```

Edit **both** endpoint sections in `testbed/local.json` with operator-assigned
IPv4 addresses and Ethernet interface names, then place the same config on
both hosts. The sender's interface name can differ from the laptop's. For
example, macOS might use `en7` and Linux `eth0`; discover the actual names using
`networksetup -listallhardwareports` / `ifconfig` or `ip -br address`.

The committed example intentionally contains placeholders and cannot run.
`local.json` is ignored by Git. The helper rejects the management/device IPs
shown in the diagram and requires separate sender/receiver addresses. If the
operator assigns you an existing diagram host, update the documented reserved
address policy deliberately instead of creating a duplicate address.

The default selected UDP range is **50000–50100 inclusive on both peers**,
with **TCP 8080 on the receiver** for signaling and readiness. Arrange the lab's
host/network firewall rules for these ports. The scripts do not change any
firewall, IP address, route, qdisc, or shared testbed configuration. MTU, VLANs,
and addressing must follow the operator's setup.

`bottleneck` fields record the diagram assumptions and operator notes. The helper
does not turn these values into live router settings or measurements. To use
the monitor9 branch instead, arrange its client-switch port and update the
name, management address, dashboard URL, and notes; do not mix results between
branches. Only label a run DualPI2 after confirming the active qdisc mode.

## 3. Preview and check the receiver laptop

```sh
python3 scripts/testbed.py plan
python3 scripts/testbed.py doctor --role receiver
python3 scripts/testbed.py run --role receiver --run-id gcc-01
```

`plan` validates config and prints exact commands without accessing the network.
`doctor` requires the selected interface to exist and be up, the configured
IPv4 address to belong to it, and the route to the opposite peer to use it.
It rejects detected Wi-Fi and tunnel interfaces. The Go endpoint also filters
ICE by **both interface and IP** and binds signaling to that IP. If a route
uses Wi-Fi or a VPN, correct the lab attachment/routing before rerunning.

Leave the receiver running. Its `/healthz` endpoint reports selected feedback
and whether its single session has already been claimed. A readiness request
does not consume the session. No media is decoded/displayed by this starter.

## 4. Run the separate sender at the servers switch

Build the same revision and use the same config. Generate the input once:

```sh
bash scripts/make-media.sh
python3 scripts/testbed.py doctor --role sender
python3 scripts/testbed.py run --role sender --run-id gcc-01
```

The sender check binds its HTTP probe to its lab IPv4 address and requires a
reachable, unused receiver with matching CCFB/TWCC configuration. A successful
check is followed by offer/answer signaling, ICE/DTLS, RTP downlink media, and
RTCP feedback in the reverse direction. The sender's duration includes setup;
the receiver runs until Ctrl+C. Restart the receiver for each new run. Use a
new `--run-id` for each pair; existing role logs are never overwritten.

The sample is a fixed 500 kbps VP8 clip. It verifies transport at light load;
it does **not** saturate the diagram's 20 Mbps link. Increasing a fixed source
above the pacer rate can create a sender queue. Add rate adaptation and a bounded
queue policy before running a valid GCC/Prague performance comparison. Use
the identical clip hash and source settings for the matched controller runs.

## 5. Check the actual media path and retain evidence

Each host saves files under `results/raw/<run-id>/<role>/`:

- `endpoint.jsonl`: Pion events, selected ICE candidate pair, RTP payload
  counters (receiver), and GCC/feedback statistics (sender).
- `metadata.json`: config, local link/route checks, timestamps, process outcome,
  binary hash, sender clip hash, Git revision/dirty state, and actual fork versions.
- `build-info.txt`: Go toolchain and module replacement information from the binary.

Check that both selected ICE addresses are the assigned lab addresses. These
checks establish endpoint selection and HTTP connectivity; they cannot prove
that a transparent bridge shaped the media. Correlate the media flow with
operator-provided captures, qdisc counters and queue telemetry on **lld-black4**.
An mDNS dashboard name may be unavailable across links; use the operator's
management access method. Dashboard reachability alone does not prove the path.

Raw artifacts remain ignored by Git. Report the dirty state when using
uncommitted code. The JSON records unavailable CE and encoder-target values as
`null`; payload throughput includes duplicates and is not deduplicated goodput.
Queue delay, RTT variation, CE counts, loss, goodput and encoder target need
further instrumentation before filling the project page's results section.

## Files and responsibilities

| File | Responsibility |
| --- | --- |
| `scripts/build.sh` | Build the committed Altanai dependency pins. |
| `scripts/make-media.sh` | Create one fixed VP8 IVF source without overwriting it. |
| `scripts/testbed.py` | Validate config, check endpoints, launch one role, record evidence. |
| `testbed/lld-black4.example.json` | Topology assumptions and required host settings. |
| `cmd/pion-l4s/main.go` | Pion transport, signaling/health, GCC and packet processing. |
| `scripts/test_testbed.py` | Offline validation of address, route, feedback and fork guards. |

All Go functions have purpose comments, all Python functions have docstrings,
and each script has a file overview. See the [build guide](build-system.md) for
direct binary use and local library workspaces, and [the mapping](udp-prague-pion-mapping.md)
for the unimplemented Prague transport/controller work.
