#!/usr/bin/env python3
"""Coordinate one authorized capture and bounded remote sender; preserve evidence."""
import datetime, hashlib, http.client, json, re, subprocess, time
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, unquote

ROOT = Path("/Users/albisht/ietf/pion-l4s-interop")
RUN = 'pion-compare-25m-20261115T172333Z-gcc'
BASE = ROOT / "results/raw" / RUN
MON = BASE / "monitor"
HOST = "lld-black4.local"
ORIGIN = "http://" + HOST
CONTROL = "/private/tmp/pion-compare-25m-20261115T172333Z.ctl"
assert re.fullmatch(r"[A-Za-z0-9_.-]+", RUN)
manifest = {"run_id": RUN, "capture_seconds": 60, "sender_seconds": 30,
            "capture_start_attempts": 0, "events": [], "capture_partial": False}

def utc():
    """Return a timestamp independent of each endpoint's display timezone."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def save():
    """Persist progress so interruption does not erase capture provenance."""
    (MON / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

def event(name, **fields):
    """Record compact progress without dumping captured traffic."""
    item = dict(time=utc(), event=name, **fields)
    manifest["events"].append(item)
    save()
    print(json.dumps(item), flush=True)

def get(path, filename=None):
    """Issue one bounded direct GET; never retry a capture-start mutation."""
    conn = http.client.HTTPConnection(HOST, 80, timeout=15)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise RuntimeError("Monitor page exceeds evidence size bound")
        if filename:
            (MON / filename).write_bytes(body)
            (MON / (filename + ".headers.json")).write_text(json.dumps(dict(response.getheaders()), indent=2))
        if response.status != 200:
            raise RuntimeError("Monitor HTTP " + str(response.status) + ": " + path)
        return body.decode("utf-8", errors="replace")
    finally:
        conn.close()

class Page(HTMLParser):
    """Extract visible body text and links while ignoring script/style content."""
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.text = []
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.skip += 1
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))
    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())

def parse(html):
    """Return text and links for safe, narrow monitor interpretation."""
    page = Page()
    page.feed(html)
    return page

def status(filename):
    """Require affirmative capture status, rather than merely a new folder."""
    html = get("/ui/status", filename)
    text = " ".join(parse(html).text)
    idle = "currently idle" in text.lower()
    busy = bool(re.search(r'class=["\'][^"\']*state[^"\']*busy', html))
    active_words = bool(re.search(r"dumpcap|captur(?:ing|e in progress|e running)|currently capturing", text, re.I))
    active = busy and active_words and not idle
    if "/data/" in text and RUN not in text:
        active = False
    return text, idle, active

def locate(html):
    """Find only this new capture's same-origin result directory."""
    for link in parse(html).links:
        decoded = unquote(link)
        target = urlsplit(urljoin(ORIGIN + "/data/", link))
        if (RUN in decoded and decoded.endswith("/") and target.hostname == HOST
                and target.path.startswith("/data/") and ".." not in decoded):
            return target.path
    return None

def run():
    """Preflight, start one capture, await readiness, run sender, await analysis."""
    if (MON / "start.json").exists() or (MON / "manifest.json").exists():
        raise RuntimeError("Capture attempt evidence already exists; refusing duplicate start")
    initial, idle, _ = status("baseline-status.html")
    if not idle:
        raise RuntimeError("Monitor is not idle: " + initial)
    listing = get("/data/?C=M;O=D", "baseline-index.html")
    if locate(listing):
        raise RuntimeError("Run name already exists on monitor")
    save()
    manifest["capture_start_attempts"] = 1
    manifest["capture_request_at"] = utc()
    save()
    response = json.loads(get("/ui/start_test?" + urlencode({"name": RUN, "duration": 60, "ajax": 1}), "start.json"))
    manifest["capture_start_response"] = response
    save()
    if response.get("ok") is not True:
        raise RuntimeError("Monitor rejected capture start: " + json.dumps(response))
    capture_clock = time.monotonic()
    event("capture_start_accepted", response=response)
    active = False
    for i in range(15):
        text, idle, active = status("startup-status-%02d.html" % i)
        event("capture_startup_status", active=active, status=text)
        if active:
            break
        time.sleep(1)
    if not active:
        raise RuntimeError("Could not confirm active capture; sender was not launched")
    manifest["capture_active_at"] = utc()
    save()
    if time.monotonic() - capture_clock > 20:
        raise RuntimeError("Insufficient capture window remains for sender")
    remote = 'cd /home/selabs/pion-l4s-interop && exec timeout --signal=INT --kill-after=5s 45s env PION_EXPERIMENT_ROOT=/home/selabs/pion-l4s-interop PATH=/home/selabs/.local/share/pion-toolchains/go1.27.4/bin:/usr/local/bin:/usr/bin:/bin python3 results/raw/pion-compare-25m-20261115T172333Z/runtime/testbed-pinned.py run --role sender --binary results/raw/pion-compare-25m-20261115T172333Z/runtime/source/bin/pion-l4s --config results/raw/pion-compare-25m-20261115T172333Z-gcc/config.json --run-id pion-compare-25m-20261115T172333Z-gcc'
    command = ["ssh", "-F", "/dev/null", "-o", "ControlPath=" + CONTROL,
               "-o", "BatchMode=yes", "selabs@192.168.1.216", remote]
    manifest["sender_started_at"] = utc()
    event("sender_starting", offered_source_bps=23950988, initial_controller_target_bps=25000000)
    with (BASE / "sender-ssh.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        began = time.monotonic()
        i = 0
        while process.poll() is None:
            if time.monotonic() - began > 60:
                process.terminate()
                process.wait(timeout=5)
                raise RuntimeError("Sender SSH exceeded bounded timeout")
            text, idle, active = status("during-sender-status-%02d.html" % i)
            if not active:
                manifest["capture_partial"] = True
            event("sender_capture_status", active=active, status=text)
            i += 1
            time.sleep(2)
        manifest["sender_exit_code"] = process.returncode
    manifest["sender_finished_at"] = utc()
    event("sender_completed", exit_code=manifest["sender_exit_code"])
    copied = subprocess.run(["scp", "-F", "/dev/null", "-o", "ControlPath=" + CONTROL, "-o", "BatchMode=yes",
                             "-r", "selabs@192.168.1.216:/home/selabs/pion-l4s-interop/results/raw/" + RUN + "/sender",
                             str(BASE) + "/"], capture_output=True, text=True, timeout=30)
    manifest["sender_artifacts_copied"] = copied.returncode == 0
    if copied.returncode:
        (BASE / "sender-copy-error.txt").write_text(copied.stderr)
    save()
    deadline = capture_clock + 60 + 300
    previous_ready = False
    i = 0
    while time.monotonic() < deadline:
        text, idle, active = status("post-status-%03d.html" % i)
        index = get("/data/?C=M;O=D", "latest-index.html")
        folder = locate(index)
        ready = False
        if folder:
            manifest["result_url"] = ORIGIN + folder
            html = get(folder, "result-index.html")
            names = {unquote(x).lstrip("./") for x in parse(html).links}
            ready = idle and {"log.txt", "summary_table_downstream.csv", "summary_table_upstream.csv",
                             "Aggregate_thruput_downstream.pdf", "Aggregate_thruput_upstream.pdf"}.issubset(names)
        event("analysis_status", idle=idle, active=active, result_url=manifest.get("result_url"), outputs_present=ready)
        if ready and previous_ready:
            manifest["outputs_observed_at"] = utc()
            event("capture_outputs_ready", result_url=manifest["result_url"])
            return
        previous_ready = ready
        i += 1
        time.sleep(3)
    raise RuntimeError("Capture/analysis deadline reached; inspect saved evidence")

if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        event("coordinator_failed", error=str(error))
        raise
