# Build the Pion peers and develop transport forks

The application belongs in `IETF-Hackathon/pion_congestioncontrol_L4S`. The starter imports Pion as versioned Go modules and selects `altanai/interceptor` and `altanai/ice` through pinned replacements. Use the [testbed run guide](testbed.md) for the scripted two-host workflow on the supplied `lld-black4` path.

## Included code

- `cmd/pion-l4s/main.go`: receiver and sender roles in one Go binary.
- Receiver: HTTP offer/answer signaling, a Pion PeerConnection, RTP draining, and received RTP-payload throughput logs. Media is received and counted; it is not decoded or displayed.
- Sender: looping VP8 IVF input, explicitly configured Pion GCC and its pacer, RTCP draining, and controller statistics.
- `--feedback ccfb` uses RFC 8888 feedback; `--feedback twcc` selects TWCC. Use the same setting on both peers.
- `--interface` filters ICE candidates to the named network interface. The app uses direct IPv4 UDP host candidates and no configured STUN/TURN servers.
- `--local-ip` further restricts candidates to an assigned IPv4 address on that interface and requires receiver signaling to bind to it. `--udp-port-min`/`--udp-port-max` bound ICE's UDP port selection.
- `GET /healthz` reports receiver identity, feedback choice, selected lab IP, and session availability without consuming the session.

Dependencies are pinned in `go.mod` and `go.sum`: `github.com/pion/webrtc/v4 v4.2.26`, with `github.com/pion/interceptor` replaced by `github.com/altanai/interceptor v0.1.51` and `github.com/pion/ice/v4` replaced by `github.com/altanai/ice/v4 v4.4.8`. These original Pion tags in the personal forks do not yet add Prague/ECN. Module source is downloaded into the Go module cache; edit application code here or in library checkouts, not the module cache.

## Current limits

The experimental `--controller prague` mode integrates the pinned UDP-Prague core, synchronous pacing, socket ECN and ECN-aware RFC 8888 reports. See [Prague details](prague.md) for build requirements, limits and commands. CE counts are available in Prague mode; encoder targets are still unavailable. GCC retains its previous ECN behavior.

The prerecorded source runs at a fixed frame rate and bitrate; GCC controls the packet pacer but does not adapt the encoder/source in this first version. In GCC mode, sustained offered load above the pacing rate can build up the pacer's queue. Prague mode blocks RTP writes instead, so the fixed source can slow down; it still lacks encoder adaptation. Add source adaptation and a bounded queue policy before using it for congestion performance comparisons. Receiver payload throughput includes all received RTP payloads, including any duplicates; it is not a deduplicated application goodput measurement. Queue delay requires bottleneck telemetry. The starter does not yet calculate all six POC metrics.

Each receiver process handles one sender session. Restart it for the next run. The signaling service has no user authentication; bind it to the intended lab address for the controlled test.

## Build

Install Go 1.24 or newer, cgo support and a C++11 compiler (Clang on macOS or g++ on Linux). FFmpeg with VP8 encoding is needed only to create the sample input. Use `bash scripts/check.sh` for checks with the reference controller's signed-wrap flag.

```sh
cd /Users/albisht/ietf/pion-l4s-interop
bash scripts/build.sh
```

Create a short repeatable input; keep generated media out of the contribution:

```sh
mkdir -p media
ffmpeg -f lavfi -i testsrc2=size=640x360:rate=30 -t 10 \
  -c:v libvpx -b:v 500k -g 30 -an -f ivf media/sample.ivf
```

## Connect to the lab

Confirm the actual Ethernet device name and addresses before substituting values in these commands. The current network diagram with Altanai's dedicated branch identifies `lld-black4` (`192.168.1.75`) as the client-side bottleneck. Earlier proposal images show monitor9; use the operator-confirmed live path and log which device was used.

On the laptop, attached to the client side:

```sh
./bin/pion-l4s --role receiver \
  --listen LAPTOP_LAB_IP:8080 --interface ETHERNET_INTERFACE \
  --feedback ccfb
```

On the separate host attached to the servers switch, build the same revision and prepare the same input clip, then run:

```sh
./bin/pion-l4s --role sender \
  --peer http://LAPTOP_LAB_IP:8080/offer --interface SERVER_INTERFACE \
  --controller gcc --feedback ccfb --video media/sample.ivf --duration 30s
```

Replace every uppercase placeholder. The laptop must expose TCP 8080 for signaling and the ICE-selected UDP ports for media. The lab firewall policy determines the allowed port range. Both peers log the selected ICE candidate pair; compare the logged addresses to the lab Ethernet addresses and confirm traffic in the bottleneck counters.

The sender needs enough time for signaling and ICE/DTLS setup; `--duration` covers the whole process. Ctrl+C shuts down either peer. Use endpoint JSON logs plus operator-supplied captures and queue telemetry for validation.

## Same-Mac transport check

In two terminals, using the generated input:

```sh
# Terminal 1
./bin/pion-l4s --role receiver --listen 127.0.0.1:8080 \
  --interface lo0 --loopback --feedback ccfb

# Terminal 2
./bin/pion-l4s --role sender --peer http://127.0.0.1:8080/offer \
  --interface lo0 --loopback --feedback ccfb --video media/sample.ivf \
  --duration 10s
```

Look for `selected ICE pair`, increasing receiver packet/payload counters, and nonzero `congestion_feedback_reports` in the sender logs. A loopback check verifies application plumbing; the lab is still needed for DualPI2 and L4S measurements.

## Which upstream code to use

| Upstream | Role in this project | When to fork |
| --- | --- | --- |
| [pion/webrtc](https://github.com/pion/webrtc) | PeerConnection, RTP tracks, codecs, settings, and examples. | To change WebRTC transport/metadata plumbing or APIs. |
| [pion/interceptor](https://github.com/pion/interceptor) | GCC, pacing, RTP/RTCP hooks, TWCC and RFC 8888 feedback. | To add the Prague adapter or preserve ECN attributes through feedback. |
| [pion/ice](https://github.com/pion/ice) | UDP sockets, ICE candidates, and the selected media path. | To add or adapt per-packet ECN marking/observation. |
| [pion/example-webrtc-applications](https://github.com/pion/example-webrtc-applications) | Larger application and media-integration examples. | If contributing changes to one of those examples; it is not an application dependency. |
| [L4STeam/udp_prague](https://github.com/L4steam/udp_prague) | C++ Prague controller and its UDP reference application. | For controller changes or a maintained cgo wrapper; its example wire format is not WebRTC. |

Source attribution for the Pion example patterns is in `THIRD_PARTY_NOTICES.md`.

## Develop a local Pion fork without copying a repository into this one

Start from the versions actually used by the application. These commands create a sibling checkout and a local Go workspace:

```sh
mkdir -p ../pion-forks
git clone https://github.com/altanai/interceptor.git ../pion-forks/interceptor
git -C ../pion-forks/interceptor switch -c l4s-ecn v0.1.51

go work init .
go work use ../pion-forks/interceptor
go list -m github.com/pion/interceptor
```

The final command should show the local checkout as the source. If `go.work` already exists, skip `go work init` and use `go work use` to add the checkout. `go.work` and `go.work.sum` are ignored because the workspace uses machine-specific paths.

For ICE transport work, add the pinned ICE module in the same way:

```sh
git clone https://github.com/altanai/ice.git ../pion-forks/ice
git -C ../pion-forks/ice switch -c l4s-ecn v4.4.8
go work use ../pion-forks/ice
```

Keep the original module paths (`github.com/pion/interceptor`, `github.com/pion/ice/v4`) inside the library checkouts. Go workspace modules override the pinned replacements for local development; `go list -m` should show the local directory. Use `go build -o bin/pion-l4s-dev ./cmd/pion-l4s` to build that development workspace. The reproducible build script uses `GOWORK=off`, and the lab runner rejects local replacements. Additional compatible WebRTC/SRTP/transport changes may be needed to carry ECN attributes across the entire path; an ICE-only change does not establish end-to-end support.

The personal forks already exist at `altanai/interceptor`, `altanai/ice`, and `altanai/pion-webrtc` (the Go implementation). Use the `altanai` GitHub account for publishing library feature branches. The application repo's remote is independent of the library remotes.

Before publishing the application, turn tested library changes into reproducible pinned module revisions or a documented dependency patch set. Machine-local workspace paths are not reproducible dependencies for other contributors.

## Validation commands

```sh
gofmt -w cmd/pion-l4s
GOWORK=off go test ./...
GOWORK=off go vet ./...
GOWORK=off go test -race ./...
python3 -m unittest discover -s scripts -p 'test_*.py'
```
