#!/usr/bin/env bash
# Generate a named VP8 IVF source offline and record its measured bitrate/hash.
# Default: smoke (500 kbps). High load: bash scripts/make-media.sh --profile load25m
# Use --dry-run to print the command. Existing clips/manifests are preserved.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"

# Default smoke-profile FFmpeg input and output options:
#   -n                         Refuse to overwrite an existing sample. This keeps
#                              recorded media hashes and repeated runs comparable.
#   -f lavfi                   Use a libavfilter-generated input, not an input file.
#   -i testsrc2=...            Generate a moving synthetic test pattern.
#   size=640x360               Set the generated frame dimensions.
#   rate=30                    Generate 30 frames per second.
#   -t 10                      Stop after 10 seconds (about 300 frames).
#   -c:v libvpx                Encode the video as VP8 with libvpx.
#   -b:v 500k                  Request a 500,000 bit/s average video bitrate. This
#                              is a rate-control target, not strict constant rate.
#   -g 30                      Limit the keyframe interval to 30 frames, or about
#                              one second at 30 fps. Keyframes can be larger and
#                              create short bursts for the sender pacer to smooth.
#   -an                        Do not create an audio stream.
#   -f ivf                     Store the VP8 stream in an IVF container.
#   media/sample.ivf           Write the repeatable input used by the Pion sender.
#
# The resulting file is roughly 625 kB of encoded payload before container
# overhead (500 kb/s for 10 seconds), but its actual size can differ with rate
# control and frame complexity. During a call, this project reads and loops the
# already encoded IVF frames. GCC or Prague controls RTP packet pacing; it cannot
# change the quality or bitrate of this fixed source. A live rate-controllable
# encoder is still required for source adaptation experiments.
# load15m/load25m/load35m use the original 720p60 noisy two-second sweep recipe.
# The helper validates every frame and the actual payload bitrate. It records
# the exact command and FFmpeg version in <output>.manifest.json.
exec python3 "$task_root/scripts/media_source.py" generate "$@"
