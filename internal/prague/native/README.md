# Vendored UDP-Prague controller

`prague_cc.cpp`, `prague_cc.h`, and `LICENSE` are copied unchanged from
[L4STeam/udp_prague](https://github.com/L4STeam/udp_prague) commit
`46986fb95f1fdd17bf6225a0afb35f073eb7b7d9` (Apache-2.0).

The parent directory provides the C ABI and Go RTP/RTCP adapter. Build with cgo
and a C++11 compiler. `-fwrapv` preserves the reference controller's signed
timestamp/counter wrap behavior. The wrapper uses continuous mode and disables
the optional recent startup extension. The maximum controller rate is 50 Mbps.
