# Mapping UDP-Prague to the Pion WebRTC endpoints

Implementation update: the experimental Prague adapter, synchronous pacer and
IPv4 ECN/CCFB path are now implemented. See [Prague implementation](prague.md)
for the pinned source, current limitations and testbed prerequisites. The
comparison below explains the architecture; physical lab validation is pending.

## Short answer

The planned Pion sender and receiver have the same congestion-control roles as
the `udp_prague` example, but they are not wire-compatible implementations of
its example protocol. Pion carries RTP/RTCP over WebRTC's ICE/UDP transport;
`udp_prague` demonstrates a custom UDP data and acknowledgement protocol. The
Prague controller can be reused or adapted, while the packet accounting,
feedback translation, pacing integration, and IP ECN handling need WebRTC-
specific glue.

## Endpoint mapping

| Function | `udp_prague` example | Planned Pion WebRTC endpoint |
| --- | --- | --- |
| Sender controller | `PragueCC` supplies pacing rate, packet window, burst, packet size, and ECT marking choice. | A Prague adapter consumes receiver feedback and drives the RTP sender's rate, pacer, and media-source/encoder target. Disable any competing controller for this stream. |
| Sender packet tracking | Monotonic packet sequence number, send timestamp, and in-flight accounting are carried by the example data messages. | Track the sequence/timing information needed to correlate RTP packets with feedback. Keep RTP/RTCP sequence spaces and Prague's controller counters distinct and map between them explicitly. |
| Receiver accounting | The receiver reads each UDP datagram's IP ECN value and sequence number, calls `DataReceivedSequence`, and obtains cumulative ACK information or RFC 8888 reports. | The Pion receiver/interceptor processes RTP and RTCP feedback. It also needs access to the outer ICE/UDP packet's ECN value; RTP feedback alone does not reveal the IP ECN field. |
| Feedback path | Example ACK messages return timestamps, received/CE/lost counters, and an L4S error flag. The repository also defines an RFC 8888 ACK format. | Negotiate and send RTCP RFC 8888 CCFB reports, then translate their packet reception and ECN reports into Prague controller inputs. Carry RTT timing consistently. |
| ECN marking | The UDP socket sends ECT(1); the receiver observes CE and reports invalid/bleached ECN. | The ICE/UDP transport must mark outgoing datagrams ECT(1) and expose received CE marks to the receiver feedback logic. Verify this in the selected Pion ICE/transport version and operating system. |

## What is similar

Both designs have one sender-side control loop and one receiver-side measurement
loop. The receiver observes delivery, loss, timing, and ECN; feedback travels
back to the sender; the sender updates Prague and applies its output to packet
pacing and offered media rate. Both need sequence/timing correlation and a
working ECT(1) → CE → feedback path for an L4S run.

## What is different

`udp_prague` is a runnable UDP transport example with its own message formats,
sequence numbers, ACK cadence, socket ECN operations, and packet-window loop.
Pion is a WebRTC media stack. Its application sends RTP, negotiates RTCP
feedback, and sends media through ICE; it does not exchange the `udp_prague`
example's `datamessage_t`/`ackmessage_t` packets. The Pion receiver should
therefore report RTP delivery using RTCP feedback and a Prague adapter, rather
than pretending that the custom UDP ACK messages are WebRTC packets.

RFC 8888 is a particularly useful bridge because `udp_prague` includes an
RFC-8888 feedback path. That does not make the systems plug-compatible: Pion
must negotiate and parse the relevant RTCP feedback, maintain the packet
identity/timing mapping expected by the Prague implementation, and make outer
IP ECN metadata available. Confirm the exact report semantics against the
chosen revisions before wiring counters into `PragueCC`.

## Suggested integration boundary

1. Keep the existing Pion RTP/RTCP and signaling paths.
2. Add a sender-side Prague adapter that accepts parsed feedback, calls the
   appropriate Prague API, and applies rate/pacing outputs to the RTP sender
   and media source.
3. Add receiver-side packet accounting that associates incoming RTP packets
   and outer IP ECN observations with the feedback reports sent to the sender.
4. Add ICE/UDP ECN marking and observation as a separate transport task; do
   not infer CE support from RTCP negotiation.
5. Log sequence/rate/RTT/CE/loss values at both endpoints so the adapter can be
   checked against the UDP example's control-loop behavior.

The current controller and local checks are documented in [prague.md](prague.md).
Physical testbed validation and media source adaptation remain pending.

## Sources

- [UDP-Prague repository](https://github.com/L4steam/udp_prague)
- [UDP-Prague sender](https://github.com/L4steam/udp_prague/blob/main/udp_prague_sender.cpp)
- [UDP-Prague receiver](https://github.com/L4steam/udp_prague/blob/main/udp_prague_receiver.cpp)
- [UDP-Prague packet formats](https://github.com/L4steam/udp_prague/blob/main/pkt_format.h)
- [UDP-Prague congestion-control API](https://github.com/L4steam/udp_prague/blob/main/prague_cc.h)
