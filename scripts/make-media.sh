#!/usr/bin/env bash
# Generate a small, repeatable VP8 IVF input for both controller runs. Preserve
# existing media: FFmpeg's -n refuses to overwrite a clip with a recorded hash.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
mkdir -p media
ffmpeg -n -f lavfi -i testsrc2=size=640x360:rate=30 -t 10 \
  -c:v libvpx -b:v 500k -g 30 -an -f ivf media/sample.ivf
