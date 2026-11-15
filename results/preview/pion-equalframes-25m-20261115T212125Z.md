# Equal-frame new-pacer Prague trials

Every source frame: **54,465 bytes**, byte-identical VP8 keyframe, nominal 60 fps; offered encoded payload **26.143200 Mbps**. Initial controller target 25 Mbps. No live encoder adaptation.

| Trial | Steady frame Mbps | Transit P50 / P99 ms | Missing RTP | Capture CE / sender CE | Provisional sender loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| [1](http://lld-black4.local/data/pion-equalframes-25m-20261115T212125Z-r1_2026_11_15_22_21/) | 13.455 | 10.452 / 13.054 | 0 | 2620 / 2620 | 30 |
| [2](http://lld-black4.local/data/pion-equalframes-25m-20261115T212125Z-r2_2026_11_15_22_22/) | 13.970 | 10.517 / 13.031 | 0 | 2326 / 2322 | 29 |

Mean steady frame rate: **13.712 Mbps**. Mean per-trial P99 transit: **13.042 ms**.

[JSON](pion-equalframes-25m-20261115T212125Z.json) · [CSV](pion-equalframes-25m-20261115T212125Z.csv)

## Sender and frame-shape evidence

- Trial 1: source 29.18 fps submitted; median applied allowance 24.172 Mbps; UDP acceptance 13.066 Mbps. Aligned pacing/window waits 14.953/11.414 s. Complete ingress frame packet counts: {'47': 866}; wire-byte totals: {'57943': 866}. CE-increasing feedback batches: 442; simultaneous rate decreases: 184. [Flow plot](http://lld-black4.local/data/pion-equalframes-25m-20261115T212125Z-r1_2026_11_15_22_21/4_downstream.pdf).
- Trial 2: source 29.81 fps submitted; median applied allowance 24.387 Mbps; UDP acceptance 13.346 Mbps. Aligned pacing/window waits 15.019/11.416 s. Complete ingress frame packet counts: {'47': 891}; wire-byte totals: {'57943': 891}. CE-increasing feedback batches: 486; simultaneous rate decreases: 209. [Flow plot](http://lld-black4.local/data/pion-equalframes-25m-20261115T212125Z-r2_2026_11_15_22_22/1_downstream.pdf).

## Limits

- Repeated static all-keyframe workload, not representative moving video
- Actual source cadence can still fall under synchronous backpressure
- Offered source bitrate differs from the historical variable-frame clip
- Active qdisc/capacity and background traffic remain uncontrolled
- Normal Prague retains CE, ACK/RTT, probing and provisional-loss response; this is not CE-only control
- Frame throughput includes headers; paired transit includes fixed delay; pure queue delay and application goodput are unavailable
- Final feedback state may omit a tail; provisional sender loss differs from missing RTP at the capture taps
- Receiver closed-pipe shutdown is retained: trial 1 exited with an error after peer close; trial 2 was interrupted for cleanup
- Each capture interface reported zero pcap/dumpcap drops and one flushed packet; zero missing media applies to retained packets
- Capture interface mapping follows the retained UI config: NSI=enp2s0, CMCI=enp3s0; qdisc validation is separate
- Earlier GCC and variable-frame Prague runs use a different source and are historical context, not matched baselines
