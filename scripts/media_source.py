#!/usr/bin/env python3
"""Generate and measure fixed VP8 IVF sources before a Pion call.

FFmpeg runs only for generation. The Go sender later loops the encoded file;
its controller target does not change that file's bitrate. Every new generation
retains its exact argv, FFmpeg version, measured payload rate and SHA-256.
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

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "smoke": dict(target_bps=500_000, width=640, height=360, fps=30, seconds=10, video="media/sample.ivf"),
    "load15m": dict(target_bps=15_000_000, width=1280, height=720, fps=60, seconds=2, video="media/load15m.ivf"),
    "load25m": dict(target_bps=25_000_000, width=1280, height=720, fps=60, seconds=2, video="media/load25m.ivf"),
    "load35m": dict(target_bps=35_000_000, width=1280, height=720, fps=60, seconds=2, video="media/load35m.ivf"),
}


def inspect_ivf(path):
    """Validate all frames against the Go reader's limits and measure payload bps."""
    path = Path(path)
    if path.stat().st_size > 32 << 20:
        raise ValueError("IVF exceeds the sender's 32 MiB limit; generate a shorter clip")
    data = path.read_bytes()
    if len(data) < 32 or data[:12] != b"DKIF\x00\x00 \x00VP80":
        raise ValueError("expected version-0 VP8 IVF with a 32-byte header")
    width, height, rate, scale = struct.unpack_from("<HHII", data, 12)
    if not rate or not scale or not 1 <= rate / scale <= 120:
        raise ValueError("IVF frame rate must be 1..120 fps")
    position, sizes, keyframes = 32, [], 0
    while position < len(data):
        if len(data) - position < 12:
            raise ValueError("truncated IVF frame header")
        size = struct.unpack_from("<I", data, position)[0]
        position += 12
        if not 0 < size <= 1 << 20 or position + size > len(data):
            raise ValueError("invalid IVF frame length; sender limit is 1 MiB per frame")
        if not sizes and data[position] & 1:
            raise ValueError("IVF must start with a VP8 keyframe")
        keyframes += int(data[position] & 1 == 0)
        sizes.append(size)
        position += size
    if not sizes:
        raise ValueError("IVF contains no frames")
    duration = len(sizes) * scale / rate
    return dict(path=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                width=width, height=height, frames=len(sizes), fps=rate/scale,
                duration_seconds=duration, smallest_frame_bytes=min(sizes), largest_frame_bytes=max(sizes),
                unique_frame_sizes=len(set(sizes)), constant_frame_size=len(set(sizes)) == 1,
                keyframe_count=keyframes, interframe_count=len(sizes)-keyframes,
                nominal_payload_bps=sum(sizes)*8/duration)


def ffmpeg_command(profile, output, ffmpeg="ffmpeg"):
    """Return the historical smoke or noisy high-load recipe as an argv list."""
    p = PROFILES[profile]
    command = [ffmpeg, "-n", "-hide_banner", "-loglevel", "warning", "-f", "lavfi"]
    if profile == "smoke":
        return command + ["-i", "testsrc2=size=640x360:rate=30", "-t", "10", "-c:v", "libvpx",
                          "-b:v", "500k", "-g", "30", "-an", "-f", "ivf", str(output)]
    bitrate = str(p["target_bps"] // 1_000_000) + "M"
    return command + ["-i", "testsrc2=size=1280x720:rate=60,noise=alls=20:allf=t", "-t", "2",
                      "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "6", "-threads", "4",
                      "-b:v", bitrate, "-minrate", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
                      "-g", "60", "-lag-in-frames", "0", "-an", "-f", "ivf", str(output)]


def check_expected(source, minimum=None, maximum=None):
    """Reject an underloaded or mismatched file before a capture is reserved."""
    actual = source["nominal_payload_bps"]
    if minimum is not None and actual < minimum:
        raise ValueError(f"source is {actual/1e6:.3f} Mbps, below required {minimum/1e6:.3f} Mbps; generate/select the high-load clip")
    if maximum is not None and actual > maximum:
        raise ValueError(f"source is {actual/1e6:.3f} Mbps, above allowed {maximum/1e6:.3f} Mbps")


def generate(profile, output=None, ffmpeg="ffmpeg", dry_run=False):
    """Generate a new file without overwriting media and save success/failure provenance."""
    output = (ROOT / (output or PROFILES[profile]["video"])).resolve()
    command = ffmpeg_command(profile, output, ffmpeg)
    if dry_run:
        print(shlex.join(command))
        return
    manifest = Path(str(output) + ".manifest.json")
    if output.exists() or manifest.exists():
        raise ValueError(f"media or manifest already exists: {output}; inspect/reuse it or select a new --output")
    output.parent.mkdir(parents=True, exist_ok=True)
    record = dict(schema_version=1, status="generating", profile=profile,
                  created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  command=command, command_shell=shlex.join(command),
                  target_payload_bps=PROFILES[profile]["target_bps"],
                  note="Offline generation; Pion replays the result. Regeneration can differ with FFmpeg/libvpx versions and noise; matched calls must reuse identical bytes/hash.")
    with manifest.open("x") as stream:
        json.dump(record, stream, indent=2)
    try:
        record["ffmpeg_version"] = subprocess.check_output([ffmpeg, "-version"], text=True, timeout=10).splitlines()[0]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        record.update(exit_code=result.returncode, encoder_log_tail=(result.stdout+result.stderr)[-8000:])
        if result.returncode:
            raise ValueError("FFmpeg failed; inspect " + str(manifest))
        record["source"] = inspect_ivf(output)
        target = PROFILES[profile]["target_bps"]
        check_expected(record["source"], target*.85, target*1.15)
        record["status"] = "validated"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        record.update(status="failed", error=str(error))
        raise
    finally:
        manifest.write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps(dict(manifest=str(manifest), **record["source"]), indent=2))


def main():
    """Expose source inspection and named generation profiles without starting media calls."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    gen = sub.add_parser("generate", help="run FFmpeg once to create a new encoded clip")
    gen.add_argument("--profile", choices=PROFILES, default="smoke")
    gen.add_argument("--output", help="new filename; defaults to the profile's media/ path")
    gen.add_argument("--ffmpeg", default="ffmpeg")
    gen.add_argument("--dry-run", action="store_true", help="print the exact FFmpeg command without creating files")
    inspect = sub.add_parser("inspect", help="measure an existing source without FFmpeg")
    inspect.add_argument("video")
    inspect.add_argument("--min-bps", type=int)
    inspect.add_argument("--max-bps", type=int)
    args = parser.parse_args()
    try:
        if args.action == "generate":
            generate(args.profile, args.output, args.ffmpeg, args.dry_run)
        else:
            source = inspect_ivf(ROOT / args.video)
            check_expected(source, args.min_bps, args.max_bps)
            print(json.dumps(source, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"media-source: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
