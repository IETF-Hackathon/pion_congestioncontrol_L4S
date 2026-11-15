// The Prague receiver reuses Pion's RFC 8888 recorder with actual socket ECN
// observations and pre-decryption arrival timestamps instead of hardcoded zero.
package main

import (
	"sync"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/ccfb"
	"github.com/pion/rtcp"
)

type ecnFeedbackFactory struct {
	socket    *ecnSocket
	report    func(error)
	recording *sessionRecorder
}

// NewInterceptor allocates feedback state per PeerConnection.
func (f *ecnFeedbackFactory) NewInterceptor(_ string) (interceptor.Interceptor, error) {
	return &ecnFeedback{socket: f.socket, report: f.report, recorder: ccfb.NewRecorder(), done: make(chan struct{}), recording: f.recording}, nil
}

type ecnFeedback struct {
	interceptor.NoOp
	socket    *ecnSocket
	report    func(error)
	mu        sync.Mutex
	recorder  *ccfb.Recorder
	done      chan struct{}
	wg        sync.WaitGroup
	once      sync.Once
	closeOnce sync.Once
	recording *sessionRecorder
}

// BindRemoteStream records only authenticated RTP together with its socket ECN.
func (f *ecnFeedback) BindRemoteStream(_ *interceptor.StreamInfo, reader interceptor.RTPReader) interceptor.RTPReader {
	return interceptor.RTPReaderFunc(func(b []byte, a interceptor.Attributes) (int, interceptor.Attributes, error) {
		n, attrs, err := reader.Read(b, a)
		if err != nil {
			return n, attrs, err
		}
		if attrs == nil {
			attrs = make(interceptor.Attributes)
		}
		h, err := attrs.GetRTPHeader(b[:n])
		if err != nil {
			return 0, attrs, err
		}
		sample, err := f.socket.take(h.SSRC, h.SequenceNumber)
		if err != nil {
			f.report(err)
			return 0, attrs, err
		}
		f.mu.Lock()
		f.recorder.AddPacket(sample.at, h.SSRC, h.SequenceNumber, sample.bits)
		f.mu.Unlock()
		return n, attrs, nil
	})
}

// BindRTCPWriter starts one bounded feedback loop, joined by Close.
func (f *ecnFeedback) BindRTCPWriter(writer interceptor.RTCPWriter) interceptor.RTCPWriter {
	f.once.Do(func() {
		f.wg.Add(1)
		go func() {
			defer f.wg.Done()
			ticker := time.NewTicker(20 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-f.done:
					return
				case now := <-ticker.C:
					f.mu.Lock()
					report := f.recorder.BuildReport(now, 1100)
					f.mu.Unlock()
					count := 0
					for _, b := range report.ReportBlocks {
						count += len(b.MetricBlocks)
					}
					if count == 0 {
						continue
					}
					if err := f.recording.event("ccfb_generated", map[string]any{"packet": report, "metric_count": count,
						"path": "authenticated RTP -> socket ECN correlation -> Pion ccfb.Recorder.BuildReport"}); err != nil {
						f.report(err)
						return
					}
					if _, err := writer.Write([]rtcp.Packet{report}, nil); err != nil {
						f.report(err)
						return
					}
					if err := f.recording.event("ccfb_written", map[string]any{"report_timestamp": report.ReportTimestamp, "metric_count": count}); err != nil {
						f.report(err)
						return
					}
				}
			}
		}()
	})
	return writer
}

// Close stops report generation before releasing the interceptor.
func (f *ecnFeedback) Close() error {
	f.closeOnce.Do(func() { close(f.done) })
	f.wg.Wait()
	return nil
}
