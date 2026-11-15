#!/usr/bin/env bash
# Run the required checks with the pinned controller's signed-wrap compiler flag.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
export GOWORK=off CGO_ENABLED=1 CGO_CXXFLAGS_ALLOW='-fwrapv'
go test ./...
go vet ./...
go test -race ./...
python3 -m unittest discover -s scripts -p 'test_*.py'
bash -n scripts/build.sh scripts/make-media.sh scripts/check.sh scripts/run-testbed.sh run-testbed.sh
