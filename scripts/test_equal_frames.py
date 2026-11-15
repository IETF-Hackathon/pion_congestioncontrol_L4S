"""Check equal-frame capture interpretation with real PCAPNG/RTP wire layouts."""
from pathlib import Path
import struct
import tempfile
import unittest

import analyze_equal_frames as analysis


def block(kind, payload):
    """Encode one aligned PCAPNG block with matching prefix and suffix lengths."""
    payload += b'\0'*(-len(payload)%4)
    length=12+len(payload)
    return struct.pack('<II',kind,length)+payload+struct.pack('<I',length)


def capture_packet(sequence, timestamp, payload_type=96, marker=True):
    """Build a snaplen-truncated Ethernet/IPv4/UDP/RTP packet with a full wire size."""
    ethernet=b'\0'*12+b'\x08\x00'
    ip=bytearray(20);ip[0]=0x45;ip[1]=1;ip[9]=17
    ip[12:16]=bytes([192,168,1,216]);ip[16:20]=bytes([192,168,1,79])
    udp=struct.pack('!HHHH',50000,50000,1200,0)
    rtp=struct.pack('!BBHII',0x80,payload_type|(128 if marker else 0),sequence,timestamp,42)
    data=ethernet+ip+udp+rtp
    return block(6,struct.pack('<IIIII',0,0,1_000_000,len(data),1240)+data)


class CaptureAnalysisTests(unittest.TestCase):
    """Separate encrypted payload availability, wire bytes and RTP identity."""

    def test_truncated_payload_preserves_original_size_and_marker(self):
        """Retain RTP shape through snaplen truncation and ignore unnegotiated types."""
        blob=block(0x0a0d0d0a,struct.pack('<IHHq',0x1a2b3c4d,1,0,-1))
        blob+=block(1,struct.pack('<HHI',1,0,128))
        blob+=capture_packet(65535,1000)+capture_packet(0,2500)+capture_packet(1,4000,97)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'input.pcapng';path.write_bytes(blob)
            rows=analysis.packet_rows(path,'192.168.1.216','192.168.1.79',50000,50000,{96})
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['time'],1)
        self.assertEqual(rows[0]['frame_bytes'],1240)
        self.assertTrue(rows[0]['marker'])
        self.assertEqual(rows[0]['ecn'],1)
        self.assertNotEqual(analysis.identity(rows[0]),analysis.identity(rows[1]))

    def test_quantiles_match_linear_packet_percentiles(self):
        """Compute interpolated packet quantiles without a plotting dependency."""
        self.assertAlmostEqual(analysis.percentile([10,12,14,20],.99),19.82)
        self.assertEqual(analysis.percentile([10,12,14,20],.5),13)
        with self.assertRaisesRegex(ValueError,'no paired'):
            analysis.percentile([],.5)


if __name__=='__main__':
    unittest.main()
