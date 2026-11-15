#!/usr/bin/env python3
"""Plan or deploy a frozen source/media/config bundle to a separate Linux sender.

Deployments build in a new release directory and preserve the installed project.
SSH authentication stays with SSH; no password or askpass helper is stored here.
This tool does not start a media call or change lab network equipment.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tarfile

import server
import testbed

ROOT = Path(__file__).resolve().parents[1]


def connection_args(args, program="ssh"):
    """Share explicit SSH configuration and an optional attended control socket."""
    command = [program, "-F", args.ssh_config, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if args.control_path:
        command += ["-o", "ControlPath=" + args.control_path]
    return command


def validate_target(host, remote_root):
    """Keep SSH operands unambiguous and deployments below a named absolute path."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host):
        raise ValueError("--host must be an SSH user@host or host without shell options")
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", remote_root) or remote_root == "/"
            or ".." in PurePosixPath(remote_root).parts):
        raise ValueError("--remote-root must be a specific absolute project path without '..'")


def remote_command(args, command, timeout=30):
    """Run one bounded remote command over the user's existing SSH authentication."""
    return subprocess.run(connection_args(args) + [args.host, command], capture_output=True,
                          text=True, timeout=timeout, check=True).stdout


def deploy(args, config):
    """Transfer a recorded snapshot and selected IVF into a new, isolated release."""
    source = testbed.inspect_source(config)
    video = Path(config["video"])
    config_path = Path(args.config)
    for path in (video, config_path):
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("deployment config/video paths must be relative to this repository")
    tag = "sender-release-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "results/raw" / tag
    folder.mkdir(parents=True)
    archive = folder / "source.tar.gz"
    hashes = {}
    with tarfile.open(archive, "w:gz") as bundle:
        for path, relative in server.source_files(ROOT):
            if relative == config_path:
                continue
            bundle.add(path, arcname=str(relative), recursive=False)
            hashes[str(relative)] = testbed.sha256(path)
        inputs = [video, config_path]
        media_manifest = Path(str(video) + ".manifest.json")
        if (ROOT / media_manifest).is_file():
            inputs.append(media_manifest)
        for path in inputs:
            bundle.add(ROOT / path, arcname=str(path), recursive=False)
            hashes[str(path)] = testbed.sha256(ROOT / path)
    release = args.remote_root.rstrip("/") + "/results/raw/" + tag + "/source"
    record = dict(schema_version=1, tag=tag, host=args.host, remote_root=release, config=args.config,
                  source=source, files=hashes, archive_sha256=testbed.sha256(archive), status="prepared")
    metadata = folder / "manifest.json"
    metadata.write_text(json.dumps(record, indent=2)+"\n")
    remote_archive = "/tmp/" + tag + ".tar.gz"
    try:
        remote_command(args, "test ! -e " + shlex.quote(release))
        subprocess.run(connection_args(args, "scp") + [str(archive), args.host + ":" + remote_archive],
                       check=True, timeout=120)
        prefix = "mkdir -p " + shlex.quote(release) + " && tar -xzf " + shlex.quote(remote_archive) + " -C " + shlex.quote(release)
        if args.go_bin:
            prefix += " && export PION_GO_BIN=" + shlex.quote(args.go_bin)
        command = prefix + " && cd " + shlex.quote(release) + " && bash run-testbed.sh build"
        output = remote_command(args, command, timeout=600)
        (folder / "build.log").write_text(output)
        binary_hash = remote_command(args, "sha256sum " + shlex.quote(release + "/bin/pion-l4s")).split()[0]
        record.update(status="built", binary_sha256=binary_hash)
    except (OSError, subprocess.SubprocessError) as error:
        record.update(status="failed", error=str(error))
        if isinstance(error, subprocess.CalledProcessError):
            (folder / "failure.log").write_text((error.stdout or "") + (error.stderr or ""))
        raise
    finally:
        metadata.write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps(dict(remote_root=release, manifest=str(metadata), binary_sha256=binary_hash), indent=2))


def main():
    """Print a deployment plan or explicitly transfer/build one source snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "deploy"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--ssh-config", default="/dev/null")
    parser.add_argument("--control-path")
    parser.add_argument("--go-bin", help="optional Go bin directory on the Linux sender")
    args = parser.parse_args()
    try:
        validate_target(args.host, args.remote_root)
        config = testbed.load_config(ROOT / args.config)
        if args.action == "plan":
            print(json.dumps(dict(host=args.host, installed_root=args.remote_root,
                deployment="New results/raw/sender-release-<UTC>/source directory; installed files preserved",
                config=args.config, video=config["video"], source=testbed.inspect_source(config),
                toolchain_bin=args.go_bin, starts_media=False), indent=2))
        else:
            deploy(args, config)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"sender: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
