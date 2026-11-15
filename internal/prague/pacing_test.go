// Exercise byte budgets, wait lifecycle and paced-writer accounting independently
// of the physical testbed. Real backend benchmarks report scheduling precision.
package prague

import (
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
)

// fakePacingWaiter supplies a synchronized clock and explicit wake/close events.
type fakePacingWaiter struct {
	mu       sync.Mutex
	at       int64
	auto     bool
	closed   bool
	clockErr error
	wake     chan struct{}
	done     chan struct{}
	waiting  chan int64
}

// newFakePacingWaiter starts well above zero so pre-start send samples are valid.
func newFakePacingWaiter() *fakePacingWaiter {
	return &fakePacingWaiter{at: int64(time.Second), wake: make(chan struct{}, 1), done: make(chan struct{}), waiting: make(chan int64, 16)}
}

// Name labels deterministic test observations without imitating an OS backend.
func (w *fakePacingWaiter) Name() string { return "fake" }

// NowNS snapshots the injected monotonic clock or a deliberate clock failure.
func (w *fakePacingWaiter) NowNS() (int64, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.at, w.clockErr
}

// now returns the clock value for concise deterministic packet fixtures.
func (w *fakePacingWaiter) now() int64 { value, _ := w.NowNS(); return value }

// advance moves the fake clock without depending on real timer resolution.
func (w *fakePacingWaiter) advance(d time.Duration) {
	w.mu.Lock()
	w.at += int64(d)
	w.mu.Unlock()
}

// WaitUntilNS either advances automatically or waits for a test-controlled event.
func (w *fakePacingWaiter) WaitUntilNS(deadline int64) error {
	w.mu.Lock()
	if w.closed {
		w.mu.Unlock()
		return errPacerClosed
	}
	auto := w.auto
	w.mu.Unlock()
	select {
	case w.waiting <- deadline:
	default:
	}
	select {
	case <-w.done:
		return errPacerClosed
	case <-w.wake:
		return nil
	default:
	}
	if auto {
		w.mu.Lock()
		w.at = max(w.at, deadline)
		w.mu.Unlock()
		return nil
	}
	select {
	case <-w.done:
		return errPacerClosed
	case <-w.wake:
		return nil
	}
}

// Wake retains an early notification and rejects use after closure.
func (w *fakePacingWaiter) Wake() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return errPacerClosed
	}
	select {
	case w.wake <- struct{}{}:
	default:
	}
	return nil
}

// Close interrupts a blocked fake writer exactly once.
func (w *fakePacingWaiter) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if !w.closed {
		w.closed = true
		close(w.done)
	}
	return nil
}

// TestBudgetRateEpochAndFractions prevents retroactive rate credit and truncation.
func TestBudgetRateEpochAndFractions(t *testing.T) {
	var budget pacingBudget
	if err := budget.configure(0, 1_000_000, 1200); err != nil {
		t.Fatal(err)
	}
	if err := budget.spend(1200); err != nil {
		t.Fatal(err)
	}
	if err := budget.configure(250_000, 2_000_000, 1200); err != nil {
		t.Fatal(err)
	}
	if delay, err := budget.delayNS(1200); err != nil || delay != 475_000 {
		t.Fatalf("old-rate credit: delay=%d error=%v", delay, err)
	}
	budget = pacingBudget{}
	if err := budget.configure(0, 3, 2); err != nil {
		t.Fatal(err)
	}
	if err := budget.spend(2); err != nil {
		t.Fatal(err)
	}
	if err := budget.advance(333_333_333); err != nil {
		t.Fatal(err)
	}
	if delay, err := budget.delayNS(1); err != nil || delay != 1 {
		t.Fatalf("fractional credit: delay=%d error=%v", delay, err)
	}
	if err := budget.advance(333_333_334); err != nil {
		t.Fatal(err)
	}
	if err := budget.spend(1); err != nil {
		t.Fatal(err)
	}
	if budget.credit != 2 {
		t.Fatalf("fraction lost: %d", budget.credit)
	}
}

// TestBudgetBoundsIdleAndRateReduction prevents overflow and catch-up bursts.
func TestBudgetBoundsIdleAndRateReduction(t *testing.T) {
	var budget pacingBudget
	if err := budget.configure(0, 6_250_000, 2400); err != nil {
		t.Fatal(err)
	}
	if err := budget.advance(1 << 62); err != nil {
		t.Fatal(err)
	}
	if err := budget.configure(1<<62, 12500, 1200); err != nil {
		t.Fatal(err)
	}
	if budget.credit != 1200*creditScale {
		t.Fatal("credit not clamped")
	}
	if err := budget.spend(1200); err != nil {
		t.Fatal(err)
	}
	if err := budget.spend(1); err == nil {
		t.Fatal("borrowed future credit")
	}
	if err := budget.advance(1); err == nil {
		t.Fatal("backwards clock accepted")
	}
	if _, err := deadlineAfter(int64(maxCredit), 1); err == nil {
		t.Fatal("deadline overflow accepted")
	}
	if err := budget.configure(0, 1, maxCredit); err == nil {
		t.Fatal("credit overflow accepted")
	}
}

// TestPacedWriterImmediateFeedback verifies history exists before the actual write.
func TestPacedWriterImmediateFeedback(t *testing.T) {
	c, clock := controllerFixture(t)
	clock.auto = true
	writer := c.AddStream(&interceptor.StreamInfo{SSRC: 42}, interceptor.RTPWriterFunc(func(h *rtp.Header, p []byte, _ interceptor.Attributes) (int, error) {
		clock.advance(time.Microsecond)
		err := c.WriteRTCP(reportFixture(h.SequenceNumber, rtcp.CCFeedbackMetricBlock{Received: true, ECN: rtcp.ECNECT1}), nil)
		return h.MarshalSize() + len(p), err
	}))
	for i := 0; i < 20; i++ {
		if _, err := writer.Write(&rtp.Header{Version: 2, SSRC: 42, SequenceNumber: uint16(i)}, make([]byte, 1000), nil); err != nil {
			t.Fatal(err)
		}
	}
	stats := c.GetStats()
	if c.received != 20 || c.inflight != 0 || stats["accepted_packets"] != uint64(20) {
		t.Fatalf("accounting: %v", stats)
	}
	if stats["pacing_waits"].(uint64) == 0 {
		t.Fatal("pacing was bypassed")
	}
	before := c.GetTargetBitrate()
	clock.advance(time.Second)
	_ = c.GetStats()
	if c.GetTargetBitrate() != before {
		t.Fatal("statistics changed applied pacing")
	}
}

// TestFailedWriteIsNotAccepted retains the error and distinguishes admission from TX.
func TestFailedWriteIsNotAccepted(t *testing.T) {
	c, _ := controllerFixture(t)
	want := errors.New("injected downstream failure")
	writer := c.AddStream(&interceptor.StreamInfo{SSRC: 42}, interceptor.RTPWriterFunc(func(*rtp.Header, []byte, interceptor.Attributes) (int, error) { return 0, want }))
	if _, err := writer.Write(&rtp.Header{SSRC: 42, SequenceNumber: 1}, []byte{1}, nil); !errors.Is(err, want) {
		t.Fatalf("lost write failure: %v", err)
	}
	stats := c.GetStats()
	if stats["accepted_packets"] != uint64(0) || stats["write_failures"] != uint64(1) || c.sent != 1 {
		t.Fatalf("misreported acceptance: %v", stats)
	}
}

// TestFeedbackReleasesWindowWait checks a state wake without waiting for a timer.
func TestFeedbackReleasesWindowWait(t *testing.T) {
	c, clock := controllerFixture(t)
	c.sent, c.inflight = 1, 20000
	c.feedbackStarted, c.lastFeedbackNS = true, clock.now()
	c.history[packetKey{42, 1}] = &sentPacket{atNS: clock.now() - int64(20*time.Millisecond), bytes: 20000}
	writer := c.AddStream(&interceptor.StreamInfo{SSRC: 42}, interceptor.RTPWriterFunc(func(*rtp.Header, []byte, interceptor.Attributes) (int, error) { return 1, nil }))
	done := make(chan error, 1)
	go func() { _, err := writer.Write(&rtp.Header{SSRC: 42, SequenceNumber: 2}, []byte{1}, nil); done <- err }()
	select {
	case <-clock.waiting:
	case <-time.After(time.Second):
		t.Fatal("writer never waited")
	}
	if err := c.WriteRTCP(reportFixture(1, rtcp.CCFeedbackMetricBlock{Received: true, ECN: rtcp.ECNECT1}), nil); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("feedback did not wake writer")
	}
}

// TestWaiterWakeAndClose exercises early notifications and joining active waits.
func TestWaiterWakeAndClose(t *testing.T) {
	for _, backend := range []string{"portable", "auto"} {
		t.Run(backend, func(t *testing.T) {
			w, err := newPacingWaiter(backend)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = w.Close() })
			now, err := w.NowNS()
			if err != nil {
				t.Fatal(err)
			}
			if err = w.Wake(); err != nil {
				t.Fatal(err)
			}
			done := make(chan error, 1)
			go func() { done <- w.WaitUntilNS(now + int64(time.Hour)) }()
			select {
			case err = <-done:
				if err != nil {
					t.Fatal(err)
				}
			case <-time.After(time.Second):
				t.Fatal("early wake lost")
			}
			go func() { done <- w.WaitUntilNS(now + int64(time.Hour)) }()
			if err = w.Close(); err != nil {
				t.Fatal(err)
			}
			select {
			case err = <-done:
				if !errors.Is(err, errPacerClosed) {
					t.Fatalf("close result %v", err)
				}
			case <-time.After(time.Second):
				t.Fatal("close did not release wait")
			}
			if err = w.Close(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

// TestWaiterDeadline checks that both real backends use the advertised clock domain.
// It imposes no maximum lateness: OS performance is measured by the benchmark.
func TestWaiterDeadline(t *testing.T) {
	for _, backend := range []string{"portable", "auto"} {
		t.Run(backend, func(t *testing.T) {
			w, err := newPacingWaiter(backend)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = w.Close() })
			now, err := w.NowNS()
			if err != nil {
				t.Fatal(err)
			}
			deadline := now + int64(2*time.Millisecond)
			if err := w.WaitUntilNS(deadline); err != nil {
				t.Fatal(err)
			}
			woke, err := w.NowNS()
			if err != nil || woke < deadline {
				t.Fatalf("deadline=%d woke=%d error=%v", deadline, woke, err)
			}
		})
	}
}

// TestBudgetPacketIntervals distinguishes a 250 us burst horizon from a fixed tick.
func TestBudgetPacketIntervals(t *testing.T) {
	for _, tc := range []struct {
		rate, packet uint64
		interval     int64
	}{
		{3_750_000, 1200, 320_000}, // 30 Mbps: one native-sized packet takes 320 us.
		{6_250_000, 1200, 192_000}, // 50 Mbps: it takes 192 us, not 250 us.
		{3_750_000, 1250, 333_334}, // Whole RTP cost can exceed the 1200-byte reference.
	} {
		var budget pacingBudget
		if err := budget.configure(0, tc.rate, tc.packet); err != nil {
			t.Fatal(err)
		}
		if err := budget.spend(tc.packet); err != nil {
			t.Fatal(err)
		}
		if wait, err := budget.delayNS(tc.packet); err != nil || wait != tc.interval {
			t.Fatalf("rate=%d packet=%d wait=%d want=%d error=%v", tc.rate, tc.packet, wait, tc.interval, err)
		}
	}
}

// BenchmarkPacing compares the archived scheduling algorithm with the new budget.
// Results measure admission timing, not whole-Pion or on-wire media performance.
func BenchmarkPacing(b *testing.B) {
	for _, backend := range []string{"legacy", "portable", "auto"} {
		b.Run(backend, func(b *testing.B) {
			const packetBytes uint64 = 1250
			const rate uint64 = 3_750_000 // 30 Mbps; one packet per 333 microseconds.
			w, err := newPacingWaiter("portable")
			if backend != "legacy" {
				if err == nil {
					_ = w.Close()
				}
				w, err = newPacingWaiter(backend)
			}
			if err != nil {
				b.Fatal(err)
			}
			defer func() { _ = w.Close() }()
			var budget pacingBudget
			now, err := w.NowNS()
			if err != nil {
				b.Fatal(err)
			}
			next := now
			if err = budget.configure(now, rate, packetBytes); err != nil {
				b.Fatal(err)
			}
			b.ReportAllocs()
			b.SetBytes(int64(packetBytes))
			b.ResetTimer()
			start := time.Now()
			for i := 0; i < b.N; i++ {
				for {
					now, err = w.NowNS()
					if err != nil {
						b.Fatal(err)
					}
					if backend == "legacy" {
						if now < next {
							timer := time.NewTimer(time.Duration(next - now))
							<-timer.C
							continue
						}
						next = now + int64(packetBytes*creditScale/rate)
						break
					}
					if err = budget.configure(now, rate, packetBytes); err != nil {
						b.Fatal(err)
					}
					delay, delayErr := budget.delayNS(packetBytes)
					if delayErr != nil {
						b.Fatal(delayErr)
					}
					if delay == 0 {
						if err = budget.spend(packetBytes); err != nil {
							b.Fatal(err)
						}
						break
					}
					if err = w.WaitUntilNS(now + delay); err != nil {
						b.Fatal(err)
					}
				}
			}
			elapsed := time.Since(start).Seconds()
			b.ReportMetric(float64(b.N)*float64(packetBytes)*8/elapsed/1e6, "admission-Mbps")
		})
	}
}
