#!/usr/bin/env bash
set -euo pipefail
export PATH=/home/selabs/.local/share/pion-toolchains/go1.27.4/bin:"$PATH"
cd /home/selabs/pion-l4s-interop
exec python3 scripts/testbed.py "$@"
