// These tests cover malformed media, canceled startup, and configuration guards
// that prevent a lab run from silently using a different controller or address.
package main

import (
	"context"
	"encoding/binary"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestPragueReadinessRejectsGCC prevents marking toward a mismatched receiver.
func TestPragueReadinessRejectsGCC(t *testing.T) {
	for _, controller := range []string{"gcc", "prague"} {
		t.Run(controller, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/healthz" {
					t.Errorf("unexpected readiness path %s", r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, `{"service":"pion-l4s","controller":"`+controller+`","feedback":"ccfb","ecn_observation":true,"session_claimed":false}`)
			}))
			defer server.Close()
			err := checkPragueReceiver(context.Background(), config{peer: server.URL + "/offer", localIP: "127.0.0.1"})
			if (err == nil) != (controller == "prague") {
				t.Fatalf("readiness %s: %v", controller, err)
			}
		})
	}
}

// fixture creates a tiny IVF container for parser validation without disk I/O.
func fixture() []byte {
	data := make([]byte, 32+12+3)
	copy(data, "DKIF")
	binary.LittleEndian.PutUint16(data[6:8], 32)
	copy(data[8:12], "VP80")
	binary.LittleEndian.PutUint32(data[16:20], 30)
	binary.LittleEndian.PutUint32(data[20:24], 1)
	binary.LittleEndian.PutUint32(data[32:36], 3)
	copy(data[44:], []byte{0, 1, 2})
	return data
}

// TestClipValidation checks frame timing and rejects malformed media containers.
func TestClipValidation(t *testing.T) {
	t.Run("bounded frame and frame interval", func(t *testing.T) {
		clip, err := parseClip(fixture())
		if err != nil || len(clip.frames) != 1 || clip.interval != time.Second/30 {
			t.Fatalf("clip=%+v error=%v", clip, err)
		}
	})
	for _, name := range []string{"oversized frame", "truncated header", "zero timebase", "not a keyframe"} {
		t.Run(name, func(t *testing.T) {
			data := fixture()
			switch name {
			case "oversized frame":
				binary.LittleEndian.PutUint32(data[32:36], ^uint32(0))
			case "truncated header":
				data = data[:40]
			case "zero timebase":
				binary.LittleEndian.PutUint32(data[16:20], 0)
			case "not a keyframe":
				data[44] = 1
			}
			if _, err := parseClip(data); err == nil {
				t.Fatal("expected invalid IVF to fail")
			}
		})
	}
}

// TestCancelledStartup ensures cancellation prevents listener creation.
func TestCancelledStartup(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	c := config{role: "receiver", controller: "gcc", feedback: "ccfb", initialRate: 1_000_000, listen: "invalid address"}
	if err := run(ctx, c, slog.New(slog.NewJSONHandler(io.Discard, nil))); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled startup must stop before binding a listener: %v", err)
	}
}

// TestPragueCannotSilentlyRunGCC requires the ECN transport inputs for Prague.
func TestPragueCannotSilentlyRunGCC(t *testing.T) {
	c := config{role: "sender", controller: "prague", feedback: "ccfb", initialRate: 1_000_000}
	if err := c.validate(); err == nil {
		t.Fatal("unimplemented Prague must fail rather than run a mislabeled baseline")
	}
}

// TestLabNetworkGuards rejects partial port ranges and unowned lab addresses.
func TestLabNetworkGuards(t *testing.T) {
	for _, ports := range [][2]uint{{50000, 0}, {0, 50100}, {50100, 50000}, {50000, 65536}} {
		c := config{role: "receiver", controller: "gcc", feedback: "ccfb", initialRate: 1_000_000, udpMin: ports[0], udpMax: ports[1]}
		if err := c.validate(); err == nil {
			t.Fatalf("invalid UDP range %v passed", ports)
		}
	}
	c := config{role: "receiver", controller: "gcc", feedback: "ccfb", initialRate: 1_000_000, localIP: "192.168.1.220"}
	if err := c.validate(); err == nil {
		t.Fatal("local-ip without an interface must fail")
	}
	interfaces, err := net.Interfaces()
	if err != nil {
		t.Fatal(err)
	}
	for _, iface := range interfaces {
		if iface.Flags&net.FlagLoopback == 0 || iface.Flags&net.FlagUp == 0 {
			continue
		}
		c.iface, c.localIP, c.listen = iface.Name, "127.0.0.1", "127.0.0.1:8080"
		if err := c.validate(); err == nil {
			t.Fatal("loopback must require the explicit smoke-check flag")
		}
		c.loopback = true
		if err := c.validate(); err != nil {
			t.Fatalf("explicit loopback check should be permitted: %v", err)
		}
		c.listen = "0.0.0.0:8080"
		if err := c.validate(); err == nil {
			t.Fatal("wildcard signaling bind must not bypass a selected local-ip")
		}
		return
	}
	t.Skip("no active loopback interface")
}
