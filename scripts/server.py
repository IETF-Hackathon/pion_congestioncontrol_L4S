#!/usr/bin/env python3
"""Create and verify an isolated Lima Linux sender server on an Apple Silicon Mac.

Source is copied as a recorded snapshot, including uncommitted implementation
files. The VM does not mount the host filesystem or forward the SSH agent. Its
virtual network is for local development, not a physical lld-black4 experiment.
"""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "pion-sender"
ARTIFACTS = ROOT / "results/raw/server-pion-sender"
GUEST_ROOT = "/home/pion-project"
EXCLUDED = {".git", ".cache", "bin", "__pycache__", "local.json"}


def source_files(root):
    """Select regular project source files without secrets, media or run logs."""
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        if relative.parts[0] in ("results", "media"):
            continue
        if path.name.startswith(".env") or path.suffix in (".pcap", ".pcapng", ".pyc"):
            continue
        if path.is_file() and not path.is_symlink():
            yield path, relative


def snapshot(root, destination):
    """Archive current source and return per-file hashes, preserving dirty edits."""
    hashes = {}
    with tarfile.open(destination, "w:gz") as archive:
        for path, relative in source_files(root):
            archive.add(path, arcname=str(relative), recursive=False)
            hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def bundle(destination):
    """Package the current work for a separate laptop without publishing a branch."""
    path = (ROOT / destination).resolve()
    if path.exists() or Path(str(path) + ".manifest.json").exists():
        raise ValueError("bundle already exists; use a new output filename")
    path.parent.mkdir(parents=True, exist_ok=True)
    hashes = snapshot(ROOT, path)
    manifest = {"created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "git_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
                "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source_sha256": hashes}
    Path(str(path) + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Source archive: " + str(path))
    print("Includes current uncommitted source; excludes credentials, binaries and raw results.")


def instance():
    """Read only the dedicated server's Lima state, leaving other VMs alone."""
    output = subprocess.check_output(["limactl", "list", "--json"], text=True, timeout=15)
    for line in output.splitlines():
        entry = json.loads(line)
        if entry["name"] == NAME:
            return entry
    return None


def logged(command, filename, timeout):
    """Run a bounded operation, keeping the complete output for diagnosis."""
    path = ARTIFACTS / filename
    print(f"Running {command[0]} {command[1:3]}; log: {path}", flush=True)
    with path.open("a") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"command exited {result.returncode}; inspect {path}")


def guest(command, filename, timeout=600):
    """Execute one fixed guest command from a directory available without mounts."""
    logged(["limactl", "shell", "--workdir=/tmp", NAME, "--", "bash", "-lc", command], filename, timeout)


def create():
    """Create the dedicated Linux guest, transfer the source, and build Prague."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("this VM template requires an Apple Silicon Mac; use the native build on Linux")
    if not shutil.which("limactl"):
        raise ValueError("limactl is required")
    existing = instance()
    metadata_path = ARTIFACTS / "server.json"
    if existing is not None and not metadata_path.exists():
        raise ValueError("a pion-sender VM already exists without this project's record; choose it manually")
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if metadata["status"] == "ready":
            print("Server already recorded; use status or verify. Existing guest files were preserved.")
            return
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    archive = ARTIFACTS / "source.tar.gz"
    if not metadata_path.exists():
        metadata = {"scope": "local Linux development server; no physical testbed path",
                    "instance": NAME, "status": "creating", "guest_project": GUEST_ROOT,
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    "source_sha256": snapshot(ROOT, archive)}
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    logged(["limactl", "start", "--tty=false", f"--name={NAME}", "--timeout=12m",
            str(ROOT / "testbed/pion-sender.lima.yaml")], "provision.log", 780)
    logged(["limactl", "copy", str(archive), f"{NAME}:/tmp/pion-source.tar.gz"], "copy.log", 60)
    guest("sudo install -d -o \"$(id -u)\" -g \"$(id -g)\" /home/pion-project && "
          "if test ! -e /home/pion-project/go.mod; then tar -xzf /tmp/pion-source.tar.gz -C /home/pion-project; fi && "
          "cd /home/pion-project && bash scripts/build.sh && "
          "if test ! -f media/sample.ivf; then bash scripts/make-media.sh; fi",
          "build.log", 900)
    metadata.update(status="ready", state=instance())
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print("Linux sender server is ready. Connect using: limactl shell pion-sender")
    print("Project directory: " + GUEST_ROOT)


def verify():
    """Run Linux checks and a bounded media call entirely inside the owned VM."""
    state = instance()
    if state is None or state["status"] != "Running":
        raise ValueError("start the pion-sender VM before verification")
    if not (ARTIFACTS / "server.json").exists():
        raise ValueError("no project record for this server")
    guest("cd /home/pion-project && bash scripts/check.sh && "
          "python3 scripts/local-smoke.py --controller prague --video media/sample.ivf",
          "verification.log", 900)
    logged(["limactl", "copy", "--recursive", f"{NAME}:/home/pion-project/results/raw",
            str(ARTIFACTS / "linux-runs")], "copy.log", 60)
    path = ARTIFACTS / "server.json"
    metadata = json.loads(path.read_text())
    metadata["linux_verification"] = {"status": "passed", "scope": "guest loopback and automated checks",
                                      "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    path.write_text(json.dumps(metadata, indent=2) + "\n")
    print("Linux checks and Prague loopback passed. Artifacts: " + str(ARTIFACTS))


def main():
    """Dispatch explicit server creation, status inspection or Linux verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "status", "verify", "bundle"))
    parser.add_argument("--output", default="results/raw/sender-source.tar.gz", help="new bundle filename relative to the project")
    args = parser.parse_args()
    try:
        if args.action == "bundle":
            bundle(args.output)
        elif args.action == "create":
            create()
        elif args.action == "verify":
            verify()
        else:
            print(json.dumps(instance(), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"server: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
