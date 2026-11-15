// Package prague adapts the pinned UDP-Prague controller to Pion's estimator
// interface. RTP writes block for pacing/window credit, bounding the sender
// queue. Only one RTP SSRC and RTCP RFC 8888 feedback are supported.
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

type packetKey struct {
	ssrc uint32
	seq  uint16
}
type sentPacket struct {
	at     time.Time
	bytes  int
	status uint8
}

// Controller owns the native Prague instance and a bounded RTP send history.
type Controller struct {
	mu                       sync.Mutex
	sendMu                   sync.Mutex
	native                   unsafe.Pointer
	start                    time.Time
	now                      func() time.Time
	next                     time.Time
	lastFeedback             time.Time
	history                  map[packetKey]*sentPacket
	sent, received, ce, lost int32
	inflight                 int
	ssrc                     uint32
	bound                    bool
	failure                  error
	done                     chan struct{}
	changed                  chan struct{}
}

// New creates an independent controller; the caller must eventually Close it.
func New(initialBPS int) (*Controller, error) {
	if initialBPS < 100000 || initialBPS > 50000000 {
		return nil, fmt.Errorf("Prague initial rate must be 100000..50000000 bps")
	}
	p := C.prague_new(C.uint64_t(initialBPS / 8))
	if p == nil {
		return nil, fmt.Errorf("allocate Prague controller")
	}
	return &Controller{native: p, start: time.Now(), now: time.Now, history: make(map[packetKey]*sentPacket), done: make(chan struct{}), changed: make(chan struct{}, 1)}, nil
}

// clock returns wrapping microseconds, reserving zero as the reference requires.
func (c *Controller) clock(now time.Time) C.int32_t {
	v := int32(now.Sub(c.start).Microseconds() + 1)
	if v == 0 {
		v = 1
	}
	return C.int32_t(v)
}

// AddStream attaches the sole RTP stream to the synchronous Prague pacer.
func (c *Controller) AddStream(info *interceptor.StreamInfo, writer interceptor.RTPWriter) interceptor.RTPWriter {
	c.mu.Lock()
	if c.bound && c.ssrc != info.SSRC {
		c.failure = errors.New("Prague supports one RTP SSRC")
	}
	c.ssrc, c.bound = info.SSRC, true
	c.mu.Unlock()
	return interceptor.RTPWriterFunc(func(h *rtp.Header, payload []byte, a interceptor.Attributes) (int, error) {
		c.sendMu.Lock()
		defer c.sendMu.Unlock()
		size := h.MarshalSize() + len(payload) + 16 + 28 // SRTP allowance plus UDP/IPv4.
		for {
			c.mu.Lock()
			if c.native == nil {
				c.mu.Unlock()
				return 0, errors.New("Prague closed")
			}
			now := c.now()
			if !c.lastFeedback.IsZero() && now.Sub(c.lastFeedback) > 2*time.Second {
				c.failure = errors.New("Prague feedback timeout; stopping ECT(1) media")
			}
			if c.failure != nil {
				err := c.failure
				c.mu.Unlock()
				return 0, err
			}
			info := C.prague_get(c.native, c.clock(now))
			window := max(int(info.window)*int(info.size), size)
			if c.inflight+size <= window && !now.Before(c.next) {
				key := packetKey{h.SSRC, h.SequenceNumber}
				if _, ok := c.history[key]; ok {
					c.failure = errors.New("RTP sequence reused before history expiry")
					c.mu.Unlock()
					continue
				}
				if len(c.history) >= 32768 {
					c.failure = errors.New("Prague history limit reached")
					c.mu.Unlock()
					continue
				}
				c.history[key] = &sentPacket{at: now, bytes: size}
				c.sent++
				c.inflight += size
				if c.lastFeedback.IsZero() {
					c.lastFeedback = now
				}
				c.next = now.Add(time.Duration(float64(size) * float64(time.Second) / float64(max(uint64(info.rate), 1))))
				c.mu.Unlock()
				n, err := writer.Write(h, payload, a)
				if err != nil {
					c.mu.Lock()
					c.failure = fmt.Errorf("write paced RTP: %w", err)
					c.mu.Unlock()
				}
				return n, err
			}
			delay := c.next.Sub(now)
			if c.inflight+size > window {
				delay = 20 * time.Millisecond
			}
			if delay <= 0 {
				delay = 100 * time.Microsecond
			}
			if delay > 20*time.Millisecond {
				delay = 20 * time.Millisecond
			}
			c.mu.Unlock()
			timer := time.NewTimer(delay)
			select {
			case <-c.done:
				timer.Stop()
				return 0, errors.New("Prague closed")
			case <-c.changed:
				timer.Stop()
			case <-timer.C:
			}
		}
	})
}

// WriteRTCP accounts each sent packet once and recovers provisional loss on late
// reports. RTT subtracts the receiver's report hold time; clocks need not match.
func (c *Controller) WriteRTCP(packets []rtcp.Packet, _ interceptor.Attributes) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.native == nil {
		return errors.New("Prague closed")
	}
	now := c.now()
	var rtt time.Duration
	updated := false
	for _, p := range packets {
		report, ok := p.(*rtcp.CCFeedbackReport)
		if !ok {
			continue
		}
		for _, block := range report.ReportBlocks {
			for i, m := range block.MetricBlocks {
				key := packetKey{block.MediaSSRC, block.BeginSequence + uint16(i)}
				sent, ok := c.history[key]
				if !ok {
					continue
				}
				if m.Received && (m.ECN != rtcp.ECNECT1 && m.ECN != rtcp.ECNCE) {
					c.failure = errors.New("Prague receiver reported Non-ECT/ECT(0); stopping ECT(1) media")
					select {
					case c.changed <- struct{}{}:
					default:
					}
					return c.failure
				}
				if m.Received && sent.status != 1 {
					if sent.status == 2 {
						c.lost--
					} else {
						c.inflight -= sent.bytes
					}
					sent.status = 1
					c.received++
					if m.ECN == rtcp.ECNCE {
						c.ce++
					}
					updated = true
					if m.ArrivalTimeOffset < 0x1ffe {
						sample := now.Sub(sent.at) - time.Duration(m.ArrivalTimeOffset)*time.Second/1024
						if sample > 0 && sample < 10*time.Second && (rtt == 0 || sample < rtt) {
							rtt = sample
						}
					}
				} else if !m.Received && sent.status == 0 {
					sent.status = 2
					c.lost++
					c.inflight -= sent.bytes
					updated = true
				}
			}
		}
	}
	// Discard acknowledged history after a reordering horizon, well before wrap.
	for key, p := range c.history {
		if p.status != 0 && now.Sub(p.at) > time.Second {
			delete(c.history, key)
		}
	}
	if updated {
		// Initial or tail losses can fall outside the receiver's report range.
		// Release old outstanding packets conservatively while feedback is live.
		info := C.prague_get(c.native, c.clock(now))
		horizon := max(200*time.Millisecond, 3*time.Duration(info.srtt)*time.Microsecond)
		for _, p := range c.history {
			if p.status == 0 && now.Sub(p.at) > horizon {
				p.status = 2
				c.lost++
				c.inflight -= p.bytes
			}
		}
		c.lastFeedback = now
		if rtt == 0 {
			info := C.prague_get(c.native, c.clock(now))
			rtt = time.Duration(max(int32(info.srtt), 1000)) * time.Microsecond
		}
		C.prague_update(c.native, c.clock(now), C.int32_t(c.received), C.int32_t(c.ce), C.int32_t(c.lost), C.int32_t(c.sent), C.int32_t(max(rtt.Microseconds(), 1)))
		select {
		case c.changed <- struct{}{}:
		default:
		}
	}
	return nil
}

// GetTargetBitrate returns the active Prague pacer target in bits per second.
func (c *Controller) GetTargetBitrate() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.native == nil {
		return 0
	}
	info := C.prague_get(c.native, c.clock(c.now()))
	return int(info.rate) * 8
}

// OnTargetBitrateChange satisfies the estimator API; this sender polls its target.
func (c *Controller) OnTargetBitrateChange(_ func(int)) {}

// GetStats snapshots controller and packet accounting under their common lock.
func (c *Controller) GetStats() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.native == nil {
		return map[string]any{"closed": true}
	}
	info := C.prague_get(c.native, c.clock(c.now()))
	return map[string]any{"algorithm": "udp-prague", "rate_bps": uint64(info.rate) * 8, "packet_window": int(info.window), "packet_size": int(info.size), "inflight_bytes": c.inflight, "sent": c.sent, "received": c.received, "lost": c.lost, "ce": c.ce, "srtt_us": int(info.srtt), "alpha": float64(info.alpha) / (1 << 20), "failure": fmt.Sprint(c.failure)}
}

// Close cancels waiting RTP writers and releases the native controller once.
func (c *Controller) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.native != nil {
		close(c.done)
		C.prague_delete(c.native)
		c.native = nil
	}
	return nil
}
