// Adapt the reference algorithm without changing its vendored source files.
#include <cstddef>
#include <new>
#include "bridge.h"
#include "native/prague_cc.cpp"

// ClockController accepts Go's monotonic clock, also allowing deterministic tests.
class ClockController : public PragueCC {
public:
    int32_t clock = 1;
    explicit ClockController(uint64_t rate) : PragueCC(false, 1200, 0, 0, rate, 10, 12500, 6250000) {}
    time_tp Now() override { return clock; }
};

// prague_new selects continuous mode, bounded at 50 Mbps, without optional startup.
void* prague_new(uint64_t rate) { return new (std::nothrow) ClockController(rate); }
// prague_delete frees the controller after all Go writers have stopped.
void prague_delete(void* p) { delete static_cast<ClockController*>(p); }
// prague_update supplies cumulative counters and a validated positive RTT sample.
void prague_update(void* p, int32_t now, int32_t received, int32_t ce, int32_t lost, int32_t sent, int32_t rtt) {
    auto cc = static_cast<ClockController*>(p);
    cc->clock = now;
    cc->RFC8888Received(1, &rtt);
    count_tp inflight;
    cc->ACKReceived(received, ce, lost, sent, false, inflight);
}
// prague_get exposes pacing and byte-window inputs plus diagnostic state.
prague_info prague_get(void* p, int32_t now) {
    auto cc = static_cast<ClockController*>(p);
    cc->clock = now;
    prague_info info{};
    cc->GetCCInfo(info.rate, info.window, info.burst, info.size);
    auto state = cc->GetStatePtr();
    info.srtt = state->m_srtt;
    info.alpha = state->m_alpha;
    return info;
}
