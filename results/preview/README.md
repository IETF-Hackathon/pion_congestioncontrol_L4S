# Recorded transport previews

The [explicit system 2 repeat](new-pion-v2-repeat-25m-20261115T193712Z.md) confirms that “New Pion: implementation under validation” is the implemented Linux timerfd/byte-credit scheduler used in the earlier three Prague trials. One fresh, separately captured repeat of that unchanged build measured 14.036 Mbps monitor frame throughput, 12.714 ms P99 transit, zero unmatched RTP identities and 2,216 capture CE marks versus 2,213 at the last sender feedback state. It is not pooled into the earlier cohort.

The [new pacing comparison](pion-prague-pacing-v2-25m-20261115T190910Z.md) contrasts three captured Prague trials with the saved three-trial GCC baseline. Mean steady frame throughput was 13.120 Mbps for new Prague and 18.357 Mbps for GCC; the earlier single Prague observation was 8.856 Mbps. New Prague had zero unmatched RTP identities between taps and lower observed transit tails. See the [plot](pion-prague-pacing-v2-25m-20261115T190910Z.png), [JSON](pion-prague-pacing-v2-25m-20261115T190910Z.json) and [CSV](pion-prague-pacing-v2-25m-20261115T190910Z.csv) for per-run variation, CE feedback tails, waiting metrics and the unverified network/background-traffic limits. Frame throughput includes headers; these are exploratory transport results.

The [four-rate captured sweep](prague-rate-sweep-20261114.json) ran the requested
0.5, ~15, ~25 and ~35 Mbps source levels in order, with a separate 30-second sender
and 60-second lld-black4 capture for each. View the [comparison PNG](prague-rate-sweep-20261114.png)
or [CSV](prague-rate-sweep-20261114.csv); each row links to its dashboard folder and
flow plot. Monitor frame rates were 0.537, 9.053, 9.111 and 9.025 Mbps; receiver
and monitor CE counts were 2, 0, 25 and 165. These are source-rate steps: the three
higher levels did not reach their requested rates on the network.

The ~25 and ~35 Mbps receivers logged closed-pipe errors during peer shutdown,
leaving their periodic packet totals short of the complete capture. The ~35 case
also had 15 monitor-reported forward UDP drops. Most of its 165 CE marks arrived
after the last sender metrics snapshot, which recorded 1 CE; full feedback
processing is therefore unverified. These qualifications and per-run hashes are
retained in the JSON. Background traffic was not controlled, so differences in
CE count cannot be attributed solely to the nominal source levels.

The later [captured higher-load Prague run](prague-load-captured-20261114.json)
completed at 17:56–17:57 PST on 14 November. A 28.9 Mbps nominal fixed VP8 source
and 25 Mbps initial controller target produced about 8.5 Mbps received RTP
payload throughput. The monitor reports 9.039 Mbps for the full UDP frame flow,
27,166 ECT(1) media packets, zero CE, and median/P99 transit delays of
10.014/10.069 ms. These rates use different byte accounting and time windows.
The exact media flow was recorded across lld-black4's CMCI and NSI capture
interfaces. View the [local chart](prague-load-captured-20261114.png) or the
[dashboard result](http://lld-black4.local/data/prague-load-30m-20261115T015241Z_2026_11_15_02_56/).
The transit delay includes the fixed path delay; it is not pure queue delay.
Actual delivery stayed below the diagram's 20 Mbps capacity, and sustained
congestion response, the sender-rate limit, live qdisc settings and adaptive
encoding remain unvalidated. The JSON preserves failed preparation attempts
separately from this successful run and records raw-artifact hashes.

The [physical Prague run](prague-lab-selabs216-20261114.json) records the 30-second
call at 17:00 PST on 14 November: sender `192.168.1.216` / `enp1s0`, Mac receiver
`192.168.1.79` / `en7`. Receiver counters reached 2,078 RTP (2,076 ECT(1), 2 CE,
0 Non-ECT); the sender's last sample recorded both CE marks in Prague state and
924 feedback reports. The sender exited normally and the receiver was stopped
afterward. Raw logs and metadata are retained under the matching ignored
`results/raw/` run folder; the JSON records hashes and independent sample times.
The [current topology](../../docs/images/pion-current-lab-setup.md) shows the
user-confirmed placement and missing validation. This light-load check establishes
endpoint transport and ECN feedback. It does not independently prove lld-black4
traversal, live shaping settings, the marking device or congestion performance.

## Earlier local runs

The later [Prague local snapshot](prague-local-20261114.json) records a six-second
Prague/CCFB loopback call after the integration: the receiver's last sample has
358 RTP packets, all ECT(1), and zero CE; the sender's last sample has 205 CCFB
reports. These samples have separate timestamps. Go tests, race checks, vet,
eight Python runner tests, and local GCC/CCFB and GCC/TWCC calls pass. This is
local evidence. The later physical result is recorded separately above.

[`local-gcc-20261114.json`](local-gcc-20261114.json) summarizes the two GCC
transport checks completed on 14 November 2026 around 15:00 PST. The single
project page displays these observations in its [run preview](../../index.html#preview).

| Run | Last logged RTP packets | Last logged congestion-feedback reports | Last logged GCC target |
| --- | ---: | ---: | ---: |
| GCC + RFC 8888 CCFB | 331 | 39 | 1,444,751 bps |
| GCC + TWCC | 327 | 39 | 1,444,527 bps |

Both used two processes on the same Mac, direct IPv4 UDP ICE candidates on
`lo0` / `127.0.0.1`, and a five-second sender process limit including setup.
The fixed VP8 IVF input was nominally 500 kbps at 640×360 and 30 fps.

These are last periodic log samples, not final packet totals. Sender and
receiver samples have independent timestamps. The JSON preserves the individual
samples and selected ICE pairs. The payload-rate range includes startup;
payload throughput is not deduplicated application goodput. A GCC target is
not an encoder bitrate or a validated capacity measurement.

The summary identifies Go/Pion versions, the actual Altanai fork replacements,
the binary/media hashes, source-file hashes and raw-log hashes. The historical
raw logs, original input clip, and build information are retained in this checkout under
`results/raw/local-gcc-20261114/`, which remains ignored by Git. These artifacts
were preserved from the existing session runs; creating this preview did not
rerun media or produce a new experiment.

**The GCC testbed baseline has not been run.** No DualPI2 bottleneck or competing
flow was present in these local checks. Prague and outer-IP ECN were not yet
implemented when these historical GCC runs were recorded; see the current
[Prague implementation](../../docs/pion-l4s-interop/prague.md). Null POC metrics mean unavailable, not zero. The main experimental
results table stays blank until the relevant measurements are collected.

Use the [testbed guide](../../docs/pion-l4s-interop/testbed.md) for the next
two-host run. First obtain the host addresses/interfaces, prepare identical
source and config on both hosts, and start the laptop receiver before the
server-side sender. New runs do not automatically update this historical JSON
or the static HTML preview.
