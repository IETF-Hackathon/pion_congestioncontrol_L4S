# Pion Prague status update

[Shareable PNG](pion-prague-status-update.png)

Created with the built-in imagegen tool, using the detailed [current setup](pion-current-lab-setup.md) as a reference. The generated PNG was visually reviewed: addresses, arrow directions, packet/CE counts and pending-validation wording match the run evidence.

The 14 November 2026 run at 17:00 PST used the physical sender `192.168.1.216` and Mac receiver `192.168.1.79`. Receiver counters reached 2,078 RTP including 2 CE; both CE marks appeared in sender Prague state. The expected path is based on user-confirmed cabling. This is an observed endpoint media/ECN loop, not independent proof of the bottleneck path or a congestion benchmark. See [run data](../../results/preview/prague-lab-selabs216-20261114.json).

## Dashboard check and current processes

A read-only check of [lld-black4 UI](http://lld-black4.local/ui/) found no saved capture matching this run. Its [capture index](http://lld-black4.local/data/?C=M;O=D) listed July 18–19 2026 tests and the [archive](http://lld-black4.local/data/archive/) listed March 2025 tests. The status page reported `Currently idle`. The inspected latest July capture had no flow filename containing both run IPs. This does not establish that the November traffic bypassed the bottleneck. The endpoint helper did not start a monitor packet capture, so a capture must be started before a future run to obtain dashboard evidence.

A subsequent live check found neither endpoint running: no Mac Pion process or receiver TCP 8080 listener, and no `pion-l4s` process on the server. The sender's saved metadata records successful completion at 17:00:30 PST; the receiver was stopped after collecting results. The diagram reports a completed check, not continuously running services.

## Generation prompt

```text
Use case: compositing, infographic-diagram.
Asset type: simple shareable PNG status update, landscape 16:9.
Input image: the detailed Pion lab diagram is a factual reference. Simplify it radically into a clean standalone status graphic; do not preserve the cluttered original background. Preserve only facts specified below.
Style: polished, restrained engineering status slide; white background, dark navy text, large readable typography, flat simple device icons, generous spacing. Orange arrows for forward media and blue dashed arrows for reverse feedback. Green for observed success and amber for pending validation. No tiny text, no gradients, no decorative illustrations.
Main title, exact: "Pion Prague — status update"
Date, exact: "14 November 2026"
Green status strip below title: "Two-host media + ECN feedback verified"

Middle: exactly FOUR nodes in a single left-to-right row:
1 simple server icon, labeled "Pion sender" and "192.168.1.216"
2 switch icon, labeled "Servers switch"
3 simple bottleneck box, labeled "lld-black4" and "DualPI2/SRM"
4 laptop icon, labeled "Pion receiver" and "Mac · 192.168.1.79"
Orange rightward arrows connect sender → switch → lld-black4 → Mac. Label above route "Video · ECT(1)".
A blue dashed reverse arrow below the nodes points from the Mac LEFTWARD, via lld-black4 and switch, back to sender. Label "ECN feedback".
A clear short caption immediately below the route, exact: "Expected path from confirmed cabling; bottleneck capture still needed."
Do not imply that crossing the bottleneck or live qdisc settings have been independently verified.

Bottom: just TWO compact status cards, green on left and amber on right.
Left exact text:
"Completed · 30-second check"
"2,078 RTP packets received"
"2 CE marks reached Prague"
Right exact text:
"Next · congestion validation"
"Capture on lld-black4 and rerun"
"Confirm path/settings; test under load"
"Adaptive video + matched GCC comparison"

Small but legible footer, exact:
"No matching dashboard capture found. Fixed ~0.5 Mb/s video; capacity not measured."

Keep the total content limited to these specified words. Omit all management IPs, interface names, SSH commands, port numbers, other network branches, implementation details, legends and extra paragraphs. Prioritize clarity when pasted into Slack/email or a status deck. All arrows must have correct directions, all numbers must be exact.
```
