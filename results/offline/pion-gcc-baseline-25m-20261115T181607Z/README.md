# GCC baseline PDFs for offline analysis

Three GCC-only trials using the same 23.95 Mbps VP8 source and 25 Mbps initial controller target.

Each video PDF covers the selected sender-to-Mac UDP flow, including connection-control traffic on that pair. Each feedback PDF covers the reverse RTCP and connection-control flow. Flow numbers were taken from the monitor summaries rather than assumed to match in both directions.

| Trial | Video / downstream | Feedback / upstream |
| --- | --- | --- |
| 1 | [trial-1-gcc-video-downstream.pdf](trial-1-gcc-video-downstream.pdf) | [trial-1-gcc-feedback-upstream.pdf](trial-1-gcc-feedback-upstream.pdf) |
| 2 | [trial-2-gcc-video-downstream.pdf](trial-2-gcc-video-downstream.pdf) | [trial-2-gcc-feedback-upstream.pdf](trial-2-gcc-feedback-upstream.pdf) |
| 3 | [trial-3-gcc-video-downstream.pdf](trial-3-gcc-video-downstream.pdf) | [trial-3-gcc-feedback-upstream.pdf](trial-3-gcc-feedback-upstream.pdf) |

Original monitor URLs, flow identities and file SHA-256 hashes are recorded in manifest.json. These PDFs can be opened without a testbed connection. Raw captures and endpoint recordings remain in results/raw/<run-id>/ in the parent project.
