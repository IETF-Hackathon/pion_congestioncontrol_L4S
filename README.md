# pion_congestioncontrol_L4S

A Pion endpoint for L4S WebRTC interop at the IETF 127 Hackathon.

Open [`index.html`](index.html) locally in a browser for the complete proposal, architecture, network overlay, experiment plan, blank results table, and lessons. The page embeds the supplied network diagram and works offline.

## Local checkout

Use `/Users/albisht/ietf/pion-l4s-interop` as the working repository. All build and testbed scripts resolve paths from this checkout; temporary validation directories are not required.

## Status

A runnable Pion sender/receiver supports GCC and an experimental UDP-Prague controller. Prague includes synchronous pacing, IPv4 ECT(1) marking, received ECN metadata, and ECN-aware RFC 8888 feedback. Local Prague/CCFB and GCC transport checks pass. Source adaptation, complete metrics, and physical testbed measurements remain pending. See [Prague integration and testbed status](docs/pion-l4s-interop/prague.md).

The [run preview in the project page](index.html#preview) shows the actual saved
local GCC counters, technology/version summary, and next testbed commands. The
[recorded snapshot](results/preview/local-gcc-20261114.json) contains the underlying
samples and provenance. **GCC ran locally; the GCC baseline through `lld-black4`
has not been run.** The experimental results table remains reserved for measured
POC results.

## Runnable starter

```sh
bash scripts/build.sh
./bin/pion-l4s --help
```

See [build and fork instructions](docs/pion-l4s-interop/build-system.md) for the video input, laptop receiver, lab sender, loopback checks, and how to develop local Pion library forks with a Go workspace. The current `--controller gcc` starter uses a fixed prerecorded source and GCC pacing; it is not yet a complete L4S implementation or a valid adaptive-encoder performance comparison. Use `--controller prague --feedback ccfb` on both peers with explicit `--local-ip` and `--interface`; see [Prague setup](docs/pion-l4s-interop/prague.md). Building now requires cgo and a C++11 compiler.

The build selects [`altanai/interceptor`](https://github.com/altanai/interceptor) at `v0.1.51` and [`altanai/ice`](https://github.com/altanai/ice) at `v4.4.8` through versioned `go.mod` replacements. Keep the original Pion import/module paths. The forks currently contain the existing controller/transport implementation; they do not add ECN by themselves.

## Testbed scripts

For a separate sender laptop, follow the [complete two-laptop procedure](docs/pion-l4s-interop/separate-laptop.md),
including copying the current implementation, sharing config and verifying CE feedback.
The [annotated topology](docs/images/pion-testbed-placement.png) shows exactly where both programs run and where their Ethernet cables connect.

To create your own Linux development server on this Mac, use
[`scripts/server.py`](scripts/server.py); see [server setup and placement](docs/pion-l4s-interop/own-server.md).
Its local VM network does not traverse the physical lab bottleneck. A host on
the servers switch or IETF side is still needed for that physical experiment.

Use the [testbed run guide](docs/pion-l4s-interop/testbed.md) for the full two-host procedure. The latest supplied diagram places **Altanai's client behind `lld-black4` (`192.168.1.75`)**, with a separate sender at the servers switch.

```text
Server-side Pion sender -> servers switch -> lld-black4 -> laptop Pion receiver
                       <- RTCP feedback across the same path <-
```

On both hosts, build the same revision, copy `testbed/lld-black4.example.json` to `testbed/local.json`, and fill both peers' assigned IPv4 addresses and Ethernet interface names. Then:

```sh
# Laptop: checks its lab interface/address/route, then receives until Ctrl+C.
python3 scripts/testbed.py plan
python3 scripts/testbed.py doctor --role receiver
python3 scripts/testbed.py run --role receiver --run-id gcc-01

# Separate server-side host, once the laptop receiver is running:
bash scripts/make-media.sh
python3 scripts/testbed.py doctor --role sender
python3 scripts/testbed.py run --role sender --run-id gcc-01
```

The helper verifies the compiled Altanai replacements, filters ICE to the configured interface/IP and UDP ports, and records per-role logs, build details, config, and hashes under `results/raw/`. It never overwrites an existing run. Allow TCP 8080 on the receiver and UDP 50000–50100 on both peers, or configure operator-approved ports. Scripts inspect the local network; they do not configure lab gear, routes, host addresses, or firewalls. Endpoint checks cannot prove passage through a transparent bottleneck: confirm the flow in operator counters/captures.

## POC objective

Compare Prague through DualPI2 against an explicitly configured Pion GCC baseline, using the same bottleneck and media source. Measure queue delay, RTT variation, CE counts, loss, goodput, and encoder target rate.

## Endpoint choice

Use Pion for both sender and receiver in the initial POC. This gives control over the sender's congestion controller, the receiver's ECN observation and RFC 8888 feedback, and logging at both ends.

Treat the existing WebRTC client labelled `.212` in the network diagram as a later interoperability target. Confirm its application, negotiated feedback, and ECN capability before using it as the receiver for a Prague experiment. The diagram alone does not establish those capabilities. Use an organizer-assigned host and address for the Pion receiver.

<img width="681" height="266" alt="IETF 127 Hackathon network topology" src="https://github.com/user-attachments/assets/6622c942-f49d-425c-8fa6-fbf7aec0ba7a" />

## Connect the peers to the test network

1. **Confirm the physical ports.** Connect the laptop receiver to the client-side Ethernet attachment on Altanai's `lld-black4` branch and the separate sender host to the servers switch. The operator should confirm this crosses the intended shaped path. The monitor9 client switch is a separate branch. No Pion API runs on a switch.
2. **Obtain host addresses.** Use lab DHCP or the operator's assigned IP address, subnet, and gateway. Do not copy `.212` onto a second host. `192.168.1.75` labels `lld-black4` management and is not the Pion receiver's address or an assumed gateway.
3. **Check reachability on Ethernet.** Inspect each host's address and the route to the other host. Allow the selected ICE UDP port range through the host firewall. Ordinary reachability checks establish connectivity; they do not prove that the media takes the shaped path.
4. **Configure ICE for the lab.** The helper passes `--interface`, `--local-ip`, and a UDP port range. Pion's `SettingEngine` applies interface/IP filters, IPv4 UDP host candidates and the port range. Use direct host connectivity when the lab permits it.
5. **Exchange signaling.** Run a small HTTP or WebSocket signaling service on a reachable endpoint. Exchange the SDP offer, SDP answer, and ICE candidates; alternatively, gather candidates completely before exchanging SDP. Agree on the codec and the intended RTCP feedback. The signaling service does not carry the media.
6. **Verify the selected media path.** Wait for the PeerConnection to connect, then inspect the logged selected ICE candidate pair. Confirm it uses the two lab Ethernet addresses, and correlate traffic with `lld-black4` captures or counters. Check with the operator when confirming a bridge path; traceroute may not show a transparent bottleneck.
7. **Run the experiment.** Send video from the server-side Pion host to the client-side Pion host; RTCP feedback returns across the same bottleneck. Run GCC and Prague separately with matched conditions. Validate ECT(1), received CE marks, and CE feedback for Prague. Use a rate-adaptable source, or repeatable media representations selected by the target rate, if measuring encoder adaptation.

The supplied topology labels `lld-black4` as **20 Mbps with 10 ms added in each direction**. Confirm its live settings and DualPI2/SRM mode with the operator. The configured delay adds roughly 20 ms to RTT before queuing. Put competing traffic through `lld-black4` as well: monitor9 and the Netflix branch through monitor10 have separate bottlenecks.

## UDP-Prague endpoint mapping

See [the Pion-to-UDP-Prague comparison](docs/pion-l4s-interop/udp-prague-pion-mapping.md) for how the example sender and receiver map to the planned WebRTC endpoints, and which feedback and ECN adaptations are required.

Track the implementation work in [todo/README.md](todo/README.md). The WebRTC module is pinned to `v4.2.26` and interceptor to `v0.1.51`.

## Implementation roadmap

- Pion sender and receiver, starting from [Pion's example applications](https://github.com/pion/example-webrtc-applications).
- RTP/RTCP processing using [`pion/interceptor`](https://github.com/pion/interceptor).
- Explicitly configured optional Pion GCC baseline, with bitrate adaptation connected to the source and pacer.
- [`UDP-Prague`](https://github.com/L4steam/udp_prague) integration, including packet accounting and a feedback adapter.
- ECT(1) marking and CE observation through the outer ICE/UDP transport, preserving per-packet metadata through the receiver path.
- RFC 8888 feedback and the required ECN negotiation and validation. Feedback-format support alone does not establish end-to-end ECN support.
- Endpoint logs and operator-provided bottleneck telemetry for the comparison metrics.

## Pion L4S integration TODOs

These tasks track the bridge from the standalone [UDP-Prague sender/receiver](https://github.com/L4steam/udp_prague) to Pion. The experimental adapter and local ECN feedback path are implemented; physical lab validation and source adaptation remain pending. See the [endpoint mapping](docs/pion-l4s-interop/udp-prague-pion-mapping.md) for the design comparison.

### TODO 1 — Pin and inspect the interfaces

- [x] Select and record the Pion WebRTC, ICE, interceptor, and UDP-Prague revisions.
- [ ] Confirm which RTCP RFC 8888 report format is negotiated and what packet identity, timing, and ECN information it carries.
- [ ] Map the selected report fields to the exact `PragueCC` API calls and document units, counter semantics, and wrap behavior.

**Done when:** a short interface note names the versions and describes the sender and receiver data flow field by field.

### TODO 2 — Build packet and feedback adapters

- [x] Track outgoing RTP packet identity, send time, size, and in-flight state for Prague accounting.
- [x] Parse incoming receiver feedback and correlate it with the tracked RTP packets.
- [x] Handle loss, reordering, duplicate/late reports, and sequence/counter wrap without double-counting.
- [x] Translate received, lost, CE, and timing information into the selected Prague API calls.

**Done when:** a deterministic trace demonstrates that known packet/feedback inputs produce the expected Prague controller inputs and accounting.

### TODO 3 — Apply Prague output to media

- [x] Connect Prague's pacing rate and burst/window limits to Pion's RTP send path.
- [ ] Connect its target rate to a rate-controllable media source or encoder.
- [x] Ensure GCC cannot also control the same stream during a Prague run.
- [ ] Log requested controller rate, applied pacer rate, and encoder/source target for comparison.

**Done when:** logs show the controller's rate changes reaching the pacer and source, with no competing sender controller.

### TODO 4 — Expose outer-IP ECN

- [x] Mark outgoing ICE/UDP packets ECT(1) for the Prague run.
- [x] Read the ECN field on received outer IP packets and expose CE observations to receiver feedback generation.
- [x] Detect and report bleaching or invalid ECN behavior.
- [ ] Verify the behavior on the selected operating system and exact ICE/UDP path.

**Done when:** packet captures show ECT(1) before the bottleneck, CE after marking, and matching CE information in receiver feedback.

### TODO 5 — Prove the closed loop

- [ ] Correlate packet captures, receiver reports, Prague state, pacer output, and encoder/source target.
- [ ] Exercise CE marking, loss, reordering, and CE bleaching or invalid-ECN cases.
- [ ] Confirm the sender responds to reported congestion and stops relying on L4S marking when feedback reports an ECN error.

**Done when:** a timestamped trace follows an observed bottleneck mark through receiver feedback into Prague's state and applied sending rate; negative cases are distinguishable.

### TODO 6 — Run matched comparisons

- [ ] Run explicitly configured GCC and Prague using the same media source, capacity, delay, duration, and cross traffic.
- [ ] Record queue delay, RTT variation, CE count, loss, goodput, and encoder/source target rate.
- [ ] Repeat with a classic competing flow and diagnostic cases such as ECN bleaching or a non-L4S queue.
- [ ] Preserve the run configuration and endpoint/network logs with each result.

**Done when:** the paired runs are reproducible from recorded configuration and all listed measurements can be traced to endpoint or bottleneck evidence.


## Contribution workflow

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the repository and fork workflow. Use a feature branch based on `origin/main` and submit changes through a pull request. Keep a file overview and purpose comments/docstrings on every function when adding code.

## References

- [RFC 9330: L4S architecture](https://www.rfc-editor.org/rfc/rfc9330.html)
- [RFC 9331: L4S ECN](https://www.rfc-editor.org/rfc/rfc9331.html)
- [RFC 9332: DualQ coupled AQM](https://www.rfc-editor.org/rfc/rfc9332.html)
- [RFC 8888: RTP congestion-control feedback](https://www.rfc-editor.org/rfc/rfc8888.html)
- [RFC 9768: TCP AccECN](https://www.rfc-editor.org/rfc/rfc9768.html), implemented separately from the WebRTC endpoint
- [Pion ICE and network configuration](https://github.com/pion/webrtc/blob/main/settingengine.go)
