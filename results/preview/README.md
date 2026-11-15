# Recorded local GCC preview

The later [Prague local snapshot](prague-local-20261114.json) records a six-second
Prague/CCFB loopback call after the integration: the receiver's last sample has
358 RTP packets, all ECT(1), and zero CE; the sender's last sample has 205 CCFB
reports. These samples have separate timestamps. Go tests, race checks, vet,
eight Python runner tests, and local GCC/CCFB and GCC/TWCC calls pass. This is
local evidence. The physical testbed run is awaiting a separate sender host.

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
