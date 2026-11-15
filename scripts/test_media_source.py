"""Validate source-rate safeguards and the retained high-load FFmpeg recipe offline."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import media_source
import equal_frame_source
import testbed
from test_testbed import config_fixture, validated


def ivf(frame=b'\x00'+b'x'*999, frames=2):
    """Create a small keyframe-first container to exercise bounds, not decoding."""
    return struct.pack('<4sHH4sHHIIII',b'DKIF',0,32,b'VP80',640,360,30,1,frames,0)+b''.join(struct.pack('<IQ',len(frame),i)+frame for i in range(frames))


class MediaTests(unittest.TestCase):
    """Keep nominal file rate separate from controller permission and output rate."""
    def test_measure_and_reject_low_load(self):
        """Calculate actual payload rate and reject it for a high-load expectation."""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.ivf';path.write_bytes(ivf())
            source=media_source.inspect_ivf(path)
            self.assertEqual(source['nominal_payload_bps'],240000)
            with self.assertRaisesRegex(ValueError,'below required'):
                media_source.check_expected(source,20_000_000)
            path.write_bytes(ivf()+b'x')
            with self.assertRaisesRegex(ValueError,'truncated'): media_source.inspect_ivf(path)

    def test_equal_keyframes_and_source_guard(self):
        """Preserve identical decodable-frame bytes and reject size variation preflight."""
        frame = b'\x00\x00\x00\x9d\x01\x2a'+b'x'*994
        header = ivf()[:32]
        data = equal_frame_source.repeat_keyframe(header, frame, 120, 60)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'equal.ivf';path.write_bytes(data)
            source = media_source.inspect_ivf(path)
            self.assertTrue(source['constant_frame_size'])
            self.assertEqual(source['keyframe_count'],120)
            self.assertEqual(source['nominal_payload_bps'],480000)
            for i in range(120):
                at=32+i*(12+len(frame))
                self.assertEqual(struct.unpack_from('<IQ',data,at),(len(frame),i))
                self.assertEqual(data[at+12:at+12+len(frame)],frame)
            config=validated(config_fixture());config['video']=str(path)
            config['source_expectation']={'equal_frame_bytes':1000}
            self.assertTrue(testbed.inspect_source(config)['constant_frame_size'])
            config['source_expectation']['equal_frame_bytes']=999
            with self.assertRaisesRegex(ValueError,'equal encoded-frame'):
                testbed.inspect_source(config)
            with self.assertRaisesRegex(ValueError,'already exists'):
                equal_frame_source.generate(str(path))
        with self.assertRaisesRegex(ValueError,'VP8 keyframe'):
            equal_frame_source.repeat_keyframe(header,b'\x01'+frame[1:],120,60)

    def test_high_profile_matches_historical_recipe(self):
        """Retain the actual noisy 720p60 recipe instead of relabeling a 500k file."""
        cmd=media_source.ffmpeg_command('load25m',Path('/tmp/new.ivf'))
        self.assertEqual(cmd[cmd.index('-b:v')+1],'25M')
        self.assertEqual(cmd[cmd.index('-threads')+1],'4')
        self.assertIn('testsrc2=size=1280x720:rate=60,noise=alls=20:allf=t',cmd)
        self.assertIn('-n',cmd)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'existing.ivf';output.write_bytes(ivf())
            with self.assertRaisesRegex(ValueError,'already exists'):
                media_source.generate('load25m',str(output))
            self.assertEqual(output.read_bytes(),ivf())

    def test_prepare_preserves_assignments_and_adds_rate_guard(self):
        """Create a separate high-load config without overwriting the local setup."""
        original=validated(config_fixture())
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'new.json'
            config=testbed.prepare_config(original,'load25m','prague',output)
            self.assertEqual(config['receiver'],original['receiver'])
            self.assertEqual(config['video'],'media/load25m.ivf')
            self.assertEqual(config['initial_bitrate_bps'],25_000_000)
            self.assertEqual(config['source_expectation']['min_payload_bps'],21_250_000)
            with self.assertRaises(FileExistsError): testbed.prepare_config(original,'smoke','gcc',output)
            self.assertEqual(json.loads(output.read_text())['controller'],'prague')
            self.assertEqual(original['video'],'media/sample.ivf')


if __name__=='__main__': unittest.main()
