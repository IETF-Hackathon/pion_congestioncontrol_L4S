#!/usr/bin/env bash
# Build the lab endpoint from the committed Altanai fork pins. Ignore local Go
# workspaces so a developer's sibling checkout cannot silently change a lab run.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
export GOWORK=off
export CGO_ENABLED=1
export CGO_CXXFLAGS_ALLOW='-fwrapv'
go mod download
mkdir -p bin
go build -mod=readonly -o bin/pion-l4s ./cmd/pion-l4s
go version -m bin/pion-l4s
python3 scripts/source_manifest.py write --binary bin/pion-l4s
