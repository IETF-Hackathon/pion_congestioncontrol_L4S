# Recorded transport previews

The [equal-frame follow-up](pion-equalframes-25m-20261115T212125Z.md) records two new-pacer Prague trials with byte-identical 54,465-byte keyframes at nominal 60 fps (26.143200 Mbps offered payload). Steady media frame rates were 13.455 and 13.970 Mbps, P99 transit 13.054 and 13.031 ms, with zero missing RTP identities across the retained taps. Complete ingress frames had 47 packets / 57,943 wire bytes. Window waits remained about 11.4 seconds and source cadence fell to about 29 fps. Normal Prague ACK/RTT/CE/probing/loss behavior was preserved; provisional sender loss of 30/29 differs from capture loss. This new source differs from the historical GCC and variable-frame Prague source, so keep the two runs as a separate cohort. [JSON](pion-equalframes-25m-20261115T212125Z.json) · [CSV](pion-equalframes-25m-20261115T212125Z.csv).

The [throughput RCA source record](pacing-throughput-rca-20261115.json) and [derived sender metrics/model](pacing-throughput-rca-20261115-metrics.json) support the RCA section in the main HTML page. It confirms persistent Prague sender window waits and source backpressure, identifies different GCC pacing/feedback defaults (including 100 ms versus 20 ms CCFB), and keeps the proposed window/report-hold mechanism provisional. The [chart](pacing-throughput-rca-20261115.png) uses aligned sender counter deltas; the model results are not new testbed measurements.

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

**These historical local checks did not use the physical testbed.** The three-trial
GCC baseline and the later Prague comparisons are recorded above; the main
[Experimental results section](../../index.html#results) now summarizes their
measurements and the remaining POC gaps. No DualPI2 bottleneck or competing
flow was present in the earlier local checks. Prague and outer-IP ECN were not yet
implemented when these historical GCC runs were recorded; see the current
[Prague implementation](../../docs/pion-l4s-interop/prague.md). Null POC metrics mean unavailable, not zero.

Use the [testbed guide](../../docs/pion-l4s-interop/testbed.md) for the next
two-host run. First obtain the host addresses/interfaces, prepare identical
source and config on both hosts, and start the laptop receiver before the
server-side sender. New runs do not automatically update this historical JSON
or the static HTML preview.
