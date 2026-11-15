// Validate CE response, duplicate/reordered feedback, timeout and pacer shutdown
// with a deterministic monotonic clock; these are not testbed measurements.
package prague

import (
	"testing"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
)

// controllerFixture owns a controller on a deterministic clock for one test.
func controllerFixture(t *testing.T) (*Controller, *time.Time) {
	t.Helper()
	c, err := New(1000000)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	now := c.start.Add(30 * time.Millisecond)
	c.now = func() time.Time { return now }
	return c, &now
}

// reportFixture wraps packet metrics in the wire report shape expected by Pion.
func reportFixture(seq uint16, metrics ...rtcp.CCFeedbackMetricBlock) []rtcp.Packet {
	return []rtcp.Packet{&rtcp.CCFeedbackReport{ReportBlocks: []rtcp.CCFeedbackReportBlock{{MediaSSRC: 42, BeginSequence: seq, MetricBlocks: metrics}}}}
}

// TestFeedbackAccounting covers RTP wrap, duplicate reports and late loss recovery.
func TestFeedbackAccounting(t *testing.T) {
	c, now := controllerFixture(t)
	c.sent = 2
	c.inflight = 200
	for _, seq := range []uint16{65535, 0} {
		c.history[packetKey{42, seq}] = &sentPacket{at: now.Add(-20 * time.Millisecond), bytes: 100}
	}
	input := reportFixture(65535, rtcp.CCFeedbackMetricBlock{Received: true, ECN: rtcp.ECNECT1}, rtcp.CCFeedbackMetricBlock{})
	if err := c.WriteRTCP(input, nil); err != nil {
		t.Fatal(err)
	}
	if err := c.WriteRTCP(input, nil); err != nil {
		t.Fatal(err)
	}
	if c.received != 1 || c.lost != 1 || c.inflight != 0 {
		t.Fatalf("duplicate counted: %v", c.GetStats())
	}
	if err := c.WriteRTCP(reportFixture(0, rtcp.CCFeedbackMetricBlock{Received: true, ECN: rtcp.ECNCE}), nil); err != nil {
		t.Fatal(err)
	}
	if c.received != 2 || c.lost != 0 || c.ce != 1 || c.inflight != 0 {
		t.Fatalf("late packet accounting: %v", c.GetStats())
	}
}

// TestUnreportedLossReleasesWindow covers an initial loss outside CCFB's range.
func TestUnreportedLossReleasesWindow(t *testing.T) {
	c, now := controllerFixture(t)
	c.sent = 2
	c.inflight = 200
	c.history[packetKey{42, 1}] = &sentPacket{at: now.Add(-400 * time.Millisecond), bytes: 100}
	c.history[packetKey{42, 2}] = &sentPacket{at: now.Add(-20 * time.Millisecond), bytes: 100}
	if err := c.WriteRTCP(reportFixture(2, rtcp.CCFeedbackMetricBlock{Received: true, ECN: rtcp.ECNECT1}), nil); err != nil {
		t.Fatal(err)
	}
	if c.lost != 1 || c.inflight != 0 {
		t.Fatalf("unreported loss retained window: %v", c.GetStats())
	}
}

// TestCEReducesRate compares the same acknowledged load with and without CE.
func TestCEReducesRate(t *testing.T) {
	rates := []int{}
	for _, ecn := range []rtcp.ECN{rtcp.ECNECT1, rtcp.ECNCE} {
		c, now := controllerFixture(t)
		for round := 0; round < 50; round++ {
			*now = now.Add(30 * time.Millisecond)
			metrics := make([]rtcp.CCFeedbackMetricBlock, 10)
			for i := range metrics {
				seq := uint16(round*10 + i)
				c.history[packetKey{42, seq}] = &sentPacket{at: now.Add(-20 * time.Millisecond), bytes: 1000}
				metrics[i] = rtcp.CCFeedbackMetricBlock{Received: true, ECN: ecn}
			}
			c.sent += 10
			c.inflight += 10000
			if err := c.WriteRTCP(reportFixture(uint16(round*10), metrics...), nil); err != nil {
				t.Fatal(err)
			}
		}
		rates = append(rates, c.GetTargetBitrate())
	}
	if rates[1] >= rates[0] {
		t.Fatalf("CE did not lower rate: unmarked=%d CE=%d", rates[0], rates[1])
	}
}

// TestInvalidECNAndTimeoutFailClosed prevents continued ECT(1) after validation fails.
func TestInvalidECNAndTimeoutFailClosed(t *testing.T) {
	for _, kind := range []string{"Non-ECT", "ECT0", "timeout"} {
		t.Run(kind, func(t *testing.T) {
			c, now := controllerFixture(t)
			if kind == "timeout" {
				c.lastFeedback = now.Add(-3 * time.Second)
			} else {
				c.sent = 1
				c.history[packetKey{42, 1}] = &sentPacket{at: now.Add(-time.Millisecond), bytes: 100}
				ecn := rtcp.ECNNonECT
				if kind == "ECT0" {
					ecn = rtcp.ECNECT0
				}
				if err := c.WriteRTCP(reportFixture(1, rtcp.CCFeedbackMetricBlock{Received: true, ECN: ecn}), nil); err == nil {
					t.Fatal("invalid ECN accepted")
				}
			}
			wrote := false
			writer := c.AddStream(&interceptor.StreamInfo{SSRC: 42}, interceptor.RTPWriterFunc(func(*rtp.Header, []byte, interceptor.Attributes) (int, error) { wrote = true; return 1, nil }))
			if _, err := writer.Write(&rtp.Header{SSRC: 42, SequenceNumber: 2}, []byte{1}, nil); err == nil || wrote {
				t.Fatal("media escaped failed controller")
			}
		})
	}
}

// TestCloseUnblocksPacer ensures a stalled window cannot prevent process shutdown.
func TestCloseUnblocksPacer(t *testing.T) {
	c, _ := controllerFixture(t)
	c.inflight = 1 << 20
	writer := c.AddStream(&interceptor.StreamInfo{SSRC: 42}, interceptor.RTPWriterFunc(func(*rtp.Header, []byte, interceptor.Attributes) (int, error) {
		t.Error("window exceeded")
		return 0, nil
	}))
	done := make(chan error, 1)
	go func() { _, err := writer.Write(&rtp.Header{SSRC: 42}, []byte{1}, nil); done <- err }()
	_ = c.Close()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("closed write succeeded")
		}
	case <-time.After(time.Second):
		t.Fatal("pacer leaked")
	}
}
