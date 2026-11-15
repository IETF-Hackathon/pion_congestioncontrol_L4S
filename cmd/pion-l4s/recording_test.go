// Recording tests verify private SDP artifacts, explicit truncation, and an
// unchanged controller handoff, without sending traffic to the physical lab.
package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"
)

// TestSDPRecording keeps exact signaling private and shareable copies redacted.
func TestSDPRecording(t *testing.T) {
	dir := t.TempDir()
	recorder, err := newSessionRecorder(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = recorder.close() })
	offer := &webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: "v=0\r\na=ice-ufrag:test-user\r\na=ice-pwd:test-password\r\na=rtpmap:96 VP8/90000\r\na=rtcp-fb:96 ack ccfb\r\n"}
	if err := recorder.sdp("local", offer); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "local-offer.raw.sdp"))
	if err != nil || string(raw) != offer.SDP {
		t.Fatalf("raw SDP did not preserve signaling: %v", err)
	}
	redacted, err := os.ReadFile(filepath.Join(dir, "local-offer.sdp"))
	if err != nil || strings.Contains(string(redacted), "test-user") || strings.Contains(string(redacted), "test-password") || !strings.Contains(string(redacted), "a=rtcp-fb:96 ack ccfb") {
		t.Fatalf("redacted SDP must preserve feedback and remove ICE credentials: %v", err)
	}
	for _, name := range []string{"local-offer.raw.sdp", "local-offer.sdp", "session-trace.jsonl"} {
		info, err := os.Stat(filepath.Join(dir, name))
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatalf("private permissions for %s: %v", name, err)
		}
	}
	if _, err := newSessionRecorder(dir); err == nil {
		t.Fatal("an existing trace must not be replaced")
	}
	if err := recorder.sdp("local", offer); err == nil {
		t.Fatal("an existing SDP artifact must not be replaced")
	}
}

// TestTraceLimitIsVisible ensures incomplete recordings carry a clear marker.
func TestTraceLimitIsVisible(t *testing.T) {
	dir := t.TempDir()
	recorder, err := newSessionRecorder(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer recorder.close()
	recorder.bytes = 64 << 20
	if err := recorder.event("feedback", map[string]any{"received": 1}); err != nil {
		t.Fatal(err)
	}
	if err := recorder.event("feedback", map[string]any{"received": 2}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(dir, "session-trace.jsonl"))
	if err != nil || strings.Count(string(data), "\n") != 1 || !strings.Contains(string(data), "trace_truncated") {
		t.Fatalf("missing explicit trace limit marker: %s (%v)", data, err)
	}
}

// recordingEstimatorFixture supplies deterministic controller observations.
type recordingEstimatorFixture struct {
	cc.BandwidthEstimator
	calls int
	err   error
}

// GetStats distinguishes the state before and after the actual RTCP dispatch.
func (e *recordingEstimatorFixture) GetStats() map[string]any {
	ce := 0
	if e.calls > 0 {
		ce = 2
	}
	return map[string]any{"ce": ce}
}

// WriteRTCP accepts the expected decoded report and preserves a configured error.
func (e *recordingEstimatorFixture) WriteRTCP(packets []rtcp.Packet, _ interceptor.Attributes) error {
	if len(packets) != 1 {
		return errors.New("unexpected RTCP batch")
	}
	e.calls++
	return e.err
}

// TestFeedbackRecordingPreservesDispatchAndError verifies trace contents and
// proves recording does not swallow a controller rejection or call it twice.
func TestFeedbackRecordingPreservesDispatchAndError(t *testing.T) {
	for _, failure := range []bool{false, true} {
		dir := t.TempDir()
		recorder, err := newSessionRecorder(dir)
		if err != nil {
			t.Fatal(err)
		}
		underlying := &recordingEstimatorFixture{}
		if failure {
			underlying.err = errors.New("test controller rejection")
		}
		estimator := &recordedEstimator{BandwidthEstimator: underlying, recorder: recorder, controller: "prague"}
		report := &rtcp.CCFeedbackReport{ReportBlocks: []rtcp.CCFeedbackReportBlock{{MediaSSRC: 42, BeginSequence: 9,
			MetricBlocks: []rtcp.CCFeedbackMetricBlock{{Received: true, ECN: rtcp.ECNCE, ArrivalTimeOffset: 3}}}}}
		err = estimator.WriteRTCP([]rtcp.Packet{report}, nil)
		if underlying.calls != 1 || (failure && !errors.Is(err, underlying.err)) || (!failure && err != nil) {
			t.Fatalf("recording changed controller dispatch: %v", err)
		}
		if err := recorder.close(); err != nil {
			t.Fatal(err)
		}
		data, err := os.ReadFile(filepath.Join(dir, "session-trace.jsonl"))
		if err != nil {
			t.Fatal(err)
		}
		var event struct {
			Event string `json:"event"`
			Data  struct {
				Before  map[string]int `json:"state_before_dispatch"`
				After   map[string]int `json:"state_after_dispatch"`
				Outcome string         `json:"outcome"`
			} `json:"data"`
		}
		if err := json.Unmarshal(data, &event); err != nil {
			t.Fatal(err)
		}
		if event.Event != "controller_feedback" || event.Data.Before["ce"] != 0 || event.Data.After["ce"] != 2 || (event.Data.Outcome == "rejected") != failure {
			t.Fatalf("incorrect controller trace: %s", data)
		}
	}
}
