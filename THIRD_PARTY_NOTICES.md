# Pion source attribution

The starter's Pion API usage is informed by the Pion WebRTC
[`bandwidth-estimation-from-disk`](https://github.com/pion/webrtc/blob/v4.2.26/examples/bandwidth-estimation-from-disk/main.go)
example and the GCC/interceptor APIs. Application signaling, lifecycle management,
configuration, logging, and input validation are implemented in this project.
Dependencies are consumed as pinned Go modules rather than copied repository histories.

Pion WebRTC's applicable MIT license is reproduced below. Dependencies retain
their own license files in their source distributions.

---

MIT License

Copyright (c) 2026 The Pion community <https://pion.ly>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
