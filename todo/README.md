# Pion L4S integration TODOs

These tasks bridge the standalone [UDP-Prague sender/receiver](https://github.com/L4steam/udp_prague) to the Pion WebRTC endpoints. The experimental adapter now has a physical two-host ECN feedback observation: 2,078 RTP received, including 2 CE, with both CE marks recorded in sender Prague state. Sustained congestion validation and source adaptation remain pending; see [implementation status](../docs/pion-l4s-interop/prague.md), [current setup and missing work](../docs/images/pion-current-lab-setup.md), and [run evidence](../results/preview/prague-lab-selabs216-20261114.json). See the [endpoint mapping](../docs/pion-l4s-interop/udp-prague-pion-mapping.md) for the design comparison.

## Current physical setup

- [x] Install the separate sender on `selabs@192.168.1.216`, Ethernet `enp1s0`, at the servers switch (user-confirmed placement).
- [x] Connect the Mac receiver at `192.168.1.79`, Ethernet `en7`, behind lld-black4 (user-confirmed placement).
- [x] Observe direct Ethernet ICE media delivery and CE feedback in Prague state.
- [ ] Independently verify lld-black4's forwarding path, active qdisc, capacity and delay with operator captures/counters.
- [ ] Apply repeatable load through this same branch and correlate sustained CE, controller state and actual sending rate.
- [ ] Implement general ECN negotiation and dynamic fallback for wider WebRTC interoperability; paired lab capability checks currently stop on ECN failure.

## TODO 1 — Pin and inspect the interfaces

- [x] Select and record the Pion WebRTC, ICE, interceptor, and UDP-Prague revisions.
- [ ] Confirm which RTCP RFC 8888 report format is negotiated and what packet identity, timing, and ECN information it carries.
- [ ] Map the selected report fields to the exact `PragueCC` API calls and document units, counter semantics, and wrap behavior.

**Done when:** a short interface note names the versions and describes the sender and receiver data flow field by field.

## TODO 2 — Build packet and feedback adapters

- [x] Track outgoing RTP packet identity, send time, size, and in-flight state for Prague accounting.
- [x] Parse incoming receiver feedback and correlate it with the tracked RTP packets.
- [x] Handle loss, reordering, duplicate/late reports, and sequence/counter wrap without double-counting.
- [x] Translate received, lost, CE, and timing information into the selected Prague API calls.

**Done when:** a deterministic trace demonstrates that known packet/feedback inputs produce the expected Prague controller inputs and accounting.

## TODO 3 — Apply Prague output to media

- [x] Connect Prague's pacing rate and burst/window limits to Pion's RTP send path.
- [ ] Connect its target rate to a rate-controllable media source or encoder.
- [x] Ensure GCC cannot also control the same stream during a Prague run.
- [ ] Log requested controller rate, applied pacer rate, and encoder/source target for comparison.

**Done when:** logs show the controller's rate changes reaching the pacer and source, with no competing sender controller.

## TODO 4 — Expose outer-IP ECN

- [x] Mark outgoing SRTP data ECT(1) for the Prague run, keeping STUN, DTLS and RTCP Non-ECT.
- [x] Read the ECN field on received outer IP packets and expose CE observations to receiver feedback generation.
- [x] Detect and report bleaching or invalid ECN behavior.
- [ ] Verify the behavior on the selected operating system and exact ICE/UDP path.

**Done when:** packet captures show ECT(1) before the bottleneck, CE after marking, and matching CE information in receiver feedback.

## TODO 5 — Prove the closed loop

- [ ] Correlate packet captures, receiver reports, Prague state, pacer output, and encoder/source target.
- [ ] Exercise CE marking, loss, reordering, and CE bleaching or invalid-ECN cases.
- [ ] Confirm the sender responds to reported congestion and stops relying on L4S marking when feedback reports an ECN error.

**Done when:** a timestamped trace follows an observed bottleneck mark through receiver feedback into Prague's state and applied sending rate; negative cases are distinguishable.

## TODO 6 — Run matched comparisons

- [ ] Run explicitly configured GCC and Prague using the same media source, capacity, delay, duration, and cross traffic.
- [ ] Record queue delay, RTT variation, CE count, loss, goodput, and encoder/source target rate.
- [ ] Repeat with a classic competing flow and diagnostic cases such as ECN bleaching or a non-L4S queue.
- [ ] Preserve the run configuration and endpoint/network logs with each result.

**Done when:** the paired runs are reproducible from recorded configuration and all listed measurements can be traced to endpoint or bottleneck evidence.
