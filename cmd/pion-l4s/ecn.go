// ECN transport for the direct IPv4 lab path. RTP's SSRC/sequence header remains
// visible in SRTP; remember its ancillary ECN metadata, then consume it only
// after Pion authenticates/decrypts the packet. ICE, DTLS and RTCP stay Non-ECT.
package main

import (
	"encoding/binary"
	"fmt"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"golang.org/x/sys/unix"
)

type ecnSample struct {
	ssrc uint32
	seq  uint16
	bits uint8
	at   time.Time
}
type ecnSocket struct {
	conn                                          *net.UDPConn
	mark                                          bool
	writeMu                                       sync.Mutex
	mu                                            sync.Mutex
	samples                                       [65536]ecnSample
	ect1, ce, nonect                              atomic.Uint64
	origin                                        time.Time
	txMediaPackets, txMediaBytes, txMediaFailures atomic.Uint64
	txFirstNS, txLastNS                           atomic.Int64
}

// rtpIdentity recognizes muxed SRTP data, excluding STUN, DTLS and SRTCP.
func rtpIdentity(b []byte) (uint32, uint16, bool) {
	if len(b) < 12 || b[0]>>6 != 2 || (b[1] >= 192 && b[1] <= 223) {
		return 0, 0, false
	}
	return binary.BigEndian.Uint32(b[8:12]), binary.BigEndian.Uint16(b[2:4]), true
}

// newECNSocket binds one local IPv4 port within the permitted ICE range.
func newECNSocket(c config) (*ecnSocket, error) {
	low, high := int(c.udpMin), int(c.udpMax)
	var conn *net.UDPConn
	var err error
	for port := low; port <= high; port++ {
		conn, err = net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP(c.localIP), Port: port})
		if err == nil {
			break
		}
	}
	if err != nil {
		return nil, fmt.Errorf("bind ECN ICE socket: %w", err)
	}
	raw, err := conn.SyscallConn()
	if err == nil {
		var optionErr error
		err = raw.Control(func(fd uintptr) { optionErr = unix.SetsockoptInt(int(fd), unix.IPPROTO_IP, unix.IP_RECVTOS, 1) })
		if err == nil {
			err = optionErr
		}
	}
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("enable received ECN metadata: %w", err)
	}
	return &ecnSocket{conn: conn, mark: c.role == "sender", origin: time.Now()}, nil
}

// ReadFrom captures per-datagram ECN and timestamps before the ICE mux copies it.
func (s *ecnSocket) ReadFrom(b []byte) (int, net.Addr, error) {
	oob := make([]byte, 128)
	n, on, flags, addr, err := s.conn.ReadMsgUDP(b, oob)
	if err != nil {
		return n, addr, err
	}
	if flags&(unix.MSG_CTRUNC|unix.MSG_TRUNC) != 0 {
		return 0, addr, fmt.Errorf("truncated ECN UDP packet/control message")
	}
	if ssrc, seq, ok := rtpIdentity(b[:n]); ok {
		messages, err := unix.ParseSocketControlMessage(oob[:on])
		if err != nil {
			return 0, addr, err
		}
		found := false
		var bits uint8
		for _, m := range messages {
			if m.Header.Level == unix.IPPROTO_IP && (m.Header.Type == unix.IP_TOS || m.Header.Type == unix.IP_RECVTOS) && len(m.Data) > 0 {
				bits = m.Data[0] & 3
				found = true
			}
		}
		if !found {
			return 0, addr, fmt.Errorf("missing outer-IP ECN metadata for SRTP")
		}
		s.mu.Lock()
		s.samples[seq] = ecnSample{ssrc: ssrc, seq: seq, bits: bits, at: time.Now()}
		s.mu.Unlock()
	}
	return n, addr, nil
}

// take retrieves metadata only after the corresponding RTP packet authenticates.
func (s *ecnSocket) take(ssrc uint32, seq uint16) (ecnSample, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	sample := s.samples[seq]
	if sample.at.IsZero() || sample.ssrc != ssrc || sample.seq != seq {
		return sample, fmt.Errorf("missing ECN correlation for RTP %d/%d", ssrc, seq)
	}
	s.samples[seq] = ecnSample{}
	switch sample.bits {
	case 1:
		s.ect1.Add(1)
	case 3:
		s.ce.Add(1)
	default:
		s.nonect.Add(1)
	}
	return sample, nil
}

// WriteTo serializes TOS changes and marks only SRTP data; preserve DSCP bits.
func (s *ecnSocket) WriteTo(b []byte, addr net.Addr) (n int, err error) {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	_, _, media := rtpIdentity(b)
	media = media && s.mark
	defer func() {
		if !media {
			return
		}
		if err != nil {
			s.txMediaFailures.Add(1)
			return
		}
		at := time.Since(s.origin).Nanoseconds() + 1
		s.txFirstNS.CompareAndSwap(0, at)
		s.txLastNS.Store(at)
		s.txMediaPackets.Add(1)
		s.txMediaBytes.Add(uint64(n))
	}()
	bits := 0
	if _, _, ok := rtpIdentity(b); ok && s.mark {
		bits = 1
	}
	raw, err := s.conn.SyscallConn()
	if err != nil {
		return 0, err
	}
	var optionErr error
	err = raw.Control(func(fd uintptr) {
		tos, getErr := unix.GetsockoptInt(int(fd), unix.IPPROTO_IP, unix.IP_TOS)
		if getErr != nil {
			optionErr = getErr
			return
		}
		optionErr = unix.SetsockoptInt(int(fd), unix.IPPROTO_IP, unix.IP_TOS, (tos&^3)|bits)
	})
	if err != nil {
		return 0, err
	}
	if optionErr != nil {
		return 0, optionErr
	}
	return s.conn.WriteTo(b, addr)
}

// outboundStats reports media datagrams accepted by UDP, separately from NIC TX.
func (s *ecnSocket) outboundStats() map[string]any {
	return map[string]any{"accepted_packets": s.txMediaPackets.Load(), "accepted_datagram_bytes": s.txMediaBytes.Load(), "write_failures": s.txMediaFailures.Load(), "socket_created_at_utc": s.origin.UTC().Format(time.RFC3339Nano), "first_write_ns_since_socket": max(s.txFirstNS.Load()-1, 0), "last_write_ns_since_socket": max(s.txLastNS.Load()-1, 0), "note": "SRTP datagrams accepted by UDP WriteTo; excludes IP/Ethernet headers and control traffic; not NIC departure"}
}

// Close releases the socket and unblocks the ICE mux's reader.
func (s *ecnSocket) Close() error { return s.conn.Close() }

// LocalAddr advertises the one bound lab address and port to the ICE mux.
func (s *ecnSocket) LocalAddr() net.Addr { return s.conn.LocalAddr() }

// SetDeadline delegates the PacketConn deadline contract to the socket.
func (s *ecnSocket) SetDeadline(t time.Time) error { return s.conn.SetDeadline(t) }

// SetReadDeadline delegates cancellation of reads to the socket.
func (s *ecnSocket) SetReadDeadline(t time.Time) error { return s.conn.SetReadDeadline(t) }

// SetWriteDeadline delegates cancellation of writes to the socket.
func (s *ecnSocket) SetWriteDeadline(t time.Time) error { return s.conn.SetWriteDeadline(t) }
