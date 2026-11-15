# Three pacing systems: archived Pion, new Pion, UDP Prague demo

The new Pion scheduler uses the native controller's **250 microsecond burst policy**. It does **not** run a fixed 250 microsecond ticker, and it is **not an exact port** of the demo sender's lateness compensation. The distinction matters when evaluating the earlier approximately 9 Mbps plateau.

The original runtime is preserved in [the v1 source archive](../../archive/prague-pacing-v1/README.md). New scheduling code is implemented locally; performance acceptance is still in progress. The third system below is the upstream reference, not a third selectable Pion controller or a measured testbed run.

**Which system was tested?** The three Prague trials and the [explicit fresh repeat](../../results/preview/new-pion-v2-repeat-25m-20261115T193712Z.md) used **system 2, New Pion**, verified by the frozen build hashes and recorded `pacing_backend=linux-timerfd`. “Under validation” refers to remaining performance acceptance work, not an implementation that has never run. The repeat measured 14.036 Mbps monitor frame throughput and 12.714 ms P99 transit; it remains separate from the original three-run cohort.

## Symbols and the shared native controller

| Symbol | Unit / meaning |
| --- | --- |
| `R0` | Native internal pacing rate, bytes/second; divide application bits/second by 8. |
| `R` | Applied rate returned by `GetCCInfo`, bytes/second. The native phase adjustment uses `R0 × 1.03` or `R0 / 1.03`, with integer rounding. |
| `S`, `b`, `w` | Native reference packet bytes, burst packets and window packets. |
| `L`, `Lmax`, `I` | Current accounted RTP packet bytes, largest observed accounted packet, and accounted bytes in flight. |
| `t`, `dt` | Monotonic time and elapsed time, with units stated in each formula. |

Native burst selection is:

```text
H = 250 microseconds
b = max(1, floor(R0 × 250 / 1,000,000 / S))
```

The minimum of one packet means a low-rate packet can take longer than 250 microseconds to serialize at the target rate. `GetCCInfo` supplies the burst count; Pion consumes it without recomputing it from the phase-adjusted rate. The native controller, including its CE response, remains unchanged by this scheduler patch.

Source: pinned [native burst calculation](https://github.com/L4STeam/udp_prague/blob/46986fb95f1fdd17bf6225a0afb35f073eb7b7d9/prague_cc.cpp#L493-L497) and [GetCCInfo](https://github.com/L4STeam/udp_prague/blob/46986fb95f1fdd17bf6225a0afb35f073eb7b7d9/prague_cc.cpp#L606-L618). This is also the revision used by the local vendored core, which is unchanged by this scheduling patch.

## Formulas and behavior

| Area | 1. Archived Pion: observed ~9 Mbps plateau | 2. New Pion: implementation under validation | 3. UDP Prague demo sender reference |
| --- | --- | --- | --- |
| State | Next per-packet timestamp. | Elapsed-time byte credit `Q`, bounded capacity `C`, current applied limits. | Next burst timestamp and signed receive-wait compensation `c`. |
| Byte cost | `L = RTP header + payload + 16 + 28`; estimates SRTP and UDP/IPv4, excludes Ethernet. | Same `L`, to hold accounting constant; actual UDP datagram bytes are counted separately. | Uses demo datagram `packet_size`, not Pion's RTP/SRTP cost. |
| Admission | `t >= next` and `I + L <= max(w × S, L)`. | `Q >= L × 10^9` and the same window gate. | Continuous-mode burst starts if `t >= nextSend` and packet-count `inflight < w`. At the pinned revision it then completes the burst. |
| Scheduling formula | After admission: `next = t + floor(L × 10^9 / R)` nanoseconds. | Refill `Q = min(C × 10^9, Q + R_old × dt_ns)`; debit `L × 10^9` on admission. | After `n` packets: `nextSend = startSend + max(1, c + floor(S × n × 10^6 / R))` microseconds; then clear `c`. |
| Next wait | Relative `next - now`, bounded to 20 ms; full window uses a 20 ms wait, interruptible by feedback. | Absolute deadline `t + ceil(max(0, L × 10^9 - Q) / R)` nanoseconds; feedback/close wakes and rechecks all gates. | Wait for ACKs until `nextSend`; when window-blocked uses the demo's sender timeout. |
| Burst policy | Native `b` output is not explicitly enforced; one packet per admission. | `C = max(b × S, Lmax, 1)` bytes. Whole-packet rounding is logged. Excess idle/late credit is discarded. | Up to `b` demo packets back-to-back; interval is charged for `n` sent packets. |
| Late wake | New schedule starts from the actual late time; delay is not recovered. | Elapsed credit is retained only up to `C`. With a one-packet capacity, excess lateness is still discarded. | While traffic remains in flight, accumulates `c += waitTimeout - actualNow` when late. Negative compensation shortens a subsequent interval, down to 1 µs. |
| Window difference | Byte gate checks each entire RTP packet. | Preserves that byte gate; burst credit never bypasses it. | Completing a continuous-mode burst after its initial window check can overshoot remaining packet-window space by up to `b - 1`. |
| Waiting mechanism | New Go timer per wait. | Linux: reusable `CLOCK_MONOTONIC` timerfd + eventfd/poll. macOS/portable: reusable Go timer, precision not guaranteed. | C++ socket receive loop with microsecond timeout and deadline recheck. |
| Media / feedback | Fixed VP8 IVF, RTP/SRTP/ICE and RTCP CCFB. | Same media and feedback; adds source blocking, applied-limit, wait, lateness and accepted-byte metrics. | Custom UDP data/ACK protocol with an RFC 8888 feedback option; optional real-time frame mode has separate frame timing/window logic. |

The demo's continuous-mode and real-time branches, receive loop and compensation are in the pinned [sender source](https://github.com/L4STeam/udp_prague/blob/46986fb95f1fdd17bf6225a0afb35f073eb7b7d9/udp_prague_sender.cpp#L112-L154) and [end-of-loop compensation](https://github.com/L4STeam/udp_prague/blob/46986fb95f1fdd17bf6225a0afb35f073eb7b7d9/udp_prague_sender.cpp#L283-L291). The Pion implementation above is its own bounded-credit adaptation; the native 250 µs policy alone does not make their timing algorithms identical.

## Numerical examples

Assume equal 1,200-byte packet costs, no window/source limit, no phase adjustment and ideal wakeups. These are arithmetic examples, not measured throughput:

| Target | Native `b` | Old ideal packet interval | New credit eligibility after one packet | Demo interval for a full burst, before compensation |
| --- | --- | --- | --- | --- |
| 20 Mbps | 1 | 480 µs | 480 µs | 480 µs for one packet |
| 30 Mbps | 1 | 320 µs | 320 µs | 320 µs for one packet |
| 50 Mbps | 1 | 192 µs | 192 µs | 192 µs for one packet |
| 100 Mbps, reference only | 2 | 96 µs per packet | Capacity 2,400 bytes; can release two initially, then refills continuously | 192 µs for two packets |

The local wrapper's maximum native rate is 50 Mbps, so the 100 Mbps row only illustrates the burst formula outside the current configuration. Actual RTP costs, variable packet sizes and native rate/size updates change these numbers.

Example lateness at 30 Mbps: a one-packet opportunity due at 320 µs wakes at 500 µs. The old scheduler sets its following deadline to 820 µs. The new scheduler with a one-packet cap also sets eligibility around 820 µs, because the excess 180 µs credit was discarded. The demo can carry `c = -180 µs` into its next calculation and aim at 640 µs, subject to intervening ACK processing and state changes. The new Linux backend therefore needs sufficient real timing precision; bounded tokens alone do not remove the ceiling.

For scale, one 1,200-byte packet per 1 ms produces only 9.6 Mbps on that byte basis. The historical approximately 9 Mbps monitor result is consistent with coarse packet scheduling, but this calculation is not proof of root cause or a sender-NIC measurement.

## Validation so far

Linux/arm64, Go 1.27.1, isolated owned Lima VM; 1,250 accounted bytes, fixed 30 Mbps allowance and one-packet cap. One `BenchmarkPacing` sample on 15 November 2026:

| Scheduler fixture | Admission Mbps | Mean ns/op | Allocations/op |
| --- | --- | --- | --- |
| Old algorithm (`legacy`) | 7.733 | 1,293,199 | 2 |
| New credit + portable timer | 7.059 | 1,416,616 | 0 |
| New credit + Linux timerfd | 21.26 | 470,417 | 0 |
| Actual upstream demo executable | Not measured | Not measured | Not measured |

Raw log: `results/raw/pacing-scheduler-20261115T184429Z/linux-validation.log` (ignored local artifact). These are **admission-only** measurements without Pion, UDP writes or a bottleneck, not an archived-binary media A/B. Linux unit and race tests passed for that snapshot. The result supports using a precise backend but does not meet the separate ≥25 Mbps actual-media-output gate. It establishes neither testbed latency improvement nor complete removal of the physical plateau.

The same benchmark on the actual Linux/amd64 lab sender (Intel N100, Go 1.27.4) measured **8.972 Mbps legacy**, **8.957 Mbps portable**, and **28.33 Mbps timerfd**. These are still admission-only figures. The frozen build's unit, race and vet checks passed on that sender before media transmission.

### Three physical Prague trials against the saved GCC baseline

The [captured comparison](../../results/preview/pion-prague-pacing-v2-25m-20261115T190910Z.md) used the same 23.95 Mbps VP8 source hash and 25 Mbps initial target as the three saved GCC trials. New Prague averaged **13.120 Mbps steady frame throughput** (12.970–13.294), versus **18.357 Mbps** for saved GCC (17.474–18.936) and **8.856 Mbps** for one earlier Prague run. Steady rates include packet headers and use seconds 2–27 after first ingress media.

New Prague's mean per-trial P99 transit was 13.217 ms versus GCC's 100.537 ms; transit includes fixed delay. All three Prague captures had zero unmatched RTP identities between the two taps, versus a 9.343% mean missing-media fraction in saved GCC. Current qdisc/capacity were not independently verified, background traffic was uncontrolled, and the cohorts ran at different times, so these observations do not isolate a controller-only causal effect.

The last sender feedback states recorded 15.328–15.682 seconds of pacing waits and 12.335–12.668 seconds of window waits. Median large-packet ingress gaps were about 419–428 µs, versus the earlier Prague run's approximately 1,171 µs. This supports the scheduler bottleneck hypothesis and identifies window/feedback timing as remaining work; it does not establish complete root cause. The receiver uses 20 ms CCFB batching, whose interaction with a rate-derived window needs an explicit follow-up experiment before changing either policy.

Capture CE / last sender-processed CE were **2,213/2,213**, **2,606/2,599**, and **2,478/2,476**. The two tail gaps remain unresolved; do not call the last sender state final drained accounting. All bounded senders exited successfully, two receivers reported closed-pipe shutdown errors, and capture logs report one flushed interface packet per side. Those diagnostics are retained in the per-run JSON. The unshaped ≥25 Mbps actual-media gate remains open.

Use the [scheduler design and rollout gates](pacing-scheduler-design.md) before a new physical pair. A true demo-versus-Pion run must state its different transport, packet size, feedback cadence, source and capture byte basis; do not label dummy UDP throughput as equivalent encoded-video goodput.
