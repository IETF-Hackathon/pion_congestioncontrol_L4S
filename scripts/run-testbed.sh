#!/usr/bin/env bash
# Repository-owned launcher for either endpoint. PION_GO_BIN optionally names a
# Go bin directory; the installed lab toolchain is a fallback when Go is absent.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
if [[ -n "${PION_GO_BIN:-}" ]]; then
  test -x "$PION_GO_BIN/go" || { echo "PION_GO_BIN must contain executable go" >&2; exit 1; }
  export PATH="$PION_GO_BIN:$PATH"
elif ! command -v go >/dev/null 2>&1 && [[ -x "$HOME/.local/share/pion-toolchains/go1.27.4/bin/go" ]]; then
  export PATH="$HOME/.local/share/pion-toolchains/go1.27.4/bin:$PATH"
fi
case "${1:-}" in
  build) shift; exec bash scripts/build.sh "$@" ;;
  check) shift; exec bash scripts/check.sh "$@" ;;
  *) exec python3 scripts/testbed.py "$@" ;;
esac
