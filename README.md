# pion_congestioncontrol_L4S

A Pion endpoint for L4S WebRTC interop at the IETF 127 Hackathon.

Open [`index.html`](index.html) locally in a browser for the complete proposal, architecture, network overlay, experiment plan, blank results table, and lessons. The page embeds the supplied network diagram and works offline.

## POC objective

Compare Prague through DualPI2 against an explicitly configured Pion GCC baseline, using the same bottleneck and media source. Measure queue delay, RTT variation, CE counts, loss, goodput, and encoder target rate.

## Endpoint choice

Use Pion for both sender and receiver in the initial POC. This gives control over the sender's congestion controller, the receiver's ECN observation and RFC 8888 feedback, and logging at both ends.

<img width="681" height="266" alt="image" src="https://github.com/user-attachments/assets/6622c942-f49d-425c-8fa6-fbf7aec0ba7a" />

Confirm its application, negotiated feedback, and ECN capability before using it as the receiver for a Prague experiment. The diagram alone does not establish those capabilities. Use an organizer-assigned host and address for the Pion receiver.

## Connect the peers to the test network

1. **Confirm the physical ports.** Connect the receiver host by Ethernet to the clients switch on the `latency-monitor9` branch. Connect the sender host by Ethernet to the servers switch. The operator should confirm that this pair of ports crosses monitor9's shaped path. The switches carry the packets; no Pion API runs on a switch.
2. **Obtain host addresses.** Use the lab's DHCP service or the operator's assigned IP address, subnet, and gateway. Do not copy `.212` onto a second host. `192.168.1.71` labels monitor9 in the diagram and is not the Pion receiver's address or an assumed gateway.
3. **Check reachability on Ethernet.** Inspect each host's address and the route to the other host. Allow the selected ICE UDP port range through the host firewall. Ordinary reachability checks establish connectivity; they do not prove that the media takes the shaped path.
4. **Configure ICE for the lab.** Configure Pion's `SettingEngine` to use the test Ethernet interface and UDP host candidates. `SetInterfaceFilter`, `SetNetworkTypes`, and optionally `SetEphemeralUDPPortRange` provide the relevant controls. Use direct host connectivity when the lab permits it.
  
6. **Exchange signaling.** Run a lightweight WebSocket signaling service then exchange the SDP offer, SDP answer, and ICE candidates; alternatively, gather candidates completely before exchanging SDP. Ofcourse agree on the codec and the intended RTCP feedback.
   
8. **Verify the selected media path.** Wait for the PeerConnection to connect, then log the selected ICE candidate pair. Confirm it uses the two lab Ethernet addresses, and correlate traffic with monitor9 captures or counters. Wi-Fi, a VPN, or a relay can otherwise select a different path. Check with the operator when confirming a bridge path; traceroute may not show a transparent bottleneck.
   
9. **Run!! ** Send video from the server-side Pion host to the client-side Pion host; RTCP feedback returns across the same bottleneck. Run GCC and Prague separately with matched conditions. Validate ECT(1), received CE marks, and CE feedback for Prague. Use a rate-adaptable source, or repeatable media representations selected by the target rate, if measuring encoder adaptation.

The supplied topology labels monitor9 as **20 Mbps with 10 ms added in each direction**. Confirm the live settings with the operator. The configured delay adds roughly 20 ms to RTT before queuing. Put competing traffic through monitor9 as well: the Netflix branch through monitor10 has its own bottleneck.

Uses: 
* webrtc/v4 v4.2.26 and interceptor v0.1.51

Steps: 
Step 1 : get a GCC baseline with a laptop receiver, HTTP signaling, and a sender that reads VP8 video. Prague and outer-IP ECN support will remain explicit integration work; the starter won’t claim to be an L4S implementation. 

## UDP-Prague endpoint mapping

See [the Pion-to-UDP-Prague comparison](docs/pion-l4s-interop/udp-prague-pion-mapping.md) for how the example sender and receiver map to the planned WebRTC endpoints, and which feedback and ECN adaptations are required.

Track the implementation work in [todo/README.md](todo/README.md).

## Planned components

- Pion sender and receiver, starting from [Pion's example applications](https://github.com/pion/example-webrtc-applications).
- RTP/RTCP processing using [`pion/interceptor`](https://github.com/pion/interceptor).
- Explicitly configured optional Pion GCC baseline, with bitrate adaptation connected to the source and pacer.
- [`UDP-Prague`](https://github.com/L4steam/udp_prague) integration, including packet accounting and a feedback adapter.
- ECT(1) marking and CE observation through the outer ICE/UDP transport, preserving per-packet metadata through the receiver path.
- RFC 8888 feedback and the required ECN negotiation and validation. Feedback-format support alone does not establish end-to-end ECN support.
- Endpoint logs and operator-provided bottleneck telemetry for the comparison metrics.

## Contribution workflow

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the local repository and upstream workflow. Use a feature branch based on upstream `main` and submit changes through a pull request.

## References

- [RFC 9330: L4S architecture](https://www.rfc-editor.org/rfc/rfc9330.html)
- [RFC 9331: L4S ECN](https://www.rfc-editor.org/rfc/rfc9331.html)
- [RFC 9332: DualQ coupled AQM](https://www.rfc-editor.org/rfc/rfc9332.html)
- [RFC 8888: RTP congestion-control feedback](https://www.rfc-editor.org/rfc/rfc8888.html)
- [RFC 9768: TCP AccECN](https://www.rfc-editor.org/rfc/rfc9768.html), implemented separately from the WebRTC endpoint
- [Pion ICE and network configuration](https://github.com/pion/webrtc/blob/main/settingengine.go)
