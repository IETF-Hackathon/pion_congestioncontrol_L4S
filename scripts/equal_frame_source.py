#!/usr/bin/env python3
"""Create a decodable constant-size VP8 transport workload by repeating one keyframe.

FFmpeg encodes one seeded test image offline. A bounded quality search selects a
frame near the requested payload rate at the fixed frame cadence; IVF assembly
then repeats that exact keyframe. This removes key/interframe size variation, not
Prague's ACK/RTT/probing/loss response or synchronous source backpressure. It is
an artificial all-keyframe workload, not a representative moving-video encoder.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tempfile

from media_source import ROOT, check_expected, inspect_ivf


def encode_command(ffmpeg, quality, output):
    """Encode exactly one independently decodable seeded 720p VP8 keyframe."""
    return [ffmpeg, '-n', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
            '-i', 'testsrc2=size=1280x720:rate=60,noise=alls=20:allf=t:all_seed=42',
            '-frames:v', '1', '-c:v', 'libvpx', '-deadline', 'realtime',
            '-cpu-used', '6', '-threads', '1', '-b:v', '0', '-crf', str(quality),
            '-qmin', str(quality), '-qmax', str(quality), '-g', '1',
            '-lag-in-frames', '0', '-an', '-f', 'ivf', str(output)]


def repeat_keyframe(header, payload, count, fps):
    """Assemble valid IVF timestamps around byte-identical, unmodified keyframes."""
    if len(header) != 32 or not payload or payload[0] & 1 or payload[3:6] != b'\x9d\x01\x2a':
        raise ValueError('expected a complete VP8 keyframe with its sync code')
    if not 1 <= fps <= 120 or count < 1 or len(payload) > 1 << 20:
        raise ValueError('invalid equal-frame cadence, count or frame size')
    if 32 + count * (12 + len(payload)) > 32 << 20:
        raise ValueError('repeated clip would exceed the sender file-size bound')
    header = bytearray(header)
    struct.pack_into('<IIII', header, 16, fps, 1, count, 0)
    return bytes(header) + b''.join(struct.pack('<IQ', len(payload), i)+payload for i in range(count))


def generate(output, target_bps=25_000_000, fps=60, seconds=2, ffmpeg='ffmpeg'):
    """Preserve a new clip, all search commands, frame identity and decode validation."""
    output = (ROOT / output).resolve()
    manifest = Path(str(output)+'.manifest.json')
    if output.exists() or manifest.exists():
        raise ValueError('media or manifest already exists; choose a new output')
    if not 100_000 <= target_bps <= 50_000_000 or not 1 <= fps <= 120 or not 1 <= seconds <= 10:
        raise ValueError('target, fps or clip duration outside supported bounds')
    output.parent.mkdir(parents=True, exist_ok=True)
    record = dict(schema_version=1, profile='equal-frames', status='generating',
                  created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  target_payload_bps=target_bps, attempts=[], frame_cadence_fps=fps,
                  method='Repeat one byte-identical VP8 keyframe; no padding or live encoding',
                  controller='Normal Prague; ACK/RTT, probing and loss response retained')
    with manifest.open('x') as stream:
        json.dump(record, stream, indent=2)
    try:
        record['ffmpeg_version'] = subprocess.check_output([ffmpeg, '-version'], text=True, timeout=10).splitlines()[0]
        target_bytes = target_bps/(8*fps)
        low, high, candidates = 0, 63, []
        with tempfile.TemporaryDirectory(prefix='pion-equal-frame-') as folder:
            for attempt in range(7):
                quality = (low+high)//2
                path = Path(folder)/f'q{quality}.ivf'
                command = encode_command(ffmpeg, quality, path)
                subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
                source = inspect_ivf(path)
                if source['frames'] != 1 or source['keyframe_count'] != 1:
                    raise ValueError('encoder did not produce exactly one keyframe')
                data = path.read_bytes(); payload = data[44:]
                size = source['largest_frame_bytes']
                record['attempts'].append(dict(quality=quality, command=command,
                    command_shell=shlex.join(command), frame_bytes=size, offered_payload_bps=size*8*fps))
                candidates.append((abs(size-target_bytes), data[:32], payload, command, quality))
                if abs(size-target_bytes)/target_bytes <= .01:
                    break
                if size > target_bytes:
                    low = quality+1
                else:
                    high = quality-1
                if low > high:
                    break
            _, header, payload, command, quality = min(candidates, key=lambda item:item[0])
            with output.open('xb') as stream:
                stream.write(repeat_keyframe(header, payload, fps*seconds, fps))
        source = inspect_ivf(output)
        check_expected(source, target_bps*.95, target_bps*1.05)
        decode = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(output), '-f', 'null', '-']
        subprocess.run(decode, capture_output=True, text=True, timeout=60, check=True)
        record.update(status='validated', source=source, command=command, selected_quality=quality,
                      repeated_frame_sha256=hashlib.sha256(payload).hexdigest(), decode_command=decode,
                      decoder_validation='All repeated frames decoded successfully')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        record.update(status='failed', error=str(error))
        if isinstance(error, subprocess.CalledProcessError):
            record['error_log'] = (error.stderr or '')[-4000:]
        raise
    finally:
        manifest.write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(dict(manifest=str(manifest), **source), indent=2))


def main():
    """Generate one new equal-frame file without starting endpoints or captures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='media/equal25m.ivf')
    parser.add_argument('--target-bps', type=int, default=25_000_000)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--seconds', type=int, default=2)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    try:
        generate(args.output, args.target_bps, args.fps, args.seconds, args.ffmpeg)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('equal-frame-source: '+str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
