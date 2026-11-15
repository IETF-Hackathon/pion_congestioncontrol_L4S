# Repository tools

All current sender preparation, source generation, deployment and capture logic is here. The root `run-testbed.sh` is a compatibility entrypoint into `scripts/run-testbed.sh`. Commands resolve paths from this checkout.

| Tool | Purpose |
| --- | --- |
| `make-media.sh`, `media_source.py` | Generate a named fixed VP8 source; inspect actual rate, frame bounds and SHA-256. Save exact FFmpeg command/version in a generation manifest. |
| `equal_frame_source.py` | Encode one valid keyframe, repeat identical bytes at a fixed cadence, validate decoding, and retain the bounded quality-search recipe. |
| `testbed.py prepare` | Create a separate profile config while retaining assigned interfaces, IPs and ports. |
| `testbed.py plan/doctor/run` | Print, validate or execute one endpoint. Sender doctor checks the actual media rate before signaling. |
| `run-testbed.sh` | Select Go and run the endpoint helper, `build` or `check`. `PION_GO_BIN` optionally names a Go bin directory. |
| `build.sh`, `source_manifest.py` | Build with pinned dependencies and record/verify the executable plus compiled-source hashes. |
| `sender.py plan/deploy` | Plan or copy/build a frozen source/config/media snapshot in a fresh Linux release directory. |
| `capture_testbed.py plan/run` | Plan offline or execute one two-host call with one confirmed monitor capture and bounded cleanup. |
| `run_equal_frames.py`, `analyze_equal_frames.py` | Execute exactly two captured equal-frame Prague trials; then analyze both retained taps, RTP frame shape, CE feedback and sender waits offline. |
| `local-smoke.py` | Short loopback transport check; no physical bottleneck. |
| `server.py` | Source bundles and the optional owned Lima development VM. |
| `download_capture_pdfs.py` | Verify/retrieve selected flow PDFs from an explicit offline manifest. |
| `check-preview.mjs` | Desktop/phone HTML QA; requires Node with WebSocket support and Chrome. Set `PION_CHROME` for another executable. |
| `repo_inventory.py` | Inspect references and reversibly archive unreferenced old artifacts; `plan` moves nothing. |
| `recorded/2026-11-15/` | Exact scripts actually used for the morning runs and analysis, with a hash/provenance inventory. |

## Prepare a real high-load source

```sh
bash scripts/make-media.sh --profile load25m --dry-run
bash scripts/make-media.sh --profile load25m
python3 scripts/media_source.py inspect media/load25m.ivf --min-bps 21250000
```

Profiles are `smoke` (500 kbps, 360p30, ten seconds) and `load15m`, `load25m`, `load35m` (noisy 720p60, two seconds). Existing files and manifests are never overwritten. The generator validates all frames against the sender's 32 MiB file / 1 MiB frame limits and requires actual payload bitrate within 15% of the requested profile.

The high-load profiles use the recipe recovered from the original rate sweep. See [historical source provenance](../testbed/media-provenance/historical-25m.json). Its original 25 Mbps-target clip measured 23.950988 Mbps. A new local test of the same recipe measured 23.949292 Mbps with a different hash. Noise and encoder versions can change bytes: generate once and use identical file hashes across matched controller runs.

FFmpeg runs before the call. Pion reads and loops the encoded IVF. `--initial-bitrate 25000000` initializes congestion control; it is not an encoder and cannot increase a 500 kbps file to 25 Mbps.

## Equal-size frames with normal Prague

CBR encoder settings do not require equal frame lengths. The original noisy 720p60 clip has 120 frames ranging from 24,789 to 644,064 bytes, including two large keyframes. Pion's sender passes each encoded frame to `WriteSample`; it does not choose keyframes, resize frames, or re-encode video. RTP packet lengths can differ from encoded-frame lengths.

```sh
python3 scripts/equal_frame_source.py --output media/equal25m.ivf
python3 scripts/testbed.py prepare --config testbed/local.json \
  --profile load25m --controller prague --video media/equal25m.ivf \
  --output-config testbed/equal25m-prague.local.json
```

The generator repeats one valid encoded keyframe byte-for-byte in an IVF container, retaining the exact FFmpeg search commands and version and decoding the assembled clip before accepting it. This is an artificial static all-keyframe workload, not moving-video quality validation. `prepare` locks an existing equal-size input's frame length into `source_expectation.equal_frame_bytes`; both sender doctor and the capture coordinator reject a later mismatch. Never overwrite a working media file or config; choose fresh names if they exist.

The recorded source is 54,465 bytes per frame at nominal 60 fps: **26.143200 Mbps** encoded payload, near the requested 25 Mbps, rather than exactly 25 Mbps. The controller starts at 25 Mbps. Normal Prague still uses ACK/RTT, CE, rate probing and loss response; no live encoder adaptation is present, and actual source cadence can fall under backpressure.

Deploy this config using `sender.py deploy` as below, then use its printed release root:

```sh
python3 scripts/run_equal_frames.py --config testbed/equal25m-prague.local.json \
  --host selabs@192.168.1.216 --remote-root PRINTED_RELEASE_ROOT \
  --control-path /private/tmp/pion-pacing-lab.ctl \
  --go-bin /home/selabs/.local/share/pion-toolchains/go1.27.4/bin
python3 scripts/analyze_equal_frames.py --batch results/raw/PRINTED_BATCH_ID
```

The first command starts exactly two sequential 30-second sender / 60-second capture trials. Each has a fresh receiver; an ambiguous or failed attempt stops the batch without retrying. The second command is offline and reads only this batch's saved artifacts. [Recorded two-trial result](../results/preview/pion-equalframes-25m-20261115T212125Z.md) · [source provenance](../testbed/media-provenance/equal25m-20261115.json).

## Make matched configs

First copy `testbed/lld-black4.example.json` to `testbed/local.json` and fill actual operator-assigned hosts/interfaces. Preserve an existing working local config. Then:

```sh
python3 scripts/testbed.py prepare --config testbed/local.json \
  --profile load25m --controller prague --output-config testbed/load25m-prague.local.json
python3 scripts/testbed.py prepare --config testbed/local.json \
  --profile load25m --controller gcc --output-config testbed/load25m-gcc.local.json
python3 scripts/testbed.py plan --config testbed/load25m-prague.local.json
```

The profile selects `media/load25m.ivf`, a 25 Mbps initial target and a 30-second call. Its source-rate guard rejects a 500 kbps file before a capture. The sender records measured source properties and the generation command/version when the sidecar matches. To reuse the exact retained historical clip, add `--video results/raw/pion-compare-25m-20261115T172333Z/media/25m.ivf` to **both** prepare commands. A new generation is not the historical file.

## Build and prepare the Linux sender

```sh
bash run-testbed.sh build
python3 scripts/sender.py plan --config testbed/load25m-prague.local.json \
  --host selabs@192.168.1.216 --remote-root /home/selabs/pion-l4s-interop
python3 scripts/sender.py deploy --config testbed/load25m-prague.local.json \
  --host selabs@192.168.1.216 --remote-root /home/selabs/pion-l4s-interop \
  --go-bin /home/selabs/.local/share/pion-toolchains/go1.27.4/bin
```

Use your actual assigned sender and installed Go directory. Authenticate SSH in Terminal first. `--ssh-config` and `--control-path` can reuse that session; no password/askpass script is bundled. Deployment prints a **new release root** and stores its source/binary/media hashes in `results/raw/sender-release-<UTC>/manifest.json`. Use that printed root for the capture command. Existing installed source/binaries are preserved. Deployment builds but does not send media.

## One captured physical run

Substitute the exact release root printed by deploy:

```sh
python3 scripts/capture_testbed.py plan --config testbed/load25m-prague.local.json \
  --host selabs@192.168.1.216 --remote-root /home/selabs/pion-l4s-interop/results/raw/RELEASE_ID/source
python3 scripts/capture_testbed.py run --config testbed/load25m-prague.local.json \
  --host selabs@192.168.1.216 --remote-root /home/selabs/pion-l4s-interop/results/raw/RELEASE_ID/source \
  --go-bin /home/selabs/.local/share/pion-toolchains/go1.27.4/bin --collect-pcaps
```

`plan` performs no networking. `run` requires fresh matching builds and identical media on both hosts, an unused receiver, and an idle monitor. It creates one fresh per-run config, starts its own receiver, confirms one 60-second monitor capture before launching the 30-second sender, saves SDP/CCFB and logs, collects the matching result, and stops only its own processes. It never retries an ambiguous capture start or changes network settings. Optional PCAP downloads happen after analysis. The result manifest contains selected ICE identity, matching downstream summary, exact dashboard/flow URLs and outcomes. Qdisc/capacity verification remains separate.

Repeat using the GCC config and a fresh run ID/receiver/capture. The same deployment can serve both configs if the source/build hashes still match; the helper copies each run's config. Historical GCC had different pacing and feedback defaults, so keep the [RCA limitations](../results/preview/pacing-throughput-rca-20261115.json) when interpreting results.

## Today's actual tools and archival policy

The [recorded tool inventory](recorded/2026-11-15/manifest.json) preserves the exact comparison, baseline, Prague-v2, repeat, capture, cross-traffic, RCA and preview scripts used on 15 November (America/Los_Angeles). Those copies intentionally retain historical constants and data locations for audit; use the current entrypoints above for new runs. Raw SDP/credentials, SSH sockets, binaries, generated media and packet data are not part of that script collection.

Historical evidence referenced by the HTML, reports or active configs is still used and remains in place. Unreferenced old attempts/downloads and superseded tools are mapped in [the cleanup manifest](../archive/cleanup-20261115.json). Private raw archives live in ignored `archive/local/`.
