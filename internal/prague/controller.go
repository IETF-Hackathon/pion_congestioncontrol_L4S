// Package prague adapts UDP-Prague to Pion with bounded elapsed-time pacing.
// The native algorithm supplies rate/window/burst limits. A monotonic waiter
// enforces eligibility without an asynchronous packet queue or pacing worker.
package prague

/*
#cgo CXXFLAGS: -std=c++11 -fwrapv
#cgo darwin LDFLAGS: -lc++
#cgo linux LDFLAGS: -lstdc++ -lm
#include "bridge.h"
*/
import "C"

import (
	"errors"
	"fmt"
	"sync"
	"time"
	"unsafe"

	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
)

const feedbackTimeoutNS = int64(2 * time.Second)

// Option configures a Prague sender while preserving existing New callers.
type Option func(*controllerOptions) error

type controllerOptions struct{ backend string }

// WithPacingBackend selects auto, portable or the explicit Linux timerfd backend.
func WithPacingBackend(name string) Option {
	return func(options *controllerOptions) error {
		if err := ValidatePacingBackend(name); err != nil {
			return err
		}
		options.backend = name
		return nil
	}
}

type packetKey struct {
	ssrc uint32
	seq  uint16
}
type sentPacket struct {
	atNS   int64
	bytes  int
	status uint8
}
type controlLimits struct {
	rate, size          uint64
	window, burst, srtt int32
	alpha               int64
}
type pacingMetrics struct {
	admittedBytes, acceptedBytes, acceptedAccountedBytes    uint64
	acceptedPackets, writeFailures                          uint64
	pacingWaits, windowWaits, pacingWaitNS, windowWaitNS    uint64
	requestedWaitNS, wakeAfterDeadlineNS, maxWakeLatenessNS uint64
	lateWakeCount                                           uint64
}

// Controller owns the native core, pacing budget, waiter and bounded send history.
type Controller struct {
	mu, sendMu                           sync.Mutex
	native                               unsafe.Pointer
	waiter                               pacingWaiter
	startNS, lastClockNS, lastFeedbackNS int64
	startedAt                            time.Time
	clockSampleNS                        int64
	feedbackStarted                      bool
	history                              map[packetKey]*sentPacket
	sent, received, ce, lost             int32
	inflight                             int
	ssrc                                 uint32
	bound                                bool
	failure                              error
	budget                               pacingBudget
	limits                               controlLimits
	limitsAtNS                           int64
	maxPacketBytes, burstBytes           uint64
	metrics                              pacingMetrics
}

// New creates a controller with platform-specific pacing and explicit cleanup.
func New(initialBPS int, options ...Option) (*Controller, error) {
	settings := controllerOptions{backend: "auto"}
	for _, option := range options {
		if option == nil {
			return nil, fmt.Errorf("nil Prague option")
		}
		if err := option(&settings); err != nil {
			return nil, err
		}
	}
	if initialBPS < 100000 || initialBPS > 50000000 {
		return nil, fmt.Errorf("Prague initial rate must be 100000..50000000 bps")
	}
	waiter, err := newPacingWaiter(settings.backend)
	if err != nil {
		return nil, err
	}
	return newController(initialBPS, waiter)
}

// newController owns an injected waiter, including on constructor failure.
func newController(initialBPS int, waiter pacingWaiter) (*Controller, error) {
	if initialBPS < 100000 || initialBPS > 50000000 {
		return nil, errors.Join(fmt.Errorf("invalid Prague initial bitrate"), waiter.Close())
	}
	startedAt := time.Now()
	now, err := waiter.NowNS()
	clockSampleNS := time.Since(startedAt).Nanoseconds()
	if err != nil {
		return nil, errors.Join(err, waiter.Close())
	}
	native := C.prague_new(C.uint64_t(initialBPS / 8))
	if native == nil {
		return nil, errors.Join(fmt.Errorf("allocate Prague controller"), waiter.Close())
	}
	c := &Controller{native: native, waiter: waiter, startNS: now, lastClockNS: now,
		startedAt: startedAt, clockSampleNS: clockSampleNS, history: make(map[packetKey]*sentPacket)}
	if err := c.applyLimitsLocked(now); err != nil {
		return nil, errors.Join(err, c.Close())
	}
	return c, nil
}

// clock converts this backend's monotonic counter to the reference wrapping clock.
func (c *Controller) clock(nowNS int64) C.int32_t {
	value := int32((nowNS-c.startNS)/1000 + 1)
	if value == 0 {
		value = 1
	}
	return C.int32_t(value)
}

// failLocked records the first fatal error and interrupts a potentially waiting writer.
func (c *Controller) failLocked(err error) error {
	if c.failure == nil {
		c.failure = err
	}
	if c.native != nil {
		if wakeErr := c.waiter.Wake(); wakeErr != nil {
			c.failure = errors.Join(c.failure, wakeErr)
		}
	}
	return c.failure
}

// nowLocked validates monotonic progress before the clock affects controller state.
func (c *Controller) nowLocked() (int64, error) {
	now, err := c.waiter.NowNS()
	if err != nil {
		return 0, c.failLocked(err)
	}
	if now < c.lastClockNS {
		return 0, c.failLocked(fmt.Errorf("Prague monotonic clock moved backwards"))
	}
	c.lastClockNS = now
	return now, nil
}

// applyLimitsLocked settles old credit and caches the actual applied native outputs.
func (c *Controller) applyLimitsLocked(nowNS int64) error {
	info := C.prague_get(c.native, c.clock(nowNS))
	limits := controlLimits{rate: uint64(info.rate), size: uint64(info.size), window: int32(info.window), burst: int32(info.burst), srtt: int32(info.srtt), alpha: int64(info.alpha)}
	if limits.rate == 0 || limits.size == 0 || limits.window < 1 || limits.burst < 1 || limits.size > maxCredit/uint64(max(limits.burst, limits.window)) {
		return c.failLocked(fmt.Errorf("invalid native Prague pacing limits"))
	}
	// The native burst count already uses its 250 us horizon. It is not a
	// fixed 250 us tick: eligibility still depends on each packet's byte cost.
	burst := max(uint64(limits.burst)*limits.size, c.maxPacketBytes, 1)
	if err := c.budget.configure(nowNS, limits.rate, burst); err != nil {
		return c.failLocked(err)
	}
	c.limits, c.limitsAtNS, c.burstBytes = limits, nowNS, burst
	return nil
}

// AddStream attaches one RTP stream to the synchronous budget and window gates.
func (c *Controller) AddStream(info *interceptor.StreamInfo, writer interceptor.RTPWriter) interceptor.RTPWriter {
	c.mu.Lock()
	if c.bound && c.ssrc != info.SSRC {
		c.failLocked(errors.New("Prague supports one RTP SSRC"))
	}
	c.ssrc, c.bound = info.SSRC, true
	c.mu.Unlock()
	return interceptor.RTPWriterFunc(func(header *rtp.Header, payload []byte, attributes interceptor.Attributes) (int, error) {
		c.sendMu.Lock()
		defer c.sendMu.Unlock()
		size := header.MarshalSize() + len(payload) + 16 + 28 // Estimated SRTP allowance and UDP/IPv4; excludes Ethernet.
		for {
			c.mu.Lock()
			if c.native == nil {
				c.mu.Unlock()
				return 0, errPacerClosed
			}
			if c.failure != nil {
				err := c.failure
				c.mu.Unlock()
				return 0, err
			}
			now, err := c.nowLocked()
			if err != nil {
				c.mu.Unlock()
				return 0, err
			}
			if c.feedbackStarted && now-c.lastFeedbackNS >= feedbackTimeoutNS {
				err = c.failLocked(errors.New("Prague feedback timeout; stopping ECT(1) media"))
				c.mu.Unlock()
				return 0, err
			}
			if uint64(size) > c.maxPacketBytes {
				c.maxPacketBytes = uint64(size)
				if c.sent == 0 {
					c.budget = pacingBudget{}
				} // Admit at most one initial whole packet.
			}
			if err = c.applyLimitsLocked(now); err != nil {
				c.mu.Unlock()
				return 0, err
			}
			delay, err := c.budget.delayNS(uint64(size))
			if err != nil {
				err = c.failLocked(err)
				c.mu.Unlock()
				return 0, err
			}
			window := max(uint64(c.limits.window)*c.limits.size, uint64(size))
			windowBlocked := uint64(c.inflight)+uint64(size) > window
			if !windowBlocked && delay == 0 {
				key := packetKey{header.SSRC, header.SequenceNumber}
				if _, exists := c.history[key]; exists {
					err = c.failLocked(errors.New("RTP sequence reused before history expiry"))
					c.mu.Unlock()
					return 0, err
				}
				if len(c.history) >= 32768 {
					err = c.failLocked(errors.New("Prague history limit reached"))
					c.mu.Unlock()
					return 0, err
				}
				if err = c.budget.spend(uint64(size)); err != nil {
					err = c.failLocked(err)
					c.mu.Unlock()
					return 0, err
				}
				c.history[key] = &sentPacket{atNS: now, bytes: size}
				c.sent++
				c.inflight += size
				c.metrics.admittedBytes += uint64(size)
				if !c.feedbackStarted {
					c.lastFeedbackNS, c.feedbackStarted = now, true
				}
				c.mu.Unlock()
				count, writeErr := writer.Write(header, payload, attributes)
				c.mu.Lock()
				if writeErr != nil {
					c.metrics.writeFailures++
					writeErr = c.failLocked(fmt.Errorf("write paced RTP: %w", writeErr))
				} else {
					c.metrics.acceptedPackets++
					c.metrics.acceptedBytes += uint64(max(count, 0))
					c.metrics.acceptedAccountedBytes += uint64(size)
				}
				c.mu.Unlock()
				return count, writeErr
			}
			feedbackBase := c.lastFeedbackNS
			if !c.feedbackStarted {
				feedbackBase = now
			}
			deadline, err := deadlineAfter(feedbackBase, feedbackTimeoutNS)
			if err == nil && !windowBlocked {
				var paceDeadline int64
				paceDeadline, err = deadlineAfter(now, delay)
				deadline = min(deadline, paceDeadline)
			}
			if err != nil {
				err = c.failLocked(err)
				c.mu.Unlock()
				return 0, err
			}
			if windowBlocked {
				c.metrics.windowWaits++
			} else {
				c.metrics.pacingWaits++
			}
			c.metrics.requestedWaitNS += uint64(max(deadline-now, 0))
			c.mu.Unlock()
			waitErr := c.waiter.WaitUntilNS(deadline)
			woke, clockErr := c.waiter.NowNS()
			c.mu.Lock()
			if clockErr == nil {
				elapsed := uint64(max(woke-now, 0))
				if windowBlocked {
					c.metrics.windowWaitNS += elapsed
				} else {
					c.metrics.pacingWaitNS += elapsed
				}
				if woke > deadline {
					late := uint64(woke - deadline)
					c.metrics.lateWakeCount++
					c.metrics.wakeAfterDeadlineNS += late
					c.metrics.maxWakeLatenessNS = max(c.metrics.maxWakeLatenessNS, late)
				}
			}
			if waitErr != nil || clockErr != nil {
				err = errors.Join(waitErr, clockErr)
				if !errors.Is(err, errPacerClosed) {
					err = c.failLocked(err)
				}
				c.mu.Unlock()
				return 0, err
			}
			c.mu.Unlock()
		}
	})
}

// WriteRTCP preserves packet accounting while applying changed limits at feedback time.
func (c *Controller) WriteRTCP(packets []rtcp.Packet, _ interceptor.Attributes) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.native == nil {
		return errPacerClosed
	}
	if c.failure != nil {
		return c.failure
	}
	now, err := c.nowLocked()
	if err != nil {
		return err
	}
	var rttNS int64
	updated := false
	for _, packet := range packets {
		report, ok := packet.(*rtcp.CCFeedbackReport)
		if !ok {
			continue
		}
		for _, block := range report.ReportBlocks {
			for i, metric := range block.MetricBlocks {
				key := packetKey{block.MediaSSRC, block.BeginSequence + uint16(i)}
				sent, ok := c.history[key]
				if !ok {
					continue
				}
				if metric.Received && metric.ECN != rtcp.ECNECT1 && metric.ECN != rtcp.ECNCE {
					return c.failLocked(errors.New("Prague receiver reported Non-ECT/ECT(0); stopping ECT(1) media"))
				}
				if metric.Received && sent.status != 1 {
					if sent.status == 2 {
						c.lost--
					} else {
						c.inflight -= sent.bytes
					}
					sent.status = 1
					c.received++
					if metric.ECN == rtcp.ECNCE {
						c.ce++
					}
					updated = true
					if metric.ArrivalTimeOffset < 0x1ffe {
						sample := now - sent.atNS - int64(metric.ArrivalTimeOffset)*int64(time.Second)/1024
						if sample > 0 && sample < int64(10*time.Second) && (rttNS == 0 || sample < rttNS) {
							rttNS = sample
						}
					}
				} else if !metric.Received && sent.status == 0 {
					sent.status = 2
					c.lost++
					c.inflight -= sent.bytes
					updated = true
				}
			}
		}
	}
	for key, sent := range c.history {
		if sent.status != 0 && now-sent.atNS > int64(time.Second) {
			delete(c.history, key)
		}
	}
	if !updated {
		return nil
	}
	if err := c.budget.advance(now); err != nil {
		return c.failLocked(err)
	}
	horizon := max(int64(200*time.Millisecond), 3*int64(c.limits.srtt)*int64(time.Microsecond))
	for _, sent := range c.history {
		if sent.status == 0 && now-sent.atNS > horizon {
			sent.status = 2
			c.lost++
			c.inflight -= sent.bytes
		}
	}
	c.lastFeedbackNS, c.feedbackStarted = now, true
	if rttNS == 0 {
		rttNS = int64(max(c.limits.srtt, 1000)) * int64(time.Microsecond)
	}
	C.prague_update(c.native, c.clock(now), C.int32_t(c.received), C.int32_t(c.ce), C.int32_t(c.lost), C.int32_t(c.sent), C.int32_t(max(rttNS/int64(time.Microsecond), 1)))
	if err := c.applyLimitsLocked(now); err != nil {
		return err
	}
	if err := c.waiter.Wake(); err != nil {
		return c.failLocked(err)
	}
	return nil
}

// GetTargetBitrate returns the cached applied pacing rate in bits per second.
func (c *Controller) GetTargetBitrate() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.native == nil {
		return 0
	}
	return int(c.limits.rate) * 8
}

// OnTargetBitrateChange satisfies Pion's API; this application polls its target.
func (c *Controller) OnTargetBitrateChange(_ func(int)) {}

// GetStats returns observational snapshots without resampling the native pacing phase.
func (c *Controller) GetStats() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return map[string]any{
		"algorithm": "udp-prague", "rate_bps": c.limits.rate * 8,
		"packet_window": int(c.limits.window), "packet_size": int(c.limits.size), "packet_burst": int(c.limits.burst),
		"inflight_bytes": c.inflight, "sent": c.sent, "received": c.received, "lost": c.lost, "ce": c.ce,
		"srtt_us": int(c.limits.srtt), "alpha": float64(c.limits.alpha) / (1 << 20), "failure": fmt.Sprint(c.failure), "closed": c.native == nil,
		"pacing_backend": c.waiter.Name(), "applied_at_ns_since_start": c.limitsAtNS - c.startNS,
		"clock_origin_ns": fmt.Sprint(c.startNS), "clock_origin_utc": c.startedAt.UTC().Format(time.RFC3339Nano),
		"clock_origin_sample_ns": c.clockSampleNS,
		"clock_note":             "monotonic origin sampled within clock_origin_sample_ns after UTC anchor; host clock synchronization error is additional",
		"burst_budget_bytes":     c.burstBytes, "burst_rounding_bytes": c.burstBytes - uint64(c.limits.burst)*c.limits.size,
		"pacing_credit_bytes":       c.budget.credit / creditScale,
		"admitted_accounted_bytes":  c.metrics.admittedBytes,
		"rtp_writer_accepted_bytes": c.metrics.acceptedBytes,
		"accepted_accounted_bytes":  c.metrics.acceptedAccountedBytes,
		"accepted_packets":          c.metrics.acceptedPackets, "write_failures": c.metrics.writeFailures,
		"pacing_waits": c.metrics.pacingWaits, "window_waits": c.metrics.windowWaits,
		"pacing_wait_ns": c.metrics.pacingWaitNS, "window_wait_ns": c.metrics.windowWaitNS,
		"requested_wait_ns": c.metrics.requestedWaitNS, "wake_after_deadline_ns": c.metrics.wakeAfterDeadlineNS,
		"max_wake_lateness_ns": c.metrics.maxWakeLatenessNS, "late_wakes": c.metrics.lateWakeCount,
		"accounting_note": "admitted/accounted bytes estimate SRTP+UDP/IP; writer return bytes are not NIC transmission",
	}
}

// Close stops admission, frees native state and joins the wait backend exactly once.
func (c *Controller) Close() error {
	c.mu.Lock()
	if c.native != nil {
		C.prague_delete(c.native)
		c.native = nil
	}
	c.mu.Unlock()
	return c.waiter.Close()
}
