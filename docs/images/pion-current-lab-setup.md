# Current physical Pion Prague setup

![Current lab setup](pion-current-lab-setup.png)

The separate sender now runs on `selabs@192.168.1.216` (`enp1s0`), installed under `/home/selabs/pion-l4s-interop`. The Mac receiver uses `192.168.1.79` (`en7`). The user places the server at the servers switch and the Mac behind lld-black4. No VM participates in this physical run.

A 30-second run on 14 November 2026 at 17:00 PST connected these exact Ethernet ICE candidates on UDP 50000. Receiver counters reached 2,078 RTP packets: 2,076 ECT(1), 2 CE and 0 Non-ECT. The sender's Prague state recorded both CE marks. These are endpoint observations; they do not locate the marking device or independently establish the transparent forwarding path. See the [recorded run and provenance](../../results/preview/prague-lab-selabs216-20261114.json).

No additional compute host is missing. Remaining work is to verify live bottleneck settings and path with operator evidence, measure sustained CE/rate response under controlled load, connect an adaptive source, collect the full POC metrics, and run a matched GCC baseline. General ECN negotiation and dynamic fallback remain interop work. The current paired application checks ECN capability and stops on invalid ECN or feedback failure.

The source is a fixed approximately 500 kbps VP8 clip. Its offered rate does not exercise 20 Mbps capacity, and the Prague target is not a capacity measurement. The diagram's 20 Mbps / 10 ms per direction settings remain unverified live settings.

## Image provenance

Generated with the built-in imagegen tool from the supplied topology. A second edit corrected arrow directions at the servers switch; the output was visually reviewed. Original diagram: `codex-clipboard-06c525df-6f78-4ac0-8c24-0843947ecab8.png`.

## Generation prompt

```text
Use case: compositing; infographic-diagram.
Edit target: the attached original IETF127 L4S Interop network diagram. Create an updated, legible annotated overlay for the actual current Pion setup. Preserve the original network topology, node positions, equipment names and management IPs. Extend canvas at right and bottom for clear callouts; no overlapping text. Upper monitor9/monitor10 branches may be faded to give the lower active branch emphasis. White background, crisp technical diagram, large readable typography. Keep the original diagram recognizable.

Title: "Current Pion Prague lab setup"
Subtitle: "30-second two-host check completed · 14 Nov 2026"

Current endpoints:
1. At bottom-left Altanai's client, attach a green-outlined callout:
"My Mac — Pion RECEIVER"
"192.168.1.79 · en7 · Ethernet"
"HTTP signaling :8080"
"Reads ECN → RFC 8888 CCFB"
"python3 scripts/testbed.py run --role receiver"
2. Add a separate server/computer to the RIGHT of the servers switch, cabled directly to that switch, with green outlined callout:
"Lab server — Pion SENDER"
"192.168.1.216 · enp1s0 · Ethernet"
"selabs · Ubuntu 22.04"
"Prague controller + RTP pacer"
"~/pion-l4s-interop/run-testbed.sh run --role sender"
"Installed; build and checks passed"

Highlight exactly the physical LOWER branch only: server -> servers switch -> lld-black4 -> Mac using thick ORANGE directional arrows. Label "SRTP / VP8 · ECT(1)". The arrowheads point TOWARD the Mac. Add BLUE DASHED arrows in reverse Mac -> lld-black4 -> servers switch -> server, label "RTCP CCFB · received ECN". Label both ends "UDP :50000 selected". Include a small grey SSH control note "Mac → selabs@192.168.1.216 · TCP 22 (control)" without drawing any route bypassing the bottleneck.
Keep lld-black4 "192.168.1.75" and add amber note "Management address only". Label "DualPI2/SRM reported by user; verify live settings" and "Diagram: 20 Mb/s + 10 ms each way".
Router/switches are forwarding equipment; do not place sender on EdgeRouterX. No VM is in this run. Other clients' monitor9/monitor10 paths are separate bottlenecks.

Bottom green evidence panel, exact text:
"VERIFIED — endpoint transport + ECN feedback"
"30 s · 2,078 RTP received"
"2,076 ECT(1) + 2 CE · 0 Non-ECT"
"Sender Prague recorded both CE marks"
"Direct ICE: 192.168.1.216 ↔ 192.168.1.79"
"Fixed VP8 ≈ 0.5 Mb/s; this does not measure link capacity."

Bottom amber panel, exact text:
"STILL NEEDED — complete Prague experiment"
"1  Confirm lld-black4 forwarding path, live qdisc and shaping with operator captures/counters."
"2  Apply controlled load on this same branch; measure sustained CE and rate response."
"3  Connect controller target to an adaptive encoder or rate-controllable source."
"4  Record queue delay, RTT variation, loss, goodput and encoder target; run matched GCC baseline."
"5  General WebRTC interop: standards-based ECN negotiation and fallback remain."
Footer: "The ECN loop is observed. The marking device and congestion performance are not yet independently established."
Legend: green = observed endpoint setup; orange = media; blue dashed = feedback; amber = missing validation/features.
Do not add claims of completed congestion benchmarking, verified qdisc, or measured 20 Mb/s capacity. No passwords.
```

## Arrow correction prompt

```text
Use case: precise-object-edit, technical network diagram. Edit ONLY the directional traffic arrows on this generated current-setup diagram. Keep all text, numbers, counts, labels, layout, nodes, title, panels and colors unchanged.
Correct the ORANGE media path so it visibly runs from the Pion SENDER server icon on the right LEFTWARD into the servers switch, then DOWNWARD from the switch toward lld-black4, then LEFTWARD into lld-black4 and LEFTWARD into the Mac. Specifically the existing orange arrowhead immediately below the servers switch currently points UP, which is incorrect: remove that upward orange arrowhead. Put a DOWNWARD orange arrowhead midway along the vertical orange segment between switch and the bend. Retain leftward orange arrowheads entering lld-black4 and Mac. Add an orange leftward arrowhead on the horizontal link from the server icon into the switch.
The BLUE DASHED feedback path must visibly run from the Mac RIGHTWARD into lld-black4, then RIGHTWARD and UPWARD toward the switch, then RIGHTWARD from the switch into the sender server icon. Keep upward blue arrowhead at switch, add a dashed blue rightward arrow on the switch-to-server horizontal segment, offset from the orange reverse arrow for readability.
Only this arrow correction; do not reword any content or change diagram connections. All arrows must agree with legend: orange media server→Mac; blue feedback Mac→server.
```
