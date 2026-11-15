#!/usr/bin/env bash
# Compatibility entrypoint; sender logic is kept in scripts/run-testbed.sh.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$task_root/scripts/run-testbed.sh" "$@"
