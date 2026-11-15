// Optional session recording preserves SDP and the actual Pion-to-controller
// RTCP handoff. Raw ICE credentials stay in private, ignored run artifacts;
// redacted SDP copies and structured feedback make the call reviewable.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"
)

// sessionRecorder serializes a bounded trace and owns its private output file.
type sessionRecorder struct {
	mu        sync.Mutex
	dir       string
	file      *os.File
	sequence  uint64
	bytes     int
	truncated bool
	closed    bool
}

// newSessionRecorder creates a new trace without replacing an earlier recording.
// An empty directory disables recording and returns a nil recorder.
func newSessionRecorder(dir string) (*sessionRecorder, error) {
	if dir == "" {
		return nil, nil
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, fmt.Errorf("create recording directory: %w", err)
	}
	file, err := os.OpenFile(filepath.Join(dir, "session-trace.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, fmt.Errorf("create session trace: %w", err)
	}
	return &sessionRecorder{dir: dir, file: file}, nil
}

// event records a timestamped observation. A 64 MiB limit emits one explicit
// truncation marker; later events are skipped without affecting media control.
func (r *sessionRecorder) event(name string, data any) error {
	if r == nil {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed || r.truncated {
		return nil
	}
	r.sequence++
	entry := map[string]any{"time": time.Now().UTC().Format(time.RFC3339Nano), "sequence": r.sequence, "event": name, "data": data}
	encoded, err := json.Marshal(entry)
	if err != nil {
		return fmt.Errorf("encode session event: %w", err)
	}
	if r.bytes+len(encoded)+1 > 64<<20 {
		r.truncated = true
		entry["event"] = "trace_truncated"
		entry["data"] = map[string]any{"limit_bytes": 64 << 20, "reason": "recording size limit reached; subsequent events omitted"}
		encoded, err = json.Marshal(entry)
		if err != nil {
			return fmt.Errorf("encode trace limit marker: %w", err)
		}
	}
	n, err := r.file.Write(append(encoded, '\n'))
	r.bytes += n
	if err != nil {
		return fmt.Errorf("write session trace: %w", err)
	}
	return nil
}

// sdp stores exact and credential-redacted descriptions, plus advertised SDP
// facts. Saving a description does not claim Pion successfully applied it.
func (r *sessionRecorder) sdp(side string, description *webrtc.SessionDescription) error {
	if r == nil {
		return nil
	}
	if (side != "local" && side != "remote") || description == nil || (description.Type != webrtc.SDPTypeOffer && description.Type != webrtc.SDPTypeAnswer) {
		return fmt.Errorf("record SDP requires local/remote and an offer/answer")
	}
	stem := side + "-" + description.Type.String()
	rawName, redactedName := stem+".raw.sdp", stem+".sdp"
	if err := writeNewPrivateFile(filepath.Join(r.dir, rawName), []byte(description.SDP)); err != nil {
		return err
	}
	redacted := redactSDP(description.SDP)
	if err := writeNewPrivateFile(filepath.Join(r.dir, redactedName), []byte(redacted)); err != nil {
		return err
	}
	var feedback, codecs, candidates []string
	for _, line := range strings.Split(strings.ReplaceAll(redacted, "\r\n", "\n"), "\n") {
		switch {
		case strings.HasPrefix(line, "a=rtcp-fb:"):
			feedback = append(feedback, line)
		case strings.HasPrefix(line, "a=rtpmap:"):
			codecs = append(codecs, line)
		case strings.HasPrefix(line, "a=candidate:"):
			candidates = append(candidates, line)
		}
	}
	digest := sha256.Sum256([]byte(description.SDP))
	return r.event("sdp_recorded", map[string]any{"side": side, "type": description.Type.String(),
		"raw_file": rawName, "redacted_file": redactedName, "raw_sha256": hex.EncodeToString(digest[:]),
		"advertised_feedback": feedback, "advertised_codecs": codecs, "candidates": candidates,
		"general_sdp_ecn_negotiation": false})
}

// writeNewPrivateFile writes an artifact with owner-only access and no overwrite.
func writeNewPrivateFile(path string, data []byte) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return fmt.Errorf("create recording %s: %w", filepath.Base(path), err)
	}
	_, writeErr := file.Write(data)
	return errors.Join(writeErr, file.Close())
}

// redactSDP removes ICE usernames/passwords while retaining codec, feedback,
// fingerprint and candidate lines needed to inspect the lab negotiation.
func redactSDP(value string) string {
	lines := strings.Split(value, "\n")
	for i, line := range lines {
		for _, prefix := range []string{"a=ice-ufrag:", "a=ice-pwd:"} {
			if strings.HasPrefix(line, prefix) {
				ending := ""
				if strings.HasSuffix(line, "\r") {
					ending = "\r"
				}
				lines[i] = prefix + "[REDACTED]" + ending
			}
		}
	}
	return strings.Join(lines, "\n")
}

// close finishes the recorder after Pion and its feedback goroutines stop.
func (r *sessionRecorder) close() error {
	if r == nil {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return nil
	}
	r.closed = true
	return r.file.Close()
}

// recordedEstimator preserves the estimator API while observing the exact
// decoded RTCP dispatch made by Pion's congestion-control interceptor.
type recordedEstimator struct {
	cc.BandwidthEstimator
	recorder   *sessionRecorder
	controller string
}

// WriteRTCP records parsed reports, controller snapshots and the handling result.
// Snapshot intervals can include concurrent RTP sends; they are not atomic with
// the pacer and do not independently establish causal congestion performance.
func (e *recordedEstimator) WriteRTCP(packets []rtcp.Packet, attributes interceptor.Attributes) error {
	before := e.BandwidthEstimator.GetStats()
	err := e.BandwidthEstimator.WriteRTCP(packets, attributes)
	after := e.BandwidthEstimator.GetStats()
	var types []string
	for _, packet := range packets {
		types = append(types, fmt.Sprintf("%T", packet))
	}
	outcome, message := "returned_without_error", ""
	if err != nil {
		outcome, message = "rejected", err.Error()
	}
	recordErr := e.recorder.event("controller_feedback", map[string]any{
		"path":       "Pion cc.BindRTCPReader -> Attributes.GetRTCPPackets -> BandwidthEstimator.WriteRTCP",
		"controller": e.controller, "packet_types": types, "packets": packets,
		"state_before_dispatch": before, "state_after_dispatch": after,
		"outcome": outcome, "handling_error": message,
		"arrival_time_offset_unit": "1/1024 second", "ecn_codepoints": "0=Non-ECT, 1=ECT(1), 2=ECT(0), 3=CE",
	})
	return errors.Join(err, recordErr)
}
