// Command pion-l4s runs a lab sender or receiver with GCC, direct IPv4 ICE,
// HTTP signaling, and transport logs. Prague and IP ECN are not implemented.
// Pion API usage follows the MIT-licensed bandwidth-estimation-from-disk example.
// See THIRD_PARTY_NOTICES.md for source attribution and license text.
package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/interceptor/pkg/gcc"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

type config struct {
	role, listen, peer, iface, localIP, video, controller, feedback string
	loopback                                                        bool
	duration                                                        time.Duration
	initialRate                                                     int
	udpMin, udpMax                                                  uint
}

// main parses endpoint flags and owns process cancellation and error reporting.
func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	var c config
	flag.StringVar(&c.role, "role", "receiver", "receiver or sender")
	flag.StringVar(&c.listen, "listen", "127.0.0.1:8080", "receiver signaling listen address; use the laptop's lab IP")
	flag.StringVar(&c.peer, "peer", "http://127.0.0.1:8080/offer", "receiver signaling endpoint (sender only)")
	flag.StringVar(&c.iface, "interface", "", "only gather ICE candidates on this interface, e.g. en7 or eth0")
	flag.StringVar(&c.localIP, "local-ip", "", "only gather this IPv4 address; requires --interface")
	flag.UintVar(&c.udpMin, "udp-port-min", 0, "first ICE UDP port; set both port flags or leave both zero")
	flag.UintVar(&c.udpMax, "udp-port-max", 0, "last ICE UDP port, inclusive")
	flag.StringVar(&c.video, "video", "", "constant-frame-rate VP8 IVF input (sender only)")
	flag.StringVar(&c.controller, "controller", "gcc", "sender controller; gcc implemented, prague reserved")
	flag.StringVar(&c.feedback, "feedback", "ccfb", "ccfb (RFC 8888) or twcc; use the same value at both ends")
	flag.BoolVar(&c.loopback, "loopback", false, "include loopback ICE candidates for local smoke checks")
	flag.DurationVar(&c.duration, "duration", 0, "whole-process time limit including setup; zero runs until Ctrl+C")
	flag.IntVar(&c.initialRate, "initial-bitrate", 1_000_000, "initial GCC target in bits/second")
	flag.Parse()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if c.duration > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.duration)
		defer cancel()
	}
	if err := run(ctx, c, log); err != nil && ctx.Err() == nil {
		log.Error("application failed", "error", err)
		os.Exit(1)
	}
}

// validate rejects unsupported controller modes and invalid network/media flags.
func (c config) validate() error {
	if c.role != "receiver" && c.role != "sender" {
		return fmt.Errorf("role must be receiver or sender")
	}
	if c.controller != "gcc" {
		return fmt.Errorf("controller %q is not implemented; Prague requires a controller adapter and verified ECN transport", c.controller)
	}
	if c.feedback != "ccfb" && c.feedback != "twcc" {
		return fmt.Errorf("feedback must be ccfb or twcc")
	}
	if c.duration < 0 || c.initialRate < 100_000 || c.initialRate > 50_000_000 {
		return fmt.Errorf("duration must be nonnegative and initial bitrate must be 100000..50000000 bps")
	}
	if (c.udpMin == 0) != (c.udpMax == 0) || c.udpMin > c.udpMax || c.udpMax > 65535 {
		return fmt.Errorf("UDP port range requires 1 <= min <= max <= 65535, or both zero")
	}
	if c.iface != "" {
		if _, err := net.InterfaceByName(c.iface); err != nil {
			return fmt.Errorf("select interface %q: %w", c.iface, err)
		}
	}
	if c.localIP != "" {
		if err := c.validateLocalIP(); err != nil {
			return err
		}
	}
	if c.role == "sender" {
		if c.video == "" {
			return fmt.Errorf("sender requires --video path/to/video.ivf")
		}
		u, err := url.ParseRequestURI(c.peer)
		if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
			return fmt.Errorf("peer must be an absolute HTTP(S) signaling URL")
		}
	}
	return nil
}

// validateLocalIP requires the selected IPv4 address to belong to an active
// interface and requires receiver signaling to bind that same address.
func (c config) validateLocalIP() error {
	ip := net.ParseIP(c.localIP)
	if ip == nil || ip.To4() == nil || ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() || (ip.IsLoopback() && !c.loopback) {
		return fmt.Errorf("local-ip must be an assigned IPv4 unicast address (loopback requires --loopback)")
	}
	iface, err := net.InterfaceByName(c.iface)
	if err != nil {
		return fmt.Errorf("local-ip requires a valid --interface: %w", err)
	}
	if iface.Flags&net.FlagUp == 0 {
		return fmt.Errorf("interface %s is down", c.iface)
	}
	addresses, err := iface.Addrs()
	if err != nil {
		return fmt.Errorf("read interface addresses: %w", err)
	}
	found := false
	for _, address := range addresses {
		assigned, _, err := net.ParseCIDR(address.String())
		if err == nil && assigned.Equal(ip) {
			found = true
		}
	}
	if !found {
		return fmt.Errorf("local-ip %s is not assigned to interface %s", c.localIP, c.iface)
	}
	if c.role == "receiver" {
		host, _, err := net.SplitHostPort(c.listen)
		if err != nil || !net.ParseIP(host).Equal(ip) {
			return fmt.Errorf("receiver --listen must bind the selected local-ip %s", c.localIP)
		}
	}
	return nil
}

type endpoint struct {
	pc              *webrtc.PeerConnection
	estimator       cc.BandwidthEstimator
	connected       chan struct{}
	tracks          chan *webrtc.TrackRemote
	errors          chan error
	wg              sync.WaitGroup
	packets         atomic.Uint64
	payload         atomic.Uint64
	feedbacks       atomic.Uint64
	feedbackReports atomic.Uint64
	log             *slog.Logger
}

// newEndpoint configures Pion codecs, GCC, feedback, ICE filters, and callbacks.
func newEndpoint(c config, log *slog.Logger) (*endpoint, error) {
	e := &endpoint{connected: make(chan struct{}), tracks: make(chan *webrtc.TrackRemote, 1), errors: make(chan error, 1), log: log}
	m, registry := &webrtc.MediaEngine{}, &interceptor.Registry{}
	if err := m.RegisterDefaultCodecs(); err != nil {
		return nil, fmt.Errorf("register codecs: %w", err)
	}
	if c.role == "sender" {
		factory, err := cc.NewInterceptor(func() (cc.BandwidthEstimator, error) {
			return gcc.NewSendSideBWE(gcc.SendSideBWEInitialBitrate(c.initialRate))
		})
		if err != nil {
			return nil, fmt.Errorf("create GCC: %w", err)
		}
		factory.OnNewPeerConnection(func(_ string, estimator cc.BandwidthEstimator) { e.estimator = estimator })
		registry.Add(factory)
	}
	if err := webrtc.ConfigureRTCPReports(registry); err != nil {
		return nil, fmt.Errorf("configure reports: %w", err)
	}
	var err error
	if c.feedback == "ccfb" {
		err = webrtc.ConfigureCongestionControlFeedback(m, registry)
	} else {
		err = webrtc.ConfigureTWCCHeaderExtensionSender(m, registry)
		if err == nil {
			err = webrtc.ConfigureTWCCSender(m, registry)
		}
	}
	if err != nil {
		return nil, fmt.Errorf("configure congestion feedback: %w", err)
	}
	settings := webrtc.SettingEngine{}
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	settings.SetIncludeLoopbackCandidate(c.loopback)
	if c.iface != "" {
		settings.SetInterfaceFilter(func(name string) bool { return name == c.iface })
	}
	if c.localIP != "" {
		settings.SetIPFilter(func(ip net.IP) bool { return ip.Equal(net.ParseIP(c.localIP)) })
	}
	if c.udpMin != 0 {
		if err := settings.SetEphemeralUDPPortRange(uint16(c.udpMin), uint16(c.udpMax)); err != nil {
			return nil, fmt.Errorf("set ICE UDP port range: %w", err)
		}
	}
	api := webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(registry), webrtc.WithSettingEngine(settings))
	e.pc, err = api.NewPeerConnection(webrtc.Configuration{}) // Direct host candidates; no public STUN/TURN.
	if err != nil {
		return nil, fmt.Errorf("create PeerConnection: %w", err)
	}
	var connected sync.Once
	e.pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		log.Info("peer state", "state", state.String())
		if state == webrtc.PeerConnectionStateConnected {
			connected.Do(func() { close(e.connected) })
			e.logSelectedPair() // Use the RTP transport: this session has no data channel.
		}
		if state == webrtc.PeerConnectionStateFailed {
			e.report(fmt.Errorf("PeerConnection failed; inspect Ethernet connectivity and selected ICE candidates"))
		}
	})
	e.pc.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		select {
		case e.tracks <- track:
		default:
		}
	})
	return e, nil
}

// logSelectedPair records the selected media candidates from the RTP transport.
func (e *endpoint) logSelectedPair() {
	var transport *webrtc.DTLSTransport
	for _, sender := range e.pc.GetSenders() {
		if sender.Transport() != nil {
			transport = sender.Transport()
			break
		}
	}
	if transport == nil {
		for _, receiver := range e.pc.GetReceivers() {
			if receiver.Transport() != nil {
				transport = receiver.Transport()
				break
			}
		}
	}
	if transport == nil {
		e.log.Warn("selected ICE pair unavailable", "reason", "RTP transport is absent")
		return
	}
	pair, err := transport.ICETransport().GetSelectedCandidatePair()
	if err != nil || pair == nil {
		e.log.Warn("selected ICE pair unavailable", "error", err)
		return
	}
	e.log.Info("selected ICE pair", "local", pair.Local.String(), "remote", pair.Remote.String())
}

// report queues the first pending asynchronous error without blocking callbacks.
func (e *endpoint) report(err error) {
	select {
	case e.errors <- err:
	default:
	}
}

// close releases Pion transports, unblocks readers, and joins owned goroutines.
func (e *endpoint) close() {
	if err := e.pc.Close(); err != nil {
		e.log.Warn("close PeerConnection", "error", err)
	}
	e.wg.Wait() // Closing the PeerConnection unblocks the RTP/RTCP readers.
}

// run validates startup, owns endpoint cleanup, and dispatches the chosen role.
func run(ctx context.Context, c config, log *slog.Logger) error {
	if err := c.validate(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	e, err := newEndpoint(c, log.With("role", c.role))
	if err != nil {
		return err
	}
	defer e.close()
	if err := ctx.Err(); err != nil {
		return err
	}
	log.Info("capabilities", "controller", c.controller, "feedback", c.feedback, "ecn_observation", "not implemented", "prague", false, "encoder_adaptation", false)
	log.Info("network settings", "interface", c.iface, "local_ip", c.localIP, "udp_port_min", c.udpMin, "udp_port_max", c.udpMax)
	if c.role == "receiver" {
		return receive(ctx, c, e)
	}
	return send(ctx, c, e)
}

// receive serves health/signaling, drains incoming RTP, and logs payload counters
// until cancellation or a transport failure. One process accepts one session.
func receive(ctx context.Context, c config, e *endpoint) error {
	if _, err := e.pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		return fmt.Errorf("add receive transceiver: %w", err)
	}
	listener, err := (&net.ListenConfig{}).Listen(ctx, "tcp", c.listen)
	if err != nil {
		return fmt.Errorf("listen for signaling: %w", err)
	}
	var claimed atomic.Bool
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(map[string]any{
			"service": "pion-l4s", "role": "receiver", "feedback": c.feedback,
			"local_ip": c.localIP, "session_claimed": claimed.Load(),
			"prague": false, "ecn_observation": false,
		}); err != nil {
			e.log.Warn("write health response", "error", err)
		}
	})
	mux.HandleFunc("POST /offer", func(w http.ResponseWriter, r *http.Request) {
		if !claimed.CompareAndSwap(false, true) {
			http.Error(w, "one sender per receiver process; restart for another run", http.StatusConflict)
			return
		}
		// A failed offer consumes this single-session receiver; fail visibly and restart.
		answer, offerErr := answerOffer(r.Context(), e.pc, http.MaxBytesReader(w, r.Body, 1<<20))
		if offerErr != nil {
			http.Error(w, "offer failed; inspect receiver logs", http.StatusBadRequest)
			e.report(offerErr)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(answer); err != nil {
			e.report(fmt.Errorf("return SDP answer: %w", err))
		}
	})
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 20 * time.Second, IdleTimeout: 30 * time.Second, BaseContext: func(net.Listener) context.Context { return ctx }}
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			e.report(fmt.Errorf("serve signaling: %w", err))
		}
	}()
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if err := server.Shutdown(cleanup); err != nil {
			e.log.Warn("shutdown signaling", "error", err)
			if closeErr := server.Close(); closeErr != nil {
				e.log.Warn("close signaling", "error", closeErr)
			}
		}
		<-done
	}()
	if err := ctx.Err(); err != nil {
		return err
	}
	e.log.Info("receiver ready", "signaling", "http://"+listener.Addr().String()+"/offer")
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	lastTime, lastBytes := time.Now(), uint64(0)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err := <-e.errors:
			return err
		case track := <-e.tracks:
			e.log.Info("receiving track", "codec", track.Codec().MimeType, "ssrc", track.SSRC())
			e.wg.Add(1)
			go func() {
				defer e.wg.Done()
				for {
					packet, _, readErr := track.ReadRTP()
					if readErr != nil {
						return
					}
					e.packets.Add(1)
					e.payload.Add(uint64(len(packet.Payload)))
				}
			}()
		case now := <-ticker.C:
			bytes := e.payload.Load()
			e.log.Info("receiver metrics", "packets", e.packets.Load(), "rtp_payload_bytes", bytes, "rtp_payload_mbps", float64(bytes-lastBytes)*8/now.Sub(lastTime).Seconds()/1e6, "ce_count", nil)
			lastTime, lastBytes = now, bytes
		}
	}
}

// answerOffer applies one SDP offer and returns an answer with gathered candidates.
func answerOffer(ctx context.Context, pc *webrtc.PeerConnection, body io.Reader) (*webrtc.SessionDescription, error) {
	var offer webrtc.SessionDescription
	if err := json.NewDecoder(body).Decode(&offer); err != nil {
		return nil, fmt.Errorf("decode offer: %w", err)
	}
	if offer.Type != webrtc.SDPTypeOffer {
		return nil, fmt.Errorf("expected an SDP offer")
	}
	if err := pc.SetRemoteDescription(offer); err != nil {
		return nil, fmt.Errorf("set remote offer: %w", err)
	}
	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		return nil, fmt.Errorf("create answer: %w", err)
	}
	if err := gather(ctx, pc, answer); err != nil {
		return nil, err
	}
	return pc.LocalDescription(), nil
}

// gather installs the local SDP and waits for ICE gathering or cancellation.
func gather(ctx context.Context, pc *webrtc.PeerConnection, description webrtc.SessionDescription) error {
	complete := webrtc.GatheringCompletePromise(pc)
	if err := pc.SetLocalDescription(description); err != nil {
		return fmt.Errorf("set local description: %w", err)
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-complete:
		return nil
	}
}

// signalSender exchanges a complete SDP offer/answer over bounded HTTP signaling.
func signalSender(ctx context.Context, pc *webrtc.PeerConnection, peer string) error {
	setup, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	offer, err := pc.CreateOffer(nil)
	if err != nil {
		return fmt.Errorf("create offer: %w", err)
	}
	if err := gather(setup, pc, offer); err != nil {
		return err
	}
	body, err := json.Marshal(pc.LocalDescription())
	if err != nil {
		return fmt.Errorf("encode offer: %w", err)
	}
	request, err := http.NewRequestWithContext(setup, http.MethodPost, peer, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("make signaling request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	transport := &http.Transport{}
	defer transport.CloseIdleConnections()
	response, err := (&http.Client{Transport: transport}).Do(request)
	if err != nil {
		return fmt.Errorf("exchange SDP with %s: %w", peer, err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("receiver signaling returned %s", response.Status)
	}
	var answer webrtc.SessionDescription
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&answer); err != nil {
		return fmt.Errorf("decode answer: %w", err)
	}
	if answer.Type != webrtc.SDPTypeAnswer {
		return fmt.Errorf("expected an SDP answer")
	}
	if err := pc.SetRemoteDescription(answer); err != nil {
		return fmt.Errorf("set remote answer: %w", err)
	}
	return setup.Err()
}

// send loops a VP8 clip through the GCC pacer, drains RTCP, and reports controller
// counters. It does not adapt the prerecorded source to the GCC target bitrate.
func send(ctx context.Context, c config, e *endpoint) error {
	clip, err := readClip(c.video)
	if err != nil {
		return err
	}
	track, err := webrtc.NewTrackLocalStaticSample(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8}, "video", "pion-l4s")
	if err != nil {
		return fmt.Errorf("create video track: %w", err)
	}
	sender, err := e.pc.AddTrack(track)
	if err != nil {
		return fmt.Errorf("add video track: %w", err)
	}
	e.wg.Add(1)
	go func() {
		defer e.wg.Done()
		for {
			packets, _, readErr := sender.ReadRTCP()
			if readErr != nil {
				return
			}
			e.feedbacks.Add(1)
			for _, packet := range packets {
				switch packet.(type) {
				case *rtcp.CCFeedbackReport, *rtcp.TransportLayerCC:
					e.feedbackReports.Add(1)
				}
			}
		}
	}()
	if err := signalSender(ctx, e.pc, c.peer); err != nil {
		return err
	}
	connectTimer := time.NewTimer(15 * time.Second)
	defer connectTimer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case err := <-e.errors:
		return err
	case <-connectTimer.C:
		return fmt.Errorf("ICE/DTLS did not connect within 15 seconds")
	case <-e.connected:
	}
	e.log.Info("sender ready", "frames", len(clip.frames), "frame_interval", clip.interval.String(), "source_adaptation", "fixed prerecorded file")
	frames, metrics := time.NewTicker(clip.interval), time.NewTicker(time.Second)
	defer frames.Stop()
	defer metrics.Stop()
	index := 0
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err := <-e.errors:
			return err
		case <-frames.C:
			if err := track.WriteSample(media.Sample{Data: clip.frames[index], Duration: clip.interval}); err != nil {
				return fmt.Errorf("send video sample: %w", err)
			}
			index = (index + 1) % len(clip.frames)
		case <-metrics.C:
			e.log.Info("sender metrics", "gcc_target_bps", e.estimator.GetTargetBitrate(), "rtcp_batches", e.feedbacks.Load(), "congestion_feedback_reports", e.feedbackReports.Load(), "gcc", e.estimator.GetStats(), "encoder_target_bps", nil, "ce_count", nil)
		}
	}
}

type videoClip struct {
	frames   [][]byte
	interval time.Duration
}

// readClip reads a size-bounded IVF file and validates its media framing.
func readClip(path string) (videoClip, error) {
	f, err := os.Open(path)
	if err != nil {
		return videoClip{}, fmt.Errorf("open IVF: %w", err)
	}
	defer f.Close()
	const maxSize = 32 << 20
	data, err := io.ReadAll(io.LimitReader(f, maxSize+1))
	if err != nil {
		return videoClip{}, fmt.Errorf("read IVF: %w", err)
	}
	if len(data) > maxSize {
		return videoClip{}, fmt.Errorf("IVF exceeds 32 MiB; use a short repeatable clip")
	}
	return parseClip(data)
}

// parseClip checks VP8 IVF headers and frame bounds, then derives frame timing.
func parseClip(data []byte) (videoClip, error) {
	if len(data) < 32 || string(data[:4]) != "DKIF" || binary.LittleEndian.Uint16(data[4:6]) != 0 || binary.LittleEndian.Uint16(data[6:8]) != 32 || string(data[8:12]) != "VP80" {
		return videoClip{}, fmt.Errorf("expected version-0 VP8 IVF with a 32-byte header")
	}
	denominator, numerator := binary.LittleEndian.Uint32(data[16:20]), binary.LittleEndian.Uint32(data[20:24])
	if denominator == 0 || numerator == 0 {
		return videoClip{}, fmt.Errorf("IVF timebase must be nonzero")
	}
	interval := time.Duration(float64(time.Second) * float64(numerator) / float64(denominator))
	if interval < time.Second/120 || interval > time.Second {
		return videoClip{}, fmt.Errorf("IVF must use a constant frame rate between 1 and 120 fps")
	}
	clip := videoClip{interval: interval}
	for position := 32; position < len(data); {
		if len(data)-position < 12 {
			return videoClip{}, fmt.Errorf("truncated IVF frame header")
		}
		size := uint64(binary.LittleEndian.Uint32(data[position : position+4]))
		position += 12
		if size == 0 || size > uint64(len(data)-position) || size > 1<<20 {
			return videoClip{}, fmt.Errorf("invalid IVF frame length")
		}
		clip.frames = append(clip.frames, data[position:position+int(size)])
		position += int(size)
	}
	if len(clip.frames) == 0 || clip.frames[0][0]&1 != 0 {
		return videoClip{}, fmt.Errorf("IVF needs at least one frame and must start with a VP8 keyframe")
	}
	return clip, nil
}
