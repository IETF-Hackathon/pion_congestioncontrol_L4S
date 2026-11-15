# Prague controller and ECN lab integration

The `feat/prague-cc-testbed` branch implements `--controller prague` on both
Pion peers. This is an experimental integration for the direct IPv4 lab path.
The physical `lld-black4` experiment still needs a separate server-side host;
reachable management SSH services are not a substitute for that host.

## Controller and packet path

- `internal/prague/native/` vendors the unmodified Apache-2.0 UDP-Prague core
  at commit `46986fb95f1fdd17bf6225a0afb35f073eb7b7d9`.
- A small cgo wrapper uses continuous mode, disables optional startup, and
  supplies a monotonic clock, positive RTT samples and cumulative packet counters.
  Controller rates are bytes/s internally and bits/s at the application API.
- The sender implements Pion's `cc.BandwidthEstimator`. It serializes RTP writes
  against the reference pacing rate and a conservative byte window derived from
  `packet_window * packet_size`. One packet is sent per pacing opportunity, within
  the reference burst ceiling. Existing RTP packetization is retained; this is a
  byte-window adaptation, not a reproduction of the example's packet-size loop.
- Writes block instead of collecting an unbounded application packet queue.
  Acknowledged/lost history is retained for one second for duplicate/late report
  correction. The history is capped at 32768 entries, below half the RTP sequence
  space. Old unreported packets are declared lost while fresh feedback is arriving.
- A dedicated UDP socket is bound to the selected lab IP and a port in the
  configured range, then supplied to Pion's ICE UDP mux. The socket serializes
  TOS updates, preserves DSCP, and marks only SRTP data ECT(1). STUN, DTLS and RTCP
  use Non-ECT. The current transport is for macOS/Linux IPv4 UDP host candidates.
- The receiver reads ancillary `IP_RECVTOS` metadata and the arrival time before
  ICE/SRTP processing. It correlates these with the visible RTP SSRC/sequence,
  consumes them only after SRTP authentication, and reuses Pion's CCFB recorder.
  Reports contain the observed ECN bits, rather than the stock generator's zero.
- The sender subtracts each packet's RFC 8888 arrival-time offset from elapsed
  send-to-feedback time to obtain an RTT sample. Peer clocks need not match.
  Duplicate feedback does not increment counters again; a late received report
  can undo provisional loss. CE counts and controller alpha/rate/RTT are logged.

GCC is instantiated only for `--controller gcc`. A Prague sender checks the
receiver's `/healthz` for matching Prague/CCFB/ECN support before signaling.
This is explicit agreement between this project's two lab applications, not
general RFC 6679 SDP ECN negotiation. Arbitrary browser interoperability and
full RFC conformance remain unverified.

Non-ECT/ECT(0) feedback, two seconds without fresh feedback, missing ECN metadata,
or history exhaustion ends the media run with an error. This integration stops
on ECN failure instead of dynamically switching controllers in the same session.

## Build and local verification

Install Go 1.24+, a C++11 compiler and cgo support. macOS uses Clang/libc++;
Linux needs `g++`/libstdc++. The scripts allow `-fwrapv` for the reference core's
signed timestamp/counter arithmetic.

```sh
bash scripts/build.sh
bash scripts/check.sh
bash scripts/make-media.sh  # once, if media/sample.ivf is absent
python3 scripts/local-smoke.py --controller prague --video media/sample.ivf
```

For direct Go commands use `CGO_ENABLED=1 CGO_CXXFLAGS_ALLOW=-fwrapv` and
`GOWORK=off`. Loopback checks create evidence under ignored `results/raw/`.

Tests exercise CE-driven rate reduction, RTP sequence wrap, duplicates, late loss
recovery, invalid ECN, feedback timeout, shutdown, and actual socket ECN values
surviving RFC 8888 serialization. Local VP8 transfer has also been checked with
both Prague/CCFB and the GCC/CCFB baseline. Local CE tests do not establish
DualPI2 marking or performance.

## Physical testbed run

Use the [two-host guide](testbed.md). Copy `testbed/lld-black4.example.json` to
`testbed/local.json`, set `controller` to `prague`, leave `feedback` as `ccfb`,
and fill both host addresses and interface names. Use the same config/source
on both hosts. A receiver must also run with `--controller prague`.

```sh
# Receiver laptop, behind lld-black4
python3 scripts/testbed.py plan
python3 scripts/testbed.py doctor --role receiver
python3 scripts/testbed.py run --role receiver --run-id prague-01

# Separate sender host on the servers switch
python3 scripts/testbed.py doctor --role sender
python3 scripts/testbed.py run --role sender --run-id prague-01
```

On 14 November the laptop had active `en7`, address `192.168.1.79`; the user
confirmed attachment behind lld-black4 with DualPI2/SRM. The exact active
qdisc and shaping settings still need to be recorded for measured results.
No sender IP/login has been supplied. The diagram's `.75`, `.71`, `.51` and
`.1` answer SSH but identify network equipment. The existing `.212` endpoint
did not answer SSH/HTTP during a bounded reachability check. Do not infer a
server-side compute host or valid credentials from these observations.

The fixed 500 kbps sample establishes transport at light load. A controller
target can exceed offered media rate on this application-limited source; it is
not measured link capacity. Frame production can slow while the synchronous
Prague pacer waits. Adaptive encoding, bounded latency under changing capacity,
loaded-link fairness, queue telemetry and complete POC metrics remain work to do.
No physical Prague testbed result is claimed.
