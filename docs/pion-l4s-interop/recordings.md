# SDP and Pion feedback recordings

The endpoint can retain the exact offer/answer and observe the actual decoded
RTCP handoff to its congestion controller. This records a new call; an earlier
call's SDP cannot be reconstructed from its periodic metrics.

## Files saved for each peer

`scripts/testbed.py run` now passes `--record-dir` automatically. Its artifacts
are under `results/raw/<run-id>/<role>/recording/`:

| File | Content |
| --- | --- |
| `local-offer.raw.sdp` / `remote-answer.raw.sdp` | Sender's exact descriptions. |
| `remote-offer.raw.sdp` / `local-answer.raw.sdp` | Receiver's exact descriptions. |
| Corresponding names without `.raw` | Copies with ICE username/password redacted. |
| `session-trace.jsonl` | Ordered, timestamped SDP, ICE and feedback observations. |

Files are created with owner-only permissions and never overwrite an earlier
recording. The containing run directory is ignored by Git. Exact SDP includes
live ICE credentials; use the redacted copies when sharing. Those copies still
contain host addresses and DTLS fingerprints needed to inspect this lab call.

The trace stops after 64 MiB and writes an explicit `trace_truncated` marker.
Check for this event before treating a recording as complete. A disk/encoding
error is returned visibly. Detailed recording adds processing overhead, so
record that setting when comparing performance runs.

## What Pion does with the feedback

1. **At the Prague receiver**, the socket remembers outer-IP ECN and arrival
   time, keyed by RTP identity. After Pion authenticates SRTP, the custom
   interceptor correlates that metadata with SSRC/sequence and adds it to Pion's
   `ccfb.Recorder`.
2. **The receiver builds RFC 8888 reports.** `ccfb_generated` records the report
   fields; `ccfb_written` records a successful write into the outgoing RTCP
   path. The report is protected as SRTCP before network transmission.
3. **At the sender**, Pion decrypts SRTCP. Its `cc.BindRTCPReader` calls
   `Attributes.GetRTCPPackets` and passes the parsed packets to
   `BandwidthEstimator.WriteRTCP`. The recording decorator observes this actual
   call and delegates it exactly once to the selected estimator.
4. **Prague processes CCFB metric blocks**, matching SSRC/sequence to its sent
   history, accounting newly received/lost packets and CE, recovering late loss,
   and avoiding duplicate accounting. It derives RTT samples using the report's
   arrival-time offsets and updates the native UDP-Prague state.
5. **The pacer uses the updated rate/window.** The prerecorded source still has
   no adaptive encoder. Other RTCP types, such as Receiver Reports, pass through
   the same dispatch but do not update Prague's CCFB packet accounting.

Each `controller_feedback` event includes:

- `packet_types` and parsed `packets`, including CCFB SSRC/sequence, received
  flags, ECN codepoints and arrival offsets (units: 1/1024 second).
- `state_before_dispatch` and `state_after_dispatch`, including Prague's
  received/lost/CE counters, in-flight bytes, rate, packet window, RTT and alpha.
- `outcome` and `handling_error`, preserving controller errors.

`returned_without_error` means the handler returned successfully; it does not
mean every metric was newly acknowledged. Duplicate and unrelated reports can
leave packet counters unchanged. Snapshots are not atomic with concurrent RTP
sends or the controller clock, so a rate change alone is not proof of CE response.

The SDP summary lists advertised codecs/feedback, not proof that all advertised
mechanisms were used. The actual received codec and parsed RTCP types identify
what the call used. Prague's capability agreement is still the project's paired
`/healthz` check; general SDP ECN negotiation remains unimplemented.

## New local recording, 14 November 2026

Three fresh six-second loopback checks completed with recording enabled:

| Controller / feedback | Parsed feedback reports | Additional Receiver Reports |
| --- | ---: | ---: |
| Prague / CCFB | 226 | 6 |
| GCC / CCFB | 59 | 6 |
| GCC / TWCC | 59 | 6 |

Each peer's offer/answer matched its counterpart byte-for-byte. For Prague,
all 226 generated CCFB report bodies also matched the sender's decoded reports.
The receiver's last sample recorded 352 ECT(1) RTP packets and no CE. One actual
CCFB dispatch advanced Prague's received count from 0 to 2 and released its
2,488 in-flight bytes; CE stayed zero. The report was accepted, with an RTT
state of 2,017 microseconds after that dispatch. This is a local observation,
not a new physical bottleneck result or CE-response benchmark.

Review the retained, credential-redacted [offer](../../results/preview/session-recording-local-20261114/offer.sdp),
[answer](../../results/preview/session-recording-local-20261114/answer.sdp),
[actual feedback dispatch](../../results/preview/session-recording-local-20261114/feedback-example.json),
and [recording summary with hashes](../../results/preview/session-recording-local-20261114/summary.json).

The older 30-second physical call established two CE observations reaching
Prague, but did not retain SDP or a per-report trace. A fresh instrumented
physical call is pending server access; unattended SSH to the `.216` host was
rejected. Do not label these new loopback artifacts as physical-testbed evidence.

## Run with recording

Build the updated source on both hosts before using the changed runner. A fresh
testbed run ID creates fresh recording paths automatically:

```sh
# Laptop receiver, using the shared edited testbed/local.json
bash scripts/build.sh
python3 scripts/testbed.py run --role receiver --run-id prague-sdp-01

# Separate sender host, after deploying/building the same updated source
python3 scripts/testbed.py run --role sender --run-id prague-sdp-01
```

For another local validation:

```sh
python3 scripts/local-smoke.py --controller prague --video media/sample.ivf
```

Local-smoke artifacts use `sender-recording/` and `receiver-recording/` under
the printed run directory. Direct binary use can select an explicit private
directory with `--record-dir results/raw/<unique-run>/<role>/recording`.
Endpoint recording does not start a bottleneck packet capture. Use the separate
operator-approved monitor workflow when that evidence is required.
