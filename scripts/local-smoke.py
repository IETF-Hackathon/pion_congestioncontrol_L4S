#!/usr/bin/env python3
"""Exercise a bounded Pion media call on loopback and retain honest local evidence."""

import argparse
import datetime
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    """Identify the exact local binary and source clip used by this call."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    """Run two bounded local peers and verify media, feedback and ECN counters."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--controller", choices=("gcc", "prague"), default="prague")
    parser.add_argument("--feedback", choices=("ccfb", "twcc"), default="ccfb")
    args = parser.parse_args()
    if args.controller == "prague" and args.feedback != "ccfb":
        parser.error("Prague requires ccfb")
    video = (ROOT / args.video).resolve()
    binary = ROOT / "bin/pion-l4s"
    loopback = "lo0" if sys.platform == "darwin" else "lo"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("local-%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "results/raw" / run_id
    folder.mkdir(parents=True)
    common = [str(binary), "--controller", args.controller, "--feedback", args.feedback,
              "--interface", loopback, "--local-ip", "127.0.0.1", "--loopback"]
    receiver = None
    sender_command = common + ["--role", "sender", "--peer", f"http://127.0.0.1:{port}/offer",
                               "--video", str(video), "--duration", "6s"]
    with (folder / "receiver.jsonl").open("w") as receiver_log, (folder / "sender.jsonl").open("w") as sender_log:
        try:
            receiver = subprocess.Popen(common + ["--role", "receiver", "--listen", f"127.0.0.1:{port}",
                                                 "--duration", "12s"], stdout=receiver_log, stderr=subprocess.STDOUT, cwd=ROOT)
            deadline = time.monotonic() + 5
            ready = False
            while time.monotonic() < deadline and receiver.poll() is None:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
                try:
                    connection.request("GET", "/healthz")
                    response = connection.getresponse()
                    ready = response.status == 200
                    response.read()
                    if ready:
                        break
                except OSError:
                    time.sleep(0.05)
                finally:
                    connection.close()
            if not ready:
                raise RuntimeError(f"receiver not ready; inspect {folder}")
            sender = subprocess.run(sender_command, stdout=sender_log, stderr=subprocess.STDOUT,
                                    cwd=ROOT, timeout=15, check=False)
            if sender.returncode:
                raise RuntimeError(f"sender exited {sender.returncode}; inspect {folder}")
        finally:
            if receiver is not None and receiver.poll() is None:
                receiver.terminate()
                try:
                    receiver.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    receiver.kill()
                    receiver.wait()
    logs = {}
    for role in ("receiver", "sender"):
        logs[role] = []
        for line in (folder / f"{role}.jsonl").read_text().splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("level") == "ERROR":
                raise RuntimeError(f"{role} error: {entry}")
            logs[role].append(entry)
    receivers = [e for e in logs["receiver"] if e.get("msg") == "receiver metrics"]
    senders = [e for e in logs["sender"] if e.get("msg") == "sender metrics"]
    if not receivers or receivers[-1]["packets"] == 0 or not senders or senders[-1]["congestion_feedback_reports"] == 0:
        raise RuntimeError(f"missing media or feedback; inspect {folder}")
    if args.controller == "prague" and (receivers[-1]["ect1_count"] == 0 or receivers[-1]["nonect_count"] != 0):
        raise RuntimeError(f"missing ECT(1) or invalid ECN; inspect {folder}")
    summary = {"scope": "local loopback; no physical bottleneck", "controller": args.controller,
               "feedback": args.feedback, "binary_sha256": digest(binary), "video_sha256": digest(video),
               "receiver_last_sample": receivers[-1], "sender_last_sample": senders[-1]}
    summary["git_commit"] = None
    summary["git_status"] = "source snapshot without Git metadata"
    if (ROOT / ".git").exists():
        summary["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        summary["git_status"] = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    (folder / "build-info.txt").write_text(subprocess.check_output(["go", "version", "-m", str(binary)], cwd=ROOT, text=True))
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"artifacts": str(folder), **summary}, indent=2))


if __name__ == "__main__":
    main()
