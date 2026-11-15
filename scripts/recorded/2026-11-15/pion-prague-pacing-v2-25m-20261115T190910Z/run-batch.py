"""Run three experimental Prague pacing trials, each with its own bounded monitor capture."""
from pathlib import Path
import datetime
import hashlib
import http.client
import json
import os
import signal
import subprocess
import sys
import time

ROOT = Path('/Users/albisht/ietf/pion-l4s-interop')
BASE = Path(__file__).resolve().parent
PLAN = json.loads((BASE / 'plan.json').read_text())
STATE = {'batch_id': PLAN['batch_id'], 'completed': [], 'events': [], 'scope': PLAN['scope']}
ENV = dict(os.environ, PION_EXPERIMENT_ROOT=str(ROOT))

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}

def event(name, **fields):
    row = dict(time=utc(), event=name, **fields)
    STATE['events'].append(row)
    temporary = BASE / 'progress.tmp'
    temporary.write_text(json.dumps(STATE, indent=2) + '\n')
    temporary.replace(BASE / 'progress.json')
    print(json.dumps(row), flush=True)

def stop(process):
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

def health(config):
    peer = config['receiver']
    connection = http.client.HTTPConnection(peer['ipv4'], peer['signaling_port'], timeout=2)
    try:
        connection.request('GET', '/healthz')
        response = connection.getresponse()
        if response.status != 200:
            return None
        data = json.loads(response.read(65536))
        if (data.get('service') != 'pion-l4s' or data.get('role') != 'receiver'
                or data.get('controller') != config['controller']
                or data.get('feedback') != 'ccfb' or data.get('session_claimed') is not False):
            return None
        if config['controller'] == 'prague' and data.get('ecn_observation') is not True:
            return None
        return data
    finally:
        connection.close()

def run_one(item):
    folder = ROOT / 'results/raw' / item['run_id']
    config = load(ROOT / item['config_path'])
    receiver = coordinator = None
    event('preflight', controller=item['controller'], run_id=item['run_id'])
    command = [sys.executable, '-u', str(ROOT / PLAN['runner']), 'run', '--role', 'receiver',
               '--config', item['config_path'], '--run-id', item['run_id'],
               '--binary', PLAN['receiver_binary']]
    with (folder / 'receiver-console.log').open('x') as output:
        try:
            receiver = subprocess.Popen(command, cwd=ROOT, env=ENV,
                                        stdout=output, stderr=subprocess.STDOUT)
            ready = None
            for attempt in range(30):
                if receiver.poll() is not None:
                    raise RuntimeError('Receiver exited before readiness: ' + str(receiver.returncode))
                try:
                    ready = health(config)
                except (OSError, ValueError):
                    ready = None
                if ready:
                    break
                time.sleep(.5)
            if not ready:
                raise RuntimeError('Fresh receiver readiness deadline')
            (folder / 'receiver-health.json').write_text(json.dumps(ready, indent=2) + '\n')
            remote_root = '/home/selabs/pion-l4s-interop'
            remote = ('cd ' + remote_root + ' && env PION_EXPERIMENT_ROOT=' + remote_root
                      + ' PATH=/home/selabs/.local/share/pion-toolchains/go1.27.4/bin:/usr/local/bin:/usr/bin:/bin'
                      + ' python3 ' + PLAN['runner'] + ' doctor --role sender --binary ' + PLAN['receiver_binary'] + ' --config ' + item['config_path'])
            with (folder / 'sender-doctor.json').open('x') as doctor:
                subprocess.run(['ssh', '-F', '/dev/null', '-S', PLAN['control_path'],
                                '-o', 'BatchMode=yes', 'selabs@192.168.1.216', remote],
                               stdout=doctor, stderr=subprocess.STDOUT, timeout=20, check=True)
            event('receiver_ready', controller=item['controller'], run_id=item['run_id'])
            with (folder / 'coordinator.log').open('x') as log:
                coordinator = subprocess.Popen([sys.executable, '-u', str(folder / 'capture-run.py')],
                                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 450
                announced = set()
                while coordinator.poll() is None:
                    manifest = load(folder / 'monitor/manifest.json')
                    for key in ('capture_active_at', 'sender_started_at', 'sender_finished_at', 'outputs_observed_at'):
                        if manifest.get(key) and key not in announced:
                            announced.add(key)
                            event(key, controller=item['controller'], value=manifest[key],
                                  sender_exit_code=manifest.get('sender_exit_code'))
                    if manifest.get('sender_finished_at') and 'receiver_stopped' not in announced:
                        time.sleep(2)
                        stop(receiver)
                        announced.add('receiver_stopped')
                        event('receiver_stopped', controller=item['controller'], exit_code=receiver.returncode)
                    if time.monotonic() > deadline:
                        raise RuntimeError('Capture coordinator exceeded bounded wait')
                    time.sleep(1)
                if coordinator.returncode:
                    raise RuntimeError('Capture coordinator failed; inspect ' + str(folder / 'coordinator.log'))
            manifest = load(folder / 'monitor/manifest.json')
            if manifest.get('sender_exit_code') != 0 or not manifest.get('sender_artifacts_copied'):
                raise RuntimeError('Sender failed or endpoint artifacts were not collected')
            if manifest.get('capture_partial') or manifest.get('capture_start_attempts') != 1:
                raise RuntimeError('Capture incomplete or duplicate start attempt')
            stop(receiver)
            for role in ('sender', 'receiver'):
                recording = folder / role / 'recording/session-trace.jsonl'
                if not recording.is_file() or not recording.stat().st_size:
                    raise RuntimeError('Missing physical feedback recording for ' + role)
            result = dict(item, dashboard_result_url=manifest['result_url'],
                          sender_exit_code=manifest['sender_exit_code'],
                          receiver_outcome=load(folder / 'receiver/metadata.json'))
            STATE['completed'].append(result)
            event('run_complete', controller=item['controller'], result_url=manifest['result_url'])
        finally:
            stop(receiver)
            if coordinator is not None and coordinator.poll() is None:
                coordinator.terminate()
                coordinator.wait(timeout=10)

def main():
    expected = hashlib.sha256((ROOT / PLAN['receiver_binary']).read_bytes()).hexdigest()
    if expected != PLAN['receiver_binary_sha256']:
        raise RuntimeError('Pinned receiver binary changed')
    active = subprocess.run(['pgrep', '-x', 'pion-l4s'], capture_output=True, text=True)
    if active.returncode != 1:
        raise RuntimeError('Existing endpoint process or failed process inspection: ' + active.stdout + active.stderr)
    event('comparison_started', source_mbps=PLAN['source']['nominal_payload_bps']/1e6,
          source_commit=PLAN['source_commit'])
    for item in PLAN['runs']:
        run_one(item)
        time.sleep(2)
    if hashlib.sha256((ROOT / 'testbed/local.json').read_bytes()).hexdigest() != PLAN['original_config_sha256']:
        raise RuntimeError('Original local config changed during comparison')
    event('comparison_completed', runs=len(STATE['completed']))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        event('comparison_failed', error=str(error))
        raise
