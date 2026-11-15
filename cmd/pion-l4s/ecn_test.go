// Exercise ancillary ECN on real loopback UDP sockets and verify the RFC 8888
// recorder preserves it. No lab equipment or host routes are changed.
package main

import (
	"encoding/binary"
	"net"
	"testing"
	"time"

	"github.com/pion/interceptor/pkg/ccfb"
	"github.com/pion/rtcp"
	"golang.org/x/sys/unix"
)

// TestECNMetadataToFeedback proves socket ECT(1)/CE values survive report marshal.
func TestECNMetadataToFeedback(t *testing.T) {
	receiver, err := newECNSocket(config{localIP: "127.0.0.1"})
	if err != nil {
		t.Fatal(err)
	}
	defer receiver.Close()
	sender, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer sender.Close()
	if err := receiver.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	recorder := ccfb.NewRecorder()
	for i, bits := range []int{1, 3} {
		raw, err := sender.SyscallConn()
		if err != nil {
			t.Fatal(err)
		}
		var optionErr error
		if err := raw.Control(func(fd uintptr) { optionErr = unix.SetsockoptInt(int(fd), unix.IPPROTO_IP, unix.IP_TOS, bits) }); err != nil {
			t.Fatal(err)
		}
		if optionErr != nil {
			t.Fatal(optionErr)
		}
		packet := make([]byte, 16)
		packet[0] = 0x80
		packet[1] = 96
		binary.BigEndian.PutUint16(packet[2:], uint16(i+1))
		binary.BigEndian.PutUint32(packet[8:], 42)
		if _, err := sender.WriteToUDP(packet, receiver.LocalAddr().(*net.UDPAddr)); err != nil {
			t.Fatal(err)
		}
		if _, _, err := receiver.ReadFrom(make([]byte, 1500)); err != nil {
			t.Fatal(err)
		}
		sample, err := receiver.take(42, uint16(i+1))
		if err != nil {
			t.Fatal(err)
		}
		if sample.bits != uint8(bits) {
			t.Fatalf("ECN changed: got %d want %d", sample.bits, bits)
		}
		recorder.AddPacket(sample.at, 42, sample.seq, sample.bits)
	}
	packet := recorder.BuildReport(time.Now(), 1100)
	wire, err := packet.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	decoded := &rtcp.CCFeedbackReport{}
	if err := decoded.Unmarshal(wire); err != nil {
		t.Fatal(err)
	}
	metrics := decoded.ReportBlocks[0].MetricBlocks
	if len(metrics) != 2 || metrics[0].ECN != rtcp.ECNECT1 || metrics[1].ECN != rtcp.ECNCE {
		t.Fatalf("ECN feedback changed: %v", metrics)
	}
}

// TestRTPClassification keeps ICE, DTLS and RTCP out of the L4S data marking path.
func TestRTPClassification(t *testing.T) {
	for _, pt := range []byte{200, 201, 205, 206} {
		b := make([]byte, 20)
		b[0] = 0x80
		b[1] = pt
		if _, _, ok := rtpIdentity(b); ok {
			t.Fatalf("RTCP %d classified as data", pt)
		}
	}
	for _, first := range []byte{0, 1, 20, 22, 23} {
		b := make([]byte, 20)
		b[0] = first
		if _, _, ok := rtpIdentity(b); ok {
			t.Fatalf("non-RTP %d classified as data", first)
		}
	}
}
