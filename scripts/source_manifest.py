#!/usr/bin/env python3
"""Record compiled source hashes and reject stale binaries before captured runs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    """Hash one bounded local source, config or executable artifact."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compiled_sources(root=ROOT):
    """Identify Go/cgo inputs and module pins, excluding media and run artifacts."""
    root = Path(root)
    paths = [root / "go.mod", root / "go.sum"]
    for directory in ("cmd", "internal"):
        paths += [p for p in (root / directory).rglob("*")
                  if p.is_file() and p.suffix in (".go", ".cc", ".cpp", ".h")]
    return {str(p.relative_to(root)): digest(p) for p in sorted(paths)}


def verify_build(binary, root=ROOT):
    """Require a binary hash and compiled inputs matching its build manifest."""
    path = Path(str(binary) + ".build.json")
    if not path.is_file():
        raise ValueError("build manifest missing; run bash run-testbed.sh build")
    record = json.loads(path.read_text())
    if record["binary_sha256"] != digest(binary) or record["source_sha256"] != compiled_sources(root):
        raise ValueError("binary/source changed since build; run bash run-testbed.sh build")
    return record


def main():
    """Write or verify a local build manifest; perform no compilation or networking."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("write", "verify"))
    parser.add_argument("--binary", default="bin/pion-l4s")
    args = parser.parse_args()
    binary = ROOT / args.binary
    if args.action == "verify":
        result = verify_build(binary)
    else:
        result = dict(binary_sha256=digest(binary), source_sha256=compiled_sources(),
                      go_build_info=subprocess.check_output(["go", "version", "-m", str(binary)], text=True))
        Path(str(binary) + ".build.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(dict(binary=str(binary), binary_sha256=result["binary_sha256"])))


if __name__ == "__main__":
    main()
