#!/usr/bin/env python3
"""Plan or run one bounded two-host Pion call with a matched monitor capture.

Use the same repository source on both peers, normally via sender.py deploy.
Only 'run' starts endpoints/capture. No retries of capture-start requests, shared
network changes or competing traffic are performed. Artifacts are private raw results.
"""
import argparse
import csv
import datetime
from html.parser import HTMLParser
import http.client
import io
import json
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
from urllib.parse import unquote, urlencode, urljoin, urlsplit

import sender
import source_manifest
import testbed

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    """Read visible status text and same-page links without executing scripts."""
    def __init__(self, html):
        super().__init__(); self.text=[]; self.links=[]; self.skip=0; self.feed(html)

    def handle_starttag(self, tag, attrs):
        """Collect href data while excluding script/style bodies from status text."""
        if tag in ("script", "style"): self.skip += 1
        if tag == "a": self.links.append(dict(attrs).get("href", ""))

    def handle_endtag(self, tag):
        """Resume status text after a script/style body."""
        if tag in ("script", "style"): self.skip=max(0,self.skip-1)

    def handle_data(self, value):
        """Retain plain visible text only."""
        if not self.skip and value.strip(): self.text.append(value.strip())


class Monitor:
    """Access the configured capture UI with bounded, non-retried requests."""
    def __init__(self, url, folder, run_id):
        self.origin=urlsplit(url)
        if self.origin.scheme not in ("http", "https") or not self.origin.hostname or self.origin.username or self.origin.password:
            raise ValueError("monitor URL must be HTTP(S) without credentials")
        self.folder=folder; self.run_id=run_id

    def get(self, path, filename=None, limit=4*1024*1024):
        """Fetch one local monitor resource; never retry an ambiguous mutation."""
        cls=http.client.HTTPSConnection if self.origin.scheme=="https" else http.client.HTTPConnection
        conn=cls(self.origin.hostname,self.origin.port,timeout=20)
        try:
            conn.request("GET",path); response=conn.getresponse(); body=response.read(limit+1)
            if len(body)>limit: raise ValueError("monitor response exceeds bounded size")
            if filename:
                (self.folder/filename).write_bytes(body)
                (self.folder/(filename+".headers.json")).write_text(json.dumps(dict(response.getheaders()),indent=2))
            if response.status!=200: raise ValueError(f"monitor HTTP {response.status}: {path}")
            return "" if filename and filename.endswith(".pcapng") else body.decode("utf-8",errors="replace")
        finally: conn.close()

    def status(self, filename):
        """Require affirmative dumpcap status; a new result folder is insufficient."""
        html=self.get("/ui/status",filename); text=" ".join(Page(html).text)
        idle="currently idle" in text.lower()
        active=bool(re.search(r'class=["\'][^"\']*state[^"\']*busy',html)) and bool(re.search(r"dumpcap|currently capturing",text,re.I)) and not idle
        if "/data/" in text and self.run_id not in text: active=False
        return dict(idle=idle,active=active,text=text)

    def locate(self, html):
        """Find this exact new capture beneath the configured monitor origin."""
        origin=f"{self.origin.scheme}://{self.origin.netloc}"
        for href in Page(html).links:
            target=urlsplit(urljoin(origin+"/data/",href)); decoded=unquote(target.path)
            if (decoded.rstrip('/').split('/')[-1].startswith(self.run_id+'_') and decoded.endswith("/") and target.netloc==self.origin.netloc
                    and target.scheme==self.origin.scheme and decoded.startswith("/data/")
                    and ".." not in decoded.split("/")):
                return target.path
        return None


def stop_owned(process):
    """Stop only a process object created by this invocation, with bounded cleanup."""
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


def remote_prefix(args):
    """Quote a Linux checkout and optional private Go-toolchain directory."""
    prefix="cd "+shlex.quote(args.remote_root)+" && "
    if args.go_bin: prefix+="export PION_GO_BIN="+shlex.quote(args.go_bin)+" && "
    return prefix


def endpoint_argv(args, role, config_path, run_id):
    """Use the tracked launcher/helper on both peers, with no temporary pinned copy."""
    return ["bash","run-testbed.sh","run","--role",role,"--config",config_path,
            "--run-id",run_id,"--binary",args.binary]


def run(args, config, source):
    """Preflight, start one confirmed capture, send once, collect and clean up."""
    run_id=args.run_id or "pion-"+config["controller"]+"-"+datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}",run_id): raise ValueError("invalid run ID")
    folder=ROOT/"results/raw"/run_id
    if folder.exists(): raise ValueError("run directory already exists; use a new run ID")
    active=subprocess.run(["pgrep","-x","pion-l4s"],capture_output=True,text=True)
    if active.returncode!=1: raise ValueError("an endpoint is already running or process inspection failed")
    build=source_manifest.verify_build(ROOT/args.binary)
    folder.mkdir(parents=True); monitor_dir=folder/"monitor"; monitor_dir.mkdir()
    config_path=str((folder/"config.json").relative_to(ROOT)); (ROOT/config_path).write_text(json.dumps(config,indent=2)+"\n")
    mon=Monitor(config["bottleneck"]["dashboard_url"],monitor_dir,run_id)
    manifest=dict(run_id=run_id,capture_seconds=args.capture_seconds,sender_seconds=config["duration_seconds"],
                  source=source,receiver_binary_sha256=build["binary_sha256"],source_sha256=build["source_sha256"],
                  capture_start_attempts=0,capture_partial=False,status="preflight",events=[])
    original_hash=testbed.sha256(ROOT/args.config)
    receiver=remote_sender=None

    def event(name, **values):
        """Persist progress and stage failures without exposing credentials or SDP."""
        manifest["events"].append(dict(time=testbed.utc_now(),event=name,**values))
        (monitor_dir/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
        print(json.dumps(dict(event=name,**values)),flush=True)

    try:
        mon.get("/ui/","baseline-ui.html"); state=mon.status("baseline-status.html")
        if not state["idle"]: raise ValueError("monitor is not idle; existing activity was preserved")
        listing=mon.get("/data/?C=M;O=D","baseline-index.html")
        if mon.locate(listing): raise ValueError("capture run name already exists")
        probe=("import sys,json,subprocess;sys.path.insert(0,'scripts');import source_manifest,media_source;"
               "active=subprocess.run(['pgrep','-x','pion-l4s'],capture_output=True);"
               "assert active.returncode==1,'sender already running';"
               "print(json.dumps(dict(build=source_manifest.verify_build("+repr(args.binary)+"),source=media_source.inspect_ivf("+repr(config['video'])+"))))")
        remote=json.loads(sender.remote_command(args,remote_prefix(args)+"command -v timeout >/dev/null && python3 -c "+shlex.quote(probe)))
        if remote['build']['source_sha256']!=build['source_sha256']: raise ValueError("sender and receiver compiled source hashes differ")
        if remote['source']['sha256']!=source['sha256']: raise ValueError("sender and receiver media hashes differ")
        manifest['sender_binary_sha256']=remote['build']['binary_sha256']
        remote_folder=args.remote_root.rstrip('/')+'/results/raw/'+run_id
        sender.remote_command(args,"test ! -e "+shlex.quote(remote_folder)+" && mkdir -p "+shlex.quote(remote_folder))
        subprocess.run(sender.connection_args(args,"scp")+[str(ROOT/config_path),args.host+":"+remote_folder+"/config.json"],check=True,timeout=30)
        with (folder/'receiver-console.log').open('x') as log:
            receiver=subprocess.Popen(endpoint_argv(args,'receiver',config_path,run_id),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        ready=False
        for _ in range(30):
            if receiver.poll() is not None: raise ValueError("receiver exited before readiness")
            try:
                peer=config['receiver'];health=testbed.http_json(peer['ipv4'],peer['signaling_port'],peer['ipv4'])
                testbed.check_receiver_health(health,config)
                if health.get('controller')!=config['controller']: raise ValueError('receiver controller mismatch')
                (folder/'receiver-health.json').write_text(json.dumps(health,indent=2));ready=True;break
            except (OSError,ValueError): time.sleep(.5)
        if not ready: raise ValueError('receiver readiness deadline')
        doctor=remote_prefix(args)+shlex.join(['bash','run-testbed.sh','doctor','--role','sender','--config',config_path,'--binary',args.binary])
        (folder/'sender-doctor.json').write_text(sender.remote_command(args,doctor))
        event('preflight_passed')
        manifest.update(capture_start_attempts=1,capture_request_at=testbed.utc_now(),status='starting_capture')
        event('capture_requesting')
        response=json.loads(mon.get('/ui/start_test?'+urlencode(dict(name=run_id,duration=args.capture_seconds,ajax=1)),'start.json'))
        if response.get('ok') is not True: raise ValueError('monitor rejected capture start: '+str(response))
        capture_clock=time.monotonic();manifest['capture_start_response']=response
        for i in range(15):
            if mon.status('startup-%02d.html'%i)['active']: break
            time.sleep(1)
        else: raise ValueError('capture activity unconfirmed; sender not started')
        if time.monotonic()-capture_clock>20: raise ValueError('insufficient remaining capture coverage')
        manifest.update(capture_active_at=testbed.utc_now(),sender_started_at=testbed.utc_now(),status='sending')
        event('capture_active_sender_starting')
        command=remote_prefix(args)+shlex.join(['timeout','--signal=INT','--kill-after=5s',str(config['duration_seconds']+30)+'s']+endpoint_argv(args,'sender',config_path,run_id))
        with (folder/'sender-ssh.log').open('x') as log:
            remote_sender=subprocess.Popen(sender.connection_args(args)+[args.host,command],stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+config['duration_seconds']+45;i=0
            while remote_sender.poll() is None:
                if time.monotonic()>deadline: raise ValueError('sender wall-clock deadline')
                if not mon.status('during-%03d.html'%i)['active']: manifest['capture_partial']=True
                i+=1;time.sleep(2)
        manifest.update(sender_exit_code=remote_sender.returncode,sender_finished_at=testbed.utc_now())
        event('sender_completed',exit_code=remote_sender.returncode)
        time.sleep(2);stop_owned(receiver);manifest['receiver_exit_code']=receiver.returncode
        subprocess.run(sender.connection_args(args,'scp')+['-r',args.host+':'+remote_folder+'/sender',str(folder)+'/'],check=True,timeout=60)
        manifest['sender_artifacts_copied']=True
        previous=False;i=0
        while time.monotonic()<capture_clock+args.capture_seconds+300:
            state=mon.status('post-%03d.html'%i);index=mon.get('/data/?C=M;O=D','latest-index.html');result=mon.locate(index)
            ready=False
            if result:
                html=mon.get(result,'result-index.html')
                names={unquote(link).lstrip('./') for link in Page(html).links}
                ready=state['idle'] and {'log.txt','summary_table_downstream.csv','summary_table_upstream.csv','Aggregate_thruput_downstream.pdf','Aggregate_thruput_upstream.pdf'}.issubset(names)
                manifest['result_url']=f'{mon.origin.scheme}://{mon.origin.netloc}'+result
            if ready and previous: break
            previous=ready;i+=1;time.sleep(3)
        else: raise ValueError('capture analysis deadline; saved evidence retained')
        manifest['outputs_observed_at']=testbed.utc_now()
        for name in ('log.txt','summary_table_downstream.csv','summary_table_upstream.csv'):
            mon.get(result+name,name)
        if args.collect_pcaps:
            for name in ('capture_nsi.pcapng','capture_cmci.pcapng'):
                mon.get(result+name,name,limit=128*1024*1024)
        records=[json.loads(line) for line in (folder/'sender/endpoint.jsonl').read_text().splitlines() if line.startswith('{')]
        manifest['selected_ice_pair']=next((r for r in records if r.get('msg')=='selected ICE pair'),None)
        if not manifest['selected_ice_pair']: raise ValueError('selected ICE pair missing from sender log')
        pair=manifest['selected_ice_pair']
        source_ip,source_port=re.search(r'host ([0-9.]+):([0-9]+)',pair['local']).groups()
        destination_ip,destination_port=re.search(r'host ([0-9.]+):([0-9]+)',pair['remote']).groups()
        flow=f'UDP [{source_ip} {source_port}] to [{destination_ip} {destination_port}]'
        rows=list(csv.DictReader((monitor_dir/'summary_table_downstream.csv').open()))
        summary=next((row for row in rows if row['Flow ID']==flow),None)
        if summary is None or int(summary['Num Packets'])<1: raise ValueError('selected ICE flow missing from downstream capture summary')
        manifest.update(matched_downstream_summary=summary,qdisc_mode_capacity_verified=False)
        number=summary['Flow Number']
        if number.isdigit(): manifest['flow_plot_url']=manifest['result_url']+number+'_downstream.pdf'
        if remote_sender.returncode or manifest['capture_partial']: raise ValueError('sender failed or capture coverage incomplete')
        manifest['status']='captured';event('capture_completed',result_url=manifest['result_url'])
    except (Exception,KeyboardInterrupt) as error:
        manifest['status']='failed';event('run_failed',error=str(error));raise
    finally:
        stop_owned(receiver);stop_owned(remote_sender)
        manifest['original_config_preserved']=testbed.sha256(ROOT/args.config)==original_hash
        event('cleanup_completed')


def main():
    """Print a network-free plan or explicitly execute one captured experiment."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','run'));parser.add_argument('--config',required=True)
    parser.add_argument('--host',required=True);parser.add_argument('--remote-root',required=True)
    parser.add_argument('--ssh-config',default='/dev/null');parser.add_argument('--control-path');parser.add_argument('--go-bin')
    parser.add_argument('--binary',default='bin/pion-l4s');parser.add_argument('--run-id');parser.add_argument('--capture-seconds',type=int)
    parser.add_argument('--collect-pcaps',action='store_true',help='download both bounded captures after analysis')
    args=parser.parse_args()
    try:
        sender.validate_target(args.host,args.remote_root)
        config=testbed.load_config(ROOT/args.config);source=testbed.inspect_source(config)
        args.capture_seconds=args.capture_seconds or config['duration_seconds']+30
        if not config['duration_seconds']+30<=args.capture_seconds<=3600:
            raise ValueError('capture must cover sender duration plus 30 seconds, and be at most 3600 seconds')
        if args.action=='plan':
            print(json.dumps(dict(controller=config['controller'],source=source,initial_target_bps=config['initial_bitrate_bps'],
                host=args.host,remote_root=args.remote_root,sender_seconds=config['duration_seconds'],capture_seconds=args.capture_seconds,
                starts_capture=False,requirements='Matching fresh builds and identical media on both hosts; unused receiver and idle monitor'),indent=2))
        else: run(args,config,source)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('capture-testbed: '+str(error),file=sys.stderr);return 1
    except KeyboardInterrupt: return 130
    return 0


if __name__=='__main__':sys.exit(main())
