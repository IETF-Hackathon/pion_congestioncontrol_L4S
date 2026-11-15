#!/usr/bin/env python3
"""Check and run two Pion peers on the IETF lab without configuring shared gear.

Run this on each endpoint with the same edited JSON config. The receiver is the
laptop beyond lld-black4; the sender is a separate host at the servers switch.
Only the Go endpoint sends media. This helper checks local addressing/routes,
checks sender-to-receiver HTTP readiness, and saves provenance and JSONL logs.
It cannot prove passage through a transparent bottleneck or implement L4S.
"""

import argparse
import datetime
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import socket
import subprocess
import sys

ROOT = Path(os.environ["PION_EXPERIMENT_ROOT"]).resolve()
FORKS = {
    "github.com/pion/interceptor": "github.com/altanai/interceptor",
    "github.com/pion/ice/v4": "github.com/altanai/ice/v4",
}


def integer(value, name, minimum, maximum):
    """Require an integer in a bounded range, excluding JSON booleans."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be {minimum}..{maximum}")
    return value


def host_ipv4(value):
    """Reject placeholders and addresses unsuitable for a lab endpoint."""
    address = ipaddress.IPv4Address(value)
    if (address.is_unspecified or address.is_multicast or address.is_loopback
            or address.is_link_local or str(address) == "255.255.255.255"):
        raise ValueError(f"{value} is not an assigned lab IPv4 unicast address")
    return str(address)


def load_config(path):
    """Validate the shared lab config before any process or network operation."""
    with Path(path).open(encoding="utf-8") as stream:
        config = json.load(stream)
    if config.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if config.get("controller") not in ("gcc", "prague"):
        raise ValueError("controller must be gcc or prague")
    backend = config.get("prague_pacing_backend", "auto")
    if backend not in ("auto", "portable", "linux-timerfd"):
        raise ValueError("prague_pacing_backend must be auto, portable or linux-timerfd")
    if config["controller"] != "prague" and backend != "auto":
        raise ValueError("explicit Prague pacing backend requires controller=prague")
    if config["controller"] == "prague" and config.get("feedback") != "ccfb":
        raise ValueError("Prague requires feedback=ccfb for ECN feedback")
    if config.get("feedback") not in ("ccfb", "twcc"):
        raise ValueError("feedback must be ccfb or twcc")
    integer(config.get("duration_seconds"), "duration_seconds", 1, 86400)
    integer(config.get("initial_bitrate_bps"), "initial_bitrate_bps", 100000, 50000000)
    if not isinstance(config.get("video"), str) or not config["video"]:
        raise ValueError("video must name a VP8 IVF file on the sender")
    bottleneck = config["bottleneck"]
    if not isinstance(bottleneck.get("name"), str) or not bottleneck["name"]:
        raise ValueError("bottleneck.name is required")
    management = host_ipv4(bottleneck["management_ipv4"])
    reserved = {management, "192.168.1.1", "192.168.1.22", "192.168.1.51", "192.168.1.71", "192.168.1.75", "192.168.1.212"}
    for role in ("receiver", "sender"):
        endpoint = config[role]
        interface = endpoint.get("interface", "")
        if not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", interface) or "REPLACE" in interface:
            raise ValueError(f"{role}.interface must be the actual Ethernet device name")
        endpoint["ipv4"] = host_ipv4(endpoint["ipv4"])
        if endpoint["ipv4"] in reserved:
            raise ValueError(f"{role}.ipv4 is an existing device in the supplied diagram; obtain a new host address")
        low = integer(endpoint.get("udp_port_min"), f"{role}.udp_port_min", 1024, 65535)
        high = integer(endpoint.get("udp_port_max"), f"{role}.udp_port_max", low, 65535)
        if high - low < 1:
            raise ValueError(f"{role} needs at least two ICE UDP ports")
    integer(config["receiver"].get("signaling_port"), "receiver.signaling_port", 1024, 65535)
    if config["receiver"]["ipv4"] == config["sender"]["ipv4"]:
        raise ValueError("sender and receiver must be separate hosts with distinct IP addresses")
    return config


def capture(command):
    """Run a bounded local inspection command and return its stdout or an error."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
    if result.returncode:
        raise ValueError(f"{shlex.join(command)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def inspect_interface(interface):
    """Read assigned IPv4 addresses and link state on macOS or Linux."""
    if interface not in dict(socket.if_nameindex()).values():
        raise ValueError(f"interface {interface} does not exist; check the Ethernet adapter")
    if platform.system() == "Darwin":
        raw = capture(["ifconfig", interface])
        addresses = re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)\b", raw)
        active = "UP" in raw.splitlines()[0] and "status: inactive" not in raw
        ports = capture(["networksetup", "-listallhardwareports"])
        for block in ports.split("\n\n"):
            if f"Device: {interface}\n" in block + "\n" and re.search(r"Hardware Port: (Wi-Fi|AirPort)", block):
                raise ValueError(f"{interface} is Wi-Fi; select the lab Ethernet adapter")
    elif platform.system() == "Linux":
        raw = capture(["ip", "-json", "address", "show", "dev", interface])
        device = json.loads(raw)[0]
        addresses = [item["local"] for item in device["addr_info"] if item["family"] == "inet"]
        active = "UP" in device["flags"] and device.get("operstate") != "DOWN"
        if (Path("/sys/class/net") / interface / "wireless").exists():
            raise ValueError(f"{interface} is Wi-Fi; select the lab Ethernet adapter")
    else:
        raise ValueError("testbed checks support macOS and Linux")
    if not active:
        raise ValueError(f"{interface} is inactive; check the cable, adapter and assigned switch port")
    if interface.startswith(("lo", "utun", "tun", "tap", "wg", "awdl")):
        raise ValueError(f"{interface} is not a lab Ethernet interface")
    return {"interface": interface, "ipv4_addresses": addresses, "raw": raw}


def inspect_route(peer_ip, local_ip):
    """Read the OS route to the opposite endpoint without changing routing."""
    if platform.system() == "Darwin":
        raw = capture(["route", "-n", "get", peer_ip])
        match = re.search(r"\binterface:\s*(\S+)", raw)
        interface = match.group(1) if match else ""
    else:
        raw = capture(["ip", "-json", "route", "get", peer_ip, "from", local_ip])
        interface = json.loads(raw)[0].get("dev", "")
    return {"interface": interface, "raw": raw}


def http_json(address, port, local_ip):
    """Fetch receiver readiness over a TCP socket bound to the lab IPv4 address."""
    connection = http.client.HTTPConnection(address, port, timeout=3, source_address=(local_ip, 0))
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"receiver /healthz returned HTTP {response.status}")
        return json.loads(response.read(65536))
    finally:
        connection.close()


def check_receiver_health(health, config):
    """Reject an occupied receiver or mismatched feedback before sending an offer."""
    if not isinstance(health, dict) or health.get("service") != "pion-l4s" or health.get("role") != "receiver":
        raise ValueError("HTTP service is not the Pion lab receiver")
    if health.get("local_ip") != config["receiver"]["ipv4"]:
        raise ValueError("receiver is not bound to the configured lab IP")
    if health.get("feedback") != config["feedback"]:
        raise ValueError("receiver feedback differs from config; use the same config at both ends")
    if health.get("session_claimed") is not False:
        raise ValueError("receiver session already used; restart the receiver for this run")
    if config["controller"] == "prague" and (health.get("controller") != "prague" or health.get("ecn_observation") is not True):
        raise ValueError("Prague requires a Prague receiver with ECN observation enabled")


def doctor(config, role):
    """Fail on a wrong local link, address, route, or sender signaling connection."""
    local = config[role]
    remote = config["sender" if role == "receiver" else "receiver"]
    interface = inspect_interface(local["interface"])
    if local["ipv4"] not in interface["ipv4_addresses"]:
        raise ValueError(f"{local['ipv4']} is not assigned to {local['interface']}; use DHCP or the operator's assignment")
    route = inspect_route(remote["ipv4"], local["ipv4"])
    if route["interface"] != local["interface"]:
        raise ValueError(f"route to {remote['ipv4']} uses {route['interface']}, expected {local['interface']}; check the lab attachment/VPN/routes")
    # Binding checks ownership of the source address without sending traffic.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((local["ipv4"], 0))
    report = {"role": role, "local": local, "peer_ipv4": remote["ipv4"], "interface": interface, "route": route}
    if role == "sender":
        health = http_json(remote["ipv4"], remote["signaling_port"], local["ipv4"])
        check_receiver_health(health, config)
        report["receiver_health"] = health
    report["path_validation"] = "Endpoint checks only; confirm the transparent bottleneck using operator counters/captures."
    return report


def endpoint_command(config, role, binary):
    """Build an argv list with explicit lab IP, interface, feedback and UDP range."""
    local = config[role]
    command = [str(binary), "--role", role, "--interface", local["interface"],
               "--local-ip", local["ipv4"], "--udp-port-min", str(local["udp_port_min"]),
               "--udp-port-max", str(local["udp_port_max"]), "--feedback", config["feedback"],
               "--controller", config["controller"]]
    receiver = config["receiver"]
    if role == "receiver":
        command += ["--listen", f"{receiver['ipv4']}:{receiver['signaling_port']}"]
    else:
        command += ["--peer", f"http://{receiver['ipv4']}:{receiver['signaling_port']}/offer",
                    "--video", str(ROOT / config["video"]),
                    "--duration", f"{config['duration_seconds']}s",
                    "--initial-bitrate", str(config["initial_bitrate_bps"])]
        if config["controller"] == "prague" and "prague_pacing_backend" in config:
            command += ["--prague-pacing-backend", config["prague_pacing_backend"]]
    return command


def verify_pacing_capability(binary, config, role):
    """Reject an explicit scheduler option unsupported by an older sender binary."""
    if role != "sender" or config["controller"] != "prague" or "prague_pacing_backend" not in config:
        return
    probe = subprocess.run([str(binary), "--help"], capture_output=True, text=True,
                           timeout=8, check=False)
    if probe.returncode or "-prague-pacing-backend" not in probe.stdout + probe.stderr:
        raise ValueError("sender binary lacks Prague pacing selection; rebuild before starting capture")


def verify_forks(build_info):
    """Require the compiled binary to contain both Altanai module replacements."""
    replacements = {}
    previous = None
    for line in build_info.splitlines():
        fields = line.split()
        if fields and fields[0] == "dep" and len(fields) >= 3:
            previous = fields[1]
        elif fields and fields[0] == "=>" and len(fields) >= 3:
            replacements[previous] = fields[1:3]
            previous = None
        else:
            previous = None
    for upstream, fork in FORKS.items():
        selected = replacements.get(upstream, [])
        if len(selected) != 2 or selected[0] != fork or not selected[1].startswith("v"):
            raise ValueError(f"binary does not use a pinned {fork}; rebuild using bash scripts/build.sh")
    return {upstream: {"path": item[0], "version": item[1]} for upstream, item in replacements.items() if upstream in FORKS}


def sha256(path):
    """Hash an artifact incrementally so runs can identify the exact source/binary."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now():
    """Return an ISO UTC timestamp for run provenance."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def stop_requested(signum, frame):
    """Convert SIGTERM to the same bounded child cleanup used for Ctrl+C."""
    raise KeyboardInterrupt


def run_peer(config, args):
    """Run one peer, tee JSONL logs and always save its outcome and provenance."""
    binary = (ROOT / args.binary).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("endpoint binary missing; first run bash scripts/build.sh")
    build_info = capture(["go", "version", "-m", str(binary)])
    dependencies = verify_forks(build_info)
    verify_pacing_capability(binary, config, args.role)
    checks = doctor(config, args.role)
    command = endpoint_command(config, args.role, binary)
    clip_hash = sha256(ROOT / config["video"]) if args.role == "sender" else None
    run_id = args.run_id or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
        raise ValueError("run-id must be 1..80 letters, digits, dots, underscores or hyphens")
    folder = ROOT / "results" / "raw" / run_id / args.role
    folder.mkdir(parents=True, exist_ok=False)
    command += ["--record-dir", str(folder / "recording")]
    git = {}
    if (ROOT / ".git").exists():
        git = {"commit": capture(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
               "status": capture(["git", "-C", str(ROOT), "status", "--porcelain"])}
    metadata = {"started_at": utc_now(), "role": args.role, "config": config,
                "command": command, "host": platform.platform(), "checks": checks,
                "git": git, "binary_sha256": sha256(binary), "video_sha256": clip_hash,
                "dependencies": dependencies, "status": "starting",
                "recording": {"directory": "recording", "trace": "recording/session-trace.jsonl",
                              "sdp": "Exact *.raw.sdp and credential-redacted *.sdp; private local artifacts"},
                "capabilities": {"prague": config["controller"] == "prague", "ecn": config["controller"] == "prague"},
                "pacing_backend_requested": config.get("prague_pacing_backend", "auto") if config["controller"] == "prague" and args.role == "sender" else None,
                "measurement_limits": {"adaptive_encoder": False,
                                       "queue_delay_ms": None, "encoder_target_bps": None}}
    metadata_path = folder / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (folder / "build-info.txt").write_text(build_info + "\n", encoding="utf-8")
    print(f"Logs: {folder}\nCommand: {shlex.join(command)}", flush=True)
    old_handler = signal.signal(signal.SIGTERM, stop_requested)
    process = None
    interrupted = False
    exit_code = 1
    try:
        with (folder / "endpoint.jsonl").open("w", encoding="utf-8") as logfile:
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                for line in process.stdout:
                    logfile.write(line)
                    logfile.flush()
                    print(line, end="", flush=True)
                exit_code = process.wait()
            except KeyboardInterrupt:
                interrupted = True
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                exit_code = process.returncode
                process.stdout.close()
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        metadata.update({"finished_at": utc_now(), "exit_code": exit_code,
                         "status": "interrupted" if interrupted else ("exited" if exit_code == 0 else "failed")})
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    if not interrupted and exit_code:
        raise ValueError(f"endpoint exited {exit_code}; inspect {folder / 'endpoint.jsonl'}")
    return 130 if interrupted else 0


def main():
    """Dispatch offline planning, local network checks, or a recorded endpoint run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "doctor", "run"))
    parser.add_argument("--config", default="testbed/local.json", help="shared lab JSON, relative to the repository")
    parser.add_argument("--role", choices=("receiver", "sender"))
    parser.add_argument("--binary", default="bin/pion-l4s")
    parser.add_argument("--run-id", help="same identifier on both hosts; existing logs are never overwritten")
    args = parser.parse_args()
    if args.action != "plan" and args.role is None:
        parser.error("--role is required for doctor/run")
    try:
        config = load_config(ROOT / args.config)
        if args.action == "plan":
            print("Planned downlink: sender -> servers switch -> " + config["bottleneck"]["name"] + " -> laptop receiver")
            for role in ("receiver", "sender"):
                print(f"{role}: {shlex.join(endpoint_command(config, role, ROOT / args.binary))}")
            print("Diagram values are unverified. Confirm the path and active qdisc with the operator.")
            return 0
        if args.action == "doctor":
            verify_pacing_capability((ROOT / args.binary).resolve(), config, args.role)
            print(json.dumps(doctor(config, args.role), indent=2))
            return 0
        return run_peer(config, args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"testbed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
